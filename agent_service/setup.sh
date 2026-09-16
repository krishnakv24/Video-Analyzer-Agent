#!/usr/bin/env bash
# Setup script for agent_service Python virtual environment.
# Compatible with Linux, macOS, WSL, and Git Bash on Windows.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================================"
echo "Setting up Agent Service Virtual Environment"
echo "================================================"

# 1. Locate Python 3.10+
PYTHON_CMD="${AGENT_PYTHON:-}"
if [[ -z "$PYTHON_CMD" ]]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && \
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      PYTHON_CMD="$candidate"
      break
    fi
  done
fi

if [[ -z "$PYTHON_CMD" ]]; then
  echo "Error: Python 3.10+ is required. Please install Python 3.10+ or set AGENT_PYTHON." >&2
  exit 1
fi

echo "Using Python: $($PYTHON_CMD --version) at $(command -v "$PYTHON_CMD")"

# 2. Create .venv if not exists
VENV_DIR="$SCRIPT_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
  echo "Creating virtual environment at $VENV_DIR..."
  "$PYTHON_CMD" -m venv "$VENV_DIR"
else
  echo "Reusing existing virtual environment at $VENV_DIR"
fi

# 3. Detect platform paths (POSIX vs Windows Git Bash)
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
    VENV_PIP="$VENV_DIR/Scripts/pip.exe"
    ACTIVATE_CMD="source agent_service/.venv/Scripts/activate"
    ;;
  *)
    VENV_PYTHON="$VENV_DIR/bin/python"
    VENV_PIP="$VENV_DIR/bin/pip"
    ACTIVATE_CMD="source agent_service/.venv/bin/activate"
    ;;
esac

if [[ ! -f "$VENV_PYTHON" ]]; then
  echo "Error: Virtual environment python binary not found at $VENV_PYTHON" >&2
  exit 1
fi

# 4. Install / update dependencies
echo "Upgrading pip..."
"$VENV_PYTHON" -m pip install --upgrade pip --quiet

if [[ -f "$SCRIPT_DIR/requirements.txt" ]]; then
  echo "Installing dependencies from requirements.txt..."
  "$VENV_PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt"
else
  echo "Warning: requirements.txt not found in $SCRIPT_DIR" >&2
fi

echo ""
echo "================================================"
echo "Agent Service environment setup complete!"
echo "================================================"
echo "To activate this environment, run:"
echo "  $ACTIVATE_CMD"
echo "================================================"
