#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
REPO_PART_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?$")
GITHUB_RE = re.compile(
    r"(?:https?://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
    re.I,
)
STRONG_SOURCE_LINE_RE = re.compile(r"\b(source|repository|repo|upstream|homepage|origin)\b", re.I)


@dataclass
class CommandResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str


@dataclass
class SkillRecord:
    name: str
    folder: str
    manifest: str
    root: str
    status: str
    repo: str | None = None
    source_url: str | None = None
    skill_path: str | None = None
    source_confidence: str | None = None
    evidence: str | None = None
    pinned: bool | None = None
    locally_modified: bool | None = None


def run(cmd: list[str], timeout: int = 60, cwd: Path | None = None) -> CommandResult:
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        return CommandResult(cmd, p.returncode, p.stdout.strip(), p.stderr.strip())
    except FileNotFoundError as exc:
        return CommandResult(cmd, 127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return CommandResult(cmd, 124, out.strip(), (err + "\ncommand timed out").strip())


def home() -> Path:
    return Path.home()


def user_root() -> Path:
    return home() / ".agents" / "skills"


def user_lockfile() -> Path:
    return home() / ".agents" / ".skill-lock.json"


def legacy_root() -> Path:
    return home() / ".codex" / "skills"


def data_root() -> Path:
    override = os.environ.get("CODEX_GITHUB_SKILL_MANAGER_DATA") or os.environ.get("GITHUB_SKILL_MANAGER_DATA")
    return Path(override).expanduser() if override else home() / ".agents" / ".codex-github-skill-manager"


def source_registry_path() -> Path:
    return data_root() / "sources.json"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


@contextmanager
def exclusive_file_lock(path: Path, timeout: float = 10.0):
    """Serialize read-modify-write operations without leaving stale lock files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    if path.stat().st_size == 0:
        handle.write(b"\0")
        handle.flush()
    deadline = time.monotonic() + timeout
    locked = False
    try:
        while not locked:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for state lock {path}")
                time.sleep(0.05)
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def read_source_registry() -> tuple[dict[str, dict[str, Any]], str | None]:
    path = source_registry_path()
    if not path.exists():
        return {}, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"could not read {path}: {type(exc).__name__}"
    if not isinstance(payload, dict) or payload.get("version") != 2:
        return {}, f"{path} is not a supported version 2 source registry"
    skills = payload.get("skills", {}) if isinstance(payload, dict) else {}
    if not isinstance(skills, dict):
        return {}, f"{path} has a non-object 'skills' field"
    return skills, None


def write_source_registry(skills: dict[str, dict[str, Any]]) -> None:
    path = source_registry_path()
    payload = {
        "version": 2,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "skills": skills,
    }
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def path_key(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve(strict=False)))


def registry_key(root: Path, folder: str) -> str:
    return f"{path_key(root)}::{folder}"


def normalize_skill_path(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.name != "SKILL.md":
        return None
    return path.as_posix()


def register_source(skill: str, repo_or_url: str, skill_path: str | None = None, root: str | None = None) -> dict[str, Any]:
    skill = skill.strip()
    repo = normalize_repo_from_text(repo_or_url)
    if not skill or not NAME_RE.fullmatch(skill) or "--" in skill:
        return {"ok": False, "error": "skill must be a valid kebab-case Agent Skill name"}
    if not repo:
        return {"ok": False, "error": "repo must be OWNER/REPO or an unambiguous github.com repository URL"}
    exact_skill_path = normalize_skill_path(skill_path)
    if skill_path and not exact_skill_path:
        return {"ok": False, "error": "skill-path must be a relative path ending in SKILL.md with no '..' segments"}
    roots = selected_roots("all", True)
    if root:
        requested = path_key(Path(root))
        roots = [candidate for candidate in roots if path_key(candidate) == requested]
        if not roots:
            return {"ok": False, "error": "root must identify a discovered user, project, or legacy skill root"}
    instances = [
        candidate
        for candidate in roots
        if (candidate / skill / "SKILL.md").is_file() and not is_link(candidate / skill)
    ]
    if len(instances) != 1:
        return {"ok": False, "error": f"expected exactly one installed '{skill}' instance; found {len(instances)}. Pass --root to disambiguate."}
    installed_root = instances[0]
    key = registry_key(installed_root, skill)
    try:
        with exclusive_file_lock(source_registry_path().with_suffix(".lock")):
            registry, registry_error = read_source_registry()
            if registry_error:
                return {"ok": False, "error": f"source registry is corrupt or incompatible; refusing to overwrite it: {registry_error}"}
            previous = registry.get(key)
            registered_at = (
                previous.get("registered_at")
                if isinstance(previous, dict)
                and normalize_repo_from_text(str(previous.get("repo") or "")) == repo
                and normalize_skill_path(previous.get("skill_path")) == exact_skill_path
                else dt.datetime.now(dt.timezone.utc).isoformat()
            )
            registry[key] = {
                "root": str(installed_root.resolve(strict=False)),
                "folder": skill,
                "repo": repo,
                "source_url": repo_url(repo),
                "skill_path": exact_skill_path,
                "registered_at": registered_at,
            }
            write_source_registry(registry)
    except TimeoutError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "skill": skill, **registry[key], "registry": str(source_registry_path())}


def repo_cache_path(repo: str) -> Path:
    owner, name = repo.split("/", 1)
    return data_root() / "repos" / owner / name


def project_root() -> Path | None:
    r = run(["git", "rev-parse", "--show-toplevel"], timeout=5)
    return Path(r.stdout).resolve() if r.exit_code == 0 and r.stdout else None


def project_skill_roots() -> list[Path]:
    repo = project_root()
    if not repo:
        return []
    cwd = Path.cwd().resolve()
    try:
        cwd.relative_to(repo)
    except ValueError:
        return [repo / ".agents" / "skills"]
    roots: list[Path] = []
    cur = cwd
    while True:
        roots.append(cur / ".agents" / "skills")
        if cur == repo:
            break
        cur = cur.parent
    return roots


def project_lockfiles() -> list[Path]:
    out: list[Path] = []
    for root in project_skill_roots():
        lf = root.parent / ".skill-lock.json"
        if lf not in out:
            out.append(lf)
    return out


def selected_roots(scope: str, include_legacy: bool) -> list[Path]:
    roots: list[Path] = []
    if scope in ("user", "all"):
        roots.append(user_root())
    if scope in ("project", "all"):
        roots.extend(project_skill_roots())
    if include_legacy and legacy_root().exists():
        roots.append(legacy_root())
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.absolute())
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def tree_has_links(root: Path) -> bool:
    if is_link(root):
        return True
    try:
        return any(is_link(path) for path in root.rglob("*"))
    except OSError:
        return True


def validate_skill_root(root: Path) -> tuple[bool, str | None]:
    if root.exists() and is_link(root):
        return False, "skill root is a symlink or junction"
    resolved = root.resolve(strict=False)
    if resolved in {user_root().resolve(strict=False), legacy_root().resolve(strict=False)}:
        return True, None
    repo = project_root()
    if not repo:
        return False, "project skill root is not inside a Git repository"
    repo_resolved = repo.resolve(strict=False)
    try:
        resolved.relative_to(repo_resolved)
    except ValueError:
        return False, "resolved project skill root escapes the Git repository"
    try:
        root.absolute().relative_to(repo.absolute())
    except ValueError:
        return False, "project skill root is outside the Git repository"
    return True, None


def github_cli_metadata(fm: dict[str, Any] | None) -> tuple[str | None, str | None, bool | None]:
    if not fm or not isinstance(fm.get("metadata"), dict):
        return None, None, None
    metadata = fm["metadata"]
    repo = normalize_repo_from_text(str(metadata.get("github-repo") or ""))
    skill_path = normalize_skill_path(str(metadata.get("github-path") or ""))
    pinned = bool(metadata.get("github-pinned")) if "github-pinned" in metadata else None
    return repo, skill_path, pinned


def parse_frontmatter(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None, ""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None, text
    raw = "\n".join(lines[1:end])
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None
    if yaml is not None:
        try:
            data = yaml.safe_load(raw)
            return (data if isinstance(data, dict) else None), text
        except Exception:
            return None, text

    # Dependency-free conservative subset for fresh hosts without PyYAML.
    # It supports scalar top-level fields, block descriptions, and the nested
    # metadata map injected by GitHub CLI. Unsupported or malformed YAML fails
    # closed instead of being reinterpreted as valid provenance.
    data: dict[str, Any] = {}
    current_map: dict[str, Any] | None = None
    block_key: str | None = None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "\t" in line:
            return None, text
        indent = len(line) - len(line.lstrip(" "))
        if indent:
            if block_key:
                data[block_key] = (str(data.get(block_key, "")) + " " + line.strip()).strip()
                continue
            if current_map is None:
                return None, text
            m = re.fullmatch(r"\s+([A-Za-z0-9_-]+):\s*(.*)", line)
            if not m:
                return None, text
            current_map[m.group(1)] = m.group(2).strip().strip('"\'')
            continue
        m = re.fullmatch(r"([A-Za-z0-9_-]+):\s*(.*)", line)
        if not m:
            return None, text
        key, raw_value = m.group(1), m.group(2).strip()
        current_map = None
        block_key = None
        if raw_value == "":
            nested: dict[str, Any] = {}
            data[key] = nested
            current_map = nested
        elif raw_value in {">", ">-", "|", "|-"}:
            data[key] = ""
            block_key = key
        elif raw_value[:1] in {"[", "{"} or (raw_value.startswith(('"', "'")) and not raw_value.endswith(raw_value[0])):
            return None, text
        else:
            data[key] = raw_value.strip('"\'')
    return data, text


def normalize_repo_from_text(value: str) -> str | None:
    m = GITHUB_RE.search(value)
    if not m:
        # Allow exact OWNER/REPO only in explicit source fields.
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value.strip()):
            owner, repo = value.strip().split("/", 1)
            repo = repo.removesuffix(".git")
            if owner not in {".", ".."} and repo not in {".", ".."} and REPO_PART_RE.fullmatch(owner) and REPO_PART_RE.fullmatch(repo):
                return f"{owner.lower()}/{repo.lower()}"
        return None
    owner, repo = m.group(1), m.group(2).removesuffix(".git")
    if owner in {".", ".."} or repo in {".", ".."} or not REPO_PART_RE.fullmatch(owner) or not REPO_PART_RE.fullmatch(repo):
        return None
    return f"{owner.lower()}/{repo.lower()}"


def repo_url(repo: str) -> str:
    return f"https://github.com/{repo}.git"


def read_lockfile(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    skills = data.get("skills", {}) if isinstance(data, dict) else {}
    out: list[dict[str, Any]] = []
    if isinstance(skills, dict):
        for key, value in skills.items():
            if isinstance(value, dict):
                item = dict(value)
                item["_key"] = key
                item["_lockfile"] = str(path)
                out.append(item)
    return out


def lock_entries_for_scope(scope: str) -> list[dict[str, Any]]:
    paths: list[Path] = []
    if scope in ("user", "all"):
        paths.append(user_lockfile())
    if scope in ("project", "all"):
        paths.extend(project_lockfiles())
    entries: list[dict[str, Any]] = []
    for p in paths:
        entries.extend(read_lockfile(p))
    return entries


def lock_match(folder: Path, skill_name: str, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for e in entries:
        skill_path = str(e.get("skillPath", ""))
        key = str(e.get("_key", ""))
        names = {Path(skill_path).parent.name if skill_path else "", Path(key).name if key else ""}
        if folder.name in names or skill_name in names:
            candidates.append(e)
    if len(candidates) == 1:
        return candidates[0]
    # If all candidates point to the same exact source/path, treat as one.
    if candidates:
        sigs = {(c.get("source"), c.get("sourceUrl"), c.get("skillPath")) for c in candidates}
        if len(sigs) == 1:
            return candidates[0]
    return None


def git_remote_repo(skill_dir: Path) -> tuple[str | None, str | None]:
    r = run(["git", "-C", str(skill_dir), "remote", "get-url", "origin"], timeout=5)
    if r.exit_code == 0 and r.stdout:
        return normalize_repo_from_text(r.stdout), r.stdout
    return None, None


def strong_repo_candidates(manifest: Path, fm: dict[str, Any] | None, full_text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if fm:
        for key, value in fm.items():
            if re.search(r"source|repo|repository|upstream|homepage|origin", str(key), re.I):
                if isinstance(value, (str, int, float)):
                    repo = normalize_repo_from_text(str(value))
                    if repo:
                        found.append((repo, f"SKILL.md frontmatter field '{key}'"))
    # Strong labelled lines only, avoiding incidental GitHub links.
    for line in full_text.splitlines()[:160]:
        if STRONG_SOURCE_LINE_RE.search(line):
            repo = normalize_repo_from_text(line)
            if repo:
                found.append((repo, "labelled source line in SKILL.md"))
    for name in ("README.md", "README.MD", "README.txt"):
        p = manifest.parent / name
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for line in text.splitlines()[:200]:
            if STRONG_SOURCE_LINE_RE.search(line):
                repo = normalize_repo_from_text(line)
                if repo:
                    found.append((repo, f"labelled source line in {name}"))
    # Stable de-dupe while retaining evidence.
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for repo, evidence in found:
        if repo.lower() not in seen:
            seen.add(repo.lower())
            out.append((repo, evidence))
    return out


def discover(scope: str, include_legacy: bool) -> dict[str, Any]:
    roots = selected_roots(scope, include_legacy)
    locks = lock_entries_for_scope(scope)
    registry, registry_error = read_source_registry()
    records: list[SkillRecord] = []
    issues: list[dict[str, Any]] = []
    if registry_error:
        issues.append({"type": "source-registry-corrupt", "path": str(source_registry_path()), "error": registry_error})

    for root in roots:
        if not root.exists():
            continue
        safe_root, root_error = validate_skill_root(root)
        if not safe_root:
            issues.append({"type": "unsafe-skill-root", "root": str(root), "error": root_error})
            continue
        for folder in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name):
            if folder.name.startswith("."):
                continue
            manifest = folder / "SKILL.md"
            if is_link(folder):
                records.append(SkillRecord(folder.name, folder.name, str(manifest), str(root), "malformed"))
                issues.append({"skill": folder.name, "type": "unsafe-skill-link", "path": str(folder)})
                continue
            if not manifest.is_file():
                records.append(SkillRecord(folder.name, folder.name, str(manifest), str(root), "malformed"))
                issues.append({"skill": folder.name, "type": "missing-manifest", "path": str(manifest)})
                continue
            fm, text = parse_frontmatter(manifest)
            raw_name = fm.get("name") if fm else None
            name = str(raw_name).strip() if raw_name else folder.name
            if fm is None:
                records.append(SkillRecord(name, folder.name, str(manifest), str(root), "malformed"))
                issues.append({"skill": name, "type": "invalid-frontmatter", "path": str(manifest)})
                continue

            expected_lock = str((root.parent / ".skill-lock.json").resolve())
            root_locks = [e for e in locks if str(Path(str(e.get("_lockfile", ""))).resolve()) == expected_lock]
            lock = lock_match(folder, name, root_locks)
            metadata_repo, metadata_path, metadata_pinned = github_cli_metadata(fm)
            lock_repo = normalize_repo_from_text(str(lock.get("sourceUrl") or lock.get("source") or "")) if lock else None
            if metadata_repo and lock_repo and (metadata_repo != lock_repo or (metadata_path and lock.get("skillPath") and metadata_path != normalize_skill_path(str(lock.get("skillPath"))))):
                records.append(SkillRecord(name, folder.name, str(manifest), str(root), "ambiguous"))
                issues.append({"skill": name, "type": "conflicting-github-provenance", "repos": sorted({metadata_repo, lock_repo})})
                continue
            if metadata_repo:
                records.append(
                    SkillRecord(
                        name=name,
                        folder=folder.name,
                        manifest=str(manifest),
                        root=str(root),
                        status="managed",
                        repo=metadata_repo,
                        source_url=repo_url(metadata_repo),
                        skill_path=metadata_path,
                        source_confidence="github-cli-metadata",
                        evidence="GitHub CLI metadata in the installed SKILL.md",
                        pinned=metadata_pinned,
                    )
                )
                continue
            if lock:
                repo = lock_repo
                if repo:
                    records.append(
                        SkillRecord(
                            name=name,
                            folder=folder.name,
                            manifest=str(manifest),
                            root=str(root),
                            status="managed",
                            repo=repo,
                            source_url=str(lock.get("sourceUrl") or repo_url(repo)),
                            skill_path=str(lock.get("skillPath") or "") or None,
                            source_confidence="lockfile",
                            evidence=f"GitHub CLI lock {lock.get('_lockfile')}",
                            pinned=bool(lock.get("pinnedRef")) if "pinnedRef" in lock else None,
                        )
                    )
                    continue

            registered = registry.get(registry_key(root, folder.name))
            if isinstance(registered, dict):
                repo = normalize_repo_from_text(str(registered.get("repo") or registered.get("source_url") or ""))
                registered_root = path_key(Path(str(registered.get("root") or root)))
                if repo and registered_root == path_key(root) and str(registered.get("folder") or folder.name) == folder.name:
                    records.append(
                        SkillRecord(
                            name=name,
                            folder=folder.name,
                            manifest=str(manifest),
                            root=str(root),
                            status="legacy-confident",
                            repo=repo,
                            source_url=str(registered.get("source_url") or repo_url(repo)),
                            skill_path=str(registered.get("skill_path") or "") or None,
                            source_confidence="manager-registry",
                            evidence=f"registered source {source_registry_path()}",
                        )
                    )
                    continue

            git_repo, _ = git_remote_repo(folder)
            candidates = strong_repo_candidates(manifest, fm, text)
            candidate_repos = [repo for repo, _ in candidates]
            if git_repo:
                candidate_repos.insert(0, git_repo)
            candidate_repos = list(dict.fromkeys(candidate_repos))
            if len(candidate_repos) > 1:
                records.append(SkillRecord(name, folder.name, str(manifest), str(root), "ambiguous"))
                issues.append({"skill": name, "type": "ambiguous-source", "repos": candidate_repos})
            else:
                records.append(SkillRecord(name, folder.name, str(manifest), str(root), "source-unknown"))
                issue = {"skill": name, "type": "source-unknown", "action": "confirm and register the exact GitHub repository URL once if this skill came from GitHub"}
                if candidate_repos:
                    issue["candidates"] = candidate_repos
                    issue["note"] = "candidate metadata is untrusted and was not registered automatically"
                issues.append(issue)

    for record in records:
        if not record.repo or record.status not in {"managed", "legacy-confident"}:
            continue
        candidate = exact_repo_skill_candidate(record.repo, record.skill_path)
        if candidate:
            record.locally_modified = skill_directories_differ(Path(record.manifest).parent, candidate.parent)
            if record.locally_modified:
                issues.append({"skill": record.name, "type": "locally-modified", "path": record.manifest, "action": "review or explicitly use --force after preserving customizations"})

    repos = sorted({r.repo for r in records if r.repo})
    return {
        "ok": True,
        "roots": [str(r) for r in roots],
        "lock_entries": len(locks),
        "registered_sources": len(registry),
        "skill_count": len(records),
        "repo_count": len(repos),
        "repos": repos,
        "skills": [asdict(r) for r in records],
        "issues": issues,
    }


def doctor() -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "python": sys.version.split()[0], "git": None, "gh": None, "gh_skill": False, "auth": "unknown", "errors": [], "warnings": []}
    git = shutil.which("git")
    if not git:
        out["errors"].append("git was not found in PATH")
    else:
        r = run([git, "--version"], timeout=5)
        out["git"] = r.stdout or r.stderr
    gh = shutil.which("gh")
    if not gh:
        out["errors"].append("GitHub CLI 'gh' was not found in PATH")
    else:
        r = run([gh, "--version"], timeout=5)
        out["gh"] = (r.stdout or r.stderr).splitlines()[0] if (r.stdout or r.stderr) else "unknown"
        h = run([gh, "skill", "update", "--help"], timeout=10)
        required = ("--dir", "--dry-run", "--all")
        out["gh_skill"] = h.exit_code == 0 and all(flag in (h.stdout + h.stderr) for flag in required)
        if not out["gh_skill"]:
            out["errors"].append("gh skill update is unavailable or lacks required --dir/--dry-run/--all flags")
        a = run([gh, "auth", "status"], timeout=10)
        out["auth"] = "authenticated" if a.exit_code == 0 else "not-authenticated-or-unavailable"
        if a.exit_code != 0:
            out["warnings"].append("gh auth status failed; private repository clone/update may fail")
    out["ok"] = not out["errors"]
    return out


def cache_path_error(dest: Path) -> str | None:
    root = (data_root() / "repos").resolve(strict=False)
    if (data_root() / "repos").exists() and is_link(data_root() / "repos"):
        return "cache root is a symlink or junction"
    try:
        dest.resolve(strict=False).relative_to(root)
    except ValueError:
        return "cache destination escapes the cache root"
    current = data_root() / "repos"
    try:
        relative_parts = dest.relative_to(current).parts
    except ValueError:
        return "cache destination is not below the cache root"
    for part in relative_parts:
        current = current / part
        if current.exists() and is_link(current):
            return f"cache component is a symlink or junction: {current}"
    return None


def inspect_cached_repo(repo: str, dest: Path) -> tuple[bool, str | None]:
    if is_link(dest) or not dest.is_dir() or is_link(dest / ".git") or not (dest / ".git").is_dir():
        return False, "cache is not an ordinary Git worktree"
    inside = run(["git", "-C", str(dest), "rev-parse", "--is-inside-work-tree"], timeout=10)
    if inside.exit_code != 0 or inside.stdout.strip().lower() != "true":
        return False, "cache is not a complete Git worktree"
    remote = run(["git", "-C", str(dest), "remote", "get-url", "origin"], timeout=10)
    actual = normalize_repo_from_text(remote.stdout) if remote.exit_code == 0 else None
    if actual != repo:
        return False, f"origin mismatch: expected {repo}, found {remote.stdout or 'missing'}"
    risky = run([
        "git", "-C", str(dest), "config", "--local", "--name-only", "--get-regexp",
        r"^(core\.hooksPath|core\.fsmonitor|core\.sshCommand|credential\.helper|filter\.|include\.|includeIf\.)",
    ], timeout=10)
    if risky.exit_code == 0 and risky.stdout:
        return False, f"unsafe repository-local Git configuration: {risky.stdout.splitlines()[0]}"
    return True, None


def ensure_repo(repo: str, fetch_existing: bool, timeout: int = 45) -> dict[str, Any]:
    normalized = normalize_repo_from_text(repo)
    if not normalized or normalized != repo:
        return {"repo": repo, "status": "cache-invalid", "reason": "repository identity is not canonical OWNER/REPO"}
    dest = repo_cache_path(repo)
    path_error = cache_path_error(dest)
    if path_error:
        return {"repo": repo, "path": str(dest), "status": "cache-invalid", "reason": path_error}
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        valid, reason = inspect_cached_repo(repo, dest)
        if not valid:
            status = "remote-mismatch" if reason and reason.startswith("origin mismatch") else "cache-invalid"
            return {"repo": repo, "path": str(dest), "status": status, "reason": reason}
        if not fetch_existing:
            return {"repo": repo, "path": str(dest), "status": "cached"}
        disabled_hooks = data_root() / "disabled-git-hooks"
        disabled_hooks.mkdir(parents=True, exist_ok=True)
        git_prefix = ["git", "-c", f"core.hooksPath={disabled_hooks}", "-c", "protocol.file.allow=never", "-C", str(dest)]
        f = run([*git_prefix, "fetch", "--prune", "origin"], timeout=timeout)
        if f.exit_code != 0:
            return {"repo": repo, "path": str(dest), "status": "fetch-failed", "command": asdict(f)}
        p = run([*git_prefix, "pull", "--ff-only"], timeout=timeout)
        status = "fetched" if p.exit_code == 0 else "fetch-partial"
        return {"repo": repo, "path": str(dest), "status": status, "fetch": asdict(f), "pull": asdict(p)}

    staging_parent = Path(tempfile.mkdtemp(prefix=f".{dest.name}.clone-", dir=dest.parent))
    staged = staging_parent / dest.name
    gh = shutil.which("gh")
    if gh:
        cmd = [gh, "repo", "clone", repo, str(staged), "--", "--filter=blob:none", "--depth=1"]
    else:
        cmd = ["git", "clone", "--filter=blob:none", "--depth=1", repo_url(repo), str(staged)]
    try:
        r = run(cmd, timeout=timeout)
        if r.exit_code != 0:
            return {"repo": repo, "path": str(dest), "status": "clone-failed", "command": asdict(r)}
        valid, reason = inspect_cached_repo(repo, staged)
        if not valid:
            return {"repo": repo, "path": str(dest), "status": "clone-failed", "reason": reason, "command": asdict(r)}
        if dest.exists():
            return {"repo": repo, "path": str(dest), "status": "path-conflict"}
        os.replace(staged, dest)
        return {"repo": repo, "path": str(dest), "status": "cloned", "command": asdict(r)}
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)


def bootstrap(scope: str, include_legacy: bool, fetch_existing: bool, max_clones: int | None = None) -> dict[str, Any]:
    d = discover(scope, include_legacy)
    results: list[dict[str, Any]] = []
    clone_count = 0
    for repo in d["repos"]:
        dest = repo_cache_path(repo)
        missing = not (dest / ".git").exists()
        if missing and max_clones is not None and clone_count >= max_clones:
            results.append({"repo": repo, "path": str(dest), "status": "deferred-limit"})
            continue
        item = ensure_repo(repo, fetch_existing=fetch_existing)
        if item["status"] == "cloned":
            clone_count += 1
        results.append(item)
    # Re-evaluate local modifications against the refreshed/created cache.
    d = discover(scope, include_legacy)
    state = {
        "version": 1,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "discovery": d,
        "repos": results,
    }
    data_root().mkdir(parents=True, exist_ok=True)
    atomic_write_text(data_root() / "state.json", json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    bad = {"clone-failed", "path-conflict", "fetch-failed", "fetch-partial", "cache-invalid", "remote-mismatch"}
    return {"ok": not any(r["status"] in bad for r in results), "discovery": d, "repos": results, "state": str(data_root() / "state.json")}


def repo_skill_candidates(repo: str, installed_name: str, folder_name: str) -> list[Path]:
    root = repo_cache_path(repo)
    if not root.exists():
        return []
    exact: list[Path] = []
    fallback: list[Path] = []
    for manifest in root.rglob("SKILL.md"):
        if ".git" in manifest.parts:
            continue
        if is_link(manifest) or is_link(manifest.parent):
            continue
        try:
            manifest.resolve(strict=True).relative_to(root.resolve(strict=True))
        except (OSError, ValueError):
            continue
        fm, _ = parse_frontmatter(manifest)
        name = str(fm.get("name", "")).strip() if fm else ""
        if name == installed_name:
            exact.append(manifest)
        elif manifest.parent.name in {installed_name, folder_name}:
            fallback.append(manifest)
    return exact if exact else fallback


def exact_repo_skill_candidate(repo: str, skill_path: str | None) -> Path | None:
    normalized = normalize_skill_path(skill_path)
    if not normalized:
        return None
    root = repo_cache_path(repo)
    candidate = root.joinpath(*PurePosixPath(normalized).parts)
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return None
    if not candidate.is_file() or is_link(candidate) or is_link(candidate.parent):
        return None
    return candidate


def normalized_manifest_bytes(path: Path) -> bytes | None:
    fm, text = parse_frontmatter(path)
    if fm is None:
        return None
    safe = json.loads(json.dumps(fm, default=str))
    metadata = safe.get("metadata")
    if isinstance(metadata, dict):
        for key in list(metadata):
            if key.startswith("github-") or key == "local-path":
                metadata.pop(key)
        if not metadata:
            safe.pop("metadata", None)
    lines = text.splitlines()
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None
    body = "\n".join(lines[end + 1:]).strip()
    return (json.dumps(safe, sort_keys=True, ensure_ascii=False) + "\n" + body).encode("utf-8")


def skill_directory_digest(root: Path) -> str | None:
    if not root.is_dir() or is_link(root):
        return None
    digest = hashlib.sha256()
    files: list[Path] = []
    for path in root.rglob("*"):
        if any(part in {".git", "__pycache__"} for part in path.parts) or path.suffix == ".pyc":
            continue
        if is_link(path):
            return None
        if path.is_file():
            files.append(path)
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        content = normalized_manifest_bytes(path) if path.name == "SKILL.md" else path.read_bytes()
        if content is None:
            return None
        digest.update(rel.encode("utf-8") + b"\0" + content + b"\0")
    return digest.hexdigest()


def skill_directories_differ(installed: Path, upstream: Path) -> bool | None:
    installed_digest = skill_directory_digest(installed)
    upstream_digest = skill_directory_digest(upstream)
    if installed_digest is None or upstream_digest is None:
        return None
    return installed_digest != upstream_digest


def backup_skill(skill_dir: Path) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = data_root() / "backups" / stamp / skill_dir.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_dir, dest)
    return dest


def adopt_legacy(discovery: dict[str, Any], scope: str, allow_modified: bool = False) -> dict[str, Any]:
    gh = shutil.which("gh") or "gh"
    items: list[dict[str, Any]] = []
    for rec in discovery["skills"]:
        if rec["status"] != "legacy-confident" or rec.get("source_confidence") != "manager-registry" or not rec.get("repo"):
            continue
        if rec.get("locally_modified") and not allow_modified:
            items.append({"skill": rec["name"], "status": "needs-review", "reason": "local modifications differ from the cached source; use --force only after review"})
            continue
        skill_dir = Path(rec["manifest"]).parent
        if tree_has_links(skill_dir):
            items.append({"skill": rec["name"], "status": "needs-review", "reason": "legacy skill contains a symlink or junction"})
            continue
        exact = exact_repo_skill_candidate(rec["repo"], rec.get("skill_path"))
        if rec.get("skill_path") and exact is None:
            items.append({"skill": rec["name"], "status": "needs-review", "reason": "registered upstream path is missing or unsafe"})
            continue
        if exact:
            exact_fm, _ = parse_frontmatter(exact)
            if not exact_fm or str(exact_fm.get("name") or "").strip() != rec["name"]:
                items.append({"skill": rec["name"], "status": "needs-review", "reason": "registered upstream path does not match the installed skill name"})
                continue
        candidates = [exact] if exact else repo_skill_candidates(rec["repo"], rec["name"], rec["folder"])
        if len(candidates) != 1:
            items.append({"skill": rec["name"], "status": "needs-review", "reason": f"expected exactly one upstream match, found {len(candidates)}"})
            continue
        candidate = candidates[0]
        repo_root = repo_cache_path(rec["repo"])
        rel = candidate.relative_to(repo_root).as_posix()
        rec_root = Path(rec["root"]).expanduser()
        target_scope = "user" if rec_root in {user_root(), legacy_root()} else "project"
        if rec_root.resolve(strict=False) == legacy_root().resolve(strict=False):
            existing_target = user_root() / rec["folder"]
            if existing_target.exists():
                items.append({"skill": rec["name"], "status": "destination-conflict", "reason": f"refusing to overwrite existing user skill {existing_target}"})
                continue
        backup = backup_skill(skill_dir)
        cmd = [gh, "skill", "install", rec["repo"], rel, "--agent", "codex", "--scope", target_scope, "--force"]
        if any(part.startswith(".") for part in Path(rel).parts):
            cmd.append("--allow-hidden-dirs")
        r = run(cmd, timeout=90)
        status = "migration-failed"
        removed_legacy = False
        verified = False
        if r.exit_code == 0:
            post = discover(target_scope, False)
            verified = any(
                item.get("status") == "managed"
                and item.get("repo") == rec["repo"]
                and normalize_skill_path(item.get("skill_path")) == normalize_skill_path(rel)
                and Path(item["manifest"]).is_file()
                for item in post["skills"]
            )
            status = "migrated" if verified else "migration-unverified"
        if verified and rec_root.resolve(strict=False) == legacy_root().resolve(strict=False) and skill_dir.exists():
            # The provenance-aware user-scope copy now lives under ~/.agents/skills.
            # Remove the obsolete legacy copy; the backup remains available.
            shutil.rmtree(skill_dir)
            removed_legacy = True
        items.append({"skill": rec["name"], "repo": rec["repo"], "skill_path": rel, "backup": str(backup), "verified": verified, "removed_legacy_copy": removed_legacy, "status": status, "command": asdict(r)})
    blocking = {"migration-failed", "migration-unverified", "destination-conflict", "needs-review"}
    return {"ok": not any(i["status"] in blocking for i in items), "items": items}


def update_roots(roots: Iterable[Path], apply: bool, force: bool) -> dict[str, Any]:
    gh = shutil.which("gh") or "gh"
    items: list[dict[str, Any]] = []
    ok = True
    for root in roots:
        safe_root, root_error = validate_skill_root(root)
        if not safe_root:
            ok = False
            items.append({"root": str(root), "status": "unsafe-root", "reason": root_error})
            continue
        if not root.exists():
            items.append({"root": str(root), "status": "missing-root"})
            continue
        unsafe_children = [
            str(child)
            for child in root.iterdir()
            if child.is_dir() and not child.name.startswith(".") and is_link(child)
        ]
        if unsafe_children:
            ok = False
            items.append({"root": str(root), "status": "unsafe-root", "reason": "skill root contains symlink or junction entries", "paths": unsafe_children})
            continue
        if not any(root.glob("*/SKILL.md")):
            items.append({"root": str(root), "status": "no-skills"})
            continue
        dry = run([gh, "skill", "update", "--dir", str(root), "--dry-run"], timeout=90)
        if dry.exit_code != 0:
            ok = False
            items.append({"root": str(root), "status": "dry-run-failed", "dry_run": asdict(dry)})
            continue
        if not apply:
            items.append({"root": str(root), "status": "checked", "dry_run": asdict(dry)})
            continue
        cmd = [gh, "skill", "update", "--dir", str(root), "--all"]
        if force:
            cmd.append("--force")
        upd = run(cmd, timeout=120)
        if upd.exit_code != 0:
            ok = False
        items.append({"root": str(root), "status": "updated" if upd.exit_code == 0 else "update-failed", "dry_run": asdict(dry), "update": asdict(upd)})
    return {"ok": ok, "items": items}


def audit(scope: str, include_legacy: bool) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    names: dict[str, list[str]] = {}
    count = 0
    for root in selected_roots(scope, include_legacy):
        if not root.exists():
            continue
        safe_root, root_error = validate_skill_root(root)
        if not safe_root:
            issues.append({"severity": "error", "code": "unsafe-skill-root", "path": str(root), "reason": root_error})
            continue
        for folder in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name):
            if folder.name.startswith("."):
                continue
            manifest = folder / "SKILL.md"
            count += 1
            if is_link(folder):
                issues.append({"severity": "error", "code": "unsafe-skill-link", "path": str(folder)})
                continue
            if not manifest.is_file():
                issues.append({"severity": "error", "code": "missing-manifest", "path": str(manifest)})
                continue
            fm, _ = parse_frontmatter(manifest)
            if fm is None:
                issues.append({"severity": "error", "code": "invalid-frontmatter", "path": str(manifest)})
                continue
            name = str(fm.get("name", "")).strip()
            desc = str(fm.get("description", "")).strip()
            if not name:
                issues.append({"severity": "error", "code": "missing-name", "path": str(manifest)})
            else:
                names.setdefault(name, []).append(str(manifest))
                if len(name) > 64 or not NAME_RE.match(name) or "--" in name:
                    issues.append({"severity": "error", "code": "invalid-name", "path": str(manifest), "name": name})
                if manifest.parent.name != name:
                    issues.append({"severity": "warning", "code": "name-folder-mismatch", "path": str(manifest), "name": name, "folder": manifest.parent.name})
            if not desc:
                issues.append({"severity": "error", "code": "missing-description", "path": str(manifest)})
            elif len(desc) > 1024:
                issues.append({"severity": "error", "code": "description-too-long", "path": str(manifest)})
            oy = manifest.parent / "agents" / "openai.yaml"
            if oy.exists():
                text = oy.read_text(encoding="utf-8", errors="replace")
                if re.search(r"allow_implicit_invocation\s*:\s*false", text, re.I):
                    issues.append({"severity": "info", "code": "implicit-disabled", "path": str(oy)})
    for name, paths in names.items():
        if len(paths) > 1:
            issues.append({"severity": "warning", "code": "duplicate-name", "name": name, "paths": paths})
    errors = sum(i["severity"] == "error" for i in issues)
    return {"ok": errors == 0, "skill_count": count, "error_count": errors, "warning_count": sum(i["severity"] == "warning" for i in issues), "issues": issues}


def emit(data: dict[str, Any], json_mode: bool) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main() -> int:
    p = argparse.ArgumentParser(description="Codex GitHub Skill Manager plugin helper")
    p.add_argument("command", choices=["doctor", "discover", "bootstrap", "check", "sync", "audit", "register"])
    p.add_argument("--scope", choices=["user", "project", "all"], default="user")
    p.add_argument("--include-legacy", action="store_true")
    p.add_argument("--adopt-legacy", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--fetch-existing", action="store_true")
    p.add_argument("--max-clones", type=int, default=None)
    p.add_argument("--skill", help="Installed skill name for the register command")
    p.add_argument("--repo", help="GitHub OWNER/REPO or repository URL for the register command")
    p.add_argument("--skill-path", help="Optional exact path to the upstream SKILL.md")
    p.add_argument("--root", help="Exact installed skill root for register when the same name exists in multiple scopes")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.command == "register":
        if not args.skill or not args.repo:
            emit({"ok": False, "error": "register requires --skill and --repo"}, args.json)
            return 7
        r = register_source(args.skill, args.repo, args.skill_path, args.root)
        emit(r, args.json)
        return 0 if r["ok"] else 7
    if args.command == "doctor":
        d = doctor(); emit(d, args.json); return 0 if d["ok"] else 2
    if args.command == "discover":
        d = discover(args.scope, args.include_legacy); emit(d, args.json); return 0
    if args.command == "audit":
        a = audit(args.scope, args.include_legacy); emit(a, args.json); return 0 if a["ok"] else 3

    d = doctor()
    if args.command == "bootstrap":
        # Bootstrap discovery/clone can still work for public repositories with git
        # even when gh/gh skill is unavailable. Do not gate it on the full doctor.
        b = bootstrap(args.scope, args.include_legacy, fetch_existing=args.fetch_existing, max_clones=args.max_clones)
        emit({"ok": b["ok"], "doctor": d, "bootstrap": b}, args.json); return 0 if b["ok"] else 4

    if not d["ok"]:
        emit({"ok": False, "doctor": d}, args.json); return 2

    # For checks/syncs refresh the source cache first.
    b = bootstrap(args.scope, args.include_legacy, fetch_existing=True, max_clones=args.max_clones)
    roots = selected_roots(args.scope, args.include_legacy)
    update_targets = [root for root in roots if root.resolve() != legacy_root().resolve()]

    if not b["ok"]:
        result = {"ok": False, "doctor": d, "bootstrap": b, "blocked": "source cache refresh failed; no migration or update mutation was attempted"}
        emit(result, args.json)
        return 5 if args.command == "check" else 6

    modified = [item for item in b["discovery"]["skills"] if item.get("locally_modified")]
    if args.command == "sync" and modified and not args.force:
        result = {
            "ok": False,
            "doctor": d,
            "bootstrap": b,
            "blocked": "locally modified skills were detected; review them and pass --force only if overwriting is intended",
            "locally_modified": modified,
        }
        emit(result, args.json)
        return 6

    if args.command == "check":
        c = update_roots(update_targets, apply=False, force=False)
        result = {"ok": b["ok"] and c["ok"], "doctor": d, "bootstrap": b, "check": c}
        emit(result, args.json); return 0 if result["ok"] else 5

    migration = {"ok": True, "items": []}
    if args.adopt_legacy:
        migration = adopt_legacy(b["discovery"], args.scope, allow_modified=args.force)
        # Rediscover after migration so state reflects GitHub CLI provenance where available.
        b["post_migration_discovery"] = discover(args.scope, args.include_legacy)
        if not migration["ok"]:
            a = audit(args.scope, args.include_legacy)
            result = {"ok": False, "doctor": d, "bootstrap": b, "migration": migration, "audit": a, "blocked": "legacy adoption did not verify; normal updates were not attempted"}
            emit(result, args.json)
            return 6

    u = update_roots(update_targets, apply=True, force=args.force)
    a = audit(args.scope, args.include_legacy)
    result = {"ok": b["ok"] and migration["ok"] and u["ok"] and a["ok"], "doctor": d, "bootstrap": b, "migration": migration, "update": u, "audit": a}
    emit(result, args.json)
    return 0 if result["ok"] else 6


if __name__ == "__main__":
    raise SystemExit(main())
