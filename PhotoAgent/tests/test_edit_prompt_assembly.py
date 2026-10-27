"""The editor prompt every image-edit client sends (photo_agent/edit_prompt.py)."""

import unittest
from unittest.mock import patch

import config
import mai_client
from config import PRESET_PROMPTS
from photo_agent.edit_prompt import assemble_edit_prompt
from tests.test_aspect_ratio import _analysis, _image_b64, _image_bytes

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

    def test_overwrite_appends_the_picked_style_after_the_user_text(self):
        analysis = {**_analysis("3:2"), "style_options": {"lighting": "gold", "pose": "default"},
                    "user_instruction": {"has_user_direction": True, "overwrite_prompt": True,
                                         "normalized_user_direction": "Make the sky pink."}}
        prompt, metadata, strategy = _assemble(analysis)
        self.assertEqual(strategy, "overwrite")
        self.assertIsNone(metadata)
        self.assertTrue(prompt.startswith("Make the sky pink.\n\n"))
        self.assertIn("the request wins for that aspect", prompt)
        self.assertIn("- Lighting:", prompt)
        self.assertIn("add other retouching", prompt)
        self.assertNotIn("Baseline retouch:", prompt)
        self.assertNotIn("Pose", prompt)
        self.assertNotIn("Finish:", prompt)

    def test_a_continuation_sends_the_request_alone_even_with_styles_and_baseline_on(self):
        # 接著這張調整: the source is already a finished version.
        analysis = {**_analysis("3:2"), "_continuation": True, "style_options": {"lighting": "gold"},
                    "user_instruction": {"has_user_direction": True, "overwrite_prompt": True,
                                         "normalized_user_direction": "  Remove the cup.  "}}
        with patch.object(config, "ENABLE_OVERWRITE_BASELINE_RETOUCH", True):
            prompt, metadata, strategy = _assemble(analysis)
        self.assertEqual((metadata, strategy), (None, "continuation"))
        self.assertTrue(prompt.startswith("Remove the cup.\n\n"))
        self.assertIn("Do not retouch any further", prompt)
        self.assertIn("Keep the same aspect ratio, framing, and crop", prompt)
        self.assertNotIn("- Lighting:", prompt)
        self.assertNotIn("Baseline retouch:", prompt)
        self.assertNotIn("Finish:", prompt)

    def test_a_continuation_with_the_original_says_which_image_is_which(self):
        analysis = {**_analysis("3:2"), "_continuation": True, "_continuation_original": {"restore": " the mouth "},
                    "user_instruction": {"has_user_direction": True, "overwrite_prompt": True,
                                         "normalized_user_direction": "Leave the mouth as it was."}}
        prompt, _, strategy = _assemble(analysis)
        self.assertEqual(strategy, "continuation")
        self.assertTrue(prompt.startswith("Leave the mouth as it was.\n\n"))
        self.assertLess(prompt.index("Do not retouch any further"), prompt.index("Two images are attached."))
        self.assertIn("The first image is the retouched version to edit", prompt)
        self.assertIn("Bring back from the original photo: the mouth.", prompt)

    def test_a_continuation_with_nothing_singled_out_names_nothing_to_bring_back(self):
        analysis = {**_analysis("3:2"), "_continuation": True, "_continuation_original": {"restore": ""},
                    "user_instruction": {"has_user_direction": True, "overwrite_prompt": True,
                                         "normalized_user_direction": "Like before."}}
        prompt = _assemble(analysis)[0]
        self.assertIn("Two images are attached.", prompt)
        self.assertNotIn("Bring back from", prompt)

    def test_a_continuation_without_the_original_mentions_one_image_only(self):
        analysis = {**_analysis("3:2"), "_continuation": True,
                    "user_instruction": {"has_user_direction": True, "overwrite_prompt": True,
                                         "normalized_user_direction": "Remove the cup."}}
        self.assertNotIn("Two images", _assemble(analysis)[0])

    def test_the_worker_hands_the_original_to_an_editor_that_takes_it(self):
        import pipeline
        seen = {}

        def fake_edit(image_bytes, analysis, quality, preset_key, reference_image_bytes=None, **kwargs):
            seen["images"] = (image_bytes, reference_image_bytes)
            return {"image_b64": "x", "usage": None, "geometry_warnings": []}, "prompt"

        def old_edit(image_bytes, analysis, quality, preset_key, aspect_ratio=None, single_pass_edit=None,
                     output_scale=None):
            seen["old"] = True
            return {"image_b64": "x", "usage": None, "geometry_warnings": []}, "prompt"

        stub = {"_skipped_analysis": True, "_continuation": True, "_continuation_original": {"restore": ""},
                "_aspect_ratio_pref_at_analysis": "original", "prompts": {}, "timings": {}, "usage": {},
                "compatibility_version": 1}
        edited, original = _image_bytes(), _image_bytes(800, 1200)
        for editor in (fake_edit, old_edit):
            with patch.dict(pipeline.EDIT_PHOTO_BY_MODEL, {"gpt-image-2": editor}):
                pipeline.finish_stage(edited, dict(stub), "Like before.", "natural", "medium",
                                      aspect_ratio="original", overwrite_prompt=True, single_pass_edit=True,
                                      run_qa=False, chat_body_side_clarification=False,
                                      reference_image_bytes=original)
        self.assertEqual(seen["images"], (edited, original))
        self.assertTrue(seen["old"])  # an editor without a second image still runs

    def test_the_worker_takes_the_chat_continuation_stub_as_is(self):
        # The stub chat._prepare_continuation hands the worker, with no reframe fields.
        import pipeline
        seen = {}

        def fake_edit(image_bytes, analysis, quality, preset_key, **kwargs):
            seen["analysis"] = analysis
            return {"image_b64": "x", "usage": None, "geometry_warnings": []}, "prompt"

        stub = {"_skipped_analysis": True, "_continuation": True, "_aspect_ratio_pref_at_analysis": "original",
                "prompts": {}, "timings": {}, "usage": {}, "compatibility_version": 1}
        with patch.dict(pipeline.EDIT_PHOTO_BY_MODEL, {"gpt-image-2": fake_edit}):
            pipeline.finish_stage(_image_bytes(), stub, "Remove the cup.", "natural", "medium",
                                  aspect_ratio="original", overwrite_prompt=True, single_pass_edit=True,
                                  run_qa=False, chat_body_side_clarification=False)
        prompt, _, strategy = _assemble(seen["analysis"])
        self.assertEqual(strategy, "continuation")
        self.assertTrue(prompt.startswith("Remove the cup.\n\n"))

    def test_overwrite_with_baseline_retouch_adds_the_preset_after_the_styles(self):
        analysis = {**_analysis("3:2"), "style_options": {"lighting": "gold"},
                    "user_instruction": {"has_user_direction": True, "overwrite_prompt": True,
                                         "normalized_user_direction": "Make the sky pink."}}
        with patch.object(config, "ENABLE_OVERWRITE_BASELINE_RETOUCH", True):
            prompt, metadata, strategy = _assemble(analysis)
        self.assertEqual((metadata, strategy), (None, "overwrite"))
        self.assertTrue(prompt.startswith("Make the sky pink.\n\n"))
        self.assertLess(prompt.index("- Lighting:"), prompt.index("Baseline retouch:"))
        self.assertIn(PRESET_PROMPTS["natural"], prompt)
        self.assertNotIn("add other retouching", prompt)
        self.assertNotIn("Finish:", prompt)

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
