"""Chat page API: a session of photos, their edit plans and a message log,
over the same analyze/finish jobs and history the review page uses.

Nothing here runs a job of its own kind. Adding a photo enqueues exactly
what /analyze does; rendering enqueues exactly what /continue does, one job
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

import copy
import json
import re
import uuid

from flask import Blueprint, current_app, jsonify, request

from aiserver_utils.queue import get_job_info
from backend.routes.api import _apply_review_selections, _parse_and_validate_params, _record_history_safely
from backend.services import chat_queue, chat_session as store
from backend.services.account import (
    HISTORY_MAX_LEN,
    attach_case_analysis,
    clear_history,
    delete_history_entry,
    get_history,
    get_history_entry,
    normalize_username,
)
from backend.services.compatibility import get_min_compatible_version
from backend.services.put_job import download_bytes, enqueue, get_session_id
from backend.services.template_params import get_engine_params_template
from compatibility_version import get_compatibility_version, is_compatible
from image_tools import create_preview_bytes, image_to_data_url
from photo_agent import capabilities, chat_assistant, chat_jobs
from photo_agent.chat_brief import build_brief, resolve_prompt_faces
from photo_agent.chat_plan import (
    USER_PROMPT_MAX_LEN,
    apply_plan_patch,
    build_render_form,
    build_selections,
    default_plan,
)

chat_bp = Blueprint("chat", __name__, url_prefix="/api/photo-agent/chat")

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MESSAGE_TEXT_MAX_LEN = 4000
# The most messages one post can append (the upload page sends its whole conversation at once).
MESSAGES_POST_MAX = 100
SCREEN_LABELS_MAX = 8
TERMINAL_STATUSES = ("success", "error")
# Render bases. Only the original photo for now; rendering on top of an
# earlier version is meant to be added here without changing the route.
RENDER_BASES = ("original",)
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
    analysis_bytes, download_error = download_bytes(result_keys[1 if combined else 0], job_id)
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
    user = _user(_body().get("user"))
    return jsonify(store.create_session(user)), 201


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
    sessions = store.list_sessions(user, limit=LISTED_SESSIONS_MAX)
    covered = {case_id for s in sessions for case_id in s["case_ids"]}
    cases = [_case_summary(e) for e in get_history(user, limit=HISTORY_MAX_LEN)
             if e["image_key"] not in covered and _opens_in_chat(e)]
    rows = sorted(sessions + cases, key=lambda r: r["updated_at"] or 0, reverse=True)
    return jsonify({"sessions": rows[:limit]})


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
        store.delete_session(user, row)
    return jsonify({"deleted": rows})


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
                if any(j["status"] is None for j in jobs.values()):
                    continue  # a job that has expired out of Redis never finishes
                photo["status"] = "rendering"
                photo["pending_render"] = {"action_id": version_id, "job_ids": list(version["runs"]),
                                           "based_on": version.get("based_on"), "base": "custom", "plan": None}
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
            messages.append(store.make_message("assistant", card={
                "type": "render_done", "photo_id": photo["photo_id"], "version_id": version_id,
                "job_ids": succeeded, "failed_job_ids": [j for j in version["runs"] if j not in succeeded],
                "via": "custom"}))
        if _adopt_case_analysis(photo, entry, statuses):
            changed = True
            # Usually long finished by the time the chat opens: land it now,
            # rather than show the photo as 分析中 until the next status poll.
            job_id = photo["analysis_job_id"]
            job = _job_status(job_id, statuses)
            if job["status"] in TERMINAL_STATUSES and store.claim(user, session_id, job_id):
                messages.extend(_advance_reanalysis(user, session_id, photo, job))
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
    if _job_status(job_id, statuses)["status"] in (None, "error"):
        return False
    photo["analysis_job_id"] = job_id
    photo["reanalysis"] = "custom"
    photo["status"] = "analyzing"
    photo["planned_ratio"] = None
    photo["error"] = None
    return True


@chat_bp.get("/sessions/<session_id>")
def get_session(session_id):
    user = _user(request.args.get("user"))
    _advance_photos(user, session_id, _session(user, session_id))
    _sync_from_history(user, session_id, _session(user, session_id))
    session = _session(user, session_id)
    limit = request.args.get("messages_limit", 100, type=int)
    return jsonify({**session, "messages": store.get_messages(user, session_id, limit=max(1, min(limit, store.MESSAGES_MAX_LEN)))})


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
        store.append_messages(user, session_id, [message])
        return jsonify(message), 201
    if not isinstance(batch, list) or not 0 < len(batch) <= MESSAGES_POST_MAX \
            or not all(isinstance(m, dict) for m in batch):
        raise _RequestError(f"messages must be a list of 1 to {MESSAGES_POST_MAX} JSON objects")
    messages = [_posted_message(m) for m in batch]
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
    message = store.replace_last_message(user, session_id, message_id, card)
    if message is None:
        return jsonify({"error": "only the newest user message can be replaced"}), 409
    return jsonify(message)


# --- photos ----------------------------------------------------------------

@chat_bp.post("/sessions/<session_id>/photos")
def add_photos(session_id):
    user = _user(request.form.get("user"))
    session = _session(user, session_id)
    images = [img for key in ("image", "image[]") for img in request.files.getlist(key) if img and img.filename]
    if not images:
        raise _RequestError("at least one image file is required")
    if len(session["photos"]) + len(images) > store.MAX_PHOTOS_PER_SESSION:
        raise _RequestError(f"a session holds at most {store.MAX_PHOTOS_PER_SESSION} photos")

    params, error = _parse_and_validate_params(build_render_form(session["defaults"], "analyze"))
    if error:
        raise _RequestError(error)
    params["stage"] = "analyze"
    # 「只照需求改」 on the upload page: the render only follows the user's
    # words, so the analysis it would never read is skipped -- the worker
    # just sizes the photo. 「幫我分析」 later runs the real one (analyze_photo).
    skip = bool(session["defaults"].get("overwrite_prompt")
                and (session["defaults"].get("user_prompt") or "").strip())
    if skip:
        params["skip_analysis"] = True

    photos, errors = [], []
    for image in images:
        try:
            engine_params = get_engine_params_template()
            engine_params["acts"][0]["params"] = params
            job_id = enqueue(engine_params, src_content=[image.read()])
        except Exception as exc:
            errors.append({"filename": image.filename, "error": str(exc)})
            continue
        # Same history row /analyze writes, so the photo shows up in history
        # (and can be reopened from it) even if the session later expires.
        _record_history_safely(user, job_id, "local", engine_params["srcKeys"][0], job_id, params=params)
        photos.append({
            "photo_id": f"p_{uuid.uuid4().hex[:12]}",
            "filename": image.filename,
            "case_id": engine_params["srcKeys"][0],
            "src_keys": engine_params["srcKeys"],
            "analyze_job_id": job_id,
            "status": "analyzing",
            "error": None,
            "pending_render": None,
            "queued_render": None,
            "active_version": None,
            "analysis_skipped": skip,
        })

    if photos:
        store.add_photos(user, session_id, session, photos)
        store.append_messages(user, session_id, [store.make_message(
            "user", card={"type": "photos_added", "photo_ids": [p["photo_id"] for p in photos]})])
    session = store.load_session(user, session_id)
    added = {p["photo_id"] for p in photos}
    return jsonify({"photos": [p for p in session["photos"] if p["photo_id"] in added], "errors": errors}), 202


def _needs_analysis(photo):
    """A photo uploaded with 「只照需求改」 has no real analysis; a render
    that also beautifies -- overwrite_prompt off, or nothing written to
    follow -- has to wait for one."""
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
    A new 長寬比 on a ready photo starts re-planning its composition."""
    body = _body()
    user = _user(body.get("user"))
    photo = _photo(_session(user, session_id), photo_id)
    plan, error = apply_plan_patch(photo["plan"], body.get("plan"), debug=_debug())
    if error:
        raise _RequestError(error)
    store.save_photo_plan(user, session_id, photo_id, plan)
    photo["plan"] = plan
    _reframe_if_needed(user, session_id, photo)
    return jsonify({"photo_id": photo_id, "plan": plan, "status": photo["status"]})


# --- reframe ---------------------------------------------------------------

def _reframe_if_needed(user, session_id, photo, analysis=None):
    """Re-plan a ready photo's composition when its plan's 長寬比 is not the
    one its analysis was planned for -- the results page's 長寬比 change
    (pipeline.reframe_stage), run as soon as the ratio is picked. A render
    asked for meanwhile waits in queued_render. Returns True if one started.

    planned_ratio caches the analysis's ratio on the photo, so the common
    case -- nothing to re-plan -- reads no analysis."""
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
    params["stage"] = "reframe"
    params["analysis"] = analysis
    engine_params = get_engine_params_template()
    engine_params["srcKeys"] = list(photo["src_keys"])
    engine_params["acts"][0]["params"] = params
    job_id = enqueue(engine_params, src_content=None)
    photo["status"] = "reframing"
    photo["error"] = None
    photo["pending_reframe"] = {"job_id": job_id, "aspect_ratio": ratio, "previous": planned}
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
            "previous": pending["previous"], "error": error, "queued_render_cancelled": bool(queued)})]
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
    messages = [store.make_message("assistant", card={
        "type": "reframe_done", "photo_id": photo_id, "aspect_ratio": pending["aspect_ratio"]})]
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
    run_limit = capabilities.MAX_RUN_COUNT if _debug() else capabilities.GENERAL_MAX_RUN_COUNT
    return {"params": {**params, "analysis": analysis}, "selections": selections,
            "run_count": min(plan["run_count"], run_limit)}, None


def _start_render(user, session_id, photo, prepared, base):
    """Queue one photo's render jobs and mark it rendering."""
    job_ids = [get_session_id() for _ in range(prepared["run_count"])]
    # The version's id is its first job's id, as when the review page fans
    # out N /continue calls under one action_id.
    action_id = job_ids[0]
    based_on = (photo["active_version"] or {}).get("version_id")
    for job_id in job_ids:
        engine_params = get_engine_params_template()
        engine_params["srcKeys"] = list(photo["src_keys"])
        engine_params["acts"][0]["params"] = prepared["params"]
        enqueue(engine_params, src_content=None, job_id=job_id)
        _record_history_safely(
            user, job_id, "local", photo["case_id"], action_id, params=prepared["params"],
            extra={"selections": prepared["selections"], "session_id": session_id,
                   "photo_id": photo["photo_id"], "base": base},
            based_on=based_on,
        )
    photo["status"] = "rendering"
    photo["error"] = None
    photo["queued_render"] = None
    # The plan rides along so the finished version's card can keep it: the
    # next version's note reads what changed from it.
    photo["pending_render"] = {"action_id": action_id, "job_ids": job_ids, "based_on": based_on, "base": base,
                               "plan": copy.deepcopy(photo["plan"])}
    store.save_photo_state(user, session_id, photo)
    return {"photo_id": photo["photo_id"], "action_id": action_id, "job_ids": job_ids}


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
    if base not in RENDER_BASES:
        raise _RequestError(f"base must be one of {RENDER_BASES}")

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
            raise _RequestError(problem[1], problem[2])
        to_render.append((photo, prepared))

    renders = [_start_render(user, session_id, photo, prepared, base) for photo, prepared in to_render]
    for photo in to_analyze:
        _start_analysis(user, session_id, photo, "render")
    to_queue += to_analyze
    for photo in to_queue:
        photo["queued_render"] = {"base": base}
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
        photo["queued_render"] = {"base": "original"}
        store.save_photo_state(user, session_id, photo)
        renders, queued = [], [photo["photo_id"]]
    elif photo["status"] != "ready":
        return [store.make_message("assistant", card={
            "type": "render_failed", "photo_id": photo["photo_id"], "reason": "photo_busy",
            "error": "這張照片正在處理中，完成後再試一次。"})]
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
              "advanced_options", "preset", "run_count", "user_prompt")


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
    """The buttons the page shows right now, {bar, topics, pending}, each a few
    short labels; pending names what this round changed and has not rendered."""
    raw = raw if raw is not None else {}
    if not isinstance(raw, dict):
        raise _RequestError("screen must be an object")
    out = {}
    for key in ("bar", "topics", "pending"):
        labels = raw.get(key, [])
        if not isinstance(labels, list) or len(labels) > SCREEN_LABELS_MAX or not all(
                isinstance(x, str) and 0 < len(x) <= PROMPT_REVIEW_LABEL_MAX_LEN for x in labels):
            raise _RequestError(f"screen.{key} must be a list of at most {SCREEN_LABELS_MAX} short labels")
        out[key] = labels
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
    if photo["status"] == "failed":
        raise _RequestError("this photo's analysis failed", 409)
    screen = _screen(body.get("screen"))

    card = {"type": "ask", "photo_id": photo_id}
    if subject_indices:
        card["subject_indices"] = subject_indices
    asked = store.make_message("user", text.strip(), card)
    history = [m for m in store.get_messages(user, session_id, limit=chat_assistant.HISTORY_LEN)
               if (m.get("card") or {}).get("photo_id") in (None, photo_id)] + [asked]
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
    history = [m for m in store.get_messages(user, session_id)
               if (m.get("card") or {}).get("photo_id") in (None, photo["photo_id"])]
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
        reply["card"]["text"] = "可以幫你這樣調整，確認後套用。"
        return []
    photo_id = photo["photo_id"]
    messages = [store.make_message("user", card={
        "type": "proposal_resolved", "photo_id": photo_id, "proposal_id": proposal["id"], "choice": "apply",
        "auto": True})]
    if photo.get("status") == "ready" and _round_to_open(store.get_messages(user, session_id), photo_id):
        messages.append(store.make_message("assistant", card={
            "type": "adjust_opened", "photo_id": photo_id, "via": "proposal", "auto": True,
            "plan": {k: copy.deepcopy(photo["plan"][k]) for k in ROUND_KEYS if k in photo["plan"]}}))
    store.save_photo_plan(user, session_id, photo_id, plan)
    photo["plan"] = plan
    _reframe_if_needed(user, session_id, photo)
    return messages


def _round_to_open(log, photo_id):
    """True when the photo has been rendered and no round of adjusting is open
    since: the last render came after the last adjust_opened, or the user
    closed that round."""
    rendered = opened = False
    for m in log:
        card = m.get("card") or {}
        if card.get("photo_id") not in (None, photo_id):
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
        analysis, error = _load_analysis(_analysis_job_id(photo))
        if error:
            current_app.logger.warning("chat ask without analysis for %s: %s", photo_id, error)
            analysis = None
    tag = {"answers": answers} if answers else {}
    try:
        answer = chat_queue.run({
            "stage": chat_jobs.STAGE_ASK, "analysis": analysis, "plan": photo["plan"],
            "status": photo["status"], "history": history, "debug": _debug(), "screen": screen})
    except Exception:
        current_app.logger.exception("chat ask failed for %s", photo_id)
        messages = messages + [store.make_message("assistant", card={"type": "llm_error", "photo_id": photo_id, **tag})]
        store.append_messages(user, session_id, messages)
        return jsonify({"messages": messages})

    messages = list(messages)
    proposal = answer["proposal"]
    if proposal:
        proposal = {"id": uuid.uuid4().hex[:12], **proposal}
    if answer["reply"] or proposal or not answer["render"]:
        messages.append(store.make_message("assistant", card={
            "type": "llm_reply", "photo_id": photo_id,
            "text": answer["reply"] or "我不太確定要怎麼調整，可以直接在面板上修改。", "proposal": proposal,
            "options": answer["options"], **({"lines": True} if answer.get("lines") else {}),
            **({"close": True} if answer.get("close") else {}), **tag}))
    if proposal and proposal.get("direct"):
        messages += _apply_directly(user, session_id, photo, messages[-1])
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
# Both outcomes need words of their own for the times the model returns none.
PROMPT_REVIEW_KEPT_REPLY = chat_assistant.KEPT_REPLY + chat_assistant.KEPT_NEXT
PROMPT_REVIEW_DROPPED_REPLY = "不好意思，這個我幫不上忙。想怎麼修這張照片，再跟我說說看喔！"
# What the page sends along with the line.
PROMPT_REVIEW_HISTORY_MAX = 20
PROMPT_REVIEW_PEOPLE_MAX = 20
PROMPT_REVIEW_LABEL_MAX_LEN = 40


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
    try:
        answer = chat_queue.run({
            "stage": chat_jobs.STAGE_REVIEW, "line": text.strip(), "prompt": prompt,
            "history": history, "people": [p.strip() for p in people]})
    except Exception:
        current_app.logger.exception("chat prompt review failed")
        # The model is not a gate the user can be left standing behind: the
        # line goes in, and the session checks it again with an analysis in
        # hand.
        return jsonify({"accepted": True, "reply": ""})
    # The verdict and the sentence come back together, so the list and the
    # reply cannot disagree: a line the user is told was taken down is one the
    # model said to keep.
    accepted = answer["kept"]
    reply = PROMPT_REVIEW_START_REPLIES.get(answer["start"]) or answer["reply"]
    if not reply:
        reply = PROMPT_REVIEW_KEPT_REPLY if accepted else PROMPT_REVIEW_DROPPED_REPLY
    out = {"accepted": accepted, "reply": reply}
    if answer["start"]:
        # 「幫我美化」「幫我分析」 are the page's two start buttons in words: the
        # page presses that button, with the lines already in the list.
        out["start"] = answer["start"]
    if answer["ask"]:
        # A question back, not a decision: nothing goes in the list until the
        # user answers it.
        out["ask"] = True
        out["options"] = answer["options"]
    elif answer["offer"]:
        # 「要不要我先幫你分析？」: the user only seemed to want it, so nothing
        # starts until they say so -- the options say it for them.
        out["offer"] = True
        out["options"] = answer["options"]
        if answer["later"]:
            # A question only the analysis can answer: once the user says yes,
            # the page carries it into the session to be answered there.
            out["later"] = True
    if accepted and answer["line"]:
        out["line"] = answer["line"][:USER_PROMPT_MAX_LEN]
        if answer["persons"]:
            out["persons"] = answer["persons"]
    return jsonify(out)


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
        if not isinstance(options, list) or not all(isinstance(o, str) and len(o) <= PROMPT_REVIEW_LABEL_MAX_LEN for o in options):
            raise _RequestError("history options must be a list of short strings")
        turns.append({"role": t["role"], "text": text.strip(), "question": t.get("question") is True,
                      "offer": t.get("offer") is True, "options": options[:4]})
    return turns


# --- status ----------------------------------------------------------------

def _job_status(job_id, statuses):
    if job_id not in statuses:
        info = get_job_info(job_id, keyToGet=["status", "error"]) or {}
        statuses[job_id] = {"status": info.get("status"), "error": info.get("error")}
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


def _opening_brief(analysis, wishes=""):
    """With 詳細解說 on: the first note in a retoucher's voice, written
    by the worker, or None -- the caller then shows the analysis's own summary
    and issues."""
    try:
        brief = chat_queue.run({"stage": chat_jobs.STAGE_OPENING, "analysis": analysis, "wishes": wishes})
    except Exception:
        current_app.logger.exception("chat opening failed")
        return None
    ok = (isinstance(brief, dict) and isinstance(brief.get("summary"), str)
          and isinstance(brief.get("issues"), list) and all(isinstance(i, str) for i in brief["issues"]))
    return brief if ok else None


def _note_images(photo, job_id):
    """(original, result) as small data URLs for the note under a finished
    version, or (None, None) when either cannot be had -- the note then only
    says what the version set out to do. The first run stands for a
    multi-run version."""
    try:
        res0 = (get_job_info(job_id, keyToGet=["0_res0"]) or {}).get("0_res0") or {}
        runs = res0.get("runs")
        keys = (runs[0].get("cusKeys") if runs else res0.get("cusKeys")) or []
        src = (photo.get("src_keys") or [None])[0]
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


def _render_note(user, session_id, photo, plan, job_id, based_on):
    """With 詳細解說 on: a retoucher's note on a finished version,
    written by the worker from the original and the result side by side, or
    None -- the result card then speaks for itself."""
    analysis, error = _load_analysis(_analysis_job_id(photo))
    if error:
        return None
    previous_plan, earlier_notes = _earlier_versions(user, session_id, photo["photo_id"], based_on)
    before, after = _note_images(photo, job_id)
    try:
        note = chat_queue.run({"stage": chat_jobs.STAGE_NOTE, "analysis": analysis, "plan": plan,
                               "previous_plan": previous_plan, "earlier_notes": earlier_notes,
                               "before": before, "after": after})
    except Exception:
        current_app.logger.exception("chat render note failed")
        return None
    return note.strip() if isinstance(note, str) and note.strip() else None


def _advance_analyzing(user, session_id, photo, statuses, rebuilt=False):
    """`rebuilt`: a photo reopened from history, whose brief is the
    analysis's own summary -- not worth a worker call on opening a case."""
    job_id = _analysis_job_id(photo)
    job = _job_status(job_id, statuses)
    if job["status"] not in TERMINAL_STATUSES or not store.claim(user, session_id, job_id):
        return []
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
            "queued_render_cancelled": bool(queued)})]

    photo["status"] = "ready"
    analysis = _follow_up_analysis(photo)
    skipped = bool((analysis or {}).get("_skipped_analysis"))
    messages = [store.make_message("assistant", card={
        "type": "analysis_ready", "photo_id": photo["photo_id"], **({"skipped": True} if skipped else {})})]
    if analysis is not None and not skipped:
        # Before the queued render below reads the plan.
        _name_prompt_faces(user, session_id, photo, analysis)
        detailed = photo["plan"].get("detailed_explanation") and not rebuilt
        brief = ((_opening_brief(analysis, photo["plan"].get("user_prompt") or "") if detailed else None)
                 or build_brief(analysis))
        if brief:
            messages.append(store.make_message("assistant", card={
                "type": "analysis_brief", "photo_id": photo["photo_id"], **brief}))
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
            "queued_render_cancelled": bool(queued), "reanalysis": True})]
    photo["analysis_skipped"] = False
    photo["planned_ratio"] = _planned_for(analysis)
    photo["error"] = None
    _name_prompt_faces(user, session_id, photo, analysis)
    store.save_photo_state(user, session_id, photo)
    # The brief the upload skipped: the check card below lists it.
    detailed = photo["plan"].get("detailed_explanation")
    brief = ((_opening_brief(analysis, photo["plan"].get("user_prompt") or "") if detailed else None)
             or build_brief(analysis))
    messages = [store.make_message("assistant", card={
        "type": "analysis_brief", "photo_id": photo_id, **brief})] if brief else []
    if why == "check":
        plan = {k: copy.deepcopy(photo["plan"][k]) for k in ROUND_KEYS if k in photo["plan"]}
        # The round starts from 只照需求改 (as the page's snapshotOf), so
        # beautifying the whole photo already counts as a change.
        plan["overwrite_prompt"] = True
        messages.append(store.make_message("assistant", card={
            "type": "adjust_opened", "photo_id": photo_id, "via": "check", "requested": True, "plan": plan}))
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
    succeeded = [job_id for job_id, j in jobs.items() if j["status"] == "success"]
    photo["status"] = "ready"
    photo["pending_render"] = None
    if succeeded and pending.get("base") == "custom":
        # A 面板模式 render followed by _sync_from_history: no chat plan made it.
        photo["active_version"] = store.active_version(pending["action_id"], succeeded[0])
        card = {"type": "render_done", "photo_id": photo["photo_id"], "version_id": pending["action_id"],
                "job_ids": succeeded, "failed_job_ids": [j for j in pending["job_ids"] if j not in succeeded],
                "via": "custom"}
    elif succeeded:
        photo["active_version"] = store.active_version(pending["action_id"], succeeded[0])
        plan = pending.get("plan") or photo["plan"]
        card = {"type": "render_done", "photo_id": photo["photo_id"], "version_id": pending["action_id"],
                "job_ids": succeeded, "failed_job_ids": [j for j in pending["job_ids"] if j not in succeeded],
                # Kept so the next version's note can tell what changed.
                "plan": plan}
        if plan.get("detailed_explanation"):
            note = _render_note(user, session_id, photo, plan, succeeded[0], pending.get("based_on"))
            if note:
                card["note"] = note
    else:
        photo["error"] = next((j["error"] for j in jobs.values() if j["error"]), None) or "render failed"
        card = {"type": "render_failed", "photo_id": photo["photo_id"], "version_id": pending["action_id"],
                "error": photo["error"]}
    store.save_photo_state(user, session_id, photo)
    # A 長寬比 changed while rendering is re-planned now.
    _reframe_if_needed(user, session_id, photo)
    return [store.make_message("assistant", card=card)]


@chat_bp.get("/sessions/<session_id>/status")
def session_status(session_id):
    user = _user(request.args.get("user"))
    statuses, messages = _advance_photos(user, session_id, _session(user, session_id))
    return jsonify({"session": store.load_session(user, session_id), "jobs": statuses, "new_messages": messages})


def _advance_photos(user, session_id, session):
    """Land whatever a session's busy photos were waiting on that has since
    finished, and post it. The status polls run this; so does opening the
    session, since a photo left mid-render (the user switched to 自訂模式)
    would otherwise reopen as still working until the first poll."""
    statuses, messages = {}, []
    for photo in session["photos"]:
        if photo["status"] == "analyzing":
            messages += _advance_analyzing(user, session_id, photo, statuses)
        elif photo["status"] == "reframing" and photo.get("pending_reframe"):
            messages += _advance_reframing(user, session_id, photo, statuses)
        elif photo["status"] == "rendering" and photo["pending_render"]:
            messages += _advance_rendering(user, session_id, photo, statuses)
    store.append_messages(user, session_id, messages)
    return statuses, messages
