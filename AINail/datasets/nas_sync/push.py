"""Sync local AINail/datasets changes up to the NAS without clobbering
anyone else's concurrent add / move / edit.

Usage:
    python push.py --nas-root ~/mt_nas/AINail/datasets            # dry run, just prints the plan
    python push.py --nas-root ~/mt_nas/AINail/datasets --apply    # actually copies files

Detects, relative to the last pull/push baseline:
  - ADD:    new local file, no matching path on NAS            -> copy up
  - MOVE:   a file disappeared from one local path and the same -> replicate the move on NAS
            content reappeared at a new local path
  - MODIFY: same path, content changed locally (e.g. a crop)    -> locked, copied, unlocked
            automatically; refused if someone else holds the lock
  - CONFLICT: NAS changed the same path since the baseline too  -> never overwritten automatically,
            printed for manual resolution

A local file that just disappeared (not detected as a move) is left alone on
the NAS — this tool never deletes remote files on your behalf, matching the
existing "move to archive_failed instead of deleting" convention.
"""
import argparse
import getpass
import os
import shutil
import sys

import lock
import manifest as mf


def find_moves(baseline, local_manifest, local_root, nas_root, remote_manifest):
    disappeared = [r for r in baseline if r not in local_manifest]
    new_local = [r for r in local_manifest if r not in baseline]
    moves = {}
    for old_rel in disappeared:
        remote_entry = remote_manifest.get(old_rel)
        if remote_entry is None or not mf.entries_equal(remote_entry, baseline[old_rel]):
            continue  # NAS already changed/moved this itself; don't guess
        old_size = baseline[old_rel]["size"]
        candidates = [r for r in new_local if local_manifest[r]["size"] == old_size and r not in moves.values()]
        if not candidates:
            continue
        old_hash = mf.file_hash(os.path.join(nas_root, old_rel))
        for cand in candidates:
            if mf.file_hash(os.path.join(local_root, cand)) == old_hash:
                moves[old_rel] = cand
                break
    return moves


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--nas-root", required=True, help="mounted NAS path for AINail/datasets")
    ap.add_argument("--local-root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--user", default=getpass.getuser())
    ap.add_argument("--apply", action="store_true", help="actually copy files (default: dry run only)")
    args = ap.parse_args()

    os.makedirs(args.nas_root, exist_ok=True)
    local_manifest = mf.build_manifest(args.local_root)
    remote_manifest = mf.build_manifest(args.nas_root)
    baseline = mf.load_baseline(args.local_root)

    moves = find_moves(baseline, local_manifest, args.local_root, args.nas_root, remote_manifest)
    moved_targets = set(moves.values())

    to_add, to_modify, conflicts, skipped_locked = [], [], [], []

    for rel, local_entry in local_manifest.items():
        if rel in moved_targets:
            continue
        base_entry = baseline.get(rel)
        remote_entry = remote_manifest.get(rel)

        if base_entry is None:
            if remote_entry is None:
                to_add.append(rel)
            elif not mf.entries_equal(local_entry, remote_entry):
                conflicts.append((rel, "new locally and on NAS, but contents differ"))
        else:
            if mf.entries_equal(local_entry, base_entry):
                continue  # untouched since baseline
            if remote_entry is None:
                conflicts.append((rel, "modified locally, but removed from NAS since your last pull"))
            elif mf.entries_equal(remote_entry, base_entry):
                held_by_other = lock.is_locked_by_other(args.nas_root, rel, args.user)
                if held_by_other:
                    skipped_locked.append((rel, held_by_other))
                else:
                    to_modify.append(rel)
            else:
                conflicts.append((rel, "modified both locally and on NAS since your last pull"))

    print(f"ADD ({len(to_add)}):")
    mf.print_list("+", to_add)
    print(f"MOVE ({len(moves)}):")
    mf.print_list("", [f"{old_rel} -> {new_rel}" for old_rel, new_rel in moves.items()])
    print(f"MODIFY ({len(to_modify)}):")
    mf.print_list("*", to_modify)
    if skipped_locked:
        print(f"SKIPPED - locked by someone else ({len(skipped_locked)}):")
        mf.print_list("!", [f"{rel} (locked by {info['user']} since {info['acquired_at']})" for rel, info in skipped_locked])
    if conflicts:
        print(f"CONFLICTS - not touched, resolve manually ({len(conflicts)}):")
        mf.print_list("x", [f"{rel}: {reason}" for rel, reason in conflicts])

    if not args.apply:
        print("\nDry run only. Re-run with --apply to make these changes on the NAS.")
        return

    for i, rel in enumerate(to_add, 1):
        dst = os.path.join(args.nas_root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(args.local_root, rel), dst)
        if i % 100 == 0 or i == len(to_add):
            print(f"  add {i}/{len(to_add)}")

    for i, (old_rel, new_rel) in enumerate(moves.items(), 1):
        dst = os.path.join(args.nas_root, new_rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(args.local_root, new_rel), dst)
        os.remove(os.path.join(args.nas_root, old_rel))
        if i % 100 == 0 or i == len(moves):
            print(f"  move {i}/{len(moves)}")

    modified = []
    for i, rel in enumerate(to_modify, 1):
        try:
            lock.acquire(args.nas_root, rel, args.user)
        except lock.LockError as e:
            skipped_locked.append((rel, {"user": "someone else", "acquired_at": str(e)}))
            continue
        try:
            shutil.copy2(os.path.join(args.local_root, rel), os.path.join(args.nas_root, rel))
            modified.append(rel)
        finally:
            lock.release(args.nas_root, rel, args.user)
        if i % 100 == 0 or i == len(to_modify):
            print(f"  modify {i}/{len(to_modify)}")

    new_baseline = mf.build_manifest(args.local_root)
    for rel, _ in conflicts:
        new_baseline.pop(rel, None)
        if rel in baseline:
            new_baseline[rel] = baseline[rel]
    for rel, _ in skipped_locked:
        new_baseline[rel] = baseline.get(rel, local_manifest[rel])
    mf.save_baseline(args.local_root, new_baseline)
    print(f"\nDone. Pushed {len(to_add)} add, {len(moves)} move, {len(modified)} modify.")
    if conflicts or skipped_locked:
        print("Some files were left untouched — see CONFLICTS / SKIPPED above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
