import argparse
import os
from PIL import Image


def find_candidates(root_dir):
    for dirpath, _, filenames in os.walk(root_dir):
        for fname in filenames:
            stem, ext = os.path.splitext(fname)
            if ext.lower() == ".jpg" or ext.lower() == ".jpeg":
                yield os.path.join(dirpath, fname), stem, ext


def check(path):
    with Image.open(path) as img:
        real_format = img.format
    ext = os.path.splitext(path)[1]
    issues = []
    if real_format != "JPEG":
        issues.append(f"content is {real_format}, not JPEG")
    if ext != ".jpg":
        issues.append(f"extension is {ext!r}, not '.jpg'")
    return issues


def fix(path, stem):
    dirpath = os.path.dirname(path)
    dst = os.path.join(dirpath, stem + ".jpg")
    tmp = dst + ".tmp"
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.save(tmp, "JPEG", quality=95)
    # On case-insensitive filesystems (e.g. macOS default), "x.jpg" and "X.JPG"
    # refer to the same file, so renaming case-only via a direct save+remove
    # would delete the file we just wrote. Route through a distinctly-named
    # temp file and only remove the source after it's safely persisted.
    if os.path.abspath(tmp) != os.path.abspath(path):
        os.remove(path)
    os.replace(tmp, dst)
    return dst


def main():
    parser = argparse.ArgumentParser(
        description="Verify every *.jpg/*.jpeg file under a directory is a real "
        "JPEG with a lowercase .jpg extension; optionally fix violations."
    )
    parser.add_argument("input_dir", type=str, help="directory to scan recursively")
    parser.add_argument("--fix", action="store_true", help="re-encode/rename violations in place")

    args = parser.parse_args()

    if not os.path.isdir(args.input_dir):
        print(f"Not a directory: {args.input_dir}")
        return

    violations = 0
    for path, stem, ext in find_candidates(args.input_dir):
        issues = check(path)
        if not issues:
            continue
        violations += 1
        print(f"[BAD] {path}: {', '.join(issues)}")
        if args.fix:
            dst = fix(path, stem)
            print(f"  -> fixed: {dst}")

    if violations == 0:
        print("All files are valid .jpg JPEGs.")
    else:
        print(f"{violations} violation(s) found" + (" and fixed." if args.fix else "."))


if __name__ == "__main__":
    main()
