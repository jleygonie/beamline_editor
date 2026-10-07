#!/usr/bin/env bash
# Create (or update) the virtual environment for the Beamline Editor.
# Usage: ./create_venv.sh [python_executable]   (default: python3, or python if python3 is unusable)
# Works on Linux, macOS and Windows (Git Bash). On Windows cmd/PowerShell use create_venv.bat.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

if [ $# -ge 1 ]; then
    PYTHON="$1"
else
    # On Windows "python3" is often the Microsoft Store stub, which fails when run.
    PYTHON=""
    for candidate in python3 python py; do
        if "$candidate" -c "import sys" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
    done
    if [ -z "$PYTHON" ]; then
        echo "No working Python found (tried python3, python, py)." >&2
        exit 1
    fi
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment in $VENV_DIR using $PYTHON ..."
    "$PYTHON" -m venv "$VENV_DIR"
else
    echo "Virtual environment already exists in $VENV_DIR, updating packages ..."
fi

# The venv layout is bin/ on Linux/macOS and Scripts/ on Windows.
if [ -x "$VENV_DIR/bin/python" ]; then
    VENV_PYTHON="$VENV_DIR/bin/python"
else
    VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
fi

"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt"

echo "Done. Launch the editor with ./run_beamline_editor.sh"
