"""
Segment nails in every image of an input directory using SAM3 grounding detection, and
write one merged binary mask PNG (all detected nail instances OR'd together, same size as
the source image) per image to an output directory.
"""
import argparse
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sam3_common"))
from sam3_detector import load_model, detect

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def list_images(directory):
    return sorted(
        os.path.join(directory, fname)
        for fname in os.listdir(directory)
        if os.path.splitext(fname)[1].lower() in IMAGE_EXTENSIONS
    )


def merge_masks(masks, height, width):
    merged = np.zeros((height, width), dtype=bool)
    for mask in masks:
        merged |= mask
    return merged


def main():
    parser = argparse.ArgumentParser(description="Segment nails and write merged binary mask PNGs")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--bpe-path", default=None)
    parser.add_argument("--text-prompt", default="nail")
    parser.add_argument("--confidence-threshold", type=float, default=0.2)
    parser.add_argument("--max-detections", type=int, default=10)
    parser.add_argument("--limit", type=int, default=-1, help="Only process the first N images (for quick testing)")
    args = parser.parse_args()

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    image_paths = list_images(args.input_dir)
    if not image_paths:
        print(f"No images found in '{args.input_dir}'")
        return
    print(f"Found {len(image_paths)} image(s)")
    if args.limit > 0 and len(image_paths) > args.limit:
        image_paths = image_paths[:args.limit]
        print(f"Limiting to first {args.limit} image(s) for this run")

    print("Loading SAM3 model...")
    _, processor = load_model(args.checkpoint, bpe_path=args.bpe_path)

    n_empty = 0
    for image_path in image_paths:
        basename = os.path.splitext(os.path.basename(image_path))[0]
        pil_image = Image.open(image_path).convert("RGB")
        _, masks = detect(
            processor, pil_image,
            confidence_threshold=args.confidence_threshold,
            text_prompt=args.text_prompt,
            max_detections=args.max_detections,
            return_masks=True,
        )

        if masks:
            merged = merge_masks(masks, pil_image.height, pil_image.width)
            print(f"{basename}: {len(masks)} nail(s) detected")
        else:
            merged = np.zeros((pil_image.height, pil_image.width), dtype=bool)
            n_empty += 1
            print(f"{basename}: no nail detected, writing empty mask")

        mask_image = Image.fromarray((merged * 255).astype(np.uint8), mode="L")
        mask_image.save(os.path.join(args.output_dir, f"{basename}.png"))

    print(f"\nDone. {len(image_paths) - n_empty}/{len(image_paths)} image(s) had at least one nail detected, "
          f"{n_empty} written as empty masks.")


if __name__ == "__main__":
    main()
