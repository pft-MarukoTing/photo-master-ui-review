"""API routes for the aiHair testbed.

One photo, one of aiHair's nine features per run, through the remote AIPoC
API (effect "aiHair"). The browser keeps the edit history; every Apply is one
run on the image the history is at, so edits chain:

    GET  /api/hair/get_job_id?content_type=image/jpeg   -> { job_id }
    PUT  /api/hair/upload/<job_id>                      (the photo's bytes)
    POST /api/hair/process  { job_id, feature, level? } -> { task_id }
    GET  /api/hair/status/<task_id>                     -> { status, queuePos, ... }
    GET  /api/hair/result/<task_id>                     -> { status, image_url, skipped, ... }
    GET  /api/hair/image/<task_id>                      (the result image, same-origin)

The upload and the result image both go through this server so the browser
never talks to AIPoC's storage directly (no CORS, and the result arrives as a
same-origin blob the page can show, download and send back for the next edit).
"""

import datetime
import mimetypes
import os
import re
import shutil
import tempfile
import time
import traceback
import uuid

import requests
from flask import Blueprint, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from backend.services import aipoc_client

api_bp = Blueprint('api', __name__, url_prefix='/api')

# The photo formats the aiHair engine reads, and the extension each is saved as.
_IMAGE_TYPES = {
    'image/jpeg': '.jpg',
    'image/png':  '.png',
    'image/webp': '.webp',
}

# job_id -> { access_token, request, file_id, content_type, local_path?, created_at }
# Filled by /hair/get_job_id, consumed by /hair/process.
_upload_dict: dict = {}

# task_id -> { access_token, job_id, feature, level, input_path, created_at,
#              result_url?, result_type? }
# Lets status/result/image/feedback reach the remote task after it is submitted.
_task_dict: dict = {}

# Local copies of uploaded photos, so feedback can attach the exact input of a
# task. In-memory index + temp files; lost on server restart.
_INPUT_DIR = os.path.join(tempfile.gettempdir(), 'aihair_inputs')
os.makedirs(_INPUT_DIR, exist_ok=True)

# Entries (and their local input copies) older than this are dropped.
_ENTRY_TTL_SEC = 24 * 3600


def _now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def _new_job_id():
    """Same shape as aiserver_utils' session ids: 20261107_12_34_56_<uuid4>."""
    return _now_utc().strftime('%Y%m%d_%H_%M_%S_') + str(uuid.uuid4())


def _prune_old_entries():
    """Drop upload/task entries past _ENTRY_TTL_SEC, with their local files."""
    cutoff = time.time() - _ENTRY_TTL_SEC
    for store, path_key in ((_upload_dict, 'local_path'), (_task_dict, 'input_path')):
        for key in [k for k, v in store.items() if v.get('created_at', 0) < cutoff]:
            entry = store.pop(key, None) or {}
            path = entry.get(path_key)
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


def _read_request_bytes(req):
    """The request body, or the first file part of a multipart request."""
    if req.content_type and req.content_type.startswith('multipart/'):
        file_obj = next(iter(req.files.values()), None)
        return file_obj.read() if file_obj is not None else b''
    return req.get_data()


@api_bp.errorhandler(Exception)
def _json_error(e):
    """Errors as JSON (the page shows `error`), not Flask's HTML 500 page."""
    if isinstance(e, HTTPException):
        return jsonify({'error': e.description}), e.code
    traceback.print_exc()
    return jsonify({'error': str(e) or e.__class__.__name__}), 502


# ── Validation ──────────────────────────────────────────────────────────────

_FEATURE_BY_LOWER = {name.lower(): name for name in aipoc_client.AI_HAIR_FEATURES}


def _validate_feature(value):
    """`value` as AI_HAIR_FEATURES spells it (matched ignoring case), or None."""
    if not isinstance(value, str):
        return None
    return _FEATURE_BY_LOWER.get(value.strip().lower())


def _validate_level(feature, value):
    """The level a levelled feature runs at (default its lowest); None for the
    rest. Raises ValueError for a level the feature doesn't take."""
    levels = aipoc_client.AI_HAIR_LEVELLED.get(feature)
    if not levels:
        return None
    if value is None or value == '':
        return levels[0]
    # As the engine's param_validation: an int or a digit string; bool (an
    # int subclass) and 1.5 are not levels.
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if type(value) is not int or value not in levels:
        raise ValueError(f'level for {feature} must be one of {levels}')
    return value


# ── aiHair routes (/hair/) ──────────────────────────────────────────────────

@api_bp.route('/hair/get_job_id', methods=['GET'])
def hair_get_job_id():
    """Register one photo upload with AIPoC and return its job_id.

    Query params:
        content_type: the photo's MIME type (image/jpeg, image/png, image/webp)

    The browser then PUTs the photo to /hair/upload/<job_id>, which forwards it
    to the upload URL cached here.

    Returns:
        JSON { job_id }
    """
    _prune_old_entries()

    content_type = request.args.get('content_type', 'image/jpeg')
    ext = _IMAGE_TYPES.get(content_type)
    if ext is None:
        return jsonify({'error': f'content_type must be one of {list(_IMAGE_TYPES)}'}), 400

    _, refresh_token = aipoc_client.get_default_tokens()
    access_token, _ = aipoc_client.refresh_access_token(refresh_token)

    body = aipoc_client.get_req_url(
        access_token,
        [{"contentType": content_type, "fileName": f"photo{ext}"}],
        effect=aipoc_client.EFFECT_AI_HAIR,
    )
    results = body.get("results") or []
    if not results:
        return jsonify({'error': 'get_req_url returned no entries'}), 502
    entry = results[0]
    request_spec = (entry.get("request") or [None])[0]
    file_id = entry.get("fileId")
    if not request_spec or not file_id:
        return jsonify({'error': f'get_req_url missing request/fileId: {entry}'}), 502

    job_id = _new_job_id()
    _upload_dict[job_id] = {
        "access_token": access_token,
        "request":      request_spec,
        "file_id":      file_id,
        "content_type": content_type,
        "created_at":   time.time(),
    }
    return jsonify({'job_id': job_id})


@api_bp.route('/hair/upload/<job_id>', methods=['PUT', 'POST'])
def hair_upload(job_id):
    """Proxy the photo upload: receive it from the browser, forward it to the
    AIPoC upload URL, and keep a local copy for feedback."""
    entry = _upload_dict.get(job_id)
    if entry is None:
        return jsonify({'error': 'Unknown job_id — call /hair/get_job_id first'}), 400

    data = _read_request_bytes(request)
    if not data:
        return jsonify({'error': 'photo bytes are required'}), 400

    local_path = os.path.join(_INPUT_DIR, f'{job_id}{_IMAGE_TYPES[entry["content_type"]]}')
    try:
        with open(local_path, 'wb') as f:
            f.write(data)
        entry["local_path"] = local_path
    except OSError as e:
        # Best-effort: the local copy only serves feedback.
        print(f"[hair] failed to save local input copy: {e}")

    aipoc_client.upload_to_req_url(entry["request"], data)
    entry["uploaded"] = True
    return jsonify({'ok': True})


@api_bp.route('/hair/process', methods=['POST'])
def hair_process():
    """Run one aiHair feature on the uploaded photo.

    JSON body:
        job_id      from /hair/get_job_id, its photo uploaded
        feature     one of aipoc_client.AI_HAIR_FEATURES (case-insensitive)
        level       hairline only: 1 or 2 (default 1)
        output_ext  optional result format: jpg (default), png or webp

    Returns:
        JSON { task_id, job_id, feature, level }
    """
    data = request.get_json(silent=True) or {}
    job_id = (data.get('job_id') or '').strip()
    if not job_id:
        return jsonify({'error': 'job_id is required'}), 400

    feature = _validate_feature(data.get('feature'))
    if feature is None:
        return jsonify({'error': f'feature must be one of {list(aipoc_client.AI_HAIR_FEATURES)}'}), 400
    try:
        level = _validate_level(feature, data.get('level'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    output_ext = (data.get('output_ext') or 'jpg').strip().lower().lstrip('.')
    if output_ext == 'jpeg':
        output_ext = 'jpg'
    if output_ext not in aipoc_client.AI_HAIR_OUTPUT_EXTS:
        return jsonify({'error': f'output_ext must be one of {list(aipoc_client.AI_HAIR_OUTPUT_EXTS)}'}), 400

    entry = _upload_dict.get(job_id)
    if entry is None:
        return jsonify({'error': 'Unknown job_id — call /hair/get_job_id first'}), 400
    if not entry.get("uploaded"):
        return jsonify({'error': 'The photo for this job_id has not been uploaded'}), 400
    _upload_dict.pop(job_id, None)

    body = aipoc_client.run_ai_hair_task(
        access_token = entry["access_token"],
        src_ids      = [entry["file_id"]],
        feature      = feature,
        level        = level,
        output_ext   = output_ext,
    )
    task_id = (body.get("result") or {}).get("taskId")
    if not task_id:
        return jsonify({'error': f'remote task/run returned no taskId: {body}'}), 502

    _task_dict[task_id] = {
        "access_token": entry["access_token"],
        "job_id":       job_id,
        "feature":      feature,
        "level":        level,
        "input_path":   entry.get("local_path"),
        "created_at":   time.time(),
    }
    print(f"[hair] task {task_id}: feature={feature} level={level} job_id={job_id}")
    return jsonify({'task_id': task_id, 'job_id': job_id, 'feature': feature, 'level': level})


# ── Remote status helpers ───────────────────────────────────────────────────

def _normalize_remote_status(remote_status):
    """Map AIPoC `status` strings to the values the frontend polls for."""
    if remote_status == "success":
        return "completed"
    if remote_status == "error":
        return "error"
    return "processing"


def _body_has_engine_error(body):
    """Detect a top-level engine failure in a remote status body.

    On engine errors such as a timeout the remote API returns the error at the
    top level (e.g. {'status': 500, 'errorCode': 'TaskTimeout', 'error': ...})
    and leaves result.status unset, so result-based normalization would wrongly
    report "processing" and the client would poll forever.
    """
    body = body or {}
    if body.get("errorCode"):
        return True
    status = body.get("status")
    return isinstance(status, int) and status >= 400


def _body_error_message(body):
    """Best-effort human-readable error for a failed status body."""
    body = body or {}
    result = body.get("result") or {}
    return result.get("error") or body.get("error") or body.get("errorCode")


def _status_from_body(body):
    """The status the frontend polls for, counting top-level engine errors
    that don't populate result.status (e.g. TaskTimeout)."""
    if _body_has_engine_error(body):
        return "error"
    result = (body or {}).get("result") or {}
    return _normalize_remote_status(result.get("status"))


def _fetch_remote_status(task_id):
    """The remote get-status-by-id-token body for `task_id` and its cached
    entry; (None, None) for a task this server didn't submit."""
    entry = _task_dict.get(task_id)
    if entry is None:
        return None, None
    body = aipoc_client.get_status_by_id(entry["access_token"], task_id,
                                         effect=aipoc_client.EFFECT_AI_HAIR)
    return body, entry


def _first_result_data(body):
    """results[].data[] flattened; the first entry (the one result image)."""
    result = (body or {}).get("result") or {}
    for r in (result.get("results") or []):
        for d in (r.get("data") or []):
            if isinstance(d, dict):
                return d
    return None


def _result_url(data):
    """The result image's URL in one results[].data[] entry: `url` where
    AIPoC fills it, else the first of `dstUrls` (aiHair's results come back
    with `url` null and the image under dstKeys -> dstUrls)."""
    if not data:
        return None
    if data.get("url"):
        return data["url"]
    urls = data.get("dstUrls") or []
    return urls[0] if urls else None


@api_bp.route('/hair/status/<path:task_id>', methods=['GET'])
def hair_status(task_id):
    """Poll an aiHair task's status on the remote AIPoC API."""
    body, _ = _fetch_remote_status(task_id)
    if body is None:
        return jsonify({'error': 'Job not found'}), 404

    result = body.get("result") or {}
    return jsonify({
        'status':    _status_from_body(body),
        'progress':  result.get("progress"),
        'queuePos':  result.get("queuePos"),
        'error':     _body_error_message(body) if _body_has_engine_error(body) else result.get("error"),
        'errorCode': body.get("errorCode"),
        'updatedAt': result.get("updatedAt"),
    })


@api_bp.route('/hair/result/<path:task_id>', methods=['GET'])
def hair_result(task_id):
    """An aiHair task's result.

    Returns:
        JSON { status, feature, level, remote_url, skipped, error? }

    The page loads the image itself from /hair/image/<task_id> (same-origin);
    `remote_url` is AIPoC's URL of it, for debugging. `skipped` is the
    engine's reason for leaving the photo as it was (grayHair with no grey
    hair, hairline with the forehead covered) when the remote result carries
    it, else "".
    """
    body, entry = _fetch_remote_status(task_id)
    if body is None:
        return jsonify({'error': 'Job not found'}), 404

    status = _status_from_body(body)
    out = {'status': status, 'feature': entry.get('feature'), 'level': entry.get('level')}
    if status == 'error':
        out['error'] = _body_error_message(body)
        out['errorCode'] = body.get("errorCode")
        return jsonify(out)

    data = _first_result_data(body)
    if status == 'completed' and not data:
        out['status'] = 'error'
        out['error'] = 'The task finished without a result image'
        return jsonify(out)
    if data:
        entry['result_url'] = _result_url(data)
        out['remote_url'] = entry['result_url']
        out['skipped'] = data.get("skipped") or ""
    return jsonify(out)


@api_bp.route('/hair/image/<path:task_id>', methods=['GET'])
def hair_image(task_id):
    """The result image of a finished task, fetched from AIPoC's storage and
    served same-origin."""
    entry = _task_dict.get(task_id)
    if entry is None:
        return jsonify({'error': 'Job not found'}), 404

    url = entry.get('result_url')
    if not url:
        body, _ = _fetch_remote_status(task_id)
        url = _result_url(_first_result_data(body))
        if not url:
            return jsonify({'error': 'No result image for this task yet'}), 404
        entry['result_url'] = url

    r = requests.get(url, timeout=120)
    if r.status_code >= 300:
        return jsonify({'error': f'Fetching the result image failed: HTTP {r.status_code}'}), 502
    content_type = (r.headers.get('Content-Type') or '').split(';')[0].strip()
    if not content_type.startswith('image/'):
        content_type = mimetypes.guess_type(url.split('?')[0])[0] or 'image/jpeg'
    entry['result_type'] = content_type
    return Response(r.content, status=200, headers={
        'Content-Type':  content_type,
        'Cache-Control': 'private, max-age=3600',
    })


# ── Example samples (preloaded photos) ──────────────────────────────────────

# Drop photos (.jpg / .jpeg / .png / .webp) into <testbed>/example_samples/ and
# they show up as samples on the page; the section is hidden while it's empty.
_EXAMPLE_SAMPLES_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'example_samples')
)
_SAMPLE_EXTS = ('.jpg', '.jpeg', '.png', '.webp')


@api_bp.route('/samples', methods=['GET'])
def samples_list():
    """List the sample photos in example_samples/ (the page fetches each from
    /sample_file/<name>)."""
    if not os.path.isdir(_EXAMPLE_SAMPLES_DIR):
        return jsonify({'samples': []})
    samples = [
        {'name': fn}
        for fn in sorted(os.listdir(_EXAMPLE_SAMPLES_DIR))
        if fn.lower().endswith(_SAMPLE_EXTS) and not fn.startswith('.')
    ]
    return jsonify({'samples': samples})


@api_bp.route('/sample_file/<path:filename>', methods=['GET'])
def sample_file(filename):
    """Serve one sample photo (send_from_directory rejects path traversal)."""
    if not os.path.isdir(_EXAMPLE_SAMPLES_DIR):
        return jsonify({'error': 'samples directory missing'}), 404
    return send_from_directory(_EXAMPLE_SAMPLES_DIR, filename, conditional=True)


# ── User feedback (issue reports) ───────────────────────────────────────────

_FEEDBACK_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'feedback')
)


@api_bp.route('/feedback', methods=['POST'], strict_slashes=False)
def submit_feedback():
    """Save a user-reported issue: the task's input and result photos plus a
    description.

    JSON body: { task_id, description, page? }

    Writes to <testbed>/feedback/issue_<task_id>/:
        description.txt   the description, with the task's feature and level
        source.<ext>      the photo the task ran on (when locally cached)
        result.<ext>      the aiHair result (when available)
    """
    data        = request.get_json(silent=True) or {}
    task_id     = (data.get('task_id') or '').strip()
    description = (data.get('description') or '').strip()
    page        = (data.get('page') or '').strip()

    if not task_id:
        return jsonify({'error': 'task_id is required'}), 400

    safe_id = re.sub(r'[^A-Za-z0-9_.\-]', '_', task_id) or 'unknown'
    issue_dir = os.path.join(_FEEDBACK_DIR, f'issue_{safe_id}')
    os.makedirs(issue_dir, exist_ok=True)

    saved = {}
    errors = {}
    entry = _task_dict.get(task_id) or {}

    # 1) Description text file.
    desc_lines = []
    if page:
        desc_lines.append(f'Page: {page}')
    desc_lines.append(f'Task ID: {task_id}')
    if entry.get('feature'):
        desc_lines.append(f'Feature: {entry["feature"]}')
    if entry.get('level') is not None:
        desc_lines.append(f'Level: {entry["level"]}')
    desc_lines.append(f'Submitted: {_now_utc().isoformat()}')
    desc_lines.append('')
    desc_lines.append(description or '(no description provided)')
    try:
        with open(os.path.join(issue_dir, 'description.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(desc_lines))
        saved['description'] = 'description.txt'
    except OSError as e:
        errors['description'] = str(e)

    # 2) Source photo (locally cached on upload).
    src_path = entry.get('input_path')
    if src_path and os.path.exists(src_path):
        name = 'source' + os.path.splitext(src_path)[1]
        try:
            shutil.copyfile(src_path, os.path.join(issue_dir, name))
            saved['source'] = name
        except OSError as e:
            errors['source'] = str(e)
    else:
        errors['source'] = 'no local input available for this task'

    # 3) Result photo, from the remote status' result URL.
    result_url = entry.get('result_url')
    if not result_url and entry:
        try:
            body, _ = _fetch_remote_status(task_id)
            result_url = _result_url(_first_result_data(body))
        except Exception as e:  # noqa: BLE001 — feedback saves what it can
            errors['remote_status'] = str(e)
    if result_url:
        ext = os.path.splitext(result_url.split('?')[0])[1].lower() or '.jpg'
        name = f'result{ext}'
        try:
            with requests.get(result_url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(os.path.join(issue_dir, name), 'wb') as f:
                    for chunk in r.iter_content(chunk_size=256 * 1024):
                        if chunk:
                            f.write(chunk)
            saved['result'] = name
        except Exception as e:  # noqa: BLE001
            errors['result'] = str(e)
    else:
        errors.setdefault('result', 'no result url available')

    return jsonify({'ok': True, 'dir': issue_dir, 'saved': saved, 'errors': errors})


@api_bp.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        'status':   'healthy',
        'service':  'aiHair-testbed',
        'effect':   aipoc_client.EFFECT_AI_HAIR,
        'features': list(aipoc_client.AI_HAIR_FEATURES),
    })
