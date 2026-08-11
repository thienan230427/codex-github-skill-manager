# Changelog

All notable changes to this project will be documented in this file.

## 0.0.1 — 2026-08-11

### Added
- Codex plugin packaging with a bundled `manage-github-skills` skill.
- SessionStart discovery hook for GitHub-backed Codex skills.
- Deduplicated source-repository clone cache.
- Legacy `$skill-installer` discovery and provenance-aware migration flow.
- Directory-scoped `gh skill update --dry-run` safety gate before mutation.
- Skill metadata audit and source registration fallback.
- Personal marketplace installer for local testing.
- Deterministic regression tests and end-to-end fake-`gh` simulation.
- Fail-closed provenance registry, clone-cache identity checks, local-modification detection, and verified legacy migration.
- Atomic, serialized registry/marketplace writes with rollback-safe personal plugin replacement.

### Changed
- Project identity renamed to **Codex GitHub Skill Manager**.
- Personal plugin installation now follows the current `~/.codex/plugins/` layout used in OpenAI documentation.
- Session startup keeps project sources discovery-only and only auto-clones confirmed user/legacy sources.
