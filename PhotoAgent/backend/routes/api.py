import json
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Blueprint, Response, current_app, jsonify, request

from aiserver_utils.queue import get_job_info
from photo_agent.param_validation import validate_photo_agent_request
from pipeline import analyze_stage
from backend.services.account import create_account, delete_history_entry, get_history, normalize_username, record_history
from backend.services.put_job import enqueue, create_presigned_url, download_bytes
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
            for key in ("preset", "image_quality", "image_model", "aspect_ratio", "ai_photo_editing", "run_qa")
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
        "aspect_ratio": form.get("aspect_ratio", "auto"),
        "ai_photo_editing": str(form.get("ai_photo_editing", "true")).lower() == "true",
        "run_qa": str(form.get("run_qa", current_app.config.get("DEBUG_UI", False))).lower() == "true",
    }
    user_prompt = form.get("user_prompt")
    if user_prompt:
        params["user_prompt"] = user_prompt
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
    return jsonify({"user": name, "jobs": get_history(name)})


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
    info = get_job_info(job_id, keyToGet=["status", "error", "stage", "run_count", "runs_done"])
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
    info = get_job_info(job_id, keyToGet=["status", "error", "0_res0", "run_count"])
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
        return jsonify(out)

    # Single-run job (two-stage finish-only, or an older combined job from
    # before run_count existed) -- unchanged behavior.
    if info.get("status") != "success":
        return jsonify({"status": info.get("status"), "error": info.get("error")})

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

    res0 = info.get("0_res0") or {}
    runs_meta = res0.get("runs")
    if runs_meta is not None:
        run_param = request.args.get("run", "1")
        try:
            run_index = int(run_param) - 1
        except ValueError:
            return jsonify({"error": "run must be an integer"}), 400
        if run_index < 0 or run_index >= len(runs_meta):
            return jsonify({"error": f"run {run_param} not found"}), 404
        result_keys = runs_meta[run_index].get("cusKeys") or []
    else:
        result_keys = res0.get("cusKeys") or []

    if not result_keys:
        return jsonify({"error": "job succeeded but result keys are missing"}), 500

    image_bytes, download_error = download_bytes(result_keys[0], job_id)
    if download_error or image_bytes is None:
        return jsonify({"error": f"failed to download result image: {download_error}"}), 502

    return Response(image_bytes, mimetype="image/png")


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
    # content-type, so this is a fixed label, same as /result/<job_id>/image's
    # own hardcoded "image/png" for its (always-PNG) output.
    return Response(image_bytes, mimetype="image/jpeg")


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

    entries = []
    for image in images:
        try:
            engine_params = get_engine_params_template()
            engine_params["acts"][0]["params"] = params
            job_id = enqueue(engine_params, src_content=[image.read()])
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
        analysis = analyze_stage(image.read(), aspect_ratio=params.get("aspect_ratio", "auto"))
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
            return {"filename": filename, "analysis": analyze_stage(data, aspect_ratio=aspect_ratio)}
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
        stage="finish" if analysis else None,
        analysis=analysis,
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
