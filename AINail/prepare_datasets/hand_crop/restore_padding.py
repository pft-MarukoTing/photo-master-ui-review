"""
Post-inpainting step: restore the black padding that detect_and_crop.py's crops may have
around their edges (when a crop's square region fell partly outside the source image).
Inpainting run elsewhere on those crops can paint over that padding along with everything
else; this reads back the crop_info JSON detect_and_crop.py wrote for each crop and pastes
the pre-inpaint pixels back over everything outside the real content area, so the only
pixels that differ from the pre-inpaint crop are ones inpainting was actually meant to
change.
"""
import argparse
import json
import os
from pathlib import Path

from PIL import Image

from crop_utils import restore_padding

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


def find_matching_basenames(original_dir, inpainted_dir, crop_info_dir):
    images_original = list_images(original_dir)
    images_inpainted = list_images(inpainted_dir)
    crop_info_files = list_crop_info_files(crop_info_dir)
    common = sorted(set(images_original) & set(images_inpainted) & set(crop_info_files))
    return [(images_original[b], images_inpainted[b], crop_info_files[b], b) for b in common]


def main():
    parser = argparse.ArgumentParser(
        description="Restore pre-inpaint black padding on inpainted crops, using detect_and_crop.py's crop_info JSON files")
    parser.add_argument("--original-dir", required=True, help="detect_and_crop.py's crop output dir (pre-inpaint), e.g. its --output-target")
    parser.add_argument("--inpainted-dir", required=True, help="Same crops after inpainting, same basenames as --original-dir")
    parser.add_argument("--crop-info-dir", required=True, help="detect_and_crop.py's --output-crop-info dir")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--limit", type=int, default=-1, help="Only process the first N matching crops (for quick testing)")
    args = parser.parse_args()

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    triples = find_matching_basenames(args.original_dir, args.inpainted_dir, args.crop_info_dir)
    if not triples:
        print(f"No matching basenames found across '{args.original_dir}', '{args.inpainted_dir}', '{args.crop_info_dir}'")
        return
    print(f"Found {len(triples)} matching crops")
    if args.limit > 0 and len(triples) > args.limit:
        triples = triples[:args.limit]
        print(f"Limiting to first {args.limit} crops for this run")

    n_restored = 0
    for original_path, inpainted_path, crop_info_path, basename in triples:
        with open(crop_info_path) as f:
            data = json.load(f)

        original_img = Image.open(original_path).convert("RGB")
        inpainted_img = Image.open(inpainted_path).convert("RGB")

        restored = restore_padding(original_img, inpainted_img, data["crop_info"], data["output_size"])
        ext = os.path.splitext(inpainted_path)[1] or ".jpg"
        restored.save(os.path.join(args.output_dir, f"{basename}{ext}"), quality=95)
        n_restored += 1

    print(f"\nDone. {n_restored}/{len(triples)} crops restored.")


if __name__ == "__main__":
    main()
