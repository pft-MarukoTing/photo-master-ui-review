import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import requests
from flask import Blueprint, Response, current_app, jsonify, request, send_from_directory
from PIL import Image as PILImage
from werkzeug.utils import safe_join

from aiserver_utils.queue import get_job_info
from compatibility_version import get_compatibility_version, is_compatible
from image_tools import create_preview_bytes, detect_face_boxes, ensure_jpeg_bytes, sniff_image_mime, HISTORY_THUMBNAIL_MAX_SIDE, RESULT_PREVIEW_MAX_SIDE
from photo_agent import capabilities
from photo_agent.param_validation import normalize_advanced_options, validate_photo_agent_request
from photo_agent.selections import apply_selections, validate_selections
from pipeline import analyze_stage, reframe_stage
from backend.services import chat_session
from backend.services.account import attach_case_analysis, create_account, clear_history, delete_history_entry, delete_version, get_history, get_history_entry, get_history_entry_for_job, HISTORY_MAX_LEN, normalize_username, record_history
from backend.services.compatibility import get_current_version as get_current_compatibility_version, get_min_compatible_version
from backend.services.decrypt_user_id import decrypt as decrypt_user_id
from backend.services.put_job import enqueue, create_presigned_url, download_bytes, derive_thumbnail_key, derive_preview_key, derive_view_key, cache_thumbnail, upload_report_screenshot, lost_job_error
from backend.services.report import REPORT_CHAT_FIELDS, REPORT_PAGES, delete_report, get_report, get_report_by_id, get_reported_job_info, get_report_chat, get_report_page, get_reports, is_admin, record_report, save_report_chat, save_report_page, update_report_status
from backend.services.template_params import get_engine_params_template

api_bp = Blueprint("api", __name__, url_prefix="/api/photo-agent")


def _record_history_safely(user, job_id, path, image_key, action_id, params=None, extra=None, based_on=None):
    """History bookkeeping must never fail the actual job submission it's
    attached to -- a bad/missing username here is a lost history row, not a
    500 for a job that otherwise queued/ran fine.

    `extra` is merged into the stored params as-is (the chat routes record the
    session/photo and the selections a render used); `based_on` is the
    version this one was derived from. Both default to what every route here
    has always recorded."""
    if not user:
        return
    try:
        history_params = {
            key: (params or {}).get(key)
            for key in ("preset", "image_quality", "image_model", "analysis_model", "aspect_ratio", "ai_photo_editing", "run_qa", "user_prompt", "overwrite_prompt", "run_count", "advanced_options", "single_pass_edit", "stage")
        }
        for key in ('side_reference','literal_texts'):
            if key in (params or {}):history_params[key]=params[key]
        if path == "local":
            history_params["compatibility_version"] = _history_compatibility_version(params)
        history_params.update(extra or {})
        record_history(normalize_username(user), job_id, path, image_key, action_id, params=history_params, based_on=based_on)
    except Exception:
        pass


def _history_compatibility_version(params):
    """compatibility_version of the analysis this history entry's latest
    action runs on, stored on the entry so the history list can show the
    "可更新" (outdated) badge without the frontend fetching every job's full
    /result just to read analysis.compatibility_version off it.

    /continue and /rerun carry the analysis itself. /analyze, /analyze-batch
    and a combined /submit have the worker produce it, stamped with the
    worker's current version -- the same value compatibility.py publishes.
    """
    analysis = (params or {}).get("analysis")
    if isinstance(analysis, dict):
        return get_compatibility_version(analysis)
    return get_current_compatibility_version()


def _apply_review_selections(analysis, raw_selections):
    """Returns (effective_analysis, None) or (None, error_message).

    `raw_selections` is the review screen's choices (dict, or a JSON string
    from a multipart form), applied onto the analysis the user reviewed -- see
    photo_agent/selections.py. Absent selections pass the analysis through
    untouched, which is what an older frontend that still sends an
    already-rewritten analysis relies on.
    """
    if raw_selections is None or raw_selections == "":
        return analysis, None
    if isinstance(raw_selections, str):
        try:
            raw_selections = json.loads(raw_selections)
        except (TypeError, ValueError):
            return None, "selections must be valid JSON"
    error = validate_selections(raw_selections)
    if error:
        return None, error
    return apply_selections(analysis, raw_selections), None


# 「只照需求改」的 case 只有 skipped_analysis_stage 的空殼，沒有 subject_category；
# 面板上改了需求或挑了風格再美化，照樣接著修。空殼沒有任何建議可修，只有照使用者
# 自己的需求改才修得出東西。
SKIPPED_NEEDS_PROMPT = "這張照片還沒分析，只能依照個人化需求修圖；要整張美化請先按「幫我分析」。"


def _is_skipped_analysis(analysis):
    return isinstance(analysis, dict) and bool(analysis.get("_skipped_analysis"))


def _skipped_without_prompt(analysis, params):
    """True when a skipped-analysis stub is sent without a prompt-only edit to make."""
    return _is_skipped_analysis(analysis) and not (
        params["overwrite_prompt"] and (params.get("user_prompt") or "").strip())


def _parse_and_validate_params(form, *, chat_body_side_clarification=True):
    """Returns (params, None) or (None, error_message).

    `form` may be a werkzeug form (multipart routes) or a plain dict (JSON
    routes like /continue) -- both support .get(key, default), and
    ai_photo_editing is coerced to str() first since a JSON body may send a
    real bool instead of the string "true"/"false" multipart forms send.
    """
    params = {
        "preset": form.get("preset", capabilities.DEFAULT_PRESET),
        "image_quality": form.get("image_quality", capabilities.DEFAULT_QUALITY),
        "image_model": form.get("image_model", capabilities.DEFAULT_IMAGE_MODEL),
        "analysis_model": form.get("analysis_model", capabilities.DEFAULT_ANALYSIS_MODEL),
        "aspect_ratio": form.get("aspect_ratio", capabilities.DEFAULT_ASPECT_RATIO),
        "ai_photo_editing": str(form.get("ai_photo_editing", "true")).lower() == "true",
        "run_qa": str(form.get("run_qa", current_app.config.get("DEBUG_UI", False))).lower() == "true",
        "overwrite_prompt": str(form.get("overwrite_prompt", "false")).lower() == "true",
        # Default: one-shot edit (skips Step 5b's separate gaze-repair pass,
        # see pipeline.finish_stage) instead of the two-call alternative --
        # see pipeline.finish_stage for the accompanying image_quality bump,
        # applied there instead of here since it's gated on whether this
        # photo's analysis actually has an approved gaze fix to bundle.
        "single_pass_edit": str(form.get("single_pass_edit", "true")).lower() == "true",
    }
    user_prompt = form.get("user_prompt")
    if user_prompt:
        params["user_prompt"] = user_prompt
    if form.get('literal_texts') is not None:
        from photo_agent.text_overlay import validate_texts
        try:
            value = form.get('literal_texts')
            if isinstance(value, str):
                value = json.loads(value)
            params['literal_texts'] = validate_texts(value, user_prompt or '')
        except (ValueError, TypeError) as exc:
            return None, str(exc)
    # Advanced Settings toggles (Eyes category, and whatever joins it later),
    # bundled as one JSON field instead of one form field per toggle -- see
    # normalize_advanced_options for why. Normalized here (not left as the
    # raw string) since this function's own `params` dict, not the discarded
    # return value of validate_photo_agent_request below, is what every
    # caller of this function actually reads.
    try:
        params["advanced_options"] = normalize_advanced_options(form.get("advanced_options"))
    except RuntimeError as exc:
        return None, (exc.args[0] if exc.args else str(exc))
    # Only the combined ("full" stage) job in job_processor.py actually reads
    # this; a non-numeric value is passed through as None so the shared
    # validator below rejects it with a clean 400 instead of this raising
    # ValueError itself.
    try:
        params["run_count"] = int(form.get("run_count", 1))
    except (TypeError, ValueError):
        params["run_count"] = None

    # Server-only Chat policy; never read this switch from a public form.
    if chat_body_side_clarification is False:
        params["chat_body_side_clarification"] = False

    # Fast sync validation via the same validator the worker runs later,
    # so bad params 400 immediately instead of erroring after being queued.
    task_info = {
        "effect": "photoAgent",
        "acts": [{"id": 0, "params": params}],
        "srcKeys": ["placeholder"],
        "dstKeys": ["placeholder"],
    }
    try:
        validate_photo_agent_request(task_info)
    except RuntimeError as exc:
        message = exc.args[0] if exc.args else str(exc)
        return None, message
    return params, None


DEMO_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
# Optional manifest, sits alongside the photos themselves -- see
# _ordered_demo_image_names for the ordering rule it drives. Plain filename,
# not a config.py setting, since editing it is meant to be as low-friction as
# dropping a new photo into the folder: no server restart, no code touched.
DEMO_IMAGE_ORDER_FILE = "order.json"


def _ordered_demo_image_names(demo_dir, available):
    """`available` (a set of filenames) ordered per demo_images/order.json if
    present -- a JSON array of filenames, shown in that order (entries no
    longer in `available`, e.g. a deleted photo, are silently skipped).
    Anything in `available` but NOT listed in the manifest is appended after,
    alphabetically -- so a newly-added photo always shows up (at the end)
    without requiring the manifest to be updated first. Falls back to plain
    alphabetical order if the manifest is missing or unparsable, so it's
    optional, not a hard requirement for the picker to work at all.
    """
    try:
        with open(os.path.join(demo_dir, DEMO_IMAGE_ORDER_FILE), "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        manifest = []
    if not isinstance(manifest, list):
        manifest = []

    ordered = []
    seen = set()
    for name in manifest:
        if name in available and name not in seen:
            ordered.append(name)
            seen.add(name)
    ordered.extend(sorted(available - seen))
    return ordered


@api_bp.get("/demo-images")
def demo_images():
    demo_dir = current_app.config.get("DEMO_IMAGES_DIR")
    if not demo_dir or not os.path.isdir(demo_dir):
        return jsonify({"images": []})
    available = {
        name for name in os.listdir(demo_dir)
        if os.path.splitext(name)[1].lower() in DEMO_IMAGE_EXTENSIONS
    }
    return jsonify({"images": _ordered_demo_image_names(demo_dir, available)})


@api_bp.get("/demo-images/<path:filename>")
def demo_image_file(filename):
    """Serves one demo_images/ file by name -- send_from_directory's own
    safe_join guards against path traversal the same way static.py's
    static_files() route relies on for FRONTEND_DIR."""
    demo_dir = current_app.config.get("DEMO_IMAGES_DIR")
    if not demo_dir:
        return jsonify({"error": "demo images not configured"}), 404
    return send_from_directory(demo_dir, filename)


@lru_cache(maxsize=64)
def _demo_thumbnail_bytes(path):
    """Cached on the resolved absolute path (not just the filename) -- demo
    photos never change while the process is running, so there's no need to
    re-decode/resize the same file on every picker grid render."""
    with open(path, "rb") as f:
        return create_preview_bytes(f.read(), max_side=HISTORY_THUMBNAIL_MAX_SIDE)


@api_bp.get("/demo-images/<path:filename>/thumbnail")
def demo_image_thumbnail(filename):
    """Small JPEG counterpart of demo_image_file(), for the picker grid
    (#demo-images-modal) -- some demo photos are multi-MB, and a couple are
    HEIC (which create_preview_bytes/ensure_jpeg_bytes-style Pillow decoding
    handles the same as any other format, unlike a plain <img> tag pointed
    straight at demo_image_file's raw bytes). Selecting a tile still fetches
    the real file via demo_image_file above -- this is display-only.
    """
    demo_dir = current_app.config.get("DEMO_IMAGES_DIR")
    if not demo_dir:
        return jsonify({"error": "demo images not configured"}), 404
    # safe_join (same guard demo_image_file gets for free from
    # send_from_directory) since this route reads+transcodes the bytes
    # itself rather than handing the path straight to send_from_directory.
    safe_path = safe_join(demo_dir, filename)
    if not safe_path or not os.path.isfile(safe_path):
        return jsonify({"error": "image not found"}), 404
    try:
        thumb_bytes = _demo_thumbnail_bytes(safe_path)
    except Exception as exc:
        return jsonify({"error": f"failed to generate thumbnail: {exc}"}), 500
    return Response(thumb_bytes, mimetype="image/jpeg")


@api_bp.get("/decrypt-user-id")
def decrypt_user_id_route():
    """Resolves ?user_id=<AES-256-CBC ciphertext> (OpenLink_photoMaster.js)
    into the plain username, for account.js's fallback when a page is
    linked in with user_id instead of a plain ?user=NAME."""
    encrypted = request.args.get("user_id")
    if not encrypted:
        return jsonify({"error": "user_id is required"}), 400
    try:
        return jsonify({"user": decrypt_user_id(encrypted)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@api_bp.post("/account")
def account():
    body = request.get_json(silent=True) or request.form
    try:
        name = normalize_username(body.get("user"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    created = create_account(name)
    return jsonify({"user": name, "created": created})


@api_bp.get("/history/<user>")
def history(user):
    try:
        name = normalize_username(user)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        limit = min(max(int(request.args.get("limit", 50)), 0), HISTORY_MAX_LEN)
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        return jsonify({"error": "limit and offset must be integers"}), 400
    all_jobs = get_history(name, limit=HISTORY_MAX_LEN)
    jobs = all_jobs[offset:offset + limit]
    return jsonify({
        "user": name,
        "jobs": jobs,
        "has_more": offset + len(jobs) < len(all_jobs),
    })


@api_bp.delete("/history/<user>")
def delete_history(user):
    # image_key is an S3 key path (contains "/"), so it comes as a query
    # param instead of a URL path segment -- encoding a literal "/" into one
    # path segment is inconsistently handled across WSGI servers.
    try:
        name = normalize_username(user)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    image_key = request.args.get("image_key")
    if not image_key:
        return jsonify({"error": "image_key is required"}), 400
    delete_history_entry(name, image_key)
    return jsonify({"user": name, "deleted": image_key})


@api_bp.get("/history/<user>/case")
def get_history_case(user):
    """One case with its full version list. Separate from /history/<user>
    (which returns only the per-case summaries) because that endpoint is
    re-fetched on every page of scrolling -- versions are read only for the
    case actually being opened.

    Identified either by image_key (a case opened from the history list,
    which already has it) or by job_id (a case the client only knows one job
    of -- a photo uploaded and edited in this same session never had a case
    key on the client at all). job_id resolves through the job index, one
    lookup, and the returned case carries its image_key either way.
    """
    try:
        name = normalize_username(user)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    image_key = request.args.get("image_key")
    job_id = request.args.get("job_id")
    if not image_key and not job_id:
        return jsonify({"error": "image_key or job_id is required"}), 400
    case = get_history_entry(name, image_key) if image_key else get_history_entry_for_job(name, job_id)
    if not case:
        return jsonify({"error": "case not found"}), 404
    return jsonify({"user": name, "case": case})


@api_bp.delete("/history/<user>/version")
def delete_history_version(user):
    """Removes one version of a case, or one single result image of it when
    `job_id`/`run` narrow it further (a run_count>1 version holds several).
    Same scope as delete_history above -- only the history pointer goes, the
    underlying job and its S3 artifacts are left alone.

    Deleting a case's last version deletes the case too, so the caller gets
    `case_deleted` back rather than having to re-query to find that out.
    """
    try:
        name = normalize_username(user)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    image_key = request.args.get("image_key")
    version_id = request.args.get("version_id")
    if not image_key or not version_id:
        return jsonify({"error": "image_key and version_id are required"}), 400
    job_id = request.args.get("job_id") or None
    run = request.args.get("run")
    if run is not None:
        try:
            run = int(run)
        except (TypeError, ValueError):
            return jsonify({"error": "run must be an integer"}), 400
    if not delete_version(name, image_key, version_id, job_id=job_id, run=run):
        return jsonify({"error": "version not found"}), 404
    return jsonify({
        "user": name,
        "deleted": version_id,
        "case_deleted": get_history_entry(name, image_key) is None,
    })


@api_bp.delete("/history/<user>/all")
def delete_all_history(user):
    """Clears every entry in one call -- distinct from delete_history above
    (which needs a specific image_key) rather than looping that endpoint
    once per entry client-side, so "remove all" is one request/one Redis
    round trip regardless of history size."""
    try:
        name = normalize_username(user)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    clear_history(name)
    return jsonify({"user": name, "deleted": "all"})


def _panel_case_chat(body):
    """The panel UI's report names the case on screen (owner + case_id); when
    that case still has a conversation -- opened from the chat page's
    對話｜面板 switch, or ever opened there -- the report keeps it, so the
    admin can read that conversation as for a chat report. Best effort: a
    lookup that fails just leaves the report as a plain panel one."""
    owner, case_id = body.get("owner"), body.get("case_id")
    if not owner or not case_id:
        return None
    try:
        owner = normalize_username(owner)
        session_id = chat_session.find_session_for_case(owner, str(case_id))
        session = chat_session.load_session(owner, session_id) if session_id else None
    except Exception:
        current_app.logger.warning("report: no conversation lookup for case %s", case_id, exc_info=True)
        return None
    if not session:
        return None
    photo = next((p for p in session["photos"] if p.get("case_id") == case_id), None)
    chat = {"owner": owner, "session_id": session_id, "photo_id": (photo or {}).get("photo_id"), "batch_id": session.get("batch_id")}
    return {key: str(value)[:128] for key, value in chat.items() if value}


@api_bp.post("/report")
def report_job():
    # A screenshot upload arrives as multipart/form-data (request.files),
    # not JSON -- request.get_json(silent=True) correctly returns None for
    # that content type, so the existing request.form fallback already
    # covers the text fields either way.
    body = request.get_json(silent=True) or request.form
    try:
        name = normalize_username(body.get("user"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    # Optional -- #report-btn (app.js) is always clickable, even with no
    # result on screen, for a general "something's wrong" report with no
    # job to point at. record_report keys those by a random id instead of
    # job_id since there's nothing to dedup them against.
    job_id = body.get("job_id") or None

    screenshot = request.files.get("screenshot")
    screenshot_key = None
    if screenshot and screenshot.filename:
        # Deterministic-enough (job or "general" + reporter + upload time),
        # not looked up anywhere else -- report_screenshot below resolves it
        # straight from this same report entry rather than the client ever
        # handling or passing around the actual key.
        screenshot_key = f"demo/photoAgent/reports/{job_id or 'general'}/{name}_{int(time.time())}_screenshot.jpg"
        upload_report_screenshot(screenshot.read(), screenshot_key, job_id or "general")

    # The chat UI (/chat/) also says which conversation was on screen, so the
    # admin can open it (REPORT_CHAT_FIELDS, backend/services/report.py).
    source = "chat" if body.get("source") == "chat" else None
    chat = {key: str(body.get(key))[:128] for key in REPORT_CHAT_FIELDS if body.get(key)} if source else _panel_case_chat(body)
    page = _reported_page(body) if source else None
    if page:
        chat = {**(chat or {}), "page": page["page"]}
    report_id = record_report(
        name, job_id, body.get("path") or "local",
        note=body.get("note"), user_prompt=body.get("user_prompt"), screenshot_key=screenshot_key,
        source=source, chat=chat
    )
    _save_reported_chat(report_id, chat)
    if page:
        save_report_page(report_id, page)
    return jsonify({"reported": True, "report_id": report_id})


REPORT_PAGE_MAX_BYTES = 2 * 1024 * 1024
REPORT_PAGE_MAX_IMAGE = 64 * 1024


def _reported_page(body):
    """The chat UI's 首頁/upload page as it stood at the report
    (frontend-chat/src/report-page.ts), for a report with no conversation to
    copy. Kept to plain strings and small data: image URLs -- anything the
    admin's read-only view puts in an <img> -- must be small inline JPEG/PNG
    thumbnails, so a reporter can't point the admin's browser elsewhere."""
    kind = body.get("page")
    if kind not in REPORT_PAGES:
        return None
    page = {"page": kind}
    # A file part, not a field: Werkzeug caps plain form fields well below
    # what a dozen thumbnails add up to.
    upload = request.files.get("page_state")
    if kind != "upload" or not upload:
        return page
    data = upload.read(REPORT_PAGE_MAX_BYTES + 1)
    if len(data) > REPORT_PAGE_MAX_BYTES:
        return page
    try:
        state = json.loads(data)
    except ValueError:
        return page
    if not isinstance(state, dict):
        return page

    def image(src):
        ok = isinstance(src, str) and len(src) <= REPORT_PAGE_MAX_IMAGE and src.startswith(("data:image/jpeg;base64,", "data:image/png;base64,"))
        return src if ok else None

    def texts(items, limit, size):
        return [str(x)[:size] for x in (items if isinstance(items, list) else [])[:limit] if isinstance(x, (str, int, float))]

    page["photos"] = [src for src in map(image, (state.get("photos") if isinstance(state.get("photos"), list) else [])[:12]) if src]
    total = state.get("total")
    page["total"] = total if isinstance(total, int) and total >= 0 else len(page["photos"])
    page["settings"] = texts(state.get("settings"), 20, 200)
    messages = state.get("messages") if isinstance(state.get("messages"), list) else []
    page["messages"] = [
        {
            "user": bool(m.get("user")),
            "lines": texts(m.get("lines"), 40, 2000),
            "images": [src for src in map(image, (m.get("images") if isinstance(m.get("images"), list) else [])[:4]) if src],
            "chips": texts(m.get("chips"), 12, 80),
        }
        for m in messages[:80] if isinstance(m, dict)
    ]
    return page


def _save_reported_chat(report_id, chat):
    """Keeps the conversation as it is right now with the report, so the
    admin's read-only view (report_session, routes/chat.py) shows the moment
    of the report rather than whatever the user did after. Best effort: the
    report is already recorded, and without a copy the view reads live."""
    owner = (chat or {}).get("owner")
    if not owner or not (chat.get("session_id") or chat.get("batch_id")):
        return
    try:
        save_report_chat(report_id, chat_session.report_chat(owner, chat.get("session_id"), chat.get("batch_id")))
    except Exception:
        current_app.logger.warning("report: conversation not copied for %s", report_id, exc_info=True)


REPORTS_MAX_PAGE = 200


@api_bp.get("/reports/<user>")
def reports(user):
    # Doubles as the frontend's "is this account an admin?" check (see
    # checkAdminAccess, app.js) -- a 403 here just means "hide the admin
    # button", not a real error to surface.
    try:
        name = normalize_username(user)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not is_admin(name):
        return jsonify({"error": "not authorized"}), 403
    try:
        limit = min(max(int(request.args.get("limit", 50)), 0), REPORTS_MAX_PAGE)
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        return jsonify({"error": "limit and offset must be integers"}), 400
    reports, has_more = get_reports(limit=limit, offset=offset)
    for entry in reports:
        thumbnail = _page_report_thumbnail(entry) or _chat_report_thumbnail(entry)
        if thumbnail:
            entry["thumbnail"] = thumbnail
    return jsonify({"reports": reports, "has_more": has_more})


@api_bp.get("/reports/<user>/<report_id>")
def report_entry(user, report_id):
    """Admin-only, same is_admin gate as reports() above -- one report in
    the list's shape, for the old UI's #report=<id> entry point (the chat
    page's read-only report view links there as its 面板 switch)."""
    try:
        name = normalize_username(user)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not is_admin(name):
        return jsonify({"error": "not authorized"}), 403
    entry = get_report(report_id, job_id=request.args.get("job") or None)
    if entry is None:
        return jsonify({"error": "report not found"}), 404
    if not entry.get("job_id"):
        analyze_job_id = _chat_report_analyze_job(entry)
        if analyze_job_id:
            entry["analyze_job_id"] = analyze_job_id
    return jsonify({"report": entry})


def _chat_report_analyze_job(entry):
    """A chat report filed on a photo with no edit yet has no job_id, but the
    photo still has its analysis: the analyze job, so 面板模式 can open that
    photo the way the history drawer opens a 待美化 case. Best effort, like
    _panel_case_chat."""
    owner, session_id, photo_id = entry.get("owner"), entry.get("session_id"), entry.get("photo_id")
    if not owner or not session_id or not photo_id:
        return None
    try:
        session = chat_session.load_session(normalize_username(owner), session_id)
    except Exception:
        current_app.logger.warning("report: no conversation lookup for %s", entry.get("report_id"), exc_info=True)
        return None
    photo = next((p for p in (session or {}).get("photos", []) if p.get("photo_id") == photo_id), None)
    return (photo or {}).get("analyze_job_id")


def _page_report_thumbnail(entry):
    """A report filed from the upload page: its first photo, from the page
    copy (_reported_page), as {src: <data URL>}. 首頁 ones have none."""
    if entry.get("page") != "upload":
        return None
    photos = (get_report_page(entry["report_id"]) or {}).get("photos") or []
    return {"src": photos[0]} if photos else None


def _chat_report_thumbnail(entry):
    """The drawer row's thumbnail for a chat report with no job of its own
    (a 一般回報 filed from a photo, a conversation or a batch overview), in
    session_summary's {job_id, run} shape -- run None means the original.
    From the report-time copy of the conversation (save_report_chat) when
    there is one, so it shows what was on screen then. A job-tied report is
    thumbnailed from its job_id by the drawer itself (renderReportsList,
    app.js). Best effort, like _chat_report_analyze_job."""
    owner, session_id, batch_id = entry.get("owner"), entry.get("session_id"), entry.get("batch_id")
    if entry.get("job_id") or not owner or not (session_id or batch_id):
        return None
    try:
        sessions = (get_report_chat(entry["report_id"]) or {}).get("sessions")
        if sessions is None:
            user = normalize_username(owner)
            # A live read only needs the one session the thumbnail comes from.
            session = chat_session.load_session(user, session_id) if session_id else next(iter(chat_session.batch_sessions(user, batch_id)), None)
            sessions = [session] if session else []
    except Exception:
        current_app.logger.warning("report: no conversation lookup for %s", entry.get("report_id"), exc_info=True)
        return None
    # The session the report names, else (filed from a batch overview) the
    # batch's first conversation.
    session = next((s for s in sessions if s.get("session_id") == session_id), None) if session_id else next(iter(sessions), None)
    if not session:
        return None
    photo = next((p for p in session.get("photos", []) if p.get("photo_id") == entry.get("photo_id")), None)
    if photo:
        version = photo.get("active_version") or {}
        if version.get("job_id"):
            return {"job_id": version["job_id"], "run": version.get("run") or 1}
        return {"job_id": photo.get("analyze_job_id"), "run": None} if photo.get("analyze_job_id") else None
    # No photo: the conversation's own tile thumbnail on the home page.
    summary = chat_session.session_tile(session)
    if summary.get("thumbnail_result"):
        return summary["thumbnail_result"]
    return {"job_id": summary["thumbnail_job_id"], "run": None} if summary.get("thumbnail_job_id") else None


@api_bp.patch("/reports/<user>/<report_id>")
def update_report_status_entry(user, report_id):
    """Admin-only, same is_admin gate as reports() above -- sets the
    triage status (unread/read/solved, backend/services/report.py's
    REPORT_STATUSES) an admin clicks through in the reports drawer."""
    try:
        name = normalize_username(user)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not is_admin(name):
        return jsonify({"error": "not authorized"}), 403
    status = (request.get_json(silent=True) or {}).get("status")
    entry = update_report_status(report_id, status)
    if entry is None:
        return jsonify({"error": "invalid report_id or status"}), 400
    return jsonify({"report": entry})


@api_bp.delete("/reports/<user>/<report_id>")
def delete_report_entry(user, report_id):
    """Admin-only, same is_admin gate as reports() above -- removes just the
    flagged-report entry (backend/services/report.py's delete_report), not
    the job/result it points to."""
    try:
        name = normalize_username(user)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not is_admin(name):
        return jsonify({"error": "not authorized"}), 403
    delete_report(report_id)
    return jsonify({"deleted": report_id})


@api_bp.get("/reports/screenshot/<report_id>")
def report_screenshot(report_id):
    """Same trust level as /source/<job_id>/image and /result/<job_id>/image
    above -- no admin check of its own, just this app's usual "the caller
    already knows the id so there's nothing to look up that isn't already
    effectively public" model. report_id (not job_id/user) is the lookup key
    since a general report (record_report, backend/services/report.py) has
    no job_id to key on at all. The admin reports drawer is the only place
    this URL is ever constructed from (renderReportsList, app.js).
    """
    entry = get_report_by_id(report_id)
    if not entry or not entry.get("screenshot_key"):
        return jsonify({"error": "no screenshot for this report"}), 404
    image_bytes, download_error = download_bytes(entry["screenshot_key"], entry.get("job_id") or "general")
    if download_error or image_bytes is None:
        return jsonify({"error": f"failed to download screenshot: {download_error}"}), 502
    return Response(image_bytes, mimetype="image/jpeg")


@api_bp.post("/detect-faces")
def detect_faces():
    """The local face detector's boxes for an image, with nothing else.

    The chat's upload page asks for this the moment a photo is picked, so it
    can offer the same 指定對象 face picker the analysis page has before any
    session, job or analysis exists. Runs in the web process rather than
    through the queue: it is one 640px ONNX pass on a 1.2MB model, far below
    the cost of standing a job up, and the answer is needed while the user is
    still typing.

    Best-effort by design (image_tools.detect_face_boxes): a photo with no
    detectable face answers 200 with an empty list, same as one the detector
    failed on -- the page simply shows no picker. Only an unreadable upload
    is an error.
    """
    image = request.files.get("image")
    if image is None or not image.filename:
        return jsonify({"error": "image file is required"}), 400

    try:
        faces = detect_face_boxes(image.read())
    except PILImage.DecompressionBombError:
        return jsonify({"error": "Image resolution is too high, please use a smaller image."}), 422
    except Exception:
        return jsonify({"error": "Could not read this image."}), 422
    return jsonify({"faces": faces})


@api_bp.post("/submit")
def submit():
    image = request.files.get("image")
    if image is None or not image.filename:
        return jsonify({"error": "image file is required"}), 400

    params, error = _parse_and_validate_params(request.form)
    if error:
        return jsonify({"error": error}), 400

    engine_params = get_engine_params_template()
    engine_params["acts"][0]["params"] = params
    job_id = enqueue(engine_params, src_content=[image.read()])
    # enqueue() resolves srcKeys' "{msg_id}" placeholder in place -- this is
    # a genuinely new upload, so srcKeys[0] is this job's own fresh S3 path.
    # action_id=job_id: run_count (if any) is handled entirely server-side
    # within this one job (job_processor.py), so there's no sibling call to
    # group this with.
    _record_history_safely(request.form.get("user"), job_id, "local", engine_params["srcKeys"][0], job_id, params=params)
    return jsonify({"job_id": job_id}), 202


def _readable_job_info(job_id, keyToGet):
    """get_job_info for the routes that only READ a job back out (status,
    result, images, analysis). The job's Redis info expires after a few
    weeks while its S3 artifacts stay; a job someone reported keeps a copy
    of that info (backend/services/report.py), so an admin can still open
    the report afterwards. Routes that start new work from a job (continue,
    reframe, rerun) keep the live lookup only."""
    keys = list(keyToGet)
    info = get_job_info(job_id, keyToGet=keys)
    if info is None:
        info = get_reported_job_info(job_id, keyToGet)
    return info


@api_bp.get("/status/<job_id>")
def status(job_id):
    # "stage"/"run_count"/"runs_done" are only ever present for a combined
    # analyze+edit job (see job_processor.py's set_job_info(...) calls) --
    # absent for the two-stage analyze-only/finish-only jobs, and absent
    # until the worker has actually started the combined job. runs_done lets
    # the frontend notice a new RUN has landed (and go fetch it via /result)
    # without having to re-download every RUN's data on every poll tick.
    info = _readable_job_info(job_id, ["status", "error", "error_message", "stage", "run_count", "runs_done", "ts"])
    if info is None:
        return jsonify({"error": "Job not found"}), 404
    info.update(lost_job_error(job_id, info) or {})
    info.pop("ts", None)
    return jsonify(info)


def _runs_from_meta(runs_meta, job_id):
    """Downloads+presigns whatever RUNs in runs_meta have valid keys so far.
    A RUN that fails to download is simply skipped (not yet visible/flushed
    to the artifact store) rather than failing the whole response -- the
    next poll will pick it up once it's actually there."""
    runs = []
    for i, run_meta in enumerate(runs_meta):
        keys = (run_meta or {}).get("cusKeys") or []
        if len(keys) < 2:
            continue
        image_key, json_key = keys[0], keys[1]
        json_bytes, download_error = download_bytes(json_key, job_id)
        if download_error or json_bytes is None:
            continue
        runs.append({
            "run": i + 1,
            "image_url": create_presigned_url(image_key, "get_object"),
            "result": json.loads(json_bytes),
        })
    return runs


@api_bp.get("/result/<job_id>")
def result(job_id):
    info = _readable_job_info(job_id, ["status", "error", "error_message", "0_res0", "run_count"])
    if info is None:
        return jsonify({"error": "Job not found"}), 404

    res0 = info.get("0_res0") or {}
    runs_meta = res0.get("runs")

    if runs_meta is not None:
        # Multi-run combined job: return whichever RUNs have completed so
        # far, even while the job as a whole is still "processing" -- the
        # RUNs are written to Redis one at a time as they finish server-side
        # (job_processor.py), independent of whether anything is polling, so
        # this always reflects real current progress rather than an
        # all-or-nothing final result.
        out = {
            "status": info.get("status") or "processing",
            "runs": _runs_from_meta(runs_meta, job_id),
            "run_count": info.get("run_count"),
        }
        if info.get("status") == "error":
            out["error"] = info.get("error")
            out["error_message"] = info.get("error_message")
        return jsonify(out)

    # Single-run job (two-stage finish-only, or an older combined job from
    # before run_count existed) -- unchanged behavior.
    if info.get("status") != "success":
        return jsonify({"status": info.get("status"), "error": info.get("error"), "error_message": info.get("error_message")})

    result_keys = res0.get("cusKeys") or []
    if len(result_keys) < 2:
        return jsonify({"status": "error", "error": "job succeeded but result keys are missing"})

    image_key, json_key = result_keys[0], result_keys[1]
    json_bytes, download_error = download_bytes(json_key, job_id)
    if download_error or json_bytes is None:
        return jsonify({"status": "error", "error": f"failed to download result metadata: {download_error}"})

    result_text = json.loads(json_bytes)
    image_url = create_presigned_url(image_key, "get_object")

    return jsonify({"status": "success", "image_url": image_url, "result": result_text})


def _resolve_result_keys(info):
    """Shared by result_image/result_thumbnail: resolves the cusKeys list
    for a successful job's 0_res0 metadata, handling both a single-run
    job ("cusKeys" directly on 0_res0) and a multi-run combined job
    ("runs": [...], selected via ?run=N, 1-based). Returns
    (result_keys, None) on success, or (None, (response, status)) to
    return as-is on failure."""
    res0 = info.get("0_res0") or {}
    runs_meta = res0.get("runs")
    if runs_meta is not None:
        run_param = request.args.get("run", "1")
        try:
            run_index = int(run_param) - 1
        except ValueError:
            return None, (jsonify({"error": "run must be an integer"}), 400)
        if run_index < 0 or run_index >= len(runs_meta):
            return None, (jsonify({"error": f"run {run_param} not found"}), 404)
        result_keys = runs_meta[run_index].get("cusKeys") or []
    else:
        result_keys = res0.get("cusKeys") or []

    if not result_keys:
        return None, (jsonify({"error": "job succeeded but result keys are missing"}), 500)
    return result_keys, None


@api_bp.get("/result/<job_id>/image")
def result_image(job_id):
    """Same bytes as /result's image_url, but served from our own origin.

    The frontend needs to fetch() the result image client-side (to bundle it
    into a zip download) -- a presigned S3 URL doesn't have CORS headers for
    that, so this re-downloads it server-side (where CORS doesn't apply) and
    relays the bytes. job_id is our own trusted job state, not a
    client-supplied URL, so this can't be used as an open fetch proxy.

    For a multi-run combined job, ?run=N (1-based) selects which RUN's image
    to serve -- defaults to 1 so a caller that doesn't know about multi-run
    jobs still gets a sensible result.
    """
    info = _readable_job_info(job_id, ["status", "error", "0_res0"])
    if info is None:
        return jsonify({"error": "Job not found"}), 404
    if info.get("status") != "success":
        return jsonify({"error": info.get("error") or "job not successful"}), 400

    result_keys, error = _resolve_result_keys(info)
    if error:
        return error

    image_bytes, download_error = download_bytes(result_keys[0], job_id)
    if download_error or image_bytes is None:
        return jsonify({"error": f"failed to download result image: {download_error}"}), 502

    # Sniffed, not hardcoded: results are stored as JPEG now
    # (job_processor.py's _write_result_artifacts), but jobs from before that
    # kept genuine PNG bytes, and their keys are still in Redis.
    return Response(image_bytes, mimetype=sniff_image_mime(image_bytes))


@api_bp.get("/result/<job_id>/pre_edit_image")
def result_pre_edit_image(job_id):
    """Step 5's own output, before Step 5b's gaze-repair pass overwrites it
    (see pipeline.py's finish_stage) -- a debug-UI-only "stage 1" view,
    fetched lazily only when #show-prompt-btn's modal's corresponding tab is
    opened (see loadPromptsModalImageTab, app.js), unlike result_image()
    which every result display needs eagerly.

    Not every job has one: single_pass_edit skips Step 5b entirely, and
    ai_photo_editing=False never calls it either, so result_keys[2] is
    simply absent in both cases -- a 404 here, not an error, and the caller
    is expected to treat it that way. Local-path only, same as
    source_image() below: this key only exists in our own worker's S3
    layout (job_processor.py), not in AIPoC's separate deployment of this
    pipeline.
    """
    info = _readable_job_info(job_id, ["status", "error", "0_res0"])
    if info is None:
        return jsonify({"error": "Job not found"}), 404
    if info.get("status") != "success":
        return jsonify({"error": info.get("error") or "job not successful"}), 400

    result_keys, error = _resolve_result_keys(info)
    if error:
        return error
    if len(result_keys) < 3:
        return jsonify({"error": "no stage-1 image for this job"}), 404

    image_bytes, download_error = download_bytes(result_keys[2], job_id)
    if download_error or image_bytes is None:
        return jsonify({"error": f"failed to download stage-1 image: {download_error}"}), 502

    return Response(image_bytes, mimetype=sniff_image_mime(image_bytes))


def _ensure_jpeg_bytes_or_error(image_bytes):
    """ensure_jpeg_bytes(), but turns PIL's decompression-bomb guard into a
    plain-language 422 instead of an unhandled 500 -- these routes serve a
    source image that's already stored (unlike the /submit upload path,
    which never gets this far for an oversized photo), so a job created
    before that guard existed, or one whose source came from an
    already-huge upload, can still trip it here. Returns (jpeg_bytes, None)
    on success or (None, response) on failure, mirroring this file's other
    (result, error) route helpers.
    """
    try:
        return ensure_jpeg_bytes(image_bytes), None
    except PILImage.DecompressionBombError:
        return None, (jsonify({"error": "Image resolution is too high, please use a smaller image."}), 422)


def _immutable_image_response(image_bytes, mimetype):
    """Same Response(bytes, mimetype) every image route already returns, plus
    a long-lived Cache-Control -- used only where the bytes for a given URL
    can never change once served (a finished job's result/source thumbnail),
    so the browser can skip re-fetching it every time the history/batch
    record list re-renders (opening the drawer, switching tabs, etc.)
    instead of re-hitting this server -> S3 round trip for the same bytes
    every single time.
    """
    resp = Response(image_bytes, mimetype=mimetype)
    resp.cache_control.public = True
    resp.cache_control.max_age = 31536000
    resp.cache_control.immutable = True
    return resp


@api_bp.get("/result/<job_id>/thumbnail")
def result_thumbnail(job_id):
    """Small JPEG counterpart of result_image(), for fast-loading UI lists
    (the history grid) that only ever render this at a few dozen CSS
    pixels.

    Generated lazily here (on first request) and cached back to S3 at a
    derived key, rather than pre-generated by job_processor.py at finish
    time: which job (of a multi-job "Run N times" action) or RUN (of a
    multi-run combined job) actually ends up displayed by the history list
    is only decided client-side, after every job/RUN has already finished
    -- job_processor.py has no way to know in advance which one that'll be,
    so pre-generating one per job/RUN there would mostly go unused. This
    way exactly one thumbnail is ever made: whichever result the history
    list actually asked to see.
    """
    return _downscaled_result(job_id, derive_thumbnail_key, HISTORY_THUMBNAIL_MAX_SIDE)


@api_bp.get("/result/<job_id>/preview")
def result_preview(job_id):
    """Mid-size JPEG of the result, for a finished version shown as a card in
    the chat: the 320px thumbnail is blurry there on 2x screens, and the
    full result is many times the bytes. Made and cached like the thumbnail."""
    return _downscaled_result(job_id, derive_preview_key, RESULT_PREVIEW_MAX_SIDE)


def _downscaled_result(job_id, derive_key, max_side):
    """A finished job's result at max_side, served from its cached copy at
    derive_key(result key) or made on this first request and cached there."""
    info = _readable_job_info(job_id, ["status", "error", "0_res0"])
    if info is None:
        return jsonify({"error": "Job not found"}), 404
    if info.get("status") != "success":
        return jsonify({"error": info.get("error") or "job not successful"}), 400

    result_keys, error = _resolve_result_keys(info)
    if error:
        return error

    image_key = result_keys[0]
    small_key = derive_key(image_key)

    small_bytes, small_error = download_bytes(small_key, job_id)
    if not small_error and small_bytes is not None:
        return _immutable_image_response(small_bytes, "image/jpeg")

    image_bytes, download_error = download_bytes(image_key, job_id)
    if download_error or image_bytes is None:
        return jsonify({"error": f"failed to download result image: {download_error}"}), 502

    try:
        small_bytes = create_preview_bytes(image_bytes, max_side=max_side)
    except PILImage.UnidentifiedImageError:
        # An analyze job's result is its analysis.json, not an image.
        return jsonify({"error": "job result is not an image"}), 404
    cache_thumbnail(small_bytes, small_key, job_id)
    return _immutable_image_response(small_bytes, "image/jpeg")


@api_bp.get("/source/<job_id>/image")
def source_image(job_id):
    """The original uploaded photo for job_id -- lets the history page (and
    any other local-path job) show a real before/after compare instead of
    just the generated result. srcKeys is retained on the job's own Redis
    hash for as long as that hash lives (no TTL, same as every other job
    field), independent of whether the job ever finished successfully, so
    this works even for a job that's still processing or that errored.

    AIPoC-path jobs have no local Redis job at all (dispatched externally),
    so there's nothing this endpoint can serve for a task_id -- callers
    should only use this for path="local" jobs.
    """
    info = _readable_job_info(job_id, ["srcKeys"])
    if info is None:
        return jsonify({"error": "Job not found"}), 404
    src_keys = info.get("srcKeys") or []
    if not src_keys:
        return jsonify({"error": "no source image for this job"}), 404

    # The JPEG below, as converted the first time this was asked for. A
    # job's upload never changes, so neither does it: cached here and in
    # the browser, the results page does not wait on the raw original
    # (often 10MB+) and its re-encoding every time a photo is opened.
    view_key = derive_view_key(src_keys[0])
    view_bytes, view_error = download_bytes(view_key, job_id)
    if not view_error and view_bytes is not None:
        return _immutable_image_response(view_bytes, "image/jpeg")

    image_bytes, download_error = download_bytes(src_keys[0], job_id)
    if download_error or image_bytes is None:
        return jsonify({"error": f"failed to download source image: {download_error}"}), 502

    # srcKeys is always a "src.jpg"-named key (see template_params.py) --
    # store bytes are the raw upload regardless of the browser's own original
    # content-type, so a HEIC/HEIF photo (most browsers can't render that in
    # an <img> tag under any label, see image_tools.py's own doc on this) would
    # otherwise come back looking like a broken image. ensure_jpeg_bytes
    # normalizes it to a real JPEG first. (/result/<job_id>/image has the
    # mirror-image problem and solves it by sniffing instead: its bytes are
    # always a real image, just PNG or JPEG depending on the job's age.)
    jpeg_bytes, error_response = _ensure_jpeg_bytes_or_error(image_bytes)
    if error_response:
        return error_response
    cache_thumbnail(jpeg_bytes, view_key, job_id)
    return _immutable_image_response(jpeg_bytes, "image/jpeg")


@api_bp.get("/source/<job_id>/thumbnail")
def source_thumbnail(job_id):
    """Small JPEG counterpart of source_image(), for fast-loading UI lists
    (the history grid) that only ever render this at a few dozen CSS
    pixels. The thumbnail key is derived deterministically from srcKeys[0]
    (put_job.py uploads it there at submission time) -- falls back to the
    full-res source if that thumbnail is missing (a job submitted before
    thumbnails existed, or a thumbnail upload that failed silently).
    """
    info = _readable_job_info(job_id, ["srcKeys"])
    if info is None:
        return jsonify({"error": "Job not found"}), 404
    src_keys = info.get("srcKeys") or []
    if not src_keys:
        return jsonify({"error": "no source image for this job"}), 404

    thumb_bytes, thumb_error = download_bytes(derive_thumbnail_key(src_keys[0]), job_id)
    if not thumb_error and thumb_bytes is not None:
        return _immutable_image_response(thumb_bytes, "image/jpeg")

    image_bytes, download_error = download_bytes(src_keys[0], job_id)
    if download_error or image_bytes is None:
        return jsonify({"error": f"failed to download source image: {download_error}"}), 502
    # Unlike thumb_bytes above (already a real JPEG from create_preview_bytes
    # at upload time), this is the raw full-res upload -- same HEIC/HEIF risk
    # as source_image() above, so normalize it the same way.
    jpeg_bytes, error_response = _ensure_jpeg_bytes_or_error(image_bytes)
    if error_response:
        return error_response
    return _immutable_image_response(jpeg_bytes, "image/jpeg")


# ── Two-stage local flow: analyze now, review, /continue later ─────────────
#
# /submit + /result above run the full pipeline in one job. These routes let
# the frontend pause after analysis (steps 1-3) for user review before
# committing to the slower/costlier edit step (steps 4-6). Stage 2 is a
# *separate* queued job rather than the same job resumed mid-flight, since
# the shared queue worker (server.py) has no pause/resume primitive -- it
# just runs one job to completion per dequeue.


@api_bp.post("/analyze")
def analyze():
    image = request.files.get("image")
    if image is None or not image.filename:
        return jsonify({"error": "image file is required"}), 400

    params, error = _parse_and_validate_params(request.form)
    if error:
        return jsonify({"error": error}), 400
    params["stage"] = "analyze"

    engine_params = get_engine_params_template()
    engine_params["acts"][0]["params"] = params
    job_id = enqueue(engine_params, src_content=[image.read()])
    # action_id=job_id, same as /submit -- an analyze-only job has no prior
    # action to group under. Without this, the two-stage manual flow (Run
    # (Manual)) had no history row at all until /continue's later call, so
    # navigating to a different history record before then made this photo
    # unrecoverable -- there was nothing in the list to click back into.
    _record_history_safely(request.form.get("user"), job_id, "local", engine_params["srcKeys"][0], job_id, params=params)
    return jsonify({"job_id": job_id}), 202


@api_bp.post("/analyze-batch")
def analyze_batch():
    """Batch stage 1 (local): enqueue one analyze-only job per image.

    Looped equivalent of /analyze -- each input file yields one entry in list
    order with a job_id (poll via /status, fetch via /analyze/result) or a
    per-file error.
    """
    images = [img for img in request.files.getlist("image") if img and img.filename]
    if not images:
        return jsonify({"error": "at least one image file is required"}), 400

    params, error = _parse_and_validate_params(request.form)
    if error:
        return jsonify({"error": error}), 400
    params["stage"] = "analyze"

    user = request.form.get("user")
    entries = []
    for image in images:
        try:
            engine_params = get_engine_params_template()
            engine_params["acts"][0]["params"] = params
            job_id = enqueue(engine_params, src_content=[image.read()])
            # Same reasoning as /analyze just above: without this, a batch
            # manual-review upload had no history row per image until each
            # one got past review and continued -- an image left at "analyzed"
            # was invisible in history, not just unrecoverable via one shared
            # row. action_id=job_id since an analyze-only job has no prior
            # action to group under, same as /analyze.
            _record_history_safely(user, job_id, "local", engine_params["srcKeys"][0], job_id, params=params)
            entries.append({"filename": image.filename, "job_id": job_id})
        except Exception as exc:
            entries.append({"filename": image.filename, "error": str(exc)})

    return jsonify({"jobs": entries}), 202


@api_bp.post("/analyze/<job_id>")
def analyze_existing(job_id):
    """/analyze on the photo an earlier job already has -- the analysis a
    「只照需求改」 case skipped, asked for from its result page (自訂模式's
    「幫我分析」). Same analyze-only job as /analyze, fetched the same way
    (/status, /analyze/result).

    Recorded as a new version of the case, the analyze kind an upload's
    analysis is: the case moves on to 待美化 with this analysis, and the
    next 美化 is the version after it. Also attached to the case
    (attach_case_analysis), which is how the stub analysis a skipped case's
    renders carry gets replaced on reopening -- in either mode -- and how the
    photo's chat session picks it up (chat._sync_from_history)."""
    body = request.get_json(silent=True) or {}
    params, error = _parse_and_validate_params(body)
    if error:
        return jsonify({"error": error}), 400
    params["stage"] = "analyze"

    prior = get_job_info(job_id, keyToGet=["srcKeys"])
    if not prior or not prior.get("srcKeys"):
        return jsonify({"error": "Prior job not found"}), 404

    engine_params = get_engine_params_template()
    engine_params["srcKeys"] = prior["srcKeys"]
    engine_params["acts"][0]["params"] = params
    new_job_id = enqueue(engine_params, src_content=None)
    # srcKeys[0] is the case key, the same one _record_history_safely files
    # every job of this photo under.
    user = body.get("user")
    _record_history_safely(user, new_job_id, "local", engine_params["srcKeys"][0], new_job_id, params=params)
    if user:
        try:
            attach_case_analysis(normalize_username(user), engine_params["srcKeys"][0], new_job_id)
        except Exception:
            current_app.logger.warning("attach_case_analysis failed for %s", new_job_id, exc_info=True)
    return jsonify({"job_id": new_job_id}), 202


@api_bp.get("/analyze/result/<job_id>")
def analyze_result(job_id):
    info = _readable_job_info(job_id, ["status", "error", "0_res0"])
    if info is None:
        return jsonify({"error": "Job not found"}), 404

    if info.get("status") != "success":
        return jsonify({"status": info.get("status"), "error": info.get("error")})

    result_keys = (info.get("0_res0") or {}).get("cusKeys") or []
    if not result_keys:
        return jsonify({"status": "error", "error": "job succeeded but analysis key is missing"})

    analysis_bytes, download_error = download_bytes(result_keys[0], job_id)
    if download_error or analysis_bytes is None:
        return jsonify({"status": "error", "error": f"failed to download analysis: {download_error}"})

    analysis = json.loads(analysis_bytes)
    # compatible tells the frontend (viewAnalyzeHistoryRecord) whether this
    # history item's analysis still meets the floor continue_job below will
    # itself enforce -- surfaced here so it can hint "reprocess this photo"
    # before the user invests time in the review screen, not only once they
    # hit Run and continue_job rejects it. get_min_compatible_version() reads
    # the worker's own floor from Redis (backend/services/compatibility.py)
    # so this stays right even when only the worker, not this Flask process,
    # has been redeployed since the floor last moved.
    return jsonify({"status": "success", "result": {
        "analysis": analysis,
        "compatible": is_compatible(analysis, min_version=get_min_compatible_version()),
        "compatibilityVersion": get_compatibility_version(analysis),
    }})


@api_bp.post("/continue/<job_id>")
def continue_job(job_id):
    body = request.get_json(silent=True) or {}

    analysis = body.get("analysis")
    if not isinstance(analysis, dict) or ("subject_category" not in analysis and not _is_skipped_analysis(analysis)):
        return jsonify({"error": "analysis (from /analyze/result) is required"}), 400
    if not is_compatible(analysis, min_version=get_min_compatible_version()):
        # Authoritative gate: the frontend already checks compatible from
        # /analyze/result before offering Run, but this is what actually
        # prevents an old analysis from reaching finish_stage.
        return jsonify({"error": "analysis_compatibility_version_outdated"}), 409

    analysis, error = _apply_review_selections(analysis, body.get("selections"))
    if error:
        return jsonify({"error": error}), 400

    params, error = _parse_and_validate_params(body)
    if error:
        return jsonify({"error": error}), 400
    if _skipped_without_prompt(analysis, params):
        return jsonify({"error": SKIPPED_NEEDS_PROMPT}), 400
    params["stage"] = "finish"
    params["analysis"] = analysis

    prior = get_job_info(job_id, keyToGet=["srcKeys"])
    if not prior or not prior.get("srcKeys"):
        return jsonify({"error": "Prior job not found"}), 404

    engine_params = get_engine_params_template()
    engine_params["srcKeys"] = prior["srcKeys"]
    engine_params["acts"][0]["params"] = params
    new_job_id = enqueue(engine_params, src_content=None)
    # srcKeys was carried forward from prior (unchanged, no "{msg_id}" left
    # for enqueue() to substitute) -- same image identity as whatever job(s)
    # this chain already recorded, so this overwrites that same history entry
    # instead of adding a new one.
    #
    # action_id groups every job from ONE "Run" click together -- this route's
    # own job_id (URL param, the prior job being continued from) can't be used
    # for that: it stays the SAME across every *separate* later revise too
    # (the frontend's singleFlowState.analyzeJobId is set once and never
    # updated for a plain single-RUN continue), so grouping by it would merge
    # unrelated revises done minutes/hours apart into one entry, not just the
    # N parallel RUNs of a single runCount>1 click. The frontend instead
    # generates and passes its own action_id explicitly for that case
    # (runSingleItem/editBatchItem); falling back to this call's own
    # new_job_id when absent makes a plain single-RUN continue its own
    # standalone group, same as before action_id existed.
    action_id = body.get("action_id") or new_job_id
    _record_history_safely(body.get("user"), new_job_id, "local", engine_params["srcKeys"][0], action_id, params=params)
    return jsonify({"job_id": new_job_id}), 202


@api_bp.post("/rerun")
def rerun():
    """/continue for an analysis that has no prior job behind it.

    Same stage="finish" job as /continue, but the photo is uploaded with the
    request instead of being reused from an earlier job's srcKeys. That is the
    only thing /continue cannot do, and the only thing standing between an
    exported result folder (source photo + run_N/result.json, the debug-UI zip
    layout) and re-running its edit: everything downstream -- steps 3-6,
    including a fresh resolve_crop_box_with_report off the supplied analysis's
    crop_box -- is already identical to a normal re-run.

    The analysis is passed through untouched, so the run reproduces the
    conditions the folder recorded; only code changes since then move the
    result.
    """
    image = request.files.get("image")
    if image is None or not image.filename:
        return jsonify({"error": "image file is required"}), 400

    analysis_raw = request.form.get("analysis")
    if not analysis_raw:
        return jsonify({"error": "analysis (an earlier run's result.json) is required"}), 400
    try:
        analysis = json.loads(analysis_raw)
    except json.JSONDecodeError:
        return jsonify({"error": "analysis must be valid JSON"}), 400
    if not isinstance(analysis, dict) or ("subject_category" not in analysis and not _is_skipped_analysis(analysis)):
        return jsonify({"error": "analysis must be the analysis object from a result.json"}), 400
    analysis, error = _apply_review_selections(analysis, request.form.get("selections"))
    if error:
        return jsonify({"error": error}), 400

    params, error = _parse_and_validate_params(request.form)
    if error:
        return jsonify({"error": error}), 400
    if _skipped_without_prompt(analysis, params):
        return jsonify({"error": SKIPPED_NEEDS_PROMPT}), 400
    params["stage"] = "finish"
    params["analysis"] = analysis

    engine_params = get_engine_params_template()
    engine_params["acts"][0]["params"] = params
    job_id = enqueue(engine_params, src_content=[image.read()])
    # The photo rides along with this request, so enqueue() resolved srcKeys'
    # "{msg_id}" placeholder into a fresh S3 path of its own -- same as /submit,
    # and unlike /continue, which reuses the prior job's key and therefore
    # overwrites that chain's existing entry. A rerun is a new upload, so it
    # gets its own history entry rather than folding into the one the exported
    # folder's original run recorded (a different session entirely, quite
    # possibly a different user's).
    action_id = request.form.get("action_id") or job_id
    _record_history_safely(request.form.get("user"), job_id, "local", engine_params["srcKeys"][0], action_id, params=params)
    return jsonify({"job_id": job_id}), 202


def _reframe_request(body, analysis):
    """Shared checks for the reframing routes. Returns (params, None) or
    (None, (response, status))."""
    if not isinstance(analysis, dict) or "subject_category" not in analysis:
        return None, (jsonify({"error": "analysis (from /analyze/result) is required"}), 400)
    if not is_compatible(analysis, min_version=get_min_compatible_version()):
        return None, (jsonify({"error": "analysis_compatibility_version_outdated"}), 409)
    params, error = _parse_and_validate_params(body)
    if error:
        return None, (jsonify({"error": error}), 400)
    params["stage"] = "reframe"
    params["analysis"] = analysis
    return params, None


# Results-page 長寬比 change: re-plan only the composition (pipeline.reframe_stage).
# The result is an analysis, fetched through /analyze/result like any other.
@api_bp.post("/reframe/<job_id>")
def reframe_job(job_id):
    body = request.get_json(silent=True) or {}
    params, failure = _reframe_request(body, body.get("analysis"))
    if failure:
        return failure

    prior = get_job_info(job_id, keyToGet=["srcKeys"])
    if not prior or not prior.get("srcKeys"):
        return jsonify({"error": "Prior job not found"}), 404

    engine_params = get_engine_params_template()
    engine_params["srcKeys"] = prior["srcKeys"]
    engine_params["acts"][0]["params"] = params
    return jsonify({"job_id": enqueue(engine_params, src_content=None)}), 202


@api_bp.post("/reframe")
def reframe_upload():
    """/reframe/<job_id> for an analysis with no prior job (an imported
    result folder): the photo is uploaded with the request."""
    image = request.files.get("image")
    if image is None or not image.filename:
        return jsonify({"error": "image file is required"}), 400
    try:
        analysis = json.loads(request.form.get("analysis") or "null")
    except json.JSONDecodeError:
        return jsonify({"error": "analysis must be valid JSON"}), 400
    params, failure = _reframe_request(request.form, analysis)
    if failure:
        return failure

    engine_params = get_engine_params_template()
    engine_params["acts"][0]["params"] = params
    return jsonify({"job_id": enqueue(engine_params, src_content=[image.read()])}), 202


@api_bp.post("/aipoc/reframe")
def aipoc_reframe():
    """Synchronous /reframe for the AIPoC path, whose preview analysis is
    local too (see /aipoc/analyze)."""
    image = request.files.get("image")
    if image is None or not image.filename:
        return jsonify({"error": "image file is required"}), 400
    try:
        analysis = json.loads(request.form.get("analysis") or "null")
    except json.JSONDecodeError:
        return jsonify({"error": "analysis must be valid JSON"}), 400
    params, failure = _reframe_request(request.form, analysis)
    if failure:
        return failure

    try:
        reframed = reframe_stage(
            image.read(), analysis,
            aspect_ratio=params.get("aspect_ratio", "auto"),
            analysis_model=params["analysis_model"],
        )
    except Exception as exc:
        return jsonify({"error": f"重新構圖失敗：{exc}"}), 502
    return jsonify({"status": "success", "analysis": reframed})


@api_bp.post("/aipoc/analyze")
def aipoc_analyze():
    """Local preview analysis for the AIPoC path.

    AIPoC's remote gateway only exposes one opaque "run everything" task --
    it can't be paused/resumed. This runs the same steps 1-2 locally (a
    plain OpenAI call, not queue-bound) just to show the user something to
    confirm; /aipoc/submit below still redoes its own analysis from scratch
    when "continue" is clicked.
    """
    image = request.files.get("image")
    if image is None or not image.filename:
        return jsonify({"error": "image file is required"}), 400

    params, error = _parse_and_validate_params(request.form)
    if error:
        return jsonify({"error": error}), 400

    try:
        analysis = analyze_stage(
            image.read(),
            aspect_ratio=params.get("aspect_ratio", "auto"),
            advanced_options=params.get("advanced_options", {}),
        )
    except Exception as exc:
        return jsonify({"error": f"分析失敗：{exc}"}), 502

    return jsonify({"status": "success", "analysis": analysis})


@api_bp.post("/aipoc/analyze-batch")
def aipoc_analyze_batch():
    """Batch stage 1 (aipoc): synchronous local analyze for each image.

    Looped equivalent of /aipoc/analyze -- analysis is returned inline (no job
    to poll). Each input file yields one entry in list order with analysis or a
    per-file error. Stage 2 is driven per-image by the frontend calling the
    single-image /aipoc/submit (which, per its design, re-runs its own analysis
    from scratch).
    """
    images = [img for img in request.files.getlist("image") if img and img.filename]
    if not images:
        return jsonify({"error": "at least one image file is required"}), 400

    params, error = _parse_and_validate_params(request.form)
    if error:
        return jsonify({"error": error}), 400

    aspect_ratio = params.get("aspect_ratio", "auto")
    advanced_options = params.get("advanced_options", {})
    # Read every file's bytes up front -- request.files' underlying streams
    # belong to this request context and aren't safe to touch from the
    # worker threads below. Each analyze_stage() call is a real OpenAI vision
    # call (~30s+); running them one at a time in a plain loop makes this
    # single request's duration scale linearly with batch size, which is
    # exactly what was blowing past client/proxy timeouts on larger batches.
    # A bounded thread pool collapses that to roughly one image's latency
    # regardless of batch size, while still capping how many images hit the
    # OpenAI API at once.
    file_bytes = [(image.filename, image.read()) for image in images]

    def _analyze_one(entry):
        filename, data = entry
        try:
            return {"filename": filename, "analysis": analyze_stage(data, aspect_ratio=aspect_ratio, advanced_options=advanced_options)}
        except Exception as exc:
            return {"filename": filename, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=min(6, len(file_bytes))) as pool:
        entries = list(pool.map(_analyze_one, file_bytes))

    return jsonify({"jobs": entries}), 200


# ── AIPoC-backed routes (/aipoc/) ───────────────────────────────────────────
#
# Parallel path to /submit + /status + /result above: instead of enqueueing to
# the local Redis queue for PhotoAgent's own docker worker to process, these
# call the shared AIPoC gateway (aipoc-dev.perfectcorp.com) directly and poll
# it directly -- mirrors MTVideoAI's /vor/* routes, which bypass Redis the
# same way for their remote-service effects. See backend/services/aipoc_client.py
# for what's confirmed vs. inferred about the "photoAgent" effect's shape.

# Maps a remote task_id to {access_token} so aipoc_status/aipoc_result can
# poll the remote task. In-memory only -- lost on process restart, same
# tradeoff MTVideoAI's _vor_task_dict makes.
_aipoc_task_dict = {}


def _normalize_remote_status(remote_status):
    """Map AIPoC `status` strings to the values our /status endpoint returns."""
    if remote_status == "success":
        return "success"
    if remote_status == "error":
        return "error"
    return "processing"


def _body_has_engine_error(body):
    """Detect a top-level engine failure (e.g. a timeout) that leaves
    result.status unset -- without this check, such failures would be
    misreported as "processing" forever."""
    body = body or {}
    if body.get("errorCode"):
        return True
    status = body.get("status")
    return isinstance(status, int) and status >= 400


def _body_error_message(body):
    body = body or {}
    result = body.get("result") or {}
    return result.get("error") or body.get("error") or body.get("errorCode")


def _status_from_body(body):
    if _body_has_engine_error(body):
        return "error"
    result = (body or {}).get("result") or {}
    return _normalize_remote_status(result.get("status"))


@api_bp.post("/aipoc/submit")
def aipoc_submit():
    image = request.files.get("image")
    if image is None or not image.filename:
        return jsonify({"error": "image file is required"}), 400

    params, error = _parse_and_validate_params(request.form)
    if error:
        return jsonify({"error": error}), 400

    # Optional: the analysis already shown to the user on the preview screen
    # (from /aipoc/analyze). When present, mirrors /continue/<job_id>'s
    # stage="finish" shape so the remote worker reuses it for steps 3-6
    # instead of re-analyzing from scratch -- see run_photo_agent_task's
    # docstring for what's confirmed vs. assumed about the remote gateway.
    analysis = None
    analysis_raw = request.form.get("analysis")
    if analysis_raw:
        try:
            analysis = json.loads(analysis_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "analysis must be valid JSON"}), 400
        if not isinstance(analysis, dict) or "subject_category" not in analysis:
            return jsonify({"error": "analysis (from /aipoc/analyze) is invalid"}), 400
        analysis, error = _apply_review_selections(analysis, request.form.get("selections"))
        if error:
            return jsonify({"error": error}), 400

    from backend.services import aipoc_client

    _, refresh_token = aipoc_client.get_default_tokens()
    access_token, refresh_token = aipoc_client.refresh_access_token(refresh_token)

    body = aipoc_client.get_req_url(
        access_token,
        [{"contentType": image.mimetype or "image/jpeg", "fileName": image.filename}],
        effect=aipoc_client.EFFECT_PHOTO_AGENT,
    )
    results = body.get("results") or []
    if not results:
        return jsonify({"error": "get_req_url returned no entries"}), 502
    entry = results[0]
    request_spec = (entry.get("request") or [None])[0]
    file_id = entry.get("fileId")
    if not request_spec or not file_id:
        return jsonify({"error": f"get_req_url missing request/fileId: {entry}"}), 502

    aipoc_client.upload_to_req_url(request_spec, image.read())

    body = aipoc_client.run_photo_agent_task(
        access_token,
        src_files=[file_id],
        preset=params["preset"],
        image_quality=params["image_quality"],
        image_model=params["image_model"],
        aspect_ratio=params.get("aspect_ratio", "auto"),
        ai_photo_editing=params["ai_photo_editing"],
        run_qa=params["run_qa"],
        user_prompt=params.get("user_prompt"),
        overwrite_prompt=params.get("overwrite_prompt", False),
        stage="finish" if analysis else None,
        analysis=analysis,
        advanced_options=params.get("advanced_options", {}),
        single_pass_edit=params.get("single_pass_edit", True),
    )
    task_id = body.get("result", {}).get("taskId")
    if not task_id:
        return jsonify({"error": f"remote task/run returned no taskId: {body}"}), 502

    _aipoc_task_dict[task_id] = {"access_token": access_token}
    # No durable per-image identity exists for an AIPoC-path job (task_id is
    # in-memory only, lost on restart) -- action_id (see /continue's own
    # comment on this) is what groups N parallel AIPoC submissions from one
    # runCount>1 "Run" click together, falling back to this task's own id for
    # a solo submission. Reused as image_key too here (nothing better to key
    # on for this path), so a grouped submission's entries all land on the
    # same hash field instead of each getting its own.
    action_id = request.form.get("action_id") or task_id
    _record_history_safely(request.form.get("user"), task_id, "aipoc", action_id, action_id, params=params)
    return jsonify({"task_id": task_id}), 202


@api_bp.get("/aipoc/status/<path:task_id>")
def aipoc_status(task_id):
    from backend.services import aipoc_client

    entry = _aipoc_task_dict.get(task_id)
    if entry is None:
        return jsonify({"error": "Job not found"}), 404

    body = aipoc_client.get_status_by_id(entry["access_token"], task_id, effect=aipoc_client.EFFECT_PHOTO_AGENT)
    status_value = _status_from_body(body)
    out = {"status": status_value}
    if status_value == "error":
        out["error"] = _body_error_message(body)
    return jsonify(out)


@api_bp.get("/aipoc/result/<path:task_id>")
def aipoc_result(task_id):
    """Confirmed against the real AIPoC service (2026-07-03): the photoAgent
    effect returns the exact same output convention as the local worker --
    result.results[0].data[0].cusKeys is [imgKey, jsonKey] and .cusUrls is
    the matching pair of presigned URLs, already signed by AIPoC so no
    presigning of our own is needed here (unlike the local /result route,
    which has to presign itself since S3Access doesn't hand back URLs).
    """
    from backend.services import aipoc_client

    entry = _aipoc_task_dict.get(task_id)
    if entry is None:
        return jsonify({"error": "Job not found"}), 404

    body = aipoc_client.get_status_by_id(entry["access_token"], task_id, effect=aipoc_client.EFFECT_PHOTO_AGENT)
    status_value = _status_from_body(body)
    if status_value != "success":
        return jsonify({"status": status_value, "error": _body_error_message(body) if status_value == "error" else None})

    result = body.get("result") or {}
    flat = []
    for r in result.get("results") or []:
        for d in r.get("data") or []:
            flat.append(d)

    if not flat:
        return jsonify({"status": "error", "error": "AIPoC returned no result data"})

    cus_urls = flat[0].get("cusUrls") or []
    if len(cus_urls) < 2:
        return jsonify({"status": "error", "error": "AIPoC result missing image/metadata URLs"})

    image_url, json_url = cus_urls[0], cus_urls[1]
    try:
        resp = requests.get(json_url, timeout=15)
        resp.raise_for_status()
        result_text = resp.json()
    except Exception as exc:
        return jsonify({"status": "error", "error": f"failed to download result metadata: {exc}"})

    return jsonify({"status": "success", "image_url": image_url, "result": result_text})


@api_bp.get("/aipoc/result/<path:task_id>/image")
def aipoc_result_image(task_id):
    """Same-origin relay of AIPoC's presigned image URL, for the same CORS
    reason as result_image() above. task_id is only ever a value we minted
    into _aipoc_task_dict ourselves (from our own /aipoc/submit), so this
    can't be used to make the server fetch an attacker-supplied URL.
    """
    from backend.services import aipoc_client

    entry = _aipoc_task_dict.get(task_id)
    if entry is None:
        return jsonify({"error": "Job not found"}), 404

    body = aipoc_client.get_status_by_id(entry["access_token"], task_id, effect=aipoc_client.EFFECT_PHOTO_AGENT)
    status_value = _status_from_body(body)
    if status_value != "success":
        return jsonify({"error": _body_error_message(body) if status_value == "error" else "job not successful"}), 400

    result = body.get("result") or {}
    flat = []
    for r in result.get("results") or []:
        for d in r.get("data") or []:
            flat.append(d)

    if not flat:
        return jsonify({"error": "AIPoC returned no result data"}), 500

    cus_urls = flat[0].get("cusUrls") or []
    if not cus_urls:
        return jsonify({"error": "AIPoC result missing image URL"}), 500

    try:
        resp = requests.get(cus_urls[0], timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        return jsonify({"error": f"failed to download result image: {exc}"}), 502

    return Response(resp.content, mimetype=resp.headers.get("Content-Type", "image/png"))
