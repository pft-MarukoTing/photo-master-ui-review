"""
Copy tagged masks from one stem to a range of stems.

Usage:
  python copy_masks.py --src 00011 --dst 00010-00019
"""

import argparse
import shutil
from pathlib import Path

MASKS_DIR = Path(__file__).parent / "tagged_masks"


def parse_dst(dst: str) -> list[str]:
    if "-" in dst:
        start, end = dst.split("-", 1)
        width = len(start)
        return [str(i).zfill(width) for i in range(int(start), int(end) + 1)]
    return [dst]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src",  required=True, help="source stem, e.g. 00011")
    parser.add_argument("--dst",  required=True, help="destination stem or range, e.g. 00010-00019")
    parser.add_argument("--masks-dir", type=Path, default=MASKS_DIR)
    args = parser.parse_args()

    targets = [t for t in parse_dst(args.dst) if t != args.src]

    copied = 0
    for stem in targets:
        for i in range(5):
            src_path = args.masks_dir / f"{args.src}_mask_{i}.png"
            dst_path = args.masks_dir / f"{stem}_mask_{i}.png"
            if not src_path.exists():
                print(f"  [warn] {src_path.name} not found")
                continue
            shutil.copy2(src_path, dst_path)
            copied += 1

    print(f"\nDone: {copied} files copied -> {args.masks_dir}")


if __name__ == "__main__":
    main()
