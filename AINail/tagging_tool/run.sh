#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
python3 app.py \
    -c data/nailsalon_control_1 \
    -t data/nailsalon_target \
    -m data/src_sized_mask
