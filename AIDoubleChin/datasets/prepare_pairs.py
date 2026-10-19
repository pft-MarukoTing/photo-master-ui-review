import argparse
import io
import os
from PIL import Image, ImageOps

try:
    from PIL import ImageCms
    SRGB = ImageCms.createProfile("sRGB")
except ImportError:  # Pillow built without littlecms
    ImageCms = None

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()  # lets Image.open read iPhone .heic/.heif
except ImportError:
    register_heif_opener = None

HEIF_EXTS = {".heic", ".heif"}
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff"} | HEIF_EXTS


def list_images(folder):
    return {
        os.path.splitext(f)[0]: os.path.join(folder, f)
        for f in os.listdir(folder)
        if os.path.splitext(f)[1].lower() in SUPPORTED_EXTS
    }


def to_srgb(img, name=""):
    """Converts pixels from the embedded ICC profile (e.g. iPhone Display P3) to sRGB.
    Outputs are saved without a profile, which viewers and the model treat as sRGB,
    so skipping this makes P3 photos look desaturated/shifted."""
    icc = img.info.get("icc_profile")
    if not icc:
        return img.convert("RGB")
    if ImageCms is None:
        print(f"[WARN] {name}: has an ICC profile but this Pillow lacks ImageCms; colors may shift")
        return img.convert("RGB")
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if img.mode not in ("RGB", "CMYK", "L"):
            img = img.convert("RGB")
        return ImageCms.profileToProfile(img, src, SRGB, outputMode="RGB")
    except Exception as exc:
        print(f"[WARN] {name}: ICC conversion failed ({exc}); using raw pixel values")
        return img.convert("RGB")


def load(path):
    # Apply EXIF orientation so phone photos and their edited exports line up.
    return to_srgb(ImageOps.exif_transpose(Image.open(path)), path)


def fit_size(img, size):
    w, h = img.size
    scale = size / max(w, h)
    return round(w * scale), round(h * scale)


def resize_pad(img, size, fit):
    nw, nh = fit
    img = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    canvas.paste(img, ((size - nw) // 2, (size - nh) // 2))
    return canvas


def main():
    parser = argparse.ArgumentParser(
        description="Resize+pad before/after pairs to a square for ai-toolkit Qwen-Image-Edit training. "
        "Before images (with double chin) go to <output-dir>/<split>/control, after images (removed) go "
        "to <output-dir>/<split>/target, both saved as <stem>.jpg so ai-toolkit can match them by filename. "
        "<split> is 'test' for stems listed in --test-list and 'train' for everything else."
    )
    parser.add_argument("before_dir", type=str, help="folder of original images (with double chin)")
    parser.add_argument("after_dir", type=str, help="folder of retouched images (double chin removed)")
    parser.add_argument("--output-dir", type=str, required=True, help="output folder")
    parser.add_argument("--size", type=int, default=1024, help="output size (square side length)")
    parser.add_argument("--test-list", type=str, default=None,
                        help="text file of held-out stems (one per line), excluded from training")

    args = parser.parse_args()

    for d in (args.before_dir, args.after_dir):
        if not os.path.isdir(d):
            print(f"Not a directory: {d}")
            return

    before = list_images(args.before_dir)
    after = list_images(args.after_dir)
    if register_heif_opener is None and any(os.path.splitext(p)[1].lower() in HEIF_EXTS
                                            for p in list(before.values()) + list(after.values())):
        print("HEIC/HEIF inputs need pillow-heif: pip install pillow-heif")
        return

    for stem in sorted(before.keys() - after.keys()):
        print(f"[MISS] no after image for {before[stem]}")
    for stem in sorted(after.keys() - before.keys()):
        print(f"[MISS] no before image for {after[stem]}")

    test_stems = set()
    if args.test_list:
        with open(args.test_list) as f:
            test_stems = {line.strip() for line in f if line.strip()}
        for stem in sorted(test_stems - (before.keys() & after.keys())):
            print(f"[MISS] test stem {stem} has no complete pair")

    for split in ("train", "test"):
        for side in ("control", "target"):
            os.makedirs(os.path.join(args.output_dir, split, side), exist_ok=True)

    written = {"train": 0, "test": 0}
    skipped = 0
    for stem in sorted(before.keys() & after.keys()):
        src_img = load(before[stem])
        dst_img = load(after[stem])
        # Both sides must land on the same resized geometry, otherwise padding offsets
        # differ and the LoRA learns a spatial shift instead of the edit. Proportionally
        # rescaled exports (e.g. 800x1005 vs 815x1024) still pass; the 1px tolerance
        # absorbs the exporter's rounding, and both sides use the before image's fit.
        fit = fit_size(src_img, args.size)
        dst_fit = fit_size(dst_img, args.size)
        if abs(fit[0] - dst_fit[0]) > 1 or abs(fit[1] - dst_fit[1]) > 1:
            print(f"[SKIP] {stem}: size mismatch before={src_img.size} after={dst_img.size}")
            skipped += 1
            continue
        split = "test" if stem in test_stems else "train"
        split_dir = os.path.join(args.output_dir, split)
        resize_pad(src_img, args.size, fit).save(os.path.join(split_dir, "control", stem + ".jpg"), "JPEG", quality=95)
        resize_pad(dst_img, args.size, fit).save(os.path.join(split_dir, "target", stem + ".jpg"), "JPEG", quality=95)
        print(f"[OK] {split}/{stem}")
        written[split] += 1

    print(f"{written['train']} train + {written['test']} test pair(s) written, {skipped} skipped, "
          f"{len(before.keys() ^ after.keys())} unpaired.")


if __name__ == "__main__":
    main()
