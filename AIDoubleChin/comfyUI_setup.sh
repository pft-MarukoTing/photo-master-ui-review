#!/bin/bash
# Downloads the base models workflows/doubleChin_workflow_*_api.json needs into ComfyUI's models/ folder.
# Uses the huggingface_hub Python API already in the env -- does NOT pip install/upgrade anything.
#
# Usage: bash comfyUI.sh [ComfyUI models dir]
set -e
M="${1:-/data/home/rdmt/jerry_chen/ComfyUI/models}"

python - "$M" <<'EOF'
import os
import shutil
import sys

from huggingface_hub import hf_hub_download

models = sys.argv[1]
# Stage inside models/ (same disk, so the final move is instant) instead of /tmp or the HF cache.
stage = os.path.join(models, ".hf_download")
files = [  # (repo, path in repo, ComfyUI models subfolder)
    ("lightx2v/Qwen-Image-Lightning", "Qwen-Image-Edit-2509/qwen_image_edit_2509_fp8_e4m3fn_scaled.safetensors", "diffusion_models"),
    ("Comfy-Org/Qwen-Image_ComfyUI", "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors", "text_encoders"),
    ("Comfy-Org/Qwen-Image_ComfyUI", "split_files/vae/qwen_image_vae.safetensors", "vae"),
    ("lightx2v/Qwen-Image-Lightning", "Qwen-Image-Lightning-4steps-V2.0.safetensors", "loras"),
]
for repo, filename, sub in files:
    dst = os.path.join(models, sub, os.path.basename(filename))
    if os.path.exists(dst):
        print(f"[skip] {dst} already exists")
        continue
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    print(f"[download] {repo} :: {filename}")
    src = hf_hub_download(repo, filename, local_dir=stage)
    shutil.move(src, dst)
    print(f"[ok] {dst} ({os.path.getsize(dst) / 1e9:.1f} GB)")
shutil.rmtree(stage, ignore_errors=True)
print("done -- restart ComfyUI if it doesn't list the new models")
EOF
