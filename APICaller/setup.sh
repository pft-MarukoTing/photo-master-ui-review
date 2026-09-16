#!/bin/bash
set -e

ENV_NAME="apicaller"
PYTHON_VERSION="3.10"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[1/3] Creating conda environment '${ENV_NAME}' with Python ${PYTHON_VERSION}..."
if conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
    echo "  '${ENV_NAME}' already exists, skipping creation (rerun-safe)."
else
    conda create -y -n "${ENV_NAME}" python="${PYTHON_VERSION}"
fi

echo "[2/3] Activating environment..."
CONDA_BASE="$(conda info --base 2>/dev/null)" || { echo "ERROR: conda not found or 'conda info' failed."; exit 1; }
source "${CONDA_BASE}/etc/profile.d/conda.sh" || { echo "ERROR: Failed to source conda.sh."; exit 1; }
conda activate "${ENV_NAME}" || { echo "ERROR: Failed to activate conda environment '${ENV_NAME}'."; exit 1; }

if [ "${CONDA_DEFAULT_ENV}" != "${ENV_NAME}" ]; then
    echo "ERROR: Active environment is '${CONDA_DEFAULT_ENV}', expected '${ENV_NAME}'. Aborting."
    exit 1
fi

echo "[3/3] Installing dependencies..."
pip install --upgrade pip setuptools wheel
# aiohttp: the aipoc gateway backend (fronts Gemini) needs several round
#   trips per generation (refresh token, submit task, poll status, download),
#   so the client is async to keep that useful.
# openai: the OpenAI images API client.
# python-dotenv: loading API credentials from a local .env that is never
#   committed.
# requests: comfy_client.py's synchronous ComfyUI HTTP API client.
# pillow: re-encoding ComfyUI's PNG output to JPEG in comfyui_run.py.
pip install \
    "aiohttp>=3.9" \
    "openai>=1.99" \
    "python-dotenv>=1.0" \
    "requests>=2.31" \
    "pillow>=11.0"

echo ""
echo "Setup complete. To use it:"
echo "  conda activate ${ENV_NAME}"
echo ""
echo "API credentials are read from ${SCRIPT_DIR}/.env, which is git-ignored."
echo "Copy the template and fill in your own keys:"
echo "  cp ${SCRIPT_DIR}/.env.example ${SCRIPT_DIR}/.env"
