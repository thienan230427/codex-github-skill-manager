$ErrorActionPreference = "SilentlyContinue"

$script = Join-Path $env:PLUGIN_ROOT "skills\manage-github-skills\scripts\session_start.py"

function Invoke-PythonCandidate {
    param(
        [string]$Command,
        [string[]]$PrefixArgs
    )
    $resolved = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $resolved) { return $false }

    & $Command @PrefixArgs $script
    if ($LASTEXITCODE -eq 0) { return $true }
    return $false
}

if (Invoke-PythonCandidate "py" @("-3")) { exit 0 }
if (Invoke-PythonCandidate "python3" @()) { exit 0 }
if (Invoke-PythonCandidate "python" @()) { exit 0 }

# Never block Codex startup if Python is unavailable. Surface a deterministic
# context message so the bundled skill can explain the missing prerequisite.
Write-Output '{"continue":true,"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Codex GitHub Skill Manager startup discovery was skipped because Python 3 was not found in PATH. The plugin remains available; install/expose Python 3, then start or resume a Codex session."}}'
exit 0
