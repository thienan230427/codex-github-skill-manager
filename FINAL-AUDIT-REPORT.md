# Codex GitHub Skill Manager 0.0.1 — final pre-release audit

Audit date: 2026-08-11
Target: first public GitHub release
Audit scope: complete directory snapshot, 17 original package files plus this report
Final verdict: **READY WITH MINOR NOTES**

## Executive summary

The original 0.0.1 snapshot was **not ready** to publish. Dynamic reproductions and independent security review confirmed several release-blocking defects in source provenance, cache identity, migration deletion order, redirected skill roots, sync failure handling, and installer rollback. Those defects were corrected with bounded changes and regression tests.

The final package now:

- uses current GitHub CLI 2.97.0 Agent Skills provenance (`metadata.github-*`) and compatible lock metadata;
- never promotes Git remotes, frontmatter URLs, README URLs, or search/name matches to trusted provenance without explicit user registration;
- binds manual source registration to the canonical installed root and folder;
- verifies clone-cache containment, worktree completeness, exact GitHub origin, and risky local Git configuration;
- stages clones and plugin installs, cleans failed partial state, and uses atomic serialized shared-state writes;
- detects local skill modifications and blocks implicit overwrite;
- verifies a migrated GitHub CLI-managed installation before deleting an obsolete legacy copy;
- keeps project-controlled sources discovery-only during SessionStart;
- stops all update mutation after bootstrap, migration, or dry-run failure;
- installs, lists, enables, removes, and resets correctly in an isolated current Codex CLI environment.

No remaining critical, high, medium, or low security vulnerability was accepted by the completed standard Codex Security scan. The remaining notes concern host coverage and preview-API drift, not a known correctness or security defect.

## Severity summary

| Class | Found before fixes | Remaining | Disposition |
|---|---:|---:|---|
| Critical | 0 | 0 | None |
| High / release-blocking | 6 | 0 | Fixed and regression-tested |
| Medium | 6 | 0 | Fixed and regression-tested |
| Low | 0 | 0 | None |
| Minor release notes | 3 | 3 | Documented below |

The counts above group root causes, not every symptom or test case.

## Verified findings and fixes

### 1. Untrusted metadata could become update provenance — high, fixed

**Before:** a local Git remote or labelled repository URL in an installed third-party skill could be treated as `legacy-confident`, allowing bootstrap/migration without a separate confirmation boundary.

**Risk:** attacker-controlled skill content could redirect clones or an eventual forced reinstall to an unrelated repository.

**Fix:** Git remotes and labelled URLs are now candidates only. They produce `source-unknown` (or `ambiguous`) and require explicit `register --repo ...`. Search/name similarity is never sufficient.

**Proof:** candidate, incidental-link, ambiguous-source, unknown-source, and registration regression tests pass.

### 2. Manual source registry was name-only and corruption-destructive — high, fixed

**Before:** same-named skills in user/project/legacy roots could inherit one mapping; malformed registry JSON was treated as empty and overwritten.

**Risk:** cross-scope source confusion and persistent provenance/data loss.

**Fix:** registry v2 keys each entry by canonical root plus folder, requires `--root` when a name is ambiguous, preserves invalid content, refuses incompatible writes, normalizes repository/path input, writes atomically, and serializes concurrent read-modify-write operations.

**Proof:** scope-collision, corrupt-registry, idempotency, invalid-path/repository, and concurrent-registration tests pass.

### 3. Clone-cache state could be trusted from incomplete or wrong repositories — high, fixed

**Before:** the existence of `.git` was sufficient in important paths; a failed clone could leave reusable partial state; an existing cache could point to a different origin.

**Risk:** cache poisoning, incorrect migration source, or mutation based on unrelated content.

**Fix:** cache paths must remain below the canonical cache root and contain no link/junction component. Reuse requires a complete ordinary Git worktree, exact normalized `origin`, and no risky repository-local Git configuration. New clones use unique staging directories, are verified before atomic promotion, and are always cleaned on failure. Fetch/pull disables hooks and file protocol and requires fast-forward-only pull.

**Proof:** partial cache, origin mismatch, failed clone cleanup, bootstrap deduplication, and bootstrap failure-gate tests pass.

### 4. Redirected project skill roots could reach update mutation — high, fixed

**Before:** a project `.agents/skills` symlink/junction or linked skill directory could redirect a directory-scoped update outside the intended repository.

**Risk:** arbitrary same-user filesystem overwrite through `gh skill update --dir`.

**Fix:** user, legacy, and project roots are validated before discovery/audit/update; project roots must remain inside the Git repository both lexically and after resolution; root and immediate skill links/junctions are rejected. Legacy migration also rejects links anywhere in the copied skill tree.

**Proof:** fail-closed implementation reviewed; the Windows symlink regression is present but this host skipped setup with `WinError 1314` because it lacks symlink privilege.

### 5. Legacy migration could remove the only usable copy — high, fixed

**Before:** a zero GitHub CLI exit code could lead to deletion of the legacy directory without proving that the expected managed skill was installed. Existing user destinations and local changes were not consistently protected.

**Risk:** irreversible skill loss or silent overwrite of customized files.

**Fix:** only user-registered legacy mappings are eligible. Migration requires a safe exact path or one unique upstream match, matching manifest name, no links, no destination conflict, no unreviewed local modification, and a backup. After `gh skill install`, discovery must prove the expected managed repository/path before any obsolete legacy copy is removed.

**Proof:** backup, destination-conflict, local-modification, exact-path, verified migration, and unverified-migration preservation tests pass.

### 6. Sync could continue after upstream/cache failure — high, fixed

**Before:** cache refresh failures could coexist with later migration/update work.

**Risk:** update mutation against stale, partial, or mismatched evidence.

**Fix:** clone, cache, fetch, or fast-forward failure makes bootstrap unsuccessful. Check/sync stop before migration or update. Local modifications block sync unless the user explicitly supplies `--force`. A failed update dry run blocks apply for that root.

**Proof:** failed clone, remote mismatch, bootstrap failure, local-modification, and dry-run ordering tests pass.

### 7. SessionStart allowed repository-controlled network work — medium, fixed

**Before:** opening/resuming in a project could allow project skill metadata to contribute automatic clones.

**Risk:** repository-triggered network/resource consumption and startup degradation.

**Fix:** SessionStart discovers all scopes, but bounded bootstrap is explicitly user scope plus existing legacy root only. Project sources are discovery-only until an explicit user request. Startup never updates installed files and always returns non-blocking JSON.

**Proof:** hook policy source assertion and real minimal-PATH failure test pass.

### 8. Personal installer could delete itself or damage an active install — high, fixed

**Before:** running the installer from its destination, a copy/backup failure, invalid marketplace state, or promotion failure could remove or partially replace the active plugin.

**Risk:** plugin loss and marketplace corruption.

**Fix:** source/destination aliasing is refused; marketplace state is validated before plugin mutation; the complete package is staged and checked; an existing plugin is backed up; promotion and catalog update roll back on failure; marketplace writes are atomic and serialized; unrelated entries are preserved; staging is always cleaned.

**Proof:** fresh install, self-install refusal, corrupt-catalog preservation, idempotent reinstall, backup creation, and isolated Codex lifecycle checks pass.

### 9. Concurrent JSON updates could lose entries — medium, fixed

**Before:** atomic replacement prevented torn JSON but two processes could still overwrite each other's read-modify-write result.

**Risk:** lost registry or marketplace entries.

**Fix:** cross-platform advisory file locks now serialize registry and personal marketplace transactions without stale exclusive-create lock files.

**Proof:** concurrent registry test and repeated installer/catalog preservation tests pass.

### 10. Current GitHub CLI provenance was not fully recognized — medium, fixed

**Before:** current GitHub CLI-installed skills could be classified as legacy because discovery expected older/incorrect lock placement assumptions.

**Risk:** false migration attempts and broken normal update flow.

**Fix:** discovery reads the nested `metadata.github-repo`, `github-path`, `github-ref`, `github-tree-sha`, and pin fields injected by current GitHub CLI. Compatible lock evidence is cross-checked when available; a conflict becomes ambiguous.

**Proof:** real network-backed GitHub CLI 2.97.0 install emitted the expected metadata; project metadata and lock compatibility tests pass.

### 11. Plugin listing contract exceeded current short-description limit — medium, fixed

**Before:** `interface.shortDescription` was 39 characters; current plugin submission validation limits it to 30.

**Fix:** shortened to `Manage GitHub-backed skills` and added a regression assertion. `agents/openai.yaml` now restricts routing policy to `CODEX` while retaining implicit invocation.

**Proof:** current Codex CLI ingested, listed, installed, enabled, and removed the plugin successfully in isolation.

### 12. Routing controls and documentation were incomplete/stale — medium, fixed

**Before:** required Vietnamese provenance/health prompts and negative controls for VS Code, React, GitHub CLI, and Docker were absent; documentation overstated automatic source confidence and described an unavailable host toolchain.

**Fix:** routing CSV now contains 28 balanced prompts, including all requested controls. README, skill instructions, architecture, plan, changelog, and test report now match actual 0.0.1 behavior and GitHub CLI 2.97.0.

**Proof:** CSV parse and contract checks pass; no stale 0.01/0.02/0.03/v2/v3 release identity was found.

## Phase-by-phase audit result

| Phase | Result | Evidence |
|---:|---|---|
| 1. Inventory | PASS | All 17 original files read recursively; no build cache or hidden release artifact retained |
| 2. Identity/version | PASS | Product name and semantic version consistently `Codex GitHub Skill Manager` / `0.0.1` |
| 3. Contract correctness | PASS | JSON/YAML/frontmatter, current Codex plugin ingestion, 30-character listing limit |
| 4. Routing | PASS | 28 parsed trigger/no-trigger prompts including all requested controls |
| 5. Discovery accuracy | PASS | User/project/legacy roots, malformed/missing manifests, duplicate/conflict behavior tested |
| 6. Provenance | PASS | Current nested GitHub CLI metadata; scope-bound registry; candidates never auto-trusted |
| 7. Clone/cache | PASS | Deduplication, canonical paths/origin, partial failure cleanup, explicit refresh behavior |
| 8. Legacy migration | PASS | Backup, conflict/link/local-change gates, exact source, post-install verification |
| 9. Update safety | PASS | Cache-first, dry-run-first, directory-scoped apply, pinned behavior delegated to GitHub CLI |
| 10. Auditing | PASS | Name/description/folder/frontmatter/link/duplicate checks and clear issue codes |
| 11. SessionStart | PASS | Current hook contract; project discovery-only; max 8 user/legacy clones; non-blocking failure |
| 12. Security | PASS | Completed sealed standard scan; complete coverage; 0 remaining reportable findings |
| 13. Secrets/privacy | PASS | No credentials, tokens, private keys, machine-specific user paths, or generated logs in package |
| 14. Cross-platform | PASS WITH NOTE | Windows launcher and dependency-free fallback tested; live Windows CLI checks; symlink privilege unavailable |
| 15. Tests | PASS | 36 run, 35 pass, 1 capability skip, 0 fail |
| 16. Installation | PASS | Fresh install, idempotent reinstall, Codex marketplace/plugin add/list/remove in temporary home |
| 17. Real flows | PASS WITH NOTE | Real public GitHub skill install and current metadata verified; first destructive update intentionally not run |
| 18. Failure injection | PASS | Corrupt JSON/YAML, duplicate names, cache mismatch, clone fail, bootstrap fail, migration fail, alias install |
| 19. Documentation | PASS | README, SKILL, architecture, plan, changelog, and test report reconciled with implementation |
| 20. Final diff | PASS | No deletions; only verified implementation, tests, contract metadata, documentation, and this report changed |
| 21. Clean-room | PASS | Copied/extracted package tests and contract checks pass from temporary paths with no real user skill mutation |

## Test and validation evidence

### Deterministic package suite

- `python -m unittest discover -s tests -v`: **36 run; 35 pass; 0 fail; 1 skip**
- `python -m compileall -q skills tools tests`: **PASS**
- JSON parse: **3/3 PASS**
- YAML/frontmatter/BOM validation: **PASS**
- routing CSV: **28/28 rows valid**
- source and clean-room executions produced the same result
- Ruff: **SKIP — not installed on this host**

### Live GitHub CLI

- official GitHub CLI `2.97.0` Windows binary: **PASS**
- `gh skill`, `gh skill install`, and `gh skill update` help contracts: **PASS**
- manager doctor against 2.97.0: **PASS** (private-repository auth warning expected)
- local `--from-local --dir <temp>` installation: **PASS**
- public network install from `github/awesome-copilot`: **PASS**
- nested `metadata.github-*` output: **PASS**

### Live Codex CLI in temporary state

- Codex version: `codex-cli 0.147.0-alpha.6.6`
- personal installer to temporary home: **PASS**
- marketplace add/list: **PASS**
- plugin list before install: **PASS** (`not installed`, version/source discoverable)
- plugin add: **PASS** (version 0.0.1 cached)
- installed/enabled status: **PASS**
- plugin and marketplace removal: **PASS**
- final isolated lists empty: **PASS**

### Security scan

- scan ID: `b6fcad6b-d616-4f45-bb10-e5f3ddfbfdaa`
- mode: standard whole-package scan
- completion: sealed with canonical manifest, findings, coverage, Markdown report, and SARIF
- coverage: complete, 9 security surfaces, no exclusions or deferred work
- remaining findings: **0**

## Post-audit marketplace packaging update

After the security scan, the repository received one additive packaging change: `.agents/plugins/marketplace.json`. It exposes this repository itself as the `codex-github-skill-manager` marketplace and resolves the plugin source from the repository root.

This change was validated with the current Codex CLI in an isolated temporary home:

- `codex plugin marketplace add <repository-root>`: **PASS**
- marketplace discovery of `codex-github-skill-manager`: **PASS**
- `codex plugin add codex-github-skill-manager@codex-github-skill-manager`: **PASS**
- installed/enabled state, plugin removal, and marketplace removal: **PASS**
- full regression suite after the change: **35 passed, 1 capability skip, 0 failed**

After pushing to GitHub, the documented remote installation form is:

```text
codex plugin marketplace add thienan230427/codex-github-skill-manager --ref main
codex plugin add codex-github-skill-manager@codex-github-skill-manager
```

## Files changed from the original snapshot

No original file was deleted. Fourteen original files were modified, this report was added, and the post-audit marketplace manifest was added:

- `.codex-plugin/plugin.json`
- `.agents/plugins/marketplace.json` (post-audit addition)
- `CHANGELOG.md`
- `PLAN.md`
- `README.md`
- `TEST-REPORT.md`
- `hooks/hooks.json`
- `skills/manage-github-skills/SKILL.md`
- `skills/manage-github-skills/agents/openai.yaml`
- `skills/manage-github-skills/evals/trigger-prompts.csv`
- `skills/manage-github-skills/references/ARCHITECTURE.md`
- `skills/manage-github-skills/scripts/session_start.py`
- `skills/manage-github-skills/scripts/skill_manager.py`
- `tests/test_skill_manager.py`
- `tools/install_personal.py`
- `FINAL-AUDIT-REPORT.md` (new)

## Release blockers

**None remaining.**

The host-installed GitHub CLI is 2.86.0 and does not expose `gh skill`; this is not hidden by the package. The documented minimum is 2.97.0, `doctor` fails closed on the older binary, and the official 2.97.0 portable binary passed the live audit.

## Remaining risks and minor notes

1. **GitHub CLI Agent Skills is preview functionality.** Command shape or injected metadata may change after 2.97.0. The manager fails closed when required flags disappear, but each future dependency bump should rerun this audit.
2. **One Windows symlink test was not dynamically exercised** because this process lacks `SeCreateSymbolicLinkPrivilege`. The test remains strict and skipped before executing; link/junction checks were independently reviewed. Run that test once in Developer Mode or an elevated CI worker.
3. **Human-visible desktop behavior remains a smoke test.** CLI plugin ingestion/lifecycle and hook JSON are proven, but the actual hook trust dialog and probabilistic natural-language routing picker require a user session. This does not block a first public GitHub release; document it in release notes.
4. **Official OpenAI plugin-directory submission is a separate milestone.** This audit establishes GitHub/public-test readiness, not directory artwork, review, or publication approval.

## Release recommendation

Publish **0.0.1 as a first public GitHub release** with the following release-note prerequisites:

- require GitHub CLI 2.97.0 or newer;
- label GitHub CLI Agent Skills support as preview/experimental;
- recommend a disposable or backed-up skill for the first live update/migration;
- tell users to review/trust the SessionStart hook;
- tell users to confirm and register unknown provenance explicitly rather than trusting search/name matches;
- mention the remaining Windows symlink and desktop UI smoke tests.

Do not change the version number unless code changes after this audited snapshot. If any implementation or contract file changes, rerun the deterministic suite, contract validators, isolated Codex lifecycle, and final archive extraction before publishing.

## Authoritative references checked

- OpenAI plugin packaging: https://developers.openai.com/plugins/build/plugins
- OpenAI plugin submission errors: https://developers.openai.com/plugins/deploy/submission-errors
- OpenAI hooks: https://learn.chatgpt.com/docs/hooks
- GitHub CLI 2.97.0 release: https://github.com/cli/cli/releases/tag/v2.97.0
- GitHub CLI skill manual: https://cli.github.com/manual/gh_skill
- GitHub CLI skill install: https://cli.github.com/manual/gh_skill_install
- GitHub CLI skill update: https://cli.github.com/manual/gh_skill_update
