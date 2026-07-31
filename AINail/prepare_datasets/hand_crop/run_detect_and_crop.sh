#!/bin/bash
# Run detect_and_crop.py end-to-end on the real dataset (defaults to the full dataset;
# pass a positive limit as $1 to only process the first N pairs for a quick test).
#
# Usage:
#   ./run_detect_and_crop.sh [output_dir] [limit]
set -e

ENV_NAME="ainail_sam3"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CONTROL_1_DIR="/data/home/rdmt/jack_lin/MTImageAI_dataset/AINail/datasets/nailsalon_control_1"
CONTROL_2_DIR="/data/home/rdmt/jack_lin/MTImageAI_dataset/AINail/datasets/nailsalon_control_2"
TARGET_DIR="/data/home/rdmt/jack_lin/MTImageAI_dataset/AINail/datasets/nailsalon_target"
CHECKPOINT="${SCRIPT_DIR}/models/sam3.pt"

OUTPUT_DIR="${1:-${SCRIPT_DIR}/sam3_cropped_output}"
LIMIT="${2:--1}"

CONDA_BASE="$(conda info --base 2>/dev/null)" || { echo "ERROR: conda not found."; exit 1; }
source "${CONDA_BASE}/etc/profile.d/conda.sh"
conda activate "${ENV_NAME}"
export SETUPTOOLS_USE_DISTUTILS=local

cd "${SCRIPT_DIR}"
python detect_and_crop.py \
    --control-1-dir "${CONTROL_1_DIR}" \
    --control-2-dir "${CONTROL_2_DIR}" \
    --target-dir "${TARGET_DIR}" \
    --output-control-1 "${OUTPUT_DIR}/sam3_cropped_control_1" \
    --output-control-2 "${OUTPUT_DIR}/sam3_cropped_control_2" \
    --output-target "${OUTPUT_DIR}/sam3_cropped_target" \
    --checkpoint "${CHECKPOINT}" \
    --text-prompt hand --confidence-threshold 0.2 --max-detections 2 \
    --scale 1.2 --min-area-ratio 0.005 \
    --limit "${LIMIT}"

echo ""
echo "Output written to ${OUTPUT_DIR}/ (sam3_cropped_control_1/, sam3_cropped_control_2/, sam3_cropped_target/)"
