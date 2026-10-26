#!/usr/bin/env bash
# Compile src/*.ui into src/ui_*.py. Thin wrapper around scripts/compile_ui.py;
# all arguments are passed through (e.g. --check, or specific .ui files).
# Uses $PYTHON if set, else the repo's .venv, else python3 on PATH.
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(dirname "$script_dir")"

if [ -n "${PYTHON:-}" ]; then
    python="$PYTHON"
elif [ -x "$repo_root/.venv/bin/python" ]; then
    python="$repo_root/.venv/bin/python"
elif [ -x "$repo_root/.venv/Scripts/python.exe" ]; then
    python="$repo_root/.venv/Scripts/python.exe"
else
    python="python3"
fi

exec "$python" "$script_dir/compile_ui.py" "$@"
