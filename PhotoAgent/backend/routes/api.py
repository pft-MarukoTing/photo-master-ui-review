import json

import requests
from flask import Blueprint, jsonify, request

from aiserver_utils.queue import get_job_info
from photo_agent.param_validation import validate_photo_agent_request
from backend.services.put_job import enqueue, create_presigned_url, download_bytes
from backend.services.template_params import get_engine_params_template

api_bp = Blueprint("api", __name__, url_prefix="/api/photo-agent")


def _parse_and_validate_params(form):
    """Returns (params, None) or (None, error_message)."""
    params = {
        "preset": form.get("preset", "natural"),
        "image_quality": form.get("image_quality", "medium"),
        "ai_photo_editing": form.get("ai_photo_editing", "true").lower() == "true",
    }
    user_prompt = form.get("user_prompt")
    if user_prompt:
        params["user_prompt"] = user_prompt

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
    return jsonify({"job_id": job_id}), 202


@api_bp.get("/status/<job_id>")
def status(job_id):
    info = get_job_info(job_id, keyToGet=["status", "error"])
    if info is None:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(info)


@api_bp.get("/result/<job_id>")
def result(job_id):
    info = get_job_info(job_id, keyToGet=["status", "error", "0_res0"])
    if info is None:
        return jsonify({"error": "Job not found"}), 404

    if info.get("status") != "success":
        return jsonify({"status": info.get("status"), "error": info.get("error")})

    result_keys = (info.get("0_res0") or {}).get("cusKeys") or []
    if len(result_keys) < 2:
        return jsonify({"status": "error", "error": "job succeeded but result keys are missing"})

    image_key, json_key = result_keys[0], result_keys[1]
    json_bytes, download_error = download_bytes(json_key, job_id)
    if download_error or json_bytes is None:
        return jsonify({"status": "error", "error": f"failed to download result metadata: {download_error}"})

    result_text = json.loads(json_bytes)
    image_url = create_presigned_url(image_key, "get_object")

    return jsonify({"status": "success", "image_url": image_url, "result": result_text})


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
        ai_photo_editing=params["ai_photo_editing"],
        user_prompt=params.get("user_prompt"),
    )
    task_id = body.get("result", {}).get("taskId")
    if not task_id:
        return jsonify({"error": f"remote task/run returned no taskId: {body}"}), 502

    _aipoc_task_dict[task_id] = {"access_token": access_token}
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
