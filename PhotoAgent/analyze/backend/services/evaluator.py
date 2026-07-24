"""Batch evaluation pipeline for original-vs-edited retouching QA.

Everything between "uploaded ZIP" and "scored results" lives here: ZIP safety
checks and extraction, run discovery, the per-run original-vs-result OpenAI
evaluation, and deterministic score math (the model supplies each direct
0-100 dimension score and the backend applies only the configured weights),
and the in-memory job store processed by a background thread inside this
backend process (no external worker).
"""

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from analyze.backend import config
from analyze.backend.services import openai_client

logger = logging.getLogger("analyze")


class ZipValidationError(Exception):
    """Raised when an uploaded ZIP violates safety or size limits."""


# ── ZIP safety & extraction ────────────────────────────────────────────────────

_JUNK_BASENAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
_SIDECAR_NAMES = {"result.json"}


def _is_junk_member(name: str) -> bool:
    parts = [p for p in name.split("/") if p]
    if not parts:
        return True
    for part in parts:
        if part == "__MACOSX" or part.startswith("."):
            return True
    return parts[-1] in _JUNK_BASENAMES


def _is_wanted_member(name: str) -> bool:
    base = os.path.basename(name)
    if base in _SIDECAR_NAMES:
        return True
    return os.path.splitext(base)[1].lower() in config.ALLOWED_IMAGE_EXTS


def safe_extract(zip_path: str, extract_dir: str) -> dict:
    """Safely extracts the wanted members of a result ZIP into extract_dir.

    Guards against Zip Slip (traversal, absolute paths, symlinks) and zip
    bombs (declared-size, per-file compression-ratio, and streamed-size
    checks), skips junk (__MACOSX, dot-files, .DS_Store) plus every file type
    the evaluation does not need, and enforces the configured entry/image/
    size limits. Returns extraction counts. Raises ZipValidationError.
    """
    root = os.path.realpath(extract_dir)
    extracted_files = 0
    extracted_bytes = 0
    image_count = 0

    with zipfile.ZipFile(zip_path) as zf:
        members = zf.infolist()
        if len(members) > config.MAX_ENTRIES:
            raise ZipValidationError(f"ZIP 內含過多項目（超過 {config.MAX_ENTRIES}）。")

        declared_total = sum(m.file_size for m in members)
        if declared_total > config.MAX_UNCOMPRESSED_BYTES:
            raise ZipValidationError("ZIP 解壓後總大小超過限制。")

        for member in members:
            name = member.filename.replace("\\", "/")
            parts = [p for p in name.split("/") if p]
            if ".." in parts or name.startswith("/") or "\x00" in name:
                raise ZipValidationError("ZIP 內含路徑跳脫（Zip Slip），已拒絕。")
            if member.is_dir() or _is_junk_member(name) or not _is_wanted_member(name):
                continue
            # Symlinks could point extraction outside the temp dir.
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ZipValidationError("ZIP 內含符號連結，已拒絕。")
            if member.compress_size and member.file_size / member.compress_size > config.MAX_COMPRESSION_RATIO:
                raise ZipValidationError("ZIP 內含異常壓縮率的檔案（疑似 zip bomb）。")

            target = os.path.realpath(os.path.join(root, name))
            if os.path.commonpath([root, target]) != root:
                raise ZipValidationError("ZIP 內含路徑跳脫（Zip Slip），已拒絕。")

            is_image = os.path.splitext(name)[1].lower() in config.ALLOWED_IMAGE_EXTS
            if is_image:
                image_count += 1
                if image_count > config.MAX_TOTAL_IMAGES:
                    raise ZipValidationError(f"ZIP 內圖片數量超過限制（{config.MAX_TOTAL_IMAGES}）。")
            per_file_cap = config.MAX_IMAGE_BYTES if is_image else config.MAX_SIDECAR_BYTES

            os.makedirs(os.path.dirname(target), exist_ok=True)
            written = 0
            with zf.open(member) as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(256 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    extracted_bytes += len(chunk)
                    if written > per_file_cap:
                        raise ZipValidationError(f"檔案 {os.path.basename(name)} 超過單檔大小限制。")
                    if extracted_bytes > config.MAX_UNCOMPRESSED_BYTES:
                        raise ZipValidationError("ZIP 解壓後總大小超過限制。")
                    dst.write(chunk)
            extracted_files += 1

    return {"files": extracted_files, "bytes": extracted_bytes, "images": image_count}


# ── Run discovery ──────────────────────────────────────────────────────────────

_RUN_DIR_RE = re.compile(r"^run_\d+$")


def _run_sort_key(run_id: str) -> int:
    m = re.search(r"(\d+)$", run_id)
    return int(m.group(1)) if m else 0


def discover_runs(extract_dir: str) -> list:
    """Walks the extracted tree and returns one work unit per run_x folder.

    Units are anchored on any directory named run_<n>; the parent directory is
    the task, and the first task-level image (if any, sorted by filename) is
    the shared original for every run of that task. Both original and
    result.png are required for comparison scoring -- incomplete units are
    still returned (marked invalid later) so counts stay honest. result.json
    is optional local metadata and is never sent to OpenAI.
    """
    units = []
    for dirpath, _dirnames, _filenames in os.walk(extract_dir):
        if not _RUN_DIR_RE.match(os.path.basename(dirpath)):
            continue
        task_dir = os.path.dirname(dirpath)
        original_path = None
        for candidate in sorted(os.listdir(task_dir)):
            candidate_path = os.path.join(task_dir, candidate)
            if (
                os.path.isfile(candidate_path)
                and os.path.splitext(candidate)[1].lower() in config.ALLOWED_IMAGE_EXTS
            ):
                original_path = candidate_path
                break
        result_png = os.path.join(dirpath, "result.png")
        result_json = os.path.join(dirpath, "result.json")
        units.append({
            "task_id": os.path.basename(task_dir),
            "run_id": os.path.basename(dirpath),
            "result_png_path": result_png if os.path.isfile(result_png) else None,
            "original_path": original_path,
            "result_json_path": result_json if os.path.isfile(result_json) else None,
        })
        if len(units) > config.MAX_RUNS:
            raise ZipValidationError(f"ZIP 內 run 數量超過限制（{config.MAX_RUNS}）。")
    units.sort(key=lambda u: (u["task_id"], _run_sort_key(u["run_id"])))
    return units


def _load_source_metadata(result_json_path):
    """Defensively pulls {image_model, image_quality} out of result.json.

    result.json is generation metadata whose schema we do not control --
    missing or corrupt files never invalidate the run.
    """
    if not result_json_path:
        return None
    try:
        with open(result_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    extra = (data or {}).get("extra-info") if isinstance(data, dict) else None
    if not isinstance(extra, dict):
        return None
    meta = {k: extra.get(k) for k in ("image_model", "image_quality") if extra.get(k)}
    return meta or None


# ── Score math (backend-owned; the model never computes any of this) ──────────

def normalize_model_output(raw: dict) -> dict:
    """Validates model JSON into {dimensions, summary, suggestions}.

    Iterates the canonical dimension list from config (never the model's own
    keys) and requires every direct model score to be an integer from 0 to 100.
    Scores are never rounded, clamped, mapped, or otherwise transformed.
    """
    dims_in = (raw or {}).get("dimensions")
    if not isinstance(dims_in, dict):
        raise ValueError("model output missing dimensions")

    dimensions = {}
    for key in config.DIMENSION_KEYS:
        block = dims_in.get(key)
        if not isinstance(block, dict):
            raise ValueError(f"model output missing dimension: {key}")
        score = block.get("score")
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
            raise ValueError(f"model output score for {key} must be an integer from 0 to 100")
        dimensions[key] = {
            "score": score,
            "reason": str(block.get("reason") or ""),
            "issues": [str(i) for i in (block.get("issues") or []) if i],
        }

    return {
        "dimensions": dimensions,
        "summary": str((raw or {}).get("summary") or ""),
        "suggestions": [str(s) for s in (raw or {}).get("suggestions") or [] if s],
    }


def compute_scores(normalized: dict) -> dict:
    """Returns the direct weighted average of the model's dimension scores."""
    # Every dimension is always scored (the schema requires all ten), so the
    # active weight is simply the full configured total, validated to be 100.
    total_weight = sum(config.WEIGHTS.values())
    dims_out = {}
    weighted_sum = 0.0
    for key in config.DIMENSION_KEYS:
        weight = config.WEIGHTS[key]
        block = normalized["dimensions"][key]
        weighted_sum += weight * block["score"]
        dims_out[key] = {
            "score": block["score"], "weight": weight,
            "effective_weight": round(weight / total_weight, 4),
            "status": "scored",
            "reason": block["reason"], "issues": block["issues"],
        }

    final_score = round(weighted_sum / total_weight, 1)
    return {
        "dimensions": dims_out,
        # Keep raw_weighted_score for v2 response compatibility.  With no
        # score-ceiling mechanism it is always identical to final_score.
        "raw_weighted_score": final_score,
        "final_score": final_score,
    }


# ── Per-run evaluation ─────────────────────────────────────────────────────────

def _empty_score_fields():
    return {
        "dimensions": None,
        "raw_weighted_score": None,
        "final_score": None,
        "summary": "",
        "suggestions": [],
        "original": None,
    }


def _merge_usage(usage_a, usage_b):
    """Sums the numeric token fields of the two per-image OpenAI usage dicts."""
    if not usage_a and not usage_b:
        return None
    merged = {}
    for usage in (usage_a or {}), (usage_b or {}):
        for key, value in usage.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            merged[key] = merged.get(key, 0) + value
    return merged or None


def _score_image(image_bytes, model, overrides):
    """Runs one single-image evaluation and returns (block, telemetry).

    block matches the shape stored under both the top-level (edited) result and
    the nested ``original`` object: dimensions/final_score/summary/suggestions.
    telemetry carries the per-call usage/latency/retry so the caller can sum
    both calls into the run-level totals.
    """
    call = openai_client.evaluate_single_image(
        image_bytes,
        model=model,
        timeout=overrides.get("timeout"),
        max_retries=overrides.get("max_retries"),
    )
    normalized = normalize_model_output(call["raw"])
    scores = compute_scores(normalized)
    block = {
        "dimensions": scores["dimensions"],
        "raw_weighted_score": scores["raw_weighted_score"],
        "final_score": scores["final_score"],
        "summary": normalized["summary"],
        "suggestions": normalized["suggestions"],
    }
    telemetry = {
        "usage": call["usage"],
        "latency_ms": call["latency_ms"],
        "retry_count": call["retry_count"],
    }
    return block, telemetry


def _make_thumbnail(image_bytes):
    if not config.EVAL_THUMBNAILS_ENABLED:
        return None
    try:
        thumb = openai_client.create_preview_bytes(
            image_bytes,
            max_side=config.EVAL_THUMBNAIL_MAX_SIDE,
            jpeg_quality=config.EVAL_THUMBNAIL_JPEG_QUALITY,
        )
        return openai_client.image_to_data_url(thumb, "image/jpeg")
    except Exception:
        return None


def evaluate_run(unit: dict, overrides: dict = None) -> dict:
    """Scores one run unit and returns the complete per-run result dict.

    Never raises: any failure comes back as status "invalid" (unusable input,
    no model call made) or "error" (evaluation failed after retries), so one
    bad run can never abort the batch.
    """
    overrides = overrides or {}
    model = overrides.get("model") or config.EVAL_MODEL
    base = {
        "schema_version": config.SCHEMA_VERSION,
        "task_id": unit["task_id"],
        "run_id": unit["run_id"],
        "image_type": "edited",
        "model": model,
        "original_image": os.path.basename(unit["original_path"]) if unit["original_path"] else None,
        "source_metadata": _load_source_metadata(unit["result_json_path"]),
        "thumbnail": None,
        "usage": None,
        "latency_ms": None,
        "retry_count": 0,
        "error": None,
        **_empty_score_fields(),
    }

    if not unit["result_png_path"]:
        return {**base, "status": "invalid", "error": "缺少 result.png，無法評分。"}
    if not unit["original_path"]:
        return {**base, "status": "invalid", "error": "缺少原圖，無法進行原圖與 result 的比較評分。"}

    # The HTTP route performs the same check before creating a job; this guard
    # also covers direct calls and a key removed between job creation and
    # worker execution.
    if not openai_client.openai_key_present():
        return {
            **base,
            "status": "error",
            "error": openai_client.OPENAI_API_KEY_MISSING_MESSAGE,
        }

    try:
        with open(unit["result_png_path"], "rb") as f:
            result_bytes = f.read()
    except OSError as exc:
        return {**base, "status": "invalid", "error": f"無法讀取 result.png：{exc}"}

    try:
        with open(unit["original_path"], "rb") as f:
            original_bytes = f.read()
    except OSError as exc:
        return {**base, "status": "invalid", "error": f"無法讀取原圖：{exc}"}

    # Only confirm the bytes decode; the preview JPEGs are produced later by
    # the thumbnail and the model call, so encoding here would just be repeated.
    try:
        openai_client.decode_image(original_bytes)
    except Exception as exc:
        return {**base, "status": "invalid", "error": f"原圖不是可解析的圖片：{exc}"}
    try:
        openai_client.decode_image(result_bytes)
    except Exception as exc:
        return {**base, "status": "invalid", "error": f"result.png 不是可解析的圖片：{exc}"}

    # The extracted ZIP is deleted as soon as the job finishes, so persist the
    # edited image as an inline preview while its bytes are still available.
    base["thumbnail"] = _make_thumbnail(result_bytes)

    try:
        original_block, original_tele = _score_image(original_bytes, model, overrides)
        edited_block, edited_tele = _score_image(result_bytes, model, overrides)

        base.update(
            usage=_merge_usage(original_tele["usage"], edited_tele["usage"]),
            latency_ms=round((original_tele["latency_ms"] or 0) + (edited_tele["latency_ms"] or 0), 1),
            retry_count=(original_tele["retry_count"] or 0) + (edited_tele["retry_count"] or 0),
        )
        return {
            **base,
            "status": "scored",
            "dimensions": edited_block["dimensions"],
            "raw_weighted_score": edited_block["raw_weighted_score"],
            "final_score": edited_block["final_score"],
            "summary": edited_block["summary"],
            "suggestions": edited_block["suggestions"],
            "original": original_block,
        }
    except Exception as exc:
        logger.error("run evaluation failed", extra={
            "task_id": unit["task_id"],
            "error_type": type(exc).__name__,
            "params": {"run_id": unit["run_id"], "error": str(exc)},
        })
        return {**base, "status": "error", "error": str(exc)}


# ── In-memory job store (processed by a background thread; no worker) ─────────

_jobs_lock = threading.RLock()
_jobs = OrderedDict()


def create_job(filename: str) -> str:
    job_id = uuid.uuid4().hex
    now = time.time()
    with _jobs_lock:
        _evict_jobs_locked()
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "pending",
            "filename": filename,
            "total_runs": 0,
            "completed": 0,
            "failed": 0,
            "invalid": 0,
            "progress": 0,
            "results": [],
            "error": None,
            "created_at": now,
            "updated_at": now,
            "finished_at": None,
        }
    return job_id


def job_count() -> int:
    with _jobs_lock:
        return len(_jobs)


def get_job_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        return {key: job[key] for key in (
            "job_id", "status", "total_runs", "completed", "failed",
            "invalid", "progress", "error", "created_at",
        )}


def get_job_results(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        # Result dicts are append-only (never mutated after being recorded),
        # so copying the list is enough for a consistent snapshot.
        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "schema_version": config.SCHEMA_VERSION,
            "filename": job["filename"],
            "total_runs": job["total_runs"],
            "completed": job["completed"],
            "failed": job["failed"],
            "invalid": job["invalid"],
            "progress": job["progress"],
            "error": job["error"],
            "results": list(job["results"]),
        }


def _update_job(job_id, **fields):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.update(fields)
        job["updated_at"] = time.time()


def _evict_jobs_locked():
    now = time.time()
    expired = [jid for jid, job in _jobs.items()
               if job["finished_at"] and now - job["finished_at"] > config.EVAL_JOB_TTL_SECONDS]
    for jid in expired:
        _jobs.pop(jid, None)
    while len(_jobs) > config.EVAL_MAX_JOBS:
        victim = next((jid for jid, job in _jobs.items() if job["finished_at"]), None)
        if victim is None:
            break  # never evict live jobs
        _jobs.pop(victim, None)


def start_job(job_id: str, zip_path: str, workdir: str, overrides: dict = None):
    thread = threading.Thread(
        target=_run_job,
        args=(job_id, zip_path, workdir, overrides or {}),
        name=f"analyze-job-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return thread


def _run_job(job_id, zip_path, workdir, overrides):
    _update_job(job_id, status="processing")
    try:
        if not openai_client.openai_key_present():
            _update_job(
                job_id,
                status="failed",
                error=openai_client.OPENAI_API_KEY_MISSING_MESSAGE,
                finished_at=time.time(),
            )
            return

        extract_dir = os.path.join(workdir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        stats = safe_extract(zip_path, extract_dir)
        units = discover_runs(extract_dir)
        if not units:
            _update_job(job_id, status="failed", finished_at=time.time(),
                        error="ZIP 內找不到任何 run_x 結構，請確認上傳的是 result ZIP。")
            return
        _update_job(job_id, total_runs=len(units))
        logger.info("runs discovered", extra={"status": "processing", "params": {
            "job_id": job_id, "runs": len(units),
            "files": stats["files"], "images": stats["images"],
        }})

        max_workers = max(1, int(overrides.get("max_concurrency") or config.EVAL_MAX_CONCURRENCY))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"analyze-run-{job_id[:8]}") as pool:
            futures = [pool.submit(_evaluate_and_record, job_id, unit, overrides) for unit in units]
            for future in futures:
                future.result()  # _evaluate_and_record never raises

        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is not None:
                job["results"].sort(key=lambda r: (r["task_id"], _run_sort_key(r["run_id"])))
        _update_job(job_id, status="completed", finished_at=time.time())
    except ZipValidationError as exc:
        _update_job(job_id, status="failed", error=str(exc), finished_at=time.time())
    except Exception as exc:
        logger.exception("job failed", extra={"status": "failed", "params": {"job_id": job_id}})
        _update_job(job_id, status="failed", error=str(exc), finished_at=time.time())
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _evaluate_and_record(job_id, unit, overrides):
    try:
        result = evaluate_run(unit, overrides)
    except Exception as exc:  # belt & suspenders -- evaluate_run should not raise
        logger.exception("run crashed", extra={
            "task_id": unit["task_id"], "params": {"run_id": unit["run_id"]},
        })
        result = {
            "schema_version": config.SCHEMA_VERSION,
            "task_id": unit["task_id"],
            "run_id": unit["run_id"],
            "image_type": "edited",
            "model": (overrides or {}).get("model") or config.EVAL_MODEL,
            "original_image": None, "source_metadata": None,
            "thumbnail": None,
            "usage": None, "latency_ms": None, "retry_count": 0,
            "status": "error", "error": str(exc),
            **_empty_score_fields(),
        }
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job["results"].append(result)
        if result["status"] == "scored":
            job["completed"] += 1
        elif result["status"] == "invalid":
            job["invalid"] += 1
        else:
            job["failed"] += 1
        done = job["completed"] + job["failed"] + job["invalid"]
        job["progress"] = int(round(100 * done / job["total_runs"])) if job["total_runs"] else 0
        job["updated_at"] = time.time()
