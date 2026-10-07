#!/usr/bin/env python3
"""
run_inference.py -- run every image in a folder through one or more
AIDoubleChin checkpoints on a running ComfyUI server. No ground truth
needed, no scoring -- just the generated images.

Each input (any size/aspect, EXIF orientation applied, ICC profile such as
iPhone Display P3 converted to sRGB) is resized + padded to
--size x --size exactly like datasets/prepare_pairs.py does for training, sent
through the workflow, then the padding is cropped off the result and it is
resized back to the input's original resolution.

With --no-pad (for workflows that crop the face themselves, e.g.
workflows/doubleChin_faceCrop_workflow_*_api.json) the sRGB input is sent at its
own size and the workflow's output is saved as-is.

Output dir layout:
    output_<input-dir-name>_YYYYMMDD_hhmmss/
      result/<checkpoint>/<stem>.jpg   generated image at the input's original size
      result/<checkpoint>/square/      raw square model output, before un-padding
      compare/<stem>.jpg               padded input | each checkpoint (square, side by side)
      padded/<stem>.jpg                the padded input actually sent to ComfyUI
                                       (with --no-pad: the sRGB input as sent)

Usage (run inside the env that has requests/Pillow):
    python run_inference.py \\
        --input-dir /data/.../my_photos \\
        --checkpoint aiDoubleChin/double_chin_exp_20261001.safetensors \\
        --comfyui-port 8190
  where each --checkpoint is the path relative to your ComfyUI's own
  models/loras/ dir; pass several to compare them side by side.
"""

import argparse
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image, ImageOps

from run_benchmark import DEFAULT_WORKFLOW, JPEG_QUALITY, TZ_UTC8, preflight, run_one, save_sheet

try:
    from PIL import ImageCms
    SRGB = ImageCms.createProfile("sRGB")
except ImportError:  # Pillow built without littlecms
    ImageCms = None

IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")


def to_srgb(img: Image.Image, name: str = "") -> Image.Image:
    """Converts pixels from the embedded ICC profile (e.g. iPhone Display P3) to sRGB.
    ComfyUI ignores ICC profiles and all outputs are saved untagged (= sRGB), so without
    this a P3 photo's raw values get reinterpreted as sRGB and the result looks
    desaturated/shifted next to the original. Same conversion as datasets/prepare_pairs.py."""
    icc = img.info.get("icc_profile")
    if not icc:
        return img.convert("RGB")
    if ImageCms is None:
        print(f"[warn] {name}: has an ICC profile but this Pillow lacks ImageCms; colors may shift", file=sys.stderr)
        return img.convert("RGB")
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if img.mode not in ("RGB", "CMYK", "L"):
            img = img.convert("RGB")
        return ImageCms.profileToProfile(img, src, SRGB, outputMode="RGB")
    except Exception as exc:
        print(f"[warn] {name}: ICC conversion failed ({exc}); using raw pixel values", file=sys.stderr)
        return img.convert("RGB")


def list_inputs(input_dir: Path) -> list:
    inputs, seen = [], {}
    for p in sorted(input_dir.iterdir()):
        if p.suffix.lower() not in IMG_EXTS:
            continue
        if p.stem in seen:
            print(f"[warn] skipping {p.name}: same stem as {seen[p.stem].name}", file=sys.stderr)
            continue
        seen[p.stem] = p
        inputs.append(p)
    return inputs


def pad_to_square(img: Image.Image, size: int):
    """Same geometry as datasets/prepare_pairs.py; returns the canvas and the
    (left, top, width, height) box the image occupies inside it."""
    w, h = img.size
    scale = size / max(w, h)
    nw, nh = round(w * scale), round(h * scale)
    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    box = ((size - nw) // 2, (size - nh) // 2, nw, nh)
    canvas.paste(img.resize((nw, nh), Image.LANCZOS), box[:2])
    return canvas, box


def restore(raw: bytes, box, original_size, size: int) -> Image.Image:
    """Crops the padding back off the generated image and resizes it to the input's resolution."""
    with Image.open(io.BytesIO(raw)) as im:
        im = im.convert("RGB")
        if im.size != (size, size):  # map the box if the workflow returned a different resolution
            sx, sy = im.width / size, im.height / size
        else:
            sx = sy = 1.0
        left, top, w, h = box
        crop = im.crop((round(left * sx), round(top * sy), round((left + w) * sx), round((top + h) * sy)))
        return crop.resize(original_size, Image.LANCZOS)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, nargs="+",
                        help="one or more LoRA paths relative to ComfyUI's models/loras/ dir, compared side by side")
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="defaults to ./output_<input-dir-name>; a _YYYYMMDD_hhmmss suffix is always appended")
    parser.add_argument("--size", type=int, default=1024, help="square side the input is padded to (training used 1024)")
    parser.add_argument("--no-pad", action="store_true",
                        help="send the input at its own size and keep the output as-is "
                             "(for the face-crop workflow, which crops and pastes back itself)")
    parser.add_argument("--comfyui-host", default="127.0.0.1")
    parser.add_argument("--comfyui-port", type=int, default=8188, help="matches ComfyUI's --port at launch")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=600, help="seconds to wait per prompt")
    parser.add_argument("--input-title", default="Input Image")
    parser.add_argument("--lora-title", default="Double Chin LoRA Loader")
    parser.add_argument("--face-onnx-dir", default=None,
                        help="face-crop workflow only: dir with the FaceRestorer ONNX models, e.g. "
                             "<ComfyUI>/custom_nodes/ComfyUI-FaceRestorer/core/LVOcclusionONNX; "
                             "defaults to the production path stored in the workflow")
    args = parser.parse_args()
    comfyui_url = f"http://{args.comfyui_host}:{args.comfyui_port}"

    inputs = list_inputs(args.input_dir)
    if not inputs:
        print(f"[error] no images ({', '.join(IMG_EXTS)}) found in {args.input_dir}", file=sys.stderr)
        sys.exit(1)

    run_id = datetime.now(TZ_UTC8).strftime("%Y%m%d_%H%M%S")
    output_base = args.output_dir if args.output_dir is not None else Path(f"output_{args.input_dir.name}")
    output_dir = output_base.parent / f"{output_base.name}_{run_id}"
    template = json.loads(args.workflow.read_text(encoding="utf-8"))
    print(f"[info] {len(inputs)} image(s) x {len(args.checkpoint)} checkpoint(s), output_dir={output_dir}")

    failed = 0
    with requests.Session() as session:
        problems = preflight(comfyui_url, session, template, args)
        if problems:
            print("[error] the workflow references models this ComfyUI doesn't have:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            print("Each --checkpoint must be a name relative to ComfyUI's models/loras/ "
                  "(e.g. aiDoubleChin/<file>.safetensors), not a file path.", file=sys.stderr)
            sys.exit(1)
        for sub in ("padded", "compare"):
            (output_dir / sub).mkdir(parents=True, exist_ok=True)

        for i, src in enumerate(inputs, 1):
            try:
                with Image.open(src) as im:
                    img = to_srgb(ImageOps.exif_transpose(im), src.name)
            except Exception as exc:
                print(f"[{i}/{len(inputs)}] {src.name} ... SKIPPED, cannot read: {exc}")
                failed += len(args.checkpoint)
                continue
            if args.no_pad:
                padded, box = img, None
            else:
                padded, box = pad_to_square(img, args.size)
            padded_path = output_dir / "padded" / f"{src.stem}.jpg"
            padded.save(padded_path, "JPEG", quality=JPEG_QUALITY)

            columns = [("input", padded_path)]
            for ckpt in args.checkpoint:
                ckpt_name = Path(ckpt).stem
                print(f"[{i}/{len(inputs)}] {src.name} @ {ckpt_name} ...", end=" ", flush=True)
                try:
                    raw = run_one(comfyui_url, session, template, args, padded_path, ckpt,
                                  f"double_chin_inference/{run_id}/{ckpt_name}/{src.stem}")
                    result_path = output_dir / "result" / ckpt_name / f"{src.stem}.jpg"
                    result_path.parent.mkdir(parents=True, exist_ok=True)
                    if args.no_pad:
                        with Image.open(io.BytesIO(raw)) as out:
                            out.convert("RGB").save(result_path, "JPEG", quality=JPEG_QUALITY)
                        columns.append((ckpt_name, result_path))
                    else:
                        restore(raw, box, img.size, args.size).save(result_path, "JPEG", quality=JPEG_QUALITY)
                        square_path = output_dir / "result" / ckpt_name / "square" / f"{src.stem}.jpg"
                        square_path.parent.mkdir(parents=True, exist_ok=True)
                        with Image.open(io.BytesIO(raw)) as out:
                            out.convert("RGB").save(square_path, "JPEG", quality=JPEG_QUALITY)
                        columns.append((ckpt_name, square_path))
                    print("ok")
                except Exception as exc:
                    failed += 1
                    columns.append((f"{ckpt_name} (failed)", None))
                    print(f"FAILED: {exc}")
            save_sheet(output_dir / "compare" / f"{src.stem}.jpg", columns)

    total = len(inputs) * len(args.checkpoint)
    print(f"[done] {total - failed} ok / {failed} failed -> {output_dir}")


if __name__ == "__main__":
    main()
