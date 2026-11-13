"""Local UI preview: answer the panel's analysis calls with sample data.

Only active when PHOTOAGENT_UI_MOCK=1 (set by a developer's local start script, never in a
deployment). Without Redis/S3/models a local analysis always fails, so the panel's workspace
(Photo Overview, Analysis & Adjustments, version strip, Retouch) can't be seen or laid out.
With this on, /analyze and /analyze-batch keep the uploaded photos in a temp folder and hand
back "mock-" job ids; /status, /analyze/result and /source/<id>/image|thumbnail answer for
those ids, so the panel's own code renders the real workspace from a sample analysis. Every
other request goes to the real routes untouched.
"""

import copy
import os
import re
import tempfile
import uuid

from flask import jsonify, request, send_file

ENV_FLAG = "PHOTOAGENT_UI_MOCK"
_STORE = os.path.join(tempfile.gettempdir(), "photoagent-ui-mock")
_PREFIX = "/api/photo-agent"
_ID = re.compile(r"(mock-[0-9a-f]{12})")

SAMPLE_ANALYSIS = {
    "photo_summary": "一位人物在自然光下的照片，背景柔和，整體清新自然。",
    "subject_category": "人物",
    "scene_category": "戶外",
    "main_subject": "看向鏡頭的人物",
    "scene_type": "戶外人像",
    "composition_mood": "清新、自然、溫暖",
    "aesthetic_diagnosis": "主體清楚，光線柔和；臉部稍暗，背景有些許雜物分散注意力。",
    "detected_issues": [
        "臉部受光不足，膚色略顯暗沉。",
        "背景右側有雜物，分散對主體的注意力。",
        "畫面下方留白偏多，主體不夠集中。",
    ],
    "retouch_recipe": [
        "重新構圖：裁成 4:5，讓人物在畫面中更集中",
        "提亮臉部與中間調，保留自然膚色",
        "柔化背景雜物，讓主體更突出",
    ],
    "correction_plan": [
        "Reframe to 4:5 keeping the subject centered",
        "Lift face exposure and midtones",
        "Soften distracting background clutter",
    ],
    "recommended_aspect_ratio": "4:5",
    "aspect_ratio_reason": "直式人像，4:5 能讓主體更集中。",
    "subjects": [
        {
            "is_person": True,
            "label": "畫面中的人物",
            # Where the person is, as fractions of the image (the panel's 人像 tab crops the
            # avatar from the upper part of this box). A rough centre-frame guess for any photo.
            "box": {"x": 0.25, "y": 0.08, "w": 0.5, "h": 0.8},
            "blink_issue": {"should_fix": False, "level": "none"},
            "gaze_issue": {"should_fix": True, "level": "moderate"},
            "hair_issue": {"should_fix": False, "level": "none"},
            "smile_issue": {"adjust_safe": False, "smile_intensity": "broad"},
        }
    ],
    "user_instruction": {"text": "", "overwrite_prompt": False},
}


SAMPLE_ANALYSIS_EN = {
    "photo_summary": "A person photographed in natural light with a soft background; the overall look is fresh and natural.",
    "subject_category": "People",
    "scene_category": "Outdoor",
    "main_subject": "A person looking at the camera",
    "scene_type": "Outdoor portrait",
    "composition_mood": "Fresh, natural, warm",
    "aesthetic_diagnosis": "Clear subject and soft light; the face is a little dark and background clutter distracts.",
    "detected_issues": [
        "The face is underexposed and skin looks slightly dull.",
        "Clutter on the right side of the background distracts from the subject.",
        "Too much empty space at the bottom; the subject is not focused enough.",
    ],
    "retouch_recipe": [
        "Reframe: crop to 4:5 to keep the person more centered",
        "Brighten the face and midtones while keeping natural skin tone",
        "Soften background clutter so the subject stands out",
    ],
}


def _sample():
    """The sample in the page's language (X-PhotoAgent-Lang): Chinese stays the source."""
    lang = (request.headers.get("X-PhotoAgent-Lang") or "").lower()
    data = copy.deepcopy(SAMPLE_ANALYSIS)
    if lang and not lang.startswith("zh"):
        data.update(copy.deepcopy(SAMPLE_ANALYSIS_EN))
        data["aspect_ratio_reason"] = "Portrait orientation: 4:5 keeps the subject more focused."
        data["subjects"][0]["label"] = "Person in the photo"
    return data


def _save(file_storage):
    os.makedirs(_STORE, exist_ok=True)
    job_id = f"mock-{uuid.uuid4().hex[:12]}"
    file_storage.save(os.path.join(_STORE, job_id))
    return job_id


def _path(job_id):
    path = os.path.join(_STORE, job_id)
    return path if os.path.isfile(path) else None


def init_ui_mock(app):
    if os.getenv(ENV_FLAG, "") != "1":
        return
    app.logger.warning("%s=1: panel analysis calls answer with sample data (local UI preview)", ENV_FLAG)

    @app.before_request
    def _ui_mock():
        path = request.path
        if not path.startswith(_PREFIX):
            return None
        rest = path[len(_PREFIX):]

        if request.method == "POST" and rest == "/analyze-batch":
            return jsonify({"jobs": [{"job_id": _save(f)} for f in request.files.getlist("image")]})
        if request.method == "POST" and rest == "/analyze":
            image = request.files.get("image")
            return jsonify({"job_id": _save(image) if image else f"mock-{uuid.uuid4().hex[:12]}"})

        match = _ID.search(rest)
        if not match:
            return None
        job_id = match.group(1)
        if rest.startswith("/status/"):
            return jsonify({"status": "success"})
        if rest.startswith("/analyze/result/"):
            return jsonify({"status": "success", "result": {
                "analysis": _sample(), "compatible": True}})
        if rest.startswith("/source/"):
            file_path = _path(job_id)
            return send_file(file_path) if file_path else ("", 404)
        # Anything else about a mock job (history rows, case lookups…): an empty success.
        return jsonify({})
