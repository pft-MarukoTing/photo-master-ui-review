import argparse
from pathlib import Path

import cv2

from hand_keypoints import HandKeypointDetector

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}

DEFAULT_MIN_DETECTION_CONFIDENCE = 0.3
DEFAULT_SCALE = 0.55
DEFAULT_FLIP = True
DEFAULT_COLOR_MODE = "finger"


def process_folder(input_dir: Path, output_dir: Path, min_detection_confidence: float = DEFAULT_MIN_DETECTION_CONFIDENCE) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    images = sorted(p for p in input_dir.iterdir() if p.suffix in IMAGE_EXTENSIONS)
    if not images:
        print(f"No images found in {input_dir}")
        return

    with HandKeypointDetector(min_detection_confidence=min_detection_confidence) as detector:
        for img_path in images:
            image = cv2.imread(str(img_path))
            if image is None:
                print(f"Skip (unreadable): {img_path.name}")
                continue

            hands_data = detector.detect(image, flip_handedness=DEFAULT_FLIP)
            if hands_data:
                detector.draw_fingertip_boxes(image, hands_data, scale=DEFAULT_SCALE, color_mode=DEFAULT_COLOR_MODE)
                print(f"{img_path.name}: {len(hands_data)} hand(s) {[l for _, l in hands_data]}")
            else:
                print(f"{img_path.name}: no hands detected")

            cv2.imwrite(str(output_dir / img_path.name), image)


def main():
    parser = argparse.ArgumentParser(description="Batch fingertip box detection on a folder of images")
    parser.add_argument("input", help="Input folder")
    parser.add_argument("--output", "-o", help="Output folder (default: <input>/processed)")
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output) if args.output else input_dir / "processed"

    process_folder(input_dir, output_dir)
    print(f"Done. Results saved to {output_dir}")


if __name__ == "__main__":
    main()
