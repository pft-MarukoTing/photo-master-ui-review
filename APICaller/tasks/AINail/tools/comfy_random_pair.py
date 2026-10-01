#!/usr/bin/env python3
"""comfy_random_pair.py -- pair each image in --source-dir with one randomly
picked image from --reference-dir, and run every pair through ComfyUI's
nail-transfer workflow (source gets the reference's nail style).

Unlike AINail/benchmark/run_inference.py (full source x reference cross
product) or comfyui_run.py (one explicit pair), this is "one random reference
per source" -- e.g. testing --source-dir against a large real-photo dataset
without running every source against every reference in it.

Usage (run inside the `apicaller` conda env, from the APICaller/ directory):
    python tools/comfy_random_pair.py \\
        --source-dir out/prompt_v5_locked \\
        --reference-dir /data/.../sam3_cropped_control_2 \\
        --checkpoint nail_salon_lora/nail_salon_lora_20260826.safetensors \\
        --comfyui-port 8450 \\
        --out-dir out/prompt_v5_locked_comfy
"""

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.comfyui_run import DEFAULT_WORKFLOW, run_pair

TZ_UTC8 = timezone(timedelta(hours=8))
IMG_EXTS = (".jpg", ".jpeg", ".png")


def list_images(d: Path) -> list:
    return sorted(p for p in d.iterdir() if p.suffix.lower() in IMG_EXTS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--reference-dir", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True,
                         help="lora_name as ComfyUI resolves it (relative to its own models/loras/ dir)")
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--comfyui-host", default="127.0.0.1")
    parser.add_argument("--comfyui-port", type=int, default=8188)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--source-title", default="Input Image 1 (Target)")
    parser.add_argument("--reference-title", default="Input Image 2 (Reference)")
    parser.add_argument("--lora-title", default="Nail LoRA Loader")
    parser.add_argument("--random-seed", type=int, default=None,
                         help="seed Python's random module for reproducible reference picks")
    args = parser.parse_args()

    if args.random_seed is not None:
        random.seed(args.random_seed)

    sources = list_images(args.source_dir)
    references = list_images(args.reference_dir)
    if not sources:
        raise SystemExit(f"no images found in {args.source_dir}")
    if not references:
        raise SystemExit(f"no images found in {args.reference_dir}")
    print(f"[info] {len(sources)} source(s), picking randomly from {len(references)} reference(s)")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(TZ_UTC8).strftime("%Y%m%d_%H%M%S")
    pairs = []

    for i, source in enumerate(sources, 1):
        reference = random.choice(references)
        out_path = args.out_dir / f"{source.stem}__{reference.stem}.jpg"
        print(f"[{i}/{len(sources)}] {source.name} + {reference.name} -> {out_path.name}")
        try:
            run_pair(
                source, reference, args.checkpoint, out_path,
                workflow=args.workflow, comfyui_host=args.comfyui_host, comfyui_port=args.comfyui_port,
                seed=args.seed, timeout=args.timeout,
                source_title=args.source_title, reference_title=args.reference_title, lora_title=args.lora_title,
                filename_prefix=f"apicaller/random_pair/{run_id}/{source.stem}",
            )
            pairs.append({"source": str(source), "reference": str(reference), "result": str(out_path), "error": None})
        except Exception as e:
            print(f"  FAILED: {e}")
            pairs.append({"source": str(source), "reference": str(reference), "result": None, "error": str(e)})

    manifest_path = args.out_dir / f"manifest_{run_id}.json"
    manifest_path.write_text(json.dumps(pairs, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for p in pairs if p["error"] is None)
    print(f"[done] {ok}/{len(pairs)} succeeded -> {args.out_dir} (manifest: {manifest_path})")


if __name__ == "__main__":
    main()
