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
import copy
import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, Response, current_app, jsonify, request

from aiserver_utils.queue import get_job_info
from backend.routes.api import _apply_review_selections, _parse_and_validate_params, _record_history_safely
from backend.services import chat_queue, chat_session as store, preview_conversation_store as conversation_store
from photo_agent import preview_conversation as conversation_domain
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
from backend.services.put_job import download_bytes, enqueue, get_session_id, lost_job_error
from backend.services.template_params import get_engine_params_template
from compatibility_version import get_compatibility_version, is_compatible
from image_tools import create_preview_bytes, image_to_data_url
from photo_agent import capabilities, chat_assistant, chat_jobs
import config as agent_config
from photo_agent.preview_context import (
    MAX_UPLOAD_BYTES, PREVIEW_CONTEXT_FIELD, source_sha256, preview_versions, validate_preview_binding, validate_preview_context,
)
from photo_agent.chat_brief import build_brief, person_names, resolve_prompt_faces
from photo_agent.subject_preview import _valid_subject_box
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
_FACE_WISH_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="chat-face-wishes")
# Render bases. Only the original photo for now; rendering on top of an
# earlier version is meant to be added here without changing the route.
RENDER_BASES = ("original",)
# The next-step sentence a reply applied without asking ends on
# (chat_assistant._next_step), and what replaces it while a new 長寬比 is
# being reframed: nothing can be pressed until reframe_done.
NEXT_STEP_RE = re.compile(r"(?:還想調整其他項目可以點下面的項目，或按|按下面的)「[^」]+」我就開始。(?:想看整張照片還有哪些地方可以修，也可以按「幫我分析」。)?$")
REFRAMING_NOTE = "正在照新比例重新構圖，好了就可以接著調整或美化。"
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
    for photo in session.get('photos',[]):
        state=photo.get('preview_conversation')
        if state and state.get('action_schema_version') != conversation_domain.person_request.VERSION:
            old=state.get('effective_prompt','')
            updated=conversation_domain.refresh_state(state)
            photo['preview_conversation']=updated
            prompt=photo.get('plan',{}).get('user_prompt','')
            if old and old in prompt:
                photo['plan']={**photo['plan'],'user_prompt':prompt.replace(old,updated['effective_prompt'],1).strip('；')}
            # Read-side migration is a derived view. Persist only through the
            # subsequent owned mutation, never overwrite a concurrent answer
            # from a GET that loaded an older revision.
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
    sessions = [_landed(user, s) if s["status"] in BUSY_SESSION_STATUSES else s
                for s in store.list_sessions(user, limit=LISTED_SESSIONS_MAX)]
    covered = {case_id for s in sessions for case_id in s["case_ids"]}
    cases = [_case_summary(e) for e in get_history(user, limit=HISTORY_MAX_LEN)
             if e["image_key"] not in covered and _opens_in_chat(e)]
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


def _landed(user, row):
    """A busy session's row once whatever it was waiting on has landed --
    the photo state only moves when something advances it, so a render that
    finished after the user left would otherwise stay 美化中 on its tile
    until the session is opened."""
    session = store.load_session(user, row["session_id"])
    if session is None:
        return row
    _advance_photos(user, row["session_id"], session)
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


def _conversation_snapshot(user, preview_id, body):
    if not agent_config.ENABLE_CHAT_SUBJECT_PREVIEW:
        raise _RequestError("Subject preview is disabled", 404)
    info, binding = _owned_preview(user, preview_id)
    if body.get('source_sha256') != binding['source_sha256'] or body.get('analysis_model') != binding['analysis_model']:
        raise _RequestError('Preview conversation source changed', 409)
    resolved = _preview_snapshot(info, binding)
    if not resolved or resolved[0].get('format_version') != 3 or resolved[0]['subject_preview']['state'] != 'ready':
        raise _RequestError('Preview is not ready', 409)
    return binding, resolved[0]['preview_people']


_PREVIEW_SOURCE_ERRORS = frozenset(('semantic_source_not_grounded', 'semantic_target_not_grounded',
    'semantic_span_not_grounded', 'semantic_identity_outside_target', 'semantic_group_outside_target'))


def _resolve_preview_wishes(user, binding, head, clauses, people, history, prompt):
    """One source-only recovery within the existing request's total wait budget."""
    started = time.monotonic()
    end = started + chat_queue.WAIT_SECONDS
    for attempt in range(2):
        # A new user turn invalidates the old retry before another model call.
        conversation_store.assert_current(user, binding, head)
        remaining = chat_queue.WAIT_SECONDS if attempt == 0 else end - time.monotonic()
        if remaining <= 0:
            current_app.logger.warning('Preview conversation budget exhausted preview=%s revision=%s elapsed=%.3fs',
                binding['preview_job_id'], (head or {}).get('revision'), time.monotonic() - started)
            break
        try:
            raw = chat_queue.run({'stage': conversation_domain.STAGE,
                'people': conversation_domain.roster_text(people), 'wishes': clauses,
                'history': history if attempt == 0 else [], 'effective_prompt': prompt,
                'deadline': time.time() + remaining}, wait_seconds=remaining)
        except Exception as exc:
            current_app.logger.warning('Preview conversation call failed preview=%s task_id=%s attempt=%s category=call error_type=%s elapsed=%.3fs',
                binding['preview_job_id'], chat_queue.current_task_id(), attempt + 1,
                type(exc).__name__, time.monotonic() - started)
            break
        try:
            state = conversation_domain.resolve(clauses, people, raw)
        except Exception as exc:
            source_error = isinstance(exc, ValueError) and str(exc) in _PREVIEW_SOURCE_ERRORS
            current_app.logger.warning('Preview conversation validation failed preview=%s task_id=%s attempt=%s category=%s reason=%s elapsed=%.3fs',
                binding['preview_job_id'], chat_queue.current_task_id(), attempt + 1,
                'source_validation' if source_error else 'validation',
                str(exc) if isinstance(exc, ValueError) else type(exc).__name__, time.monotonic() - started)
            if source_error and attempt == 0:
                continue
            break
        current_app.logger.info('Preview conversation accepted preview=%s task_id=%s attempt=%s elapsed=%.3fs',
            binding['preview_job_id'], chat_queue.current_task_id(), attempt + 1, time.monotonic() - started)
        return state
    return {**conversation_domain.fallback(clauses, people), 'semantic_parse_failed': True}


@chat_bp.post('/previews/<preview_id>/conversation-reply')
def preview_conversation_reply(preview_id):
    body = _body()
    user = _user(body.get('user'))
    binding, people = _conversation_snapshot(user, preview_id, body)
    revision = body.get('revision')
    if type(revision) is not int or revision < 0:
        raise _RequestError('Invalid conversation revision')
    try:
        raw_wishes = body.get('wishes', [])
        clauses = conversation_domain.wishes(raw_wishes, people, semantic=True)
        previous = None
        if body.get('previous_token'):
            previous = conversation_store.load_previous(user, body['previous_token'], binding)
            clauses = conversation_domain.retain_bindings(clauses, previous)
        history = _review_history(body.get('history', []))
        if sum(len(t['text']) for t in history) > 12000:
            raise ValueError('history_too_long')
        prompt = body.get('effective_prompt', '')
        if not isinstance(prompt, str) or len(prompt) > USER_PROMPT_MAX_LEN:
            raise ValueError('invalid_effective_prompt')
        head = conversation_store.begin(user, binding, body.get('generation'), revision,
            {'wishes': raw_wishes, 'history': history, 'prompt': prompt})
        if not people and not clauses:
            state = {'reply': '', 'clauses': []}
        else:
            state = _resolve_preview_wishes(user, binding, head, clauses, people, history, prompt)
        if head:
            state['head'] = head
        if previous:
            state = conversation_domain.retain_questions(state, previous)
        if not people:
            conversation_domain.present(state, people)
        saved = conversation_store.save(user, binding, state, people, revision, conversation_store.digest(raw_wishes), presentation_requested=body.get('presentation_requested') is True)
    except ValueError as exc:
        raise _RequestError(str(exc), 409)
    return jsonify(conversation_store.public(saved))


@chat_bp.post('/previews/<preview_id>/conversation-revise')
def preview_conversation_revise(preview_id):
    body = _body()
    user = _user(body.get('user'))
    binding, people = _conversation_snapshot(user, preview_id, body)
    text = body.get('text')
    if not isinstance(text, str) or not text.strip() or len(text) > 1000:
        raise _RequestError('Invalid correction text')
    try:
        selected = body.get('target_ids', [])
        if not isinstance(selected, list) or any(not isinstance(i,str) or i not in {p['preview_person_id'] for p in people} for i in selected):
            raise ValueError('invalid_revision_identity')
        rebasing = 'next_revision' in body
        if rebasing:
            state = conversation_store.load_previous(user, body.get('token'), binding)
            if state.get('head') and state['head']['generation'] != body.get('generation'):
                raise ValueError('conversation_source_changed')
            next_revision = body.get('next_revision')
            if type(next_revision) is not int or next_revision <= state['revision']:
                raise ValueError('stale_correction')
            raw_wishes = body['wishes']
            clauses = conversation_domain.wishes(raw_wishes,people,semantic=True)
            clauses = conversation_domain.retain_bindings(clauses,state)
            clauses = conversation_domain.wish_lifecycle.bind_unambiguous(clauses,people)
            rebased = conversation_domain.fallback(clauses,people)
            for clause in rebased['clauses']:
                if clause['status']=='matched' and clause['scope']=='protection' and clause.get('target_ids'):
                    clause['protected_ids']=clause['target_ids'];clause['target_ids']=[]
            rebased['revision']=state['revision']
            # Existing user confirmations/protection scope remain evidence.
            rebased = conversation_domain.retain_questions(rebased,state)
            state={**state,**rebased}
            if any(c.get('semantic_required') and conversation_domain.person_request.semantics(c)['action_status'] == 'pending' for c in state['clauses']):
                # A concurrent new wish has not been semantically parsed yet.
                # Resolve it before applying a revision to the older request.
                state = _retry_request_semantics(state)
            head=conversation_store.begin(user,binding,body.get('generation'),next_revision,
                {'wishes':raw_wishes,'text':text,'targets':body.get('target_ids',[])})
            if head:state['head']=head
            state['wishes_digest']=conversation_store.digest(raw_wishes)
        else:
            state = conversation_store.load(user, body.get('token'), binding)
            if state['revision'] != body.get('revision'):
                raise ValueError('stale_correction')
            next_revision=state['revision']+1
        updated = conversation_domain.wish_lifecycle.local_revision(state, people, text, selected)
        if updated is None:
            raw = chat_queue.run({'stage': conversation_domain.STAGE, 'mode': 'revision',
                'people': conversation_domain.roster_text(people), 'wishes': state['clauses'],
                'text': text, 'deadline': time.time() + chat_queue.WAIT_SECONDS}, wait_seconds=chat_queue.WAIT_SECONDS)
            updated = conversation_domain.revise(state, people, text, raw, selected)
        elif updated.get('lifecycle_changes'):
            updated['reply'] = conversation_domain.wish_lifecycle.change_reply(updated['lifecycle_changes'], conversation_domain.executable(updated,people))
        if updated is None:
            if rebasing:
                state['reply']=''
                saved=conversation_store.save(user,binding,state,people,next_revision,state['wishes_digest'],previous=state['token'], presentation_requested=body.get('presentation_requested') is True)
                conversation_store.consume(user,state,saved['token'])
                return jsonify({'changed':False,**conversation_store.public(saved)})
            return jsonify({'changed': False})
        saved = conversation_store.save(user, binding, updated, people, next_revision,
            state['wishes_digest'], previous=state['token'], presentation_requested=body.get('presentation_requested') is True)
        conversation_store.consume(user, state, saved['token'])
        return jsonify({'changed': True, **conversation_store.public(saved),
            'revision_mode': saved['revision_mode'], 'affected_request_ids': saved['affected_request_ids']})
    except ValueError as exc:
        raise _RequestError(str(exc), 409)


def _retry_request_semantics(state):
    """Explicit user retry; never paid/model work during a GET migration."""
    try:
        raw = chat_queue.run({'stage': conversation_domain.STAGE,
            'people': conversation_domain.roster_text(state.get('people', [])),
            'wishes': state['clauses'], 'history': [],
            'effective_prompt': state.get('effective_prompt', ''),
            'deadline': time.time() + chat_queue.WAIT_SECONDS}, wait_seconds=chat_queue.WAIT_SECONDS)
        parsed = conversation_domain.resolve(state['clauses'], state.get('people', []), raw)
    except Exception:
        parsed = conversation_domain.fallback(state['clauses'], state.get('people', []))
    return {**state, **parsed}


def _confirmed_target_semantics(state, ids):
    """Only recheck uncertain actions; a target pick cannot supply a new verb."""
    from photo_agent import request_semantics
    q = state.get('question') or {}
    if q.get('kind') not in (None, 'person') or not ids:
        return {}
    people = state.get('people', [])
    if any(pid not in {p['preview_person_id'] for p in people} for pid in ids):
        raise ValueError('stale_or_invalid_confirmation')
    replacements = {}
    for clause in state['clauses']:
        if clause['wish_id'] not in q.get('wish_ids', []) or not clause.get('semantic_required'):
            continue
        value = request_semantics.read(clause)
        if value and value['complete']:
            continue
        replacements[clause['wish_id']] = None
        if not value:
            continue
        try:
            raw = chat_queue.run({'stage': conversation_domain.STAGE, 'mode': 'target_action',
                'clause': copy.deepcopy(clause),
                'selected_people': conversation_domain.roster_text([p for p in people if p['preview_person_id'] in ids]),
                'deadline': time.time() + chat_queue.WAIT_SECONDS}, wait_seconds=chat_queue.WAIT_SECONDS)
            request_semantics.confirm_target_action(copy.deepcopy(clause), raw)
            replacements[clause['wish_id']] = raw
        except Exception:
            # Keep the selected identity; an unavailable interpretation is pending,
            # not evidence that the user omitted an action.
            pass
    return replacements


def _semantic_action_answer(state, wish_ids, text):
    """Interpret a supplement server-side; never accept client semantic fields."""
    from photo_agent import request_semantics
    if not isinstance(text, str) or not text.strip() or len(text) > 1000:
        raise ValueError('請補充想調整的動作。')
    replacements = {}
    for clause in state['clauses']:
        if clause['wish_id'] not in wish_ids or not clause.get('semantic_required'):
            continue
        if not request_semantics.read(clause):
            raise ValueError('new_action_message')
        try:
            raw = chat_queue.run({'stage': conversation_domain.STAGE, 'mode': 'action',
                'clause': clause, 'text': text.strip(),
                'combined_source': request_semantics.evidence(clause) + '；補充：' + text.strip(),
                'deadline': time.time() + chat_queue.WAIT_SECONDS}, wait_seconds=chat_queue.WAIT_SECONDS)
        except Exception as exc:
            raise ValueError('這項修改還沒確認完成，請稍後再試一次。') from exc
        try:
            replacements[clause['wish_id']] = request_semantics.apply_answer(clause, text.strip(), raw)
        except ValueError as exc:
            if str(exc) == 'new_action_message':
                raise
            raise ValueError('還需要確認這項修改的內容，請再補充想調整的對象或動作。') from exc
    return replacements


@chat_bp.post('/previews/<preview_id>/conversation-confirm')
def preview_conversation_confirm(preview_id):
    body = _body()
    user = _user(body.get('user'))
    binding, _ = _conversation_snapshot(user, preview_id, body)
    try:
        ids = body.get('target_ids', [])
        retry_state = conversation_store.load(user, body.get('token'), binding)
        retry_question = retry_state.get('question') or {}
        if retry_question.get('kind') == 'semantic':
            if ids or body.get('target_selection') == 'outside_preview' or retry_question['question_id'] != body.get('question_id') or retry_question['wish_ids'] != body.get('wish_ids') or type(body.get('revision')) is not int or retry_state['revision'] != body['revision']:
                raise ValueError('stale_or_invalid_confirmation')
            updated = _retry_request_semantics(retry_state)
            saved = conversation_store.save(user, binding, updated, retry_state.get('people', []), retry_state['revision'], retry_state['wishes_digest'], previous=retry_state['token'])
            conversation_store.consume(user, retry_state, saved['token'])
            return jsonify(conversation_store.public(saved))
        if body.get('text') and not body.get('edit_scope'):
            scope_state = conversation_store.load(user, body.get('token'), binding)
            if (scope_state.get('question') or {}).get('kind') == 'edit_scope':
                text = body['text']
                if isinstance(text, str) and re.fullmatch(r'\s*(?:只整理露出的髮絲|先不處理這項需求)[。！!]?\s*', text):
                    body['edit_scope'] = 'visible_hair' if '露出' in text else 'cancel'
                else:
                    return jsonify({**conversation_store.public(scope_state), 'confirmation_handled': False})
        semantic_answers = None
        if (body.get('text') and (conversation_store.load(user, body.get('token'), binding).get('question') or {}).get('kind') in ('edit_description', 'action')):
            body['description_text'] = body['text']
            action_state = conversation_store.load(user, body.get('token'), binding)
            q = action_state.get('question')
            if q['question_id'] != body.get('question_id') or q['wish_ids'] != body.get('wish_ids') or action_state['revision'] != body.get('revision'):
                raise ValueError('stale_or_invalid_confirmation')
            semantic_answers = _semantic_action_answer(action_state, q['wish_ids'], body['text'])
        if not body.get('description_text') and body.get('target_selection', 'listed') == 'listed' and body.get('text') and not ids and not body.get('protection_scope') and not body.get('selected_wish') and not body.get('edit_scope'):
            state = conversation_store.load(user, body.get('token'), binding)
            q = state['question']
            if not q or q['question_id'] != body.get('question_id') or q['wish_ids'] != body.get('wish_ids') or state['revision'] != body.get('revision'):
                raise ValueError('stale_or_invalid_confirmation')
            text = body['text']
            if not isinstance(text, str) or not text.strip() or len(text) > 1000:
                raise ValueError('invalid_confirmation_text')
            # Only interpret the answer's referent. Keep the original operation;
            # a clarification cannot create another wish or release all wishes.
            source = next(c for c in state['clauses'] if c['wish_id'] == q['wish_ids'][0])
            clarification = {**source, 'text': text, 'reference_text': text, 'source_text': text,
                             'status': 'pending', 'target_ids': [], 'protected_ids': []}
            try:
                raw = chat_queue.run({'stage': conversation_domain.STAGE, 'mode': 'confirmation',
                    'people': conversation_domain.roster_text(state['people']), 'wishes': [clarification],
                    'history': [{'role': 'assistant', 'text': q['text']}, {'role': 'user', 'text': text}],
                    'effective_prompt': '', 'deadline': time.time() + chat_queue.WAIT_SECONDS}, wait_seconds=chat_queue.WAIT_SECONDS)
                if raw.get('confirmation_intent') == 'new_message':
                    return jsonify({**conversation_store.public(state), 'confirmation_handled': False})
                if raw.get('confirmation_intent') != 'answer':
                    raise ValueError('invalid_confirmation_intent')
                raw = {k: v for k, v in raw.items() if k != 'confirmation_intent'}
                resolved = conversation_domain.resolve([clarification], state['people'], raw)
                clause = resolved['clauses'][0]
                ids = clause['target_ids'] or clause['protected_ids']
            except Exception:
                ids = []
            if not ids:
                return jsonify(conversation_store.public({**state, 'presentation': None, 'reply': q['text']}))
        target_state = conversation_store.load(user, body.get('token'), binding)
        target_q = target_state.get('question') or {}
        if target_q.get('question_id') != body.get('question_id') or target_q.get('wish_ids') != body.get('wish_ids') or type(body.get('revision')) is not int or target_state['revision'] != body['revision']:
            raise ValueError('stale_or_invalid_confirmation')
        target_semantics = _confirmed_target_semantics(target_state, ids) if body.get('target_selection', 'listed') == 'listed' else {}
        saved = conversation_store.confirm(user, body.get('token'), binding,
            body.get('question_id'), body.get('wish_ids'), ids, body.get('revision'), body.get('protection_scope'), body.get('selected_wish'), body.get('edit_scope'), presentation_requested=body.get('presentation_requested') is True, target_selection=body.get('target_selection', 'listed'), description_text=body.get('description_text'), semantic_answers=semantic_answers, target_semantics=target_semantics)
    except ValueError as exc:
        if str(exc) == 'new_action_message':
            return jsonify({**conversation_store.public(conversation_store.load(user, body.get('token'), binding)), 'confirmation_handled': False})
        raise _RequestError(str(exc), 409)
    return jsonify(conversation_store.public(saved))


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
        raise _RequestError("人物預覽暫時無法讀取，仍可進行分析。", 502)


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
        raise _RequestError("照片大小需介於 1 byte 與 30 MB。", 413)
    try:
        from image_tools import get_image_dimensions
        get_image_dimensions(data)  # existing HEIC/EXIF decoder, not face detection
    except Exception:
        raise _RequestError("無法讀取照片，請使用有效的圖片檔案。", 422)
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
        raise _RequestError("無法開始人物預覽，仍可稍後進行分析。", 503)
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
            return jsonify({**body, "state": "error", "message": "人物預覽未完成，仍可進行分析。"})
        return jsonify({**body, "state": "pending", "message": "正在初步辨識照片中的人物。"})
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
        "message": "人物初步辨識完成。" if preview["state"] == "ready" else "人物預覽暫時不可用，仍可進行分析。"})


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
        raise _RequestError("無法讀取人物預覽圖片。", 502)

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
    # Scene-only 「只照需求改」 can skip analysis. After loading the wishes
    # below, person targets override this shortcut to establish identities.
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
            conversation_state = None
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
                            raise _RequestError('人物預覽版本不符，請重新預覽。', 409)
                        photo_params["precomputed_preview"] = snapshot
                    elif strict_ready:
                        raise _RequestError('人物清單暫時無法交接，請重試預覽。', 409)
                except _RequestError as exc:
                    if strict_ready:
                        raise _RequestError("人物清單暫時無法交接，請重試預覽。", 409) from exc
                    pass
            # Freeze unresolved accepted requests even when the bridge has not
            # finished. Formal analysis starts now; actual editing is guarded.
            conversation_wishes = request.form.get('conversation_wishes')
            if conversation_wishes:
                try:
                    raw_wishes = json.loads(conversation_wishes)
                    people = photo_params.get('precomputed_preview', {}).get('preview_people', [])
                    clauses = conversation_domain.wishes(raw_wishes, people, semantic=True)
                    conversation_state = {**conversation_domain.fallback(clauses, people),
                        'people': people, 'binding': photo_params.get('precomputed_preview', {}).get('binding'),
                        'wishes_digest': conversation_store.digest(raw_wishes), 'revision': 0}
                    conversation_state['question'] = conversation_domain.question(conversation_state, people)
                except (ValueError, TypeError) as exc:
                    raise _RequestError('Invalid pending conversation wishes', 409) from exc
            conversation_token = request.form.get('conversation_token')
            if conversation_token:
                if not preview_id or 'precomputed_preview' not in photo_params:
                    raise _RequestError('Conversation requires its completed preview', 409)
                try:
                    previous = conversation_store.load_previous(user, conversation_token, binding)
                    if request.form.get('conversation_wishes_digest') != previous['wishes_digest']:
                        raise ValueError('conversation_wishes_changed')
                    if conversation_wishes:
                        # Rebuild from this submission's exact source wishes;
                        # only unchanged IDs/text inherit server-owned evidence.
                        clauses = conversation_domain.retain_bindings(clauses, previous)
                        conversation_state = conversation_domain.retain_questions(
                            {**conversation_domain.fallback(clauses, people), 'people': people,
                             'binding': binding, 'wishes_digest': conversation_store.digest(raw_wishes),
                             'revision': previous['revision']}, previous)
                        conversation_state = conversation_domain.refresh_state(conversation_state)
                    else:
                        conversation_state = previous
                except ValueError as exc:
                    raise _RequestError(str(exc), 409)
                photo_params['preview_wish_bindings'] = conversation_state
                # The confirmed scope must travel with actual job instructions,
                # including API callers that did not first patch session defaults.
                if conversation_state.get('effective_prompt') and not photo_params.get('user_prompt'):
                    photo_params['user_prompt'] = conversation_state['effective_prompt'][:USER_PROMPT_MAX_LEN]
            if conversation_state is not None:
                photo_params['preview_wish_bindings'] = conversation_state
                photo_params['user_prompt'] = conversation_domain.executable(conversation_state, conversation_state.get('people', []))[:USER_PROMPT_MAX_LEN]
            request_context = chat_analysis.project(conversation_state,
                prompt=photo_params.get('user_prompt') or '', source_sha256=source_sha256(data),
                analysis_model=photo_params['analysis_model'])
            if request_context:
                photo_params['analysis_user_requests'] = request_context
                if getattr(agent_config, 'ENABLE_CHAT_REQUEST_FEASIBILITY', False) and chat_analysis.visual_subset(request_context):
                    photo_params.pop('skip_analysis', None)
            # Only-wishes editing still needs analysis when a wish names or
            # protects a person. Keep overwrite_prompt; this pass establishes
            # identity without enabling automatic beautification.
            if _conversation_needs_identity(conversation_state):
                photo_params.pop('skip_analysis', None)
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
            "preview_conversation": conversation_state,
            **({"client_key": client_key} if client_key else {}),
        })

    if photos:
        store.add_photos(user, session_id, session, photos)
        for photo in photos:
            state = photo.get('preview_conversation')
            if state:
                plan = store.load_session(user, session_id)['defaults']
                plan = default_plan(plan)
                plan['user_prompt'] = conversation_domain.executable(state, state.get('people', []))[:USER_PROMPT_MAX_LEN]
                store.save_photo_plan(user, session_id, photo['photo_id'], plan)
        store.append_messages(user, session_id, [store.make_message(
            "user", card={"type": "photos_added", "photo_ids": [p["photo_id"] for p in photos]})])
    session = store.load_session(user, session_id)
    added = {p["photo_id"] for p in photos}
    return jsonify({"photos": [p for p in session["photos"] if p["photo_id"] in added], "errors": errors}), 202


def _conversation_needs_identity(state):
    if not state:
        return False
    if conversation_domain.pending(state):
        return True
    people = state.get('people', [])
    projected = conversation_domain.wish_lifecycle.project(state, people)
    return any((clause.get('target_ids') or clause.get('protected_ids'))
               and conversation_domain.executable({'clauses': [clause]}, people)
               for clause in projected['clauses'])


def _needs_request_validation(photo):
    if not getattr(agent_config, 'ENABLE_CHAT_REQUEST_FEASIBILITY', False) or not photo['plan'].get('user_prompt'):
        return False
    state = photo.get('preview_conversation')
    sha = photo.get('source_sha256') or ((state or {}).get('binding') or {}).get('source_sha256')
    if not sha:
        return True
    payload = chat_analysis.project(state, prompt=photo['plan']['user_prompt'], source_sha256=sha,
        analysis_model=photo['plan']['analysis_model'])
    return bool(payload and chat_analysis.visual_subset(payload))


def _needs_analysis(photo):
    """Skipped photos need analysis for person identities or automatic edits.

    Establishing identities leaves the user's overwrite_prompt choice intact.
    """
    plan = photo["plan"]
    return bool(photo.get("analysis_skipped")
                and (_conversation_needs_identity(photo.get('preview_conversation'))
                     or _needs_request_validation(photo)
                     or not (plan.get("overwrite_prompt") and (plan.get("user_prompt") or "").strip())))


def _start_analysis(user, session_id, photo, why):
    """Run the analysis a photo's upload skipped, on its source image. `why`
    is "check" (the user asked to see it: it lands as a check card) or
    "render" (a render queued behind it starts when it lands)."""
    params, error = _parse_and_validate_params(build_render_form(photo["plan"], "analyze"))
    if error:
        raise _RequestError(error)
    params["stage"] = "analyze"
    state = photo.get('preview_conversation')
    if _conversation_needs_identity(state):
        params['preview_wish_bindings'] = copy.deepcopy(state)
        if any(p.get('identity_source') != 'formal_analysis' for p in state.get('people', [])):
            info = get_job_info(photo['analyze_job_id'], keyToGet=['acts']) or {}
            acts = info.get('acts') or []
            if isinstance(acts, str):
                acts = json.loads(acts)
            context = acts[0]['params'].get('precomputed_preview') if acts else None
            if not context:
                raise _RequestError('人物清單暫時無法交接，請重新上傳照片。', 409)
            context = validate_preview_context(context, src_keys=photo['src_keys'])
            if context['binding']['user'] != user:
                raise _RequestError('人物預覽不屬於目前使用者。', 409)
            params['precomputed_preview'] = context
    initial = get_job_info(photo['analyze_job_id'], keyToGet=['acts']) or {}
    initial_acts = initial.get('acts') or []
    if isinstance(initial_acts, str): initial_acts = json.loads(initial_acts)
    initial_params = initial_acts[0]['params'] if initial_acts else {}
    sha = photo.get('source_sha256') or ((state or {}).get('binding') or {}).get('source_sha256') or (initial_params.get('analysis_user_requests') or {}).get('source_sha256')
    if not sha and getattr(agent_config, 'ENABLE_CHAT_REQUEST_FEASIBILITY', False) and photo['plan'].get('user_prompt'):
        data, error = download_bytes(photo['src_keys'][0], photo['analyze_job_id'])
        if error or not data:
            raise _RequestError('原圖暫時無法讀取，請稍後再試。', 409)
        sha = source_sha256(data)
        photo['source_sha256'] = sha
    if sha:
        requests = chat_analysis.project(state, prompt=photo['plan'].get('user_prompt') or '',
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
    prepared, problem = _prepare_render(photo, defer_unconfirmed=queued.get('defer_unconfirmed', False))
    if problem:
        if problem[0] in ('unresolved_people', 'unresolved_visual_target', 'protected_scope'):
            photo['error'] = None
            store.save_photo_state(user, session_id, photo)
            return [_confirmation_notice(photo, problem)]
        photo["error"] = problem[1]
        store.save_photo_state(user, session_id, photo)
        return [store.make_message("assistant", card={
            "type": "render_failed", "photo_id": photo["photo_id"], "reason": problem[0], "error": problem[1]})]
    render = _start_render(user, session_id, photo, prepared, queued["base"])
    return [store.make_message("assistant", card={"type": "render_started", "queued": True, **render})]


# --- render ----------------------------------------------------------------

def _adopt_request_checks(photo, analysis):
    report = (analysis or {}).get('_chat_request_checks')
    if not report:
        # Switching off an optional new call is not permission to bypass an
        # already recorded unresolved result from the same photo.
        if photo.get('chat_request_checks'): _refresh_visual_question(photo)
        return
    photo['chat_request_checks'] = copy.deepcopy(report)
    state = photo.get('preview_conversation')
    if state is None:
        rows = report['requests']['requests']
        clauses = [dict(wish_id=r['wish_id'], request_id=r['wish_id'], text=r['text'],
            source_text=r['source_text'], status=r['status'], scope=r['scope'],
            target_ids=r['preview_person_ids'], protected_ids=[], referent='', revision=0) for r in rows]
        state = dict(clauses=clauses, people=[], revision=0, reply='',
                     binding={'source_sha256':report['requests']['source_sha256']},
                     wishes_digest=report['requests']['wishes_digest'],
                     effective_prompt=report['requests']['effective_prompt'])
        photo['preview_conversation'] = state
    _refresh_visual_question(photo)


def _refresh_visual_question(photo):
    state = photo.get('preview_conversation')
    if not state: return
    state['question'] = conversation_domain.question(state, state.get('people', []))
    if state['question']: return
    blocked = chat_analysis.blocking({'_chat_request_checks':photo.get('chat_request_checks')}, state)
    if blocked:
        row = blocked[0]
        state['question'] = dict(kind='visual', question_id='visual_' + chat_analysis.fingerprint(row)[:16],
            wish_ids=[row['wish_id']], text=row['question'])


def _conversation_render_problem(photo):
    state = photo.get('preview_conversation')
    blocked = chat_analysis.blocking({'_chat_request_checks':photo.get('chat_request_checks')}, state)
    if blocked:
        return ('unresolved_visual_target', blocked[0]['question'], 409)
    if state and conversation_domain.pending(state):
        return ('unresolved_people', '請先確認個人化需求指定的人物，再開始美化。', 409)
    if state and (state.get('question') or {}).get('kind') in ('scope', 'wish'):
        return ('protected_scope', state['question']['text'], 409)
    if state and any(c.get('protected_ids') or (c['status'] == conversation_domain.OUTSIDE_CONFIRMED and c['scope'] == 'protection') for c in state['clauses']) and not photo['plan'].get('overwrite_prompt'):
        return ('protected_scope', '有保持原樣的人物，請先選擇僅依需求修改，確認保護範圍。', 409)
    return None


def _confirmation_notice(photo, problem):
    """An expected clarification is a conversation turn, not a render failure."""
    state = photo.get('preview_conversation') or {}
    question = state.get('question')
    return store.make_message('assistant', card={'type': 'llm_reply',
        'photo_id': photo['photo_id'], 'text': question['text'] if question else problem[1],
        **({'target_question': copy.deepcopy(question), 'question_revision': state['revision']}
           if question else {})})


def _confirmation_deferred_notice(photo):
    return store.make_message('assistant', card={'type': 'llm_reply',
        'photo_id': photo['photo_id'],
        'text': '我會先照你的原句處理，完成後再和你確認需要調整的對象。'})


def _render_without_guessed_targets(photo):
    """Pass complete unresolved edits verbatim, without inferred person bindings."""
    state = photo.get('preview_conversation')
    pending = conversation_domain.pending(state) if state else []
    # Unresolved protection must never be silently omitted.
    if not pending or any(c['scope'] != 'edit' or
            conversation_domain.person_request.semantics(c)['action_status'] != 'complete'
            for c in pending):
        return photo
    snapshot = copy.deepcopy(photo)
    state = snapshot['preview_conversation']
    deferred = {c['wish_id'] for c in pending}
    state['clauses'] = [c for c in state['clauses'] if c['wish_id'] not in deferred]
    state.pop('revision_question', None)
    state['question'] = conversation_domain.question(state, state.get('people', []))
    state['effective_prompt'] = conversation_domain.executable(state, state.get('people', []))
    raw = [c['text'] for c in pending]
    snapshot['plan']['user_prompt'] = '；'.join(dict.fromkeys(
        text for text in (state['effective_prompt'], *raw) if text))[:USER_PROMPT_MAX_LEN]
    snapshot['plan']['prompt_faces'] = []
    return snapshot


def _prepare_render(photo, *, defer_unconfirmed=False):
    original = photo
    if defer_unconfirmed:
        photo = _render_without_guessed_targets(photo)
    conversation_problem = _conversation_render_problem(photo)
    if conversation_problem:
        return None, conversation_problem
    """Everything a ready photo's render needs, from its plan as it is now:
    (prepared, None) or (None, (reason, message, http_status)), reason being
    a stable code the chat page can word for the user."""
    plan = photo["plan"]
    if any("detection_index" in f or "preview_person_id" in f for f in plan.get("prompt_faces", [])):
        return None, ("unresolved_preview_person", "指定人物尚未確認，請重新指定或清除該需求。", 409)
    params, error = _parse_and_validate_params(build_render_form(plan, "finish"))
    if error:
        return None, ("invalid_plan", f"photo {photo['photo_id']}: {error}", 400)
    params["stage"] = "finish"
    analysis, error = _load_analysis(_analysis_job_id(photo))
    if analysis is not None:
        blocked = chat_analysis.blocking(analysis, photo.get('preview_conversation'))
        if blocked:
            return None, ('unresolved_visual_target', blocked[0]['question'], 409)
        if analysis.get('_chat_request_checks'):
            analysis = copy.deepcopy(analysis)
            # Keep passed evidence for active wishes. Only user-cancelled /
            # removed wishes are excluded from this render's scoped checks.
            state = photo.get('preview_conversation')
            if state is not None:
                active = {c['wish_id'] for c in state['clauses'] if c['status'] not in ('cancelled','superseded')}
                analysis['_chat_request_checks']['results'] = [r for r in analysis['_chat_request_checks']['results'] if r['wish_id'] in active]
    if error:
        return None, ("analysis_unavailable", f"photo {photo['photo_id']}: {error}", 409)
    if not is_compatible(analysis, min_version=get_min_compatible_version()):
        return None, ("analysis_compatibility_version_outdated", "analysis_compatibility_version_outdated", 409)
    selections = build_selections(plan)
    # Per-person facial requests the editor renders as that person's own final state.
    face_requests = {}
    state = photo.get('preview_conversation')
    if state:
        people = state.get('people', [])
        projected = conversation_domain.wish_lifecycle.project(state, people)
        active_clauses = [clause for clause in projected['clauses']
                          if conversation_domain.executable({'clauses': [clause]}, people)]
        required_ids = {person_id for clause in active_clauses
                        for person_id in (clause.get('target_ids') or clause.get('protected_ids') or [])}
        if required_ids - {person['preview_person_id'] for person in people}:
            return None, ('unresolved_preview_person', '指定人物尚未完成正式身份確認。', 409)
        bindings = analysis.get('_preview_person_bindings') or {}
        subjects = analysis.get('subjects') or []
        named = []
        for person in people:
            # The preview roster also contains people no active wish targets.
            # Scene-only editing may intentionally skip formal analysis, so
            # only referenced/protected people require a formal binding.
            if person['preview_person_id'] not in required_ids:
                named.append(person)
                continue
            idx = person.get('subject_index') if person.get('identity_source') == 'formal_analysis' else bindings.get(person['preview_person_id'])
            if type(idx) is not int or not 0 <= idx < len(subjects) or (person.get('identity_source') != 'formal_analysis' and subjects[idx].get('preview_person_id') != person['preview_person_id']):
                return None, ('unresolved_preview_person', '指定人物尚未完成正式身份確認。', 409)
            named.append({**person, 'label': subjects[idx]['label']})
        if people:
            canonical = conversation_domain.executable(state, people)
            resolved_prompt = conversation_domain.executable(state, named)
            # Later accepted session requests remain in the plan. Rebind only
            # the exact original canonical fragment, never overwrite new edits.
            if canonical and canonical in params['user_prompt']:
                params['user_prompt'] = params['user_prompt'].replace(canonical, resolved_prompt, 1)[:USER_PROMPT_MAX_LEN]
        # Protection applies to the execution settings, never roster membership.
        for clause in active_clauses:
            for person_id in clause.get('protected_ids', []):
                idx = next((p['subject_index'] for p in people if p['preview_person_id'] == person_id and p.get('identity_source') == 'formal_analysis'), bindings.get(person_id))
                for category in (fix.id for fix in capabilities.PORTRAIT_FIXES):
                    selections['face_issue_overrides'][f'{idx}:{category}'] = False
        for clause in active_clauses:
            op=clause.get('operation') or {}
            if clause['status'] != 'matched' or op.get('intent') != 'set': continue
            if op.get('feature') == 'face_symmetry':
                active_text = conversation_domain.executable({'clauses': [clause]}, named)
                if active_text and active_text not in params['user_prompt']:
                    continue
                for person_id in clause.get('target_ids', []):
                    idx = next((p['subject_index'] for p in people if p['preview_person_id'] == person_id and p.get('identity_source') == 'formal_analysis'), bindings.get(person_id))
                    if type(idx) is int:
                        face_requests[idx] = active_text
                continue
            category = ('blink_issue' if op.get('feature') == 'eyes' and op.get('value') == 'closed' else
                        'gaze_issue' if op.get('feature') == 'gaze' and op.get('value') not in ('camera', '視線看鏡頭') else None)
            if category:
                active_text=conversation_domain.executable({'clauses':[clause]},named)
                if active_text and active_text not in params['user_prompt']:continue
                for person_id in clause.get('target_ids',[]):
                    idx=next((p['subject_index'] for p in people if p['preview_person_id']==person_id and p.get('identity_source')=='formal_analysis'),bindings.get(person_id))
                    if idx is not None: selections['face_issue_overrides'][f'{idx}:{category}']=False
        params['preview_wish_bindings'] = copy.deepcopy(state)
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
            "run_count": min(plan["run_count"], run_limit),
            "deferred_confirmation": photo is not original}, None


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
                               "plan": copy.deepcopy(photo["plan"]),
                               "deferred_confirmation": prepared.get('deferred_confirmation', False)}
    if prepared.get('deferred_confirmation'):
        photo['pending_render']['plan']['user_prompt'] = prepared['params']['user_prompt']
        photo['plan']['user_prompt'] = prepared['params']['user_prompt']
        photo['deferred_request_texts'] = {c['wish_id']:c['text']
            for c in conversation_domain.pending(photo['preview_conversation'])}
        store.save_photo_plan(user, session_id, photo['photo_id'], photo['plan'])
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
    defer_unconfirmed = body.get('defer_unconfirmed', False)
    if type(defer_unconfirmed) is not bool:
        raise _RequestError('defer_unconfirmed must be a boolean')

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
        problem = _conversation_render_problem(_render_without_guessed_targets(photo) if defer_unconfirmed else photo)
        if problem:
            return jsonify({'error': problem[1], 'reason': problem[0]}), problem[2]
        if photo["status"] in ("analyzing", "reframing") and not photo["queued_render"]:
            to_queue.append(photo)
            continue
        if photo["status"] == "ready" and _needs_analysis(photo):
            to_analyze.append(photo)
            continue
        if photo["status"] != "ready":
            state = "queued" if photo["queued_render"] else photo["status"]
            raise _RequestError(f"photo {photo['photo_id']} is {state}, not ready to render", 409)
        prepared, problem = _prepare_render(photo, defer_unconfirmed=defer_unconfirmed)
        if problem:
            return jsonify({'error': problem[1], 'reason': problem[0]}), problem[2]
        to_render.append((photo, prepared))

    renders = [_start_render(user, session_id, photo, prepared, base) for photo, prepared in to_render]
    for photo in to_analyze:
        _start_analysis(user, session_id, photo, "render")
    to_queue += to_analyze
    for photo in to_queue:
        if defer_unconfirmed:
            snapshot = _render_without_guessed_targets(photo)
            if snapshot is not photo:
                photo['plan']['user_prompt'] = snapshot['plan']['user_prompt']
                photo['deferred_request_texts'] = {c['wish_id']:c['text']
                    for c in conversation_domain.pending(photo['preview_conversation'])}
                store.save_photo_plan(user, session_id, photo['photo_id'], photo['plan'])
        photo["queued_render"] = {"base": base, **({'defer_unconfirmed': True} if defer_unconfirmed else {})}
        store.save_photo_state(user, session_id, photo)
    queued = [p["photo_id"] for p in to_queue]
    store.append_messages(user, session_id, [store.make_message(
        "user", card={"type": "render_requested", "base": base, "renders": renders, "queued": queued})])
    if defer_unconfirmed:
        for photo in photos:
            if _render_without_guessed_targets(photo) is not photo:
                store.append_messages(user, session_id, [_confirmation_deferred_notice(photo)])
    return jsonify({"renders": renders, "queued": queued}), 202


@chat_bp.post('/sessions/<session_id>/photos/<photo_id>/conversation-confirm')
def confirm_photo_conversation(session_id, photo_id):
    body = _body()
    user = _user(body.get('user'))
    photo = _photo(_session(user, session_id), photo_id)
    state = copy.deepcopy(photo.get('preview_conversation'))
    previous_prompt = (state.get('effective_prompt') or conversation_domain.executable(state, state.get('people', []))) if state else ''
    q = state.get('question') if state else None
    if not q or q['question_id'] != body.get('question_id') or q['wish_ids'] != body.get('wish_ids') or type(body.get('revision')) is not int or state['revision'] != body.get('revision'):
        raise _RequestError('人物確認已過時，請重新選擇。', 409)
    if q.get('kind') == 'visual':
        text = body.get('text')
        if body.get('subject_indices') or body.get('target_ids') or not isinstance(text, str) or not text.strip() or len(text) > 1000:
            raise _RequestError('請說明這筆需求要如何調整，或取消這筆需求。', 409)
        if not any(c['wish_id'] in q['wish_ids'] for c in state['clauses']):
            raise _RequestError('需求確認結果已過時，請重新分析。', 409)
        retry = text == '重新分析這筆需求'
        if retry and not getattr(agent_config, 'ENABLE_CHAT_REQUEST_FEASIBILITY', False):
            raise _RequestError('需求影像確認目前未啟用；可以先取消這筆需求。', 409)
        cancel = text == '取消這筆需求'
        if not retry:
            for c in state['clauses']:
                if c['wish_id'] not in q['wish_ids']: continue
                if cancel:
                    c.update(status='cancelled', target_ids=[], protected_ids=[])
                else:
                    # New wording needs new semantic and visual evidence.
                    c.update(text=text, source_text=text, reference_text=text,
                        status=conversation_domain.OUTSIDE_CONFIRMED if c['status'] == conversation_domain.OUTSIDE_CONFIRMED else 'pending',
                        semantic_required=True, revision=c.get('revision',0)+1)
                    for key in ('semantic','operation','display_text','display_target_ids','semantic_answer','action_answer','outside_description'):
                        c.pop(key, None)
            if not cancel:
                state = _retry_request_semantics(state)
                answers = [c for c in state['clauses'] if c['wish_id'] in q['wish_ids'] or c.get('semantic_parent') in q['wish_ids']]
                if answers and all(c['scope'] == 'query' for c in answers):
                    # A question about the photo is not a withdrawal/rewrite.
                    return jsonify({'confirmation_handled':False})
        state['revision'] += 1
        state = conversation_domain.refresh_state(state)
        if not store.claim(user, session_id, f'visual:{photo_id}:{body["revision"]}:{q["question_id"]}'):
            raise _RequestError('需求確認已過時，請重新整理。', 409)
        photo['preview_conversation'] = state
        existing = photo['plan'].get('user_prompt') or ''
        remaining = existing.replace(previous_prompt, state['effective_prompt'], 1).strip('；') if previous_prompt and previous_prompt in existing else '；'.join(dict.fromkeys(x for x in (existing, state['effective_prompt']) if x))
        photo['plan'] = {**photo['plan'], 'user_prompt':remaining[:USER_PROMPT_MAX_LEN]}
        store.save_photo_plan(user, session_id, photo_id, photo['plan'])
        _refresh_visual_question(photo)
        store.save_photo_state(user, session_id, photo)
        store.append_messages(user, session_id, [store.make_message('user', text, card={'type':'ask','photo_id':photo_id}),
            store.make_message('assistant', card={'type':'llm_reply','photo_id':photo_id,
                'text':'已取消這筆需求，其他需求保留。' if cancel else state['question']['text'] if state.get('question') and conversation_domain.pending(state) else '收到，我會重新確認修改目標。'})])
        if retry or (not cancel and not conversation_domain.pending(state)):
            _start_analysis(user, session_id, photo, 'check')
        return jsonify({'conversation':photo['preview_conversation']})
    indices = _subject_indices(body)
    selection = body.get('target_selection', 'listed')
    outside = selection == 'outside_preview'
    if selection not in ('listed', 'outside_preview') or (outside and (indices or body.get('target_ids') or q.get('kind') not in (None, 'person'))):
        raise _RequestError('Invalid target selection', 409)
    ids = []
    if q.get('kind') == 'semantic':
        if indices or body.get('target_ids') or outside:
            raise _RequestError('Invalid semantic retry', 409)
        state = _retry_request_semantics(state)
    elif outside:
        conversation_domain.confirm_outside_target(state, q['wish_ids'])
    elif q.get('kind') in ('edit_description', 'action'):
        if indices or body.get('target_ids'):
            raise _RequestError('Invalid action target', 409)
        try:
            answers = _semantic_action_answer(state, q['wish_ids'], body.get('text'))
            conversation_domain.complete_action(state, q['wish_ids'], body.get('text'), semantic_answers=answers)
        except ValueError as exc:
            if str(exc) == 'new_action_message':
                return jsonify({'confirmation_handled':False})
            raise _RequestError(str(exc), 409)
    elif q.get('kind') not in ('scope', 'wish', 'edit_scope', 'semantic'):
        analysis, error = _load_analysis(_analysis_job_id(photo))
        if error or not indices:
            raise _RequestError('請先選擇已完成分析的人物。', 409)
        subjects = analysis.get('subjects') or []
        for index in indices:
            if index >= len(subjects):
                raise _RequestError('Unknown person', 409)
            pid = subjects[index].get('preview_person_id') or f'formal_{index}'
            if pid not in {p['preview_person_id'] for p in state['people']}:
                raise _RequestError('Unknown person', 409)
            ids.append(pid)
    elif q['kind'] == 'scope':
        if body.get('protection_scope') not in ('features', 'all'):
            raise _RequestError('Invalid protection scope', 409)
        state['protection_scope'] = body['protection_scope']
    elif q['kind'] == 'edit_scope':
        if body.get('edit_scope') not in ('visible_hair', 'cancel'):
            raise _RequestError('Invalid edit scope', 409)
    elif body.get('selected_wish') not in q['wish_ids']:
        raise _RequestError('Invalid correction wish', 409)
    target_semantics = _confirmed_target_semantics(state, ids)
    claim = f'conversation:{photo_id}:{state["revision"]}:{q["question_id"]}'
    if not store.claim(user, session_id, claim):
        raise _RequestError('人物確認已過時，請重新選擇。', 409)
    lifecycle_choice = q.get('kind') == 'wish' and q.get('lifecycle_text')
    if lifecycle_choice:
        state = conversation_domain.confirm_revision_choice(state,state['people'],body['selected_wish'],q['lifecycle_text'])
    for clause in state['clauses']:
        if lifecycle_choice:
            continue
        if q.get('kind') == 'wish' and clause['wish_id'] == body['selected_wish']:
            conversation_domain.clear_outside_target(clause)
            clause.pop('display_text', None)
            clause.update(status='pending', target_ids=[], protected_ids=[])
        elif not outside and q.get('kind') in (None, 'person') and clause['wish_id'] in q['wish_ids']:
            if clause['wish_id'] in target_semantics:
                conversation_domain.request_semantics.confirm_target_action(clause, target_semantics[clause['wish_id']])
            conversation_domain.clear_outside_target(clause)
            clause.pop('display_text', None)
            clause.update(status='matched', awaiting_confirmation=False, binding_source='user_confirmation',
                target_ids=ids if clause['scope'] != 'protection' else [],
                protected_ids=ids if clause['scope'] == 'protection' else [])
        elif q.get('kind') == 'edit_scope' and clause['wish_id'] in q['wish_ids']:
            conversation_domain.confirm_edit_scope(clause, body['edit_scope'])
    if q.get('kind') == 'wish':
        state.pop('revision_question', None)
    state['revision'] += 1
    state = conversation_domain.refresh_state(state)
    if not lifecycle_choice:
        state['reply'] = conversation_domain.acknowledged_reply(state['clauses'], state['people'])
    if q.get('kind') == 'edit_scope' and body.get('edit_scope') == 'cancel':
        state['reply'] = '已取消這項髮絲整理需求。'
    photo['preview_conversation'] = state
    _refresh_visual_question(photo)
    existing_prompt = photo['plan'].get('user_prompt') or ''
    deferred_texts = photo.get('deferred_request_texts') or {}
    replaced_texts = {deferred_texts[wid] for wid in q['wish_ids'] if wid in deferred_texts}
    if replaced_texts:
        existing_prompt = '；'.join(part for part in existing_prompt.split('；') if part not in replaced_texts)
        photo['deferred_request_texts'] = {wid:text for wid,text in deferred_texts.items() if wid not in q['wish_ids']}
    if previous_prompt and previous_prompt in existing_prompt:
        merged_prompt = existing_prompt.replace(previous_prompt, state['effective_prompt'], 1).strip('；')
    else:
        merged_prompt = '；'.join(dict.fromkeys(part for part in (existing_prompt, state['effective_prompt']) if part))
    photo['plan'] = {**photo['plan'], 'user_prompt': merged_prompt[:USER_PROMPT_MAX_LEN]}
    store.save_photo_plan(user, session_id, photo_id, photo['plan'])
    store.save_photo_state(user, session_id, photo)
    store.append_messages(user, session_id, [store.make_message('user', '不在已辨識的人物清單中' if outside else '指定了這筆需求的對象',
        card={'type': 'ask', 'photo_id': photo_id, 'subject_indices': indices}),
        store.make_message('assistant', card={'type': 'llm_reply', 'photo_id': photo_id,
            'text': state['question']['text'] if state['question'] else state['reply'],
            'target_question': copy.deepcopy(state['question']), 'question_revision': state['revision']})])
    return jsonify({'conversation': state})


# --- typed questions ---------------------------------------------------------

def _ask_render(user, session_id, photo):
    """A typed 一鍵美化: render now, or queue it while the photo is analyzing,
    as the render route would. Returns the messages that follow the reply."""
    problem = _conversation_render_problem(photo)
    if problem:
        return [_confirmation_notice(photo, problem)]
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
              "advanced_options", "preset", "run_count", "user_prompt", "pose_face_changes")


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
    rendered, panels the panels a reply may open (left out by older pages)."""
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
    history = [m for m in store.get_messages(user, session_id, limit=1000)
               if _about(m.get("card"), photo_id)][-chat_assistant.HISTORY_LEN:] + [asked]
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
    if _reframe_if_needed(user, session_id, photo):
        # 改了比例就先重新構圖，這時底下沒有能按的鍵：先不指下一步，構圖好了
        # 那則（reframe_done）再把項目和美化接上。
        reply["card"]["text"] = NEXT_STEP_RE.sub("", reply["card"]["text"]) + REFRAMING_NOTE
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
    if _round_to_open(store.get_messages(user, session_id), photo_id):
        messages.append(store.make_message("assistant", card={
            "type": "adjust_opened", "photo_id": photo_id, "via": "open", "auto": True, "plan": snapshot}))
    # via reply: the reply above said it opens, so no tap bubble of the user's own.
    messages.append(store.make_message("user", card={
        "type": "options_opened", "photo_id": photo_id, "category": category, "via": "reply",
        "plan": copy.deepcopy(snapshot)}))
    return messages


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
# Both outcomes need words of their own for the times the model returns none.
PROMPT_REVIEW_KEPT_REPLY = chat_assistant.KEPT_REPLY + chat_assistant.KEPT_NEXT
PROMPT_REVIEW_DROPPED_REPLY = "不好意思，這個我幫不上忙。想怎麼修這張照片，再跟我說說看喔！"
# What the page sends along with the line.
PROMPT_REVIEW_HISTORY_MAX = 20
PROMPT_REVIEW_PEOPLE_MAX = 20
PROMPT_REVIEW_LABEL_MAX_LEN = 40
# 個人化需求一個人一段，加上沒指定對象的那段。
PROMPT_REVIEW_SECTIONS_MAX = PROMPT_REVIEW_PEOPLE_MAX + 1
PROMPT_REVIEW_WAIT_SECONDS = 12


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
    try:
        answer = chat_queue.run({
            "stage": chat_jobs.STAGE_REVIEW, "line": text.strip(), "prompt": prompt,
            "sections": [{"who": x["who"], "text": x["text"]} for x in sections],
            "history": history, "people": [p.strip() for p in people]}, wait_seconds=PROMPT_REVIEW_WAIT_SECONDS)
    except Exception:
        current_app.logger.exception("chat prompt review failed")
        # The model is not a gate the user can be left standing behind: the
        # line goes in, and the session checks it again with an analysis in
        # hand.
        return jsonify({"accepted": True, "reply": "這句我先保留，稍後會一起確認對象與修改內容。", "semantic_pending": True})
    # The verdict and the sentence come back together, so the list and the
    # reply cannot disagree: a line the user is told was taken down is one the
    # model said to keep.
    accepted = answer["kept"]
    reply = PROMPT_REVIEW_START_REPLIES.get(answer["start"]) or answer["reply"]
    if not reply:
        reply = PROMPT_REVIEW_KEPT_REPLY if accepted else PROMPT_REVIEW_DROPPED_REPLY
    out = {"accepted": accepted, "reply": reply}
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
    if accepted and answer.get("merged"):
        # The sections this line took part of, with what is left of each
        # (`section` is its index in the request's `sections`). The page puts
        # the line in its own section; the reply already says so.
        out["merged"] = [{"section": m["section"], "text": m["text"][:USER_PROMPT_MAX_LEN]} for m in answer["merged"]]
        if answer.get("withdrawn"):
            # The line only took an earlier wish back: the page leaves it out of
            # the list, so the item is simply gone.
            out["withdrawn"] = True
    if accepted and answer.get("aspect_ratio") in chat_assistant.REVIEW_RATIOS:
        # A wish that names a shape (「做成限動」) sets the page's 長寬比 to it,
        # so the analysis plans for that shape instead of picking its own.
        out["aspect_ratio"] = answer["aspect_ratio"]
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
        info = get_job_info(job_id, keyToGet=["status", "error", "error_message", "ts"]) or {}
        lost = lost_job_error(job_id, info)
        # error is the worker's raw failure text (an OpenAI safety rejection,
        # a timeout's message, ...) and error_code its short code -- the UI
        # turns the pair into user-facing copy, as the 面板模式 UI does.
        if lost:
            statuses[job_id] = {"status": lost["status"], "error": lost["error_message"], "error_code": lost["error"]}
        else:
            statuses[job_id] = {"status": info.get("status"), "error": info.get("error_message") or info.get("error"),
                                "error_code": info.get("error")}
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
    if not prompt.strip() or plan.get("face_requests") or photo.get("preview_conversation"):
        return None
    return _FACE_WISH_POOL.submit(chat_queue.run, {"stage": chat_jobs.STAGE_FACE_WISHES, "prompt": prompt, "analysis": analysis})


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
        text = f"{label}會幫{'、'.join(bound)}調整。"
    if unbound and ask:
        text += f"{label}要幫哪一位調整？"
        options = list(names.values())
    if not text:
        return []
    return [store.make_message("assistant", card={
        "type": "llm_reply", "photo_id": photo["photo_id"], "text": text, "proposal": None, "options": options,
        **({"answers": host["id"]} if host else {})})]


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
            "error_code": job.get("error_code"), "queued_render_cancelled": bool(queued)})]

    photo["status"] = "ready"
    analysis = _follow_up_analysis(photo)
    skipped = bool((analysis or {}).get("_skipped_analysis"))
    messages = [store.make_message("assistant", card={
        "type": "analysis_ready", "photo_id": photo["photo_id"], **({"skipped": True} if skipped else {})})]
    if analysis is not None and not skipped:
        _adopt_request_checks(photo, analysis)
        state = photo.get('preview_conversation')
        if state and not state.get('people'):
            refs = analysis.get('_local_face_boxes') or []
            state['people'] = [{ 'preview_person_id': subject.get('preview_person_id') or f'formal_{i}',
                'identity_source': 'formal_analysis', 'subject_index': i, 'label': subject.get('label') or f'人物 {i+1}',
                'box': refs[i].get('box', {'x': 0, 'y': 0, 'w': 1, 'h': 1}) if i < len(refs) else {'x': 0, 'y': 0, 'w': 1, 'h': 1}}
                for i, subject in enumerate(analysis.get('subjects') or []) if subject.get('is_person', True)]
            photo['preview_conversation'] = state
            _refresh_visual_question(photo)
        if state:
            snapshot = _render_without_guessed_targets(photo)
            if snapshot is not photo:
                photo['plan']['user_prompt'] = snapshot['plan']['user_prompt']
                photo['deferred_request_texts'] = {c['wish_id']:c['text']
                    for c in conversation_domain.pending(state)}
                store.save_photo_plan(user, session_id, photo['photo_id'], photo['plan'])
        if state and state.get('question') and not (queued and queued.get('defer_unconfirmed')):
            messages.append(store.make_message('assistant', card={'type': 'llm_reply',
                'photo_id': photo['photo_id'], 'text': state['question']['text'],
                'target_question': copy.deepcopy(state['question']), 'question_revision': state['revision']}))
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
        brief = ((_opening_brief(analysis, photo["plan"].get("user_prompt") or "") if detailed else None)
                 or build_brief(analysis))
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
    _adopt_request_checks(photo, analysis)
    photo["analysis_skipped"] = False
    photo["planned_ratio"] = _planned_for(analysis)
    photo["error"] = None
    _name_prompt_faces(user, session_id, photo, analysis)
    store.save_photo_state(user, session_id, photo)
    pending = _start_face_wishes(photo, analysis)
    # The brief the upload skipped: the check card below lists it.
    detailed = photo["plan"].get("detailed_explanation")
    brief = ((_opening_brief(analysis, photo["plan"].get("user_prompt") or "") if detailed else None)
             or build_brief(analysis))
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
        failed = next((j for j in jobs.values() if j["error"]), {})
        photo["error"] = failed.get("error") or "render failed"
        card = {"type": "render_failed", "photo_id": photo["photo_id"], "version_id": pending["action_id"],
                "error": photo["error"], "error_code": failed.get("error_code")}
    store.save_photo_state(user, session_id, photo)
    # A 長寬比 changed while rendering is re-planned now.
    _reframe_if_needed(user, session_id, photo)
    messages = [store.make_message("assistant", card=card)]
    state = photo.get('preview_conversation')
    if pending.get('deferred_confirmation') and state and state.get('question'):
        messages.append(_confirmation_notice(photo, ('unresolved_people', state['question']['text'], 409)))
    return messages


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
