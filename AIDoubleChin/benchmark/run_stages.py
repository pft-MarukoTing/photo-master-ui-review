#!/usr/bin/env python3
"""
run_stages.py -- dumps every intermediate stage of the face-crop workflow
(workflows/doubleChin_faceCrop_workflow_*_api.json) for visual checking.

One ComfyUI run per image; a SaveImage node is attached to each stage, in
workflow order:
    1 source         the sRGB input sent to ComfyUI
    2 crop           AIDoubleChinFaceRegionCrop output (face + neck, ~1MP)
    3 lora           LoRA result on the crop (VAEDecode)
    4 output         AIDoubleChinPasteBack (FaceRegionPasteBack in the 20261006
                     workflow): crop result pasted back into the source
If the workflow still has a FaceDetailRestorerwMetadata node (older/aiSmile-style
graphs), the paste-back is saved as 4_pasted and the restorer result as 5_output.

--paste-modes crop mask adds one AIDoubleChinPasteBack per
mode on the same LoRA result (same sample, so only the paste-back differs) and
saves 4_output_<mode>.jpg plus sheets/<stem>_modes.jpg: 1:1 windows of the
upper face and the edit, source | each mode | |last mode - source| x3.

Sheet per image (2 rows x 5 columns; diff maps: brighter = bigger change):
    row 1: source (red box = area the paste-back changed, yellow = zoom window
           around the strongly edited chin) | crop | lora | source (zoom) | output (zoom)
    row 2: output full frame | |output - source| x3 full frame | |lora - crop| x3 |
           |output - source| x3 (zoom) | |output - pasted| x8 (zoom, restorer only)
|lora - crop| should light up the chin/jaw/neck only; |output - source| should
fade out before the red box edge (no visible seam).

Output dir layout:
    output_stages_<input-dir-name>_YYYYMMDD_hhmmss/<checkpoint>/
      <stem>/1_source.jpg ... 4_output.jpg   each stage at full resolution
      sheets/<stem>.jpg                     the sheet above

Usage:
    python run_stages.py --input-dir ../datasets/images \\
        --checkpoint aiDoubleChin/double_chin_exp_20261001.safetensors \\
        --face-onnx-dir <ComfyUI>/custom_nodes/ComfyUI-FaceRestorer/core/ONNX \\
        --comfyui-port 8190 [--paste-modes crop mask]
"""

import argparse
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageOps

import comfy_client
from run_benchmark import JPEG_QUALITY, PROJECT_ROOT, TZ_UTC8, patch_workflow, preflight
from run_inference import list_inputs, to_srgb

DEFAULT_WORKFLOW = PROJECT_ROOT / "workflows/doubleChin_faceCrop_workflow_20261014_api.json"
TILE = 512
LABEL_H = 20
# stage name -> class_type of the node whose first output is that stage
STAGE_NODES = {
    "crop": "AIDoubleChinFaceRegionCrop",
    "lora": "VAEDecode",
    "pasted": "FaceRegionPasteBack",
}
PASTE_MODES = ["crop", "mask"]  # AIDoubleChinPasteBack's mode options


def add_stage_savers(wf: dict, prefix: str) -> dict:
    """Attaches a SaveImage node to each intermediate stage; returns {stage: save_node_id}.
    The final output is the SaveImage patch_workflow already put on the output node."""
    savers = {}
    has_restorer = any(n["class_type"] == "FaceDetailRestorerwMetadata" for n in wf.values())
    for stage, class_type in STAGE_NODES.items():
        if stage == "pasted" and not has_restorer:
            continue  # the paste-back result is the output itself
        src = comfy_client.find_node_by_class(wf, class_type)
        node_id = f"stage_{stage}"
        wf[node_id] = {
            "class_type": "SaveImage",
            "_meta": {"title": f"Stage {stage}"},
            "inputs": {"images": [src, 0], "filename_prefix": f"{prefix}_{stage}"},
        }
        savers[stage] = node_id
    savers["output"] = comfy_client.find_node_by_class(wf, "SaveImage")  # first one = patched output
    return savers


def mode_tag(mode: str) -> str:
    return mode.replace("+", "_")


def add_mode_savers(wf: dict, prefix: str, modes: list, savers: dict):
    """One extra AIDoubleChinPasteBack per mode, all fed by the same LoRA result."""
    paste_id = comfy_client.find_node_by_class(wf, "AIDoubleChinPasteBack")
    for mode in modes:
        tag = mode_tag(mode)
        wf[f"paste_{tag}"] = {**wf[paste_id], "inputs": {**wf[paste_id]["inputs"], "mode": mode}}
        wf[f"stage_output_{tag}"] = {
            "class_type": "SaveImage",
            "_meta": {"title": f"Stage output {mode}"},
            "inputs": {"images": [f"paste_{tag}", 0], "filename_prefix": f"{prefix}_output_{tag}"},
        }
        savers[f"output_{tag}"] = f"stage_output_{tag}"


def run_stages(base_url, session, template, args, src_path, checkpoint, prefix) -> dict:
    comfy_name = comfy_client.upload_image(base_url, session, src_path)
    wf = patch_workflow(template, input_title=args.input_title, lora_title=args.lora_title,
                        input_filename=comfy_name, checkpoint=checkpoint, seed=args.seed,
                        filename_prefix=f"{prefix}_output", face_onnx_dir=args.face_onnx_dir)
    if args.neck_extend_ratio is not None:
        wf[comfy_client.find_node_by_class(wf, "AIDoubleChinFaceRegionCrop")]["inputs"]["neck_extend_ratio"] = \
            args.neck_extend_ratio
    savers = add_stage_savers(wf, prefix)
    if args.paste_modes:
        # "crop" always runs: its pasted area locates the upper-face window of the modes sheet
        add_mode_savers(wf, prefix, ["crop"] + [m for m in args.paste_modes if m != "crop"], savers)
    prompt_id = comfy_client.submit_prompt(base_url, session, wf)
    outputs = comfy_client.poll_until_done(base_url, session, prompt_id, timeout=args.timeout)
    stages = {}
    for stage, node_id in savers.items():
        images = outputs.get(node_id, {}).get("images", [])
        if not images:
            raise RuntimeError(f"no image from stage {stage} (node {node_id})")
        raw = comfy_client.fetch_output_image(base_url, session, images[0])
        with Image.open(io.BytesIO(raw)) as im:
            stages[stage] = im.convert("RGB")
    return stages


def diff_map(a: Image.Image, b: Image.Image, gain: float) -> Image.Image:
    if a.size != b.size:
        b = b.resize(a.size, Image.LANCZOS)
    d = np.abs(np.asarray(a, np.int16) - np.asarray(b, np.int16)).max(axis=2)
    return Image.fromarray(np.clip(d * gain, 0, 255).astype(np.uint8)).convert("RGB")


def _bbox(mask):
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def changed_box(source: Image.Image, pasted: Image.Image):
    """Bounding box of every pixel the paste-back changed (= the crop minus its fully
    faded margin); None if nothing changed."""
    d = np.abs(np.asarray(source, np.int16) - np.asarray(pasted, np.int16)).max(axis=2)
    return _bbox(d > 1)


def edit_box(source: Image.Image, pasted: Image.Image, threshold: int = 15):
    """Square zoom window around where the LoRA really changed the image (the chin),
    ignoring the faint VAE round-trip change over the rest of the crop: bbox of the
    strongly changed pixels (diff on a 1/8 downscale, which drops isolated noise;
    threshold relative to the strongest change so scattered mid-size drift elsewhere
    doesn't stretch the box), grown by 75% and squared. None if nothing changed that much."""
    small = (max(1, source.width // 8), max(1, source.height // 8))
    a = np.asarray(source.resize(small, Image.BOX), np.int16)
    b = np.asarray(pasted.resize(small, Image.BOX), np.int16)
    d = np.abs(a - b).max(axis=2)
    box = _bbox(d > max(threshold, 0.5 * d.max()))
    if box is None:
        return None
    cx, cy = (box[0] + box[2]) / 2 * 8, (box[1] + box[3]) / 2 * 8
    half = min(max(box[2] - box[0], box[3] - box[1]) * 8 * 1.75 / 2, min(source.size) / 2)
    x1 = int(max(0, min(cx - half, source.width - 2 * half)))
    y1 = int(max(0, min(cy - half, source.height - 2 * half)))
    return (x1, y1, int(x1 + 2 * half), int(y1 + 2 * half))


def fit(img: Image.Image) -> Image.Image:
    scale = TILE / max(img.size)
    return img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)


def save_stage_sheet(path: Path, rows: list):
    """rows: list of rows, each a list of (label, image_or_None)."""
    cols = max(len(r) for r in rows)
    sheet = Image.new("RGB", (TILE * cols, (TILE + LABEL_H) * len(rows)), "white")
    draw = ImageDraw.Draw(sheet)
    for r, row in enumerate(rows):
        y0 = r * (TILE + LABEL_H)
        for c, (label, img) in enumerate(row):
            x0 = c * TILE
            if img is None:  # text-only cell
                draw.multiline_text((x0 + 8, y0 + LABEL_H + 8), label, fill="black", spacing=6)
                continue
            tile = fit(img)
            sheet.paste(tile, (x0 + (TILE - tile.width) // 2, y0 + LABEL_H + (TILE - tile.height) // 2))
            draw.text((x0 + 4, y0 + 4), label, fill="black")
    sheet.save(path, quality=90)


def build_sheet(path: Path, stages: dict, ratio_label: str):
    src, crop, lora, out = (stages[k] for k in ("source", "crop", "lora", "output"))
    pasted = stages.get("pasted")  # only saved separately when the workflow has a detail restorer
    out_n = 5 if pasted is not None else 4
    pasted_box = changed_box(src, pasted if pasted is not None else out)
    zoom_box = edit_box(src, pasted if pasted is not None else out) or pasted_box
    zoom = (lambda im: im.crop(zoom_box)) if zoom_box else (lambda im: im)
    src_boxed = src.copy()
    draw = ImageDraw.Draw(src_boxed)
    width = max(2, max(src.size) // 250)
    if pasted_box:
        draw.rectangle(pasted_box, outline=(255, 0, 0), width=width)
    if zoom_box:
        draw.rectangle(zoom_box, outline=(255, 220, 0), width=width)
    rows = [
        [("1 source  red=pasted area  yellow=zoom", src_boxed),
         (f"2 crop {crop.width}x{crop.height}  {ratio_label}", crop),
         ("3 LoRA result on crop", lora), ("1 source (zoom)", zoom(src)),
         (f"{out_n} output (zoom)", zoom(out))],
        [(f"{out_n} output (full frame)", out),
         (f"|{out_n} - 1| x3  total change (full frame)", diff_map(src, out, 3)),
         ("|3 - 2| x3  LoRA change on crop", diff_map(crop, lora, 3)),
         (f"|{out_n} - 1| x3  total change (zoom)", zoom(diff_map(src, out, 3))),
         ("|5 - 4| x8  restorer change (zoom)", zoom(diff_map(pasted, out, 8))) if pasted is not None
         else ("(no detail restorer in this workflow:\n output = paste-back result)", None)],
    ]
    save_stage_sheet(path, rows)


def window(img: Image.Image, cx: float, cy: float) -> Image.Image:
    """TILE x TILE window at 1:1 around (cx, cy), shifted to stay inside the image."""
    x = int(min(max(cx - TILE / 2, 0), max(0, img.width - TILE)))
    y = int(min(max(cy - TILE / 2, 0), max(0, img.height - TILE)))
    return img.crop((x, y, x + TILE, y + TILE))


def build_mode_sheet(path: Path, stages: dict, modes: list):
    src = stages["source"]
    outs = [(m, stages[f"output_{mode_tag(m)}"]) for m in modes]
    crop_box = changed_box(src, stages["output_crop"]) or (0, 0, src.width, src.height)
    # eyes sit ~38% down the crop (the box is extended below the chin for the neck)
    upper = ((crop_box[0] + crop_box[2]) / 2, crop_box[1] + 0.38 * (crop_box[3] - crop_box[1]))
    # a mask mode only differs from the source where the LoRA edited ("crop" differs over the
    # whole crop, strongest maybe at blurred highlights); change-weighted centre, so a thin
    # stray change elsewhere can't pull the window off the chin the way it stretches a bbox
    masked = [m for m in modes if "mask" in m]
    small = (max(1, src.width // 8), max(1, src.height // 8))
    d = np.abs(np.asarray(src.resize(small, Image.BOX), np.float32)
               - np.asarray(stages[f"output_{mode_tag(masked[0] if masked else 'crop')}"].resize(small, Image.BOX),
                            np.float32)).max(axis=2)
    d[d < 0.5 * d.max()] = 0
    if d.sum() > 0:
        ys, xs = np.mgrid[:d.shape[0], :d.shape[1]]
        chin = ((xs * d).sum() / d.sum() * 8 + 4, (ys * d).sum() / d.sum() * 8 + 4)
    else:
        chin = upper
    last = outs[-1]
    rows = []
    for name, (cx, cy) in (("upper face", upper), ("edit", chin)):
        rows.append([(f"source 1:1 ({name})", window(src, cx, cy))]
                    + [(f"{m} 1:1", window(o, cx, cy)) for m, o in outs]
                    + [(f"|{last[0]} - source| x3", window(diff_map(src, last[1], 3), cx, cy))])
    save_stage_sheet(path, rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, nargs="+",
                        help="one or more LoRA paths relative to ComfyUI's models/loras/ dir")
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="defaults to ./output_stages_<input-dir-name>; a _YYYYMMDD_hhmmss suffix is always appended")
    parser.add_argument("--neck-extend-ratio", type=float, default=None,
                        help="override AIDoubleChinFaceRegionCrop's neck_extend_ratio (default: the workflow's value)")
    parser.add_argument("--paste-modes", nargs="+", choices=PASTE_MODES, default=None,
                        help="also save AIDoubleChinPasteBack's result in each of these modes from the same LoRA run "
                             "(ablation; needs a workflow with that node)")
    parser.add_argument("--comfyui-host", default="127.0.0.1")
    parser.add_argument("--comfyui-port", type=int, default=8188, help="matches ComfyUI's --port at launch")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=600, help="seconds to wait per prompt")
    parser.add_argument("--input-title", default="Input Image")
    parser.add_argument("--lora-title", default="Double Chin LoRA Loader")
    parser.add_argument("--face-onnx-dir", default=None,
                        help="dir with the FaceRestorer ONNX models, e.g. "
                             "<ComfyUI>/custom_nodes/ComfyUI-FaceRestorer/core/ONNX; "
                             "defaults to the production path stored in the workflow")
    args = parser.parse_args()
    comfyui_url = f"http://{args.comfyui_host}:{args.comfyui_port}"

    inputs = list_inputs(args.input_dir)
    if not inputs:
        print(f"[error] no images found in {args.input_dir}", file=sys.stderr)
        sys.exit(1)
    template = json.loads(args.workflow.read_text(encoding="utf-8"))
    crop_node = template[comfy_client.find_node_by_class(template, "AIDoubleChinFaceRegionCrop")]
    ratio = args.neck_extend_ratio if args.neck_extend_ratio is not None else crop_node["inputs"]["neck_extend_ratio"]
    ratio_label = f"neck {ratio} / pad {crop_node['inputs']['padding_factor']}"

    run_id = datetime.now(TZ_UTC8).strftime("%Y%m%d_%H%M%S")
    output_base = args.output_dir if args.output_dir is not None else Path(f"output_stages_{args.input_dir.name}")
    output_dir = output_base.parent / f"{output_base.name}_{run_id}"
    print(f"[info] {len(inputs)} image(s) x {len(args.checkpoint)} checkpoint(s), {ratio_label}, output_dir={output_dir}")

    failed = no_face = 0
    with requests.Session() as session:
        problems = preflight(comfyui_url, session, template, args)
        if problems:
            print("[error] the workflow references nodes/models this ComfyUI doesn't have:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            sys.exit(1)

        for i, src in enumerate(inputs, 1):
            try:
                with Image.open(src) as im:
                    img = to_srgb(ImageOps.exif_transpose(im), src.name)
            except Exception as exc:
                print(f"[{i}/{len(inputs)}] {src.name} ... SKIPPED, cannot read: {exc}")
                failed += len(args.checkpoint)
                continue
            for ckpt in args.checkpoint:
                ckpt_name = Path(ckpt).stem
                stem_dir = output_dir / ckpt_name / src.stem
                stem_dir.mkdir(parents=True, exist_ok=True)
                src_path = stem_dir / "1_source.jpg"
                img.save(src_path, "JPEG", quality=JPEG_QUALITY)
                print(f"[{i}/{len(inputs)}] {src.name} @ {ckpt_name} ...", end=" ", flush=True)
                try:
                    stages = run_stages(comfyui_url, session, template, args, src_path, ckpt,
                                        f"double_chin_stages/{run_id}/{ckpt_name}/{src.stem}")
                except Exception as exc:
                    if "ERROR_NO_FACE" in str(exc):
                        no_face += 1
                        print("no face detected (ERROR_NO_FACE)")
                    else:
                        failed += 1
                        print(f"FAILED: {exc}")
                    continue
                # compare against what ComfyUI decoded, not the pre-JPEG image
                with Image.open(src_path) as im:
                    stages["source"] = im.convert("RGB")
                for n, stage in enumerate([s for s in ("crop", "lora", "pasted", "output") if s in stages], 2):
                    stages[stage].save(stem_dir / f"{n}_{stage}.jpg", "JPEG", quality=JPEG_QUALITY)
                for mode in args.paste_modes or []:
                    stages[f"output_{mode_tag(mode)}"].save(stem_dir / f"4_output_{mode_tag(mode)}.jpg", "JPEG",
                                                           quality=JPEG_QUALITY)
                sheet_path = output_dir / ckpt_name / "sheets" / f"{src.stem}.jpg"
                sheet_path.parent.mkdir(parents=True, exist_ok=True)
                build_sheet(sheet_path, stages, ratio_label)
                if args.paste_modes:
                    build_mode_sheet(sheet_path.with_name(f"{src.stem}_modes.jpg"), stages, args.paste_modes)
                print("ok")

    total = len(inputs) * len(args.checkpoint)
    print(f"[done] {total - failed - no_face} ok / {no_face} no face / {failed} failed -> {output_dir}")


if __name__ == "__main__":
    main()
