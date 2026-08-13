"""Download NAS changes into the local AINail/datasets copy without
clobbering local edits you haven't pushed yet.

Usage:
    python pull.py --nas-root ~/mt_nas/AINail/datasets            # dry run, just prints the plan
    python pull.py --nas-root ~/mt_nas/AINail/datasets --apply    # actually downloads/removes files

Relative to the last pull/push baseline:
  - new files on NAS                                    -> downloaded
  - files changed on NAS, unchanged locally              -> re-downloaded
  - files removed from NAS, unchanged locally             -> removed locally too
  - anything where your local copy also diverged from the -> left alone, printed as a
    baseline for the same path                              CONFLICT for you to resolve by hand
"""
import argparse
import os
import shutil

import manifest as mf


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--nas-root", required=True, help="mounted NAS path for AINail/datasets")
    ap.add_argument("--local-root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--apply", action="store_true", help="actually copy/remove files (default: dry run only)")
    args = ap.parse_args()

    local_manifest = mf.build_manifest(args.local_root)
    remote_manifest = mf.build_manifest(args.nas_root)
    baseline = mf.load_baseline(args.local_root)

    to_download, to_remove_local, conflicts = [], [], []

    for rel, remote_entry in remote_manifest.items():
        base_entry = baseline.get(rel)
        local_entry = local_manifest.get(rel)

        if base_entry is None:
            if local_entry is None:
                to_download.append(rel)
            elif not mf.entries_equal(local_entry, remote_entry):
                conflicts.append((rel, "new on NAS and locally, but contents differ"))
        else:
            if mf.entries_equal(remote_entry, base_entry):
                continue  # NAS untouched since your last sync
            if local_entry is None:
                conflicts.append((rel, "changed on NAS, but you removed/moved it locally — resolve by hand"))
            elif mf.entries_equal(local_entry, base_entry):
                to_download.append(rel)
            else:
                conflicts.append((rel, "changed both on NAS and locally since your last sync"))

    for rel, base_entry in baseline.items():
        if rel in remote_manifest:
            continue  # still exists remotely, handled above
        local_entry = local_manifest.get(rel)
        if local_entry is None:
            continue  # already gone locally too, nothing to do
        if mf.entries_equal(local_entry, base_entry):
            to_remove_local.append(rel)
        else:
            conflicts.append((rel, "removed from NAS, but you changed it locally — resolve by hand"))

    print(f"DOWNLOAD ({len(to_download)}):")
    for rel in to_download:
        print(f"  + {rel}")
    print(f"REMOVE LOCAL ({len(to_remove_local)}):")
    for rel in to_remove_local:
        print(f"  - {rel}")
    if conflicts:
        print(f"CONFLICTS - not touched, resolve manually ({len(conflicts)}):")
        for rel, reason in conflicts:
            print(f"  x {rel}: {reason}")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to update your local copy.")
        return

    for rel in to_download:
        dst = os.path.join(args.local_root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(args.nas_root, rel), dst)

    for rel in to_remove_local:
        path = os.path.join(args.local_root, rel)
        if os.path.exists(path):
            os.remove(path)

    new_baseline = mf.build_manifest(args.local_root)
    for rel, _ in conflicts:
        if rel in baseline:
            new_baseline[rel] = baseline[rel]
        else:
            new_baseline.pop(rel, None)
    mf.save_baseline(args.local_root, new_baseline)
    print(f"\nDone. Downloaded {len(to_download)}, removed {len(to_remove_local)} locally.")
    if conflicts:
        print("Some files were left untouched — see CONFLICTS above.")


if __name__ == "__main__":
    main()
