import os
import shutil

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
PREVIEW_PATH = os.path.join(SCRIPT_DIR, "preview.jpg")

CATEGORIES = {
    "1": "singleHand_bareNail",
    "2": "singleHand_nailPolish",
    "3": "bothHands_bareNail",
    "4": "bothHands_nailPolish",
    "i": "ignore",
}


def get_pending() -> list:
    return sorted(
        f for f in os.listdir(OUTPUT_DIR)
        if f.endswith(".jpg") and os.path.isfile(os.path.join(OUTPUT_DIR, f))
    )


def ensure_category_dirs():
    for name in CATEGORIES.values():
        os.makedirs(os.path.join(OUTPUT_DIR, name), exist_ok=True)


def get_next_filename(category_dir, original_filename):
    """
    Get the next available filename to avoid overwriting existing files
    Format: 000001.jpg (6-digit, starting from max existing index + 1)
    """
    if not os.path.exists(category_dir):
        return "000001.jpg"

    existing_files = [
        f for f in os.listdir(category_dir)
        if f.lower().endswith(".jpg")
    ]

    if not existing_files:
        return "000001.jpg"

    max_index = 0

    for filename in existing_files:
        try:
            # Assume filename format is "000001.jpg"
            index = int(filename.split(".")[0])
            max_index = max(max_index, index)
        except ValueError:
            # Skip non-numeric filenames
            continue

    # 6-digit zero padding
    return f"{max_index + 1:06d}.jpg"


def main():
    ensure_category_dirs()
    pending = get_pending()

    if not pending:
        print("No images to annotate in output/.")
        return

    total = len(pending)
    print(f"{total} images to annotate.")

    for idx, fname in enumerate(pending):
        src = os.path.join(OUTPUT_DIR, fname)
        shutil.copy2(src, PREVIEW_PATH)

        print(f"\n[{idx + 1}/{total}] {fname}")
        print("  1=singleHand_bareNail  2=singleHand_nailPolish")
        print("  3=bothHands_bareNail   4=bothHands_nailPolish")
        print("  i=ignore  s=skip  q=quit")

        while True:
            choice = input("  > ").strip().lower()
            if choice == "q":
                print("Quit.")
                return
            elif choice == "s":
                break
            elif choice in CATEGORIES:
                category_dir = os.path.join(OUTPUT_DIR, CATEGORIES[choice])
                new_filename = get_next_filename(category_dir, fname)
                dest = os.path.join(category_dir, new_filename)
                shutil.move(src, dest)
                print(f"  Saved as {new_filename}")
                break
            else:
                print("  Invalid. Use 1/2/3/4/i/s/q.")

    print("\nAll images annotated.")


if __name__ == "__main__":
    main()
