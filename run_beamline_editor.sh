#!/usr/bin/env bash
# Launch the Beamline Editor using the project's virtual environment.
# Usage: ./run_beamline_editor.sh [file_to_open]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [ ! -x "$VENV_PYTHON" ]; then
    echo "Virtual environment not found. Run ./create_venv.sh first." >&2
    exit 1
fi

# Resolve a relative file argument against the caller's directory before cd-ing.
args=()
for a in "$@"; do
    if [ -e "$a" ]; then args+=("$(realpath "$a")"); else args+=("$a"); fi
done

cd "$SCRIPT_DIR"
exec "$VENV_PYTHON" src/main.py "${args[@]}"
