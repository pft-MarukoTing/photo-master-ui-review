#!/bin/bash
# Thin wrapper around a single run_benchmark.py --no-report call -- edit the
# variables below per checkpoint/run rather than retyping the full command.
#
# Usage:
#   ./run_benchmark.sh <checkpoint> <output_dir> <benchmark_dir> <comfyui_port>
#
# Example:
#   ./run_benchmark.sh nail_salon_lora/nail_salon_lora_20260819.safetensors output_benchmark_20260819 TestData_All 8450
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CHECKPOINT="$1"
OUTPUT_DIR="$2"
BENCHMARK_DIR="$3"
PORT="$4"

if [ -z "${PORT}" ]; then
    echo "Usage: $0 <checkpoint> <output_dir> <benchmark_dir> <comfyui_port>"
    echo "Example: $0 nail_salon_lora/nail_salon_lora_20260819.safetensors output_benchmark_20260819 TestData 8450"
    exit 1
fi

cd "${SCRIPT_DIR}"
python3 run_benchmark.py \
    --benchmark-dir "${BENCHMARK_DIR}" \
    --checkpoint "${CHECKPOINT}" \
    --comfyui-port "${PORT}" \
    --output-dir "${OUTPUT_DIR}" \
    --no-report
