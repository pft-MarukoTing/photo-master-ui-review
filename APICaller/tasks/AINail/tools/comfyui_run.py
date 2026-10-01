#!/usr/bin/env python3
"""comfyui_run.py -- run a single (source, reference) pair through a running
ComfyUI server with a given checkpoint (LoRA) + workflow api json.

Copied/adapted from AINail/benchmark/run_inference.py for one explicit pair
instead of a full source-dir x reference-dir cross product; comfy_client.py
itself is a straight copy of AINail/benchmark/comfy_client.py (kept in sync
manually -- AINail/benchmark's own copy and its callers are untouched).

Usage (run inside the `apicaller` conda env -- see setup.sh -- from the
APICaller/ directory):
    python tasks/AINail/tools/comfyui_run.py \\
        --source ../nail_generation_samples/nail_openai.png \\
        --reference ../nail_generation_samples/nail_gemini.png \\
        --checkpoint nail_salon_lora/nail_salon_lora_20260826.safetensors \\
        --comfyui-port 8450
"""

import argparse
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from api_caller import comfy_client

TZ_UTC8 = timezone(timedelta(hours=8))
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
DEFAULT_WORKFLOW = REPO_ROOT / "AINail/workflows/nailSalon_workflow_20260924_api.json"
JPEG_QUALITY = 95


def png_bytes_to_jpeg(raw: bytes) -> bytes:
    with Image.open(io.BytesIO(raw)) as im:
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
        return buf.getvalue()


def run_pair(
    source: Path,
    reference: Path,
    checkpoint: str,
    out_path: Path,
    workflow: Path = DEFAULT_WORKFLOW,
    comfyui_host: str = "127.0.0.1",
    comfyui_port: int = 8188,
    seed: int = 42,
    timeout: float = 600,
    source_title: str = "Input Image 1 (Target)",
    reference_title: str = "Input Image 2 (Reference)",
    lora_title: str = "Nail LoRA Loader",
    filename_prefix: str | None = None,
) -> Path:
    """Send one (source, reference) pair through ComfyUI and write the JPEG
    result to out_path. Reused by the CLI below and by pipeline.py."""
    comfyui_url = f"http://{comfyui_host}:{comfyui_port}"
    filename_prefix = filename_prefix or f"apicaller/{out_path.stem}"
    template = json.loads(workflow.read_text(encoding="utf-8"))

    with requests.Session() as session:
        comfy_source_name = comfy_client.upload_image(comfyui_url, session, source)
        comfy_reference_name = comfy_client.upload_image(comfyui_url, session, reference)

        wf = comfy_client.patch_workflow(
            template,
            source_title=source_title,
            reference_title=reference_title,
            lora_title=lora_title,
            source_filename=comfy_source_name,
            reference_filename=comfy_reference_name,
            checkpoint=checkpoint,
            seed=seed,
            filename_prefix=filename_prefix,
        )
        print(f"[info] submitting prompt (source={source.name}, reference={reference.name}, "
              f"checkpoint={checkpoint})")
        prompt_id = comfy_client.submit_prompt(comfyui_url, session, wf)
        outputs = comfy_client.poll_until_done(comfyui_url, session, prompt_id, timeout=timeout)

        output_node_id = comfy_client.find_node_by_class(wf, "SaveImage")
        images = outputs.get(output_node_id, {}).get("images", [])
        if not images:
            raise RuntimeError(f"no output images from node {output_node_id}")
        raw = comfy_client.fetch_output_image(comfyui_url, session, images[0])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(png_bytes_to_jpeg(raw))
    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, type=Path, help="target hand photo (gets the new nail art)")
    parser.add_argument("--reference", required=True, type=Path, help="nail style reference photo")
    parser.add_argument("--checkpoint", required=True,
                         help="lora_name as ComfyUI resolves it: the path relative to that ComfyUI "
                              "instance's own models/loras/ dir.")
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--out-dir", type=Path, default=Path("out"))
    parser.add_argument("--out-name", default=None, help="output filename stem; defaults to <source>__<reference>")
    parser.add_argument("--comfyui-host", default="127.0.0.1")
    parser.add_argument("--comfyui-port", type=int, default=8188, help="matches ComfyUI's --port at launch")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--source-title", default="Input Image 1 (Target)")
    parser.add_argument("--reference-title", default="Input Image 2 (Reference)")
    parser.add_argument("--lora-title", default="Nail LoRA Loader")
    args = parser.parse_args()

    run_id = datetime.now(TZ_UTC8).strftime("%Y%m%d_%H%M%S")
    out_name = args.out_name or f"{args.source.stem}__{args.reference.stem}"
    out_path = args.out_dir / f"{out_name}.jpg"

    run_pair(
        args.source, args.reference, args.checkpoint, out_path,
        workflow=args.workflow, comfyui_host=args.comfyui_host, comfyui_port=args.comfyui_port,
        seed=args.seed, timeout=args.timeout,
        source_title=args.source_title, reference_title=args.reference_title, lora_title=args.lora_title,
        filename_prefix=f"apicaller/{run_id}/{out_name}",
    )
    print(f"[done] -> {out_path}")


if __name__ == "__main__":
    main()
