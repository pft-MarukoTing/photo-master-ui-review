#!/bin/bash
# Run apply_crop_info.py to apply detect_and_crop.py's saved crop_info JSON files to
# another directory of images (matched by basename), producing the same square crop.
#
# Usage:
#   ./run_apply_crop_info.sh <input_dir> <crop_info_dir> [output_dir] [limit]
set -e

ENV_NAME="ainail_sam3"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INPUT_DIR="$1"
CROP_INFO_DIR="$2"
if [ -z "${INPUT_DIR}" ] || [ -z "${CROP_INFO_DIR}" ]; then
    echo "Usage: $0 <input_dir> <crop_info_dir> [output_dir] [limit]"
    exit 1
fi

OUTPUT_DIR="${3:-${SCRIPT_DIR}/applied_crop_output}"
LIMIT="${4:--1}"

CONDA_BASE="$(conda info --base 2>/dev/null)" || { echo "ERROR: conda not found."; exit 1; }
source "${CONDA_BASE}/etc/profile.d/conda.sh"
conda activate "${ENV_NAME}"

cd "${SCRIPT_DIR}"
python apply_crop_info.py \
    --input-dir "${INPUT_DIR}" \
    --crop-info-dir "${CROP_INFO_DIR}" \
    --output-dir "${OUTPUT_DIR}" \
    --limit "${LIMIT}"

echo ""
echo "Output written to ${OUTPUT_DIR}/"
