"""Flagged-job reports, backed by the same Redis client as account.py.

Lets any user flag a job_id as needing review; lets an admin account see
every flagged job across all users. "Admin" is just a fixed username
allowlist (PHOTO_AGENT_ADMIN_USERS env var, comma-separated) -- there's no
real permission system here, matching account.py's own no-auth, username-
is-just-a-namespace model.
"""

import json
import os
import time

from aiserver_utils.pf_redis import pf_redis_client
from backend.services.account import normalize_username

REPORTS_KEY = "yce_ai:photoAgent:reports"

_ADMIN_USERS = {
    name.strip().lower()
    for name in os.environ.get("PHOTO_AGENT_ADMIN_USERS", "admin").split(",")
    if name.strip()
}


def is_admin(username):
    return normalize_username(username).lower() in _ADMIN_USERS


def record_report(username, job_id, path, note=None):
    """One entry per (job_id, user) pair -- re-reporting the same job by the
    same user just refreshes its timestamp (and note) in place, but two
    different users reporting the same job both show up to the admin as
    separate entries. note is the reporter's optional free-text description
    of the issue -- omitted from the stored entry entirely when blank,
    rather than stored as "" (keeps get_reports' output the same shape as
    before note existed for every report that doesn't use it)."""
    name = normalize_username(username)
    field = f"{job_id}:{name}"
    entry = {"job_id": job_id, "path": path, "user": name, "ts": time.time()}
    if note:
        entry["note"] = note
    pf_redis_client.hset(REPORTS_KEY, field, json.dumps(entry))


def get_reports(limit=200):
    raw_entries = pf_redis_client.hgetall(REPORTS_KEY)
    entries = []
    for raw in raw_entries.values():
        try:
            entries.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return entries[:limit]
