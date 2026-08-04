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
USER_HISTORY_KEY_FORMAT = "yce_ai:photoAgent:user:{user}:history"

# No TTL/eviction exists anywhere else in this codebase's Redis usage --
# capping list length here is the only bound on unlimited growth.
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


def record_history(username, job_id, path, params=None):
    """params carries the full submitted settings (preset/image_quality/
    image_model/aspect_ratio/ai_photo_editing/run_qa) -- not just for display,
    but so the frontend's history page can resubmit a local-path job (revise
    the recipe/style and continue) using exactly what it was first processed
    with, without having to re-derive those from the job's own Redis hash.
    """
    key = USER_HISTORY_KEY_FORMAT.format(user=username)
    entry = {"job_id": job_id, "path": path, "ts": time.time(), **(params or {})}
    pf_redis_client.lpush(key, json.dumps(entry))
    pf_redis_client.ltrim(key, 0, HISTORY_MAX_LEN - 1)


def get_history(username, limit=50):
    key = USER_HISTORY_KEY_FORMAT.format(user=username)
    raw_entries = pf_redis_client.lrange(key, 0, limit - 1)
    entries = []
    for raw in raw_entries:
        try:
            entries.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return entries
