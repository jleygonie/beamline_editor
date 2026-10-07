#!/usr/bin/env bash
# Create (or update) the virtual environment for the Beamline Editor.
# Usage: ./create_venv.sh [python_executable]   (default: python3)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON="${1:-python3}"

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment in $VENV_DIR using $PYTHON ..."
    "$PYTHON" -m venv "$VENV_DIR"
else
    echo "Virtual environment already exists in $VENV_DIR, updating packages ..."
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt"

echo "Done. Launch the editor with ./run_beamline_editor.sh"
