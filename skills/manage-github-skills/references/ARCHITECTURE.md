# Architecture

## Persistent locations

- Installed user skills: `~/.agents/skills`
- GitHub CLI user lock: `~/.agents/.skill-lock.json`
- Legacy Codex skills: `~/.codex/skills` (only if present)
- Repo clone cache: `~/.agents/.codex-github-skill-manager/repos/<owner>/<repo>`
- Backups: `~/.agents/.codex-github-skill-manager/backups/<timestamp>/<skill>`
- Discovery state: `~/.agents/.codex-github-skill-manager/state.json`
- Explicit legacy source registry: `~/.agents/.codex-github-skill-manager/sources.json`

## Source confidence

Highest to lowest:

1. GitHub CLI provenance injected into the installed `SKILL.md` (`metadata.github-repo`, `github-path`, and related fields), cross-checked with the lock when both exist.
2. GitHub CLI `.skill-lock.json` source metadata for the corresponding installed skill.
3. A scope-and-root-bound, one-time source mapping explicitly registered by the user in version 2 of `sources.json`.
4. Git remotes and labelled repository URLs are discovery candidates only. They are never trusted or registered automatically.
5. Anything else: report `source-unknown`; do not auto-clone/adopt.

The registry is keyed by canonical root plus skill folder, so same-named user, project, and legacy skills cannot silently share provenance. Registry writes are atomic and serialized. Invalid or incompatible registry content is preserved and blocks mutation.

## Why clone repositories?

The clone cache is not the installed skill directory. It is a deduplicated, non-executed source mirror used to:

- resolve legacy skill paths reliably,
- inspect upstream contents before migration,
- avoid cloning the same monorepo once per skill,
- preserve a deterministic source reference even when the original install was only a copied folder.

GitHub CLI remains the authority for installing/updating managed skill copies. Cache reuse requires a valid Git worktree whose exact `origin` matches the expected GitHub repository; failed clones remain in unique staging directories and are deleted instead of becoming trusted cache entries.

## Startup and mutation boundaries

The trusted `SessionStart` hook discovers user, project, and legacy skills, but performs bounded auto-cloning only for confirmed user/legacy sources. Project skills are discovery-only at startup because repository-controlled metadata must not trigger network work merely by opening a checkout.

Explicit sync refreshes caches first and stops before any update if refresh fails, provenance is ambiguous, or local changes differ from the cached upstream copy. Every update root is checked for symlink/junction redirection and passes `gh skill update --dry-run` before mutation. `--force` is an explicit override only after the user has reviewed local modifications.

Legacy adoption accepts only a user-confirmed registry source and one exact matching upstream `SKILL.md`. It backs up the local directory, refuses destination conflicts or links, verifies the resulting GitHub CLI-managed installation, and only then removes an obsolete legacy copy.
