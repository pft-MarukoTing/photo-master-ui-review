from flask import Blueprint, jsonify, request

from photo_agent.param_validation import validate_photo_agent_request
from backend.services import job_service

api_bp = Blueprint("api", __name__, url_prefix="/api/photo-agent")


@api_bp.post("/submit")
def submit():
    image = request.files.get("image")
    if image is None or not image.filename:
        return jsonify({"error": "image file is required"}), 400

    form = request.form
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
        return jsonify({"error": message}), 400

    job_id = job_service.submit_job(
        image_bytes=image.read(),
        preset=params["preset"],
        image_quality=params["image_quality"],
        ai_photo_editing=params["ai_photo_editing"],
        user_prompt=params.get("user_prompt"),
    )
    return jsonify({"job_id": job_id}), 202


@api_bp.get("/status/<job_id>")
def status(job_id):
    info = job_service.get_status(job_id)
    if info is None:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(info)


@api_bp.get("/result/<job_id>")
def result(job_id):
    info = job_service.get_result(job_id)
    if info is None:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(info)
