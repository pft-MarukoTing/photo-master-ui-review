"""Job enqueue utilities using aiserver_utils.

Mirrors MTVideoAI/backend/services/put_job.py. Deliberately does NOT import
set_task_to_redis.py: that module connects to a RedisCluster at hardcoded
localhost addresses as a *module-level* side effect (`redis_conn =
RedisCluster(...)`, executed on import, independent of `.env.default`'s
REDIS_NODES). Since this backend runs as a separate service from the
docker worker, merely importing that module would try (and likely fail) to
reach that hardcoded cluster. This uses aiserver_utils.pf_redis.pf_redis_client
instead -- the same env-driven client aiserver_utils.queue itself uses.
"""

import json
import os
import threading
import uuid
from datetime import datetime

from aiserver_utils.pf_redis import pf_redis_client
from aiserver_utils.s3 import S3Access, S3AccessV2

from image_tools import create_preview_bytes, HISTORY_THUMBNAIL_MAX_SIDE

MSG_ID_QUEUE = os.getenv("REDIS_MSG_QUEUE", "yce_ai:photoAgent:queue")
MSG_DETAIL_QUEUE = os.getenv("REDIS_MSG_INFO_FORMAT", "yce_ai:photoAgent:{msg_id}:info")

_s3_accessor = None
_s3_accessor_v2 = None
_s3_accessor_lock = threading.Lock()
# S3Access.upload_to_s3() (unlike S3AccessV2) isn't safe for concurrent
# callers: it recreates and restarts its own thread pool as instance state on
# every call, assuming only one upload runs at a time on this shared
# singleton. Under app.run(threaded=True), two /submit or /analyze-batch
# requests landing close together race on that state and one of them dies
# with "cannot join thread before it is started". Serializing just the
# upload call (not the whole request) keeps requests concurrent everywhere
# else while avoiding the race.
_s3_upload_lock = threading.Lock()


def get_s3_accessor():
    global _s3_accessor
    with _s3_accessor_lock:
        if _s3_accessor is None:
            _s3_accessor = S3Access()
        return _s3_accessor


def get_s3_accessor_v2():
    global _s3_accessor_v2
    if _s3_accessor_v2 is None:
        _s3_accessor_v2 = S3AccessV2()
    return _s3_accessor_v2


def get_redis_conn():
    return pf_redis_client


def get_session_id():
    now = datetime.utcnow()
    return now.strftime("%Y%m%d_%H_%M_%S_") + str(uuid.uuid4())


def get_timestamp():
    now = datetime.utcnow()
    return float(now.strftime("%Y")[2:] + now.strftime("%m%d%H%M%S.%f"))


def derive_thumbnail_key(key):
    """Sibling S3 key for a small JPEG thumbnail of any stored image, e.g.
    ".../src.jpg" -> ".../src_thumb.jpg" or ".../0_res0_thumb_0.png" ->
    ".../0_res0_thumb_0_thumb.jpg". Always ".jpg" regardless of the
    original key's extension -- create_preview_bytes always produces JPEG
    bytes, whether the source was a "src.jpg" upload or a "*.png" result.
    Deterministic so callers (the /source/<job_id>/thumbnail and
    /result/<job_id>/thumbnail routes) never need a separately-stored
    pointer to find it -- used both for the source image (thumbnail
    generated eagerly at upload time, below) and the result image
    (generated lazily on first request, see backend/routes/api.py's
    result_thumbnail())."""
    root = os.path.splitext(key)[0]
    return f"{root}_thumb.jpg"


def cache_thumbnail(thumb_bytes, thumb_key, task_id):
    """Fire-and-forget cache write for a thumbnail. upload_buffer only
    enqueues the bytes onto S3AccessV2's persistent worker threads and
    returns immediately (unlike S3Access.upload_to_s3, guarded by
    _s3_upload_lock above because it isn't safe for concurrent callers) --
    a slow/failed write here must never block or fail whatever request
    triggered it (a job submission, or a thumbnail GET request)."""
    try:
        get_s3_accessor_v2().upload_buffer(lambda result: None, thumb_bytes, thumb_key, task_id)
    except Exception:
        pass


def _upload_source_thumbnail(image_bytes, src_key, task_id):
    """Best-effort: unlike a result thumbnail (see result_thumbnail()'s
    docstring for why that one is lazy), a source image's thumbnail is
    always exactly the one file every history-list tile shows immediately
    on load (jobIds[0], not the possibly-ambiguous "whichever job/RUN ends
    up done first") -- so it's worth generating eagerly, right alongside
    the real upload, instead of paying for it on every list render."""
    try:
        thumb_bytes = create_preview_bytes(image_bytes, max_side=HISTORY_THUMBNAIL_MAX_SIDE)
        cache_thumbnail(thumb_bytes, derive_thumbnail_key(src_key), task_id)
    except Exception:
        pass


def enqueue(engine_param_dict, src_content=None, job_id=None):
    """Upload src_content (list of bytes, one per srcKey) if given, then enqueue.

    MTVideoAI's put_job.enqueue() takes a src_content param but never uses it,
    because its frontend uploads directly to S3 via a presigned PUT URL
    (create_presigned_url) before calling enqueue. PhotoAgent's
    aiserver_utils has no presigned-PUT helper, so this uploads the image
    server-side here instead.
    """
    if job_id is None:
        job_id = get_session_id()

    timestamp = get_timestamp()
    redis_conn = get_redis_conn()

    for key in engine_param_dict:
        if key in ("srcKeys", "dstKeys"):
            for k in range(len(engine_param_dict[key])):
                if engine_param_dict[key][k] is not None:
                    engine_param_dict[key][k] = engine_param_dict[key][k].replace("{msg_id}", f"{job_id}")

    if src_content:
        with _s3_upload_lock:
            get_s3_accessor().upload_to_s3(src_content, engine_param_dict["srcKeys"])
        _upload_source_thumbnail(src_content[0], engine_param_dict["srcKeys"][0], job_id)

    upload_dict = engine_param_dict.copy()
    upload_dict["srcKeys"] = json.dumps(engine_param_dict["srcKeys"])
    upload_dict["dstKeys"] = json.dumps(engine_param_dict["dstKeys"])
    upload_dict["acts"] = json.dumps(engine_param_dict["acts"])
    msg_info_key = MSG_DETAIL_QUEUE.replace("{msg_id}", f"{job_id}")
    redis_conn.hset(msg_info_key, mapping=upload_dict)
    redis_conn.zadd(MSG_ID_QUEUE, {job_id: timestamp})

    return job_id


def create_presigned_url(object_name, action, content_type=None):
    s3_accessor = get_s3_accessor()
    s3_client = s3_accessor.s3_client_proxy.s3Client
    bucket_name = s3_accessor.s3config["bucket_name"]

    params = {"Bucket": bucket_name, "Key": object_name}
    if action == "put_object" and content_type:
        params["ContentType"] = content_type

    return s3_client.generate_presigned_url(action, Params=params)


def download_bytes(file_key, task_id, timeout=15):
    """Download raw bytes for file_key via S3AccessV2.

    S3Access.download_from_s3() is image-only: s3_utils.py's
    download_fileobj() always pipes the object through read_image() (PIL
    Image.open), which silently returns None for anything that isn't a
    decodable image -- e.g. photoAgent's JSON result file.
    S3AccessV2.download_buffer() uses download_file_binary() instead, which
    returns raw bytes untouched.
    """
    done = threading.Event()
    outcome = {}

    def _callback(result):
        outcome.update(result)
        done.set()

    get_s3_accessor_v2().download_buffer(_callback, file_key, task_id)
    if not done.wait(timeout=timeout):
        return None, "timed out downloading from S3"
    return outcome.get("img_buffer"), outcome.get("error")
