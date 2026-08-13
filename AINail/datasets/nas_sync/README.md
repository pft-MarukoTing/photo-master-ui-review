# AINail/datasets NAS Sync Tools

Scripts for syncing `AINail/datasets` with a shared NAS folder. Run all commands from `AINail/datasets/nas_sync/`.

## 0. Mount the NAS

```bash
MOUNT_POINT=~/mt_nas
mkdir -p "$MOUNT_POINT"
mount_smbfs //<user>@<NAS_IP>/ARCHIVE "$MOUNT_POINT"
```

- Replace `<user>` and `<NAS_IP>` with your actual account and the NAS IP.
- `$MOUNT_POINT` can be named anything you like.
- `mount error: File exists` means it's already mounted — check with `mount`.
- `umount "$MOUNT_POINT"` unmounts it without affecting any files on the NAS
  or locally.

Set `NAS_ROOT` once so `sync.sh` knows where the NAS copy lives:

```bash
export NAS_ROOT="$MOUNT_POINT/AINail/datasets"
```

## 1. Day-to-day sync

### Pull the latest state

```bash
./sync.sh pull
```

Downloads files added/updated by others and removes files that were moved
away on the NAS. If both your local copy and the NAS changed the same file,
it's reported as `CONFLICT` and skipped — resolve it manually.

### Add / move files

```bash
./sync.sh push
```

New files are uploaded directly; moves (same content, new path) are
detected automatically and replicated on the NAS.

### Edit a file in place (e.g. cropping, same filename)

Just edit the file locally and run `./sync.sh push` like any other change.
Overwriting content at the same path is the one operation that can conflict
between two people, so `push` locks each such file on the NAS right before
copying it and releases the lock right after. If a file is already locked
by someone else, it's skipped and reported instead of being overwritten.

`push`/`pull` refuse to run if `NAS_ROOT` doesn't look like a mounted network
drive (checked via `mount`), to avoid accidentally writing to a local folder
that happens to exist at that path. Pass `--force` (on the underlying
`push.py`/`pull.py`, not through `sync.sh`) to bypass this check.

## 2. Reading the output

`push`/`pull` apply their changes by default; add `--dry-run` to only print
what would happen without touching any files, e.g. `./sync.sh push --dry-run`.

- `ADD` / `DOWNLOAD`: new files
- `MOVE`: a detected move (same content, new path)
- `MODIFY`: content changed at the same path, no lock conflict
- `SKIPPED - locked by someone else`: file is currently locked, left untouched
- `CONFLICT`: local and NAS both changed the same file — needs manual resolution

## 3. Files

| File | Purpose |
|---|---|
| `manifest.py` | Scans a folder and detects changes (by file size and mtime) |
| `lock.py` | Lock/unlock before and after in-place edits |
| `push.py` | Local → NAS |
| `pull.py` | NAS → local |
| `sync.sh` | CLI entrypoint: `pull` / `push` / `lock <path>` / `unlock <path>` |

`.baseline_manifest.json` is each machine's local sync bookkeeping — it
doesn't need to be synced to the NAS.
