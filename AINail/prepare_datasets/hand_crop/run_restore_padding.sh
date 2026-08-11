#!/bin/bash
# Run restore_padding.py to restore pre-inpaint black padding on a dir of inpainted crops,
# using the crop_info JSON files detect_and_crop.py wrote alongside the original crops.
#
# Usage:
#   ./run_restore_padding.sh <original_dir> <inpainted_dir> <crop_info_dir> [output_dir] [limit]
set -e

ENV_NAME="ainail_sam3"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ORIGINAL_DIR="$1"
INPAINTED_DIR="$2"
CROP_INFO_DIR="$3"
if [ -z "${ORIGINAL_DIR}" ] || [ -z "${INPAINTED_DIR}" ] || [ -z "${CROP_INFO_DIR}" ]; then
    echo "Usage: $0 <original_dir> <inpainted_dir> <crop_info_dir> [output_dir] [limit]"
    exit 1
fi

OUTPUT_DIR="${4:-${SCRIPT_DIR}/restored_padding_output}"
LIMIT="${5:--1}"

CONDA_BASE="$(conda info --base 2>/dev/null)" || { echo "ERROR: conda not found."; exit 1; }
source "${CONDA_BASE}/etc/profile.d/conda.sh"
conda activate "${ENV_NAME}"

cd "${SCRIPT_DIR}"
python restore_padding.py \
    --original-dir "${ORIGINAL_DIR}" \
    --inpainted-dir "${INPAINTED_DIR}" \
    --crop-info-dir "${CROP_INFO_DIR}" \
    --output-dir "${OUTPUT_DIR}" \
    --limit "${LIMIT}"

echo ""
echo "Output written to ${OUTPUT_DIR}/"
