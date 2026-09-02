#!/bin/bash
# Thin wrapper around a single run_inference.py call -- edit the variables
# below per checkpoint/run rather than retyping the full command.
#
# Usage:
#   ./run_inference.sh <checkpoint> <source_dir> <reference_dir> <comfyui_port>
#
# Example:
#   ./run_inference.sh nail_salon_lora/nail_salon_lora_20260819.safetensors source reference 8450
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CHECKPOINT="$1"
SOURCE_DIR="$2"
REFERENCE_DIR="$3"
PORT="$4"

if [ -z "${PORT}" ]; then
    echo "Usage: $0 <checkpoint> <source_dir> <reference_dir> <comfyui_port>"
    echo "Example: $0 nail_salon_lora/nail_salon_lora_20260819.safetensors source reference 8450"
    exit 1
fi

cd "${SCRIPT_DIR}"
python3 run_inference.py \
    --source-dir "${SOURCE_DIR}" \
    --reference-dir "${REFERENCE_DIR}" \
    --checkpoint "${CHECKPOINT}" \
    --comfyui-port "${PORT}" \
    --no-gallery
