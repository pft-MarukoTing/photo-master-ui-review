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
from image_tools import create_preview_bytes, ensure_jpeg_bytes, HISTORY_THUMBNAIL_MAX_SIDE
from photo_agent.param_validation import normalize_advanced_options, validate_photo_agent_request
from pipeline import analyze_stage
from backend.services.account import create_account, clear_history, delete_history_entry, get_history, HISTORY_MAX_LEN, normalize_username, record_history
from backend.services.decrypt_user_id import decrypt as decrypt_user_id
from backend.services.put_job import enqueue, create_presigned_url, download_bytes, derive_thumbnail_key, cache_thumbnail, upload_report_screenshot
from backend.services.report import delete_report, get_report_by_id, get_reports, is_admin, record_report, update_report_status
from backend.services.template_params import get_engine_params_template

api_bp = Blueprint("api", __name__, url_prefix="/api/photo-agent")


def _record_history_safely(user, job_id, path, image_key, action_id, params=None):
    """History bookkeeping must never fail the actual job submission it's
    attached to -- a bad/missing username here is a lost history row, not a
    500 for a job that otherwise queued/ran fine."""
    if not user:
        return
    try:
        history_params = {
            key: (params or {}).get(key)
            for key in ("preset", "image_quality", "image_model", "analysis_model", "aspect_ratio", "ai_photo_editing", "run_qa", "user_prompt", "overwrite_prompt", "run_count", "advanced_options", "single_pass_edit", "stage")
        }
        record_history(normalize_username(user), job_id, path, image_key, action_id, params=history_params)
    except Exception:
        pass


def _parse_and_validate_params(form):
    """Returns (params, None) or (None, error_message).

    `form` may be a werkzeug form (multipart routes) or a plain dict (JSON
    routes like /continue) -- both support .get(key, default), and
    ai_photo_editing is coerced to str() first since a JSON body may send a
    real bool instead of the string "true"/"false" multipart forms send.
    """
    params = {
        "preset": form.get("preset", "natural"),
        "image_quality": form.get("image_quality", "medium"),
        "image_model": form.get("image_model", "gpt-image-2"),
        "analysis_model": form.get("analysis_model", "gpt-4.1-mini"),
        "aspect_ratio": form.get("aspect_ratio", "auto"),
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
    return jsonify({"user": name, "jobs": get_history(name, limit=HISTORY_MAX_LEN)})


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

    report_id = record_report(
        name, job_id, body.get("path") or "local",
        note=body.get("note"), user_prompt=body.get("user_prompt"), screenshot_key=screenshot_key
    )
    return jsonify({"reported": True, "report_id": report_id})


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
    return jsonify({"reports": get_reports()})


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


@api_bp.get("/status/<job_id>")
def status(job_id):
    # "stage"/"run_count"/"runs_done" are only ever present for a combined
    # analyze+edit job (see job_processor.py's set_job_info(...) calls) --
    # absent for the two-stage analyze-only/finish-only jobs, and absent
    # until the worker has actually started the combined job. runs_done lets
    # the frontend notice a new RUN has landed (and go fetch it via /result)
    # without having to re-download every RUN's data on every poll tick.
    info = get_job_info(job_id, keyToGet=["status", "error", "error_message", "stage", "run_count", "runs_done"])
    if info is None:
        return jsonify({"error": "Job not found"}), 404
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
    info = get_job_info(job_id, keyToGet=["status", "error", "error_message", "0_res0", "run_count"])
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
    info = get_job_info(job_id, keyToGet=["status", "error", "0_res0"])
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

    return Response(image_bytes, mimetype="image/png")


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
    info = get_job_info(job_id, keyToGet=["status", "error", "0_res0"])
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

    return Response(image_bytes, mimetype="image/png")


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
    info = get_job_info(job_id, keyToGet=["status", "error", "0_res0"])
    if info is None:
        return jsonify({"error": "Job not found"}), 404
    if info.get("status") != "success":
        return jsonify({"error": info.get("error") or "job not successful"}), 400

    result_keys, error = _resolve_result_keys(info)
    if error:
        return error

    image_key = result_keys[0]
    thumb_key = derive_thumbnail_key(image_key)

    thumb_bytes, thumb_error = download_bytes(thumb_key, job_id)
    if not thumb_error and thumb_bytes is not None:
        return _immutable_image_response(thumb_bytes, "image/jpeg")

    image_bytes, download_error = download_bytes(image_key, job_id)
    if download_error or image_bytes is None:
        return jsonify({"error": f"failed to download result image: {download_error}"}), 502

    thumb_bytes = create_preview_bytes(image_bytes, max_side=HISTORY_THUMBNAIL_MAX_SIDE)
    cache_thumbnail(thumb_bytes, thumb_key, job_id)
    return _immutable_image_response(thumb_bytes, "image/jpeg")


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
    info = get_job_info(job_id, keyToGet=["srcKeys"])
    if info is None:
        return jsonify({"error": "Job not found"}), 404
    src_keys = info.get("srcKeys") or []
    if not src_keys:
        return jsonify({"error": "no source image for this job"}), 404

    image_bytes, download_error = download_bytes(src_keys[0], job_id)
    if download_error or image_bytes is None:
        return jsonify({"error": f"failed to download source image: {download_error}"}), 502

    # srcKeys is always a "src.jpg"-named key (see template_params.py) --
    # store bytes are the raw upload regardless of the browser's own original
    # content-type, so a HEIC/HEIF photo (most browsers can't render that in
    # an <img> tag under any label, see image_tools.py's own doc on this) would
    # otherwise come back looking like a broken image. ensure_jpeg_bytes
    # normalizes it to a real JPEG first -- same reasoning as /result/<job_id>
    # /image's hardcoded "image/png" mimetype, which is safe as-is since that
    # one's bytes are always genuinely PNG-encoded already.
    jpeg_bytes, error_response = _ensure_jpeg_bytes_or_error(image_bytes)
    if error_response:
        return error_response
    return Response(jpeg_bytes, mimetype="image/jpeg")


@api_bp.get("/source/<job_id>/thumbnail")
def source_thumbnail(job_id):
    """Small JPEG counterpart of source_image(), for fast-loading UI lists
    (the history grid) that only ever render this at a few dozen CSS
    pixels. The thumbnail key is derived deterministically from srcKeys[0]
    (put_job.py uploads it there at submission time) -- falls back to the
    full-res source if that thumbnail is missing (a job submitted before
    thumbnails existed, or a thumbnail upload that failed silently).
    """
    info = get_job_info(job_id, keyToGet=["srcKeys"])
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


@api_bp.get("/analyze/result/<job_id>")
def analyze_result(job_id):
    info = get_job_info(job_id, keyToGet=["status", "error", "0_res0"])
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
    return jsonify({"status": "success", "result": {"analysis": analysis}})


@api_bp.post("/continue/<job_id>")
def continue_job(job_id):
    body = request.get_json(silent=True) or {}

    analysis = body.get("analysis")
    if not isinstance(analysis, dict) or "subject_category" not in analysis:
        return jsonify({"error": "analysis (from /analyze/result) is required"}), 400

    params, error = _parse_and_validate_params(body)
    if error:
        return jsonify({"error": error}), 400
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
    if not isinstance(analysis, dict) or "subject_category" not in analysis:
        return jsonify({"error": "analysis must be the analysis object from a result.json"}), 400

    params, error = _parse_and_validate_params(request.form)
    if error:
        return jsonify({"error": error}), 400
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
