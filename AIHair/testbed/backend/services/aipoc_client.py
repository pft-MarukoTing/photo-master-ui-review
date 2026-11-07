"""Sync client for the AIPoC remote API (aipoc-dev.perfectcorp.com).

Mirrors the async helpers in helper_speechenh.py but uses `requests` so it can
be called directly from Flask sync route handlers. Wraps the four-step pipeline:

    refresh_access_token -> get_req_url -> upload_to_req_url -> run_task
                                                              -> get_status_by_id
"""

import os
import requests

API_BASE = "https://aipoc-dev.perfectcorp.com/api/v1.0"

# Static query params shared across all calls. Match helper_speechenh.py.
OS_NAME  = "linux"
APP_NAME = "poc"
APP_VER  = "local_test"
API_VER  = "1.0"
UID      = "test1"

# Default credentials — pulled from env if set, otherwise fall back to the
# encrypted apikeys baked into helper_speechenh.py.
_DEFAULT_ACCESS_TOKEN  = "AAUGazjXAAAAJDZjZDk4NDc4LTZjYzMtNGRjYS1hNDViLTRiODZmOWZjYzFhNwAAACQ2Y2Q5ODQ3OC02Y2MzLTRkY2EtYTQ1Yi00Yjg2ZjlmY2MxYTcAAAAAAAAADkFMQkVSVEtDX0NIQU5H.uITRehw62aSOpAMmHhqToDsesU8="
_DEFAULT_REFRESH_TOKEN = "AAUDVgw7LFXJVAAAACQ2Y2Q5ODQ3OC02Y2MzLTRkY2EtYTQ1Yi00Yjg2ZjlmY2MxYTcAAAAkNmNkOTg0NzgtNmNjMy00ZGNhLWE0NWItNGI4NmY5ZmNjMWE3BmwZ1wAAAAAAAAAOQUxCRVJUS0NfQ0hBTkc=.7WuLsIXknhppDcYJswF9WRKMuWs="

EFFECT_AI_HAIR = "aiHair"

# aiHair's nine features, spelled as the engine's ai_hair/features.py
# FEATURE_NAMES spells them (the engine matches them ignoring case).
AI_HAIR_FEATURES = ("frizzCare", "plump", "fluffy", "hairline", "soft", "hairpart",
                    "grayHair", "rootRetouch", "strayHair")
# The features that take a `level`, and the levels each one takes.
AI_HAIR_LEVELLED = {"hairline": (1, 2)}
# Result formats the engine can save (the dst key's extension).
AI_HAIR_OUTPUT_EXTS = ("jpg", "png", "webp")


def get_default_tokens():
    return (
        os.getenv("AIPOC_ACCESS_TOKEN",  _DEFAULT_ACCESS_TOKEN),
        os.getenv("AIPOC_REFRESH_TOKEN", _DEFAULT_REFRESH_TOKEN),
    )


def _qs(extra=""):
    base = f"os={OS_NAME}&app={APP_NAME}&appVer={APP_VER}&apiVer={API_VER}&uid={UID}&isDev=1"
    return f"?{base}{('&' + extra) if extra else ''}"


def refresh_access_token(refresh_token):
    """POST /user/refresh.action — returns (new_access_token, new_refresh_token)."""
    url = f"{API_BASE}/user/refresh.action{_qs()}"
    r = requests.post(url, json={"refreshToken": refresh_token},
                      headers={"Content-Type": "application/json"}, timeout=30)
    body = r.json()
    if body.get("status") != 200:
        raise RuntimeError(f"refresh_access_token failed: {body.get('error')} ({body.get('errorCode')})")
    result = body["result"]
    return result["accessToken"], result["refreshToken"]


def get_req_url(access_token, files, effect=None):
    """POST /file/get-req-url.action — request presigned upload URLs.

    Args:
        access_token: bearer token
        files: list of dicts, e.g. [{"contentType": "image/jpeg", "fileName": "photo.jpg"}, ...]
        effect: optional effect tag (e.g. "aiHair")

    Returns the parsed JSON body. Each entry in `body["results"]` has at least
    `request[0]` (an upload spec with url/method/headers) and a key identifier.
    """
    url = f"{API_BASE}/file/get-req-url.action{_qs()}"
    payload = {
        "urlType": "Upload",
        "files": files,
        "accessToken": access_token,
    }
    if effect:
        payload["effect"] = effect
    r = requests.post(url, json=payload,
                      headers={"Content-Type": "application/json"}, timeout=30)
    body = r.json()
    if body.get("status") != 200:
        raise RuntimeError(f"get_req_url failed: {body.get('error')} ({body.get('errorCode')})")
    return body


def upload_to_req_url(request_spec, data):
    """PUT raw bytes to the upload URL returned by get_req_url.

    Args:
        request_spec: one element of `results[i].request` from get_req_url —
                      shape: {url, method?, headers?}
        data: bytes (or file-like) to upload
    """
    url     = request_spec["url"]
    method  = request_spec.get("method", "PUT")
    headers = request_spec.get("headers", {}) or {}
    r = requests.request(method, url, headers=headers, data=data, timeout=600)
    if r.status_code >= 300:
        raise RuntimeError(f"upload_to_req_url failed: HTTP {r.status_code} {r.text[:300]}")
    return r


def run_ai_hair_task(access_token, src_ids, feature, level=None,
                     output_ext="jpg", req_id=1):
    """POST /task/run.action with the aiHair payload shape.

    Args:
        access_token: bearer token
        src_ids: [fileId] of the one photo, from get_req_url + upload_to_req_url
        feature: one of AI_HAIR_FEATURES
        level: hairline only (1 or 2); ignored for the other features
        output_ext: the result image's format (jpg / png / webp)

    Returns the parsed JSON body — caller reads `body["result"]["taskId"]`.
    """
    params = {"feature": feature}
    if feature in AI_HAIR_LEVELLED and level is not None:
        params["level"] = level
    url = f"{API_BASE}/task/run.action{_qs()}"
    payload = {
        "payload": {
            "acts": [{
                "id": 0,
                "params": params,
            }],
            "effect":    EFFECT_AI_HAIR,
            "outputExt": output_ext,
            "custom": {"dstCount": 1},
            "fileSets": {"srcIds": src_ids},
            "files": {},
        },
        "reqId":       req_id,
        "accessToken": access_token,
    }
    r = requests.post(url, json=payload,
                      headers={"Content-Type": "application/json"}, timeout=60)
    body = r.json()
    if body.get("status") != 200:
        raise RuntimeError(f"run_ai_hair_task failed: {body.get('error')} ({body.get('errorCode')})")
    return body


def get_status_by_id(access_token, task_id, effect=EFFECT_AI_HAIR):
    """POST /task/get-status-by-id-token.action — returns parsed JSON."""
    url = f"{API_BASE}/task/get-status-by-id-token.action{_qs()}"
    payload = {"taskId": task_id, "effect": effect}
    r = requests.post(url, json=payload,
                      headers={"Content-Type": "application/json"}, timeout=30)
    return r.json()
