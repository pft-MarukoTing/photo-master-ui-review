#!/usr/bin/env python3
"""
comfy_client.py -- minimal synchronous ComfyUI HTTP API client.

Used by run_benchmark.py to upload an input image, submit a ComfyUI "api
format" workflow JSON and retrieve the generated output image via the
upload/prompt/history/view sequence. Nodes are located by their _meta.title /
class_type instead of fixed node IDs so it keeps working after the workflow
is re-saved from the ComfyUI UI (which renumbers node IDs).
"""

import time
from pathlib import Path

import requests


def upload_image(base_url: str, session: requests.Session, img_path: Path) -> str:
    mime = "image/jpeg" if img_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    with open(img_path, "rb") as f:
        files = {"image": (img_path.name, f, mime)}
        resp = session.post(f"{base_url}/upload/image", files=files)
        resp.raise_for_status()
        return resp.json()["name"]


def find_node_by_title(workflow: dict, title: str) -> str:
    for node_id, node in workflow.items():
        if node.get("_meta", {}).get("title") == title:
            return node_id
    raise ValueError(
        f'workflow has no node titled "{title}" -- check the workflow json, '
        f"or pass a matching --*-title flag if it was renamed"
    )


def find_node_by_class(workflow: dict, class_type: str) -> str:
    for node_id, node in workflow.items():
        if node.get("class_type") == class_type:
            return node_id
    raise ValueError(f'workflow has no node of class_type "{class_type}"')


def submit_prompt(base_url: str, session: requests.Session, workflow: dict) -> str:
    resp = session.post(f"{base_url}/prompt", json={"prompt": workflow})
    if not resp.ok:
        raise RuntimeError(f"POST /prompt -> {resp.status_code}: {resp.text}")
    return resp.json()["prompt_id"]


def poll_until_done(
    base_url: str, session: requests.Session, prompt_id: str, timeout: float = 600, interval: float = 2.0
) -> dict:
    elapsed = 0.0
    while elapsed < timeout:
        resp = session.get(f"{base_url}/history/{prompt_id}")
        resp.raise_for_status()
        history = resp.json()
        if prompt_id in history:
            entry = history[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI prompt {prompt_id} failed: {status.get('messages')}")
            return entry.get("outputs", {})
        time.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"prompt {prompt_id} not done after {timeout}s")


def fetch_output_image(base_url: str, session: requests.Session, img_info: dict) -> bytes:
    params = {
        "filename": img_info["filename"],
        "subfolder": img_info["subfolder"],
        "type": img_info["type"],
    }
    resp = session.get(f"{base_url}/view", params=params)
    resp.raise_for_status()
    return resp.content
