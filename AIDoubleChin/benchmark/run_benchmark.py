#!/usr/bin/env python3
"""
run_benchmark.py -- held-out test benchmark for AIDoubleChin checkpoints.

Sends every control image in a benchmark folder through a running ComfyUI
server once per given checkpoint (LoRA), scores each result against the
ground-truth target with PSNR/SSIM (whole image, edited region, and
everything outside it), and writes results.json, summary.txt and per-image
comparison sheets (input | each checkpoint | ground truth) into a timestamped
output directory.

The edited region is derived per pair from where control and target disagree
(Lab-space diff), i.e. the chin/neck area the retoucher actually touched.
  - edit_psnr / edit_ssim: how close the generated chin is to ground truth.
    Compare against input_edit_psnr (the untouched input scored the same way):
    a checkpoint that does nothing scores ~input_edit_psnr, so the gap is the
    real gain.
  - background_psnr / background_ssim: regression signal, should stay high --
    a drop means the LoRA is changing face/background it shouldn't touch.

Benchmark dir layout (what datasets/prepare_pairs.py writes to <out>/test):
    benchmark_dir/
      control/   e.g. IMG_8153.jpg   (1024x1024 padded, with double chin)
      target/    e.g. IMG_8153.jpg   (ground truth, double chin removed)

Usage (run inside the env that has requests/numpy/Pillow/scikit-image):
    python run_benchmark.py \\
        --benchmark-dir /data/.../double_chin/test \\
        --checkpoint aiDoubleChin/double_chin_exp_20261001_000004000.safetensors \\
                     aiDoubleChin/double_chin_exp_20261001.safetensors
  where each --checkpoint is the path relative to your ComfyUI's own
  models/loras/ dir.
"""

import argparse
import copy
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from PIL import Image, ImageDraw

import comfy_client
import eval_metrics

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()  # Image.open reads .heic/.heif from here on, also in the scripts importing this one
except ImportError:
    register_heif_opener = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TZ_UTC8 = timezone(timedelta(hours=8))
HEIF_EXTS = (".heic", ".heif")
IMG_EXTS = (".jpg", ".jpeg", ".png") + HEIF_EXTS
DEFAULT_WORKFLOW = PROJECT_ROOT / "workflows/doubleChin_workflow_20261001_api.json"
JPEG_QUALITY = 95
SHEET_TILE = 512
METRICS = ["psnr", "ssim", "edit_psnr", "edit_ssim", "background_psnr", "background_ssim"]


def require_heif(paths) -> None:
    """Exits with an install hint if any input is HEIC/HEIF and pillow-heif is missing."""
    if register_heif_opener is None and any(Path(p).suffix.lower() in HEIF_EXTS for p in paths):
        print("[error] HEIC/HEIF inputs need pillow-heif: pip install pillow-heif", file=sys.stderr)
        sys.exit(1)


def png_bytes_to_jpeg(raw: bytes) -> bytes:
    with Image.open(io.BytesIO(raw)) as im:
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
        return buf.getvalue()


def find_pairs(benchmark_dir: Path, control_name: str, target_name: str):
    def index(sub):
        return {p.stem: p for p in sorted((benchmark_dir / sub).iterdir()) if p.suffix.lower() in IMG_EXTS}

    controls = index(control_name)
    targets = index(target_name)
    stems = sorted(set(controls) & set(targets))
    missing = (set(controls) | set(targets)) - set(stems)
    if missing:
        print(f"[warn] {len(missing)} stem(s) missing from {control_name}/ or {target_name}/, "
              f"skipped: {sorted(missing)}", file=sys.stderr)
    return [(stem, controls[stem], targets[stem]) for stem in stems]


def patch_workflow(template: dict, *, input_title: str, lora_title: str, input_filename: str,
                   checkpoint: str, seed: int, filename_prefix: str, face_onnx_dir: str = None) -> dict:
    wf = copy.deepcopy(template)
    wf[comfy_client.find_node_by_title(wf, input_title)]["inputs"]["image"] = input_filename
    wf[comfy_client.find_node_by_title(wf, lora_title)]["inputs"]["lora_name"] = checkpoint
    wf[comfy_client.find_node_by_class(wf, "KSampler")]["inputs"]["seed"] = seed
    if face_onnx_dir:  # face-crop workflow only; the workflow holds the production path
        wf[comfy_client.find_node_by_class(wf, "LoadOnnxFacePipeline")]["inputs"]["ckpt_dir"] = face_onnx_dir

    # Swap the websocket-only save node for a disk-writing SaveImage node so the
    # result can be fetched via /history + /view without holding a websocket open.
    output_id = next(k for k, n in wf.items() if n["class_type"] in ("SaveImage", "SaveImageWebsocket"))
    wf[output_id] = {
        "class_type": "SaveImage",
        "_meta": {"title": "Save Image"},
        "inputs": {"images": wf[output_id]["inputs"]["images"], "filename_prefix": filename_prefix},
    }
    return wf


def run_one(base_url, session, template, args, control_path, checkpoint, filename_prefix) -> bytes:
    comfy_name = comfy_client.upload_image(base_url, session, control_path)
    wf = patch_workflow(template, input_title=args.input_title, lora_title=args.lora_title,
                        input_filename=comfy_name, checkpoint=checkpoint, seed=args.seed,
                        filename_prefix=filename_prefix, face_onnx_dir=args.face_onnx_dir)
    prompt_id = comfy_client.submit_prompt(base_url, session, wf)
    outputs = comfy_client.poll_until_done(base_url, session, prompt_id, timeout=args.timeout)
    output_id = comfy_client.find_node_by_class(wf, "SaveImage")
    images = outputs.get(output_id, {}).get("images", [])
    if not images:
        raise RuntimeError(f"no output images from node {output_id}")
    return comfy_client.fetch_output_image(base_url, session, images[0])


def _combo_options(spec):
    """Allowed values of a ComfyUI dropdown input, or None if it isn't a dropdown.
    Handles both the legacy [[options...], {...}] and newer ["COMBO", {"options": [...]}] formats."""
    if not isinstance(spec, list) or not spec:
        return None
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get("options")
    return None


def preflight(base_url, session, template, args) -> list:
    """Checks every model/LoRA name the workflow will send (per checkpoint) against
    what this ComfyUI instance actually has, before any image is submitted."""
    object_info = session.get(f"{base_url}/object_info", timeout=60).json()
    problems = []
    for ckpt in args.checkpoint:
        wf = patch_workflow(template, input_title=args.input_title, lora_title=args.lora_title,
                            input_filename="preflight.jpg", checkpoint=ckpt, seed=args.seed,
                            filename_prefix="preflight", face_onnx_dir=args.face_onnx_dir)
        for node_id, node in wf.items():
            if node["class_type"] == "LoadImage":
                continue
            info = object_info.get(node["class_type"])
            if info is None:
                problems.append(f'node {node_id}: node type "{node["class_type"]}" is not installed in this ComfyUI')
                continue
            specs = {**info.get("input", {}).get("required", {}), **info.get("input", {}).get("optional", {})}
            for name, value in node["inputs"].items():
                options = _combo_options(specs.get(name))
                if options is not None and isinstance(value, str) and value not in options:
                    problem = f'node {node_id} ({node["_meta"]["title"]}): {name}="{value}" not found in ComfyUI'
                    if problem not in problems:
                        problems.append(problem)
    return problems


def score(control_arr, target_arr, generated_arr, mask) -> dict:
    return {
        "psnr": eval_metrics.global_psnr(generated_arr, target_arr),
        "ssim": eval_metrics.global_ssim(generated_arr, target_arr),
        "edit_psnr": eval_metrics.edit_psnr(generated_arr, target_arr, mask),
        "edit_ssim": eval_metrics.edit_ssim(generated_arr, target_arr, mask),
        "background_psnr": eval_metrics.background_psnr(generated_arr, target_arr, mask),
        "background_ssim": eval_metrics.background_ssim(generated_arr, target_arr, mask),
    }


def save_sheet(path: Path, columns: list):
    """columns: [(label, image_path_or_None), ...] laid out left to right."""
    sheet = Image.new("RGB", (SHEET_TILE * len(columns), SHEET_TILE + 20), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (label, img_path) in enumerate(columns):
        if img_path is not None:
            with Image.open(img_path) as im:
                # fit inside the tile keeping aspect (non-square inputs from run_inference --no-pad)
                scale = SHEET_TILE / max(im.size)
                tile = im.convert("RGB").resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
                sheet.paste(tile, (i * SHEET_TILE + (SHEET_TILE - tile.width) // 2, 20 + (SHEET_TILE - tile.height) // 2))
        draw.text((i * SHEET_TILE + 4, 4), label, fill="black")
    sheet.save(path, quality=90)


def _finite(values):
    return [v for v in values if v is not None and v == v and v != float("inf")]


def write_summary(output_dir: Path, results: list, meta: dict, checkpoints: list) -> Path:
    lines = [f"{k}: {v}" for k, v in meta.items()]
    baseline = _finite([r["input_edit_psnr"] for r in results])
    if baseline:
        lines.append(f"input_edit_psnr (do-nothing baseline): {sum(baseline) / len(baseline):.4f} dB")
    for ckpt in checkpoints:
        rows = [r["checkpoints"][ckpt] for r in results]
        ok = [row for row in rows if row.get("error") is None]
        lines.append("")
        lines.append(f"[{ckpt}] {len(ok)} ok / {len(rows) - len(ok)} failed")
        for key in METRICS:
            vals = _finite([row.get(key) for row in ok])
            if not vals:
                lines.append(f"  {key}: n/a")
                continue
            worst_val, worst_stem = min(
                ((row[key], r["stem"]) for r, row in zip(results, rows)
                 if row.get("error") is None and row.get(key) in vals),
                key=lambda pair: pair[0])
            unit = " dB" if "psnr" in key else ""
            lines.append(f"  {key}: {sum(vals) / len(vals):.4f}{unit} (worst {worst_val:.4f}{unit} @ {worst_stem})")
    summary_path = output_dir / "summary.txt"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--benchmark-dir", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, nargs="+",
                        help="one or more LoRA paths relative to ComfyUI's models/loras/ dir, compared side by side")
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="defaults to ./output_<benchmark-dir-name>; a _YYYYMMDD_hhmmss suffix is always appended")
    parser.add_argument("--comfyui-host", default="127.0.0.1")
    parser.add_argument("--comfyui-port", type=int, default=8188, help="matches ComfyUI's --port at launch")
    parser.add_argument("--seed", type=int, default=42, help="fixed seed for reproducible cross-checkpoint comparison")
    parser.add_argument("--timeout", type=float, default=600, help="seconds to wait per prompt")
    parser.add_argument("--edit-lab-threshold", type=float, default=5.0,
                        help="Lab-distance threshold used to derive the edited region from control/target")
    parser.add_argument("--control-dir-name", default="control")
    parser.add_argument("--target-dir-name", default="target")
    parser.add_argument("--input-title", default="Input Image")
    parser.add_argument("--lora-title", default="Double Chin LoRA Loader")
    parser.add_argument("--face-onnx-dir", default=None,
                        help="face-crop workflow only: dir with the FaceRestorer ONNX models, e.g. "
                             "<ComfyUI>/custom_nodes/ComfyUI-FaceRestorer/core/LVOcclusionONNX; "
                             "defaults to the production path stored in the workflow")
    args = parser.parse_args()
    comfyui_url = f"http://{args.comfyui_host}:{args.comfyui_port}"

    run_id = datetime.now(TZ_UTC8).strftime("%Y%m%d_%H%M%S")
    output_base = args.output_dir if args.output_dir is not None else Path(f"output_{args.benchmark_dir.name}")
    output_dir = output_base.parent / f"{output_base.name}_{run_id}"
    compare_dir = output_dir / "compare"

    template = json.loads(args.workflow.read_text(encoding="utf-8"))
    pairs = find_pairs(args.benchmark_dir, args.control_dir_name, args.target_dir_name)
    if not pairs:
        print("[error] no matched (control, target) pairs found", file=sys.stderr)
        sys.exit(1)
    require_heif(p for pair in pairs for p in pair[1:])
    print(f"[info] {len(pairs)} pair(s) x {len(args.checkpoint)} checkpoint(s), "
          f"run_id={run_id}, output_dir={output_dir}")

    results = []
    with requests.Session() as session:
        problems = preflight(comfyui_url, session, template, args)
        if problems:
            print("[error] the workflow references models this ComfyUI doesn't have:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            print("Base models go in ComfyUI's models/{diffusion_models,text_encoders,vae,loras}/; each --checkpoint "
                  "must be a name relative to models/loras/ (e.g. aiDoubleChin/<file>.safetensors), not a file path.",
                  file=sys.stderr)
            sys.exit(1)
        compare_dir.mkdir(parents=True, exist_ok=True)
        for i, (stem, control_path, target_path) in enumerate(pairs, 1):
            control_arr = eval_metrics.load_rgb(control_path)
            target_arr = eval_metrics.load_rgb(target_path)
            mask = eval_metrics.edit_mask_from_control_target(control_arr, target_arr, args.edit_lab_threshold)
            record = {
                "stem": stem,
                "control_path": str(control_path),
                "target_path": str(target_path),
                "edit_area_ratio": float(mask.mean()),
                "input_edit_psnr": eval_metrics.edit_psnr(control_arr, target_arr, mask),
                "checkpoints": {},
            }
            upload_path = control_path
            if control_path.suffix.lower() in HEIF_EXTS:  # ComfyUI's LoadImage may not read HEIC
                upload_path = output_dir / "inputs" / f"{stem}.jpg"
                upload_path.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(control_arr).save(upload_path, "JPEG", quality=JPEG_QUALITY)
            columns = [("input", control_path)]
            for ckpt in args.checkpoint:
                ckpt_name = Path(ckpt).stem
                print(f"[{i}/{len(pairs)}] {stem} @ {ckpt_name} ...", end=" ", flush=True)
                row = {"generated_path": None, "error": None}
                try:
                    raw = run_one(comfyui_url, session, template, args, upload_path, ckpt,
                                  f"double_chin_benchmark/{run_id}/{ckpt_name}/{stem}")
                    generated_path = output_dir / "generated" / ckpt_name / f"{stem}.jpg"
                    generated_path.parent.mkdir(parents=True, exist_ok=True)
                    generated_path.write_bytes(png_bytes_to_jpeg(raw))
                    row["generated_path"] = str(generated_path)
                    row.update(score(control_arr, target_arr, eval_metrics.load_rgb(generated_path), mask))
                    print(f"edit_psnr={row['edit_psnr']:.1f}dB (input {record['input_edit_psnr']:.1f}dB) "
                          f"background_ssim={row['background_ssim']:.4f}")
                except Exception as exc:
                    row["error"] = str(exc)
                    print(f"FAILED: {exc}")
                record["checkpoints"][ckpt] = row
                columns.append((ckpt_name, row["generated_path"]))
            columns.append(("ground truth", target_path))
            save_sheet(compare_dir / f"{stem}.jpg", columns)
            results.append(record)

    (output_dir / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    meta = {
        "run_id": run_id,
        "workflow": str(args.workflow),
        "benchmark_dir": str(args.benchmark_dir),
        "seed": args.seed,
    }
    summary_path = write_summary(output_dir, results, meta, args.checkpoint)
    print(summary_path.read_text(encoding="utf-8"))
    print(f"[done] summary: {summary_path}, comparison sheets: {compare_dir}")


if __name__ == "__main__":
    main()
