"""Tests for the framing half of the edit prompt.

Imports only prompts.py, so it runs without PIL/numpy/cv2:

    python3 -m unittest test_edit_prompt_framing -v
"""

import unittest

from prompts import (
    _build_crop_instruction_block,
    _canvas_shape_difference,
    _parse_ratio_label,
    build_edit_prompt,
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
        block = _build_crop_instruction_block({**CROP_PLAN, "aspectRatio": "4:5"}, source_size=SOURCE)
        self.assertIn("the same shape as the photograph", block)
        self.assertIn("a small trim is a fine answer", block)

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


if __name__ == "__main__":
    unittest.main()
