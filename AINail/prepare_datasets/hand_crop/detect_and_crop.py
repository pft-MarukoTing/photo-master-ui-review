"""
Step 1-3: detect hands in nailsalon_target, turn each detection into a square crop
region, and apply that same region to both control_1 and target (same basename in
both dirs -> same filename in both output dirs). control_2 (reference) isn't cropped by
the detected box -- it's padded to its own square (centered, black borders) and resized
the same way, then saved under the same per-crop filename so training pair filenames
line up.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from PIL import Image

from crop_utils import compute_crop_info, apply_crop, pad_to_square

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sam3_common"))
from sam3_detector import load_model, detect

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def list_images(directory):
    result = {}
    for fname in sorted(os.listdir(directory)):
        _, ext = os.path.splitext(fname)
        if ext.lower() in IMAGE_EXTENSIONS:
            result[fname.lower()] = os.path.join(directory, fname)
    return result


def find_matching_basenames(control_1_dir, control_2_dir, target_dir):
    images_control_1 = list_images(control_1_dir)
    images_control_2 = list_images(control_2_dir)
    images_target = list_images(target_dir)
    common = sorted(set(images_control_1.keys()) & set(images_control_2.keys()) & set(images_target.keys()))
    return [(images_control_1[f], images_control_2[f], images_target[f], os.path.splitext(f)[0]) for f in common]


def main():
    parser = argparse.ArgumentParser(description="Detect hands in target images and sync-crop control_1 + target")
    parser.add_argument("--control-1-dir", required=True)
    parser.add_argument("--control-2-dir", required=True, help="Reference dir -- padded to square (not box-cropped) and saved per crop index so training pair filenames line up")
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--output-control-1", required=True)
    parser.add_argument("--output-control-2", required=True)
    parser.add_argument("--output-target", required=True)
    parser.add_argument("--output-crop-info", required=True, help="Dir for per-crop crop_info JSON files, used later to restore black padding after inpainting overwrites it (see restore_padding.py)")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--bpe-path", default=None)
    parser.add_argument("--text-prompt", default="hand")
    parser.add_argument("--confidence-threshold", type=float, default=0.2)
    parser.add_argument("--max-detections", type=int, default=2)
    parser.add_argument("--scale", type=float, default=1.2)
    parser.add_argument("--min-area-ratio", type=float, default=0.005)
    parser.add_argument("--avoid-bbox-overlap", action="store_true")
    parser.add_argument("--no-merge-overlapping-squares", action="store_true")
    parser.add_argument("--no-shift-to-avoid-padding", action="store_true")
    parser.add_argument("--limit", type=int, default=-1, help="Only process the first N matching pairs (for quick testing)")
    parser.add_argument("--output-size", type=int, default=1024, help="Resize each square crop to this size (0 to keep native crop size)")
    args = parser.parse_args()

    Path(args.output_control_1).mkdir(parents=True, exist_ok=True)
    Path(args.output_control_2).mkdir(parents=True, exist_ok=True)
    Path(args.output_target).mkdir(parents=True, exist_ok=True)
    Path(args.output_crop_info).mkdir(parents=True, exist_ok=True)

    pairs = find_matching_basenames(args.control_1_dir, args.control_2_dir, args.target_dir)
    if not pairs:
        print(f"No matching filenames found across '{args.control_1_dir}', '{args.control_2_dir}', '{args.target_dir}'")
        return
    print(f"Found {len(pairs)} matching pairs")
    if args.limit > 0 and len(pairs) > args.limit:
        pairs = pairs[:args.limit]
        print(f"Limiting to first {args.limit} pairs for this run")

    print("Loading SAM3 model...")
    _, processor = load_model(args.checkpoint, bpe_path=args.bpe_path)

    n_skipped = 0
    n_crops = 0
    for control_1_path, control_2_path, target_path, basename in pairs:
        target_img = Image.open(target_path).convert("RGB")
        boxes = detect(
            processor, target_img,
            confidence_threshold=args.confidence_threshold,
            text_prompt=args.text_prompt,
            max_detections=args.max_detections,
        )
        if not boxes:
            print(f"{basename}: no hand detected, skipping")
            n_skipped += 1
            continue

        crop_info = compute_crop_info(
            boxes, target_img.width, target_img.height,
            scale=args.scale,
            min_area_ratio=args.min_area_ratio,
            avoid_bbox_overlap=args.avoid_bbox_overlap,
            merge_overlapping_squares=not args.no_merge_overlapping_squares,
            shift_to_avoid_padding=not args.no_shift_to_avoid_padding,
        )
        if not crop_info:
            print(f"{basename}: all detections filtered out by min-area-ratio, skipping")
            n_skipped += 1
            continue

        control_1_img = Image.open(control_1_path).convert("RGB")
        control_2_img = Image.open(control_2_path).convert("RGB")

        output_size = args.output_size if args.output_size > 0 else None
        control_2_padded = pad_to_square(control_2_img, output_size)
        for i, entry in enumerate(crop_info):
            apply_crop(target_img, entry, output_size).save(os.path.join(args.output_target, f"{basename}_{i}.jpg"), quality=95)
            apply_crop(control_1_img, entry, output_size).save(os.path.join(args.output_control_1, f"{basename}_{i}.jpg"), quality=95)
            control_2_padded.save(os.path.join(args.output_control_2, f"{basename}_{i}.jpg"), quality=95)

            with open(os.path.join(args.output_crop_info, f"{basename}_{i}.json"), "w") as f:
                json.dump({"crop_info": entry, "output_size": output_size}, f)

            n_crops += 1

        print(f"{basename}: {len(crop_info)} crop(s)")

    print(f"\nDone. {len(pairs) - n_skipped}/{len(pairs)} pairs processed, {n_skipped} skipped (no hand), {n_crops} crops written.")


if __name__ == "__main__":
    main()
