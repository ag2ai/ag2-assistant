# AG2 Assistant installer (Windows) — installs the ag2-assistant CLI as an isolated uv tool.
#
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/ag2ai/ag2-assistant/main/scripts/install.ps1 | iex"
#
# Installs uv first if it isn't present (uv also downloads a compatible Python,
# so no system Python 3.12 is required). Re-running the script upgrades an
# existing install to the latest commit on the ref.
#
# Environment overrides:
#   AG2_ASSISTANT_REF     git branch or tag to install (default: main)
#   AG2_ASSISTANT_EXTRAS  optional extras, e.g. "google" (default: none)
#   AG2_ASSISTANT_REPO    git URL to install from, e.g. a fork (default: the ag2ai repo)
$ErrorActionPreference = "Stop"

$Ref = if ($env:AG2_ASSISTANT_REF) { $env:AG2_ASSISTANT_REF } else { "main" }
$Extras = if ($env:AG2_ASSISTANT_EXTRAS) { "[$($env:AG2_ASSISTANT_EXTRAS)]" } else { "" }
$Repo = if ($env:AG2_ASSISTANT_REPO) { $env:AG2_ASSISTANT_REPO } else { "https://github.com/ag2ai/ag2-assistant.git" }
$Spec = "ag2-assistant$Extras @ git+$Repo@$Ref"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "error: git is required (the AG2 dependency installs from git)." -ForegroundColor Red
    Write-Host "       Install it with 'winget install Git.Git' and re-run this script." -ForegroundColor Red
    exit 1
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv not found - installing it first (https://astral.sh/uv) ..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    # The uv installer targets %USERPROFILE%\.local\bin; pick it up for this run.
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host "Installing ag2-assistant ($Ref) ..."
uv tool install --force --python ">=3.12" $Spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
if (Get-Command ag2-assistant -ErrorAction SilentlyContinue) {
    Write-Host "Installed: ag2-assistant"
} else {
    # uv placed the executable outside PATH; it prints the same warning itself.
    Write-Host "Installed, but the uv tool bin directory is not on your PATH."
    Write-Host "Run 'uv tool update-shell' (then open a new terminal) to fix that."
}
Write-Host ""
Write-Host "Start it with:"
Write-Host "  ag2-assistant run        # gateway + web UI at http://localhost:8800/"
