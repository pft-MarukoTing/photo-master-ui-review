"""Enqueue PhotoAgent jobs into the same Redis queue/S3 bucket the existing
docker worker (server.py) already reads from, and read job status/results.

Deliberately does NOT import set_task_to_redis.py: that module connects to a
RedisCluster at hardcoded localhost addresses as a *module-level* side
effect (`redis_conn = RedisCluster(...)`, executed on import, independent of
`.env.default`'s REDIS_NODES). Since this backend runs as a separate
service, merely importing that module would try (and likely fail) to reach
that hardcoded cluster. Instead this uses aiserver_utils.pf_redis.pf_redis_client
(the same env-driven client aiserver_utils.queue itself uses) and
reimplements the handful of pure helper functions (session id, timestamp,
params template) that module also defines.
"""

import json
import threading
import uuid
from datetime import datetime

from aiserver_utils.pf_redis import pf_redis_client
from aiserver_utils.queue import pf_job_fetcher_config, get_job_info
from aiserver_utils.s3 import S3Access, S3AccessV2

_s3 = S3Access()
_s3v2 = S3AccessV2()


def _download_bytes(file_key, task_id, timeout=15):
    """Download raw bytes for file_key via S3AccessV2.

    S3Access.download_from_s3() is image-only: s3_utils.py's download_fileobj()
    always pipes the object through read_image() (PIL Image.open), which
    silently returns None for anything that isn't a decodable image -- e.g.
    our JSON result file. S3AccessV2.download_buffer() uses
    download_file_binary() instead, which returns raw bytes untouched.
    """
    done = threading.Event()
    outcome = {}

    def _callback(result):
        outcome.update(result)
        done.set()

    _s3v2.download_buffer(_callback, file_key, task_id)
    if not done.wait(timeout=timeout):
        return None, "timed out downloading from S3"
    return outcome.get("img_buffer"), outcome.get("error")


def _get_session_id():
    now = datetime.utcnow()
    return now.strftime("%Y%m%d_%H_%M_%S_") + str(uuid.uuid4())


def _get_timestamp():
    now = datetime.utcnow()
    return float(now.strftime("%y") + now.strftime("%m%d%H%M%S.%f"))


def _photo_agent_params_template():
    return {
        "session_id": "guid",
        "tskId": "taskId",
        "srcKeys": ["demo/photoAgent/input/{msg_id}/src.jpg"],
        "dstKeys": ["demo/photoAgent/result/{msg_id}/result.png"],
        "effect": "photoAgent",
        "acts": [{"id": 0, "params": {}}],
    }


def submit_job(image_bytes, preset, image_quality, ai_photo_editing, user_prompt):
    """Upload the source image and enqueue a photoAgent job. Returns job_id."""
    params = _photo_agent_params_template()
    act_params = {
        "preset": preset,
        "image_quality": image_quality,
        "ai_photo_editing": ai_photo_editing,
    }
    if user_prompt:
        act_params["user_prompt"] = user_prompt
    params["acts"][0]["params"] = act_params

    job_id = _get_session_id()
    params["tskId"] = job_id
    params["session_id"] = job_id
    for key in ("srcKeys", "dstKeys"):
        params[key] = [v.replace("{msg_id}", job_id) for v in params[key]]

    _s3.upload_to_s3([image_bytes], params["srcKeys"])

    info_key = pf_job_fetcher_config.redisMsgInfoFormat.replace("{msg_id}", job_id)
    payload = dict(params)
    payload["srcKeys"] = json.dumps(params["srcKeys"])
    payload["dstKeys"] = json.dumps(params["dstKeys"])
    payload["acts"] = json.dumps(params["acts"])
    pf_redis_client.hset(info_key, mapping=payload)
    pf_redis_client.zadd(pf_job_fetcher_config.redisMsgQueue[0], {job_id: _get_timestamp()})

    return job_id


def get_status(job_id):
    """Returns {"status", "error"} or None if the job isn't known yet."""
    return get_job_info(job_id, keyToGet=["status", "error"])


def get_result(job_id):
    """Returns None if unknown, else {"status", "error", "image_url"?, "result"?}."""
    info = get_job_info(job_id, keyToGet=["status", "error", "0_res0"])
    if info is None:
        return None
    if info.get("status") != "success":
        return {"status": info.get("status"), "error": info.get("error")}

    result_keys = (info.get("0_res0") or {}).get("cusKeys") or []
    if len(result_keys) < 2:
        return {"status": "error", "error": "job succeeded but result keys are missing"}

    image_key, json_key = result_keys[0], result_keys[1]
    json_bytes, download_error = _download_bytes(json_key, job_id)
    if download_error or json_bytes is None:
        return {"status": "error", "error": f"failed to download result metadata: {download_error}"}

    result_text = json.loads(json_bytes)
    image_url = _s3v2.get_url_for_file_key(image_key)

    return {"status": "success", "image_url": image_url, "result": result_text}
