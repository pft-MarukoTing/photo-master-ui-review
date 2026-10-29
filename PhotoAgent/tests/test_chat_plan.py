import unittest

from photo_agent import capabilities
from photo_agent.chat_plan import (
    apply_plan_patch,
    build_render_form,
    build_selections,
    default_plan,
    default_settings,
    settings_of,
)
from photo_agent.param_validation import _validate_params
from photo_agent.selections import validate_selections


class SettingsTest(unittest.TestCase):
    def test_defaults_render_with_valid_params_and_selections(self):
        plan = default_plan()
        for stage in ("analyze", "finish"):
            _validate_params(build_render_form(plan, stage))
        self.assertIsNone(validate_selections(build_selections(plan)))
        self.assertEqual(settings_of(plan), default_settings())

    def test_patch_replaces_fields_without_touching_the_input(self):
        current = default_plan()
        plan, error = apply_plan_patch(current, {"preset": "portrait", "run_count": 3, "user_prompt": "warmer"})
        self.assertIsNone(error)
        self.assertEqual((plan["preset"], plan["run_count"], plan["user_prompt"]), ("portrait", 3, "warmer"))
        self.assertEqual(current, default_plan())

    def test_debug_only_choices_need_debug(self):
        for patch in ({"render_option": "nano-banana-2"}, {"analysis_model": "gpt-4.1-mini"}):
            with self.subTest(patch=patch):
                self.assertIsNone(apply_plan_patch(default_plan(), patch)[0])
                self.assertIsNotNone(apply_plan_patch(default_plan(), patch, debug=True)[0])

    def test_fixed_aspect_ratios_are_open_to_everyone(self):
        plan, error = apply_plan_patch(default_plan(), {"aspect_ratio": "4:5"})
        self.assertIsNone(error)
        self.assertEqual(plan["aspect_ratio"], "4:5")

    def test_output_scale_accepts_listed_values_only(self):
        plan, error = apply_plan_patch(default_plan(), {"output_scale": "2K"})
        self.assertIsNone(error)
        self.assertEqual(plan["output_scale"], "2K")
        for bad in ("2x", "3K", "4K", "5K", "", 2):
            self.assertIsNotNone(apply_plan_patch(default_plan(), {"output_scale": bad})[1])

    def test_run_count_limit_follows_debug(self):
        over = capabilities.GENERAL_MAX_RUN_COUNT + 1
        self.assertIsNotNone(apply_plan_patch(default_plan(), {"run_count": over})[1])
        self.assertIsNone(apply_plan_patch(default_plan(), {"run_count": over}, debug=True)[1])
        for bad in (0, True, "2", capabilities.MAX_RUN_COUNT + 1):
            self.assertIsNotNone(apply_plan_patch(default_plan(), {"run_count": bad}, debug=True)[1])

    def test_style_options_merge_and_unset_values_drop(self):
        plan, _ = apply_plan_patch(default_plan(), {"style_options": {"pose": "cheer", "angle": "side"}})
        plan, error = apply_plan_patch(plan, {"style_options": {"angle": "default"}})
        self.assertIsNone(error)
        self.assertEqual(plan["style_options"], {"pose": "cheer"})
        self.assertIsNotNone(apply_plan_patch(plan, {"style_options": {"pose": "moonwalk"}})[1])

    def test_advanced_options_merge(self):
        plan, error = apply_plan_patch(default_plan(), {"advanced_options": {"gaze_contact_detection": False}})
        self.assertIsNone(error)
        self.assertFalse(plan["advanced_options"]["gaze_contact_detection"])
        self.assertTrue(plan["advanced_options"]["eye_closed_detection"])

    def test_bad_patches_are_rejected(self):
        for patch in ("x", {"preset": "vintage"}, {"model": "x"}, {"overwrite_prompt": "yes"},
                      {"user_prompt": "x" * 2001}, {"style_options": []}):
            with self.subTest(patch=patch):
                plan, error = apply_plan_patch(default_plan(), patch)
                self.assertIsNone(plan)
                self.assertIsNotNone(error)


class PhotoOnlyTest(unittest.TestCase):
    def test_overrides_merge_and_null_removes(self):
        plan, error = apply_plan_patch(default_plan(), {
            "recipe_checked": [True, False],
            "face_issue_overrides": {"0:blink_issue": False, "1:gaze_issue": {"allowed": True}},
        })
        self.assertIsNone(error)
        plan, error = apply_plan_patch(plan, {"face_issue_overrides": {"0:blink_issue": None}})
        self.assertIsNone(error)
        self.assertEqual(plan["face_issue_overrides"], {"1:gaze_issue": {"allowed": True}})
        self.assertEqual(plan["recipe_checked"], [True, False])

    def test_bad_patches_are_rejected(self):
        for patch in ({"face_issue_overrides": {"0:tan_issue": False}}, {"recipe_checked": "all"},
                      {"body_reshape_enabled": "yes"}):
            with self.subTest(patch=patch):
                self.assertIsNotNone(apply_plan_patch(default_plan(), patch)[1])

    def test_settings_only_rejects_photo_only_fields(self):
        for key in ("recipe_checked", "face_issue_overrides", "body_reshape_enabled"):
            with self.subTest(key=key):
                plan, error = apply_plan_patch(default_plan(), {key: None}, settings_only=True)
                self.assertIsNone(plan)
                self.assertIn("one photo", error)
        plan, error = apply_plan_patch({**default_plan(), "recipe_checked": [False]}, {"run_count": 2}, settings_only=True)
        self.assertIsNone(error)
        self.assertEqual(plan["recipe_checked"], [False])


class DetailedExplanationTest(unittest.TestCase):
    def test_on_by_default_and_a_boolean(self):
        self.assertIs(default_plan()["detailed_explanation"], True)
        plan, error = apply_plan_patch(default_plan(), {"detailed_explanation": False}, settings_only=True)
        self.assertIsNone(error)
        self.assertIs(plan["detailed_explanation"], False)
        self.assertNotIn("detailed_explanation", build_render_form(plan, "finish"))
        plan, error = apply_plan_patch(default_plan(), {"detailed_explanation": "yes"})
        self.assertIsNone(plan)
        self.assertIn("detailed_explanation", error)


class BuildTest(unittest.TestCase):
    def test_selections_come_from_the_plan(self):
        plan, _ = apply_plan_patch(default_plan(), {"style_options": {"pose": "cheer"}, "recipe_checked": [False]})
        selections = build_selections(plan)
        self.assertEqual(selections["options"], {"pose": "cheer"})
        self.assertEqual(selections["recipe_checked"], [False])
        self.assertNotIn("body_reshape_enabled", selections)
        self.assertFalse(build_selections({**plan, "body_reshape_enabled": False})["body_reshape_enabled"])

    def test_render_form_maps_the_render_option(self):
        plan, _ = apply_plan_patch(default_plan(), {
            "render_option": "gpt-image-2.5-sunburst:high", "run_count": 4, "user_prompt": "warmer"})
        form = build_render_form(plan, "finish")
        self.assertEqual((form["image_model"], form["image_quality"]), ("gpt-image-2.5-sunburst", "high"))
        self.assertEqual(form["run_count"], 1)
        self.assertEqual(form["user_prompt"], "warmer")
        self.assertEqual(build_render_form(plan, "analyze")["user_prompt"], "warmer")


if __name__ == "__main__":
    unittest.main()
