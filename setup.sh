#!/usr/bin/env bash
# Create the local Python environment and install the backend dependencies.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

if [[ ! -d .venv ]]; then
  if command -v python3 >/dev/null 2>&1; then
    python_cmd=python3
  elif command -v python >/dev/null 2>&1; then
    python_cmd=python
  else
    printf 'Python 3 is required. Install it, then rerun setup.sh.\n' >&2
    exit 1
  fi
  "$python_cmd" -m venv .venv
fi

if [[ -f .venv/bin/python ]]; then
  venv_python=.venv/bin/python
  activate_cmd='source .venv/bin/activate'
elif [[ -f .venv/Scripts/python.exe ]]; then
  venv_python=.venv/Scripts/python.exe
  activate_cmd='source .venv/Scripts/activate'
else
  printf 'The existing .venv is incomplete or belongs to another platform.\n' >&2
  printf 'Remove that environment and rerun setup.sh.\n' >&2
  exit 1
fi

if ! "$venv_python" -m pip --version >/dev/null 2>&1; then
  "$venv_python" -m ensurepip --upgrade
fi
"$venv_python" -m pip install -r requirements.txt

printf '\nSetup complete. To activate this environment in your current Bash shell:\n'
printf '  %s\n' "$activate_cmd"
printf 'Then start the server with: python -m uvicorn main:app --reload\n'
