#!/bin/bash
# Run segment_nails.py end-to-end on a real dataset dir (defaults to the full dataset;
# pass a positive limit as $3 to only process the first N images for a quick test).
#
# Usage:
#   ./run_segment_nails.sh <input_dir> [output_dir] [limit]
set -e

ENV_NAME="ainail_sam3"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INPUT_DIR="$1"
if [ -z "${INPUT_DIR}" ]; then
    echo "Usage: $0 <input_dir> [output_dir] [limit]"
    exit 1
fi

CHECKPOINT="${SCRIPT_DIR}/../sam3_common/models/sam3.pt"
OUTPUT_DIR="${2:-${SCRIPT_DIR}/sam3_nail_masks_output}"
LIMIT="${3:--1}"

CONDA_BASE="$(conda info --base 2>/dev/null)" || { echo "ERROR: conda not found."; exit 1; }
source "${CONDA_BASE}/etc/profile.d/conda.sh"
conda activate "${ENV_NAME}"
export SETUPTOOLS_USE_DISTUTILS=local

cd "${SCRIPT_DIR}"
python segment_nails.py \
    --input-dir "${INPUT_DIR}" \
    --output-dir "${OUTPUT_DIR}" \
    --checkpoint "${CHECKPOINT}" \
    --text-prompt nail --confidence-threshold 0.2 --max-detections 10 \
    --limit "${LIMIT}"

echo ""
echo "Output written to ${OUTPUT_DIR}/"
