"""Send PhotoAgent's source text to the Localization Hub's doc repo.

  python scripts/l10n_doc.py push [--yes]

Refreshes i18n/translation-source.json and i18n/arb-keys.json
(scripts/translation_source.py), writes intl_en.arb / intl_zh_Hant.arb into
the doc repo, commits and pushes. It never writes another language's file:
those are the Hub's, and overwriting one makes the Hub translate it again.
Only PhotoAgent-dev sends: other branches would swap the Hub's source back
and forth.

The doc repo is cloned next to MTImageAI/ unless PHOTOAGENT_L10N_DOC names
another folder. Run from PhotoAgent/.
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys

import l10n_arb

ROOT = l10n_arb.ROOT
REMOTE = "git@github.com:perfect-corp/MTImageAI-PhotoAgent-doc.git"
DOC = pathlib.Path(os.environ.get("PHOTOAGENT_L10N_DOC") or ROOT.parent.parent / "MTImageAI-PhotoAgent-doc")
SOURCE_BRANCH = "PhotoAgent-dev"


def git(*args, capture=False):
    result = subprocess.run(["git", "-C", str(DOC), *args], check=True, encoding="utf-8",
                            capture_output=capture)
    return result.stdout if capture else None


def main_repo(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, encoding="utf-8",
                          capture_output=True).stdout.strip()


def update_clone():
    if not (DOC / ".git").exists():
        print(f"cloning {REMOTE} into {DOC}", flush=True)
        subprocess.run(["git", "clone", REMOTE, str(DOC)], check=True)
    if subprocess.run(["git", "ls-remote", "--heads", REMOTE], check=True, encoding="utf-8",
                      capture_output=True).stdout.strip():
        git("pull", "--rebase", "--quiet")


def push(args):
    branch = main_repo("rev-parse", "--abbrev-ref", "HEAD")
    if branch != SOURCE_BRANCH:
        sys.exit(f"on {branch}: only {SOURCE_BRANCH} sends the source text to the Hub")
    subprocess.run([sys.executable, str(ROOT / "scripts/translation_source.py")], check=True)
    entries = json.loads((ROOT / "i18n/translation-source.json").read_text("utf-8"))["entries"]
    update_clone()
    files = l10n_arb.arb_files(entries)
    for name, text in files.items():
        (DOC / name).write_text(text, "utf-8", newline="\n")
    git("add", *files)
    if not git("status", "--porcelain", "--", *files, capture=True).strip():
        print("the doc repo already has these strings; nothing to push")
        return
    git("--no-pager", "diff", "--cached", "--stat")
    if not args.yes and input(f"commit and push to {REMOTE}? [y/N] ").strip().lower() != "y":
        git("reset", "--quiet", "--", *files)
        print("not pushed; the files are left in the doc repo")
        return
    sha = main_repo("rev-parse", "--short", "HEAD")
    git("commit", "--quiet", "-m", f"[what] Update the PhotoAgent source strings (MTImageAI {sha})")
    git("push", "--quiet", "origin", "HEAD")
    print("pushed; the Hub picks it up on its next sync")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    push_parser = sub.add_parser("push", help="send the source strings to the doc repo")
    push_parser.add_argument("--yes", action="store_true", help="commit and push without asking")
    args = parser.parse_args()
    {"push": push}[args.command](args)


if __name__ == "__main__":
    main()
