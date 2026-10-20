"""Snapshot of what every editor model sends to its provider today.

Guards the model-client refactor: each case runs a model's edit_photo /
edit_eye_repair_pass with the HTTP call mocked out and compares the request (prompt
text, size / imageConfig / auto_aspect_ratio) and the returned result (minus
image bytes) against tests/fixtures/editor_snapshots.json.

Regenerate the baseline only when a change is MEANT to alter what is sent:
    UPDATE_EDITOR_SNAPSHOTS=1 python3 -m unittest tests.test_editor_snapshots
"""

import base64
import copy
import hashlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import gemini_client
import image_tools
import mai_client
import openai_client
import pipeline
import prompts

SNAPSHOT_PATH = Path(__file__).parent / "fixtures" / "editor_snapshots.json"
UPDATE = os.getenv("UPDATE_EDITOR_SNAPSHOTS") == "1"


def _png(width, height):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 140, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


# One fixed raster comes back from every provider, so geometry warnings are
# deterministic (and non-empty for most requested ratios).
_RESULT_B64 = base64.b64encode(_png(1024, 1024)).decode()


def _analysis(ratio, crop_enabled, reframe_mode=None, prepared_for=None, source_size=None,
              wide_subject=False):
    analysis = {
        "subject_category": "portrait",
        "recommended_aspect_ratio": ratio,
        "crop_box": {
            "enabled": crop_enabled,
            "x": 0.05,
            "y": 0.05,
            "w": 0.9,
            "h": 0.9,
            "aspect_ratio": ratio,
        },
        "roi_box": {"x": 0.35, "y": 0.25, "w": 0.3, "h": 0.45},
        "subject_extent_box": {"x": 0.3, "y": 0.2, "w": 0.4, "h": 0.55},
        "protected_regions": [],
        "user_instruction": {},
        "correction_plan": ["brighten the subject slightly", "warm up the skin tone"],
        "retouch_recipe": ["主體稍微提亮", "膚色偏暖"],
        "correction_axes": ["brightness", "color"],
        "correction_inverse": ["keep exposure", "keep color"],
    }
    if wide_subject:
        # Too wide for a narrow crop, so expand mode is not overridden back
        # to a crop by crop_would_cut_a_subject.
        analysis["roi_box"] = analysis["subject_extent_box"] = {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}
    if reframe_mode:
        analysis["reframe_mode"] = reframe_mode
    if prepared_for:
        # What the analysis stage hands the editor for a user-named ratio,
        # including its reframe_extension plan in expand mode.
        analysis = image_tools.apply_derived_aspect_ratio(analysis, *source_size, prepared_for)
    return analysis


_GAZE_ITEMS = [
    {
        "subject_label": "person on the left",
        "box": {"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.4},
        "gaze_repair_strategy": {
            "edit_instruction": "Look at the lens.",
            "strategy_type": "eyes_only",
            "repair_strength": "light",
        },
    }
]

# (case name, source size, edit_photo kwargs, analysis kwargs)
_EDIT_CASES = [
    ("auto_4x5", (1200, 800), {"aspect_ratio": "auto"}, {"ratio": "4:5", "crop_enabled": False}),
    ("auto_original", (1200, 800), {"aspect_ratio": "auto"}, {"ratio": "original", "crop_enabled": False}),
    ("original", (900, 1400), {"aspect_ratio": "original"}, {"ratio": "16:9", "crop_enabled": False}),
    ("auto_16x9_crop", (1200, 800), {"aspect_ratio": "auto"}, {"ratio": "16:9", "crop_enabled": True}),
    ("auto_1x4_crop", (1200, 800), {"aspect_ratio": "auto"}, {"ratio": "1:4", "crop_enabled": True}),
    ("fixed_9x16_crop", (1200, 800), {"aspect_ratio": "9:16"},
     {"ratio": "9:16", "crop_enabled": True, "reframe_mode": "crop", "prepared_for": "9:16"}),
    ("fixed_9x16_expand", (1200, 800), {"aspect_ratio": "9:16"},
     {"ratio": "9:16", "crop_enabled": True, "reframe_mode": "expand", "prepared_for": "9:16", "wide_subject": True}),
    ("fixed_9x16_crop_wide", (1200, 800), {"aspect_ratio": "9:16"},
     {"ratio": "9:16", "crop_enabled": True, "reframe_mode": "crop", "prepared_for": "9:16", "wide_subject": True}),
    ("fixed_4x5_no_mode", (1200, 800), {"aspect_ratio": "4:5"},
     {"ratio": "4:5", "crop_enabled": True, "prepared_for": "4:5"}),
    ("high_quality", (1200, 800), {"aspect_ratio": "auto", "quality": "high"}, {"ratio": "3:2", "crop_enabled": False}),
    ("single_pass_revision", (1200, 800),
     {"aspect_ratio": "auto", "single_pass_edit": True, "revision_feedback": "less warm", "preset_key": "natural"},
     {"ratio": "1:1", "crop_enabled": False}),
]


def _openai_request(call):
    model, prompt_text, quality, size_param, png_bytes = call.args
    return {"model": model, "prompt": prompt_text, "quality": quality, "size": size_param,
            "png_size": list(Image.open(io.BytesIO(png_bytes)).size)}


def _gemini_request(call):
    model, payload = call.args
    parts = payload["contents"][0]["parts"]
    return {
        "model": model,
        "prompt": "\n".join(p["text"] for p in parts if "text" in p),
        "image_parts": [p["inlineData"]["mimeType"] for p in parts if "inlineData" in p],
        "generationConfig": payload.get("generationConfig"),
    }


def _mai_request(call):
    deployment, prompt_text, png_bytes = call.args
    return {"deployment": deployment, "prompt": prompt_text,
            "png_size": list(Image.open(io.BytesIO(png_bytes)).size), **call.kwargs}


def _mocks():
    """Patchers for every provider's HTTP call, plus how to read each one back."""
    return {
        "openai": (patch.object(openai_client, "_post_image_edit",
                                return_value={"data": [{"b64_json": _RESULT_B64}], "usage": {"n": 1}}),
                   _openai_request),
        "gemini": (patch.object(gemini_client, "_post_generate_content",
                                return_value={"candidates": [{"content": {"parts": [
                                    {"inlineData": {"data": _RESULT_B64}}]}}],
                                    "usageMetadata": {"n": 1}}),
                   _gemini_request),
        "mai": (patch.object(mai_client, "_post_image_edit",
                             return_value={"data": [{"b64_json": _RESULT_B64}], "usage": {"n": 1}}),
                _mai_request),
    }


def _run(fn, *args, **kwargs):
    """Calls fn with every provider mocked; returns (return value, request)."""
    mocks = _mocks()
    started = {name: (patcher.start(), reader) for name, (patcher, reader) in mocks.items()}
    try:
        value = fn(*args, **kwargs)
    finally:
        for patcher, _ in mocks.values():
            patcher.stop()
    called = [(name, mock, reader) for name, (mock, reader) in started.items() if mock.called]
    assert len(called) == 1, f"expected exactly one provider call, got {[c[0] for c in called]}"
    name, mock, reader = called[0]
    assert mock.call_count == 1
    return value, {"provider": name, **reader(mock.call_args)}


def _edit_snapshot(model_key, source_size, call_kwargs, analysis_kwargs):
    if analysis_kwargs.get("prepared_for"):
        analysis_kwargs = {**analysis_kwargs, "source_size": source_size}
    analysis = _analysis(**analysis_kwargs)
    if analysis_kwargs.get("reframe_mode") == "expand":
        assert analysis.get("reframe_extension"), "expand case no longer reaches the expand path"
    before = copy.deepcopy(analysis)
    (result, prompt), request = _run(
        pipeline.EDIT_PHOTO_BY_MODEL[model_key], _png(*source_size), analysis, **call_kwargs
    )
    assert analysis == before, "edit_photo mutated its analysis"
    assert result.pop("image_b64") == _RESULT_B64
    return {"request": request, "returned_prompt": prompt, "result": result}


def _eye_repair_snapshot(model_key):
    prompt_text = prompts.build_eye_repair_pass_prompt(copy.deepcopy(_GAZE_ITEMS))
    (image_bytes, prompt, usage), request = _run(
        pipeline.EYE_REPAIR_PASS_BY_MODEL[model_key], _png(1200, 800), prompt_text
    )
    assert image_bytes == base64.b64decode(_RESULT_B64)
    return {"request": request, "returned_prompt": prompt, "usage": usage}


def build_snapshots():
    snapshots = {}
    for model_key in sorted(pipeline.EDIT_PHOTO_BY_MODEL):
        for case, size, call_kwargs, analysis_kwargs in _EDIT_CASES:
            snapshots[f"edit|{model_key}|{case}"] = _edit_snapshot(model_key, size, call_kwargs, analysis_kwargs)
    for model_key in sorted(pipeline.EYE_REPAIR_PASS_BY_MODEL):
        snapshots[f"eye_repair|{model_key}"] = _eye_repair_snapshot(model_key)
    return snapshots


# Long strings (prompts, raw prompt sections) repeat across models, so the
# fixture keeps each once in a table and cases refer to it by hash.
_REF = "@text:"


def _pack(snapshots):
    table = {}

    def walk(value):
        if isinstance(value, str) and len(value) > 200:
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
            table[digest] = value
            return _REF + digest
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    return {"cases": walk(snapshots), "texts": table}


def _unpack(packed):
    table = packed["texts"]

    def walk(value):
        if isinstance(value, str) and value.startswith(_REF):
            return table[value[len(_REF):]]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    return walk(packed["cases"])


class EditorSnapshotTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        # The MAI deployment name falls back to the model id; pin that so a
        # local AZURE_MAI_DEPLOYMENT can't leak into the snapshot.
        # Expand knobs pinned to their intended values (see
        # test_aspect_ratio.ExpandPermissionTests): they may be turned for
        # hands-on testing, and the snapshot must not follow that.
        cls._patchers = [
            patch.dict(os.environ, {"AZURE_MAI_DEPLOYMENT": ""}),
            patch.object(prompts, "EXPAND_PERMISSION_RETAINED_THRESHOLD", 1.0),
            patch.object(prompts, "EXPAND_PERMISSION_ANY_PREFERENCE", False),
        ]
        for patcher in cls._patchers:
            patcher.start()
        # Round-trip through JSON so tuples etc. compare the way they're stored.
        cls.actual = json.loads(json.dumps(build_snapshots(), ensure_ascii=False))
        if UPDATE:
            SNAPSHOT_PATH.parent.mkdir(exist_ok=True)
            SNAPSHOT_PATH.write_text(
                json.dumps(_pack(cls.actual), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        cls.expected = _unpack(json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")))

    @classmethod
    def tearDownClass(cls):
        for patcher in reversed(cls._patchers):
            patcher.stop()

    def test_same_cases(self):
        self.assertEqual(sorted(self.actual), sorted(self.expected))

    def test_every_case_matches(self):
        for key in sorted(self.expected):
            with self.subTest(key):
                self.assertEqual(self.actual.get(key), self.expected[key])


if __name__ == "__main__":
    unittest.main()
