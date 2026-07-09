"""Sync client for the AIPoC remote API (aipoc-dev.perfectcorp.com).

Mirrors MTVideoAI/backend/services/aipoc_client.py's request/response
plumbing (refresh_access_token / get_req_url / upload_to_req_url /
get_status_by_id are copied verbatim -- they're effect-agnostic). Adds
EFFECT_PHOTO_AGENT so PhotoAgent's pipeline can be requested from the same
shared inference gateway instead of calling OpenAI directly.

run_photo_agent_task()'s payload shape was modeled on run_obj_detect_task()
(the closest actually-used single-image, no-mask effect in MTVideoAI's
client) using the same acts[0].params keys PhotoAgent's own local worker
validates (photo_agent/param_validation.py: preset/image_quality/
ai_photo_editing/user_prompt). Confirmed against the real AIPoC service on
2026-07-03: the photoAgent effect accepts this payload and returns the exact
same output convention as the local worker -- result.results[0].data[0]
has cusKeys [imgKey, jsonKey] and matching presigned cusUrls, and the JSON
at cusUrls[1] is the same {analysis, quality_check, usage} shape
pipeline.py produces locally. See aipoc_result() in routes/api.py for the
result parsing.

AIPOC_ACCESS_TOKEN / AIPOC_REFRESH_TOKEN env vars override the defaults
below if set.
"""

import os
import requests

API_BASE = "https://aipoc-dev.perfectcorp.com/api/v1.0"

OS_NAME = "linux"
APP_NAME = "poc"
APP_VER = "local_test"
API_VER = "1.0"
UID = "test1"

EFFECT_PHOTO_AGENT = "photoAgent"

_DEFAULT_ACCESS_TOKEN  = "AAUGazjXAAAAJDZjZDk4NDc4LTZjYzMtNGRjYS1hNDViLTRiODZmOWZjYzFhNwAAACQ2Y2Q5ODQ3OC02Y2MzLTRkY2EtYTQ1Yi00Yjg2ZjlmY2MxYTcAAAAAAAAADkFMQkVSVEtDX0NIQU5H.uITRehw62aSOpAMmHhqToDsesU8="
_DEFAULT_REFRESH_TOKEN = "AAUDVgw7LFXJVAAAACQ2Y2Q5ODQ3OC02Y2MzLTRkY2EtYTQ1Yi00Yjg2ZjlmY2MxYTcAAAAkNmNkOTg0NzgtNmNjMy00ZGNhLWE0NWItNGI4NmY5ZmNjMWE3BmwZ1wAAAAAAAAAOQUxCRVJUS0NfQ0hBTkc=.7WuLsIXknhppDcYJswF9WRKMuWs="

def get_default_tokens():
    return (
        os.getenv("AIPOC_ACCESS_TOKEN", _DEFAULT_ACCESS_TOKEN),
        os.getenv("AIPOC_REFRESH_TOKEN", _DEFAULT_REFRESH_TOKEN),
    )

def _qs(extra=""):
    base = f"os={OS_NAME}&app={APP_NAME}&appVer={APP_VER}&apiVer={API_VER}&uid={UID}&isDev=1"
    return f"?{base}{('&' + extra) if extra else ''}"


def refresh_access_token(refresh_token):
    """POST /user/refresh.action -- returns (new_access_token, new_refresh_token)."""
    url = f"{API_BASE}/user/refresh.action{_qs()}"
    r = requests.post(url, json={"refreshToken": refresh_token},
                       headers={"Content-Type": "application/json"}, timeout=30)
    body = r.json()
    if body.get("status") != 200:
        raise RuntimeError(f"refresh_access_token failed: {body.get('error')} ({body.get('errorCode')})")
    result = body["result"]
    return result["accessToken"], result["refreshToken"]


def get_req_url(access_token, files, effect=None):
    """POST /file/get-req-url.action -- request presigned upload URLs."""
    url = f"{API_BASE}/file/get-req-url.action{_qs()}"
    payload = {"urlType": "Upload", "files": files, "accessToken": access_token}
    if effect:
        payload["effect"] = effect
    r = requests.post(url, json=payload,
                       headers={"Content-Type": "application/json"}, timeout=30)
    body = r.json()
    if body.get("status") != 200:
        raise RuntimeError(f"get_req_url failed: {body.get('error')} ({body.get('errorCode')})")
    return body


def upload_to_req_url(request_spec, data):
    """PUT raw bytes to the upload URL returned by get_req_url."""
    url = request_spec["url"]
    method = request_spec.get("method", "PUT")
    headers = request_spec.get("headers", {}) or {}
    r = requests.request(method, url, headers=headers, data=data, timeout=600)
    if r.status_code >= 300:
        raise RuntimeError(f"upload_to_req_url failed: HTTP {r.status_code} {r.text[:300]}")
    return r


def run_photo_agent_task(access_token, src_files, preset="natural", image_quality="medium",
                          image_model="gpt-image-2", ai_photo_editing=True, user_prompt=None,
                          stage=None, analysis=None, req_id=1):
    """POST /task/run.action with the photoAgent payload shape.

    UNVERIFIED against the real AIPoC service -- see module docstring.

    Args:
        access_token: bearer token
        src_files: list with the single uploaded fileId (photoAgent takes one
            source image, no mask), e.g. [file_id]
        preset/image_quality/image_model/ai_photo_editing/user_prompt: same
            fields as photo_agent/param_validation.py's local validator
        stage/analysis: mirrors the local worker's /continue/<job_id> shape
            (photo_agent/param_validation.py's "stage"/"analysis" params) --
            when the caller already has an analysis from /aipoc/analyze, pass
            stage="finish" + that analysis so the remote worker's
            job_processor.py (assuming it's running the same pipeline.py --
            see aipoc_client module docstring) skips re-analyzing from
            scratch and reuses it for steps 3-6. UNCONFIRMED whether the
            remote gateway's orchestration layer forwards these through to
            job_processor.py unmodified -- if it silently drops or rejects
            them, this falls back to today's from-scratch full-pipeline
            behavior (or errors out, which is easy to spot).

    Returns the parsed JSON body -- caller reads body["result"]["taskId"].
    """
    url = f"{API_BASE}/task/run.action{_qs()}"
    params = {
        "preset": preset,
        "image_quality": image_quality,
        "image_model": image_model,
        "ai_photo_editing": ai_photo_editing,
    }
    if user_prompt:
        params["user_prompt"] = user_prompt
    if stage:
        params["stage"] = stage
    if analysis is not None:
        params["analysis"] = analysis
    payload = {
        "payload": {
            "acts": [{"id": 0, "params": params}],
            "custom": {"dstCount": 1},
            "effect": EFFECT_PHOTO_AGENT,
            "fileSets": {"srcIds": src_files},
        },
        "reqId": req_id,
        "accessToken": access_token,
    }
    r = requests.post(url, json=payload,
                       headers={"Content-Type": "application/json"}, timeout=60)
    body = r.json()
    if body.get("status") != 200:
        raise RuntimeError(f"run_photo_agent_task failed: {body.get('error')} ({body.get('errorCode')})")
    return body


def get_status_by_id(access_token, task_id, effect=EFFECT_PHOTO_AGENT):
    """POST /task/get-status-by-id-token.action -- returns parsed JSON."""
    url = f"{API_BASE}/task/get-status-by-id-token.action{_qs()}"
    payload = {"taskId": task_id, "effect": effect}
    r = requests.post(url, json=payload,
                       headers={"Content-Type": "application/json"}, timeout=30)
    return r.json()
