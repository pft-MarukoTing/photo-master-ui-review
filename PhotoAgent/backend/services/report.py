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
import uuid

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


def record_report(username, job_id, path, note=None, user_prompt=None, screenshot_key=None):
    """job_id is optional -- #report-btn is always clickable (app.js), even
    with no result on screen, for a general "something's wrong" report with
    nothing to point at. A job-tied report keys on (job_id, user): re-
    reporting the same job by the same user just refreshes its timestamp
    (and note/user_prompt/screenshot) in place, while two different users
    reporting the same job both show up to the admin as separate entries.
    A job-less report has no such natural dedup key -- each submission gets
    its own random id instead, so a second general report from the same
    user doesn't overwrite the first. Returns the report's id (the Redis
    hash field itself), which report_screenshot resolves by later.

    note is the reporter's optional free-text description of the issue;
    user_prompt is whatever 補充指令 text the original submission actually
    used (the frontend reads it off its own in-memory record of that job,
    since neither is stored anywhere else the report could look it up from
    -- the raw text a user types is never persisted into the job's own
    analysis/result JSON). screenshot_key is the S3 key report_job
    (backend/routes/api.py) already uploaded the reporter's screen capture
    to, if any -- stored so report_screenshot can resolve it later without
    the client ever handling a raw S3 key itself. All three are omitted
    from the stored entry entirely when absent, rather than stored as ""
    (keeps get_reports' output the same shape as before any of them existed
    for every report that doesn't use them)."""
    name = normalize_username(username)
    field = f"{job_id}:{name}" if job_id else f"general:{name}:{uuid.uuid4().hex}"
    entry = {"report_id": field, "job_id": job_id, "path": path, "user": name, "ts": time.time()}
    if note:
        entry["note"] = note
    if user_prompt:
        entry["user_prompt"] = user_prompt
    if screenshot_key:
        entry["screenshot_key"] = screenshot_key
    pf_redis_client.hset(REPORTS_KEY, field, json.dumps(entry))
    return field


def get_report_by_id(report_id):
    """One report entry, including its raw screenshot_key -- for
    report_screenshot (backend/routes/api.py) to resolve server-side only;
    get_reports below strips that same key from what it hands to the
    frontend (has_screenshot instead), so a client never needs to know or
    pass around the actual S3 key. Keyed by report_id (not job_id/user
    separately) since a general report has no job_id to key on at all."""
    raw = pf_redis_client.hget(REPORTS_KEY, report_id)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def get_reports(limit=200):
    raw_entries = pf_redis_client.hgetall(REPORTS_KEY)
    entries = []
    for raw in raw_entries.values():
        try:
            entry = json.loads(raw)
        except (TypeError, ValueError):
            continue
        entry["has_screenshot"] = bool(entry.pop("screenshot_key", None))
        entries.append(entry)
    entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return entries[:limit]
