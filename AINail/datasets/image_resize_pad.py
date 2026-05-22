import argparse
import os
from PIL import Image

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff"}


def resize_long_side(img, target_size):
    w, h = img.size
    scale = target_size / max(w, h)
    return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


def pad_to_square(img, size):
    w, h = img.size
    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    canvas.paste(img, ((size - w) // 2, (size - h) // 2))
    return canvas


def process(src_path, dst_path, size):
    img = Image.open(src_path).convert("RGB")
    img = resize_long_side(img, size)
    img = pad_to_square(img, size)
    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
    img.save(dst_path, "JPEG", quality=95)
    print(f"[OK] {src_path} -> {dst_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir", type=str, help="input folder")
    parser.add_argument("--size", type=int, required=True, help="output size (square side length)")
    parser.add_argument("--output-dir", type=str, required=True, help="output folder")

    args = parser.parse_args()

    if not os.path.isdir(args.input_dir):
        print(f"Not a directory: {args.input_dir}")
        return

    files = sorted(
        f for f in os.listdir(args.input_dir)
        if os.path.splitext(f)[1].lower() in SUPPORTED_EXTS
    )

    if not files:
        print("No supported images found.")
        return

    for fname in files:
        stem, _ = os.path.splitext(fname)
        src = os.path.join(args.input_dir, fname)
        dst = os.path.join(args.output_dir, stem + ".jpg")
        process(src, dst, args.size)


if __name__ == "__main__":
    main()
