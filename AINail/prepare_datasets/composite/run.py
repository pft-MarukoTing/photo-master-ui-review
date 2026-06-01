"""
Composite nail regions from nailsalon_target onto nailsalon_control_1
using tagged_masks as the blend mask.

For each (stem, finger) pair that has a tagged mask:
  - Alpha-blend that single finger's mask: result = control * (1 - mask) + target * mask
  - Save to output/{stem}_finger_{i}.jpg
"""

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE        = Path(__file__).parent
TAGGING_DIR = HERE.parent.parent / "tagging_tool"

DEFAULT_CONTROL = TAGGING_DIR / "data" / "nailsalon_control_1"
DEFAULT_TARGET  = TAGGING_DIR / "data" / "nailsalon_target"
DEFAULT_MASKS   = TAGGING_DIR / "tagged_masks"
DEFAULT_OUTPUT  = HERE / "output"


def load_rgb(path: Path) -> np.ndarray:
    return np.array(Image.open(path).convert("RGB"), dtype=np.float32)


def load_mask(path: Path) -> np.ndarray:
    """Return float32 mask in [0, 1] from grayscale-encoded PNG."""
    arr = np.array(Image.open(path).convert("RGB"), dtype=np.float32)
    return arr[:, :, 0] / 255.0  # R=G=B = original brush alpha; take any channel


def composite_one(stem: str, finger: int, ctrl_path: Path, tgt_path: Path,
                  mask_path: Path, output_dir: Path) -> bool:
    ctrl = load_rgb(ctrl_path)
    tgt  = load_rgb(tgt_path)
    mask = load_mask(mask_path)

    if ctrl.shape != tgt.shape:
        print(f"  [warn] size mismatch for {stem}: control={ctrl.shape} target={tgt.shape}")
        tgt = np.array(
            Image.open(tgt_path).convert("RGB").resize(
                (ctrl.shape[1], ctrl.shape[0]), Image.LANCZOS
            ),
            dtype=np.float32,
        )

    if mask.shape != ctrl.shape[:2]:
        mask = np.array(
            Image.fromarray((mask * 255).astype(np.uint8)).resize(
                (ctrl.shape[1], ctrl.shape[0]), Image.LANCZOS
            ),
            dtype=np.float32,
        ) / 255.0

    # >128 → fully use target; ≤128 → proportional blend
    alpha  = np.where(mask > 0.5, 1.0, mask / 0.5)[:, :, np.newaxis]
    result = (ctrl * (1 - alpha) + tgt * alpha).clip(0, 255).astype(np.uint8)

    out_path = output_dir / f"{stem}_finger_{finger}.jpg"
    Image.fromarray(result).save(out_path, quality=95)
    print(f"  [ok] {stem} finger {finger} -> {out_path.name}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path, default=DEFAULT_CONTROL)
    parser.add_argument("--target",  type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--masks",   type=Path, default=DEFAULT_MASKS)
    parser.add_argument("--output",  type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    # collect all (stem, finger) pairs
    mask_files = sorted(args.masks.glob("*_mask_*.png"))
    if not mask_files:
        print("No tagged masks found.")
        sys.exit(1)

    total = ok = 0
    for mask_path in mask_files:
        parts = mask_path.stem.rsplit("_mask_", 1)
        if len(parts) != 2:
            continue
        stem, finger_str = parts
        try:
            finger = int(finger_str)
        except ValueError:
            continue

        ctrl_path = None
        for ext in (".jpg", ".jpeg", ".png"):
            p = args.control / f"{stem}{ext}"
            if p.exists():
                ctrl_path = p
                break

        tgt_path = None
        for ext in (".jpg", ".jpeg", ".png"):
            p = args.target / f"{stem}{ext}"
            if p.exists():
                tgt_path = p
                break

        total += 1
        if ctrl_path is None:
            print(f"  [skip] no control image for {stem}")
            continue
        if tgt_path is None:
            print(f"  [skip] no target image for {stem}")
            continue

        ok += composite_one(stem, finger, ctrl_path, tgt_path, mask_path, args.output)

    print(f"\nDone: {ok}/{total} composited -> {args.output}")


if __name__ == "__main__":
    main()
