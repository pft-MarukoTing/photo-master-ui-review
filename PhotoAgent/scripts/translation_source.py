"""Keep i18n/translation-source.json in step with the English catalogs.

The file is what a translation tool reads to make a new language. It lists
every Chinese key of the three catalogs with the English shown on the page
(`en`, filled in from the catalogs) and where it is used. Some entries also
have hand-written fields, kept as they are on every sync:
  en_full  the full meaning in English, where `en` was shortened to fit the
           layout or reads differently out of context. A tool should
           translate `en_full` when it is there, `en` otherwise.
  note     what the text means and where it sits.
  max      how much room it has on the page.

  python scripts/translation_source.py          # refresh the file
  python scripts/translation_source.py --check  # exit 1 if it is out of step

Run from PhotoAgent/. Needs node for the two JavaScript catalogs.
"""

import argparse
import collections
import importlib.util
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "i18n" / "translation-source.json"
HAND = ("en_full", "note", "max")

ABOUT = [
    "Source for translating PhotoAgent into another language. Keys are the Chinese the code uses; a new catalog is keyed the same way.",
    "Translate `en_full` when an entry has it, `en` otherwise. `en_full` and `note` mark English that was shortened for the layout, or that stands for several Chinese texts with different meanings.",
    "`where`: panel = frontend/i18n-en.js, chat = frontend-chat/src/i18n/en.ts, backend = photo_agent/i18n_en.py (labels = LABELS, text = TEXT). When the catalogs disagree, `en` holds each one's English.",
    "A key ending in @@something is the same Chinese used for a different meaning; the part after @@ says which and is not shown.",
    "Keep {placeholders}, quotes and leading/trailing spaces as they are: the page joins pieces.",
    "Not in this file and written by hand for each language: FORMAT and PATTERNS in photo_agent/i18n_en.py (punctuation, and the regular expressions that read the AI's replies).",
    "Refresh with `python scripts/translation_source.py`; hand-written fields are kept.",
]

JS_LOADER = r"""
const fs = require("fs");
const read = (p) => fs.readFileSync(p, "utf8");
const window = {};
new Function("window", read(process.argv[1]))(window);
const ts = read(process.argv[2]).replace(/export const EN[^=]*=/, "return");
process.stdout.write(JSON.stringify({panel: window.PHOTOAGENT_CATALOGS.en, chat: new Function(ts)()}));
"""


def load_catalogs():
    js = subprocess.run(
        ["node", "-e", JS_LOADER, str(ROOT / "frontend/i18n-en.js"), str(ROOT / "frontend-chat/src/i18n/en.ts")],
        capture_output=True, check=True)
    found = json.loads(js.stdout.decode("utf-8"))
    spec = importlib.util.spec_from_file_location("i18n_en", ROOT / "photo_agent/i18n_en.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    found["backend labels"] = module.LABELS
    found["backend text"] = module.TEXT
    return found


def build(old):
    seen = collections.defaultdict(dict)
    for where, catalog in load_catalogs().items():
        for zh, en in catalog.items():
            seen[zh][where] = en
    entries = {}
    for zh in sorted(seen):
        per = seen[zh]
        english = set(per.values())
        entry = {"en": english.pop() if len(english) == 1 else per, "where": list(per)}
        for field in HAND:
            if field in old.get(zh, {}):
                entry[field] = old[zh][field]
        entries[zh] = entry
    return entries


def shared(entries):
    """English that stands for several Chinese keys and has no note on any of them."""
    by_en = collections.defaultdict(list)
    for zh, entry in entries.items():
        if isinstance(entry["en"], str):
            by_en[entry["en"].strip().lower()].append(zh)
    return {en: keys for en, keys in by_en.items()
            if len(keys) > 1 and not any("note" in entries[k] or "en_full" in entries[k] for k in keys)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="report only; exit 1 if the file is out of step")
    args = parser.parse_args()
    old = json.loads(OUT.read_text("utf-8"))["entries"] if OUT.exists() else {}
    entries = build(old)
    text = json.dumps({"_about": ABOUT, "entries": entries}, ensure_ascii=False, indent=2) + "\n"

    added = sorted(set(entries) - set(old))
    gone = sorted(set(old) - set(entries))
    changed = sorted(k for k in set(entries) & set(old) if entries[k]["en"] != old[k]["en"])
    for title, keys in (("new", added), ("removed", gone), ("English changed", changed)):
        if keys:
            print(f"{title} ({len(keys)}):", *keys[:20], sep="\n  ")
    for zh in changed:
        if any(field in entries[zh] for field in HAND):
            print(f"  check the hand-written fields of 「{zh}」: its English changed")
    lonely = shared(entries)
    if lonely:
        print(f"same English for different Chinese, no note yet ({len(lonely)}):")
        for en, keys in lonely.items():
            print(f"  {en!r} <= {keys}")

    if args.check:
        current = OUT.read_text("utf-8") if OUT.exists() else ""
        sys.exit(0 if current == text else 1)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(text, "utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(entries)} entries")


if __name__ == "__main__":
    main()
