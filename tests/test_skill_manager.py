from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MANAGER_PATH = PLUGIN_ROOT / "skills" / "manage-github-skills" / "scripts" / "skill_manager.py"
spec = importlib.util.spec_from_file_location("gsm", MANAGER_PATH)
gsm = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules["gsm"] = gsm
spec.loader.exec_module(gsm)


def skill(path: Path, name: str, extra_frontmatter: str = "", body: str = "") -> None:
    path.mkdir(parents=True, exist_ok=True)
    text = f"---\nname: {name}\ndescription: Test skill {name}\n{extra_frontmatter}---\n{body}\n"
    (path / "SKILL.md").write_text(text, encoding="utf-8")


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_plugin_manifest_and_hook_json(self):
        manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text())
        marketplace = json.loads((PLUGIN_ROOT / ".agents" / "plugins" / "marketplace.json").read_text())
        hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())
        self.assertEqual(manifest["name"], "codex-github-skill-manager")
        self.assertEqual(manifest["skills"], "./skills/")
        allowed = {"id", "name", "version", "description", "skills", "apps", "mcpServers", "interface", "author", "homepage", "repository", "license", "keywords"}
        self.assertFalse(set(manifest) - allowed)
        self.assertNotIn("hooks", manifest)
        self.assertTrue((PLUGIN_ROOT / "hooks" / "hooks.json").exists())
        self.assertEqual(manifest["interface"]["category"], "Developer Tools")
        self.assertLessEqual(len(manifest["interface"]["shortDescription"]), 30)
        self.assertTrue(manifest["author"]["name"])
        for key in ("displayName", "shortDescription", "longDescription", "developerName", "category", "capabilities", "defaultPrompt"):
            self.assertIn(key, manifest["interface"])
        self.assertIn("SessionStart", hooks["hooks"])
        self.assertEqual(hooks["hooks"]["SessionStart"][0]["matcher"], "^(startup|resume)$")
        self.assertEqual(marketplace["name"], "codex-github-skill-manager")
        entry = next(item for item in marketplace["plugins"] if item["name"] == manifest["name"])
        self.assertEqual(entry["source"], {"source": "local", "path": "."})
        openai_yaml = (PLUGIN_ROOT / "skills" / "manage-github-skills" / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("- CODEX", openai_yaml)

    def test_lockfile_discovery_managed(self):
        root = self.home / ".agents" / "skills"
        skill(root / "foo", "foo")
        lock = {
            "version": 3,
            "skills": {
                "foo": {
                    "source": "owner/repo",
                    "sourceType": "github",
                    "sourceUrl": "https://github.com/owner/repo.git",
                    "skillPath": "skills/foo/SKILL.md"
                }
            }
        }
        (self.home / ".agents").mkdir(exist_ok=True)
        (self.home / ".agents" / ".skill-lock.json").write_text(json.dumps(lock))
        d = gsm.discover("user", False)
        self.assertEqual(d["skills"][0]["status"], "managed")
        self.assertEqual(d["skills"][0]["repo"], "owner/repo")

    def test_two_skills_one_repo_deduplicates(self):
        root = self.home / ".agents" / "skills"
        skill(root / "foo", "foo")
        skill(root / "bar", "bar")
        lock = {"version": 3, "skills": {
            "foo": {"source": "owner/repo", "sourceUrl": "https://github.com/owner/repo.git", "skillPath": "skills/foo/SKILL.md"},
            "bar": {"source": "owner/repo", "sourceUrl": "https://github.com/owner/repo.git", "skillPath": "skills/bar/SKILL.md"},
        }}
        (self.home / ".agents" / ".skill-lock.json").write_text(json.dumps(lock))
        d = gsm.discover("user", False)
        self.assertEqual(d["repo_count"], 1)
        self.assertEqual(d["repos"], ["owner/repo"])

    def test_legacy_explicit_repository_detected(self):
        root = self.home / ".codex" / "skills"
        skill(root / "legacy", "legacy", "repository: https://github.com/acme/skills.git\n")
        d = gsm.discover("user", True)
        rec = next(x for x in d["skills"] if x["name"] == "legacy")
        self.assertEqual(rec["status"], "source-unknown")
        self.assertIsNone(rec["repo"])
        issue = next(x for x in d["issues"] if x.get("skill") == "legacy")
        self.assertEqual(issue["candidates"], ["acme/skills"])

    def test_incidental_github_link_is_not_source(self):
        root = self.home / ".codex" / "skills"
        skill(root / "legacy", "legacy", body="See https://github.com/random/example/issues/1 for discussion")
        d = gsm.discover("user", True)
        rec = next(x for x in d["skills"] if x["name"] == "legacy")
        self.assertEqual(rec["status"], "source-unknown")

    def test_ambiguous_source_never_auto_selected(self):
        root = self.home / ".codex" / "skills"
        skill(root / "legacy", "legacy", "repository: https://github.com/acme/one.git\n", body="Upstream: https://github.com/acme/two")
        d = gsm.discover("user", True)
        rec = next(x for x in d["skills"] if x["name"] == "legacy")
        self.assertEqual(rec["status"], "ambiguous")
        self.assertIsNone(rec["repo"])

    def test_bootstrap_calls_clone_once_per_repo(self):
        root = self.home / ".agents" / "skills"
        skill(root / "foo", "foo")
        skill(root / "bar", "bar")
        lock = {"version": 3, "skills": {
            "foo": {"source": "owner/repo", "sourceUrl": "https://github.com/owner/repo.git", "skillPath": "skills/foo/SKILL.md"},
            "bar": {"source": "owner/repo", "sourceUrl": "https://github.com/owner/repo.git", "skillPath": "skills/bar/SKILL.md"},
        }}
        (self.home / ".agents" / ".skill-lock.json").write_text(json.dumps(lock))
        calls = []
        def fake(repo, fetch_existing, timeout=45):
            calls.append(repo)
            return {"repo": repo, "path": str(gsm.repo_cache_path(repo)), "status": "cloned"}
        with patch.object(gsm, "ensure_repo", side_effect=fake):
            b = gsm.bootstrap("user", False, False)
        self.assertTrue(b["ok"])
        self.assertEqual(calls, ["owner/repo"])

    def test_failed_dry_run_blocks_update(self):
        root = self.home / ".agents" / "skills"
        skill(root / "foo", "foo")
        calls = []
        def fake_run(cmd, timeout=60, cwd=None):
            calls.append(cmd)
            if "--dry-run" in cmd:
                return gsm.CommandResult(cmd, 1, "", "dry run failed")
            return gsm.CommandResult(cmd, 0, "ok", "")
        with patch.object(gsm, "run", side_effect=fake_run):
            r = gsm.update_roots([root], apply=True, force=False)
        self.assertFalse(r["ok"])
        self.assertFalse(any("--all" in c for c in calls))

    def test_legacy_adoption_creates_backup(self):
        local = self.home / ".codex" / "skills" / "legacy"
        skill(local, "legacy", "repository: https://github.com/acme/skills.git\n")
        clone = gsm.repo_cache_path("acme/skills") / "skills" / "legacy"
        skill(clone, "legacy")
        discovery = {"skills": [{
            "name": "legacy", "folder": "legacy", "manifest": str(local / "SKILL.md"), "root": str(local.parent),
            "status": "legacy-confident", "repo": "acme/skills", "source_confidence": "manager-registry",
            "skill_path": "skills/legacy/SKILL.md",
        }]}
        def fake_run(cmd, timeout=60, cwd=None):
            skill(
                gsm.user_root() / "legacy",
                "legacy",
                "metadata:\n  github-repo: https://github.com/acme/skills.git\n  github-path: skills/legacy/SKILL.md\n",
            )
            return gsm.CommandResult(cmd, 0, "installed", "")
        with patch.object(gsm, "run", side_effect=fake_run):
            a = gsm.adopt_legacy(discovery, "user")
        self.assertTrue(a["ok"])
        self.assertEqual(a["items"][0]["status"], "migrated")
        self.assertTrue(Path(a["items"][0]["backup"]).exists())
        self.assertIn("skills/legacy/SKILL.md", a["items"][0]["skill_path"])

    def test_audit_detects_bad_name_folder(self):
        root = self.home / ".agents" / "skills"
        skill(root / "wrong-folder", "right-name")
        a = gsm.audit("user", False)
        codes = {i["code"] for i in a["issues"]}
        self.assertIn("name-folder-mismatch", codes)


    def test_personal_installer_creates_marketplace(self):
        installer = PLUGIN_ROOT / "tools" / "install_personal.py"
        install_home = self.home / "install-home"
        p = subprocess.run([sys.executable, str(installer), "--home", str(install_home)], text=True, capture_output=True, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr)
        market = json.loads((install_home / ".agents" / "plugins" / "marketplace.json").read_text())
        entry = next(x for x in market["plugins"] if x["name"] == "codex-github-skill-manager")
        self.assertEqual(entry["source"]["path"], "./.codex/plugins/codex-github-skill-manager")
        self.assertEqual(entry["category"], "Developer Tools")
        self.assertTrue((install_home / ".codex" / "plugins" / "codex-github-skill-manager" / ".codex-plugin" / "plugin.json").exists())

    def test_session_hook_always_returns_valid_continue_json(self):
        hook = PLUGIN_ROOT / "skills" / "manage-github-skills" / "scripts" / "session_start.py"
        env = os.environ.copy()
        # PATH intentionally minimal so doctor/bootstrap can fail safely.
        env["PATH"] = str(Path(sys.executable).parent)
        p = subprocess.run([sys.executable, str(hook)], text=True, capture_output=True, env=env, timeout=10)
        self.assertEqual(p.returncode, 0)
        payload = json.loads(p.stdout)
        self.assertTrue(payload["continue"])
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "SessionStart")

    def test_session_hook_discovers_projects_but_bootstraps_only_user_scope(self):
        hook = (PLUGIN_ROOT / "skills" / "manage-github-skills" / "scripts" / "session_start.py").read_text(encoding="utf-8")
        self.assertIn('"discover", "--scope", "all", "--include-legacy", "--json"', hook)
        self.assertIn('"bootstrap", "--scope", "user", "--include-legacy", "--max-clones", "8", "--json"', hook)
        self.assertNotIn('"bootstrap", "--scope", "all"', hook)


    def test_register_source_makes_unknown_legacy_discoverable(self):
        root = self.home / ".codex" / "skills"
        skill(root / "legacy", "legacy")
        before = gsm.discover("user", True)
        rec = next(x for x in before["skills"] if x["name"] == "legacy")
        self.assertEqual(rec["status"], "source-unknown")
        registered = gsm.register_source("legacy", "https://github.com/acme/skills")
        self.assertTrue(registered["ok"])
        after = gsm.discover("user", True)
        rec = next(x for x in after["skills"] if x["name"] == "legacy")
        self.assertEqual(rec["status"], "legacy-confident")
        self.assertEqual(rec["repo"], "acme/skills")
        self.assertEqual(rec["source_confidence"], "manager-registry")

    def test_register_source_rejects_invalid_repo(self):
        result = gsm.register_source("legacy", "not a github repository")
        self.assertFalse(result["ok"])
        self.assertFalse(gsm.source_registry_path().exists())

    def test_unknown_skill_reports_source_unknown_without_guessing(self):
        root = self.home / ".agents" / "skills"
        skill(root / "mystery", "mystery", body="See https://github.com/random/example/issues/1")
        result = gsm.discover("user", False)
        self.assertEqual(result["skills"][0]["status"], "source-unknown")
        self.assertTrue(any(i.get("type") == "source-unknown" for i in result["issues"]))


    def test_same_name_user_and_project_use_scope_local_lockfiles(self):
        user = self.home / ".agents" / "skills"
        project = self.home / "repo"
        project_skills = project / ".agents" / "skills"
        skill(user / "same", "same")
        skill(project_skills / "same", "same")
        (self.home / ".agents" / ".skill-lock.json").write_text(json.dumps({"version": 3, "skills": {"same": {"source": "user/repo", "sourceUrl": "https://github.com/user/repo.git", "skillPath": "skills/same/SKILL.md"}}}))
        (project / ".agents" / ".skill-lock.json").write_text(json.dumps({"version": 3, "skills": {"same": {"source": "project/repo", "sourceUrl": "https://github.com/project/repo.git", "skillPath": "skills/same/SKILL.md"}}}))
        with patch.object(gsm, "project_root", return_value=project), patch.object(Path, "cwd", return_value=project):
            result = gsm.discover("all", False)
        by_root = {x["root"]: x for x in result["skills"]}
        self.assertEqual(by_root[str(user)]["repo"], "user/repo")
        self.assertEqual(by_root[str(project_skills)]["repo"], "project/repo")

    def test_empty_legacy_root_is_skipped_during_update(self):
        root = self.home / ".codex" / "skills"
        root.mkdir(parents=True)
        with patch.object(gsm, "run") as mocked:
            result = gsm.update_roots([root], apply=True, force=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["items"][0]["status"], "no-skills")
        mocked.assert_not_called()

    def test_cli_sync_end_to_end_with_fake_gh(self):
        root = self.home / ".agents" / "skills"
        skill(root / "local-skill", "local-skill")
        legacy = self.home / ".codex" / "skills"
        skill(legacy / "old-skill", "old-skill")
        fakebin = self.home / "fakebin"
        fakebin.mkdir()
        log = self.home / "gh.log"

        gh_impl = fakebin / "gh.py"
        gh_impl.write_text(
            "#!/usr/bin/env python3\n"
            "import os,sys\n"
            "args=sys.argv[1:]\n"
            "log=os.environ.get('FAKE_GH_LOG')\n"
            "if log:\n"
            "    with open(log,'a',encoding='utf-8') as f: f.write(' '.join(args)+'\\n')\n"
            "if args == ['--version']:\n"
            "    print('gh version 9.9.9')\n"
            "elif args[:3] == ['skill','update','--help']:\n"
            "    print('--dir --dry-run --all --force')\n"
            "elif args[:2] == ['auth','status']:\n"
            "    print('logged in')\n"
            "elif args[:2] == ['skill','update']:\n"
            "    print('ok')\n"
            "else:\n"
            "    print('ok')\n",
            encoding="utf-8",
        )
        gh_impl.chmod(0o755)

        git_impl = fakebin / "git.py"
        git_impl.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "args=sys.argv[1:]\n"
            "if args == ['--version']:\n"
            "    print('git version 2.99.0')\n"
            "    raise SystemExit(0)\n"
            "if args[:2] == ['rev-parse','--show-toplevel']:\n"
            "    raise SystemExit(1)\n"
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        git_impl.chmod(0o755)
        if os.name == "nt":
            (fakebin / "gh.cmd").write_text(f'@"{sys.executable}" "%~dp0gh.py" %*\n', encoding="utf-8")
            (fakebin / "git.cmd").write_text(f'@"{sys.executable}" "%~dp0git.py" %*\n', encoding="utf-8")
        else:
            shutil.copy2(gh_impl, fakebin / "gh")
            shutil.copy2(git_impl, fakebin / "git")
            (fakebin / "gh").chmod(0o755)
            (fakebin / "git").chmod(0o755)

        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["USERPROFILE"] = str(self.home)
        env["FAKE_GH_LOG"] = str(log)
        env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
        p = subprocess.run(
            [sys.executable, str(MANAGER_PATH), "sync", "--scope", "user", "--include-legacy", "--json"],
            text=True,
            capture_output=True,
            env=env,
            timeout=20,
        )
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        payload = json.loads(p.stdout)
        self.assertTrue(payload["ok"])
        lines = log.read_text(encoding="utf-8").splitlines()
        dry_index = next(i for i, line in enumerate(lines) if "skill update --dir" in line and "--dry-run" in line)
        apply_index = next(i for i, line in enumerate(lines) if "skill update --dir" in line and "--all" in line)
        self.assertLess(dry_index, apply_index)
        self.assertFalse(any(str(legacy) in line and "skill update" in line for line in lines))

    def test_github_cli_metadata_marks_project_skill_managed_without_project_lock(self):
        project = self.home / "repo"
        root = project / ".agents" / "skills"
        skill(
            root / "project-skill",
            "project-skill",
            "metadata:\n  github-repo: https://github.com/Acme/Project.git\n  github-path: skills/project-skill/SKILL.md\n",
        )
        with patch.object(gsm, "project_root", return_value=project), patch.object(Path, "cwd", return_value=project):
            result = gsm.discover("project", False)
        rec = result["skills"][0]
        self.assertEqual(rec["status"], "managed")
        self.assertEqual(rec["repo"], "acme/project")
        self.assertEqual(rec["source_confidence"], "github-cli-metadata")

    def test_registry_is_scope_bound_and_same_name_requires_root(self):
        user = self.home / ".agents" / "skills"
        legacy = self.home / ".codex" / "skills"
        skill(user / "same", "same")
        skill(legacy / "same", "same")
        ambiguous = gsm.register_source("same", "owner/repo")
        self.assertFalse(ambiguous["ok"])
        registered = gsm.register_source("same", "owner/repo", root=str(legacy))
        self.assertTrue(registered["ok"])
        result = gsm.discover("all", True)
        by_root = {x["root"]: x for x in result["skills"]}
        self.assertEqual(by_root[str(user)]["status"], "source-unknown")
        self.assertEqual(by_root[str(legacy)]["status"], "legacy-confident")

    def test_corrupt_registry_is_preserved_and_blocks_registration(self):
        legacy = self.home / ".codex" / "skills"
        skill(legacy / "legacy", "legacy")
        registry = gsm.source_registry_path()
        registry.parent.mkdir(parents=True)
        registry.write_text("{broken", encoding="utf-8")
        result = gsm.register_source("legacy", "owner/repo")
        self.assertFalse(result["ok"])
        self.assertEqual(registry.read_text(encoding="utf-8"), "{broken")

    def test_registering_same_instance_twice_is_idempotent(self):
        legacy = self.home / ".codex" / "skills"
        skill(legacy / "legacy", "legacy")
        first = gsm.register_source("legacy", "Owner/Repo", root=str(legacy))
        second = gsm.register_source("legacy", "https://github.com/owner/repo.git", root=str(legacy))
        self.assertTrue(first["ok"] and second["ok"])
        payload = json.loads(gsm.source_registry_path().read_text(encoding="utf-8"))
        self.assertEqual(len(payload["skills"]), 1)
        self.assertEqual(next(iter(payload["skills"].values()))["repo"], "owner/repo")

    def test_concurrent_source_registration_preserves_both_entries(self):
        root = self.home / ".agents" / "skills"
        skill(root / "first", "first")
        skill(root / "second", "second")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda item: gsm.register_source(item, f"owner/{item}"), ("first", "second")))
        self.assertTrue(all(item["ok"] for item in results), results)
        registry, error = gsm.read_source_registry()
        self.assertIsNone(error)
        self.assertEqual(len(registry), 2)

    def test_malformed_yaml_and_missing_manifest_are_reported(self):
        root = self.home / ".agents" / "skills"
        bad = root / "bad"
        bad.mkdir(parents=True)
        (bad / "SKILL.md").write_text("---\nname: bad\ndescription: [unterminated\n---\nbody", encoding="utf-8")
        (root / "missing").mkdir(parents=True)
        discovered = gsm.discover("user", False)
        statuses = {x["folder"]: x["status"] for x in discovered["skills"]}
        self.assertEqual(statuses, {"bad": "malformed", "missing": "malformed"})
        audited = gsm.audit("user", False)
        codes = {x["code"] for x in audited["issues"]}
        self.assertIn("invalid-frontmatter", codes)
        self.assertIn("missing-manifest", codes)

    def test_cache_rejects_partial_repo_and_remote_mismatch(self):
        partial = gsm.repo_cache_path("owner/partial")
        (partial / ".git").mkdir(parents=True)
        self.assertEqual(gsm.ensure_repo("owner/partial", False)["status"], "cache-invalid")

        mismatch = gsm.repo_cache_path("owner/mismatch")
        mismatch.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(mismatch)], check=True)
        subprocess.run(["git", "-C", str(mismatch), "remote", "add", "origin", "https://github.com/evil/other.git"], check=True)
        result = gsm.ensure_repo("owner/mismatch", False)
        self.assertEqual(result["status"], "remote-mismatch")

    def test_failed_clone_cleans_staging_even_if_dot_git_exists(self):
        dest = gsm.repo_cache_path("owner/failed")
        def fail_after_git_init(cmd, timeout=60, cwd=None):
            staged = Path(cmd[4] if cmd[1:3] == ["repo", "clone"] else cmd[-1])
            (staged / ".git").mkdir(parents=True)
            return gsm.CommandResult(cmd, 1, "", "failed")
        with patch.object(gsm, "run", side_effect=fail_after_git_init):
            result = gsm.ensure_repo("owner/failed", False)
        self.assertEqual(result["status"], "clone-failed")
        self.assertFalse(dest.exists())
        self.assertFalse(any(dest.parent.glob(".failed.clone-*")))

    def test_local_modifications_are_detected_against_cached_source(self):
        installed = self.home / ".agents" / "skills" / "managed"
        metadata = "metadata:\n  github-repo: https://github.com/owner/repo.git\n  github-path: skills/managed/SKILL.md\n"
        skill(installed, "managed", metadata, body="original body")
        upstream = gsm.repo_cache_path("owner/repo") / "skills" / "managed"
        skill(upstream, "managed", body="original body")
        initial = gsm.discover("user", False)["skills"][0]
        self.assertFalse(initial["locally_modified"])
        (installed / "extra.txt").write_text("custom", encoding="utf-8")
        modified = gsm.discover("user", False)
        self.assertTrue(modified["skills"][0]["locally_modified"])
        self.assertTrue(any(x.get("type") == "locally-modified" for x in modified["issues"]))

    def test_legacy_adoption_blocks_existing_user_destination(self):
        legacy = self.home / ".codex" / "skills" / "same"
        existing = self.home / ".agents" / "skills" / "same"
        skill(legacy, "same")
        skill(existing, "same", body="local customization")
        upstream = gsm.repo_cache_path("owner/repo") / "skills" / "same"
        skill(upstream, "same")
        discovery = {"skills": [{
            "name": "same", "folder": "same", "manifest": str(legacy / "SKILL.md"), "root": str(legacy.parent),
            "status": "legacy-confident", "repo": "owner/repo", "source_confidence": "manager-registry",
            "skill_path": "skills/same/SKILL.md",
        }]}
        with patch.object(gsm, "run") as mocked:
            result = gsm.adopt_legacy(discovery, "user")
        self.assertFalse(result["ok"])
        self.assertEqual(result["items"][0]["status"], "destination-conflict")
        self.assertIn("local customization", (existing / "SKILL.md").read_text(encoding="utf-8"))
        mocked.assert_not_called()

    def test_unverified_migration_preserves_legacy_copy(self):
        legacy = self.home / ".codex" / "skills" / "legacy"
        skill(legacy, "legacy")
        upstream = gsm.repo_cache_path("owner/repo") / "skills" / "legacy"
        skill(upstream, "legacy")
        discovery = {"skills": [{
            "name": "legacy", "folder": "legacy", "manifest": str(legacy / "SKILL.md"), "root": str(legacy.parent),
            "status": "legacy-confident", "repo": "owner/repo", "source_confidence": "manager-registry",
            "skill_path": "skills/legacy/SKILL.md",
        }]}
        with patch.object(gsm, "run", return_value=gsm.CommandResult(["gh"], 0, "ok", "")):
            result = gsm.adopt_legacy(discovery, "user")
        self.assertFalse(result["ok"])
        self.assertEqual(result["items"][0]["status"], "migration-unverified")
        self.assertTrue(legacy.exists())
        self.assertTrue(Path(result["items"][0]["backup"]).exists())

    def test_sync_stops_before_mutation_when_bootstrap_fails(self):
        failed_bootstrap = {"ok": False, "discovery": {"skills": []}, "repos": [{"status": "remote-mismatch"}]}
        with (
            patch.object(sys, "argv", [str(MANAGER_PATH), "sync", "--json"]),
            patch.object(gsm, "doctor", return_value={"ok": True}),
            patch.object(gsm, "bootstrap", return_value=failed_bootstrap),
            patch.object(gsm, "selected_roots", return_value=[self.home / ".agents" / "skills"]),
            patch.object(gsm, "update_roots") as update,
            patch.object(gsm, "emit") as emit,
        ):
            code = gsm.main()
        self.assertEqual(code, 6)
        update.assert_not_called()
        self.assertIn("no migration or update mutation", emit.call_args.args[0]["blocked"])

    def test_personal_installer_refuses_source_destination_alias(self):
        installer_home = self.home / "self-install"
        dest = installer_home / ".codex" / "plugins" / "codex-github-skill-manager"
        shutil.copytree(PLUGIN_ROOT, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        installer = dest / "tools" / "install_personal.py"
        result = subprocess.run([sys.executable, str(installer), "--home", str(installer_home)], text=True, capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((dest / ".codex-plugin" / "plugin.json").exists())

    def test_corrupt_marketplace_does_not_replace_existing_plugin(self):
        installer_home = self.home / "corrupt-market"
        dest = installer_home / ".codex" / "plugins" / "codex-github-skill-manager"
        dest.mkdir(parents=True)
        marker = dest / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        marketplace = installer_home / ".agents" / "plugins" / "marketplace.json"
        marketplace.parent.mkdir(parents=True)
        marketplace.write_text("{broken", encoding="utf-8")
        installer = PLUGIN_ROOT / "tools" / "install_personal.py"
        result = subprocess.run([sys.executable, str(installer), "--home", str(installer_home)], text=True, capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_personal_installer_reinstall_is_idempotent_and_preserves_catalog(self):
        installer_home = self.home / "reinstall"
        marketplace = installer_home / ".agents" / "plugins" / "marketplace.json"
        marketplace.parent.mkdir(parents=True)
        marketplace.write_text(json.dumps({"name": "personal", "plugins": [{"name": "unrelated"}]}), encoding="utf-8")
        installer = PLUGIN_ROOT / "tools" / "install_personal.py"
        first = subprocess.run([sys.executable, str(installer), "--home", str(installer_home)], text=True, capture_output=True, timeout=20)
        second = subprocess.run([sys.executable, str(installer), "--home", str(installer_home)], text=True, capture_output=True, timeout=20)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        data = json.loads(marketplace.read_text(encoding="utf-8"))
        self.assertEqual([x["name"] for x in data["plugins"]].count("codex-github-skill-manager"), 1)
        self.assertTrue(any(x["name"] == "unrelated" for x in data["plugins"]))
        self.assertTrue(any((installer_home / ".agents" / ".codex-github-skill-manager" / "plugin-backups").glob("*")))

    def test_project_symlink_root_is_not_updated(self):
        project = self.home / "symlink-project"
        target = self.home / "outside-skills"
        skill(target / "outside", "outside")
        (project / ".agents").mkdir(parents=True)
        link = project / ".agents" / "skills"
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        with patch.object(gsm, "project_root", return_value=project), patch.object(gsm, "run") as mocked:
            result = gsm.update_roots([link], apply=True, force=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["items"][0]["status"], "unsafe-root")
        mocked.assert_not_called()

    def test_windows_hook_launcher_is_packaged(self):
        hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())
        command_windows = hooks["hooks"]["SessionStart"][0]["hooks"][0]["commandWindows"]
        self.assertIn("session_start.ps1", command_windows)
        self.assertTrue((PLUGIN_ROOT / "hooks" / "session_start.ps1").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
