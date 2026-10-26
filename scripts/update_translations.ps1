# Refresh translations/*.ts from src/ and compile them to .qm. Thin wrapper
# around scripts/update_translations.py; all arguments are passed through
# (e.g. --check or --qm-only).
# Uses $env:PYTHON if set, else the repo's .venv, else `python` on PATH.
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = $env:PYTHON
if (-not $python) {
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    $python = if (Test-Path $venvPython) { $venvPython } else { "python" }
}
& $python (Join-Path $PSScriptRoot "update_translations.py") @args
exit $LASTEXITCODE
