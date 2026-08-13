"""Build and persist a snapshot of a dataset folder for diffing against NAS state.

Uses (size, mtime) as the change fingerprint, like rsync -a, so scanning the
NAS over SMB only needs stat() calls, not reading file contents over the
network. Content hashing is only used, lazily, to confirm a suspected file
move (see push.py).
"""
import hashlib
import json
import os


SKIP_DIRNAMES = {"nas_sync", ".nas_sync"}


def _iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRNAMES and not d.startswith(".")]
        for fname in filenames:
            if fname.startswith("."):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            yield rel, full


def build_manifest(root):
    manifest = {}
    for rel, full in _iter_files(root):
        st = os.stat(full)
        manifest[rel] = {"size": st.st_size, "mtime": int(st.st_mtime)}
    return manifest


def file_hash(path, chunk_size=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def entries_equal(a, b):
    if a is None or b is None:
        return False
    return a["size"] == b["size"] and a["mtime"] == b["mtime"]


def baseline_path(local_root):
    return os.path.join(local_root, "nas_sync", ".baseline_manifest.json")


def load_baseline(local_root):
    path = baseline_path(local_root)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save_baseline(local_root, manifest):
    path = baseline_path(local_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(manifest, f)
    os.replace(tmp, path)


def print_list(prefix, items, limit=10):
    for item in items[:limit]:
        print(f"  {prefix} {item}")
    if len(items) > limit:
        print(f"  ... and {len(items) - limit} more")
