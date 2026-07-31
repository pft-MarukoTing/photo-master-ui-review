#!/bin/bash
set -e

ENV_NAME="ainail_sam3"
PYTHON_VERSION="3.12"
SAM3_REPO="https://github.com/facebookresearch/sam3.git"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAM3_SRC_DIR="${SCRIPT_DIR}/.sam3_src"

echo "[1/4] Creating conda environment '${ENV_NAME}' with Python ${PYTHON_VERSION}..."
if conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
    echo "  '${ENV_NAME}' already exists, skipping creation (rerun-safe)."
else
    conda create -y -n "${ENV_NAME}" python="${PYTHON_VERSION}"
fi

echo "[2/4] Activating environment..."
CONDA_BASE="$(conda info --base 2>/dev/null)" || { echo "ERROR: conda not found or 'conda info' failed."; exit 1; }
source "${CONDA_BASE}/etc/profile.d/conda.sh" || { echo "ERROR: Failed to source conda.sh."; exit 1; }
conda activate "${ENV_NAME}" || { echo "ERROR: Failed to activate conda environment '${ENV_NAME}'."; exit 1; }

if [ "${CONDA_DEFAULT_ENV}" != "${ENV_NAME}" ]; then
    echo "ERROR: Active environment is '${CONDA_DEFAULT_ENV}', expected '${ENV_NAME}'. Aborting."
    exit 1
fi

echo "[3/4] Installing torch/torchvision..."
# Some environments set SETUPTOOLS_USE_DISTUTILS=stdlib globally, which breaks setuptools
# on Python 3.12 (stdlib distutils was removed). Force setuptools' own vendored copy.
export SETUPTOOLS_USE_DISTUTILS=local
pip install --upgrade pip setuptools wheel
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

echo "[4/4] Cloning and installing facebookresearch/sam3..."
if [ ! -d "${SAM3_SRC_DIR}" ]; then
    git clone "${SAM3_REPO}" "${SAM3_SRC_DIR}"
fi
pip install -e "${SAM3_SRC_DIR}" --no-build-isolation
# sam3's pyproject.toml doesn't declare all of its actual runtime imports (e.g. einops) --
# install the full set known to be needed (matches custom_nodes/comfyui-sam3's
# comfy-env.toml pypi-dependencies, minus the ComfyUI-only comfy-aimdo/comfy-kitchen).
pip install pillow "numpy<2" einops ftfy regex iopath huggingface_hub \
    opencv-python-headless scikit-image tqdm typing_extensions requests \
    transformers timm safetensors pycocotools psutil

echo ""
echo "Setup complete. To use the pipeline:"
echo "  conda activate ${ENV_NAME}"
echo "  cd ${SCRIPT_DIR}"
echo ""
echo "You still need a local SAM3 checkpoint (sam3.pt). Symlink your own copy, e.g.:"
echo "  ln -s /path/to/your/sam3.pt ${SCRIPT_DIR}/models/sam3.pt"
echo "then pass it explicitly, e.g.:"
echo "  python detect_and_crop.py --checkpoint ${SCRIPT_DIR}/models/sam3.pt ..."
mkdir -p "${SCRIPT_DIR}/models"
