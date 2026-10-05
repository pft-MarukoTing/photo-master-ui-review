"""Tests for the framing half of the edit prompt.

Imports only prompts.py, so it runs without PIL/numpy/cv2:

    python3 -m unittest test_edit_prompt_framing -v
"""

import unittest
from unittest.mock import patch

import prompts
from prompts import (
    _build_crop_instruction_block,
    _canvas_shape_difference,
    _parse_ratio_label,
    build_edit_prompt,
    build_overall_analysis_instruction,
)

# The run that motivated this: a 1122x1402 (4:5) photo, a 1088x1456 (3:4) canvas,
# and a crop plan whose own ratio matched the canvas. The model kept the whole
# frame and squeezed it, and every requested-shape check passed.
SOURCE = (1122, 1402)
CROP_PLAN = {"x": 0.0258, "y": 0.035, "w": 0.8684, "h": 0.93, "aspectRatio": "1088:1456"}

PRESET = {"intent": "intent", "label": "label"}
ANALYSIS = {
    "photo_summary": "一位男性講者",
    "main_subject": "男性講者",
    "roi_anchor": "臉與雙手",
    "roi_box": {"x": 0.3, "y": 0.15, "w": 0.44, "h": 0.6, "reason": "臉與雙手"},
    "recommended_aspect_ratio": "3:4",
    "correction_plan": ["提升整體曝光"],
    "correction_axes": [["exposure"]],
    "subjects": [{"label": "男性", "is_person": True, "prominence": 1}],
}


class OverallAnalysisPrompt(unittest.TestCase):
    def test_light_color_target_flows_into_one_execution_contract(self):
        prompt = build_overall_analysis_instruction("original", {})
        self.assertIn("Treat edit_intent as the single finished light-and-color target", prompt)
        self.assertIn("Make lighting_strategy and memory_color_strategy implementation plans for that same target", prompt)
        self.assertIn("correction_plan, correction_axes, correction_inverse, and retouch_recipe describe the same adjustments", prompt)
        self.assertIn("Scene exposure, white balance, contrast, hue, and saturation remain available", prompt)
        self.assertIn("Do not use preserve_rules to retain scene exposure", prompt)

    def test_every_ratio_mode_requires_an_active_crop_comparison(self):
        for ratio in ("auto", "original"):
            prompt = build_overall_analysis_instruction(ratio, {})
            self.assertIn(
                "Choose the strongest framing independently of the output aspect-ratio preference",
                prompt,
            )
            self.assertIn("crop that conforms to the selected ratio, treating both as equally eligible", prompt)
            self.assertIn("protected_regions contain only indispensable subject details", prompt)
            self.assertIn("keep every protected region inside crop_box with breathing room", prompt)
            self.assertIn("intentionally trimmed subject/body edges outside these boxes", prompt)

        overall_prompt = build_overall_analysis_instruction("original", {})
        self.assertIn("normally remove at least 8% of the original span on one axis", overall_prompt)
        self.assertIn("change subject scale, placement, or edge content", overall_prompt)
        self.assertIn("intended-to-remove edge content outside these protection boxes", overall_prompt)

    def test_original_ratio_is_not_a_request_to_disable_crop(self):
        prompt = build_overall_analysis_instruction("original", {})
        self.assertIn('Set recommended_aspect_ratio to "original"', prompt)
        self.assertIn("Every crop candidate must preserve the source aspect ratio", prompt)


class ParseRatioLabel(unittest.TestCase):
    def test_numeric_labels(self):
        self.assertAlmostEqual(_parse_ratio_label("1088:1456"), 1088 / 1456)
        self.assertAlmostEqual(_parse_ratio_label("4:5"), 0.8)
        self.assertAlmostEqual(_parse_ratio_label(" 16 : 9 "), 16 / 9)

    def test_non_ratios_are_none(self):
        for label in ("original", "", None, "keep the resulting region's natural ratio", "0:5"):
            self.assertIsNone(_parse_ratio_label(label), label)


class CanvasShapeDifference(unittest.TestCase):
    def test_the_real_case_is_narrower(self):
        self.assertEqual(_canvas_shape_difference("1088:1456", SOURCE), "narrower")

    def test_wider_canvas(self):
        self.assertEqual(_canvas_shape_difference("16:9", SOURCE), "wider")

    def test_matching_shape(self):
        self.assertEqual(_canvas_shape_difference("4:5", SOURCE), "same")

    def test_provider_raster_rounding_counts_as_same(self):
        # Gemini rasters 4:5 as 928x1152 -- 0.7% off, inside the tolerance.
        self.assertEqual(_canvas_shape_difference("928:1152", (1000, 1250)), "same")

    def test_undeterminable_returns_none(self):
        self.assertIsNone(_canvas_shape_difference("original", SOURCE))
        self.assertIsNone(_canvas_shape_difference("3:4", None))
        self.assertIsNone(_canvas_shape_difference("3:4", (0, 0)))


class CropInstructionBlock(unittest.TestCase):
    def test_no_coordinates_ever_reach_the_prompt(self):
        block = _build_crop_instruction_block(CROP_PLAN, source_size=SOURCE)
        for fragment in ("x=", "0.868", "0.026", "0.93", "%"):
            self.assertNotIn(fragment, block, fragment)

    def test_narrower_canvas_states_the_consequence_and_withholds_the_reassurance(self):
        block = _build_crop_instruction_block(CROP_PLAN, source_size=SOURCE)
        self.assertIn("narrower than the photograph you were given", block)
        self.assertIn("left and right edges cannot both be inside it", block)
        # The sentence that made a zero-trim answer look legitimate on a canvas
        # where no zero-trim answer exists.
        self.assertNotIn("a small trim is a fine answer", block)

    def test_narrower_canvas_outranks_the_analysis_breathing_room_notes(self):
        block = _build_crop_instruction_block(CROP_PLAN, source_size=SOURCE)
        self.assertIn("rather than narrowing the people and objects in it", block)

    def test_wider_canvas_talks_about_the_other_axis(self):
        block = _build_crop_instruction_block({**CROP_PLAN, "aspectRatio": "16:9"}, source_size=SOURCE)
        self.assertIn("top and bottom edges cannot both be inside it", block)
        self.assertNotIn("left and right edges", block)
        self.assertNotIn("a small trim is a fine answer", block)

    def test_matching_shape_is_the_only_case_that_reassures(self):
        crop_plan = {**CROP_PLAN, "w": .96, "h": .96, "aspectRatio": "4:5"}
        block = _build_crop_instruction_block(crop_plan, source_size=SOURCE)
        self.assertIn("the same shape as the photograph", block)
        self.assertIn("a small trim is a fine answer", block)

    def test_matching_shape_visible_axis_trim_is_still_a_reframe(self):
        crop_plan = {**CROP_PLAN, "w": .9275, "h": .9275, "aspectRatio": "4:5"}
        block = _build_crop_instruction_block(crop_plan, source_size=SOURCE)
        self.assertIn("visibly tightening the composition", block)
        self.assertIn("let the selected outer content leave the final frame", block)
        self.assertNotIn("a small trim is a fine answer", block)

    def test_unknown_source_size_omits_the_consequence_rather_than_guessing(self):
        block = _build_crop_instruction_block(CROP_PLAN)
        for fragment in ("narrower than", "wider than", "the same shape as", "a small trim is a fine answer"):
            self.assertNotIn(fragment, block, fragment)

    def test_every_branch_carries_the_shape_rules(self):
        blocks = [
            _build_crop_instruction_block(None),
            _build_crop_instruction_block(CROP_PLAN, source_size=SOURCE),
            _build_crop_instruction_block({**CROP_PLAN, "aspectRatio": "4:5"}, source_size=SOURCE),
            _build_crop_instruction_block(CROP_PLAN, require_tighter=True, source_size=SOURCE),
        ]
        for block in blocks:
            self.assertIn("Resizing a single axis is not available", block)
            self.assertIn("keeps the proportions, the tilt", block)

    def test_closer_override_keeps_tightening_mandatory(self):
        block = _build_crop_instruction_block(CROP_PLAN, require_tighter=True, source_size=SOURCE)
        self.assertIn("Closer framing was requested", block)
        self.assertNotIn("a small trim is a fine answer", block)


class FullPrompt(unittest.TestCase):
    def setUp(self):
        """This file asserts the crop-only framing text, so it must not read
        whatever the expand knobs happen to be turned to (see prompts.py)."""
        patcher = patch.object(prompts, "EXPAND_PERMISSION_RETAINED_THRESHOLD", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_raw_editor_receives_one_finished_light_color_target(self):
        analysis = {
            **ANALYSIS,
            "edit_intent": "明亮、通透並具有清楚的主體層次。",
            "lighting_strategy": "提亮主體與中暗部，保留高光層次。",
            "memory_color_strategy": "建立乾淨而有吸引力的場景配色，獨立保護膚色。",
        }
        prompt = build_edit_prompt(
            analysis, PRESET, "自然風格", "", CROP_PLAN, source_size=SOURCE,
        )
        target_start = prompt.index("Finished light and color target:")
        analysis_start = prompt.index("Photo analysis:")
        self.assertLess(target_start, analysis_start)
        self.assertIn("- Finished visual result: 明亮、通透並具有清楚的主體層次。", prompt)
        self.assertIn("- Lighting plan: 提亮主體與中暗部，保留高光層次。", prompt)
        self.assertIn("- Color plan: 建立乾淨而有吸引力的場景配色，獨立保護膚色。", prompt)
        self.assertEqual(prompt.count("提亮主體與中暗部，保留高光層次。"), 1)
        self.assertEqual(prompt.count("建立乾淨而有吸引力的場景配色，獨立保護膚色。"), 1)
        self.assertEqual(prompt.count("提升整體曝光"), 1)
        self.assertIn("Realize the Finished light and color target through the approved correction plan", prompt)
        self.assertNotIn("open shadows selectively", prompt)
        self.assertNotIn("A face is one surface", prompt)

    def test_raw_editor_rebuilds_target_after_light_item_is_unchecked(self):
        analysis = {
            **ANALYSIS,
            "edit_intent": "提亮人物並建立明亮背景。",
            "lighting_strategy": "人物與背景共同提升半級。",
            "memory_color_strategy": "建立乾淨的藍色場景。",
            "aesthetic_diagnosis": "人物太暗，必須提升人物與背景曝光半級。",
            "detected_issues": ["人物與背景曝光不足。"],
            "correction_plan": ["整理場景藍色。"],
            "correction_axes": [["white_balance"]],
            "rejected_corrections": ["提升人物與背景曝光半級。"],
            "rejected_axes": [["exposure"]],
            "rejected_preserve": ["維持原圖人物與背景的曝光。"],
        }
        prompt = build_edit_prompt(
            analysis, PRESET, "自然風格", "", CROP_PLAN, source_size=SOURCE,
        )
        self.assertNotIn("提亮人物並建立明亮背景", prompt)
        self.assertNotIn("人物與背景共同提升半級", prompt)
        self.assertNotIn("人物太暗，必須提升人物與背景曝光半級", prompt)
        self.assertNotIn("人物與背景曝光不足", prompt)
        self.assertNotIn("Aesthetic diagnosis:", prompt)
        self.assertIn("Photo context after user review:", prompt)
        self.assertIn("Color plan: 建立乾淨的藍色場景", prompt)
        self.assertIn("Reviewed finished result", prompt)
        self.assertIn("User-preserved choices", prompt)
        self.assertIn("維持原圖人物與背景的曝光", prompt)
        self.assertNotIn("提升人物與背景曝光半級", prompt)

    def _prompt(self, style_options=None, **kwargs):
        analysis = {**ANALYSIS, "style_options": style_options or {}}
        return build_edit_prompt(
            analysis, PRESET, "自然風格", "", CROP_PLAN, source_size=SOURCE, **kwargs
        )

    def test_no_box_coordinates_anywhere_in_the_prompt(self):
        analysis = {
            **ANALYSIS,
            "style_options": {},
            "subject_extent_box": {"x": 0.2, "y": 0.1, "w": 0.6, "h": 0.8, "reason": "含手部"},
            "protected_regions": [{"label": "雙手", "x": 0.3, "y": 0.4, "w": 0.4, "h": 0.3, "reason": "手勢"}],
        }
        prompt = build_edit_prompt(analysis, PRESET, "自然風格", "", CROP_PLAN, source_size=SOURCE)
        self.assertNotIn('"x":', prompt)
        self.assertNotIn("x=0.", prompt)
        # The semantic content of those boxes still has to arrive.
        self.assertIn("含手部", prompt)
        self.assertIn("雙手", prompt)
        self.assertIn("臉與雙手", prompt)

    def test_consequence_reaches_the_prompt_in_faithful_mode(self):
        self.assertIn("left and right edges cannot both be inside it", self._prompt())

    def test_outpaint_mode_adds_scene_instead_of_giving_it_up(self):
        prompt = self._prompt({"size": "wider"})
        self.assertIn("take in more scene above and below than at the sides", prompt)
        # The give-something-up phrasing is wrong when outpainting is allowed.
        self.assertNotIn("cannot both be inside it", prompt)

    def test_closer_mode_still_gets_the_consequence(self):
        prompt = self._prompt({"size": "closer"})
        self.assertIn("left and right edges cannot both be inside it", prompt)
        self.assertIn("Closer framing was requested", prompt)

    def test_prompt_builds_without_source_size(self):
        analysis = {**ANALYSIS, "style_options": {}}
        prompt = build_edit_prompt(analysis, PRESET, "自然風格", "", CROP_PLAN)
        self.assertIn("Resizing a single axis is not available", prompt)
        self.assertNotIn("cannot both be inside it", prompt)

    def test_raw_builder_exposes_the_exact_crop_section_for_compact_renderer(self):
        prompt, sections = build_edit_prompt(
            {**ANALYSIS, "style_options": {}}, PRESET, "自然風格", "",
            CROP_PLAN, source_size=SOURCE, return_sections=True,
        )
        self.assertTrue(sections["crop_instruction"])
        self.assertIn(f'Crop instruction:\n{sections["crop_instruction"]}', prompt)

    def test_raw_builder_exposes_the_exact_style_section_for_compact_renderer(self):
        prompt, sections = build_edit_prompt(
            {**ANALYSIS, "style_options": {"pose": "playful", "lighting": "soft"}},
            PRESET, "自然風格", "", CROP_PLAN, source_size=SOURCE,
            return_sections=True,
        )
        self.assertTrue(sections["style_instruction"])
        self.assertIn(sections["style_instruction"], prompt)
        self.assertIn("Pose: Playful", sections["style_instruction"])
        self.assertIn("Lighting: Soft Glow", sections["style_instruction"])

    def test_blue_toned_light_reaches_style_and_light_color_plan(self):
        prompt, sections = build_edit_prompt(
            {**ANALYSIS, "style_options": {"lighting": "cool"}},
            PRESET, "自然風格", "", CROP_PLAN, source_size=SOURCE,
            return_sections=True,
        )
        self.assertIn("Lighting: Blue Hour: Transform the photo with a clearly visible cool blue-hour atmosphere", sections["style_instruction"])
        self.assertIn("shape subjects and background together", sections["style_instruction"])
        self.assertIn("Keep faces in that ambient light, with exposure and shadow direction consistent with nearby surfaces", sections["style_instruction"])
        self.assertIn("existing lamps keep their own color", sections["style_instruction"])
        self.assertIn("Use the selected Blue Hour style as the final atmosphere", prompt)

    def test_selected_lighting_replaces_stale_analysis_color_goal(self):
        analysis = {
            **ANALYSIS,
            "style_options": {"lighting": "cool"},
            "edit_intent": "營造溫暖的金色夕陽。",
            "lighting_strategy": "保留暖色日光。",
            "memory_color_strategy": "加強橘黃色調。",
            "aesthetic_diagnosis": "畫面需要更暖。",
        }
        prompt = build_edit_prompt(
            analysis, PRESET, "自然風格", "", CROP_PLAN, source_size=SOURCE,
        )
        self.assertIn("Use the selected Blue Hour style as the final atmosphere", prompt)
        for stale_goal in ("營造溫暖的金色夕陽", "保留暖色日光", "加強橘黃色調", "畫面需要更暖"):
            self.assertNotIn(stale_goal, prompt)

    def test_night_reaches_style_and_light_color_plan(self):
        prompt, sections = build_edit_prompt(
            {**ANALYSIS, "style_options": {"lighting": "night"}},
            PRESET, "自然風格", "", CROP_PLAN, source_size=SOURCE,
            return_sections=True,
        )
        self.assertIn("Lighting: Night: Transform the whole photo into a clearly recognizable night scene", sections["style_instruction"])
        self.assertIn("existing light sources create gentle highlights on the subject and surroundings", sections["style_instruction"])
        self.assertIn("Use the selected Night style as the final atmosphere", prompt)


if __name__ == "__main__":
    unittest.main()
