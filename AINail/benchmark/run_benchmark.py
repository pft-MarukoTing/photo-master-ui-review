#!/usr/bin/env python3
"""
run_benchmark.py -- regression benchmark for AINail checkpoints/workflows.

Sends every (source, reference) pair in a benchmark folder through a running
ComfyUI server with a given checkpoint (LoRA) + workflow api json, scores the
result against the ground-truth target with PSNR/SSIM (whole image and
nail-region vs. background), and writes report.html + results.json
into a timestamped output directory.

Meant to be re-run after every new LoRA training run against a fixed
benchmark set, to catch regressions (hand/background broken by the new
checkpoint) independently of nail-transfer quality. Update the benchmark
standard by pointing --workflow at a new api json or by adding/removing image
pairs in the benchmark dir -- no code changes needed unless a workflow
renames its LoadImage/LoRA node titles (see --source-title etc. below).

Benchmark dir layout:
    benchmark_dir/
      source/       e.g. 0001.jpg   (Input Image 1 / Target hand photo)
      reference/    e.g. 0001.jpg   (Input Image 2 / nail style reference)
      target/       e.g. 0001.jpg   (ground truth: source with reference's nail style)
  Pairs are matched by filename stem across the three folders; unmatched
  files are skipped with a warning.

Usage (run inside the docker/conda env that has requests/numpy/Pillow/
scikit-image -- do NOT invoke python directly outside it):
    python run_benchmark.py \\
        --benchmark-dir /data/.../benchmark_set_v1 \\
        --checkpoint <subfolder>/<name>.safetensors
  where --checkpoint is the path relative to your ComfyUI's own
  models/loras/ dir (subfolder layout there is whatever it happens to be,
  not a fixed convention -- check that dir or the lora_name dropdown in the
  ComfyUI UI). --output-dir defaults to ./output_<benchmark-dir-name>_YYYYMMDD_hhmmss
  if not given; pass it explicitly to control where results land.
"""

import argparse
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from PIL import Image

import comfy_client
import eval_metrics
import report

TZ_UTC8 = timezone(timedelta(hours=8))
IMG_EXTS = (".jpg", ".jpeg", ".png")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKFLOW = REPO_ROOT / "AINail/workflows/nailSalon_workflow_20260626_api.json"
JPEG_QUALITY = 95


def png_bytes_to_jpeg(raw: bytes) -> bytes:
    """ComfyUI's SaveImage node always writes PNG; convert to JPEG here so
    generated/ doesn't balloon in size across repeated benchmark runs."""
    with Image.open(io.BytesIO(raw)) as im:
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
        return buf.getvalue()


def find_pairs(benchmark_dir: Path, source_name: str, reference_name: str, target_name: str):
    def index(sub):
        d = benchmark_dir / sub
        found = {}
        for p in sorted(d.iterdir()):
            if p.suffix.lower() in IMG_EXTS:
                found[p.stem] = p
        return found

    sources = index(source_name)
    references = index(reference_name)
    targets = index(target_name)

    stems = sorted(set(sources) & set(references) & set(targets))
    missing = (set(sources) | set(references) | set(targets)) - set(stems)
    if missing:
        print(f"[warn] {len(missing)} stem(s) missing from at least one of "
              f"{source_name}/{reference_name}/{target_name}, skipped: {sorted(missing)}", file=sys.stderr)

    return [(stem, sources[stem], references[stem], targets[stem]) for stem in stems]


def run_pair(base_url, session, template, args, stem, source_path, reference_path, run_id):
    comfy_source_name = comfy_client.upload_image(base_url, session, source_path)
    comfy_reference_name = comfy_client.upload_image(base_url, session, reference_path)

    wf = comfy_client.patch_workflow(
        template,
        source_title=args.source_title,
        reference_title=args.reference_title,
        lora_title=args.lora_title,
        source_filename=comfy_source_name,
        reference_filename=comfy_reference_name,
        checkpoint=args.checkpoint,
        seed=args.seed,
        filename_prefix=f"benchmark/{run_id}/{stem}",
    )
    prompt_id = comfy_client.submit_prompt(base_url, session, wf)
    outputs = comfy_client.poll_until_done(base_url, session, prompt_id, timeout=args.timeout)

    output_node_id = comfy_client.find_node_by_class(wf, "SaveImage")
    images = outputs.get(output_node_id, {}).get("images", [])
    if not images:
        raise RuntimeError(f"no output images from node {output_node_id}")
    return comfy_client.fetch_output_image(base_url, session, images[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--benchmark-dir", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True,
                         help="lora_name as ComfyUI resolves it: the path relative to that ComfyUI "
                              "instance's own models/loras/ dir (whatever subfolder it actually lives "
                              "under there -- NOT a filesystem path relative to this script or to "
                              "AINail/models/). Check your ComfyUI's models/loras/ layout, or the "
                              "lora_name dropdown in its UI, to get the right value.")
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--output-dir", type=Path, default=None,
                         help="base output dir; a _YYYYMMDD_hhmmss (UTC+8) suffix is appended. "
                              "Defaults to ./output_<benchmark-dir-name>_YYYYMMDD_hhmmss in the current directory.")
    parser.add_argument("--comfyui-host", default="127.0.0.1")
    parser.add_argument("--comfyui-port", type=int, default=8188, help="matches ComfyUI's --port at launch")
    parser.add_argument("--seed", type=int, default=42, help="fixed seed for reproducible cross-checkpoint comparison")
    parser.add_argument("--timeout", type=float, default=600, help="seconds to wait per prompt")
    parser.add_argument("--nail-lab-threshold", type=float, default=5.0, help="Lab-distance threshold used to derive the nail mask from source/target")
    parser.add_argument("--source-dir-name", default="source")
    parser.add_argument("--reference-dir-name", default="reference")
    parser.add_argument("--target-dir-name", default="target")
    parser.add_argument("--source-title", default="Input Image 1 (Target)")
    parser.add_argument("--reference-title", default="Input Image 2 (Reference)")
    parser.add_argument("--lora-title", default="Nail LoRA Loader")
    args = parser.parse_args()
    comfyui_url = f"http://{args.comfyui_host}:{args.comfyui_port}"

    run_id = datetime.now(TZ_UTC8).strftime("%Y%m%d_%H%M%S")
    output_base = args.output_dir if args.output_dir is not None else Path(f"output_{args.benchmark_dir.name}")
    output_dir = output_base.parent / f"{output_base.name}_{run_id}"
    generated_dir = output_dir / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)

    template = json.loads(args.workflow.read_text(encoding="utf-8"))
    pairs = find_pairs(args.benchmark_dir, args.source_dir_name, args.reference_dir_name, args.target_dir_name)
    if not pairs:
        print("[error] no matched (source, reference, target) triples found", file=sys.stderr)
        sys.exit(1)
    print(f"[info] {len(pairs)} pair(s) found, run_id={run_id}, output_dir={output_dir}")

    results = []
    with requests.Session() as session:
        for i, (stem, source_path, reference_path, target_path) in enumerate(pairs, 1):
            print(f"[{i}/{len(pairs)}] {stem} ...", end=" ", flush=True)
            record = {
                "stem": stem,
                "source_path": str(source_path),
                "reference_path": str(reference_path),
                "target_path": str(target_path),
                "generated_path": None,
                "psnr": None,
                "ssim": None,
                "nail_psnr": None,
                "nail_ssim": None,
                "background_psnr": None,
                "background_ssim": None,
                "error": None,
            }
            try:
                raw = run_pair(comfyui_url, session, template, args, stem, source_path, reference_path, run_id)
                generated_path = generated_dir / f"{stem}.jpg"
                generated_path.write_bytes(png_bytes_to_jpeg(raw))
                record["generated_path"] = str(generated_path)

                source_arr = eval_metrics.load_rgb(source_path)
                target_arr = eval_metrics.load_rgb(target_path)
                generated_arr = eval_metrics.load_rgb(generated_path)

                mask = eval_metrics.nail_mask_from_source_target(source_arr, target_arr, args.nail_lab_threshold)
                record["psnr"] = eval_metrics.global_psnr(generated_arr, target_arr)
                record["ssim"] = eval_metrics.global_ssim(generated_arr, target_arr)
                record["nail_psnr"] = eval_metrics.nail_mask_psnr(generated_arr, target_arr, mask)
                record["nail_ssim"] = eval_metrics.nail_mask_ssim(generated_arr, target_arr, mask)
                record["background_psnr"] = eval_metrics.background_psnr(generated_arr, target_arr, mask)
                record["background_ssim"] = eval_metrics.background_ssim(generated_arr, target_arr, mask)
                print(f"psnr={record['psnr']:.1f}dB ssim={record['ssim']:.4f} "
                      f"nail_psnr={record['nail_psnr']:.1f}dB nail_ssim={record['nail_ssim']:.4f} "
                      f"background_psnr={record['background_psnr']:.1f}dB background_ssim={record['background_ssim']:.4f}")
            except Exception as exc:
                record["error"] = str(exc)
                print(f"FAILED: {exc}")
            results.append(record)

    (output_dir / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    meta = {
        "run_id": run_id,
        "checkpoint": args.checkpoint,
        "workflow": str(args.workflow),
        "benchmark_dir": str(args.benchmark_dir),
    }
    report_path = report.render_report(output_dir, results, meta)
    print(f"[done] report: {report_path}")


if __name__ == "__main__":
    main()
