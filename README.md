# Codex GitHub Skill Manager

> **Have you ever installed a great Codex skill from GitHub, then watched it become outdated because Codex could not safely keep it in sync?**

This plugin fixes that gap. It discovers GitHub-backed Codex skills, records their verified source, checks for updates, and updates only when you ask. Your local edits and unknown skill origins stay protected.

## Why use it?

- Find user, project, and legacy Codex skills in one place.
- Keep one local cache for each GitHub repository.
- Check, migrate, update, and audit skills safely.
- Back up legacy skills before a migration.
- Refuse to guess where an unknown skill came from.

## Install

### Requirements

- Codex with plugin and hook support
- Python 3.10+
- Git
- GitHub CLI 2.97.0+ with the preview `gh skill` commands

Install from GitHub:

```bash
codex plugin marketplace add thienan230427/codex-github-skill-manager --ref main
codex plugin add codex-github-skill-manager@codex-github-skill-manager
```

Restart Codex, enable the plugin, review its `SessionStart` hook, and start a new task.

To upgrade the marketplace installation:

```bash
codex plugin marketplace upgrade codex-github-skill-manager
codex plugin add codex-github-skill-manager@codex-github-skill-manager
```

For local development:

```powershell
py -3 tools\install_personal.py
```

On macOS or Linux, use `python3 tools/install_personal.py`.

## Use it in Codex

Ask naturally:

```text
Discover my GitHub-backed Codex skills. Do not update anything.
```

```text
Check my GitHub-backed Codex skills for updates without applying them.
```

```text
Update all my GitHub-backed Codex skills safely and audit them afterward.
```

## Command line

Run these commands from the plugin root. On Windows, replace `python` with `py -3` if needed.

```bash
python skills/manage-github-skills/scripts/skill_manager.py doctor --json
python skills/manage-github-skills/scripts/skill_manager.py discover --scope user --include-legacy --json
python skills/manage-github-skills/scripts/skill_manager.py check --scope user --include-legacy --json
python skills/manage-github-skills/scripts/skill_manager.py sync --scope user --include-legacy --json
python skills/manage-github-skills/scripts/skill_manager.py audit --scope user --include-legacy --json
```

Install new skills through GitHub CLI so their provenance is recorded:

```bash
gh skill install OWNER/REPO SKILL_OR_PATH --agent codex --scope user
```

If an old copied skill is reported as `source-unknown`, confirm its real GitHub repository first, then register it:

```bash
python skills/manage-github-skills/scripts/skill_manager.py register --skill SKILL_NAME --repo OWNER/REPO --json
```

## Safety by default

- Every update runs a dry run before it can change a skill.
- Local changes are not overwritten unless you explicitly use `--force`.
- Legacy migrations create a backup and verify the replacement first.
- A URL, README link, frontmatter field, or search result is never treated as proof of source ownership.
- The startup hook discovers skills but never silently updates them.

## Troubleshooting

If GitHub authentication fails:

```bash
gh auth login
gh auth status
```

If `gh skill` is unavailable, update GitHub CLI and run `gh skill --help`. For a skill with an unknown source, register its exact repository rather than guessing.

## Development

```powershell
python -m unittest discover -s tests -v
python -m compileall -q skills tools tests
```

The 0.0.1 release was audited with 35 passing tests and one Windows symlink test skipped when the host lacked symlink permission. See [TEST-REPORT.md](TEST-REPORT.md), [FINAL-AUDIT-REPORT.md](FINAL-AUDIT-REPORT.md), and [ARCHITECTURE.md](skills/manage-github-skills/references/ARCHITECTURE.md) for details.

## Uninstall

```bash
codex plugin remove codex-github-skill-manager@codex-github-skill-manager
```

Cached repositories and backups remain on disk so uninstalling never deletes recovery data silently.

## License

[MIT](LICENSE) © 2026 Nguyen Thien An
