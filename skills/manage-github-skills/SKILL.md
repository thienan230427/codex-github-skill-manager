---
name: manage-github-skills
description: >-
  Manage GitHub-hosted Codex Agent Skills: discover installed skills, identify their source repos, clone missing source repos, migrate legacy $skill-installer copies to provenance-aware GitHub CLI installs, check/update all GitHub skills, audit SKILL.md discovery, or diagnose a skill Codex does not recognize. Use for requests like “update my Codex skills”, “sync GitHub skills”, “clone skill sources”, “migrate old skills”, or “why is this skill missing?”. Do not use for ordinary git repositories, npm/pip dependencies, Codex/plugin upgrades, MCP servers, or non-skill GitHub work.
compatibility: Codex CLI or Codex desktop app with Python 3, Git, and GitHub CLI 2.97.0 or newer. Network access is required for clone/update operations.
metadata:
  author: local
  version: "0.0.1"
---

# Manage GitHub Skills

Manage external GitHub-backed Codex Agent Skills with deterministic local discovery and GitHub CLI provenance.

## Startup behavior

This plugin bundles a trusted-once `SessionStart` hook. After the user reviews and trusts the hook in Codex, each new Codex session performs a bounded bootstrap:

1. Discover `$HOME/.agents/skills`, current-project `.agents/skills` roots, and an existing `$HOME/.codex/skills` legacy root.
2. Read GitHub CLI metadata injected into installed `SKILL.md` files and compatible `.skill-lock.json` entries.
3. Trust a legacy source only when the user previously registered that exact root/folder mapping. Git remotes and labelled repository URLs are candidates for review, never automatic provenance.
4. Clone each missing confirmed user/legacy `OWNER/REPO` only once into `$HOME/.agents/.codex-github-skill-manager/repos/OWNER/REPO`. Project sources are discovery-only at startup.
5. Return a short discovery summary to Codex so later update requests route to this skill.

The startup hook **does not update installed skills**, overwrite local files, auto-clone project-controlled sources, or execute scripts from cloned repositories.

## Unknown legacy source fallback

If discovery reports `source-unknown`, the previous installer did not preserve enough provenance to identify the GitHub repository safely. Do not search/guess solely from the skill name. Ask for the repository URL once, then register it:

```powershell
python scripts/skill_manager.py register --skill SKILL_NAME --repo OWNER/REPO --json
```

An HTTPS GitHub repository URL is also accepted. If the same name exists in more than one scope, add `--root PATH_TO_EXACT_SKILL_ROOT`. Add `--skill-path path/to/SKILL.md` when known so later migration is exact. After registration, run `bootstrap` or `sync`; future startup/resume discovery will reuse the scope-bound mapping automatically.

## Default update workflow

When the user asks to update/sync GitHub skills, use the bundled manager from this skill directory:

```powershell
python scripts/skill_manager.py sync --scope user --include-legacy --adopt-legacy --json
```

If `python` maps to Python 2 or is unavailable on Windows, use `py -3`.

The command must perform, in order:

1. **Doctor**: validate Python runtime assumptions, `git`, `gh`, `gh skill`, and GitHub auth status.
2. **Discover**: inventory installed Codex skills and classify each as managed, legacy-confident, source-unknown, ambiguous, or malformed.
3. **Clone/fetch source cache**: one clone per confirmed GitHub repository; verify the cache worktree and exact `origin`, then update it with hooks/protocol hardening, `git fetch`, and fast-forward-only pull. Stop before mutation on any refresh failure.
4. **Adopt registered legacy skills**: require the user-confirmed scope-bound mapping and one exact matching upstream manifest. Reject links, local modifications, and destination conflicts; back up first, reinstall, and verify the managed result before removing an obsolete legacy copy.
5. **Dry run**: `gh skill update --dir <managed-root> --dry-run` before any update mutation. The legacy `~/.codex/skills` root is discovery/migration-only and is never an update target.
6. **Update**: `gh skill update --dir <managed-root> --all`; never use an unscoped global updater for a Codex-only request.
7. **Audit**: verify discovery-critical `SKILL.md` metadata and duplicate names.
8. **Report**: show updated/current/pinned/unmanaged/ambiguous/failures and backup paths.

Do not use `--force` for ordinary updates unless the user explicitly asks to restore upstream contents after reviewing detected local modifications. Legacy adoption may use `gh skill install --force` only after link/conflict checks and creating a backup, because replacing the old unmanaged copy is required to inject provenance.

## Read-only commands

Inventory + source classification without network mutation:

```powershell
python scripts/skill_manager.py discover --scope user --include-legacy --json
```

Check for upstream updates without applying them:

```powershell
python scripts/skill_manager.py check --scope user --include-legacy --json
```

Audit discovery only:

```powershell
python scripts/skill_manager.py audit --scope user --include-legacy --json
```

Clone missing source repositories without modifying installed skills:

```powershell
python scripts/skill_manager.py bootstrap --scope user --include-legacy --json
```

## Installing a new GitHub skill

Prefer GitHub CLI so source tracking is created immediately:

```powershell
gh skill install OWNER/REPO SKILL_OR_PATH --agent codex --scope user
```

Then clone/cache the source once:

```powershell
python scripts/skill_manager.py bootstrap --scope user --json
```

Use an exact repository path when known. Do not pin unless the user asks for a fixed version.

## Legacy adoption rules

A legacy skill is eligible for adoption during an explicit update request only if all are true:

- The user explicitly registered the exact installed root/folder and GitHub `OWNER/REPO`.
- The repository clone succeeds.
- The registered `SKILL.md` path is safe and matches the installed skill name, or exactly one safe upstream manifest matches when no path was registered.
- The local tree contains no symlink/junction, has no unreviewed modifications, and the destination does not already exist.
- The local skill is backed up before replacement.
- The resulting GitHub CLI-managed installation is verified before any legacy copy is removed.

If any condition is uncertain, do not overwrite it. Report the skill as **needs source review**.

## Safety boundaries

- Never execute third-party scripts merely because a repo was cloned or a skill was updated.
- Never delete the source repo cache as part of update.
- Never rewrite pinned skills unless the user explicitly asks to unpin.
- Never infer a GitHub repo solely from the skill name.
- Never promote a Git remote or labelled repository URL from an installed third-party skill without user confirmation.
- Do not modify Codex system/admin skills.
- Reject redirected roots/skills and mismatched or partial clone caches.
- A failed dry run blocks update mutation for that root.

## Output contract

Finish with concise groups:

- **Managed / recognized**
- **Cloned or fetched repos**
- **Migrated legacy skills**
- **Updated / already current**
- **Pinned / intentionally skipped**
- **Needs source review**
- **Discovery warnings**
- **Failures / backups**
