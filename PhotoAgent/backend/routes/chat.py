"""Chat page API: a session of photos, their edit plans and a message log,
over the same analyze/finish jobs and history the review page uses.

Selecting a photo can enqueue a separate best-effort analyze_preview job.
Adding a session photo enqueues what /analyze does; rendering enqueues
exactly what /continue does, one job
per run, all recorded in history under one action_id. Images and version
lists are read through the existing /result, /source and /history routes.

Every photo is independent: its own status, its own plan. Status moves
analyzing -> ready (or failed), then ready -> rendering -> ready on each
render. A 長寬比 the analysis was not planned for re-plans the composition
right away (pipeline.reframe_stage): ready -> reframing -> ready, after
which the photo reads its analysis from that reframe job. A photo asked to render while still analyzing is queued and starts,
with its plan as it is by then, once its analysis is done. The move out of
analyzing/rendering happens in the status route, when a poll first sees the
jobs finished; `claim` makes sure only one poll does it, so its messages are
posted once.

Acting on every photo at once -- a batch plan edit, or rendering "all" --
waits until no photo is analyzing or rendering. Photos whose analysis failed
never change again, so they are left out of both.
"""

from photo_agent import chat_analysis
import contextvars
import copy
import json
import re
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, Response, current_app, jsonify, request

from aiserver_utils.queue import get_job_info
from backend.routes.api import _apply_review_selections, _parse_and_validate_params, _record_history_safely
from backend.services import chat_queue, chat_session as store, continuation as continuation_svc
from backend.services.timing import job_spans, timed, timeline
from backend.services.account import (
    HISTORY_MAX_LEN,
    attach_case_analysis,
    clear_history,
    delete_history_entry,
    get_history,
    get_history_entry,
    normalize_username,
    set_version_note,
)
from backend.services.compatibility import get_min_compatible_version
from backend.services.put_job import download_bytes, enqueue, get_session_id, lost_job_error
from backend.services.report import get_report_by_id, get_report_chat, get_report_page, is_admin
from backend.services.template_params import get_engine_params_template
from compatibility_version import get_compatibility_version, is_compatible
from image_tools import create_preview_bytes, image_to_data_url
from prompts import COMPOSITION_STATE_KEYS
from photo_agent import capabilities, chat_assistant, chat_jobs, i18n
import config as agent_config
from photo_agent.preview_context import (
    MAX_UPLOAD_BYTES, PREVIEW_CONTEXT_FIELD, source_sha256, preview_versions, validate_preview_binding, validate_preview_context,
)
from photo_agent.selections import validate_selections
from photo_agent.chat_brief import build_brief, person_names, resolve_prompt_faces
from photo_agent.subject_preview import _valid_subject_box
from photo_agent.chat_plan import (
    USER_PROMPT_MAX_LEN,
    apply_plan_patch,
    build_render_form,
    build_selections,
)

chat_bp = Blueprint("chat", __name__, url_prefix="/api/photo-agent/chat")

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MESSAGE_TEXT_MAX_LEN = 4000
# The most messages one post can append (the upload page sends its whole conversation at once).
MESSAGES_POST_MAX = 100
SCREEN_LABELS_MAX = 8
SCREEN_VERSIONS_MAX = 1000
TERMINAL_STATUSES = ("success", "error")
_FACE_WISH_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="chat-face-wishes")
# Render bases: the original photo, or (ENABLE_CHAT_RENDER_FROM_VERSION) one
# finished version's own output -- 接著這張調整, see _render_from_version.
RENDER_BASES = ("original",)
VERSION_BASE = "version"


def _render_bases():
    return RENDER_BASES + ((VERSION_BASE,) if agent_config.ENABLE_CHAT_RENDER_FROM_VERSION else ())
# The next-step sentence a reply applied without asking ends on
# (chat_assistant._next_step), and what replaces it while a new 長寬比 is
# being reframed: nothing can be pressed until reframe_done.
NEXT_STEP_RE = re.compile(r"(?:還想調整其他項目可以點下面的項目，或按|要修改或調整其他項目，可以點下面的美化項目，或按|按下面的)「[^」]+」我就開始。(?:想看整張照片還有哪些地方可以修，也可以按「幫我分析」。)?$")
REFRAMING_NOTE = "正在照新比例規劃構圖，好了就可以接著調整或美化。"
# 「重新構圖：…」, the retouch_recipe item a reframe replaces (util.ts REFRAME_RE).
REFRAME_ITEM_RE = re.compile(r"^重新構圖\s*[:：]")
# A fixed W:H 長寬比 can only be had by reframing, so its 重新構圖 item stays on.
FIXED_RATIO_RE = re.compile(r"^\d+(?:\.\d+)?:\d+(?:\.\d+)?$")


class _RequestError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


@chat_bp.errorhandler(_RequestError)
@chat_bp.errorhandler(continuation_svc.ContinuationError)
def _request_error(exc):
    return jsonify({"error": exc.message}), exc.status


def _debug():
    return bool(current_app.config.get("DEBUG_UI", False))


def _user(raw):
    try:
        return normalize_username(raw)
    except ValueError as exc:
        raise _RequestError(str(exc))


def _body():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise _RequestError("request body must be a JSON object")
    return body


def _session(user, session_id):
    session = store.load_session(user, session_id) if SESSION_ID_RE.match(session_id) else None
    if session is None:
        raise _RequestError("Session not found", 404)
    return session


def _photo(session, photo_id):
    for photo in session["photos"]:
        if photo["photo_id"] == photo_id:
            return photo
    raise _RequestError("Photo not found", 404)


# A finished job's result file never changes (its key carries the job id), yet
# every ask, plan and brief re-reads it from S3. Keeping the raw bytes, not the
# parsed dict, hands each caller a fresh copy it may mutate.
_ANALYSIS_CACHE_SIZE = 256
_analysis_bytes = OrderedDict()
_analysis_bytes_lock = threading.Lock()


def _download_analysis(key, job_id):
    with _analysis_bytes_lock:
        if key in _analysis_bytes:
            _analysis_bytes.move_to_end(key)
            return _analysis_bytes[key], None
    data, error = download_bytes(key, job_id)
    if error or data is None:
        return data, error
    with _analysis_bytes_lock:
        _analysis_bytes[key] = data
        while len(_analysis_bytes) > _ANALYSIS_CACHE_SIZE:
            _analysis_bytes.popitem(last=False)
    return data, None


def _load_analysis(job_id):
    """(analysis, None) or (None, error) for a finished analyze job -- the
    same lookup /analyze/result does.

    Also a one-shot analyze+edit job (面板模式 with review skipped -- an old
    case's only job): its analysis rides inside the first run's result
    metadata, the second of [image, metadata] where an analyze job keys the
    analysis alone."""
    info = get_job_info(job_id, keyToGet=["status", "error", "0_res0"])
    if info is None:
        return None, "Job not found"
    if info.get("status") != "success":
        return None, info.get("error") or f"analysis is {info.get('status') or 'pending'}"
    res0 = info.get("0_res0") or {}
    runs = res0.get("runs")
    result_keys = ((runs[0] if runs else res0).get("cusKeys")) or []
    if not result_keys:
        return None, "job succeeded but analysis key is missing"
    combined = len(result_keys) > 1
    analysis_bytes, download_error = _download_analysis(result_keys[1 if combined else 0], job_id)
    if download_error or analysis_bytes is None:
        return None, f"failed to download analysis: {download_error}"
    analysis = json.loads(analysis_bytes)
    if combined:
        analysis = analysis.get("analysis") if isinstance(analysis, dict) else None
        if not analysis:
            return None, "job result carries no analysis"
    return analysis, None


def _analysis_job_id(photo):
    """The job holding the analysis a photo's plan is read against: its
    analyze job, or the last reframe that re-planned its composition. The
    source image and the history row stay on analyze_job_id."""
    return photo.get("analysis_job_id") or photo["analyze_job_id"]


def _planned_for(analysis):
    """The 長寬比 an analysis's composition was planned for."""
    return str(analysis.get("_aspect_ratio_pref_at_analysis") or "auto").strip().lower()


def _recipe_items(analysis):
    """retouch_recipe as the chat page lists it (util.ts recipeItems): what
    the plan's recipe_checked indexes."""
    items = analysis.get("retouch_recipe")
    return [x for x in items if isinstance(x, str) and x.strip()] if isinstance(items, list) else []


# --- sessions --------------------------------------------------------------

@chat_bp.post("/sessions")
def create_session():
    body = _body()
    user = _user(body.get("user"))
    batch_id = body.get("batch_id")
    if batch_id is not None and not (isinstance(batch_id, str) and SESSION_ID_RE.match(batch_id)):
        raise _RequestError("invalid batch_id")
    client_key = _client_key_param(body.get("client_key"))
    return jsonify(store.create_session(user, batch_id=batch_id, client_key=client_key)), 201


def _client_key_param(value):
    """The optional key a retried request repeats (store.CLIENT_KEY_TTL_SECONDS)."""
    if value in (None, ""):
        return None
    if not (isinstance(value, str) and SESSION_ID_RE.match(value)):
        raise _RequestError("invalid client_key")
    return value


# Live sessions read to tell which history cases already have one.
LISTED_SESSIONS_MAX = 100


@chat_bp.get("/sessions")
def list_sessions():
    """Live sessions, and every history case none of them holds -- a photo
    edited only in 面板模式, or one whose session has expired -- as one list,
    newest first. A case row has no session_id; opening it goes through
    /sessions/from-case."""
    user = _user(request.args.get("user"))
    # Up to the whole merged list: 面板模式's 最近照片 pages through every
    # case, so a cap here would hide older ones only on the chat side.
    limit = max(1, min(request.args.get("limit", 20, type=int), LISTED_SESSIONS_MAX + HISTORY_MAX_LEN))
    history = get_history(user, limit=HISTORY_MAX_LEN)
    case_ts = {e["image_key"]: e.get("ts") or 0 for e in history}
    sessions = [_listed(user, s, case_ts) for s in store.list_sessions(user, limit=LISTED_SESSIONS_MAX)]
    covered = {case_id for s in sessions for case_id in s["case_ids"]}
    cases = [_case_summary(e) for e in history if e["image_key"] not in covered and _opens_in_chat(e)]
    rows = sorted(_batched(sessions) + cases, key=lambda r: r["updated_at"] or 0, reverse=True)
    return jsonify({"sessions": rows[:limit]})


# A tile badge that only a running job keeps up (chat_session._summary).
BUSY_SESSION_STATUSES = ("analyzing", "rendering")


def _batched(sessions):
    """The sessions of one multi-photo upload (same batch_id) as one row,
    the way a session of several photos is one tile. Its session_id is the
    one worked on last, which is what opening the tile shows; session_ids
    lists them all in upload order, for the batch sidebar and for delete."""
    groups, rows = {}, []
    for s in sessions:
        if s.get("batch_id"):
            groups.setdefault(s["batch_id"], []).append(s)
        else:
            rows.append(s)
    for members in groups.values():
        rows.append(members[0] if len(members) == 1 else _batch_row(members))
    return rows


def _batch_status(statuses):
    """As chat_session._summary ranks the photos of one session."""
    for state in ("rendering", "analyzing"):
        if state in statuses:
            return state
    if all(s == "failed" for s in statuses):
        return "failed"
    return "done" if "done" in statuses else "ready"


def _batch_row(members):
    members = sorted(members, key=lambda s: s["created_at"] or 0)
    latest = max(members, key=lambda s: s["updated_at"] or 0)
    shots = []
    for s in members:
        shots += s["thumbnails"] or [s["thumbnail_result"] or {"job_id": s["thumbnail_job_id"], "run": None}]
    newest = next((s["thumbnail_result"] for s in sorted(members, key=lambda s: s["updated_at"] or 0, reverse=True)
                   if s["thumbnail_result"]), None)
    return {
        **latest,
        "session_ids": [s["session_id"] for s in members],
        "created_at": members[0]["created_at"],
        "photo_count": sum(s["photo_count"] for s in members),
        "thumbnail_job_id": members[0]["thumbnail_job_id"],
        "thumbnail_result": newest,
        "thumbnails": [t for t in shots if t["job_id"]][:4],
        "status": _batch_status([s["status"] for s in members]),
        "rendered_count": sum(s["rendered_count"] for s in members),
        "case_ids": [c for s in members for c in s["case_ids"]],
    }


def _listed(user, row, case_ts):
    """A session's row as of now. A busy one is advanced (_landed); one
    whose case got a version in 面板模式 after the session last changed is
    synced from history first, so its tile shows that edit -- running or
    done -- without the session being opened."""
    if row["status"] in BUSY_SESSION_STATUSES:
        return _landed(user, row)
    if not any(case_ts.get(c, 0) > (row["updated_at"] or 0) for c in row["case_ids"]):
        return row
    session = store.load_session(user, row["session_id"])
    if session is None:
        return row
    _sync_from_history(user, row["session_id"], session)
    return store.session_summary(user, row["session_id"]) or row


def _landed(user, row):
    """A busy session's row once whatever it was waiting on has landed --
    the photo state only moves when something advances it, so a render that
    finished after the user left would otherwise stay 美化中 on its tile
    until the session is opened."""
    if store.load_session(user, row["session_id"]) is None:
        return row
    _advance_session(user, row["session_id"])
    return store.session_summary(user, row["session_id"]) or row


def _opens_in_chat(entry):
    """Only local-path cases: the chat page reads images through /result
    and /source, which an AIPoC-path job never wrote to."""
    return (entry.get("path") or "local") == "local" and bool(entry.get("runs"))


def _case_summary(entry):
    """A history case shaped like a session row (chat_session._summary)."""
    edited = bool(entry.get("modified_at"))
    runs = entry.get("runs") or []
    return {
        "session_id": None,
        "case_id": entry["image_key"],
        "created_at": entry.get("uploaded_at") or entry.get("ts"),
        "updated_at": entry.get("ts"),
        "photo_count": 1,
        # Any job of the case shares its source, so the latest one serves.
        "thumbnail_job_id": runs[0] if runs else None,
        "thumbnail_result": {"job_id": runs[0], "run": 1} if edited and runs else None,
        "status": "done" if edited else "ready",
        "rendered_count": 1 if edited else 0,
        "case_ids": [entry["image_key"]],
    }


# A row of the list that is a history case with no session: `case:<case_id>`
# (home.tsx CASE_PREFIX).
CASE_ROW_PREFIX = "case:"
# The most rows one delete can name: the whole list.
DELETE_ROWS_MAX = LISTED_SESSIONS_MAX + HISTORY_MAX_LEN


@chat_bp.post("/sessions/delete")
def delete_sessions():
    """Removes rows of the list the way 面板模式's 最近照片 removes its cases:
    {rows: [...]} names sessions and `case:<case_id>` rows, {all: true}
    empties the list. A session goes with the history cases of its photos,
    so 面板模式 stops listing them too and the photo does not come back here
    as a case row. Only the records go; jobs and their images stay, as in
    面板模式."""
    body = _body()
    user = _user(body.get("user"))
    if body.get("all") is True:
        store.delete_all_sessions(user)
        clear_history(user)
        return jsonify({"deleted": "all"})
    rows = body.get("rows")
    if (not isinstance(rows, list) or not rows or len(rows) > DELETE_ROWS_MAX
            or not all(isinstance(r, str) and r for r in rows)):
        return jsonify({"error": "rows must be a non-empty list of row ids"}), 400
    # All checked before any is removed, so a bad id deletes nothing.
    bad = [r for r in rows if not r.startswith(CASE_ROW_PREFIX) and not SESSION_ID_RE.match(r)]
    if bad:
        return jsonify({"error": f"invalid session id: {bad[0]}"}), 400
    for row in rows:
        if row.startswith(CASE_ROW_PREFIX):
            delete_history_entry(user, row[len(CASE_ROW_PREFIX):])
            continue
        session = store.load_session(user, row)
        for photo in (session or {}).get("photos", []):
            if photo.get("case_id"):
                delete_history_entry(user, photo["case_id"])
        # A batch is deleted as one row, every session of it at once.
        if (session or {}).get("batch_id"):
            store.delete_batch_overview(user, session["batch_id"])
        store.delete_session(user, row)
    return jsonify({"deleted": rows})


def _batch_id(batch_id):
    if not SESSION_ID_RE.match(batch_id):
        raise _RequestError("invalid batch_id")
    return batch_id


@chat_bp.get("/batches/<batch_id>/overview")
def get_batch_overview(batch_id):
    """The overview conversation of a multi-photo upload, as the page last
    saved it ({"overview": null} if it never did, or it expired)."""
    user = _user(request.args.get("user"))
    return jsonify({"overview": store.load_batch_overview(user, _batch_id(batch_id))})


@chat_bp.put("/batches/<batch_id>/overview")
def put_batch_overview(batch_id):
    """{overview: {...}} replaces what is kept for this batch. The page owns
    its shape; it only has to be a JSON object of bounded size."""
    body = _body()
    user = _user(body.get("user"))
    batch_id = _batch_id(batch_id)
    overview = body.get("overview")
    if not isinstance(overview, dict):
        raise _RequestError("overview must be a JSON object")
    if len(json.dumps(overview)) > store.OVERVIEW_MAX_BYTES:
        raise _RequestError("overview too large", 413)
    store.save_batch_overview(user, batch_id, overview)
    return jsonify({"ok": True})


@chat_bp.post("/sessions/from-case")
def session_from_case():
    """Open a history case in the chat page: its live session if it still
    has one, else a new one-photo session rebuilt from history -- the
    photo, its analysis and every version, without the conversation that
    made them (which expired with the old session, or never existed for a
    case made in 面板模式).

    409 when there is nothing for the chat page to work from: a case whose
    analysis is gone or that ran on the AIPoC path. The client then opens
    it in 面板模式 instead.
    """
    body = _body()
    user = _user(body.get("user"))
    case_id = body.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise _RequestError("case_id is required")
    existing = store.find_session_for_case(user, case_id)
    if existing:
        return jsonify({"session_id": existing, "created": False})

    entry = get_history_entry(user, case_id)
    if not entry:
        raise _RequestError("case not found", 404)
    if not _opens_in_chat(entry):
        raise _RequestError("unsupported_path", 409)
    versions = entry.get("versions") or []
    # A case made in one go (面板模式 with review skipped) has no analyze
    # version: its first edit's job did the analysis too (_load_analysis).
    analyze = (next((v for v in reversed(versions) if v["kind"] == "analyze" and v["runs"]), None)
               or next((v for v in versions if v["runs"] and (v.get("path") or "local") == "local"), None))
    if analyze is None:
        raise _RequestError("no_analysis", 409)
    job_id = analyze["runs"][0]
    info = get_job_info(job_id, keyToGet=["status", "error", "srcKeys"])
    if not info or not info.get("srcKeys"):
        raise _RequestError("no_analysis", 409)
    if analyze["kind"] != "analyze" and _load_analysis(job_id)[1]:
        raise _RequestError("no_analysis", 409)

    session = store.create_session(user)
    session_id = session["session_id"]
    photo = {
        "photo_id": f"p_{uuid.uuid4().hex[:12]}",
        "filename": None,
        "case_id": case_id,
        "src_keys": list(info["srcKeys"]),
        "analyze_job_id": job_id,
        "status": "analyzing",
        "error": None,
        "pending_render": None,
        "queued_render": None,
        "active_version": None,
    }
    store.add_photos(user, session_id, session, [photo])
    store.append_messages(user, session_id, [store.make_message(
        "user", card={"type": "photos_added", "photo_ids": [photo["photo_id"]]})])
    # A finished analysis lands the way a status poll would land it, so the
    # page opens on the same analysis card as a fresh upload; one still
    # running is left to the polls.
    session = store.load_session(user, session_id)
    photo = session["photos"][0]
    store.append_messages(user, session_id, _advance_analyzing(user, session_id, photo, {}, rebuilt=True))
    _sync_from_history(user, session_id, store.load_session(user, session_id))
    return jsonify({"session_id": session_id, "created": True}), 201


def _panel_continuation(version):
    """The fields a chat continuation's render_done card carries, for a
    面板模式 version made from another result, so the page shows it as
    由第 N 版調整 too. Empty for one made from the original."""
    params = version.get("params") or {}
    if params.get("base") != VERSION_BASE or not isinstance(params.get("source_result"), dict):
        return {}
    return {"base": VERSION_BASE, "source_result": params["source_result"], "prompt": params.get("user_prompt"),
            "style_options": (params.get("analysis") or {}).get("style_options") or {}}


def _sync_from_history(user, session_id, session):
    """Post the versions a session's photos got outside it as render_done
    cards, so the chat page lists them like its own -- edits made in
    面板模式, and those of an expired session a photo was reopened from.
    A version is posted once (claimed by its version_id, like a chat
    render). One still running is followed as the photo's pending render,
    so the page shows it rendering and the status polls post it."""
    statuses, messages, posted = {}, [], None
    for photo in session["photos"]:
        if photo["status"] != "ready":
            continue
        entry = get_history_entry(user, photo["case_id"]) or {}
        edits = [v for v in entry.get("versions") or []
                 if v["kind"] != "analyze" and v["runs"] and (v.get("path") or "local") == "local"]
        if edits and posted is None:
            posted = {(m.get("card") or {}).get("version_id")
                      for m in store.get_messages(user, session_id, limit=store.MESSAGES_MAX_LEN)}
        changed = False
        for version in edits:
            version_id = version["version_id"]
            if version_id in posted:
                continue
            jobs = {job_id: _job_status(job_id, statuses) for job_id in version["runs"]}
            if any(j["status"] not in TERMINAL_STATUSES for j in jobs.values()):
                if any(j.get("expired") for j in jobs.values()):
                    continue  # a job that has expired out of Redis never finishes
                photo["status"] = "rendering"
                photo["pending_render"] = {"action_id": version_id, "job_ids": list(version["runs"]),
                                           "based_on": version.get("based_on"), "base": "custom", "plan": None,
                                           "panel_continuation": _panel_continuation(version)}
                changed = True
                break
            # A one-shot case's first version is also the photo's analyze
            # job, whose own token _advance_analyzing already took.
            token = f"version:{version_id}" if version_id == photo["analyze_job_id"] else version_id
            if not store.claim(user, session_id, token):
                continue
            succeeded = [job_id for job_id, j in jobs.items() if j["status"] == "success"]
            if not succeeded:
                continue
            photo["active_version"] = store.active_version(version_id, succeeded[0])
            changed = True
            messages += _land_panel_version(user, session_id, photo, entry, statuses, {
                "type": "render_done", "photo_id": photo["photo_id"], "version_id": version_id,
                "job_ids": succeeded, "failed_job_ids": [j for j in version["runs"] if j not in succeeded],
                "via": "custom", **_panel_continuation(version)}, version.get("based_on"))
        landed = _take_case_analysis(user, session_id, photo, entry, statuses)
        if landed is not None:
            changed = True
            messages.extend(landed)
        if changed:
            store.save_photo_state(user, session_id, photo)
    store.append_messages(user, session_id, messages)


def _adopt_case_analysis(photo, entry, statuses):
    """A 「只照需求改」 photo analyzed from 自訂模式 (api.analyze_existing):
    follow that job as this photo's reanalysis, so its brief lands the way
    the chat's own 「幫我分析」 does -- right away when it's already done
    (_sync_from_history), else from the status polls. One that failed is
    left alone -- the photo keeps working off its stub, as after a failed
    chat-side analysis."""
    job_id = entry.get("analysis_job_id")
    if (not job_id or not photo.get("analysis_skipped") or photo["status"] != "ready"
            or job_id == photo.get("analysis_job_id")):
        return False
    job = _job_status(job_id, statuses)
    if job.get("expired") or job["status"] == "error":
        return False
    photo["analysis_job_id"] = job_id
    photo["reanalysis"] = "custom"
    photo["status"] = "analyzing"
    photo["planned_ratio"] = None
    photo["error"] = None
    return True


def _take_case_analysis(user, session_id, photo, entry, statuses):
    """_adopt_case_analysis, landing the analysis right away when it's
    already done rather than show the photo as 分析中 until the next status
    poll. The messages that posts, or None when nothing was adopted."""
    if not _adopt_case_analysis(photo, entry, statuses):
        return None
    job_id = photo["analysis_job_id"]
    job = _job_status(job_id, statuses)
    if job["status"] not in TERMINAL_STATUSES or not store.claim(user, session_id, _reanalysis_token(job_id)):
        return []
    with i18n.use(job.get("lang", i18n.current())):
        return _advance_reanalysis(user, session_id, photo, job)


def _land_panel_version(user, session_id, photo, entry, statuses, card, based_on=None):
    """The messages a 面板模式 version's render_done `card` goes up with. One
    from 一鍵美化 analyzed the photo in the same job: it lands as a chat 一鍵美化
    does -- the analysis card with its brief, then the result with its plan, so
    詳細解說 writes the note under it -- and the photo's plan takes on what that
    version was made with (its 個人化需求 and 風格, beautifying the whole
    photo), so 再美化 carries on from it."""
    message = store.make_message("assistant", card=card)
    if entry.get("analysis_job_id") != card["version_id"]:
        return [message]
    landed = _take_case_analysis(user, session_id, photo, entry, statuses)
    if landed is None or photo.get("analysis_skipped"):
        return [message, *(landed or [])]
    version = next((v for v in entry.get("versions") or [] if v["version_id"] == card["version_id"]), {})
    params = version.get("params") or {}
    # Dropping the unset picks, as the worker's apply_selections does.
    styles = {group: value for group, value in ((params.get("selections") or {}).get("options") or {}).items()
              if value and value not in capabilities.STYLE_UNSET_VALUES}
    photo["plan"].update({"overwrite_prompt": False, "user_prompt": params.get("user_prompt") or "",
                          "style_options": styles})
    store.save_photo_plan(user, session_id, photo["photo_id"], photo["plan"])
    plan = copy.deepcopy(photo["plan"])
    card["plan"] = plan
    if plan.get("detailed_explanation"):
        photo["pending_note"] = {"version_id": card["version_id"], "message_id": message["id"],
                                 "render_job": card["job_ids"][0], "based_on": based_on,
                                 "plan": plan, "lang": i18n.current()}
    ready = store.make_message("assistant", card={"type": "analysis_ready", "photo_id": photo["photo_id"]})
    return [ready, *landed, message]


def _reanalysis_token(job_id):
    """What landing a reanalysis claims. Not the bare job id: 面板模式's
    analyze_and_finish job is the edit's version too, whose render_done
    claims that (_sync_from_history, _advance_rendering)."""
    return f"reanalysis:{job_id}"


@chat_bp.get("/sessions/<session_id>")
def get_session(session_id):
    user = _user(request.args.get("user"))
    _session(user, session_id)
    _advance_session(user, session_id)
    _sync_from_history(user, session_id, _session(user, session_id))
    session = _session(user, session_id)
    limit = request.args.get("messages_limit", 100, type=int)
    return jsonify({**session, "messages": store.get_messages(user, session_id, limit=max(1, min(limit, store.MESSAGES_MAX_LEN)))})


# What the read-only view shows of the report itself.
REPORT_VIEW_FIELDS = ("report_id", "user", "owner", "ts", "note", "job_id", "session_id", "photo_id", "batch_id", "status", "page")


def _reported_chat():
    """(chat, report) for the admin's read-only view of the chat a report was
    filed from: chat_session.report_chat's {sessions, overview, live}. By
    default the copy kept when the report was filed (save_report_chat), so
    what the user did afterwards doesn't show; ?live=1 -- or a report filed
    before copies were kept -- reads the owner's conversation as it is now.
    Read as-is either way: get_session's _advance_photos/_sync_from_history
    write into the owner's session, and an admin looking must not change what
    the user sees next."""
    if not is_admin(_user(request.args.get("user"))):
        raise _RequestError("admin only", 403)
    report_id = request.args.get("report") or ""
    report = get_report_by_id(report_id)
    owner = (report or {}).get("owner")
    session_id, batch_id = (report or {}).get("session_id"), (report or {}).get("batch_id")
    if not owner or not (session_id or batch_id):
        raise _RequestError("這筆回報沒有對應的對話", 404)
    report = {**report, "report_id": report_id}
    kept = None if request.args.get("live") else get_report_chat(report_id)
    if kept is not None:
        chat = {**kept, "live": False}
    else:
        if session_id and not SESSION_ID_RE.match(session_id) or batch_id and not SESSION_ID_RE.match(batch_id):
            raise _RequestError("這筆回報沒有對應的對話", 404)
        chat = {**store.report_chat(owner, session_id, batch_id), "live": True}
    if not chat["sessions"]:
        raise _RequestError("這筆回報沒有對應的對話", 404)
    return chat, report


@chat_bp.get("/report-session")
def report_session():
    try:
        chat, report = _reported_chat()
    except _RequestError as exc:
        # Filed from 首頁 or the upload page (record_report's "page"): no
        # conversation, or a batch whose photos never got one -- the view
        # shows the page as it was instead (save_report_page). A batch that
        # did get going still opens as the conversation above.
        report = get_report_by_id(request.args.get("report") or "") if exc.status == 404 else None
        if not (report or {}).get("page"):
            raise
        report = {**report, "report_id": request.args.get("report")}
        return jsonify({
            "sessions": [], "overview": None, "live": False, "kept": True,
            "page": get_report_page(report["report_id"]) or {"page": report["page"]},
            "report": {key: report.get(key) for key in REPORT_VIEW_FIELDS},
        })
    return jsonify({
        **chat,
        # Whether there is a report-time copy to switch back to from live.
        "kept": not chat["live"] or get_report_chat(report["report_id"]) is not None,
        "report": {key: report.get(key) for key in REPORT_VIEW_FIELDS},
    })


@chat_bp.get("/report-session/analysis")
def report_session_analysis():
    chat, _ = _reported_chat()
    session = next((s for s in chat["sessions"] if s["session_id"] == request.args.get("session_id")), None)
    if session is None:
        raise _RequestError("Session not found", 404)
    photo = _photo(session, request.args.get("photo_id") or "")
    analysis, error = _load_analysis(_analysis_job_id(photo))
    if error:
        return jsonify({"status": "error" if photo["status"] == "failed" else photo["status"], "error": error})
    return jsonify({"status": "success", "result": {"analysis": analysis}})


def _posted_message(body):
    role = body.get("role", "user")
    text = body.get("text", "")
    card = body.get("card")
    if role not in ("user", "assistant"):
        raise _RequestError("role must be 'user' or 'assistant'")
    if not isinstance(text, str) or len(text) > MESSAGE_TEXT_MAX_LEN:
        raise _RequestError(f"text must be a string of at most {MESSAGE_TEXT_MAX_LEN} characters")
    if card is not None and not isinstance(card, dict):
        raise _RequestError("card must be a JSON object")
    if not text and card is None:
        raise _RequestError("text or card is required")
    return store.make_message(role, text, card)


def _bind_message_thumbnails(messages, user, session_id):
    session = _session(user, session_id)
    for message in messages:
        card = message.get('card') or {}
        if not card.get('pre') or not isinstance(card.get('faces'), list):
            continue
        referenced = [f for f in card['faces'] if isinstance(f, dict) and 'preview_person_id' in f]
        if not referenced:
            continue
        for face in referenced:
            face.pop('thumbnail_box', None)
        try:
            photo = _photo(session, card.get('photo_id'))
            info = get_job_info(photo['analyze_job_id'], keyToGet=['acts', 'srcKeys'])
            acts = info['acts'] if isinstance(info['acts'], list) else json.loads(info['acts'])
            context = acts[0]['params'].get('precomputed_preview')
            if not context or context.get('format_version') != 3:
                continue
            context = validate_preview_context(context, src_keys=photo['src_keys'])
            if context['binding']['user'] != user:
                continue
            people = {p['preview_person_id']: p for p in context['preview_people']}
            for face in referenced:
                person = people.get(face['preview_person_id']) if isinstance(face['preview_person_id'], str) else None
                if person:
                    face['box'] = person['box']
                    face['thumbnail_box'] = person['thumbnail_box']
        except (_RequestError, ValueError, KeyError, TypeError):
            # Pending metadata may be temporarily unavailable. The UI can
            # resolve the same person ID from the formal trusted analysis later.
            continue


@chat_bp.post("/sessions/<session_id>/messages")
def post_message(session_id):
    """One message, or with `messages` a list of them appended in one go --
    the upload page's conversation, carried into the session it starts."""
    body = _body()
    user = _user(body.get("user"))
    _session(user, session_id)
    batch = body.get("messages")
    if batch is None:
        message = _posted_message(body)
        _bind_message_thumbnails([message], user, session_id)
        store.append_messages(user, session_id, [message])
        return jsonify(message), 201
    if not isinstance(batch, list) or not 0 < len(batch) <= MESSAGES_POST_MAX \
            or not all(isinstance(m, dict) for m in batch):
        raise _RequestError(f"messages must be a list of 1 to {MESSAGES_POST_MAX} JSON objects")
    messages = [_posted_message(m) for m in batch]
    _bind_message_thumbnails(messages, user, session_id)
    store.append_messages(user, session_id, messages)
    return jsonify({"messages": messages}), 201


@chat_bp.put("/sessions/<session_id>/messages/<message_id>")
def replace_message(session_id, message_id):
    """Only the newest message, and only one the user posted, can change."""
    body = _body()
    user = _user(body.get("user"))
    _session(user, session_id)
    card = body.get("card")
    if not isinstance(card, dict):
        raise _RequestError("card must be a JSON object")
    last = store.get_messages(user, session_id, limit=1)
    if not last or last[0]["id"] != message_id or last[0]["role"] != "user":
        return jsonify({"error": "only the newest user message can be replaced"}), 409
    _bind_message_thumbnails([{"card": card}], user, session_id)
    message = store.replace_last_message(user, session_id, message_id, card)
    if message is None:
        return jsonify({"error": "only the newest user message can be replaced"}), 409
    return jsonify(message)


# --- photos ----------------------------------------------------------------

def _owned_preview(user, preview_job_id):
    """Resolve only a server-created preview job owned by this Chat user.

    Matches existing normalized-user access semantics (not a new auth system).
    Neither client-supplied S3 keys nor a preview JSON body are trusted.
    """
    if not isinstance(preview_job_id, str) or not SESSION_ID_RE.fullmatch(preview_job_id):
        raise _RequestError("Preview not found", 404)
    info = get_job_info(preview_job_id, keyToGet=["status", "acts", "srcKeys", "dstKeys", "0_res0", "endTs", PREVIEW_CONTEXT_FIELD])
    try:
        params = info["acts"][0]["params"]
        binding = params["preview_binding"]
        if params["stage"] != "analyze_preview" or binding["user"] != user or binding["preview_job_id"] != preview_job_id:
            raise ValueError("wrong_preview_owner_or_type")
        binding = validate_preview_binding(binding)
        keys = info["srcKeys"]
        if not isinstance(keys, list) or len(keys) != 1 or not isinstance(keys[0], str):
            raise ValueError("invalid_preview_source")
    except (KeyError, TypeError, IndexError, ValueError):
        raise _RequestError("Preview not found or expired", 404)
    return info, binding


def _preview_snapshot(info, binding):
    """Use finished 2.0 data even while artifact upload is running/failed.

    UI and formal submission resolve the same server-owned, validated snapshot;
    neither accepts a context supplied by the browser. Old workers still use S3.
    """
    raw = info.get(PREVIEW_CONTEXT_FIELD)
    if raw is not None:
        try:
            snapshot = validate_preview_context(raw, src_keys=info["srcKeys"], binding=binding)
            timing = raw.get("timing", {})
            return snapshot, timing if isinstance(timing, dict) else {}
        except (ValueError, TypeError, KeyError):
            current_app.logger.warning("Invalid preview snapshot job=%s", binding["preview_job_id"])
    return _preview_artifact(info, binding)


def _preview_artifact(info, binding):
    if info.get("status") != "success":
        return None
    try:
        key = info["0_res0"]["cusKeys"][0]
        dst = info["dstKeys"][0]
        expected = (dst.rsplit('/', 1)[0] + '/' if '/' in dst else '') + '0_res0_preview.json'
        if key != expected:
            raise ValueError("wrong_preview_artifact")
        data, error = download_bytes(key, binding["preview_job_id"])
        if error or not data or len(data) > 1024 * 1024:
            raise ValueError("preview_artifact_unavailable")
        raw = json.loads(data)
        snapshot = validate_preview_context(raw, src_keys=info["srcKeys"], binding=binding)
        return snapshot, raw.get("timing", {})
    except Exception:
        current_app.logger.warning("Preview artifact unavailable job=%s", binding["preview_job_id"])
        raise _RequestError(i18n.tr("人物預覽暫時無法讀取，仍可進行分析。"), 502)


@chat_bp.post("/previews")
def create_preview():
    if not agent_config.ENABLE_CHAT_SUBJECT_PREVIEW:
        raise _RequestError("Preview disabled", 404)
    user = _user(request.form.get("user"))
    image = request.files.get("image")
    if image is None or not image.filename:
        raise _RequestError("image file is required")
    data = image.read(MAX_UPLOAD_BYTES + 1)
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise _RequestError(i18n.tr("照片大小需介於 1 byte 與 30 MB。"), 413)
    try:
        from image_tools import get_image_dimensions
        get_image_dimensions(data)  # existing HEIC/EXIF decoder, not face detection
    except Exception:
        raise _RequestError(i18n.tr("無法讀取照片，請使用有效的圖片檔案。"), 422)
    params, error = _parse_and_validate_params(request.form)
    if error:
        raise _RequestError(error)
    job_id = get_session_id()
    binding = {"preview_job_id": job_id, "user": user, "source_sha256": source_sha256(data),
        "created_at": time.time(), "versions": preview_versions(), "analysis_model": params["analysis_model"]}
    params.update(stage="analyze_preview", preview_binding=binding, preview_format_version=3)
    engine = get_engine_params_template()
    engine["acts"][0]["params"] = params
    try:
        enqueue(engine, src_content=[data], job_id=job_id)
    except Exception:
        raise _RequestError(i18n.tr("無法開始人物預覽，仍可稍後進行分析。"), 503)
    current_app.logger.info("Preview selected_to_enqueue_seconds=%.4f job=%s",
                            time.time() - binding["created_at"], job_id)
    return jsonify({"preview_job_id": job_id, "source_sha256": binding["source_sha256"],
                    "status": "queued", "provisional": True}), 202


@chat_bp.get("/previews/<preview_job_id>")
def preview_status(preview_job_id):
    if not agent_config.ENABLE_CHAT_SUBJECT_PREVIEW:
        raise _RequestError("Preview disabled", 404)
    info, binding = _owned_preview(_user(request.args.get("user")), preview_job_id)
    status = info.get("status") or "queued"
    body = {"preview_job_id": preview_job_id, "source_sha256": binding["source_sha256"],
            "status": status, "provisional": True}
    artifact = _preview_snapshot(info, binding)
    if artifact is None:
        if status == "error":
            return jsonify({**body, "state": "error", "message": i18n.tr("人物預覽未完成，仍可進行分析。")})
        return jsonify({**body, "state": "pending", "message": i18n.tr("正在初步辨識照片中的人物。")})
    snapshot, timing = artifact
    preview = snapshot["subject_preview"]
    classified = {d["detection_index"]: d for d in preview.get("detections", [])}
    faces = [{**d, "label": classified.get(d["detection_index"], {}).get("label", ""),
              "role": classified.get(d["detection_index"], {}).get("role", "uncertain")}
             for d in snapshot["detections"]]
    # No API/model envelopes, diagnostic output or internal S3 paths go to UI.
    end_ts = info.get("endTs")
    return jsonify({**body, "state": preview["state"], "faces": faces,
        "format_version": snapshot["format_version"], "people": snapshot.get("preview_people", []),
        "augmentation_state": preview.get("augmentation_state"),
        "low_confidence_state": snapshot.get("low_confidence_state"),
        "reusable": True,
        "coordinate_space": snapshot["coordinate_space"],
        "completed_at": timing.get("completed_at") or (end_ts / 1000 if type(end_ts) in (int, float) else None),
        "message": i18n.tr("人物初步辨識完成。") if preview["state"] == "ready" else i18n.tr("人物預覽暫時不可用，仍可進行分析。")})


@chat_bp.get("/previews/<preview_job_id>/image")
def preview_image(preview_job_id):
    """Owned EXIF-corrected source for browser crops, including HEIC."""
    if not agent_config.ENABLE_CHAT_SUBJECT_PREVIEW:
        raise _RequestError("Preview disabled", 404)
    info, _ = _owned_preview(_user(request.args.get("user")), preview_job_id)
    try:
        from image_tools import create_preview_bytes
        data, error = download_bytes(info["srcKeys"][0], preview_job_id)
        if error or not data:
            raise ValueError("source_unavailable")
        return Response(create_preview_bytes(data), mimetype="image/jpeg")
    except Exception:
        raise _RequestError(i18n.tr("無法讀取人物預覽圖片。"), 502)

@chat_bp.post("/sessions/<session_id>/photos")
def add_photos(session_id):
    user = _user(request.form.get("user"))
    session = _session(user, session_id)
    images = [img for key in ("image", "image[]") for img in request.files.getlist(key) if img and img.filename]
    if not images:
        raise _RequestError("at least one image file is required")
    client_key = _client_key_param(request.form.get("client_key"))
    if client_key is None:
        return _add_photos(user, session_id, session, images)
    # A retry of an upload the page gave up waiting for: hand back what that
    # try made, or say it's still going, rather than add the photo twice.
    if len(images) != 1:
        raise _RequestError("client_key goes with exactly one image")
    done = _photo_with_client_key(session, client_key)
    if done is not None:
        return jsonify({"photos": [done], "errors": []}), 200
    if not store.claim_upload(user, session_id, client_key):
        raise _RequestError("upload_in_progress", 409)
    try:
        # The other try may have finished between the look above and the claim.
        session = _session(user, session_id)
        done = _photo_with_client_key(session, client_key)
        if done is not None:
            return jsonify({"photos": [done], "errors": []}), 200
        return _add_photos(user, session_id, session, images, client_key)
    finally:
        store.release_upload(user, session_id, client_key)


def _photo_with_client_key(session, client_key):
    return next((p for p in session["photos"] if p.get("client_key") == client_key), None)


def _add_photos(user, session_id, session, images, client_key=None):
    if len(session["photos"]) + len(images) > store.MAX_PHOTOS_PER_SESSION:
        raise _RequestError(f"a session holds at most {store.MAX_PHOTOS_PER_SESSION} photos")

    params, error = _parse_and_validate_params(build_render_form(session["defaults"], "analyze"))
    if error:
        raise _RequestError(error)
    params["stage"] = "analyze"
    # Scene-only 「只照需求改」 can skip analysis.
    skip = bool(session["defaults"].get("overwrite_prompt")
                and (session["defaults"].get("user_prompt") or "").strip())
    if skip:
        params["skip_analysis"] = True

    photos, errors = [], []
    for image in images:
        try:
            engine_params = get_engine_params_template()
            photo_params = copy.deepcopy(params)
            data = image.read()
            preview_id = request.form.get("preview_job_id") if len(images) == 1 else None
            reuse = False
            if preview_id and agent_config.ENABLE_CHAT_SUBJECT_PREVIEW:
                info, binding = _owned_preview(user, preview_id)
                if (binding["source_sha256"] != source_sha256(data)
                        or request.form.get("source_sha256") != binding["source_sha256"]
                        or binding["analysis_model"] != photo_params["analysis_model"]):
                    raise _RequestError("Preview does not match this photo or settings", 409)
                engine_params["srcKeys"] = list(info["srcKeys"])
                reuse = True
                # Freeze now: formal worker never waits for or resolves a token.
                # Completed 2.0 is reusable before S3/job completion; absence
                # of a valid snapshot retains the original analyzer fallback.
                preview_params = info['acts'][0]['params'] if isinstance(info.get('acts'), list) else json.loads(info['acts'])[0]['params']
                frozen_raw = info.get(PREVIEW_CONTEXT_FIELD)
                if isinstance(frozen_raw, str):
                    try:
                        frozen_raw = json.loads(frozen_raw)
                    except ValueError:
                        frozen_raw = None
                strict_ready = (preview_params.get('preview_format_version') == 3
                    and (info.get('status') == 'success' or
                         (isinstance(frozen_raw, dict) and frozen_raw.get('subject_preview', {}).get('state') == 'ready')))
                try:
                    resolved = _preview_snapshot(info, binding)
                    if resolved is not None:
                        snapshot, _ = resolved
                        if strict_ready and snapshot['format_version'] != 3:
                            raise _RequestError(i18n.tr('人物預覽版本不符，請重新預覽。'), 409)
                        photo_params["precomputed_preview"] = snapshot
                    elif strict_ready:
                        raise _RequestError(i18n.tr('人物清單暫時無法交接，請重試預覽。'), 409)
                except _RequestError as exc:
                    if strict_ready:
                        raise _RequestError(i18n.tr("人物清單暫時無法交接，請重試預覽。"), 409) from exc
                    pass
            request_context = chat_analysis.project(
                prompt=photo_params.get('user_prompt') or '', source_sha256=source_sha256(data),
                analysis_model=photo_params['analysis_model'])
            if request_context:
                photo_params['analysis_user_requests'] = request_context
            engine_params["acts"][0]["params"] = photo_params
            job_id = enqueue(engine_params, src_content=None if reuse else [data])
            current_app.logger.info("Formal analyze job=%s preview_source_reused=%s preview_context_reused=%s",
                job_id, reuse, "precomputed_preview" in photo_params)
        except _RequestError:
            raise
        except Exception as exc:
            errors.append({"filename": image.filename, "error": str(exc)})
            continue
        # Same history row /analyze writes, so the photo shows up in history
        # (and can be reopened from it) even if the session later expires.
        _record_history_safely(user, job_id, "local", engine_params["srcKeys"][0], job_id, params=photo_params)
        photos.append({
            "photo_id": f"p_{uuid.uuid4().hex[:12]}",
            "filename": image.filename,
            "source_sha256": source_sha256(data),
            "case_id": engine_params["srcKeys"][0],
            "src_keys": engine_params["srcKeys"],
            "analyze_job_id": job_id,
            "status": "analyzing",
            "error": None,
            "pending_render": None,
            "queued_render": None,
            "active_version": None,
            "analysis_skipped": bool(photo_params.get('skip_analysis')),
            **({"client_key": client_key} if client_key else {}),
        })

    if photos:
        store.add_photos(user, session_id, session, photos)
        store.append_messages(user, session_id, [store.make_message(
            "user", card={"type": "photos_added", "photo_ids": [p["photo_id"] for p in photos]})])
    session = store.load_session(user, session_id)
    added = {p["photo_id"] for p in photos}
    return jsonify({"photos": [p for p in session["photos"] if p["photo_id"] in added], "errors": errors}), 202


def _needs_analysis(photo):
    """Skipped photos need analysis for automatic edits."""
    plan = photo["plan"]
    return bool(photo.get("analysis_skipped")
                and not (plan.get("overwrite_prompt") and (plan.get("user_prompt") or "").strip()))


def _start_analysis(user, session_id, photo, why):
    """Run the analysis a photo's upload skipped, on its source image. `why`
    is "check" (the user asked to see it: it lands as a check card) or
    "render" (a render queued behind it starts when it lands)."""
    params, error = _parse_and_validate_params(build_render_form(photo["plan"], "analyze"))
    if error:
        raise _RequestError(error)
    params["stage"] = "analyze"
    initial = get_job_info(photo['analyze_job_id'], keyToGet=['acts']) or {}
    initial_acts = initial.get('acts') or []
    if isinstance(initial_acts, str): initial_acts = json.loads(initial_acts)
    initial_params = initial_acts[0]['params'] if initial_acts else {}
    sha = photo.get('source_sha256') or (initial_params.get('analysis_user_requests') or {}).get('source_sha256')
    if sha:
        requests = chat_analysis.project(prompt=photo['plan'].get('user_prompt') or '',
            source_sha256=sha, analysis_model=params['analysis_model'])
        if requests: params['analysis_user_requests'] = requests
    engine_params = get_engine_params_template()
    engine_params["srcKeys"] = list(photo["src_keys"])
    engine_params["acts"][0]["params"] = params
    job_id = enqueue(engine_params, src_content=None)
    photo["analysis_job_id"] = job_id
    photo["reanalysis"] = why
    photo["status"] = "analyzing"
    photo["planned_ratio"] = None
    photo["error"] = None
    store.save_photo_state(user, session_id, photo)
    # A new version of the case, as in 自訂模式 (api.analyze_existing).
    _record_history_safely(user, job_id, "local", engine_params["srcKeys"][0], job_id, params=params)
    # So 自訂模式 opening this case reads it too, not the renders' stub.
    try:
        attach_case_analysis(user, photo["case_id"], job_id)
    except Exception:
        current_app.logger.warning("attach_case_analysis failed for %s", job_id, exc_info=True)


@chat_bp.post("/sessions/<session_id>/photos/<photo_id>/analyze")
def analyze_photo(session_id, photo_id):
    """「幫我分析」 on a photo whose upload skipped the analysis."""
    body = _body()
    user = _user(body.get("user"))
    photo = _photo(_session(user, session_id), photo_id)
    if not photo.get("analysis_skipped"):
        raise _RequestError("this photo has been analyzed", 409)
    if photo["status"] != "ready":
        raise _RequestError(f"photo {photo_id} is {photo['status']}, not ready", 409)
    _start_analysis(user, session_id, photo, "check")
    store.append_messages(user, session_id, [store.make_message(
        "user", card={"type": "analysis_requested", "photo_id": photo_id})])
    return jsonify({"photo_id": photo_id, "status": photo["status"]}), 202


@chat_bp.get("/sessions/<session_id>/photos/<photo_id>/analysis")
def photo_analysis(session_id, photo_id):
    user = _user(request.args.get("user"))
    photo = _photo(_session(user, session_id), photo_id)
    analysis, error = _load_analysis(_analysis_job_id(photo))
    if error:
        return jsonify({"status": "error" if photo["status"] == "failed" else photo["status"], "error": error})
    return jsonify({"status": "success", "result": {
        "analysis": analysis,
        "compatible": is_compatible(analysis, min_version=get_min_compatible_version()),
        "compatibilityVersion": get_compatibility_version(analysis),
    }})


@chat_bp.put("/sessions/<session_id>/photos/<photo_id>/active-version")
def set_active_version(session_id, photo_id):
    body = _body()
    user = _user(body.get("user"))
    photo = _photo(_session(user, session_id), photo_id)
    version_id, job_id, run = body.get("version_id"), body.get("job_id"), body.get("run", 1)
    entry = get_history_entry(user, photo["case_id"]) or {}
    version = next((v for v in entry.get("versions") or [] if v["version_id"] == version_id), None)
    if version is None or not any(r["job_id"] == job_id and r["run"] == run for r in version["results"]):
        raise _RequestError("version not found for this photo", 404)
    photo["active_version"] = store.active_version(version_id, job_id, run)
    store.save_photo_state(user, session_id, photo)
    return jsonify(store.load_session(user, session_id))


# --- plans -----------------------------------------------------------------

def _not_ready(session):
    """Photos still changing: what a batch edit or render "all" waits for."""
    return [{"photo_id": p["photo_id"], "reason": p["status"]}
            for p in session["photos"] if p["status"] in ("analyzing", "reframing", "rendering")]


@chat_bp.patch("/sessions/<session_id>/plan")
def patch_all_plans(session_id):
    """Batch edit: the settings part of every photo's plan, plus the session
    defaults new photos start from."""
    body = _body()
    user = _user(body.get("user"))
    session = _session(user, session_id)
    not_ready = _not_ready(session)
    if not_ready:
        return jsonify({"error": "photos_not_ready", "not_ready": not_ready}), 409
    patch = body.get("plan")
    defaults, error = apply_plan_patch(session["defaults"], patch, debug=_debug(), settings_only=True)
    if error:
        raise _RequestError(error)
    plans = {}
    for photo in session["photos"]:
        if photo["status"] == "failed":
            continue
        plan, error = apply_plan_patch(photo["plan"], patch, debug=_debug(), settings_only=True)
        if error:
            raise _RequestError(f"photo {photo['photo_id']}: {error}")
        plans[photo["photo_id"]] = plan
    store.save_batch_plans(user, session_id, defaults, plans)
    for photo in session["photos"]:
        if photo["photo_id"] in plans:
            photo["plan"] = plans[photo["photo_id"]]
            _reframe_if_needed(user, session_id, photo)
    return jsonify({"defaults": defaults, "photos": [{"photo_id": pid, "plan": plan} for pid, plan in plans.items()]})


@chat_bp.patch("/sessions/<session_id>/photos/<photo_id>/plan")
def patch_photo_plan(session_id, photo_id):
    """One photo's plan, any time. While the photo is rendering this only
    affects its next render; while it is queued, the queued render uses it.
    A new 長寬比 on a ready photo starts re-planning its composition.

    compositions: what 面板模式 already planned for this photo (its
    analysis's _composition_by_ratio), handed over on a mode switch, so a
    ratio picked there is adopted rather than planned again."""
    body = _body()
    user = _user(body.get("user"))
    photo = _photo(_session(user, session_id), photo_id)
    plan, error = apply_plan_patch(photo["plan"], body.get("plan"), debug=_debug())
    if error:
        raise _RequestError(error)
    photo["plan"] = plan
    store.save_photo_plan(user, session_id, photo_id, plan)
    _reframe_if_needed(user, session_id, photo, compositions=body.get("compositions"))
    return jsonify({"photo_id": photo_id, "plan": plan, "status": photo["status"]})


# --- reframe ---------------------------------------------------------------

def _handed_compositions(compositions):
    """Every composition the other mode planned, out of its
    _composition_by_ratio, by ratio. Composition keys only, and only ones
    planned for the ratio they are filed under -- any other would leave the
    photo planned for a different ratio, and re-plan all over again."""
    if not isinstance(compositions, dict):
        return {}
    return {
        ratio: {key: state[key] for key in COMPOSITION_STATE_KEYS if key in state}
        for ratio, state in compositions.items()
        if isinstance(state, dict)
        and str(state.get("_aspect_ratio_pref_at_analysis") or "").strip().lower() == ratio
    }


def _reframe_if_needed(user, session_id, photo, analysis=None, compositions=None):
    """Re-plan a ready photo's composition when its plan's 長寬比 is not the
    one its analysis was planned for -- the results page's 長寬比 change
    (pipeline.reframe_stage), run as soon as the ratio is picked. A render
    asked for meanwhile waits in queued_render. Returns True if one started.

    planned_ratio caches the analysis's ratio on the photo, so the common
    case -- nothing to re-plan -- reads no analysis.

    The compositions handed over in `compositions` join the analysis's
    _composition_by_ratio, which reframe_stage reuses without calling the
    model: the framing the other mode showed, not a new one."""
    if photo["status"] != "ready":
        return False
    ratio = photo["plan"].get("aspect_ratio") or "auto"
    if photo.get("planned_ratio") == ratio:
        return False
    if analysis is None:
        analysis, error = _load_analysis(_analysis_job_id(photo))
        if error:
            current_app.logger.warning("reframe check skipped for %s: %s", photo["photo_id"], error)
            return False
    if analysis.get("_skipped_analysis"):
        return False
    planned = _planned_for(analysis)
    if photo.get("planned_ratio") != planned:
        photo["planned_ratio"] = planned
        store.save_photo_state(user, session_id, photo)
    if planned == ratio:
        return False
    params, error = _parse_and_validate_params(build_render_form(photo["plan"], "analyze"))
    if error:
        current_app.logger.warning("reframe not started for %s: %s", photo["photo_id"], error)
        return False
    # All of them, not just this ratio's: a later pick of one the other mode
    # planned is then a cache hit too.
    handed = _handed_compositions(compositions)
    if handed:
        analysis = {**analysis, "_composition_by_ratio": {**(analysis.get("_composition_by_ratio") or {}), **handed}}
    params["stage"] = "reframe"
    params["analysis"] = analysis
    engine_params = get_engine_params_template()
    engine_params["srcKeys"] = list(photo["src_keys"])
    engine_params["acts"][0]["params"] = params
    job_id = enqueue(engine_params, src_content=None)
    photo["status"] = "reframing"
    photo["error"] = None
    # handed: only adopting the other mode's composition -- done in moments,
    # so it shows no 重新構圖 in progress, and its 完成 says so.
    photo["pending_reframe"] = {"job_id": job_id, "aspect_ratio": ratio, "previous": planned, "handed": ratio in handed}
    store.save_photo_state(user, session_id, photo)
    return True


def _remap_recipe_checked(checked, before, after):
    """recipe_checked for the reframed analysis. Reframing swaps or moves the
    重新構圖 item, so positions shift: every other item keeps the state the
    user gave it, matched by its text; the new 重新構圖 item, and anything
    else new, starts checked (as frontend/app.js adoptReframedAnalysis)."""
    if not checked:
        return []
    was = {text: not (i < len(checked) and checked[i] is False) for i, text in enumerate(_recipe_items(before))}
    return [True if REFRAME_ITEM_RE.match(text) else was.get(text, True) for text in _recipe_items(after)]


def _advance_reframing(user, session_id, photo, statuses):
    pending = photo["pending_reframe"]
    job = _job_status(pending["job_id"], statuses)
    if job["status"] not in TERMINAL_STATUSES or not store.claim(user, session_id, pending["job_id"]):
        return []
    photo_id = photo["photo_id"]
    photo["status"] = "ready"
    photo["pending_reframe"] = None
    queued = photo["queued_render"]
    after, error = (None, job["error"] or "reframe failed") if job["status"] != "success" \
        else _load_analysis(pending["job_id"])
    if error:
        photo["error"] = error
        photo["queued_render"] = None
        messages = [store.make_message("assistant", card={
            "type": "reframe_failed", "photo_id": photo_id, "aspect_ratio": pending["aspect_ratio"],
            "previous": pending["previous"], "error": error, "error_code": job.get("error_code"),
            "queued_render_cancelled": bool(queued)})]
        # The 長寬比 goes back to the one the composition is still planned
        # for -- unless it was changed again meanwhile, which re-plans anew.
        if photo["plan"].get("aspect_ratio") == pending["aspect_ratio"]:
            photo["plan"] = {**photo["plan"], "aspect_ratio": pending["previous"]}
            store.save_photo_plan(user, session_id, photo_id, photo["plan"])
            store.save_photo_state(user, session_id, photo)
        elif not _reframe_if_needed(user, session_id, photo):
            store.save_photo_state(user, session_id, photo)
        return messages

    before, _ = _load_analysis(_analysis_job_id(photo))
    if before is not None:
        checked = _remap_recipe_checked(photo["plan"].get("recipe_checked") or [], before, after)
        if checked != (photo["plan"].get("recipe_checked") or []):
            photo["plan"] = {**photo["plan"], "recipe_checked": checked}
            store.save_photo_plan(user, session_id, photo_id, photo["plan"])
    photo["analysis_job_id"] = pending["job_id"]
    photo["planned_ratio"] = _planned_for(after)
    photo["error"] = None
    # handed still gets its line: the chat's last word on the composition
    # would otherwise be the ratio from before the switch.
    messages = [store.make_message("assistant", card={
        "type": "reframe_done", "photo_id": photo_id, "aspect_ratio": pending["aspect_ratio"],
        "handed": bool(pending.get("handed"))})]
    # Changed again while this one ran: re-plan for that one, the queued
    # render still waiting.
    if _reframe_if_needed(user, session_id, photo, after):
        return messages
    if not queued:
        store.save_photo_state(user, session_id, photo)
        return messages
    photo["queued_render"] = None
    messages += _start_queued_render(user, session_id, photo, queued)
    return messages


def _start_queued_render(user, session_id, photo, queued):
    # The render may have been requested in a different language from the
    # analysis/reframe it waited for. Polling must not choose its language.
    with i18n.use(queued.get("lang", i18n.current())):
        return _start_queued_render_in_language(user, session_id, photo, queued)


def _start_queued_render_in_language(user, session_id, photo, queued):
    """A render asked for while the photo was busy starts now, with its plan
    as it is now -- after the analysis, if it now needs the one its upload
    skipped."""
    if _needs_analysis(photo):
        _start_analysis(user, session_id, photo, "render")
        photo["queued_render"] = queued
        store.save_photo_state(user, session_id, photo)
        return []
    prepared, problem = _prepare_render(photo)
    if problem:
        photo["error"] = problem[1]
        store.save_photo_state(user, session_id, photo)
        return [store.make_message("assistant", card={
            "type": "render_failed", "photo_id": photo["photo_id"], "reason": problem[0], "error": problem[1]})]
    render = _start_render(user, session_id, photo, prepared, queued["base"])
    return [store.make_message("assistant", card={"type": "render_started", "queued": True, **render})]


# --- render ----------------------------------------------------------------

def _prepare_render(photo):
    """Everything a ready photo's render needs, from its plan as it is now:
    (prepared, None) or (None, (reason, message, http_status)), reason being
    a stable code the chat page can word for the user."""
    plan = photo["plan"]
    if any("detection_index" in f or "preview_person_id" in f for f in plan.get("prompt_faces", [])):
        return None, ("unresolved_preview_person", i18n.tr("指定人物尚未確認，請重新指定或清除該需求。"), 409)
    params, error = _parse_and_validate_params(build_render_form(plan, "finish"))
    if error:
        return None, ("invalid_plan", f"photo {photo['photo_id']}: {error}", 400)
    params["stage"] = "finish"
    analysis, error = _load_analysis(_analysis_job_id(photo))
    if error:
        return None, ("analysis_unavailable", f"photo {photo['photo_id']}: {error}", 409)
    if not is_compatible(analysis, min_version=get_min_compatible_version()):
        return None, ("analysis_compatibility_version_outdated", "analysis_compatibility_version_outdated", 409)
    selections = build_selections(plan)
    # Per-person facial requests the editor renders as that person's own final state.
    face_requests = {}
    if FIXED_RATIO_RE.match(plan["aspect_ratio"]) and selections.get("recipe_checked"):
        # The page keeps this item on for a fixed ratio; so does the render.
        checked = list(selections["recipe_checked"])
        for i, text in enumerate(_recipe_items(analysis)):
            if REFRAME_ITEM_RE.match(text) and i < len(checked):
                checked[i] = True
        selections = {**selections, "recipe_checked": checked}
    analysis, error = _apply_review_selections(analysis, selections)
    if error:
        return None, ("invalid_plan", f"photo {photo['photo_id']}: {error}", 400)
    # A request marked in the session counts only while that person still has a wish in 個人化需求.
    names = person_names(analysis)
    for key in plan.get("face_requests") or {}:
        index, _, kind = key.partition(":")
        idx = int(index)
        if kind == "face_symmetry" and names.get(idx) and names[idx] in params["user_prompt"]:
            face_requests.setdefault(idx, "")
    if face_requests:
        analysis = {**analysis, "_user_face_requests": [
            {"subject_index": idx, "kind": "face_symmetry", "text": text}
            for idx, text in sorted(face_requests.items())
        ]}
    run_limit = capabilities.MAX_RUN_COUNT if _debug() else capabilities.GENERAL_MAX_RUN_COUNT
    return {"params": {**params, "analysis": analysis}, "selections": selections,
            "run_count": min(plan["run_count"], run_limit)}, None


def _start_render(user, session_id, photo, prepared, base, source=None, draft=None):
    """Queue one photo's render jobs and mark it rendering. `source` (a
    continuation_svc.resolve_source_result) edits that version's output instead of the
    original, with `draft` the continuation request it was sent with; the
    case stays the photo's own either way."""
    job_ids = [get_session_id() for _ in range(prepared["run_count"])]
    # The version's id is its first job's id, as when the review page fans
    # out N /continue calls under one action_id.
    action_id = job_ids[0]
    based_on = source["version_id"] if source else (photo["active_version"] or {}).get("version_id")
    source_result = {k: source[k] for k in ("version_id", "job_id", "run")} if source else None
    for job_id in job_ids:
        engine_params = get_engine_params_template()
        # A continuation that needs the original carries it second; the worker
        # edits the first and hands the second to the editor as a reference.
        engine_params["srcKeys"] = ([source["key"]] + ([prepared["reference_key"]] if prepared.get("reference_key") else [])
                                    if source else list(photo["src_keys"]))
        engine_params["acts"][0]["params"] = prepared["params"]
        enqueue(engine_params, src_content=None, job_id=job_id)
        _record_history_safely(
            user, job_id, "local", photo["case_id"], action_id, params=prepared["params"],
            extra={"selections": prepared["selections"], "session_id": session_id,
                   "photo_id": photo["photo_id"], "base": base,
                   **({"source_result": source_result} if source else {})},
            based_on=based_on,
        )
    photo["status"] = "rendering"
    photo["error"] = None
    photo["queued_render"] = None
    # The plan rides along so the finished version's card can keep it: the
    # next version's note reads what changed from it.
    photo["pending_render"] = {"action_id": action_id, "job_ids": job_ids, "based_on": based_on, "base": base,
                               "plan": copy.deepcopy(photo["plan"])}
    if source:
        photo["pending_render"].update({"source_result": source_result, "draft": draft})
    store.save_photo_state(user, session_id, photo)
    return {"photo_id": photo["photo_id"], "action_id": action_id, "job_ids": job_ids}


# --- 接著這張調整 ------------------------------------------------------------
# The source checks and the request-only stub live in services/continuation,
# shared with 面板模式's /continue-version (imported as continuation_svc).

@chat_bp.put("/sessions/<session_id>/photos/<photo_id>/continuation-draft")
def save_continuation_draft(session_id, photo_id):
    """This round's request for 接著這張調整 from one version. One draft per
    photo: a different source starts a new one. `revision` is the one the
    client last saw (0 for none), so a stale tab cannot overwrite a newer
    draft. `style_options` are styles picked for this round, alone or beside
    the text; no text and no style clears it."""
    if not agent_config.ENABLE_CHAT_RENDER_FROM_VERSION:
        raise _RequestError("Not found", 404)
    body = _body()
    user = _user(body.get("user"))
    photo = _photo(_session(user, session_id), photo_id)
    source = continuation_svc.source_result_ref(body.get("source_result"))
    text, revision = body.get("text"), body.get("revision", 0)
    if not isinstance(text, str) or len(text) > USER_PROMPT_MAX_LEN:
        raise _RequestError(f"text must be a string of at most {USER_PROMPT_MAX_LEN} characters")
    if type(revision) is not int:
        raise _RequestError("revision must be an integer")
    styles = body.get("style_options") or {}
    error = validate_selections({"options": styles})
    if error:
        raise _RequestError(error.replace("selections.options", "style_options"))
    styles = {k: v for k, v in styles.items() if v not in (None, *capabilities.STYLE_UNSET_VALUES)}
    continuation_svc.resolve_source_result(user, photo["case_id"], source)
    draft = photo.get("continuation_draft")
    current = draft["revision"] if draft and continuation_svc.same_source(draft["source_result"], source) else 0
    if revision != current:
        return jsonify({"error": "draft_changed", "draft": draft}), 409
    text = text.strip()
    photo["continuation_draft"] = ({"source_result": source, "text": text, "style_options": styles,
                                    "revision": current + 1} if text or styles else None)
    store.save_photo_state(user, session_id, photo)
    return jsonify(store.load_session(user, session_id))


def _prepare_continuation(photo, draft):
    """_prepare_render for 接著這張調整: the plan's technical choices (model,
    quality, output size, run count) with the draft's text as the whole
    request, plus the styles picked for this round. No recipe, selections,
    preset polish, body reshape, crop or ratio change, and no eye-repair pass."""
    plan = photo["plan"]
    form = continuation_svc.continuation_form(build_render_form(plan, "finish"), draft["text"])
    params, error = _parse_and_validate_params(form)
    if error:
        raise _RequestError(error)
    params["stage"] = "finish"
    analysis = continuation_svc.continuation_analysis(draft["text"], draft.get("style_options"), params["image_model"])
    reference_key = photo["src_keys"][0] if "_continuation_original" in analysis else None
    run_limit = capabilities.MAX_RUN_COUNT if _debug() else capabilities.GENERAL_MAX_RUN_COUNT
    return {"params": {**params, "analysis": analysis}, "selections": {},
            "run_count": min(plan["run_count"], run_limit),
            "reference_key": reference_key}


def _render_from_version(user, session_id, session, body):
    """POST /render with base "version": one photo, from the version in
    source_result, with the photo's saved continuation draft for that source."""
    photo_ids = body.get("photo_ids")
    if not (isinstance(photo_ids, list) and len(photo_ids) == 1 and isinstance(photo_ids[0], str)):
        raise _RequestError("base \"version\" renders exactly one photo")
    photo = _photo(session, photo_ids[0])
    source = continuation_svc.source_result_ref(body.get("source_result"))
    if photo["status"] != "ready":
        raise _RequestError(f"photo {photo['photo_id']} is {photo['status']}, not ready to render", 409)
    draft = photo.get("continuation_draft")
    if not draft or not continuation_svc.same_source(draft["source_result"], source) \
            or not (draft["text"] or draft.get("style_options")):
        raise _RequestError("draft_missing", 409)
    revision = body.get("draft_revision")
    if revision is not None and revision != draft["revision"]:
        return jsonify({"error": "draft_changed", "draft": draft}), 409
    resolved = continuation_svc.resolve_source_result(user, photo["case_id"], source)
    prepared = _prepare_continuation(photo, draft)
    rendered = _start_render(user, session_id, photo, prepared, VERSION_BASE,
                             source=resolved, draft=copy.deepcopy(draft))
    if not photo.get("analysis_skipped"):
        # The version carries a skipped-analysis stub; pointing the case at the
        # photo's analysis lets 面板模式 read it as analyzed, as it does a
        # 「只照需求改」 case analyzed afterwards.
        try:
            attach_case_analysis(user, photo["case_id"], _analysis_job_id(photo))
        except Exception:
            current_app.logger.warning("attach_case_analysis failed for %s", photo["photo_id"], exc_info=True)
    store.append_messages(user, session_id, [store.make_message("user", card={
        "type": "render_requested", "base": VERSION_BASE, "renders": [rendered], "queued": [],
        "source_result": source, "prompt": draft["text"],
        "style_options": draft.get("style_options") or {}})])
    return jsonify({"renders": [rendered], "queued": []}), 202


@chat_bp.post("/sessions/<session_id>/render")
def render(session_id):
    """photo_ids "all": every photo, once none is analyzing or rendering.
    A list: those photos -- a ready one renders now, one still analyzing is
    queued. Either way everything is checked before anything is queued, so
    a request that fails has started nothing."""
    body = _body()
    user = _user(body.get("user"))
    session = _session(user, session_id)
    base = body.get("base", "original")
    if base not in _render_bases():
        raise _RequestError(f"base must be one of {_render_bases()}")
    if base == VERSION_BASE:
        return _render_from_version(user, session_id, session, body)

    photo_ids = body.get("photo_ids")
    if photo_ids == "all":
        not_ready = _not_ready(session)
        if not_ready:
            return jsonify({"error": "photos_not_ready", "not_ready": not_ready}), 409
        photos = [p for p in session["photos"] if p["status"] != "failed"]
        if not photos:
            raise _RequestError("no photo to render", 409)
    elif isinstance(photo_ids, list) and photo_ids and all(isinstance(i, str) for i in photo_ids):
        photos = [_photo(session, photo_id) for photo_id in dict.fromkeys(photo_ids)]
    else:
        raise _RequestError("photo_ids must be a non-empty list of photo ids or \"all\"")

    to_render, to_queue, to_analyze = [], [], []
    for photo in photos:
        if photo["status"] in ("analyzing", "reframing") and not photo["queued_render"]:
            to_queue.append(photo)
            continue
        if photo["status"] == "ready" and _needs_analysis(photo):
            to_analyze.append(photo)
            continue
        if photo["status"] != "ready":
            state = "queued" if photo["queued_render"] else photo["status"]
            raise _RequestError(f"photo {photo['photo_id']} is {state}, not ready to render", 409)
        prepared, problem = _prepare_render(photo)
        if problem:
            return jsonify({'error': problem[1], 'reason': problem[0]}), problem[2]
        to_render.append((photo, prepared))

    renders = [_start_render(user, session_id, photo, prepared, base) for photo, prepared in to_render]
    for photo in to_analyze:
        _start_analysis(user, session_id, photo, "render")
    to_queue += to_analyze
    for photo in to_queue:
        photo["queued_render"] = {"base": base, "lang": i18n.current()}
        store.save_photo_state(user, session_id, photo)
    queued = [p["photo_id"] for p in to_queue]
    store.append_messages(user, session_id, [store.make_message(
        "user", card={"type": "render_requested", "base": base, "renders": renders, "queued": queued})])
    return jsonify({"renders": renders, "queued": queued}), 202


# --- typed questions ---------------------------------------------------------

def _ask_render(user, session_id, photo):
    """A typed 一鍵美化: render now, or queue it while the photo is analyzing,
    as the render route would. Returns the messages that follow the reply."""
    if photo["status"] == "ready" and _needs_analysis(photo):
        _start_analysis(user, session_id, photo, "render")
    if photo["status"] in ("analyzing", "reframing") and not photo["queued_render"]:
        photo["queued_render"] = {"base": "original", "lang": i18n.current()}
        store.save_photo_state(user, session_id, photo)
        renders, queued = [], [photo["photo_id"]]
    elif photo["status"] != "ready":
        return [store.make_message("assistant", card={
            "type": "render_failed", "photo_id": photo["photo_id"], "reason": "photo_busy",
            "error": i18n.tr("這張照片正在處理中，完成後再試一次。")})]
    else:
        prepared, problem = _prepare_render(photo)
        if problem:
            return [store.make_message("assistant", card={
                "type": "render_failed", "photo_id": photo["photo_id"], "reason": problem[0], "error": problem[1]})]
        renders, queued = [_start_render(user, session_id, photo, prepared, "original")], []
    return [store.make_message("user", card={
        "type": "render_requested", "photo_id": photo["photo_id"], "base": "original",
        "renders": renders, "queued": queued, "label": "一鍵美化"})]


# 調整一輪可以動到的 plan 欄位（跟 frontend-chat 的 ADJUSTABLE 同一份）。
ROUND_KEYS = ("recipe_checked", "face_issue_overrides", "style_options", "render_option", "aspect_ratio",
              "output_scale", "advanced_options", "preset", "run_count", "user_prompt", "pose_face_changes")


def _end_round(user, session_id, photo, history):
    """The user is done adjusting: put the plan back to where this round
    started, so the page can leave adjusting with nothing left half-done.
    A round still open is the last adjust_opened after the last render."""
    start = None
    for m in history:
        kind = (m.get("card") or {}).get("type") or ""
        if kind == "adjust_opened":
            start = m["card"].get("plan")
        elif kind.startswith("render_") or kind == "adjust_cancelled":
            start = None
    if not isinstance(start, dict):
        return
    base = {**photo["plan"], "face_issue_overrides": {}, "style_options": {}, "advanced_options": {}}
    plan, error = apply_plan_patch(base, {k: v for k, v in start.items() if k in ROUND_KEYS}, debug=_debug())
    if error:
        current_app.logger.warning("could not restore %s to its round start: %s", photo["photo_id"], error)
        return
    if plan != photo["plan"]:
        store.save_photo_plan(user, session_id, photo["photo_id"], plan)
        photo["plan"] = plan
        _reframe_if_needed(user, session_id, photo)


def _screen(raw):
    """The buttons the page shows right now, {bar, topics, pending, panels},
    each a few short labels; pending names what this round changed and has not
    rendered, panels the panels a reply may open (left out by older pages).
    versions and shown -- how many versions the photo has and which one is on
    screen -- and added -- what this round added to 個人化需求 that no version
    has yet -- come only from a page that can 接著這張調整 (ENABLE_CHAT_RENDER_FROM_VERSION)."""
    raw = raw if raw is not None else {}
    if not isinstance(raw, dict):
        raise _RequestError("screen must be an object")
    out = {}
    for key in ("bar", "topics", "pending", "panels"):
        if key == "panels" and key not in raw:
            continue
        labels = raw.get(key, [])
        if not isinstance(labels, list) or len(labels) > SCREEN_LABELS_MAX or not all(
                isinstance(x, str) and 0 < len(x) <= PROMPT_REVIEW_LABEL_MAX_LEN for x in labels):
            raise _RequestError(f"screen.{key} must be a list of at most {SCREEN_LABELS_MAX} short labels")
        out[key] = labels
    for key in ("versions", "shown"):
        if key not in raw:
            continue
        n = raw[key]
        if type(n) is not int or not 0 <= n <= SCREEN_VERSIONS_MAX:
            raise _RequestError(f"screen.{key} must be a whole number from 0 to {SCREEN_VERSIONS_MAX}")
        if agent_config.ENABLE_CHAT_RENDER_FROM_VERSION:
            out[key] = n
    if "added" in raw:
        added = raw["added"]
        if not isinstance(added, str) or len(added) > USER_PROMPT_MAX_LEN:
            raise _RequestError(f"screen.added must be text of at most {USER_PROMPT_MAX_LEN} characters")
        if agent_config.ENABLE_CHAT_RENDER_FROM_VERSION and added.strip():
            out["added"] = added.strip()
    return out


def _subject_indices(body):
    """The tapped faces, in the order they were tapped. `subject_index` is
    the one-face form an older page still sends."""
    raw = body.get("subject_indices")
    if raw is None:
        raw = [] if body.get("subject_index") is None else [body.get("subject_index")]
    if (not isinstance(raw, list) or len(raw) > PROMPT_REVIEW_PEOPLE_MAX
            or not all(not isinstance(i, bool) and isinstance(i, int) and i >= 0 for i in raw)):
        raise _RequestError(
            f"subject_indices must be a list of at most {PROMPT_REVIEW_PEOPLE_MAX} non-negative integers")
    return list(dict.fromkeys(raw))


def _ask_faces(body):
    """The tapped faces of a photo with no analysis (「只照需求改」): no
    subjects to index, so each is the local detector's box and where it
    stands, the same form an upload-page line carries."""
    raw = body.get("faces")
    if raw is None:
        return []
    error = f"faces must be a list of at most {PROMPT_REVIEW_PEOPLE_MAX} {{box, where}} objects"
    if not isinstance(raw, list) or len(raw) > PROMPT_REVIEW_PEOPLE_MAX:
        raise _RequestError(error)
    out = []
    for face in raw:
        box = face.get("box") if isinstance(face, dict) else None
        where = face.get("where") if isinstance(face, dict) else None
        if (not _valid_subject_box(box) or not isinstance(where, str)
                or not 0 < len(where) <= PROMPT_REVIEW_LABEL_MAX_LEN):
            raise _RequestError(error)
        out.append({"box": {k: float(box[k]) for k in ("x", "y", "w", "h")}, "where": where})
    return out


@chat_bp.post("/sessions/<session_id>/photos/<photo_id>/ask")
def ask_photo(session_id, photo_id):
    """A typed message about one photo. The reply may carry a proposal -- a
    checked plan patch the page offers to apply -- or, for a plain "fix it",
    start the render right away.

    `waiting`, in place of `text`, answers a question already in the log: one
    asked on the upload page that only the analysis could answer, posted as
    an ask card marked waiting when the session started. It is answered once,
    and the reply says which question it answers."""
    body = _body()
    user = _user(body.get("user"))
    photo = _photo(_session(user, session_id), photo_id)
    waiting = body.get("waiting")
    if waiting is not None:
        return _answer_waiting(user, session_id, photo, waiting, _screen(body.get("screen")))
    text = body.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MESSAGE_TEXT_MAX_LEN:
        raise _RequestError(f"text must be a non-empty string of at most {MESSAGE_TEXT_MAX_LEN} characters")
    # The faces the user tapped to say who the message is about.
    subject_indices = _subject_indices(body)
    faces = _ask_faces(body)
    if faces and subject_indices:
        raise _RequestError("send subject_indices or faces, not both")
    if photo["status"] == "failed":
        raise _RequestError("this photo's analysis failed", 409)
    screen = _screen(body.get("screen"))

    # The plan as the user left it when they spoke: a panel opened before this
    # line is summed up (its recap) by what it held then, not by what the chat
    # changed afterwards.
    card = {"type": "ask", "photo_id": photo_id,
            "plan": {k: copy.deepcopy(photo["plan"][k]) for k in ROUND_KEYS if k in photo["plan"]}}
    if subject_indices:
        card["subject_indices"] = subject_indices
    if faces:
        card["faces"] = faces
    asked = store.make_message("user", text.strip(), card)
    # Filtered before the limit: in a session of several photos the others'
    # turns would otherwise use up this photo's history.
    with timed("ask_history", photo=photo_id):
        prior = [m for m in store.get_messages(user, session_id, limit=1000)
                 if _about(m.get("card"), photo_id)]
    answer_to = body.get("answer_to")
    if answer_to is not None:
        actionable = [m for m in prior if (m.get("card") or {}).get("type") != "personalized_snapshot"]
        previous = actionable[-1] if actionable else {}
        if not isinstance(answer_to, str) or previous.get("id") != answer_to or previous.get("role") != "assistant" or (previous.get("card") or {}).get("type") != "llm_reply" or not (previous.get("card") or {}).get("question_kind"):
            raise _RequestError("this clarification is no longer current", 409)
        asked["card"]["answers_question_id"] = answer_to
        if ((previous.get('card') or {}).get('clarification') or {}).get('patch_continue') and agent_config.ENABLE_CHAT_PATCH_CONTINUE:
            if text in (previous.get('card') or {}).get('options', []):
                # Only this display-only continuation preserves a clicked LLM
                # label verbatim, including whitespace, for selected_option.
                asked['text'] = text
    history = prior[-chat_assistant.HISTORY_LEN:] + [asked]
    with timed("ask_answer", photo=photo_id):
        return _answer(user, session_id, photo, history, screen, [asked])


def _answer_waiting(user, session_id, photo, message_id, screen):
    if not isinstance(message_id, str):
        raise _RequestError("waiting must be a message id")
    if photo["status"] == "failed":
        raise _RequestError("this photo's analysis failed", 409)
    if photo["status"] == "analyzing":
        raise _RequestError("this photo is still being analyzed", 409)
    # After 一鍵美化 it is answered with the result in hand.
    if photo["status"] == "rendering":
        raise _RequestError("this photo is still being retouched", 409)
    history = [m for m in store.get_messages(user, session_id, limit=1000)
               if _about(m.get("card"), photo["photo_id"])]
    asked = next((m for m in history if m["id"] == message_id and m["role"] == "user"
                  and (m.get("card") or {}).get("type") == "ask" and m["card"].get("waiting")), None)
    if asked is None:
        raise _RequestError("no such waiting question", 404)
    # Whichever page asks first gets the answer; a second tab, or the same
    # page after a reload, finds it answered already.
    if not store.claim(user, session_id, f"answer:{message_id}"):
        return jsonify({"messages": []})
    # The question was asked before the analysis came in; it is still the
    # line to answer, so it goes last.
    history = [m for m in history if m is not asked][-chat_assistant.HISTORY_LEN + 1:] + [asked]
    return _answer(user, session_id, photo, history, screen, [], answers=message_id)


def _apply_directly(user, session_id, photo, reply):
    """Applies a proposal that needs no 套用, the way the page does when it is
    pressed: the plan changes, the log says it was applied, and after a render
    it opens a round of adjusting so 美化 makes the next version. `reply` is
    the llm_reply that carries the proposal."""
    proposal = reply["card"]["proposal"]
    plan, error = apply_plan_patch(photo["plan"], proposal["patch"], debug=_debug())
    if error:
        # Checked when it was proposed; if the plan moved since, ask after all.
        current_app.logger.warning("could not apply %s directly: %s", proposal["id"], error)
        proposal.pop("direct", None)
        proposal.pop("undo", None)
        reply["card"]["text"] = i18n.tr("可以幫你這樣調整，確認後套用。")
        return []
    photo_id = photo["photo_id"]
    messages = [store.make_message("user", card={
        "type": "proposal_resolved", "photo_id": photo_id, "proposal_id": proposal["id"], "choice": "apply",
        "auto": True})]
    log = store.get_messages(user, session_id)
    if photo.get("status") == "ready" and _round_to_open(log, photo_id):
        messages.append(store.make_message("assistant", card={
            "type": "adjust_opened", "photo_id": photo_id, "via": "proposal", "auto": True,
            "plan": _parked_round_start(log, photo_id)
            or {k: copy.deepcopy(photo["plan"][k]) for k in ROUND_KEYS if k in photo["plan"]}}))
    photo["plan"] = plan
    store.save_photo_plan(user, session_id, photo_id, plan)
    if _reframe_if_needed(user, session_id, photo):
        # 改了比例就先重新構圖，這時底下沒有能按的鍵：先不指下一步，構圖好了
        # 那則（reframe_done）再把項目和美化接上。
        next_step = i18n.pattern("next_step", NEXT_STEP_RE)
        text = next_step.sub("", reply["card"]["text"]) if next_step else reply["card"]["text"]
        reply["card"]["text"] = i18n.sentences(text, i18n.tr(REFRAMING_NOTE))
    return messages


def _open_panel(user, session_id, photo, category):
    """Opens the panel a reply says it opened, the way pressing its button
    does; after a render it first opens a round of adjusting, as 看美化項目
    does. Both cards carry the plan as the page's snapshotOf would, so the
    round starts out unchanged."""
    photo_id = photo["photo_id"]
    snapshot = {k: copy.deepcopy(photo["plan"][k]) for k in ROUND_KEYS if k in photo["plan"]}
    if photo["plan"].get("overwrite_prompt"):
        snapshot["overwrite_prompt"] = True
    messages = []
    log = store.get_messages(user, session_id)
    if _round_to_open(log, photo_id):
        messages.append(store.make_message("assistant", card={
            "type": "adjust_opened", "photo_id": photo_id, "via": "open", "auto": True,
            "plan": _parked_round_start(log, photo_id) or snapshot}))
    # via reply: the reply above said it opens, so no tap bubble of the user's own.
    messages.append(store.make_message("user", card={
        "type": "options_opened", "photo_id": photo_id, "category": category, "via": "reply",
        "plan": copy.deepcopy(snapshot)}))
    return messages


def _parked_round_start(log, photo_id):
    """The plan a round left with 返回 started from, while nothing has been
    rendered from the plan since (接著這張調整 does not use it): the next round
    picks up from there, so what that round changed still shows as changed.
    None otherwise."""
    start, parked = None, False
    for m in log:
        card = m.get("card") or {}
        if not _about(card, photo_id):
            continue
        kind = card.get("type") or ""
        if kind == "adjust_opened":
            start, parked = card.get("plan"), False
        elif kind == "adjust_cancelled":
            parked = start is not None
        elif kind.startswith("render_") and card.get("base") != VERSION_BASE:
            start, parked = None, False
    return copy.deepcopy(start) if parked and isinstance(start, dict) else None


def _about(card, photo_id):
    """Whether a log card concerns this photo. Most carry one photo_id; the
    ones a session of several photos shares name theirs in a list instead
    (photos_added's photo_ids, render_requested's renders and queued), and a
    card naming none is about the session as a whole."""
    card = card or {}
    if card.get("photo_id"):
        return card["photo_id"] == photo_id
    ids = list(card.get("photo_ids") or []) + list(card.get("queued") or [])
    ids += [r.get("photo_id") for r in card.get("renders") or [] if isinstance(r, dict)]
    return not ids or photo_id in ids


def _round_to_open(log, photo_id):
    """True when the photo has been rendered and no round of adjusting is open
    since: the last render came after the last adjust_opened, or the user
    closed that round."""
    rendered = opened = False
    for m in log:
        card = m.get("card") or {}
        if not _about(card, photo_id):
            continue
        kind = card.get("type") or ""
        if kind.startswith("render_") or kind == "adjust_cancelled":
            rendered, opened = rendered or kind.startswith("render_"), False
        elif kind == "adjust_opened":
            opened = True
        elif kind == "llm_reply" and card.get("close"):
            opened = False
    return rendered and not opened


def _answer(user, session_id, photo, history, screen, messages, answers=None):
    """The model's reply to the last line of `history`, appended to the log
    after `messages` (the question itself, when it is new)."""
    photo_id = photo["photo_id"]
    analysis = None
    if photo["status"] != "analyzing":
        with timed("ask_analysis", photo=photo_id):
            analysis, error = _load_analysis(_analysis_job_id(photo))
        if error:
            current_app.logger.warning("chat ask without analysis for %s: %s", photo_id, error)
            analysis = None
    tag = {"answers": answers} if answers else {}
    base_plan = copy.deepcopy(photo["plan"])
    try:
        answer = chat_queue.run({
            "stage": chat_jobs.STAGE_ASK, "analysis": analysis, "plan": photo["plan"],
            "status": photo["status"], "history": history, "debug": _debug(), "screen": screen,
            "deadline": time.time() + chat_assistant.TIMEOUT_SECONDS})
    except Exception:
        current_app.logger.exception("chat ask failed for %s", photo_id)
        messages = messages + [store.make_message("assistant", card={"type": "llm_error", "photo_id": photo_id, **tag})]
        store.append_messages(user, session_id, messages)
        return jsonify({"messages": messages})

    messages = list(messages)
    proposal = answer["proposal"]
    if proposal and answer.get("request_validated"):
        current_photo = _photo(_session(user, session_id), photo_id)
        if current_photo["plan"] != base_plan:
            proposal = None
            answer = {**answer, "render": False, "close": False, "open": "", "options": [],
                "reply": i18n.tr("設定已在這段時間更新，這次需求尚未套用，請依最新設定再送出。")}
        else:
            photo = current_photo
    if proposal:
        proposal = {"id": uuid.uuid4().hex[:12], **proposal}
    if answer["reply"] or proposal or not answer["render"]:
        messages.append(store.make_message("assistant", card={
            "type": "llm_reply", "photo_id": photo_id,
            "text": answer["reply"] if agent_config.ENABLE_CHAT_PATCH_CONTINUE and (answer.get('clarification') or {}).get('patch_continue') else answer["reply"] or i18n.tr("我不太確定要怎麼調整，可以直接在面板上修改。"), "proposal": proposal,
            "options": answer["options"],
            **({"question_kind": answer["question_kind"], "answer_mode": answer.get("answer_mode"),
                "clarification": answer.get("clarification")} if answer.get("question_kind") else {}),
            **({"lines": True} if answer.get("lines") else {}),
            **({"close": True} if answer.get("close") else {}),
            **({"continue": answer["continue"]} if answer.get("continue") else {}), **tag}))
    if proposal and proposal.get("direct"):
        messages += _apply_directly(user, session_id, photo, messages[-1])
    if answer.get("open") and photo["status"] == "ready" and analysis:
        messages += _open_panel(user, session_id, photo, answer["open"])
    if answer.get("close"):
        _end_round(user, session_id, photo, history)
    if answer["render"]:
        # Re-read: a poll may have moved the photo on while the model thought.
        messages += _ask_render(user, session_id, _photo(_session(user, session_id), photo_id))
    store.append_messages(user, session_id, messages)
    return jsonify({"messages": messages})


# --- the upload page's typed requests ----------------------------------------

# A typed 一鍵美化 or 分析: the page starts it, so the reply says it is starting.
PROMPT_REVIEW_START_REPLIES = {"render": "好，馬上幫你一鍵美化。", "analyze": "好，我先幫你分析這張照片。"}
# A missing review sentence must not silently discard the user's input.
PROMPT_REVIEW_KEPT_REPLY = chat_assistant.KEPT_REPLY + chat_assistant.KEPT_NEXT
# What the page sends along with the line.
PROMPT_REVIEW_HISTORY_MAX = 20
PROMPT_REVIEW_PEOPLE_MAX = 20
PROMPT_REVIEW_LABEL_MAX_LEN = 40
# 個人化需求一個人一段，加上沒指定對象的那段。
PROMPT_REVIEW_SECTIONS_MAX = PROMPT_REVIEW_PEOPLE_MAX + 1
PROMPT_REVIEW_WAIT_SECONDS = chat_queue.WAIT_SECONDS


@chat_bp.post("/prompt-review")
def prompt_review():
    """One line typed on the upload page, before there is a session.

    In a session every typed message goes past the model first, and that is
    what keeps 個人化需求 about this photo: anything else gets a sentence back
    and changes nothing. The upload page collects the same lines with no
    session, no photo and no analysis to put them against, so they come here
    instead -- the same model, asked the one question this page has
    (chat_assistant.review_line, run by the worker as a chat job).

    The line itself is never rewritten here. It is the user's own words next
    to the face they pointed at, and _name_prompt_faces and the panel's own
    check rewrite and merge them later, once there is an analysis to name
    people by. All this decides is whether the line belongs there at all --
    or, when the user's own words leave out who or what, asks them (`ask` and
    `options`). A line that only makes sense next to an earlier one -- the
    answer to that question, or 「褲子也是」 after 「把男孩的衣服換成白色」 --
    comes back written out in full (`line`), with the person it carried over
    (`person`, one of `people`), so each wish in the list stands on its own.
    """
    body = _body()
    text = body.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MESSAGE_TEXT_MAX_LEN:
        raise _RequestError(f"text must be a non-empty string of at most {MESSAGE_TEXT_MAX_LEN} characters")
    prompt = body.get("prompt", "")
    if not isinstance(prompt, str) or len(prompt) > USER_PROMPT_MAX_LEN:
        raise _RequestError(f"prompt must be a string of at most {USER_PROMPT_MAX_LEN} characters")
    history = _review_history(body.get("history", []))
    people = body.get("people", [])
    if (not isinstance(people, list) or len(people) > PROMPT_REVIEW_PEOPLE_MAX
            or not all(isinstance(p, str) and p.strip() and len(p) <= PROMPT_REVIEW_LABEL_MAX_LEN for p in people)):
        raise _RequestError(
            f"people must be a list of at most {PROMPT_REVIEW_PEOPLE_MAX} non-empty strings "
            f"of at most {PROMPT_REVIEW_LABEL_MAX_LEN} characters")
    sections = body.get("sections", [])
    if (not isinstance(sections, list) or len(sections) > PROMPT_REVIEW_SECTIONS_MAX
            or not all(isinstance(x, dict) and isinstance(x.get("who"), str) and isinstance(x.get("text"), str)
                       and len(x["who"]) <= PROMPT_REVIEW_LABEL_MAX_LEN * PROMPT_REVIEW_PEOPLE_MAX
                       and len(x["text"]) <= USER_PROMPT_MAX_LEN for x in sections)):
        raise _RequestError(f"sections must be a list of at most {PROMPT_REVIEW_SECTIONS_MAX} {{who, text}} strings")
    batch = _review_batch(body.get("batch"))
    # 「接續某一版修改」：在已經美化好的版本上再改，只有「接續第 N 版美化」一個鍵，
    # 比例和設定照舊。新的頁面送版本號，舊的送 true（鍵就叫「美化」）。
    continuation = body.get("continuation")
    if not (continuation is True or (type(continuation) is int and 0 < continuation <= 999)):
        continuation = False
    # 一共幾版：「從第二版調」這類換底的話，AI 才知道有哪幾版可以換。
    versions = body.get("versions")
    if not (type(continuation) is int and type(versions) is int and continuation <= versions <= 999):
        versions = 0
    try:
        answer = chat_queue.run({
            "stage": chat_jobs.STAGE_REVIEW, "line": text, "prompt": prompt,
            "sections": [{"who": x["who"], "text": x["text"]} for x in sections],
            "history": history, "people": [p.strip() for p in people],
            **({"batch": batch} if batch else {}),
            **({"continuation": continuation} if continuation else {}),
            **({"versions": versions} if versions else {}),
            "deadline": time.time() + chat_assistant.TIMEOUT_SECONDS}, wait_seconds=PROMPT_REVIEW_WAIT_SECONDS)
    except Exception:
        current_app.logger.exception("chat prompt review failed")
        if history and history[-1].get('question') and not history[-1].get('offer'):
            from photo_agent.clarification_choices import answer_context,completion_retry
            return jsonify(dict(accepted=False,ask=True,**completion_retry(answer_context(history,text))))
        # The model is not a gate the user can be left standing behind: the
        # line goes in, and the session checks it again with an analysis in
        # hand.
        return jsonify({"accepted": True, "reply": i18n.tr("這句我先保留，稍後會一起確認對象與修改內容。"), "semantic_pending": True})
    # The verdict and the sentence come back together, so the list and the
    # reply cannot disagree: a line the user is told was taken down is one the
    # model said to keep.
    accepted = answer["kept"]
    reply = i18n.tr(PROMPT_REVIEW_START_REPLIES.get(answer["start"]) or "") or answer["reply"]
    if not reply and not accepted and not any(answer[k] for k in ('start', 'ask', 'offer')):
        answer = {**answer, 'ask': True, 'options': []}
        reply = i18n.tr(chat_assistant.INTERPRETATION_CLARIFY_REPLY)
    if not reply:
        reply = (chat_assistant._cat(i18n.tr(chat_assistant.KEPT_REPLY), i18n.tr(chat_assistant.KEPT_NEXT))
                 if accepted else i18n.tr(chat_assistant.CLARIFY_REPLY))
    out = {"accepted": accepted, "reply": reply}
    if accepted and answer.get('interpreted') is True:
        out['interpreted'] = True
    if answer.get('verbatim') is True:
        out.update(verbatim=True, line=text)
    if "request_semantics" in answer:
        out["request_semantics"] = answer["request_semantics"]
    if answer.get("semantic_pending"):
        out["semantic_pending"] = True
    if answer["start"]:
        # 「幫我美化」「幫我分析」 are the page's two start buttons in words: the
        # page presses that button, with the lines already in the list.
        out["start"] = answer["start"]
    if answer["ask"]:
        # A question back, not a decision: nothing goes in the list until the
        # user answers it.
        out["ask"] = True
        out["question_kind"] = answer.get("question_kind") if answer.get("question_kind") in ("person", "meaning", "content") else "meaning"
        out["options"] = answer["options"]
    elif answer["offer"]:
        # 「要不要我先幫你分析？」: the user only seemed to want it, so nothing
        # starts until they say so -- the options say it for them.
        out["offer"] = True
        out["options"] = answer["options"]
    if answer.get("continue_from"):
        # 「從第二版調」：頁面切到那一版接著改，已經記下的需求都留著。
        out["continue_from"] = answer["continue_from"]
    # A pending answer may accompany a clarification or plain reply too.
    # Preserve the contract independently of the buttons rendered for this turn.
    if answer.get("later") and not accepted and not answer["start"] and not continuation:
        out["later"] = True
    if accepted and answer["line"] and not answer.get('verbatim'):
        out["line"] = answer["line"][:USER_PROMPT_MAX_LEN]
        if answer["persons"]:
            out["persons"] = answer["persons"]
    if accepted and answer.get("merged"):
        # The sections this line took part of, with what is left of each
        # (`section` is its index in the request's `sections`). The page puts
        # the line in its own section; the reply already says so.
        out["merged"] = [{"section": m["section"], "text": m["text"][:USER_PROMPT_MAX_LEN]} for m in answer["merged"]]
        if answer.get("withdrawn"):
            # The line only took an earlier wish back: the page leaves it out of
            # the list, so the item is simply gone.
            out["withdrawn"] = True
    if answer.get("aspect_ratio") in chat_assistant.REVIEW_RATIOS:
        # A line that names a shape (「做成限動」) sets the page's 長寬比 to it,
        # so the analysis plans for that shape instead of picking its own --
        # also when that was all it said and nothing went into the list.
        out["aspect_ratio"] = answer["aspect_ratio"]
    switches = {k: v for k, v in (answer.get("advanced_options") or {}).items()
                if k in chat_assistant.REVIEW_SWITCHES and isinstance(v, bool)}
    if switches:
        # 「閉眼不用修」: the 人像修正 switches in 「設定」, not a line in the list.
        out["advanced_options"] = switches
    return jsonify(out)


def _review_batch(batch):
    """The overview of several photos a line was typed on, or None for one
    photo's upload page: {photos, unrendered} counts plus the labels of the
    batch's buttons (analyze "" when nothing is left to analyze) and where the
    photo list is, all short."""
    if batch is None:
        return None
    if not isinstance(batch, dict):
        raise _RequestError("batch must be an object")
    out = {}
    for key in ("photos", "unrendered"):
        n = batch.get(key)
        if type(n) is not int or not 0 <= n <= 1000:
            raise _RequestError(f"batch.{key} must be an integer from 0 to 1000")
        out[key] = n
    for key in ("analyze", "render", "where"):
        label = batch.get(key, "")
        if not isinstance(label, str) or len(label) > PROMPT_REVIEW_LABEL_MAX_LEN:
            raise _RequestError(f"batch.{key} must be a string of at most {PROMPT_REVIEW_LABEL_MAX_LEN} characters")
        out[key] = label.strip()
    # Every photo rendered already: a kept line goes to all of them and the
    # render button renders them again.
    redo = batch.get("redo", False)
    if not isinstance(redo, bool):
        raise _RequestError("batch.redo must be a boolean")
    if redo:
        out["redo"] = True
    return out


def _review_history(history):
    """The upload page's conversation, newest last, as {role, text, question, offer, options}.

    Only the tail is kept: the list itself is in `prompt`, and the turns are
    there so the model can tell an answer to its question from a new wish."""
    if not isinstance(history, list):
        raise _RequestError("history must be a list")
    turns = []
    for t in history[-PROMPT_REVIEW_HISTORY_MAX:]:
        if not isinstance(t, dict) or t.get("role") not in ("user", "assistant"):
            raise _RequestError("history turns must be objects with role 'user' or 'assistant'")
        text, options = t.get("text"), t.get("options", [])
        if not isinstance(text, str) or len(text) > MESSAGE_TEXT_MAX_LEN:
            raise _RequestError(f"history text must be a string of at most {MESSAGE_TEXT_MAX_LEN} characters")
        from photo_agent.clarification_choices import is_literal_choice
        if not isinstance(options, list) or not all(isinstance(o, str) and (
                len(o) <= agent_config.CHAT_OPTION_MAX_LEN or
                is_literal_choice(o) and len(o) <= MESSAGE_TEXT_MAX_LEN) for o in options):
            raise _RequestError("history options must be a list of short strings")
        # Preserve original characters for follow-up text-overlay completion.
        turns.append({"role": t["role"], "text": text, "question": t.get("question") is True,
                      "offer": t.get("offer") is True, "options": options[:4]})
    return turns


# --- status ----------------------------------------------------------------

def _job_status(job_id, statuses):
    if job_id not in statuses:
        info = get_job_info(job_id, keyToGet=["status", "error", "error_message", "ts", "acts"]) or {}
        lost = lost_job_error(job_id, info)
        # error is the worker's raw failure text (an OpenAI safety rejection,
        # a timeout's message, ...) and error_code its short code -- the UI
        # turns the pair into user-facing copy, as the 面板模式 UI does.
        if lost:
            statuses[job_id] = {"status": lost["status"], "error": lost["error_message"], "error_code": lost["error"]}
        else:
            # A job the worker is still on has no status yet either; only one
            # whose info is gone has expired out of Redis.
            statuses[job_id] = {"status": info.get("status"), "error": info.get("error_message") or info.get("error"),
                                "error_code": info.get("error"), "expired": not info}
        # Work owns its language, including source-language jobs whose params
        # omit lang. Old/missing job records fall back to the caller's context.
        acts = info.get("acts")
        if isinstance(acts, str):
            acts = json.loads(acts)
        if acts:
            statuses[job_id]["lang"] = i18n.normalize((acts[0].get("params") or {}).get("lang"))
    return statuses[job_id]


def _follow_up_analysis(photo):
    """The analysis behind what a just-analyzed photo gets besides its status:
    its brief, and the names its 個人化需求 refers people by. Both are extra --
    failing to read the analysis here just means neither happens, and the
    render path reports its own read failure."""
    analysis, error = _load_analysis(_analysis_job_id(photo))
    if error:
        current_app.logger.warning("chat follow-up skipped for %s: %s", photo["photo_id"], error)
        return None
    return analysis


def _name_prompt_faces(user, session_id, photo, analysis):
    """Settle the people the 個人化需求 points at against the analysis that
    just landed.

    A line written on the upload page names its person from the local
    detection order, which is the best the page can do with no analysis to
    read. Now there is one: each pick carries the box it was made on, so
    swapping its text for the name the face panel gives that same box is a
    literal replace -- the pick is dropped either way, and a line the user has
    since edited past recognition simply keeps the words it has.
    """
    plan = photo["plan"]
    if not plan.get("prompt_faces"):
        return
    prompt = plan["user_prompt"]
    for ref, named in resolve_prompt_faces(analysis, plan["prompt_faces"]).items():
        if ref != named:
            prompt = prompt.replace(ref, named)
    photo["plan"] = {**plan, "user_prompt": prompt[:USER_PROMPT_MAX_LEN], "prompt_faces": []}
    store.save_photo_plan(user, session_id, photo["photo_id"], photo["plan"])


def _start_face_wishes(photo, analysis):
    """The face-wish job for the 個人化需求 the analysis just met, already
    running alongside the opening brief; None when there is nothing to find."""
    plan = photo["plan"]
    prompt = plan.get("user_prompt") or ""
    if not prompt.strip() or plan.get("face_requests"):
        return None
    # Pool threads do not inherit the caller's language: hand it over.
    return _FACE_WISH_POOL.submit(contextvars.copy_context().run, chat_queue.run,
                                  {"stage": chat_jobs.STAGE_FACE_WISHES, "prompt": prompt, "analysis": analysis})


def _bind_face_wishes(user, session_id, photo, analysis, pending, *, ask=True, host=None):
    """Give each face-symmetry wish `pending` (_start_face_wishes) found to
    the one person it fits, and the reply that says who -- or, with `ask`,
    asks which person when several fit. `host` is the analysis_ready message
    the reply is shown inside."""
    if pending is None:
        return []
    try:
        found = pending.result()
    except Exception:
        current_app.logger.exception("chat face wishes failed")
        return []
    plan = photo["plan"]
    prompt = plan.get("user_prompt") or ""
    names = person_names(analysis)
    requests, bound, unbound = {}, [], False
    for item in found:
        index = item["subject_index"]
        if index not in names:
            unbound = True
            continue
        name, wish, referent = names[index], item["wish"], item["referent"]
        # The render keeps a face request only while its person's name is in user_prompt.
        if name not in prompt:
            if referent and referent in wish:
                prompt = prompt.replace(wish, wish.replace(referent, name, 1), 1)
            elif referent and prompt.count(referent) == 1:
                prompt = prompt.replace(referent, name, 1)
            else:
                prompt = prompt.replace(wish, name + wish, 1)
        requests[f"{index}:face_symmetry"] = True
        if name not in bound:
            bound.append(name)
    label = chat_assistant.FACE_SYMMETRY_LABEL
    text, options = "", []
    if bound:
        new_plan, error = apply_plan_patch(plan, {"user_prompt": prompt[:USER_PROMPT_MAX_LEN], "face_requests": requests})
        if error:
            current_app.logger.warning("chat face wishes not applied: %s", error)
            return []
        photo["plan"] = new_plan
        store.save_photo_plan(user, session_id, photo["photo_id"], new_plan)
        text = i18n.tr("{label}會幫{who}調整。", label=i18n.label(label), who=i18n.tr("、").join(bound))
    if unbound and ask:
        text = i18n.tr("{a}{b}", a=text, b=i18n.tr("{label}要幫哪一位調整？", label=i18n.label(label)))
        options = list(names.values())
    if not text:
        return []
    return [store.make_message("assistant", card={
        "type": "llm_reply", "photo_id": photo["photo_id"], "text": text, "proposal": None, "options": options,
        **({"answers": host["id"]} if host else {})})]


def _submit_brief(analysis, wishes=""):
    """With 詳細解說 on: queue the first note in a retoucher's voice for
    the worker to write, as the photo's pending_brief, or None when it could
    not be queued -- the caller then shows the analysis's own summary and
    issues. Nothing waits for it (a queued render starts right away);
    _advance_texts posts it once it is in."""
    try:
        job_id = chat_queue.submit({"stage": chat_jobs.STAGE_OPENING, "analysis": analysis, "wishes": wishes})
    except Exception:
        current_app.logger.exception("chat opening failed")
        return None
    return {"job_id": job_id, "since": time.time()}


def _valid_brief(brief):
    ok = (isinstance(brief, dict) and isinstance(brief.get("summary"), str)
          and isinstance(brief.get("issues"), list) and all(isinstance(i, str) for i in brief["issues"]))
    return brief if ok else None


def _note_images(photo, job_id, source=None):
    """(before, result) as small data URLs for the note under a finished
    version, or (None, None) when either cannot be had -- the note then only
    says what the version set out to do. Before is the original, or for a
    continuation the result it was made from (`source`). The first run stands
    for a multi-run version."""
    try:
        res0 = (get_job_info(job_id, keyToGet=["0_res0"]) or {}).get("0_res0") or {}
        runs = res0.get("runs")
        keys = (runs[0].get("cusKeys") if runs else res0.get("cusKeys")) or []
        src = (continuation_svc.result_key(source["job_id"], source["run"]) if source
               else (photo.get("src_keys") or [None])[0])
        if not keys or not src:
            return None, None
        before, before_error = download_bytes(src, job_id)
        after, after_error = download_bytes(keys[0], job_id)
        if before_error or after_error or not before or not after:
            return None, None
        return (image_to_data_url(create_preview_bytes(before), "image/jpeg"),
                image_to_data_url(create_preview_bytes(after), "image/jpeg"))
    except Exception:
        current_app.logger.exception("chat render note images failed")
        return None, None


# The last notes a new one is told not to repeat.
EARLIER_NOTES_MAX = 2


def _earlier_versions(user, session_id, photo_id, based_on):
    """(the plan of the version this one was made from, the notes already
    shown under this photo's earlier versions). The plan is None for a first
    version, or for one whose card predates keeping the plan on it."""
    try:
        done = [m["card"] for m in store.get_messages(user, session_id, limit=500)
                if (m.get("card") or {}).get("type") == "render_done" and m["card"].get("photo_id") == photo_id]
    except Exception:
        current_app.logger.exception("chat render note history failed")
        return None, []
    source = next((c for c in reversed(done) if based_on and c.get("version_id") == based_on), None)
    notes = [c["note"] for c in done if isinstance(c.get("note"), str)][-EARLIER_NOTES_MAX:]
    return (source or {}).get("plan"), notes


def _submit_note(user, session_id, photo, pending):
    """With 詳細解說 on: queue a retoucher's note on a finished version
    (`pending`, the photo's pending_note), for the worker to write from the
    original and the result side by side; the job id, or None -- the result
    card then speaks for itself."""
    analysis, error = _load_analysis(_analysis_job_id(photo))
    continuation = pending.get("continuation")
    if error and not continuation:
        return None
    job_id = pending["render_job"]
    previous_plan, earlier_notes = _earlier_versions(user, session_id, photo["photo_id"], pending.get("based_on"))
    with timed("note_images", photo=photo["photo_id"], job=job_id):
        before, after = _note_images(photo, job_id, continuation and continuation["source_result"])
    try:
        return chat_queue.submit({"stage": chat_jobs.STAGE_NOTE, "analysis": analysis, "plan": pending["plan"],
                                  "previous_plan": previous_plan, "earlier_notes": earlier_notes,
                                  "before": before, "after": after, "continuation": continuation,
                                  "lang": pending.get("lang", i18n.current())})
    except Exception:
        current_app.logger.exception("chat render note failed")
        return None


def _advance_texts(user, session_id, photo):
    """The brief and the note the worker writes while the photo itself has
    already moved on: the brief is posted once it is in (or the analysis's
    own summary, if the worker could not write it); the note goes into the
    card of the version it is about. Returns the messages to post."""
    messages, fields = [], {}
    brief = photo.get("pending_brief")
    if brief:
        done, answer = chat_queue.poll(brief["job_id"], brief["since"], chat_jobs.STAGE_OPENING)
        if done and store.claim(user, session_id, brief["job_id"]):
            fields["pending_brief"] = None
            card = _valid_brief(answer)
            if card is None:
                analysis = _load_analysis(_analysis_job_id(photo))[0]
                card = build_brief(analysis) if analysis else None
            if card:
                messages.append(store.make_message("assistant", card={
                    "type": "analysis_brief", "photo_id": photo["photo_id"], **card}))
    note = photo.get("pending_note")
    if note and not note.get("job_id"):
        # Asked here rather than as the version landed, so its card is
        # already up while the images for the note are fetched.
        if store.claim(user, session_id, f"note:{note['version_id']}"):
            job_id = _submit_note(user, session_id, photo, note)
            fields["pending_note"] = {**note, "job_id": job_id, "since": time.time()} if job_id else None
    elif note:
        done, answer = chat_queue.poll(note["job_id"], note["since"], chat_jobs.STAGE_NOTE)
        if done and store.claim(user, session_id, note["job_id"]):
            fields["pending_note"] = None
            text = answer.strip() if isinstance(answer, str) else ""
            if text:
                store.update_card(user, session_id, note["message_id"], {"note": text})
                # Kept with the version too, for 面板模式.
                try:
                    set_version_note(user, photo["case_id"], note["version_id"], text)
                except Exception:
                    current_app.logger.exception("chat render note history failed")
    if fields:
        photo.update(fields)
        store.update_photo_state(user, session_id, photo["photo_id"], fields)
    return messages


def _advance_analyzing(user, session_id, photo, statuses, rebuilt=False):
    """`rebuilt`: a photo reopened from history, whose brief is the
    analysis's own summary -- not worth a worker call on opening a case."""
    job_id = _analysis_job_id(photo)
    job = _job_status(job_id, statuses)
    token = _reanalysis_token(job_id) if photo.get("reanalysis") else job_id
    if job["status"] not in TERMINAL_STATUSES or not store.claim(user, session_id, token):
        return []
    timeline(photo=photo["photo_id"], job=job_id, event="analyzed", **job_spans("analyze", job_id))
    if photo.get("reanalysis"):
        return _advance_reanalysis(user, session_id, photo, job)
    queued = photo["queued_render"]
    photo["queued_render"] = None
    if job["status"] != "success":
        photo["status"] = "failed"
        photo["error"] = job["error"] or "analysis failed"
        store.save_photo_state(user, session_id, photo)
        return [store.make_message("assistant", card={
            "type": "analysis_failed", "photo_id": photo["photo_id"], "error": photo["error"],
            "error_code": job.get("error_code"), "queued_render_cancelled": bool(queued)})]

    photo["status"] = "ready"
    analysis = _follow_up_analysis(photo)
    skipped = bool((analysis or {}).get("_skipped_analysis"))
    messages = [store.make_message("assistant", card={
        "type": "analysis_ready", "photo_id": photo["photo_id"], **({"skipped": True} if skipped else {})})]
    if analysis is not None and not skipped:
        # Before the queued render below reads the plan.
        try:
            _name_prompt_faces(user, session_id, photo, analysis)
        except ValueError as exc:
            # Keep the user's wish/reference for correction. Never silently
            # assign it to another subject, and never auto-render that plan.
            photo["error"] = str(exc)
            store.save_photo_state(user, session_id, photo)
            messages.append(store.make_message("assistant", card={
                "type": "render_failed", "photo_id": photo["photo_id"],
                "reason": "unresolved_preview_person", "error": str(exc)}))
            return messages
        face_wishes = _start_face_wishes(photo, analysis)
        detailed = photo["plan"].get("detailed_explanation") and not rebuilt
        photo["pending_brief"] = _submit_brief(analysis, photo["plan"].get("user_prompt") or "") if detailed else None
        brief = None if photo["pending_brief"] else build_brief(analysis)
        if brief:
            messages.append(store.make_message("assistant", card={
                "type": "analysis_brief", "photo_id": photo["photo_id"], **brief}))
        # A queued 一鍵美化 starts right away, so there is no one to ask.
        messages += _bind_face_wishes(user, session_id, photo, analysis, face_wishes, ask=not queued, host=messages[0])
    # A 長寬比 changed while analyzing is re-planned first; a queued render
    # waits for it.
    photo["queued_render"] = queued
    if analysis is not None and _reframe_if_needed(user, session_id, photo, analysis):
        return messages
    photo["queued_render"] = None
    if not queued:
        store.save_photo_state(user, session_id, photo)
        return messages
    return messages + _start_queued_render(user, session_id, photo, queued)


def _advance_reanalysis(user, session_id, photo, job):
    """The real analysis 「幫我分析」 asked for on a photo whose upload
    skipped it (analyze_photo, or 自訂模式 via _adopt_case_analysis). The
    photo goes back to ready either way; on success a chat-side request
    ("check") lands as the same check card a 「原圖還有可以修的地方」 answer
    opens, so the page lists what it found. One made in 自訂模式 ("custom")
    only posts its brief: the user already went through it there."""
    why = photo["reanalysis"]
    queued = photo["queued_render"]
    photo["status"] = "ready"
    photo["reanalysis"] = None
    photo["queued_render"] = None
    photo_id = photo["photo_id"]
    analysis = _load_analysis(photo["analysis_job_id"])[0] if job["status"] == "success" else None
    if analysis is None:
        # Back to the stub the photo had, so 「只照需求改」 keeps working.
        photo["analysis_job_id"] = None
        photo["error"] = job["error"] or "analysis failed"
        store.save_photo_state(user, session_id, photo)
        return [store.make_message("assistant", card={
            "type": "analysis_failed", "photo_id": photo_id, "error": photo["error"],
            "error_code": job.get("error_code"), "queued_render_cancelled": bool(queued), "reanalysis": True})]
    photo["analysis_skipped"] = False
    photo["planned_ratio"] = _planned_for(analysis)
    photo["error"] = None
    _name_prompt_faces(user, session_id, photo, analysis)
    # The brief the upload skipped: the check card below lists it.
    detailed = photo["plan"].get("detailed_explanation")
    photo["pending_brief"] = _submit_brief(analysis, photo["plan"].get("user_prompt") or "") if detailed else None
    brief = None if photo["pending_brief"] else build_brief(analysis)
    store.save_photo_state(user, session_id, photo)
    pending = _start_face_wishes(photo, analysis)
    messages = [store.make_message("assistant", card={
        "type": "analysis_brief", "photo_id": photo_id, **brief})] if brief else []
    # Before the round below snapshots the plan; said last, where its options can be tapped.
    face_wishes = _bind_face_wishes(user, session_id, photo, analysis, pending, ask=not queued)
    if why == "check":
        plan = {k: copy.deepcopy(photo["plan"][k]) for k in ROUND_KEYS if k in photo["plan"]}
        # The round starts from 只照需求改 (as the page's snapshotOf), so
        # beautifying the whole photo already counts as a change.
        plan["overwrite_prompt"] = True
        messages.append(store.make_message("assistant", card={
            "type": "adjust_opened", "photo_id": photo_id, "via": "check", "requested": True, "plan": plan}))
    messages += face_wishes
    # A 長寬比 changed while analyzing is re-planned first; a queued render
    # waits for it.
    photo["queued_render"] = queued
    if _reframe_if_needed(user, session_id, photo, analysis):
        return messages
    photo["queued_render"] = None
    if not queued:
        return messages
    return messages + _start_queued_render(user, session_id, photo, queued)


def _advance_rendering(user, session_id, photo, statuses):
    pending = photo["pending_render"]
    jobs = {job_id: _job_status(job_id, statuses) for job_id in pending["job_ids"]}
    if any(j["status"] not in TERMINAL_STATUSES for j in jobs.values()):
        return []
    if not store.claim(user, session_id, pending["action_id"]):
        return []
    timeline(photo=photo["photo_id"], job=pending["job_ids"][0], event="rendered",
             **job_spans("render", pending["job_ids"][0]))
    succeeded = [job_id for job_id, j in jobs.items() if j["status"] == "success"]
    photo["status"] = "ready"
    photo["pending_render"] = None
    if succeeded and pending.get("base") == "custom":
        # A 面板模式 render followed by _sync_from_history: no chat plan made it.
        photo["active_version"] = store.active_version(pending["action_id"], succeeded[0])
        card = {"type": "render_done", "photo_id": photo["photo_id"], "version_id": pending["action_id"],
                "job_ids": succeeded, "failed_job_ids": [j for j in pending["job_ids"] if j not in succeeded],
                "via": "custom", **(pending.get("panel_continuation") or {})}
    elif succeeded:
        photo["active_version"] = store.active_version(pending["action_id"], succeeded[0])
        plan = pending.get("plan") or photo["plan"]
        card = {"type": "render_done", "photo_id": photo["photo_id"], "version_id": pending["action_id"],
                "job_ids": succeeded, "failed_job_ids": [j for j in pending["job_ids"] if j not in succeeded],
                # Kept so the next version's note can tell what changed.
                "plan": plan}
        if pending.get("base") == VERSION_BASE:
            # With the styles too, so 再美化 on it can send the same request again.
            card.update({"base": VERSION_BASE, "source_result": pending["source_result"],
                         "prompt": (pending.get("draft") or {}).get("text"),
                         "style_options": (pending.get("draft") or {}).get("style_options") or {}})
    else:
        failed = next((j for j in jobs.values() if j["error"]), {})
        photo["error"] = failed.get("error") or "render failed"
        card = {"type": "render_failed", "photo_id": photo["photo_id"], "version_id": pending["action_id"],
                "error": photo["error"], "error_code": failed.get("error_code")}
        if pending.get("base") == VERSION_BASE:
            card["base"] = VERSION_BASE
    if succeeded and pending.get("base") == VERSION_BASE:
        # Done with the request it was sent; kept after a failure for a retry,
        # and one typed since it went out stays either way.
        draft, sent = photo.get("continuation_draft"), pending.get("draft") or {}
        if draft and continuation_svc.same_source(draft["source_result"], sent.get("source_result")) \
                and draft["revision"] == sent.get("revision"):
            photo["continuation_draft"] = None
    messages = [store.make_message("assistant", card=card)]
    if card["type"] == "render_done" and card.get("plan", {}).get("detailed_explanation"):
        # The card goes up now; _advance_texts adds the note to it once written.
        photo["pending_note"] = {"version_id": pending["action_id"], "message_id": messages[0]["id"],
                                 "render_job": succeeded[0], "based_on": pending.get("based_on"),
                                 "plan": card["plan"], "lang": i18n.current()}
        if pending.get("base") == VERSION_BASE:
            # Compared with the result it was made from, under the request it
            # was sent rather than a recipe.
            sent = pending.get("draft") or {}
            photo["pending_note"]["continuation"] = {
                "source_result": pending["source_result"], "request": sent.get("text") or "",
                "style_options": sent.get("style_options") or {}}
    if succeeded and pending.get("base") == "custom":
        entry = get_history_entry(user, photo["case_id"]) or {}
        messages = _land_panel_version(user, session_id, photo, entry, statuses, card, pending.get("based_on"))
        # An analysis made from 自訂模式 apart from this edit lands with it too.
        messages += _take_case_analysis(user, session_id, photo, entry, statuses) or []
    store.save_photo_state(user, session_id, photo)
    # A 長寬比 changed while rendering is re-planned now.
    _reframe_if_needed(user, session_id, photo)
    return messages


@chat_bp.get("/sessions/<session_id>/status")
def session_status(session_id):
    user = _user(request.args.get("user"))
    _session(user, session_id)
    statuses, messages = _advance_session(user, session_id) or ({}, [])
    return jsonify({"session": store.load_session(user, session_id), "jobs": statuses, "new_messages": messages})


def _advance_session(user, session_id):
    """_advance_photos, by whichever gets to it first: the watcher below or
    a request (the fallback, should the watcher not be running). None when
    another is at it right now -- the caller then reads the state as it is."""
    token = store.lock_advance(user, session_id)
    if token is None:
        return None
    try:
        return _advance_photos(user, session_id, _session(user, session_id))
    finally:
        store.unlock_advance(user, session_id, token)


# How often the watcher lands what active sessions' jobs finished.
WATCH_SECONDS = 1.0


def start_watcher(app):
    """Land finished jobs for every session with a photo waiting on one,
    about once a second, in a thread of this process -- so the next step
    starts, and its card is posted, without waiting for a page to poll, or
    for anyone to have the page open. Each process running the backend may
    run one; the per-session lock keeps them out of each other's way."""
    threading.Thread(target=_watch, args=(app,), name="chat-watcher", daemon=True).start()


def _watch(app):
    while True:
        time.sleep(WATCH_SECONDS)
        with app.app_context():
            try:
                active = store.active_sessions()
            except Exception:
                current_app.logger.exception("chat watcher: listing active sessions failed")
                continue
            for user, session_id in active:
                try:
                    _watch_session(user, session_id)
                except Exception:
                    current_app.logger.exception("chat watcher: session %s failed", session_id)


def _watch_session(user, session_id):
    session = store.load_session(user, session_id)
    # Fallback for legacy jobs without params. Each recorded operation below
    # overrides this with its own language, independent of page switches.
    with i18n.use((session or {}).get("lang")):
        _watch_loaded(user, session_id, session)


def _watch_loaded(user, session_id, session):
    if session is not None and any(store.waiting(p) for p in session["photos"]):
        _advance_session(user, session_id)
        session = store.load_session(user, session_id)
    if session is None or not any(store.waiting(p) for p in session["photos"]):
        store.drop_active(user, session_id)
        # A photo that started waiting since the read above marked the
        # session again before that drop.
        session = store.load_session(user, session_id)
        if session is not None and any(store.waiting(p) for p in session["photos"]):
            store.mark_active(user, session_id)


def _advance_photos(user, session_id, session):
    """Land whatever a session's busy photos were waiting on that has since
    finished, and post it. The status polls run this; so does opening the
    session, since a photo left mid-render (the user switched to 自訂模式)
    would otherwise reopen as still working until the first poll."""
    statuses, messages = {}, []
    for photo in session["photos"]:
        started, status = time.monotonic(), photo["status"]
        if status == "analyzing":
            job_id = _analysis_job_id(photo)
        elif status == "reframing" and photo.get("pending_reframe"):
            job_id = photo["pending_reframe"]["job_id"]
        elif status == "rendering" and photo["pending_render"]:
            job_id = photo["pending_render"]["job_ids"][0]
        else:
            continue
        job = _job_status(job_id, statuses)
        # Both the watcher and a status poll can land this job. Neither the
        # session's creation language nor a later page switch changes it.
        with i18n.use(job.get("lang", i18n.current())):
            if status == "analyzing":
                landed = _advance_analyzing(user, session_id, photo, statuses)
            elif status == "reframing":
                landed = _advance_reframing(user, session_id, photo, statuses)
            else:
                landed = _advance_rendering(user, session_id, photo, statuses)
        if landed:
            # How long the request that noticed it was held up landing it.
            timeline(photo=photo["photo_id"], event=f"advanced_{status}", ms=int((time.monotonic() - started) * 1000),
                     cards="+".join(m["card"]["type"] for m in landed if m.get("card")))
        messages += landed
    store.append_messages(user, session_id, messages)
    texts = []
    for photo in session["photos"]:
        if photo.get("pending_brief") or photo.get("pending_note"):
            texts += _advance_texts(user, session_id, photo)
    store.append_messages(user, session_id, texts)
    messages += texts
    return statuses, messages
