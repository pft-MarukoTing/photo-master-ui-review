"""HTTP API routes for batch image evaluation jobs."""

import io
import json
import os
import shutil
import tempfile
import zipfile

from flask import Blueprint, Response, jsonify, request

from analyze.backend import config
from analyze.backend.services import evaluator, openai_client, prompts


api_bp = Blueprint("api", __name__, url_prefix="/api")


def _parse_overrides(form):
    """Returns (overrides, None) or (None, error_message).

    Optional per-job overrides of the env-driven defaults; values outside a
    sane range 400 immediately instead of failing mid-job.
    """
    overrides = {}
    model = (form.get("model") or "").strip()
    if model:
        overrides["model"] = model
    for name, low, high in (("timeout", 10, 600), ("max_retries", 0, 10), ("max_concurrency", 1, 16)):
        value = (form.get(name) or "").strip()
        if not value:
            continue
        try:
            number = int(value)
        except ValueError:
            return None, f"{name} 必須是整數"
        if not low <= number <= high:
            return None, f"{name} 必須介於 {low} 與 {high} 之間"
        overrides[name] = number
    return overrides, None


@api_bp.get("/health")
def health():
    key_present = openai_client.openai_key_present()
    payload = {
        "status": "ok" if key_present else "error",
        "service": "analyze",
        "schema_version": config.SCHEMA_VERSION,
        "jobs_in_memory": evaluator.job_count(),
        "openai_key_present": key_present,
    }
    if not key_present:
        payload.update({
            "code": openai_client.OPENAI_API_KEY_MISSING_CODE,
            "error": openai_client.OPENAI_API_KEY_MISSING_MESSAGE,
        })
    return jsonify(payload), 200 if key_present else 503


@api_bp.post("/evaluations")
def create_evaluation():
    if not openai_client.openai_key_present():
        return jsonify({
            "code": openai_client.OPENAI_API_KEY_MISSING_CODE,
            "error": openai_client.OPENAI_API_KEY_MISSING_MESSAGE,
        }), 503

    upload = request.files.get("file") or request.files.get("zip")
    if upload is None or not upload.filename:
        return jsonify({"error": "file (result ZIP) is required"}), 400
    if not upload.filename.lower().endswith(".zip"):
        return jsonify({"error": "只接受 .zip 檔案。"}), 400

    overrides, error = _parse_overrides(request.form)
    if error:
        return jsonify({"error": error}), 400

    workdir = tempfile.mkdtemp(prefix="analyze_job_")
    zip_path = os.path.join(workdir, "upload.zip")
    try:
        upload.save(zip_path)
        if not zipfile.is_zipfile(zip_path):
            raise ValueError("上傳的檔案不是有效的 ZIP。")
    except ValueError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        return jsonify({"error": f"ZIP 上傳失敗：{exc}"}), 500

    job_id = evaluator.create_job(upload.filename)
    evaluator.start_job(job_id, zip_path, workdir, overrides)
    return jsonify({"job_id": job_id}), 202


@api_bp.get("/evaluations/<job_id>")
def evaluation_status(job_id):
    info = evaluator.get_job_status(job_id)
    if info is None:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(info)


@api_bp.get("/evaluations/<job_id>/results")
def evaluation_results(job_id):
    data = evaluator.get_job_results(job_id)
    if data is None:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(data)


@api_bp.get("/evaluations/<job_id>/download")
def evaluation_download(job_id):
    data = evaluator.get_job_results(job_id)
    if data is None:
        return jsonify({"error": "Job not found"}), 404

    # Keep previews available to the UI through the results API, but omit the
    # large Base64 JPEG payloads from the downloadable JSON. Copy every result
    # before removing the field so the in-memory job data is never mutated.
    download_data = {
        **data,
        "results": [
            {key: value for key, value in result.items() if key != "thumbnail"}
            for result in data["results"]
        ],
    }
    if download_data["status"] not in ("completed", "failed"):
        download_data["partial"] = True

    fmt = (request.args.get("format") or "zip").lower()
    if fmt != "zip":
        return jsonify({"error": "format must be zip"}), 400

    stem = f"evaluation-{job_id[:8]}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("results.json", json.dumps(download_data, ensure_ascii=False, indent=2))
        prompt_data = {
            "evaluation_prompt": prompts.build_evaluation_prompt().split("\n"),
        }
        zf.writestr("prompt.json", json.dumps(prompt_data, ensure_ascii=False, indent=2))
    return Response(buf.getvalue(), mimetype="application/zip", headers={
        "Content-Disposition": f"attachment; filename={stem}.zip",
    })
