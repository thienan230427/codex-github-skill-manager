# Codex GitHub Skill Manager 0.0.1 — implementation plan

Status: **implemented and regression-tested**.

## Phase 1 — Package as a Codex plugin ✅
- Add `.codex-plugin/plugin.json` using the current plugin-creator ingestion shape.
- Bundle a focused `manage-github-skills` skill.
- Enable implicit invocation through `agents/openai.yaml`.

## Phase 2 — Automatic discovery ✅
- Add a trusted-once `SessionStart` hook for `startup` and `resume`.
- Scan user, current-project, and legacy Codex skill roots.
- Read GitHub CLI provenance injected into installed `SKILL.md` files and cross-check compatible lock metadata.
- Treat Git remotes and labelled repository URLs only as untrusted candidates requiring user confirmation.
- Mark provenance-less old copies as `source-unknown`; never guess a repository.
- Support one-time explicit source registration for those legacy copies.

## Phase 3 — Deduplicated repository cache ✅
- Cache one clone per `OWNER/REPO` under `~/.agents/.codex-github-skill-manager/repos`.
- Auto-clone missing confirmed user/legacy sources at session start after the hook is trusted; keep project sources discovery-only at startup.
- Bound startup work to eight new clones per session.
- Do not fetch every existing clone at startup; fetch on explicit check/update.
- Never execute scripts from cached third-party repositories.

## Phase 4 — Legacy migration ✅
- Resolve each legacy skill against its cached upstream repository.
- Require a safe registered path or exactly one matching upstream `SKILL.md` before adoption.
- Reject links and destination conflicts, then back up the local skill.
- Reinstall with `gh skill install ... --force` so GitHub CLI injects source tracking metadata.
- Verify the managed installation before removing an obsolete legacy copy.
- Keep `~/.codex/skills` discovery/migration-only; never target it with `gh skill update`.

## Phase 5 — Update + audit ✅
- Refresh source clones on explicit check/update.
- Verify cache worktree identity and exact GitHub origin; abort sync when refresh fails.
- Run directory-scoped `gh skill update --dry-run` before mutation.
- Apply `gh skill update --dir <managed-root> --all` only after the dry run succeeds.
- Keep pinned skills pinned by default.
- Audit names, descriptions, folder/name consistency, duplicate names, and implicit invocation metadata.

## Phase 6 — Windows + failure handling ✅
- Add a PowerShell startup launcher that tries `py -3`, `python3`, then `python`.
- Never block Codex startup when Python/Git/GitHub/network checks fail.
- Surface concise discovery context so later natural-language update requests route to the plugin skill.

## Phase 7 — Regression + package verification ✅
- Validate plugin manifest/frontmatter against the current OpenAI plugin-creator contract.
- Compile all Python entry points.
- Run unit/integration simulations for discovery, deduplication, migration, dry-run gating, scoped provenance, personal marketplace installation, and end-to-end fake-`gh` sync ordering.
- Re-extract the final archive and rerun package tests before release.
