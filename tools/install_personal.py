#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

PLUGIN_NAME = "codex-github-skill-manager"


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
    """Serialize marketplace/plugin replacement across installer processes."""
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
                    raise TimeoutError(f"timed out waiting for installer lock {path}")
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


def load_marketplace(path: Path) -> dict:
    if not path.exists():
        return {"name": "personal", "interface": {"displayName": "Personal"}, "plugins": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Existing marketplace.json is invalid; no plugin files were changed: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise SystemExit("Existing marketplace.json is not a JSON object; no plugin files were changed")
    data.setdefault("name", "personal")
    data.setdefault("interface", {"displayName": "Personal"})
    data.setdefault("plugins", [])
    if not isinstance(data["plugins"], list):
        raise SystemExit("Existing marketplace.json 'plugins' field is not an array; no plugin files were changed")
    return data


def install_locked(home: Path, plugin_root: Path, dest: Path, marketplace: Path) -> int:
    if dest.exists() and plugin_root.samefile(dest):
        raise SystemExit("Refusing to reinstall from the active installed directory because source and destination are identical. Run the installer from a separate release checkout.")

    # Validate shared user state and stage a complete package before touching
    # the active plugin directory.
    data = load_marketplace(marketplace)
    dest.parent.mkdir(parents=True, exist_ok=True)
    stage_parent = Path(tempfile.mkdtemp(prefix=f".{PLUGIN_NAME}.stage-", dir=dest.parent))
    staged = stage_parent / PLUGIN_NAME
    old = dest.with_name(f".{PLUGIN_NAME}.old-{os.getpid()}")
    backup: Path | None = None
    try:
        shutil.copytree(plugin_root, staged, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache", ".venv", "venv"))
        json.loads((staged / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        if not any((staged / "skills").glob("*/SKILL.md")):
            raise ValueError("Staged plugin has no discoverable bundled skill")
    except Exception:
        shutil.rmtree(stage_parent, ignore_errors=True)
        raise

    entry = {
        "name": PLUGIN_NAME,
        "source": {"source": "local", "path": f"./.codex/plugins/{PLUGIN_NAME}"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Developer Tools",
    }
    data["plugins"] = [x for x in data["plugins"] if not (isinstance(x, dict) and x.get("name") == PLUGIN_NAME)]
    data["plugins"].append(entry)

    had_dest = dest.exists()
    if had_dest:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = home / ".agents" / ".codex-github-skill-manager" / "plugin-backups" / f"{PLUGIN_NAME}-{stamp}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(dest, backup)
    try:
        if had_dest:
            os.replace(dest, old)
        os.replace(staged, dest)
        atomic_write_text(marketplace, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    except Exception:
        if old.exists():
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            os.replace(old, dest)
        elif not had_dest and dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(stage_parent, ignore_errors=True)
    if old.exists():
        shutil.rmtree(old)
    if backup:
        print(f"Backed up existing plugin to: {backup}")

    print(f"Installed plugin files: {dest}")
    print(f"Updated marketplace: {marketplace}")
    print(f"Marketplace name: {data['name']}")
    print("Next: restart the ChatGPT/Codex desktop app, open Plugins, choose the Personal marketplace, and install/enable Codex GitHub Skill Manager.")
    print("Then open /hooks once and review/trust the SessionStart hook.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Install Codex GitHub Skill Manager into the personal Codex plugin marketplace layout")
    p.add_argument("--home", help="Override home directory (mainly for testing)")
    args = p.parse_args()

    home = Path(args.home).expanduser().resolve() if args.home else Path.home()
    plugin_root = Path(__file__).resolve().parents[1]
    dest = home / ".codex" / "plugins" / PLUGIN_NAME
    marketplace = home / ".agents" / "plugins" / "marketplace.json"
    try:
        with exclusive_file_lock(marketplace.with_suffix(".install.lock")):
            return install_locked(home, plugin_root, dest, marketplace)
    except TimeoutError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
