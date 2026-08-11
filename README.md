# Codex GitHub Skill Manager

> Experimental v0.0.1 plugin for safely managing GitHub-backed Codex Agent Skills.

Codex GitHub Skill Manager discovers installed skills, tracks their GitHub sources, keeps one source clone per repository, checks for updates, migrates legacy copies, and audits broken skill metadata.

It is conservative by design: unknown repositories are reported for review, local changes are protected, and every update runs a dry run first.

## Features

- Discover user, project, and legacy Codex skills.
- Recognize provenance created by GitHub CLI.
- Cache one clone per `OWNER/REPO`.
- Check or update only the requested skill directories.
- Back up and verify legacy skills before migration.
- Detect malformed manifests, duplicate names, unsafe links, cache conflicts, and local modifications.
- Run bounded startup discovery without modifying installed skills.

## Safety rules

The manager never:

- guesses a repository from a skill name;
- trusts Git remotes, frontmatter URLs, README links, or search matches without user confirmation;
- auto-clones project-controlled sources during startup;
- executes scripts from cached third-party repositories;
- updates a root after a failed cache refresh or dry run;
- overwrites local changes with `--force` unless the user explicitly requests it;
- deletes a legacy skill before the replacement is verified.

## Requirements

- Codex with plugin and hook support
- Python 3.10+
- Git
- GitHub CLI 2.97.0+ with the preview `gh skill` commands
- GitHub access; authentication is required for private repositories

Verify the tools:

```powershell
py -3 --version
git --version
gh --version
gh skill --help
gh auth status
```

On macOS or Linux, use `python3` instead of `py -3`.

## Install from GitHub

After pushing this repository to GitHub on the `main` branch, run:

```bash
codex plugin marketplace add thienan230427/codex-github-skill-manager --ref main
codex plugin add codex-github-skill-manager@codex-github-skill-manager
```

Start a new Codex task, then review and trust the `SessionStart` hook before expecting startup discovery.

To update an existing Git marketplace installation:

```bash
codex plugin marketplace upgrade codex-github-skill-manager
codex plugin add codex-github-skill-manager@codex-github-skill-manager
```

The repository includes its marketplace manifest at `.agents/plugins/marketplace.json`. Codex resolves the plugin source from the repository root, so no personal installer is needed for a GitHub marketplace installation.

## Local development install

### 1. Install the plugin

Download or clone this repository, open a terminal in its root, then run:

```powershell
py -3 tools\install_personal.py
```

macOS/Linux:

```bash
python3 tools/install_personal.py
```

Run the installer from this checkout or an extracted release, not from the active installed directory.

The installer safely updates:

```text
~/.codex/plugins/codex-github-skill-manager
~/.agents/plugins/marketplace.json
```

Existing plugin files are backed up before replacement, and unrelated marketplace entries are preserved.

### 2. Enable it in Codex

1. Restart Codex or the ChatGPT desktop app.
2. Open **Plugins** and select the **Personal** marketplace.
3. Install or enable **Codex GitHub Skill Manager**.
4. Review and trust its `SessionStart` hook.
5. Start a new Codex task.

The hook discovers all supported scopes, but only bootstraps confirmed user/legacy sources. It clones at most eight missing repositories per session and never updates installed skills automatically.

### 3. Try it

Ask Codex:

```text
Discover my GitHub-backed Codex skills. Do not update anything.
```

```text
Check my GitHub-backed Codex skills for updates without applying them.
```

```text
Update all my GitHub-backed Codex skills safely and audit them afterward.
```

## Common commands

Run commands from the repository or installed plugin root.

Windows:

```powershell
$manager = "skills\manage-github-skills\scripts\skill_manager.py"
```

macOS/Linux:

```bash
manager="skills/manage-github-skills/scripts/skill_manager.py"
```

| Task | Windows command |
|---|---|
| Check prerequisites | `py -3 $manager doctor --json` |
| Discover skills | `py -3 $manager discover --scope user --include-legacy --json` |
| Clone missing confirmed sources | `py -3 $manager bootstrap --scope user --include-legacy --json` |
| Check for updates | `py -3 $manager check --scope user --include-legacy --json` |
| Safely update skills | `py -3 $manager sync --scope user --include-legacy --json` |
| Update and migrate eligible legacy skills | `py -3 $manager sync --scope user --include-legacy --adopt-legacy --json` |
| Audit skill metadata | `py -3 $manager audit --scope user --include-legacy --json` |

On macOS/Linux, replace `py -3 $manager` with `python3 "$manager"`.

Available scopes are `user`, `project`, and `all`. The legacy root is discovery/migration-only and is never passed to normal `gh skill update`.

## Install a new GitHub skill

Use GitHub CLI so provenance is recorded immediately:

```bash
gh skill install OWNER/REPO SKILL_OR_PATH --agent codex --scope user
```

Example:

```bash
gh skill install owner/example-skills skills/code-review --agent codex --scope user
```

Then run discovery or bootstrap again.

## Register an unknown legacy source

If a copied legacy skill is reported as `source-unknown`, confirm its real repository yourself, then register it once:

```powershell
py -3 $manager register --skill SKILL_NAME --repo OWNER/REPO --json
```

If the same skill name exists in multiple roots, identify the exact root:

```powershell
py -3 $manager register --skill SKILL_NAME --repo OWNER/REPO --root "PATH_TO_SKILL_ROOT" --json
```

When known, also record the exact upstream manifest:

```powershell
py -3 $manager register --skill SKILL_NAME --repo OWNER/REPO --skill-path "path/to/SKILL.md" --root "PATH_TO_SKILL_ROOT" --json
```

An HTTPS GitHub repository URL is also accepted.

Do not register a repository based only on a name, search result, copied URL, or content match. Those are candidates, not proof of origin.

## Legacy migration

A legacy skill is migrated only when:

1. its exact root and repository were registered by the user;
2. the cached repository and origin are valid;
3. a safe, matching upstream `SKILL.md` is found;
4. no unsafe link, destination conflict, or unreviewed local change exists;
5. a backup is created;
6. the new GitHub CLI-managed copy is verified.

If any check fails, migration stops and the legacy copy remains intact.

## Data locations

| Data | Default location |
|---|---|
| User skills | `~/.agents/skills` |
| Project skills | `<repository>/.agents/skills` |
| Legacy skills | `~/.codex/skills` |
| Plugin files | `~/.codex/plugins/codex-github-skill-manager` |
| Repository cache | `~/.agents/.codex-github-skill-manager/repos` |
| Source registry | `~/.agents/.codex-github-skill-manager/sources.json` |
| Backups and state | `~/.agents/.codex-github-skill-manager` |

## Troubleshooting

### `gh skill` is unavailable

Install GitHub CLI 2.97.0 or newer, then verify:

```powershell
gh skill update --help
```

### GitHub authentication fails

```powershell
gh auth login
gh auth status
```

Public repositories may still work without authentication; private repositories will not.

### A skill is `source-unknown`

Confirm and register the exact repository using the command above. The manager intentionally refuses to guess.

### Sync reports local modifications

Review and back up the changes first. Use `--force` only when you deliberately want upstream files to replace local edits.

### Cache refresh or dry run fails

No update is applied. Resolve the reported network, repository, cache, authentication, or metadata error, then rerun the command.

### The startup hook does not run

- confirm the plugin is enabled;
- review and trust the hook;
- ensure Python 3 is available in `PATH`;
- restart Codex and open a new task.

## Development and verification

Run the regression suite:

```powershell
python -m unittest discover -s tests -v
```

Compile the Python sources:

```powershell
python -m compileall -q skills tools tests
```

The audited 0.0.1 package ran 36 tests: 35 passed, 1 Windows symlink test was skipped because the host lacked symlink privilege, and 0 failed.

See [TEST-REPORT.md](TEST-REPORT.md) and [FINAL-AUDIT-REPORT.md](FINAL-AUDIT-REPORT.md) for detailed evidence.

## Uninstall

Remove or disable the plugin from Codex. If installed through the Personal marketplace, Codex CLI can remove it with:

```powershell
codex plugin remove codex-github-skill-manager@personal
```

Cached repositories, registry entries, and backups remain under `~/.agents/.codex-github-skill-manager` so uninstalling the plugin does not silently delete recovery data or installed skills.

## Known limitations

- GitHub CLI Agent Skills is preview functionality and may change.
- Missing provenance cannot be reconstructed safely without user confirmation.
- Implicit Codex skill routing is probabilistic; explicit invocation is best for debugging.
- The SessionStart hook limits bootstrap work to eight confirmed user/legacy repositories per session.

## Security guidance

Treat third-party Agent Skills as code dependencies. Review repositories and updates, keep GitHub authentication narrowly scoped, preserve local backups, and never publish private URLs, credentials, or sanitized manager output containing sensitive paths.

## License

Distributed under the [MIT License](LICENSE).

## References

- [OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins)
- [OpenAI hooks](https://learn.chatgpt.com/docs/hooks)
- [GitHub CLI `gh skill`](https://cli.github.com/manual/gh_skill)
- [GitHub CLI `gh skill install`](https://cli.github.com/manual/gh_skill_install)
- [GitHub CLI `gh skill update`](https://cli.github.com/manual/gh_skill_update)

## Release status

Version 0.0.1 is ready for a first public GitHub test release with minor notes. Test the first live update or migration with disposable or backed-up skills.
