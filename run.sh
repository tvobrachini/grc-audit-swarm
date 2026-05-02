#!/usr/bin/env bash
# Launch the GRC Audit Swarm Streamlit app
# Uses the shared venv from the scf-auto-crosswalker project

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/../scf-auto-crosswalker/.venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ venv not found at $VENV_PYTHON"
    echo "   Run: cd ../scf-auto-crosswalker && uv venv && uv pip install ..."
    exit 1
fi

echo "🚀 Starting GRC Audit Swarm..."
PYTHONPATH="$SCRIPT_DIR/src" "$SCRIPT_DIR/../scf-auto-crosswalker/.venv/bin/streamlit" run "$SCRIPT_DIR/app.py"
