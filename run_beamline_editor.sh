#!/usr/bin/env bash
# Launch the Beamline Editor using the project's virtual environment.
# Usage: ./run_beamline_editor.sh [file_to_open]
# Works on Linux, macOS and Windows (Git Bash). On Windows cmd/PowerShell use run_beamline_editor.bat.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The venv layout is bin/ on Linux/macOS and Scripts/ on Windows.
if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
elif [ -x "$SCRIPT_DIR/.venv/Scripts/python.exe" ]; then
    VENV_PYTHON="$SCRIPT_DIR/.venv/Scripts/python.exe"
else
    echo "Virtual environment not found. Run ./create_venv.sh first." >&2
    exit 1
fi

# Resolve a relative file argument against the caller's directory before cd-ing.
args=()
for a in "$@"; do
    if [ -e "$a" ]; then args+=("$(realpath "$a")"); else args+=("$a"); fi
done

cd "$SCRIPT_DIR"
exec "$VENV_PYTHON" src/main.py ${args[@]+"${args[@]}"}
