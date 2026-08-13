"""File-level locking on the NAS so two people can't crop/edit the same
image at the same time. Only needed for in-place content edits — adding a
new file or moving a file doesn't need a lock since those can't collide on
the same path.
"""
import getpass
import json
import os
import time


LOCK_DIRNAME = ".locks"


class LockError(Exception):
    pass


def _lock_file(nas_root, rel_path):
    encoded = rel_path.replace("/", "__")
    return os.path.join(nas_root, LOCK_DIRNAME, encoded + ".lock")


def acquire(nas_root, rel_path, user=None):
    user = user or getpass.getuser()
    lock_path = _lock_file(nas_root, rel_path)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    if os.path.exists(lock_path):
        with open(lock_path) as f:
            info = json.load(f)
        if info["user"] != user:
            raise LockError(
                f"{rel_path} is locked by {info['user']} since {info['acquired_at']}"
            )
        return  # already held by us
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        with open(lock_path) as f:
            info = json.load(f)
        raise LockError(
            f"{rel_path} is locked by {info['user']} since {info['acquired_at']}"
        )
    with os.fdopen(fd, "w") as f:
        json.dump({"user": user, "acquired_at": time.strftime("%Y-%m-%d %H:%M:%S")}, f)


def release(nas_root, rel_path, user=None):
    user = user or getpass.getuser()
    lock_path = _lock_file(nas_root, rel_path)
    if not os.path.exists(lock_path):
        return
    with open(lock_path) as f:
        info = json.load(f)
    if info["user"] != user:
        raise LockError(f"cannot release {rel_path}: locked by {info['user']}, not {user}")
    os.remove(lock_path)


def is_locked_by_other(nas_root, rel_path, user=None):
    user = user or getpass.getuser()
    lock_path = _lock_file(nas_root, rel_path)
    if not os.path.exists(lock_path):
        return None
    with open(lock_path) as f:
        info = json.load(f)
    return info if info["user"] != user else None
