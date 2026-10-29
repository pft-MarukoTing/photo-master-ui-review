"""Offline Analyze → Edit harness for the holistic Body Reshape contract.

The harness has injectable analyzer/editor callables, so the unit tests prove
the routing without an API key.  The command-line mode consumes a saved JSON
analysis and writes a small HTML report; it never makes a paid model request.
"""

from html import escape
import argparse
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from photo_agent.feature_resolver import build_compact_editor_prompt
from prompts import get_body_reshape_edit_prompt


BODY_PROMPT = "自然修飾人物可見腰腹與身形輪廓，保持衣物邊界及背景透視連續"


def body_analysis():
    return {
        "subjects": [{
            "label": "主要人物",
            "is_person": True,
            "box": {"x": .1, "y": .1, "w": .6, "h": .8},
            "body_reshape": {"should_edit": True, "edit_prompt": BODY_PROMPT},
        }],
        "correction_plan": [],
        "correction_axes": [],
    }


def analyze_then_edit(image, analyze_fn, edit_fn, *, editor_prompt_fn=None):
    """Run one analyzer and one editor call, passing the final prompt through."""
    analysis = analyze_fn(image)
    body_prompt = get_body_reshape_edit_prompt(analysis)
    if not body_prompt:
        raise ValueError("analysis did not contain an enabled Body Reshape edit prompt")
    if editor_prompt_fn is None:
        editor_prompt_fn = lambda value: build_compact_editor_prompt(value, "自然", "", None)[0]
    actual_editor_prompt = editor_prompt_fn(analysis)
    result = edit_fn(image, actual_editor_prompt)
    return {
        "analysis": analysis,
        "body_edit_prompt": body_prompt,
        "actual_editor_prompt": actual_editor_prompt,
        "result": result,
    }


def _image_html(value, label):
    if not value:
        return f"<p>{escape(label)} image not supplied.</p>"
    return f'<img alt="{escape(label)}" src="{escape(str(value), quote=True)}">'


def build_report_html(record):
    """Render only the new contract fields in a human-readable debug report."""
    body_prompt = escape(str(record.get("body_edit_prompt") or ""))
    editor_prompt = escape(str(record.get("actual_editor_prompt") or ""))
    before = record.get("before")
    after = record.get("after")
    return f"""<!doctype html>
<html lang="zh-Hant"><meta charset="utf-8"><title>Body Reshape Analyze → Edit</title>
<body>
<h1>Body Reshape Analyze → Edit</h1>
<section><h2>Body Reshape Analyze edit prompt</h2><pre>{body_prompt}</pre></section>
<section><h2>Actual Edit API prompt</h2><pre>{editor_prompt}</pre></section>
<section><h2>Before</h2>{_image_html(before, "Before")}</section>
<section><h2>After</h2>{_image_html(after, "After")}</section>
</body></html>"""


def _load_record(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    analysis = payload.get("analysis", payload) if isinstance(payload, dict) else {}
    body_prompt = get_body_reshape_edit_prompt(analysis)
    actual_prompt = build_compact_editor_prompt(analysis, "自然", "", None)[0]
    return {
        "analysis": analysis,
        "body_edit_prompt": body_prompt,
        "actual_editor_prompt": actual_prompt,
        "before": payload.get("before") if isinstance(payload, dict) else None,
        "after": payload.get("after") if isinstance(payload, dict) else None,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_json", type=Path, help="saved analysis JSON")
    parser.add_argument("--output", type=Path, default=Path("body_reshape_analyze_to_edit.html"))
    args = parser.parse_args(argv)
    record = _load_record(args.analysis_json)
    if not record["body_edit_prompt"]:
        parser.error("analysis JSON has no enabled body_reshape.edit_prompt")
    args.output.write_text(build_report_html(record), encoding="utf-8")
    print(args.output.resolve())
    return 0


@patch("config.ENABLE_BODY_RESHAPE", True)
class BodyReshapeApiToEditTests(unittest.TestCase):
    def test_harness_makes_one_editor_call_with_analyzer_prompt(self):
        analyzer = Mock(return_value=body_analysis())
        editor = Mock(return_value="generated-image")

        record = analyze_then_edit(b"source", analyzer, editor)

        analyzer.assert_called_once_with(b"source")
        editor.assert_called_once()
        self.assertEqual(record["body_edit_prompt"], BODY_PROMPT)
        self.assertEqual(record["result"], "generated-image")
        self.assertEqual(record["actual_editor_prompt"].count(BODY_PROMPT), 1)

    def test_report_has_required_labels_and_no_legacy_body_details(self):
        html = build_report_html({
            "body_edit_prompt": BODY_PROMPT,
            "actual_editor_prompt": f"Body reshape execution instructions (analysis-approved):\n{BODY_PROMPT}",
            "before": "before.jpg",
            "after": "after.png",
        })
        for label in (
            "Body Reshape Analyze edit prompt",
            "Actual Edit API prompt",
            "Before",
            "After",
        ):
            self.assertIn(label, html)
        for removed in ("key_points", "target_pct", "effective_target_pct", "execution plan", "strength"):
            self.assertNotIn(removed, html)

    def test_editor_prompt_builder_keeps_body_sentence_once(self):
        analysis = body_analysis()
        record = analyze_then_edit(b"source", lambda _: analysis, lambda _, prompt: prompt)
        self.assertEqual(record["actual_editor_prompt"].count(BODY_PROMPT), 1)


if __name__ == "__main__":
    raise SystemExit(main())
