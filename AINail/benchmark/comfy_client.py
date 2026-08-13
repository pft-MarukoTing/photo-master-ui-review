#!/usr/bin/env python3
"""
comfy_client.py -- minimal synchronous ComfyUI HTTP API client.

Reused by run_benchmark.py to submit a source+reference image pair through a
ComfyUI "api format" workflow JSON and retrieve the generated output image.
Mirrors the upload/prompt/history/view sequence used by
AISmile/demo_server/app.py, but patches nodes by their _meta.title / class_type
instead of fixed node IDs so it keeps working after the workflow is re-saved
from the ComfyUI UI (which renumbers node IDs).
"""

import copy
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


def patch_workflow(
    template: dict,
    *,
    source_title: str,
    reference_title: str,
    lora_title: str,
    source_filename: str,
    reference_filename: str,
    checkpoint: str,
    seed: int,
    filename_prefix: str,
) -> dict:
    """Deep-copies the template and patches inputs + output node for this run.

    Returns the patched workflow; the output node key can be recovered from
    the returned dict by looking for class_type == "SaveImage".
    """
    wf = copy.deepcopy(template)

    wf[find_node_by_title(wf, source_title)]["inputs"]["image"] = source_filename
    wf[find_node_by_title(wf, reference_title)]["inputs"]["image"] = reference_filename
    wf[find_node_by_title(wf, lora_title)]["inputs"]["lora_name"] = checkpoint

    sampler_id = find_node_by_class(wf, "KSampler")
    wf[sampler_id]["inputs"]["seed"] = seed

    # The workflow's "Patch Sage Attention KJ" node requires the optional
    # `sageattention` pip package; many ComfyUI installs (including the
    # benchmark target env) don't have it installed. "disabled" short-circuits
    # the node before it imports sageattention, at the cost of that perf
    # optimization -- fine for a correctness/regression benchmark.
    try:
        sage_id = find_node_by_class(wf, "PathchSageAttentionKJ")
        wf[sage_id]["inputs"]["sage_attention"] = "disabled"
    except ValueError:
        pass

    # Swap the websocket-only save node for a disk-writing SaveImage node so
    # the result can be fetched via /history + /view without holding a
    # websocket connection open (same trick as AISmile/demo_server/app.py).
    output_id = None
    for node_id, node in wf.items():
        if node["class_type"] in ("SaveImage", "SaveImageWebsocket"):
            output_id = node_id
            break
    if output_id is None:
        raise ValueError("workflow has no SaveImage/SaveImageWebsocket output node")
    wf[output_id] = {
        "class_type": "SaveImage",
        "_meta": {"title": "Save Image"},
        "inputs": {
            "images": wf[output_id]["inputs"]["images"],
            "filename_prefix": filename_prefix,
        },
    }
    return wf


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
