# Codex GitHub Skill Manager 0.0.1 — test report

Date: 2026-08-11

## Result

**PASS with one host-capability skip.** All executed package, contract, integration, failure-path, live GitHub CLI, and isolated Codex plugin lifecycle checks passed. No assertion was weakened to obtain a pass.

## Deterministic suite

Command:

```powershell
python -m unittest discover -s tests -v
```

Result: **36 tests run; 35 passed; 0 failed; 1 skipped**.

The skipped test creates a Windows directory symlink to prove a redirected project skill root is rejected. This host returned `WinError 1314` because the process lacks symlink privilege. The test skipped at setup; the fail-closed root/link implementation and related non-symlink failure paths were still reviewed and exercised.

Coverage includes:

- GitHub CLI nested provenance metadata and compatible lock metadata;
- source candidates that remain untrusted until exact user registration;
- scope/root-bound registry v2, corrupt-state preservation, idempotency, and concurrent writes;
- duplicate repository clone deduplication, partial/mismatched cache rejection, and failed-clone cleanup;
- bootstrap, local-modification, and dry-run mutation gates;
- destination-conflict, backup, exact-path, and post-install-verification migration rules;
- malformed/missing manifests and metadata audit behavior;
- non-blocking SessionStart output and project discovery-only startup policy;
- personal installer alias refusal, corrupt-catalog preservation, staged replacement, rollback behavior, and idempotent catalog preservation;
- Windows launcher packaging and end-to-end fake-`gh` command ordering.

## Contract and static checks

- Python compile (`skills`, `tools`, and `tests`): **PASS**
- JSON parse (3 files): **PASS**
- YAML parse (`agents/openai.yaml`): **PASS**
- SKILL frontmatter and BOM check: **PASS**
- Routing CSV parse (28 prompts): **PASS**
- Plugin ingestion through the current Codex CLI: **PASS**
- Standard whole-package Codex Security scan: **PASS**, complete coverage, 0 remaining reportable findings
- Ruff: **SKIPPED**, executable/module not installed on this host

## Live GitHub CLI 2.97.0 checks

The official Windows archive for GitHub CLI 2.97.0 was downloaded to a temporary directory and run without replacing the installed host CLI.

- `gh --version`: **PASS** (`2.97.0`, released 2026-07-31)
- `gh skill --help`: **PASS**
- `gh skill install --help`: **PASS**, required install/agent/scope/force/path flags present
- `gh skill update --help`: **PASS**, required `--dir`, `--dry-run`, `--all`, and `--force` flags present
- manager `doctor()` against that exact executable: **PASS**; unauthenticated private-repository access was correctly reported as a warning
- local `gh skill install --from-local --dir <temp>`: **PASS**
- network-backed public install from `github/awesome-copilot`: **PASS**; emitted the expected nested `metadata.github-*` fields

The restricted audit process could not write the GitHub CLI global user lock while using the custom `--dir`; discovery does not depend on that write because current GitHub CLI provenance is also injected into the installed manifest.

## Isolated Codex plugin lifecycle

Using `codex-cli 0.147.0-alpha.6.6` with a fresh temporary `CODEX_HOME`:

1. personal installer created the plugin and marketplace layout: **PASS**
2. `codex plugin marketplace add <temp-home>`: **PASS**
3. `codex plugin list` discovered version 0.0.1: **PASS**
4. `codex plugin add codex-github-skill-manager@personal`: **PASS**
5. enabled/installed status and cached manifest: **PASS**
6. plugin removal and marketplace removal: **PASS**
7. final isolated marketplace/plugin lists were empty: **PASS**

The interactive desktop hook-trust prompt and UI routing picker require a human-visible Codex session and were not automated. Their package contracts, command wiring, trigger controls, and failure-safe hook output were validated.

## Release smoke checklist

- Use GitHub CLI **2.97.0 or newer**; older installed versions do not provide `gh skill`.
- Run the personal installer from a separate extracted release checkout, never from the active destination.
- Review and trust the SessionStart hook before expecting startup discovery.
- Use a disposable or backed-up skill for the first real update/migration.
- Register unknown legacy provenance explicitly; never confirm a repository based only on a search/name match.
