#!/usr/bin/env python3
"""Codex SessionStart hook: local discovery + bounded user-source bootstrap.
Never blocks the session, clones project-controlled sources, or updates skills.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    here = Path(__file__).resolve().parent
    manager = here / "skill_manager.py"
    try:
        discover_cmd = [sys.executable, str(manager), "discover", "--scope", "all", "--include-legacy", "--json"]
        bootstrap_cmd = [sys.executable, str(manager), "bootstrap", "--scope", "user", "--include-legacy", "--max-clones", "8", "--json"]
        discovered = subprocess.run(discover_cmd, text=True, capture_output=True, timeout=20)
        bootstrapped = subprocess.run(bootstrap_cmd, text=True, capture_output=True, timeout=35)
        if discovered.returncode != 0 or bootstrapped.returncode != 0:
            raise RuntimeError("manager discovery/bootstrap returned a nonzero exit code")
        discovery = json.loads(discovered.stdout)
        data = json.loads(bootstrapped.stdout)
        boot = data.get("bootstrap", {})
        repos = boot.get("repos", [])
        skills = discovery.get("skills", [])
        managed = sum(1 for s in skills if s.get("status") == "managed")
        legacy = sum(1 for s in skills if s.get("status") == "legacy-confident")
        review = sum(1 for s in skills if s.get("status") in {"ambiguous", "source-unknown", "malformed"})
        cloned = sum(1 for r in repos if r.get("status") == "cloned")
        deferred = sum(1 for r in repos if r.get("status") == "deferred-limit")
        failed = sum(1 for r in repos if r.get("status") in {"clone-failed", "path-conflict", "fetch-failed", "fetch-partial", "cache-invalid", "remote-mismatch"})
        context = (
            f"Codex GitHub Skill Manager startup discovery: {len(skills)} Codex skill(s); "
            f"{managed} provenance-managed, {legacy} legacy with confident GitHub source, "
            f"{review} needing source/discovery review. Confirmed user/legacy source cache cloned {cloned} missing repo(s)"
            + (f", deferred {deferred} to a later bootstrap" if deferred else "")
            + (f", {failed} clone/cache failure(s)" if failed else "")
            + ". Project sources are discovery-only at startup and are cloned only after an explicit request. When the user asks to update/sync/migrate/audit GitHub Codex skills, use the manage-github-skills plugin skill."
        )
        print(json.dumps({
            "continue": True,
            "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}
        }, ensure_ascii=False))
    except Exception as exc:
        # A convenience hook must never prevent Codex from starting.
        print(json.dumps({
            "continue": True,
            "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": f"Codex GitHub Skill Manager startup discovery could not complete: {type(exc).__name__}. The session may continue; run the manage-github-skills doctor/bootstrap command if needed."}
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
