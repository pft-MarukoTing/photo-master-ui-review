import unittest
from unittest.mock import patch

import config
import gemini_client
import mai_client
import pipeline
from photo_agent import capabilities
from photo_agent.param_validation import (
    ADVANCED_OPTION_CHOICE_DEFAULTS,
    ADVANCED_OPTION_DEFAULTS,
    VALID_IMAGE_MODELS,
    _validate_params,
)
from photo_agent.selections import validate_selections


class RegistryConsistencyTest(unittest.TestCase):
    def test_style_values_match_the_editor_instructions(self):
        self.assertEqual(
            {g.id: {c.value for c in g.choices} for g in capabilities.STYLE_GROUPS},
            {group: set(values) for group, values in config.STYLE_OPTION_INSTRUCTIONS.items()},
        )
        self.assertEqual(
            {g.id: {c.value for c in g.choices} for g in capabilities.STYLE_GROUPS},
            {group: set(values) for group, values in config.STYLE_OPTION_VALUE_LABELS.items()},
        )

    def test_presets_match_config(self):
        self.assertEqual(set(capabilities.PRESET_IDS), set(config.PRESETS))
        self.assertEqual(set(capabilities.PRESET_IDS), set(config.PRESET_PROMPTS))

    def test_every_model_has_an_editor(self):
        self.assertEqual(set(pipeline.EDIT_PHOTO_BY_MODEL), set(VALID_IMAGE_MODELS))
        self.assertEqual(
            set(pipeline.EYE_REPAIR_PASS_BY_MODEL),
            {m.id for m in capabilities.IMAGE_MODELS if m.gaze_repair},
        )
        self.assertIn(config.MODELS["gaze_repair"], pipeline.EYE_REPAIR_PASS_BY_MODEL)
        self.assertIn(pipeline.BLINK_REPAIR_IMAGE_MODEL, pipeline.EYE_REPAIR_PASS_BY_MODEL)
        for m in capabilities.IMAGE_MODELS:
            self.assertEqual(pipeline.EDIT_PHOTO_BY_MODEL[m.id].keywords, {"model": m.api_model})

    def test_api_model_names_match_the_clients(self):
        self.assertEqual(capabilities.image_model("nano-banana-2").api_model, gemini_client.NANO_BANANA_2_MODEL)
        self.assertEqual(capabilities.image_model("nano-banana-lite").api_model, gemini_client.NANO_BANANA_LITE_MODEL)
        self.assertEqual(capabilities.image_model("MAI-Image-2.6").api_model, mai_client.MAI_IMAGE_26_MODEL)

    def test_arbitrary_size_is_openai_only(self):
        for m in capabilities.IMAGE_MODELS:
            if m.arbitrary_size:
                self.assertEqual(m.provider, "openai")
        self.assertTrue(capabilities.supports_arbitrary_openai_size("gpt-image-2.5-sunburst"))
        self.assertFalse(capabilities.supports_arbitrary_openai_size("gpt-image-1.5"))

    def test_render_options_and_defaults_are_valid(self):
        ids = [o.id for o in capabilities.RENDER_OPTIONS]
        self.assertEqual(len(ids), len(set(ids)))
        for o in capabilities.RENDER_OPTIONS:
            self.assertIn(o.image_model, capabilities.IMAGE_MODELS_BY_ID)
            self.assertIn(o.quality, capabilities.QUALITIES)
        self.assertIn(capabilities.DEFAULT_RENDER_OPTION, ids)
        default = next(o for o in capabilities.RENDER_OPTIONS if o.id == capabilities.DEFAULT_RENDER_OPTION)
        self.assertFalse(default.debug_only)

    def test_validate_params_defaults_come_from_the_registry(self):
        params = _validate_params({})
        self.assertEqual(params["image_model"], capabilities.DEFAULT_IMAGE_MODEL)
        self.assertEqual(params["image_quality"], capabilities.DEFAULT_QUALITY)
        self.assertEqual(params["analysis_model"], capabilities.DEFAULT_ANALYSIS_MODEL)

    def test_advanced_option_defaults_are_unchanged(self):
        self.assertEqual(ADVANCED_OPTION_DEFAULTS, {
            "eye_closed_detection": True,
            "gaze_contact_detection": True,
            "hair_mess_detection": True,
            "calm_hair": False,
            "smile_adjustment": True,
        })
        self.assertEqual(ADVANCED_OPTION_CHOICE_DEFAULTS, {"smile_level": "auto"})

    def test_portrait_fix_toggles_are_advanced_options(self):
        for fix in capabilities.PORTRAIT_FIXES:
            if fix.toggle_key:
                self.assertIn(fix.toggle_key, ADVANCED_OPTION_DEFAULTS)
            if fix.levels:
                self.assertIn(fix.default_level, [c.value for c in fix.levels])


class UnknownModelTest(unittest.TestCase):
    def test_unknown_image_model_raises_instead_of_falling_back(self):
        with self.assertRaises(RuntimeError):
            pipeline._editor_for("gpt-image-9")


class DescribeTest(unittest.TestCase):
    def test_release_view_hides_debug_internal_and_gated_entries(self):
        with patch.object(config, "ENABLE_BODY_RESHAPE", False):
            caps = capabilities.describe(debug=False)
        self.assertEqual(
            [o["id"] for o in caps["render"]["options"]],
            ["gpt-image-2.5-sunburst:low", "gpt-image-2.5-sunburst:medium", "gpt-image-2.5-sunburst:high"],
        )
        self.assertEqual([a["value"] for a in caps["render"]["aspect_ratios"]],
                         ["auto", "original", "9:16", "2:3", "3:4", "4:5", "1:1", "5:4", "4:3", "3:2", "16:9"])
        self.assertEqual([m["id"] for m in caps["render"]["analysis_models"]], ["gpt-5.6-luna"])
        self.assertEqual(caps["render"]["max_run_count"], capabilities.GENERAL_MAX_RUN_COUNT)
        self.assertEqual(caps["render"]["defaults"]["render_option"], "gpt-image-2.5-sunburst:medium")
        self.assertEqual([s["value"] for s in caps["render"]["output_scales"]], ["1K", "2K", "3K", "4K"])
        self.assertEqual(caps["render"]["defaults"]["output_scale"], "1K")
        four_k = caps["render"]["output_scales"][-1]
        self.assertEqual(four_k["sizes"]["16:9"], "3840x2160")
        self.assertNotIn("auto", four_k["sizes"])
        self.assertEqual(four_k["megapixels"], 8.3)
        self.assertTrue(all(o["scalable"] for o in caps["render"]["options"]))
        keys = [o["key"] for o in caps["advanced_options"]]
        self.assertNotIn("calm_hair", keys)
        self.assertNotIn("smile_level", keys)
        self.assertNotIn("body_reshape", [f["id"] for f in caps["portrait_fixes"]])

    def test_debug_view_and_body_reshape_flag(self):
        with patch.object(config, "ENABLE_BODY_RESHAPE", True):
            caps = capabilities.describe(debug=True)
        self.assertEqual(len(caps["render"]["options"]), len(capabilities.RENDER_OPTIONS))
        self.assertEqual(caps["render"]["max_run_count"], capabilities.MAX_RUN_COUNT)
        keys = [o["key"] for o in caps["advanced_options"]]
        self.assertNotIn("calm_hair", keys)
        self.assertNotIn("smile_level", keys)
        self.assertIn("body_reshape", [f["id"] for f in caps["portrait_fixes"]])


class ValidateSelectionValuesTest(unittest.TestCase):
    def test_current_frontend_payload_is_accepted(self):
        self.assertIsNone(validate_selections({
            "options": {"pose": "cheer", "angle": "default", "lighting": "", "size": None},
            "recipe_checked": [True, False, None],
            "advanced_options": {
                "eye_closed_detection": True,
                "gaze_contact_detection": False,
                "hair_mess_detection": True,
                "smile_adjustment": True,
            },
            "face_issue_overrides": {
                "0:blink_issue": False,
                "0:gaze_issue": {"allowed": True},
                "1:hair_issue": {"allowed": True, "level": "tidy"},
                "1:smile_issue": {"allowed": True, "level": "laughing"},
                "2:body_reshape": {"allowed": True},
            },
            "body_reshape_enabled": False,
        }))

    def test_legacy_values_are_still_accepted(self):
        self.assertIsNone(validate_selections({
            "options": {"angle": "side"},
            "advanced_options": {"calm_hair": True, "smile_level": "closed_smile", "editor_prompt_strategy": "legacy"},
            "face_issue_overrides": {"0:smile_issue": {"allowed": True, "level": "toothy_smile"}},
        }))

    def test_bad_values_are_rejected(self):
        bad = [
            {"options": {"pose": "moonwalk"}},
            {"options": {"mood": "happy"}},
            {"advanced_options": {"hair_mess_detection": "yes"}},
            {"advanced_options": {"smile_level": "grin"}},
            {"face_issue_overrides": {"0:tan_issue": False}},
            {"face_issue_overrides": {"zero:blink_issue": False}},
            {"face_issue_overrides": {"blink_issue": False}},
            {"face_issue_overrides": {"0:hair_issue": {"allowed": True, "level": "shaved"}}},
            {"face_issue_overrides": {"0:blink_issue": {"allowed": True, "level": "tidy"}}},
            {"face_issue_overrides": {"0:gaze_issue": {"allowed": "yes"}}},
            {"face_issue_overrides": {"0:gaze_issue": "on"}},
        ]
        for selections in bad:
            with self.subTest(selections=selections):
                self.assertIsNotNone(validate_selections(selections))


if __name__ == "__main__":
    unittest.main()
