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
from aiserver_utils.queue import get_job_info
from backend.services.account import get_history_entry, get_history_entry_for_job, normalize_username

REPORTS_KEY = "yce_ai:photoAgent:reports"
REPORT_STATUSES = ("unread", "read", "solved")
# A reported job's own Redis job info, copied at report time. The job info
# expires out of Redis after a few weeks while its S3 artifacts stay, and an
# admin may well open a report later than that -- api.py's read-only result
# routes fall back to this copy (get_reported_job_info) once the live one is
# gone. Keyed by job_id alone: the job is the same whoever reported it.
REPORT_JOBS_KEY = "yce_ai:photoAgent:report_jobs"
REPORT_JOB_FIELDS = ("status", "error", "error_message", "stage", "run_count", "runs_done", "0_res0", "srcKeys")
# The reported conversation as it stood when the report was filed
# (chat_session.report_chat), keyed by report_id: the live one keeps changing
# as the user goes on -- new messages, another version picked -- and expires
# after SESSION_TTL_SECONDS, while the admin wants the moment of the report.
REPORT_CHATS_KEY = "yce_ai:photoAgent:report_chats"
# What a report filed from the chat UI (/chat/) carries about the conversation
# on screen, so an admin can open that conversation itself. owner is whose
# session it is -- the report's own "user" is whatever name the reporter typed
# into the form, which need not be the account the session lives under.
REPORT_CHAT_FIELDS = ("owner", "session_id", "photo_id", "batch_id")
# Filed from the chat UI's 首頁 or upload page, where there is no conversation
# yet to copy: the entry's "page" says which, and this keeps what the upload
# page had on it (the photos picked, the wishes typed and what the AI said to
# them, settings changed), keyed by report_id like REPORT_CHATS_KEY.
REPORT_PAGES_KEY = "yce_ai:photoAgent:report_pages"
REPORT_PAGES = ("home", "upload")

_ADMIN_USERS = {
    name.strip().lower()
    for name in os.environ.get("PHOTO_AGENT_ADMIN_USERS", "admin").split(",")
    if name.strip()
}


def _case_for_report(username, job_id, image_key=None, include_versions=False):
    """Best-effort history metadata lookup; reporting must stay independent
    from history availability just like screenshot upload already does."""
    try:
        if image_key:
            # include_versions=False unless asked: recording a report only
            # needs the case's lifecycle timestamps, and loading every version
            # of it would read a second key for nothing.
            case = get_history_entry(username, image_key, include_versions=include_versions)
            if case:
                return case
        return get_history_entry_for_job(username, job_id)
    except Exception:
        return None


def _history_owner(entry):
    """Whose history the reported job is under: the chat session's account
    when the report came from the chat UI, else the name the reporter typed
    (the panel UI's report form defaults it to the current account)."""
    return entry.get("owner") or entry.get("user")


def _reported_version(case, job_id):
    """The version of case holding job_id, or None."""
    for version in (case or {}).get("versions") or []:
        if job_id in (version.get("runs") or []):
            return version
    return None


def is_admin(username):
    return normalize_username(username).lower() in _ADMIN_USERS


def record_report(username, job_id, path, note=None, user_prompt=None, screenshot_key=None, source=None, chat=None):
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
    for every report that doesn't use them).

    source is "chat" for a report filed from the chat UI (absent means the
    panel UI, which is all there was before it); chat holds that UI's
    REPORT_CHAT_FIELDS, each kept only when present, same as note above."""
    name = normalize_username(username)
    field = f"{job_id}:{name}" if job_id else f"general:{name}:{uuid.uuid4().hex}"
    # Re-reporting an already-triaged entry (dedup key above) resets status
    # back to "unread" along with the timestamp -- new activity on a job an
    # admin already marked read/solved needs a fresh look, not to stay
    # silently marked as handled.
    entry = {"report_id": field, "job_id": job_id, "path": path, "user": name, "ts": time.time(), "status": "unread"}
    if note:
        entry["note"] = note
    if user_prompt:
        entry["user_prompt"] = user_prompt
    if screenshot_key:
        entry["screenshot_key"] = screenshot_key
    if source:
        entry["source"] = source
    for key in REPORT_CHAT_FIELDS:
        if (chat or {}).get(key):
            entry[key] = chat[key]
    if (chat or {}).get("page") in REPORT_PAGES:
        entry["page"] = chat["page"]
    if job_id:
        case = _case_for_report(_history_owner(entry), job_id)
        if case:
            entry["uploaded_at"] = case.get("uploaded_at")
            entry["modified_at"] = case.get("modified_at")
            entry["history_image_key"] = case.get("image_key")
    pf_redis_client.hset(REPORTS_KEY, field, json.dumps(entry))
    if job_id:
        _snapshot_reported_job(job_id)
    return field


def _snapshot_reported_job(job_id):
    """Best-effort, like _case_for_report: a failed copy only means this
    report stops opening once the job expires, which must never fail the
    report itself. A re-report refreshes the copy (e.g. a job reported while
    still processing, then again once finished)."""
    try:
        info = get_job_info(job_id, keyToGet=list(REPORT_JOB_FIELDS))
        if info:
            pf_redis_client.hset(REPORT_JOBS_KEY, job_id, json.dumps(info))
    except Exception:
        pass


def save_report_chat(report_id, chat):
    """Best-effort, like _snapshot_reported_job: without the copy the
    read-only view just falls back to the live conversation. A re-report of
    the same job (record_report's dedup) refreshes it with the new moment."""
    try:
        pf_redis_client.hset(REPORT_CHATS_KEY, report_id, json.dumps(chat))
    except Exception:
        pass


def get_report_chat(report_id):
    """save_report_chat's copy for report_id, or None (a report filed before
    copies were kept, or one whose copy failed)."""
    try:
        raw = pf_redis_client.hget(REPORT_CHATS_KEY, report_id)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def save_report_page(report_id, page):
    """Best-effort, like save_report_chat: without it the read-only view of a
    首頁/upload-page report shows just the report itself."""
    try:
        pf_redis_client.hset(REPORT_PAGES_KEY, report_id, json.dumps(page))
    except Exception:
        pass


def get_report_page(report_id):
    """save_report_page's copy for report_id, or None."""
    try:
        raw = pf_redis_client.hget(REPORT_PAGES_KEY, report_id)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def get_reported_job_info(job_id, keys):
    """The report-time copy of job_id's job info, narrowed to `keys` in the
    same shape get_job_info returns -- or None if no report holds one."""
    try:
        raw = pf_redis_client.hget(REPORT_JOBS_KEY, job_id)
        info = json.loads(raw) if raw else None
    except Exception:
        return None
    if not info:
        return None
    picked = {key: info[key] for key in keys if key in info}
    return picked or None


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


def update_report_status(report_id, status):
    """Triage state an admin sets from the reports drawer -- unread (default,
    also what a re-report resets to, see record_report) -> read (opened at
    least once) -> solved (admin considers it handled). Returns the updated
    entry (in the same has_screenshot-stripped shape get_reports uses), or
    None if report_id doesn't exist or status isn't one of REPORT_STATUSES."""
    if status not in REPORT_STATUSES:
        return None
    entry = get_report_by_id(report_id)
    if entry is None:
        return None
    entry["status"] = status
    pf_redis_client.hset(REPORTS_KEY, report_id, json.dumps(entry))
    entry["has_screenshot"] = bool(entry.pop("screenshot_key", None))
    entry.pop("history_image_key", None)
    return entry


def delete_report(report_id):
    """Removes just this report entry -- the job/result it points to (if
    any) is a separate lifecycle (see account.py's own history-vs-job
    split) and is never touched here. Any S3 screenshot the report carried
    is left in place; it's just no longer reachable via report_screenshot
    once its entry is gone. The conversation copy (save_report_chat) and the
    page copy (save_report_page) go with it; the job-info copy (_snapshot_reported_job) is dropped once no
    report points at that job any more."""
    entry = get_report_by_id(report_id)
    pf_redis_client.hdel(REPORTS_KEY, report_id)
    pf_redis_client.hdel(REPORT_CHATS_KEY, report_id)
    pf_redis_client.hdel(REPORT_PAGES_KEY, report_id)
    # The job copy goes with the last report pointing at that job.
    job_id = (entry or {}).get("job_id")
    if job_id and not _job_still_reported(job_id):
        pf_redis_client.hdel(REPORT_JOBS_KEY, job_id)


def _job_still_reported(job_id):
    for raw in pf_redis_client.hvals(REPORTS_KEY):
        try:
            if json.loads(raw).get("job_id") == job_id:
                return True
        except (TypeError, ValueError, AttributeError):
            continue
    return False


def get_reports(limit=50, offset=0):
    """One page of reports, newest first -- (entries, has_more), same
    offset/limit/has_more shape as GET /history/<user> so the admin drawer
    can load it on scroll the same way #history-list does. The hash itself
    is unordered, so every entry is still read and sorted to find the page;
    only the per-entry history lookup (_case_for_report, one Redis read per
    report) is limited to the page actually returned.

    Each job-tied entry also gets version_params, the settings its job was
    actually submitted with, so the admin's 面板模式 shows those rather than
    defaults: the report itself only knows the job_id."""
    raw_entries = pf_redis_client.hgetall(REPORTS_KEY)
    entries = []
    for field, raw in raw_entries.items():
        try:
            entry = json.loads(raw)
        except (TypeError, ValueError):
            continue
        # The hash field IS the report_id (record_report writes entry["report_id"] == field),
        # but entries recorded before that field existed have no report_id in their JSON --
        # always deriving it from the hash key itself (rather than trusting the JSON copy)
        # keeps those old entries addressable by update_report_status/delete_report too.
        entry["report_id"] = field.decode() if isinstance(field, bytes) else field
        entries.append(entry)
    entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
    page = entries[offset:offset + limit]
    for entry in page:
        _present_report(entry)
    return page, offset + len(page) < len(entries)


def get_report(report_id, job_id=None):
    """One report in the same shape get_reports hands out -- for the old
    UI's #report=<id> entry point (openReportFromHash, app.js), which the
    chat page's read-only report view links to as its 面板 switch.

    job_id: present it as if filed on that job instead -- another photo of
    the reported batch, picked in the read-only view's sidebar (or any photo
    of a report filed from 總覽, which has no job of its own). The owner's
    history then fills that job's version_params and times; user_prompt and
    history_image_key belong to the reported photo, so they're dropped."""
    entry = get_report_by_id(report_id)
    if entry is None:
        return None
    entry["report_id"] = report_id
    if job_id:
        entry["job_id"] = job_id
        entry.pop("user_prompt", None)
        entry.pop("history_image_key", None)
        entry.pop("uploaded_at", None)
        entry.pop("modified_at", None)
    return _present_report(entry)


def _present_report(entry):
    entry["has_screenshot"] = bool(entry.pop("screenshot_key", None))
    entry.setdefault("status", "unread")  # entries recorded before this field existed
    # Backfill reports created before case lifecycle timestamps were
    # copied onto the report itself. This also keeps an older report in
    # sync if the user subsequently re-edits the same case.
    history_image_key = entry.pop("history_image_key", None)
    if entry.get("job_id"):
        case = _case_for_report(_history_owner(entry), entry["job_id"], history_image_key, include_versions=True)
        if case:
            entry.setdefault("uploaded_at", case.get("uploaded_at"))
            entry["modified_at"] = case.get("modified_at")
            version = _reported_version(case, entry["job_id"]) or {}
            # The settings the version was submitted with (record_history's
            # params: user_prompt, preset, aspect_ratio, models...).
            if version.get("params"):
                entry["version_params"] = version["params"]
            # Reported in 對話模式 while the photo was only analyzed: the job
            # is its analyze job, with no edit result to open -- 面板模式
            # opens it like a 待美化 case instead (openReportInPanel, app.js).
            if version.get("kind") == "analyze":
                entry["analyze_job_id"] = entry["job_id"]
    return entry
