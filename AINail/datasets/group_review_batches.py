#!/usr/bin/env python3
"""Group images in 0_TO_BE_REVIEWED into one numbered subfolder per sample.

A "sample" is a group of files sharing the same original id, ignoring the
suffix added by build_review_set.py (e.g. 02090_0_0_reference.jpg,
02090_0_1_source.jpg, ... all belong to sample "02090_0").

Usage:
    python group_review_batches.py <date_folder>

Example:
    python group_review_batches.py 20260814
"""

import argparse
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "0_TO_BE_REVIEWED"

SUFFIXES = ["0_reference", "1_source", "2_meitu", "3_gemini", "4_perf"]
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def sample_key(filename: str) -> str:
    stem = Path(filename).stem
    for suffix in SUFFIXES:
        marker = f"_{suffix}"
        if stem.endswith(marker):
            return stem[: -len(marker)]
    raise ValueError(f"Unrecognized filename (no known suffix): {filename}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("date_folder", help="Subfolder under 0_TO_BE_REVIEWED to place grouped samples into, e.g. 20260814")
    args = parser.parse_args()

    dst_root = SRC_DIR / args.date_folder
    dst_root.mkdir(parents=True, exist_ok=True)

    files = [
        f for f in SRC_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTS
    ]

    samples = {}
    for f in files:
        key = sample_key(f.name)
        samples.setdefault(key, []).append(f)

    sorted_keys = sorted(samples.keys())

    moved = 0
    for i, key in enumerate(sorted_keys):
        target_dir = dst_root / str(i + 1)
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in samples[key]:
            shutil.move(str(f), str(target_dir / f.name))
            moved += 1

    print(f"Grouped {len(sorted_keys)} samples ({moved} files) into "
          f"{dst_root} across {len(sorted_keys)} subfolders")


if __name__ == "__main__":
    main()
