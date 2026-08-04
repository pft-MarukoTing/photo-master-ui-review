"""Username-only account + processing-history store, backed by the same
shared Redis client as put_job.py/aiserver_utils.queue. No auth/password --
a username is just a namespace to group a person's own job history under.
"""

import json
import re
import time

from aiserver_utils.pf_redis import pf_redis_client

USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

USERS_SET_KEY = "yce_ai:photoAgent:users"
# ":v2" -- a HASH now (one field per distinct uploaded image), not the LIST
# (one field per submission) the same key name used before. Redis enforces
# one type per key, so reusing the old name would throw WRONGTYPE against any
# key created under the previous schema.
USER_HISTORY_KEY_FORMAT = "yce_ai:photoAgent:user:{user}:history:v2"

# No TTL/eviction exists anywhere else in this codebase's Redis usage --
# capping entry count here is the only bound on unlimited growth. Now that
# entries are one-per-image (not one-per-submission), this bounds how many
# *different* photos a user's history holds -- revising the same photo any
# number of times no longer grows it at all.
HISTORY_MAX_LEN = 200


def normalize_username(raw):
    name = (raw or "").strip()
    if not USERNAME_RE.match(name):
        raise ValueError("user must be 1-64 characters, letters/digits/_/- only")
    return name


def create_account(username):
    """Idempotent: returns True if this username is newly registered."""
    added = pf_redis_client.sadd(USERS_SET_KEY, username)
    return bool(added)


def account_exists(username):
    return bool(pf_redis_client.sismember(USERS_SET_KEY, username))


def record_history(username, job_id, path, image_key, action_id, params=None):
    """image_key identifies "the same uploaded image" across an entire
    revise/continue chain -- a local-path job's resolved srcKeys[0] (stable
    across every continue of that same photo, since continue_job carries the
    prior job's srcKeys forward unchanged) or, for an AIPoC-path job (no such
    durable identity), just its own task_id.

    Storing history as a Redis HASH keyed by image_key means a later revise
    of the same photo overwrites its one entry in place (HSET) instead of
    piling up a new row per edit -- the history page always shows just each
    photo's latest status, not one row per edit ever made.

    A single "Run" click with run_count > 1 (two-stage manual path --
    runSingleItem/editBatchItem in app.js) submits N *separate*
    /continue/<job_id> calls, one per RUN, each producing its own job_id --
    without action_id, whichever call's response lands last would silently
    overwrite the others right back out of history. action_id identifies
    "one user action" so every RUN from it accumulates into the same entry's
    `runs` list instead of clobbering each other: it's the prior job_id being
    continued from (shared by every RUN of one action, since none of them
    change what they're continuing from mid-action) for /continue, or just
    the job's own id for /submit (run_count handled entirely server-side in
    ONE job there, job_processor.py) and /aipoc/submit (no revise-chain
    concept to group at all).

    params carries the full submitted settings (preset/image_quality/
    image_model/aspect_ratio/ai_photo_editing/run_qa) -- not just for display,
    but so the frontend's history page can resubmit a local-path job (revise
    the recipe/style and continue) using exactly what it was first processed
    with, without having to re-derive those from the job's own Redis hash.
    """
    key = USER_HISTORY_KEY_FORMAT.format(user=username)
    existing = None
    existing_raw = pf_redis_client.hget(key, image_key)
    if existing_raw:
        try:
            existing = json.loads(existing_raw)
        except (TypeError, ValueError):
            existing = None

    if existing and existing.get("action_id") == action_id:
        runs = existing.get("runs") or []
        if job_id not in runs:
            runs.append(job_id)
        entry = {**existing, "runs": runs, "ts": time.time()}
    else:
        entry = {
            "action_id": action_id,
            "path": path,
            "runs": [job_id],
            "ts": time.time(),
            **(params or {}),
        }
    pf_redis_client.hset(key, image_key, json.dumps(entry))
    _prune_oldest(key)


def _prune_oldest(key):
    """Hash fields aren't insertion-ordered/trimmable like a list -- fetch
    everything, and if over the cap, drop whichever entries have the oldest
    `ts` until back at HISTORY_MAX_LEN."""
    raw_entries = pf_redis_client.hgetall(key)
    if len(raw_entries) <= HISTORY_MAX_LEN:
        return
    parsed = []
    for field, raw in raw_entries.items():
        try:
            parsed.append((field, json.loads(raw).get("ts", 0)))
        except (TypeError, ValueError):
            parsed.append((field, 0))
    parsed.sort(key=lambda item: item[1])
    overflow = len(parsed) - HISTORY_MAX_LEN
    stale_fields = [field for field, _ in parsed[:overflow]]
    if stale_fields:
        pf_redis_client.hdel(key, *stale_fields)


def get_history(username, limit=50):
    key = USER_HISTORY_KEY_FORMAT.format(user=username)
    raw_entries = pf_redis_client.hgetall(key)
    entries = []
    for raw in raw_entries.values():
        try:
            entries.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return entries[:limit]
