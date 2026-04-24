#!/bin/bash
set -e

ENV_NAME="ainail"
PYTHON_VERSION="3.10"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[1/3] Creating conda environment '${ENV_NAME}' with Python ${PYTHON_VERSION}..."
conda create -y -n "${ENV_NAME}" python="${PYTHON_VERSION}"

echo "[2/3] Activating environment..."
CONDA_BASE="$(conda info --base 2>/dev/null)" || { echo "ERROR: conda not found or 'conda info' failed."; exit 1; }
source "${CONDA_BASE}/etc/profile.d/conda.sh" || { echo "ERROR: Failed to source conda.sh."; exit 1; }
conda activate "${ENV_NAME}" || { echo "ERROR: Failed to activate conda environment '${ENV_NAME}'."; exit 1; }

# verify we are actually inside the right env before touching pip
ACTIVE_ENV="$(conda info --envs | awk '/\*/ {print $1}')"
if [ "${ACTIVE_ENV}" != "${ENV_NAME}" ]; then
    echo "ERROR: Active environment is '${ACTIVE_ENV}', expected '${ENV_NAME}'. Aborting."
    exit 1
fi

echo "[3/3] Installing dependencies..."
pip install -r "${SCRIPT_DIR}/requirements.txt"

echo ""
echo "Setup complete. To use the pipeline:"
echo "  conda activate ${ENV_NAME}"
echo "  cd $(dirname "${SCRIPT_DIR}")/prepare_datasets"
echo "  python pipeline.py"
