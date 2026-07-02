#!/usr/bin/env python3
"""
demo_server/app.py

QA batch inference service:
  upload folder of images → ComfyUI aiSmile LoRA workflow → download result zip

Usage:
  /workspace/miniconda3/envs/qwen/bin/python demo_server/app.py
"""

import asyncio
import copy
import io
import json
import mimetypes
import random
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
from aiohttp import web
from PIL import Image

# ── Config ─────────────────────────────────────────────────────────────────────
COMFYUI_URL   = "http://127.0.0.1:8188"
SERVICE_PORT  = 7788
REPO_ROOT     = Path(__file__).parent.parent
WORKFLOW_PATH = REPO_ROOT / "user/default/workflows/aiSmile_workflow_api_20260630.json"
INPUTS_DIR    = Path(__file__).parent / "inputs"
RESULTS_DIR   = Path(__file__).parent / "results"
STATIC_DIR    = Path(__file__).parent / "static"
ALLOWED_EXT   = {".jpg", ".jpeg", ".png"}
UPLOAD_LIMIT  = 500 * 1024 * 1024   # 500 MB
SEED_MAX      = 2**32 - 1
JPEG_QUALITY  = 95
TZ_UTC8       = timezone(timedelta(hours=8))

# ── In-memory state ────────────────────────────────────────────────────────────
batches: dict[str, dict] = {}
#  batch = { total, finished, zip_ready, errors: [] }


# ── ComfyUI helpers ────────────────────────────────────────────────────────────
async def upload_image_to_comfy(session: aiohttp.ClientSession, img_path: Path) -> str:
    mime = mimetypes.guess_type(img_path.name)[0] or "image/jpeg"
    with open(img_path, "rb") as f:
        form = aiohttp.FormData()
        form.add_field("image", f, filename=img_path.name, content_type=mime)
        async with session.post(f"{COMFYUI_URL}/upload/image", data=form) as resp:
            resp.raise_for_status()
            result = await resp.json()
            return result["name"]


def patch_workflow(template: dict, comfy_filename: str, batch_id: str, stem: str) -> dict:
    wf = copy.deepcopy(template)
    # node 1: LoadImage – point to the uploaded file
    wf["1"]["inputs"]["image"] = comfy_filename
    # node 13: KSampler – randomize seed so repeated batches don't reproduce identical results
    wf["13"]["inputs"]["seed"] = random.randint(0, SEED_MAX)
    # node 16: replace SaveImageWebsocket → SaveImage (writes to disk, no websocket needed)
    wf["16"] = {
        "class_type": "SaveImage",
        "_meta": {"title": "Save Image"},
        "inputs": {
            "images": ["33", 0],
            "filename_prefix": f"{batch_id}/{stem}",
        },
    }
    return wf


async def submit_prompt(session: aiohttp.ClientSession, workflow: dict) -> str:
    async with session.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow}) as resp:
        resp.raise_for_status()
        result = await resp.json()
        return result["prompt_id"]


async def poll_until_done(
    session: aiohttp.ClientSession, prompt_id: str, timeout: float = 600
) -> dict:
    elapsed = 0.0
    while elapsed < timeout:
        async with session.get(f"{COMFYUI_URL}/history/{prompt_id}") as resp:
            history = await resp.json()
        if prompt_id in history:
            return history[prompt_id].get("outputs", {})
        await asyncio.sleep(2.0)
        elapsed += 2.0
    raise TimeoutError(f"prompt {prompt_id} not done after {timeout}s")


async def fetch_output_image(session: aiohttp.ClientSession, img_info: dict) -> bytes:
    params = {
        "filename": img_info["filename"],
        "subfolder": img_info["subfolder"],
        "type": img_info["type"],
    }
    async with session.get(f"{COMFYUI_URL}/view", params=params) as resp:
        resp.raise_for_status()
        return await resp.read()


def png_to_jpeg(raw: bytes) -> bytes:
    with Image.open(io.BytesIO(raw)) as im:
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
        return buf.getvalue()


# ── Batch processor (runs in background) ──────────────────────────────────────
async def process_batch(batch_id: str, img_paths: list[Path]) -> None:
    batch = batches[batch_id]
    template = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    result_dir = RESULTS_DIR / batch_id
    result_dir.mkdir(parents=True, exist_ok=True)

    async with aiohttp.ClientSession() as session:
        for img_path in img_paths:
            try:
                comfy_name = await upload_image_to_comfy(session, img_path)
                wf = patch_workflow(template, comfy_name, batch_id, img_path.stem)
                prompt_id = await submit_prompt(session, wf)
                outputs = await poll_until_done(session, prompt_id)

                for img_info in outputs.get("16", {}).get("images", []):
                    raw = await fetch_output_image(session, img_info)
                    out_name = Path(img_info["filename"]).with_suffix(".jpg").name
                    (result_dir / out_name).write_bytes(png_to_jpeg(raw))

            except Exception as exc:
                batch["errors"].append(f"{img_path.name}: {exc}")

            batch["finished"] += 1

    # Pack result zip
    zip_path = RESULTS_DIR / f"{batch_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(result_dir.iterdir()):
            zf.write(f, f.name)
    batch["zip_ready"] = True


# ── HTTP handlers ──────────────────────────────────────────────────────────────
async def handle_index(request: web.Request) -> web.Response:
    return web.FileResponse(STATIC_DIR / "index.html")


async def handle_upload(request: web.Request) -> web.Response:
    reader = await request.multipart()
    timestamp = datetime.now(TZ_UTC8).strftime("%Y%m%d_%H%M%S")
    batch_id = f"{timestamp}_{uuid.uuid4().hex[:12]}"
    input_dir = INPUTS_DIR / batch_id
    input_dir.mkdir(parents=True, exist_ok=True)
    img_paths: list[Path] = []

    async for field in reader:
        if field.name != "files":
            continue
        suffix = Path(field.filename).suffix.lower()
        if suffix not in ALLOWED_EXT:
            continue
        safe_name = Path(field.filename).name   # strip any dir component
        dest = input_dir / safe_name
        dest.write_bytes(await field.read())
        img_paths.append(dest)

    if not img_paths:
        return web.json_response(
            {"error": "No valid images found (.jpg / .jpeg / .png)"}, status=400
        )

    batches[batch_id] = {
        "total":    len(img_paths),
        "finished": 0,
        "zip_ready": False,
        "errors":   [],
    }
    asyncio.create_task(process_batch(batch_id, img_paths))
    return web.json_response({"batch_id": batch_id, "total": len(img_paths)})


async def handle_status(request: web.Request) -> web.Response:
    batch_id = request.match_info["batch_id"]
    batch = batches.get(batch_id)
    if batch is None:
        return web.json_response({"error": "unknown batch_id"}, status=404)
    return web.json_response({
        "batch_id":  batch_id,
        "total":     batch["total"],
        "finished":  batch["finished"],
        "zip_ready": batch["zip_ready"],
        "errors":    batch["errors"],
    })


async def handle_download(request: web.Request) -> web.Response:
    batch_id = request.match_info["batch_id"]
    batch = batches.get(batch_id)
    if batch is None:
        return web.json_response({"error": "unknown batch_id"}, status=404)
    if not batch["zip_ready"]:
        return web.json_response({"error": "zip not ready yet"}, status=202)
    zip_path = RESULTS_DIR / f"{batch_id}.zip"
    return web.FileResponse(
        zip_path,
        headers={"Content-Disposition": f'attachment; filename="result_{batch_id}.zip"'},
    )


# ── App factory ────────────────────────────────────────────────────────────────
def create_app() -> web.Application:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    app = web.Application(client_max_size=UPLOAD_LIMIT)
    app.router.add_get("/", handle_index)
    app.router.add_post("/upload", handle_upload)
    app.router.add_get("/status/{batch_id}", handle_status)
    app.router.add_get("/download/{batch_id}", handle_download)
    app.router.add_static("/static", STATIC_DIR)
    return app


if __name__ == "__main__":
    print(f"demo_server  →  http://0.0.0.0:{SERVICE_PORT}")
    print(f"ComfyUI      →  {COMFYUI_URL}")
    print(f"Workflow     →  {WORKFLOW_PATH}")
    web.run_app(create_app(), host="0.0.0.0", port=SERVICE_PORT)
