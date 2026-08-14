#!/usr/bin/env python3
"""Copy images from nailsalon_cropped_data subfolders into 0_TO_BE_REVIEWED with a suffix per source.

Usage:
    python build_review_set.py
"""

import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SRC_ROOT = BASE_DIR / "nailsalon_cropped_data"
DST_DIR = BASE_DIR / "0_TO_BE_REVIEWED"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

FOLDER_SUFFIXES = {
    "sam3_cropped_control_2": "0_reference",
    "sam3_cropped_control_1": "1_source",
    "sam3_cropped_meitu": "2_meitu",
    "sam3_cropped_gemini": "3_gemini",
    "sam3_cropped_ours": "4_perf",
}


def main():
    DST_DIR.mkdir(parents=True, exist_ok=True)

    for folder_name, suffix in FOLDER_SUFFIXES.items():
        src_dir = SRC_ROOT / folder_name
        if not src_dir.is_dir():
            print(f"[skip] {folder_name}: source folder not found")
            continue

        copied = 0
        skipped = 0
        for src_file in sorted(src_dir.iterdir()):
            if not src_file.is_file() or src_file.suffix.lower() not in IMAGE_EXTS:
                continue

            dst_name = f"{src_file.stem}_{suffix}{src_file.suffix}"
            dst_file = DST_DIR / dst_name

            if dst_file.exists():
                skipped += 1
                continue

            shutil.copy2(src_file, dst_file)
            copied += 1

        print(f"[{folder_name}] copied {copied}, skipped {skipped} (already existed)")


if __name__ == "__main__":
    main()
