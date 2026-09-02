"""
Apply detect_and_crop.py's saved crop_info JSON files to a different directory of images
(matched by basename), producing the same square crop that detect_and_crop.py originally
computed for its own control/target images -- so a folder that wasn't part of the original
run can still get an equivalent, pixel-aligned crop.
"""
import argparse
import json
import os
from pathlib import Path

from PIL import Image

from crop_utils import apply_crop

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def list_images(directory):
    result = {}
    for fname in sorted(os.listdir(directory)):
        _, ext = os.path.splitext(fname)
        if ext.lower() in IMAGE_EXTENSIONS:
            result[os.path.splitext(fname)[0]] = os.path.join(directory, fname)
    return result


def list_crop_info_files(directory):
    result = {}
    for fname in sorted(os.listdir(directory)):
        if fname.lower().endswith(".json"):
            result[os.path.splitext(fname)[0]] = os.path.join(directory, fname)
    return result


def find_matching_basenames(input_dir, crop_info_dir):
    images = list_images(input_dir)
    crop_info_files = list_crop_info_files(crop_info_dir)
    common = sorted(set(images) & set(crop_info_files))
    return [(images[b], crop_info_files[b], b) for b in common]


def main():
    parser = argparse.ArgumentParser(
        description="Apply detect_and_crop.py's saved crop_info JSON files to another directory of images (matched by basename)")
    parser.add_argument("--input-dir", required=True, help="Directory of images to crop, filenames matching the crop_info JSON basenames")
    parser.add_argument("--crop-info-dir", required=True, help="detect_and_crop.py's --output-crop-info dir")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--limit", type=int, default=-1, help="Only process the first N matching images (for quick testing)")
    args = parser.parse_args()

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    pairs = find_matching_basenames(args.input_dir, args.crop_info_dir)
    if not pairs:
        print(f"No matching basenames found across '{args.input_dir}', '{args.crop_info_dir}'")
        return
    print(f"Found {len(pairs)} matching images")
    if args.limit > 0 and len(pairs) > args.limit:
        pairs = pairs[:args.limit]
        print(f"Limiting to first {args.limit} images for this run")

    n_cropped = 0
    for image_path, crop_info_path, basename in pairs:
        with open(crop_info_path) as f:
            data = json.load(f)

        img = Image.open(image_path).convert("RGB")
        cropped = apply_crop(img, data["crop_info"], data["output_size"])

        ext = os.path.splitext(image_path)[1] or ".jpg"
        cropped.save(os.path.join(args.output_dir, f"{basename}{ext}"), quality=95)
        n_cropped += 1

    print(f"\nDone. {n_cropped}/{len(pairs)} images cropped.")


if __name__ == "__main__":
    main()
