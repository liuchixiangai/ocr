# Compile src/*.ui into src/ui_*.py. Thin wrapper around scripts/compile_ui.py;
# all arguments are passed through (e.g. -- --check, or specific .ui files).
# Uses $env:PYTHON if set, else the repo's .venv, else `python` on PATH.
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = $env:PYTHON
if (-not $python) {
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    $python = if (Test-Path $venvPython) { $venvPython } else { "python" }
}
& $python (Join-Path $PSScriptRoot "compile_ui.py") @args
exit $LASTEXITCODE
