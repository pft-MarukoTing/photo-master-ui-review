"""The editor prompt every image-edit client sends (photo_agent/edit_prompt.py)."""

import unittest
from unittest.mock import patch

import mai_client
from photo_agent.edit_prompt import assemble_edit_prompt
from test_aspect_ratio import _analysis, _image_b64, _image_bytes

CROP_PLAN = {"x": 0.05, "y": 0.05, "w": 0.9, "h": 0.9, "aspectRatio": "3:2"}


def _assemble(analysis):
    return assemble_edit_prompt(
        analysis, CROP_PLAN, preset_key="natural", aspect_ratio="auto",
        source_size=(1200, 800), single_pass_edit=True,
    )


class AssembleEditPrompt(unittest.TestCase):
    def test_default_is_the_compact_prompt(self):
        prompt, metadata, strategy = _assemble(_analysis("3:2"))
        self.assertEqual(strategy, "resolved_compact")
        self.assertEqual(metadata["strategy"], "resolved_compact")
        self.assertIn("Finish:", prompt)

    def test_overwrite_sends_the_user_text_verbatim(self):
        analysis = {**_analysis("3:2"), "user_instruction": {
            "has_user_direction": True, "overwrite_prompt": True,
            "normalized_user_direction": "  Make the sky pink.  ",
        }}
        self.assertEqual(_assemble(analysis), ("Make the sky pink.", None, "overwrite"))

    def test_overwrite_without_text_falls_back_to_compact(self):
        analysis = {**_analysis("3:2"), "user_instruction": {"overwrite_prompt": True, "normalized_user_direction": "  "}}
        self.assertEqual(_assemble(analysis)[2], "resolved_compact")

    def test_a_stored_legacy_strategy_is_ignored(self):
        analysis = {**_analysis("3:2"), "_advanced_options": {"editor_prompt_strategy": "legacy"}}
        self.assertEqual(_assemble(analysis)[2], "resolved_compact")


class MaiEditPhoto(unittest.TestCase):
    def test_sends_the_assembled_prompt_and_reports_its_strategy(self):
        sent = {}

        def fake_post(deployment, prompt_text, png_bytes, **kwargs):
            sent["prompt"] = prompt_text
            sent["auto_aspect_ratio"] = kwargs.get("auto_aspect_ratio")
            return {"data": [{"b64_json": _image_b64(1200, 800)}]}

        with patch.object(mai_client, "_post_image_edit", fake_post):
            result, prompt = mai_client.edit_photo(_image_bytes(1200, 800), _analysis("3:2"), aspect_ratio="auto")

        self.assertEqual(prompt, sent["prompt"])
        self.assertTrue(sent["auto_aspect_ratio"])
        self.assertEqual(result["prompt_strategy"], "resolved_compact")
        self.assertNotIn("raw_prompt", result)


if __name__ == "__main__":
    unittest.main()
