"""Generic text-to-image generation via Gemini, fronted by the internal
aipoc gateway (its gmnImgEdit effect is normally used for image *editing*, but
leaving fileSets empty -- no source image uploaded -- makes it a pure
generation call). See MTImageAI/gmn_imgedit_helper.py for the same gateway
used for editing.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os

import aiohttp

BASE_URL = "https://aipoc-dev.perfectcorp.com"
APP_NAME = "poc"
OS_NAME = "linux"
APP_VER = "local_test"
API_VER = "1.0"
UID = "test1"
EFFECT = "gmnImgEdit"

DEFAULT_MODEL = "gemini-3.1-flash-image-preview"

# The aipoc gateway's gmnImgEdit effect only ever returns an image or an
# error; asking the (image-generation) model to answer in text makes it
# report the missing image as this specific error, with the model's text
# answer embedded in the message after this prefix. Same trick used by
# AIModelJudge's aimj/judges/aipoc.py.
TEXT_ONLY_PREFIX = "Gemini responsed with text only:"


def _action_url(action: str) -> str:
    return (
        f"{BASE_URL}/api/v1.0/{action}"
        f"?os={OS_NAME}&app={APP_NAME}&appVer={APP_VER}&apiVer={API_VER}&uid={UID}&isDev=1"
    )


async def _refresh_access_token(session: aiohttp.ClientSession, refresh_token: str) -> str:
    async with session.post(_action_url("user/refresh.action"), json={"refreshToken": refresh_token}) as resp:
        body = await resp.json()
    if body.get("status") != 200:
        raise RuntimeError(f"aipoc refresh failed: {json.dumps(body, ensure_ascii=False)[:300]}")
    return body["result"]["accessToken"]


async def _upload_image(session: aiohttp.ClientSession, image_path: str, access_token: str) -> str:
    content_type = mimetypes.guess_type(image_path)[0] or "application/octet-stream"
    async with session.post(
        _action_url("file/get-req-url.action"),
        json={
            "urlType": "Upload",
            "files": [{"contentType": content_type, "fileName": os.path.basename(image_path)}],
            "accessToken": access_token,
            "effect": EFFECT,
        },
    ) as resp:
        req_res = await resp.json()
    if req_res.get("status") != 200:
        raise RuntimeError(f"get-req-url failed: {json.dumps(req_res, ensure_ascii=False)[:300]}")

    entry = req_res["results"][0]
    upload = entry["request"][0]
    with open(image_path, "rb") as f:
        data = f.read()
    async with session.request(
        upload.get("method", "PUT"), upload["url"], headers=upload.get("headers") or {}, data=data
    ) as resp:
        if resp.status != 200:
            raise RuntimeError(f"upload of {image_path} returned HTTP {resp.status}")
    return entry["fileId"]


async def _run_and_poll(session: aiohttp.ClientSession, run_payload: dict, out_path: str) -> str:
    async with session.post(_action_url("task/run.action"), json=run_payload) as resp:
        run_result = await resp.json()
    if run_result.get("status") != 200:
        raise RuntimeError(f"task/run failed: {json.dumps(run_result, ensure_ascii=False)[:500]}")
    task_id = run_result["result"]["taskId"]

    while True:
        async with session.post(
            _action_url("task/get-status-by-id-token.action"),
            json={"taskId": task_id, "effect": EFFECT},
        ) as resp:
            status_result = await resp.json()
        result = status_result.get("result") or {}
        status = result.get("status")
        if status == "success":
            result_url = result["results"][0]["data"][0]["url"]
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            async with session.get(result_url) as img_resp:
                with open(out_path, "wb") as f:
                    f.write(await img_resp.read())
            return out_path
        if status == "error":
            raise RuntimeError(f"gmnImgEdit task failed: {result.get('error') or result}")
        await asyncio.sleep((result.get("nextTimeOut") or 1000) / 1000)


async def generate_image(
    prompt: str,
    out_path: str,
    model: str = DEFAULT_MODEL,
    refresh_token: str | None = None,
) -> str:
    """Generate one image from a text prompt and save it to out_path.

    refresh_token defaults to the AIPOC_REFRESH_TOKEN environment variable.
    """
    refresh_token = refresh_token or os.getenv("AIPOC_REFRESH_TOKEN", "").strip()
    if not refresh_token:
        raise RuntimeError("AIPOC_REFRESH_TOKEN is not set")

    async with aiohttp.ClientSession() as session:
        access_token = await _refresh_access_token(session, refresh_token)
        run_payload = {
            "payload": {
                "acts": [{"id": 0, "params": {"prompt": prompt, "model": model}}],
                "custom": {"dstCount": 1},
                "effect": EFFECT,
                "fileSets": {},  # no source image -> pure text-to-image
                "files": {},
                "outputExt": "png",
                "msgVer": 100,
            },
            "reqId": 0,
            "accessToken": access_token,
        }
        return await _run_and_poll(session, run_payload, out_path)


async def judge_images(
    image_paths: list[str],
    prompt: str,
    model: str = DEFAULT_MODEL,
    refresh_token: str | None = None,
) -> str:
    """Send several images plus a text prompt and get back a text answer.

    Images are uploaded in order and referred to positionally in the prompt
    (e.g. "IMAGE 1 = ..."), since the gateway has no per-image labels.
    refresh_token defaults to the AIPOC_REFRESH_TOKEN environment variable.
    """
    refresh_token = refresh_token or os.getenv("AIPOC_REFRESH_TOKEN", "").strip()
    if not refresh_token:
        raise RuntimeError("AIPOC_REFRESH_TOKEN is not set")

    async with aiohttp.ClientSession() as session:
        access_token = await _refresh_access_token(session, refresh_token)
        file_ids = [await _upload_image(session, p, access_token) for p in image_paths]
        run_payload = {
            "payload": {
                "acts": [{"id": 0, "params": {"prompt": prompt, "model": model}}],
                "custom": {"dstCount": 1},
                "effect": EFFECT,
                "fileSets": {"srcIds": file_ids},
                "files": {},
                "outputExt": "png",
                "msgVer": 100,
            },
            "reqId": 0,
            "accessToken": access_token,
        }
        async with session.post(_action_url("task/run.action"), json=run_payload) as resp:
            run_result = await resp.json()
        if run_result.get("status") != 200:
            raise RuntimeError(f"task/run failed: {json.dumps(run_result, ensure_ascii=False)[:500]}")
        task_id = run_result["result"]["taskId"]

        while True:
            async with session.post(
                _action_url("task/get-status-by-id-token.action"),
                json={"taskId": task_id, "effect": EFFECT},
            ) as resp:
                status_result = await resp.json()
            result = status_result.get("result") or {}
            status = result.get("status")
            if status == "error":
                code = str(result.get("errorCode") or result.get("error") or "")
                message = str(result.get("errorMessage") or "")
                if code == "error_no_img_result" and TEXT_ONLY_PREFIX in message:
                    return message.split(TEXT_ONLY_PREFIX, 1)[1].strip()
                raise RuntimeError(f"gmnImgEdit task failed: {code}: {message}"[:500])
            if status == "success":
                raise RuntimeError(
                    "gateway returned an image instead of text -- the prompt did not make it "
                    "answer in text only"
                )
            await asyncio.sleep((result.get("nextTimeOut") or 1000) / 1000)


async def edit_image(
    image_path: str,
    prompt: str,
    out_path: str,
    model: str = DEFAULT_MODEL,
    refresh_token: str | None = None,
) -> str:
    """Edit an existing image with a text prompt and save the result to out_path.

    refresh_token defaults to the AIPOC_REFRESH_TOKEN environment variable.
    """
    refresh_token = refresh_token or os.getenv("AIPOC_REFRESH_TOKEN", "").strip()
    if not refresh_token:
        raise RuntimeError("AIPOC_REFRESH_TOKEN is not set")

    async with aiohttp.ClientSession() as session:
        access_token = await _refresh_access_token(session, refresh_token)
        file_id = await _upload_image(session, image_path, access_token)
        run_payload = {
            "payload": {
                "acts": [{"id": 0, "params": {"prompt": prompt, "model": model}}],
                "custom": {"dstCount": 1},
                "effect": EFFECT,
                "fileSets": {"srcIds": [file_id]},
                "files": {},
                "outputExt": "png",
                "msgVer": 100,
            },
            "reqId": 0,
            "accessToken": access_token,
        }
        return await _run_and_poll(session, run_payload, out_path)
