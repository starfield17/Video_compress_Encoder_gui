#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

# If no explicit python binary given, try to use conda environment
if [[ $# -eq 0 ]]; then
    if [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
        PYTHON_BIN="${CONDA_PREFIX}/bin/python"
        echo "Detected conda environment: ${CONDA_PREFIX}"
    else
        PYTHON_BIN="python3"
    fi
else
    PYTHON_BIN="$1"
fi

echo "Using Python: ${PYTHON_BIN}"
"${PYTHON_BIN}" scripts/build_pyinstaller.py --clean
