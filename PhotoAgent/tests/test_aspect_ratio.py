import base64
import copy
import io
import unittest
from unittest.mock import patch

from PIL import Image

import gemini_client
import openai_client
import prompts
import image_tools
from image_tools import (
    crop_resolution_ratio_label,
    crop_would_cut_a_subject,
    plan_reframe_extension,
)
from prompts import (
    build_framing_sections,
    build_overall_analysis_instruction,
    expand_permitted,
)
from photo_agent.feature_resolver import _compact_raw_crop_instruction, build_compact_editor_prompt
from aspect_ratio_rules import AUTO_ASPECT_RATIO_LABELS, allowed_auto_ratio_labels
from image_tools import (
    apply_derived_aspect_ratio,
    derive_aspect_ratio_from_crop,
    openai_edit_size_for_ratio,
    output_geometry_warnings,
    resolve_crop_box_with_report,
)


def _image_bytes(width=1200, height=800):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 140, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


def _image_b64(width, height):
    return base64.b64encode(_image_bytes(width, height)).decode()


def _analysis(ratio="original", crop_enabled=False):
    return {
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
    }


class CropGeometryTests(unittest.TestCase):
    def test_fallback_crop_matches_requested_canvas_without_stretching(self):
        context = _analysis("4:5", crop_enabled=False)
        crop = resolve_crop_box_with_report(
            context["crop_box"], context, image_width=1200, image_height=800
        )["cropBox"]

        self.assertIsNotNone(crop)
        actual = crop["w"] * 1200 / (crop["h"] * 800)
        self.assertAlmostEqual(actual, 4 / 5, places=6)

    def test_hard_protection_slides_toward_right_and_bottom_before_growing(self):
        context = {
            "recommended_aspect_ratio": "1:1",
            "crop_box": {
                "enabled": True, "x": 0.1, "y": 0.1,
                "w": 0.8, "h": 0.8, "aspect_ratio": "1:1",
            },
            "roi_box": {"x": 0.3, "y": 0.3, "w": 0.3, "h": 0.3},
            "subject_extent_box": None,
            "protected_regions": [{"x": 0.85, "y": 0.85, "w": 0.1, "h": 0.1}],
        }

        crop = resolve_crop_box_with_report(
            context["crop_box"], context, image_width=1600, image_height=1600
        )["cropBox"]

        self.assertIsNotNone(crop)
        self.assertAlmostEqual(crop["w"], 0.8, places=6)
        self.assertAlmostEqual(crop["h"], 0.8, places=6)
        self.assertAlmostEqual(crop["x"] + crop["w"], 1.0, places=6)
        self.assertAlmostEqual(crop["y"] + crop["h"], 1.0, places=6)


class RatioFitPlacementTests(unittest.TestCase):
    """Forcing a box back onto its aspect ratio slides it before it sacrifices
    the ROI -- the loss used to come off whichever side the origin wasn't on,
    which was regularly the side ROI protection had just grown toward."""

    def _context(self):
        # Real case: a full-height crop whose ROI sits on the right, protected
        # out to x=0.915, then forced back to a 0.747-wide 3:4 window.
        return {
            "recommended_aspect_ratio": "3:4",
            "crop_box": {"enabled": True, "x": 0, "y": 0, "w": 0.75, "h": 1, "aspect_ratio": "3:4"},
            "roi_box": {"x": 0.45, "y": 0.55, "w": 0.4, "h": 0.4},
            "subject_extent_box": {"x": 0.22, "y": 0.38, "w": 0.63, "h": 0.55},
            "protected_regions": [{"label": "left rail", "x": 0.05, "y": 0.5, "w": 0.25, "h": 0.45}],
            "crop_intent": {"subject_anchor": "center", "trim_right": True},
        }

    def test_shortened_window_slides_to_keep_the_roi_inside(self):
        context = self._context()
        crop = resolve_crop_box_with_report(context["crop_box"], context, 1600, 1600)["cropBox"]

        self.assertAlmostEqual(crop["w"] / crop["h"], 3 / 4, places=2)
        roi = context["roi_box"]
        self.assertLessEqual(crop["x"], roi["x"])
        self.assertGreaterEqual(crop["x"] + crop["w"], roi["x"] + roi["w"])
        # It slid right rather than staying pinned at the frame's left edge.
        self.assertGreater(crop["x"], 0)

    def test_sliding_never_changes_the_fitted_size(self):
        context = self._context()
        pinned = resolve_crop_box_with_report(context["crop_box"], context, 1600, 1600)["cropBox"]
        # Same geometry, ROI moved to the left half: the window must end up
        # somewhere else, at exactly the same size.
        context["roi_box"] = {"x": 0.05, "y": 0.55, "w": 0.4, "h": 0.4}
        context["subject_extent_box"] = {"x": 0.05, "y": 0.38, "w": 0.5, "h": 0.55}
        moved = resolve_crop_box_with_report(context["crop_box"], context, 1600, 1600)["cropBox"]

        self.assertAlmostEqual(pinned["w"], moved["w"], places=6)
        self.assertAlmostEqual(pinned["h"], moved["h"], places=6)
        self.assertLess(moved["x"], pinned["x"])

    def test_window_too_small_for_everything_still_covers_the_roi(self):
        # ROI plus a protected region on the far side span more than the window
        # can hold: the ROI wins and the protected region takes the loss.
        context = self._context()
        context["protected_regions"] = [{"label": "far left", "x": 0.0, "y": 0.5, "w": 0.1, "h": 0.45}]
        crop = resolve_crop_box_with_report(context["crop_box"], context, 1600, 1600)["cropBox"]

        roi = context["roi_box"]
        self.assertLessEqual(crop["x"], roi["x"])
        self.assertGreaterEqual(crop["x"] + crop["w"], roi["x"] + roi["w"])


class DerivedAspectRatioTests(unittest.TestCase):
    """Auto mode decides the output ratio from the crop rectangle the analysis
    proposed, not from the ratio label it wrote -- the two used to disagree."""

    def test_ratio_comes_from_the_rectangle_not_from_the_label(self):
        # A real failure: 0.85 x 1.00 on a square photo is 0.85:1 (portrait),
        # and the analysis labelled it "3:2".
        crop = {"enabled": True, "x": 0.15, "y": 0, "w": 0.85, "h": 1, "aspect_ratio": "3:2"}
        label, measured = derive_aspect_ratio_from_crop(crop, 1600, 1600)
        self.assertEqual(label, "4:5")
        self.assertAlmostEqual(measured, 0.85, places=6)

    def test_a_crop_close_to_the_source_ratio_keeps_the_original(self):
        crop = {"enabled": True, "x": 0.05, "y": 0, "w": 0.9, "h": 1, "aspect_ratio": "3:2"}
        self.assertEqual(derive_aspect_ratio_from_crop(crop, 1600, 1600)[0], "original")
        # Same rectangle on a 3:2 source measures 1.35 -> 4:3 is closer than 3:2.
        self.assertEqual(derive_aspect_ratio_from_crop(crop, 1200, 800)[0], "4:3")

    def test_16_9_and_9_16_need_a_source_at_least_that_elongated(self):
        # 0.80 x 0.45 of a square measures 1.778 -- exactly 16:9, which would
        # win outright. The square source is nowhere near that wide, so 3:2
        # takes it instead.
        wide = {"enabled": True, "x": 0.1, "y": 0.275, "w": 0.8, "h": 0.45}
        self.assertEqual(derive_aspect_ratio_from_crop(wide, 1600, 1600)[0], "3:2")
        tall = {"enabled": True, "x": 0.275, "y": 0.1, "w": 0.45, "h": 0.8}
        self.assertEqual(derive_aspect_ratio_from_crop(tall, 1600, 1600)[0], "2:3")

        # A 7:4 photo (1.75) is within tolerance of 16:9, so a crop of it that
        # measures 1.9 -- nearer 16:9 than to the source's own ratio -- may snap
        # there. On the square photo above the same crop could not.
        near_16_9 = {"enabled": True, "x": 0.025, "y": 0.0625, "w": 0.95, "h": 0.875}
        self.assertEqual(derive_aspect_ratio_from_crop(near_16_9, 1400, 800)[0], "16:9")

    def test_allowed_labels_drop_only_the_extremes(self):
        square = allowed_auto_ratio_labels(1.0)
        self.assertNotIn("16:9", square)
        self.assertNotIn("9:16", square)
        self.assertEqual(
            [l for l in AUTO_ASPECT_RATIO_LABELS if l not in ("16:9", "9:16")], list(square)
        )
        # Exactly 16:9 / 9:16 qualifies, and so does anything more elongated
        # than that in the same direction (21:9, 1:2) -- those photos are
        # already past the extreme, so offering it takes nothing extra away.
        self.assertIn("16:9", allowed_auto_ratio_labels(16 / 9))
        self.assertIn("16:9", allowed_auto_ratio_labels(21 / 9))
        self.assertIn("9:16", allowed_auto_ratio_labels(9 / 16))
        self.assertIn("9:16", allowed_auto_ratio_labels(1 / 2))
        # Less elongated stays out, and neither extreme unlocks its transpose.
        self.assertNotIn("16:9", allowed_auto_ratio_labels(3 / 2))
        self.assertNotIn("9:16", allowed_auto_ratio_labels(2 / 3))
        self.assertNotIn("9:16", allowed_auto_ratio_labels(21 / 9))
        self.assertNotIn("16:9", allowed_auto_ratio_labels(1 / 2))

    def test_allowed_labels_drop_ratios_a_crop_cannot_reach_without_losing_too_much(self):
        portrait = allowed_auto_ratio_labels(2 / 3)
        self.assertIn("1:1", portrait)  # keeps 67% of a 2:3 photo
        for label in ("3:2", "4:3", "5:4"):
            self.assertNotIn(label, portrait)
        landscape = allowed_auto_ratio_labels(4 / 3)
        self.assertIn("1:1", landscape)
        self.assertNotIn("4:5", landscape)  # keeps 60%
        self.assertNotIn("3:4", landscape)

    def test_flipped_crop_snaps_to_the_nearest_allowed_ratio(self):
        landscape_crop = {"enabled": True, "x": 0.0, "y": 0.2, "w": 1.0, "h": 4 / 9}
        self.assertEqual(derive_aspect_ratio_from_crop(landscape_crop, 1000, 1500)[0], "1:1")

    def test_no_usable_crop_means_original(self):
        self.assertEqual(derive_aspect_ratio_from_crop(None, 1600, 1600), ("original", None))
        self.assertEqual(
            derive_aspect_ratio_from_crop({"enabled": False, "w": 0.5, "h": 0.5}, 1600, 1600),
            ("original", None),
        )
        self.assertEqual(
            derive_aspect_ratio_from_crop({"enabled": True, "w": 0, "h": 1}, 1600, 1600),
            ("original", None),
        )

    def test_apply_rewrites_every_ratio_field_and_leaves_the_input_alone(self):
        analysis = {
            "recommended_aspect_ratio": "3:2",
            "aspect_ratio_reason": "模型自己的理由",
            "crop_box": {"enabled": True, "x": 0.15, "y": 0, "w": 0.85, "h": 1, "aspect_ratio": "3:2"},
            "crop_candidates": [
                {"id": "c1", "crop_box": {"enabled": True, "x": 0, "y": 0.2, "w": 1, "h": 0.6, "aspect_ratio": "3:2"}},
            ],
        }
        before = copy.deepcopy(analysis)
        applied = apply_derived_aspect_ratio(analysis, 1600, 1600)

        self.assertEqual(applied["recommended_aspect_ratio"], "4:5")
        self.assertEqual(applied["crop_box"]["aspect_ratio"], "4:5")
        # Candidates are alternative framings, each measured on its own:
        # 1.00 x 0.60 of a square is 1.667 -- 3:2, since 16:9 is only offered to
        # photos already at least that wide.
        self.assertEqual(applied["crop_candidates"][0]["crop_box"]["aspect_ratio"], "3:2")
        self.assertEqual(applied["aspect_ratio_source"], "crop_box_geometry")
        self.assertIn("4:5", applied["aspect_ratio_reason"])
        self.assertEqual(analysis, before)

    def test_derived_ratio_lets_the_crop_survive_instead_of_being_replaced(self):
        # With the model's own "3:2" the crop could not be reconciled with the
        # canvas at all, so it was dropped and a middle band was invented.
        analysis = {
            "recommended_aspect_ratio": "3:2",
            "crop_box": {"enabled": True, "x": 0.15, "y": 0, "w": 0.85, "h": 1, "aspect_ratio": "3:2"},
            "roi_box": {"x": 0.4, "y": 0.6, "w": 0.5, "h": 0.35},
            "protected_regions": [],
        }
        applied = apply_derived_aspect_ratio(analysis, 1600, 1600)
        crop = resolve_crop_box_with_report(applied["crop_box"], applied, 1600, 1600)["cropBox"]

        self.assertIsNotNone(crop)
        self.assertAlmostEqual(crop["w"] / crop["h"], 4 / 5, places=3)
        # The ROI (y 0.60-0.95) stays inside the frame, which the 3:2 label's
        # forced middle band (y 0.167-0.833) did not manage.
        self.assertLessEqual(crop["y"], 0.6)
        self.assertGreaterEqual(crop["y"] + crop["h"], 0.95)

    @staticmethod
    def _review_analysis(crop_box, *, roi_box=None, subject_extent_box=None, protected_regions=None):
        return {
            "recommended_aspect_ratio": "3:2",
            "crop_box": crop_box,
            "roi_box": roi_box or {"x": 0.35, "y": 0.3, "w": 0.3, "h": 0.35},
            "subject_extent_box": subject_extent_box or {"x": 0.25, "y": 0.2, "w": 0.5, "h": 0.6},
            "protected_regions": protected_regions or [],
            "crop_recommendation": "改變長寬比並收窄左右空間",
            "correction_plan": ["調整為3:2並收窄左右空間", "提升整體曝光"],
            "correction_axes": [["crop"], ["exposure"]],
            "correction_inverse": ["維持原圖構圖", "維持原圖曝光"],
            "retouch_recipe": ["重新構圖：裁成3:2，收窄左右空間", "稍微提亮照片"],
        }

    def test_geometry_inserts_the_resolved_ratio_into_review_text(self):
        analysis = self._review_analysis(
            {"enabled": True, "x": 0.15, "y": 0, "w": 0.85, "h": 1, "aspect_ratio": "3:2"}
        )

        applied = apply_derived_aspect_ratio(analysis, 1600, 1600)

        self.assertEqual(applied["recommended_aspect_ratio"], "4:5")
        self.assertTrue(applied["crop_box"]["enabled"])
        self.assertAlmostEqual(applied["crop_box"]["w"] / applied["crop_box"]["h"], 4 / 5, places=3)
        self.assertTrue(applied["correction_plan"][0].startswith("以4:5輸出比例重新構圖；"))
        self.assertEqual(applied["retouch_recipe"][0], "重新構圖：調整為4:5，收窄左右空間")

    def test_user_ratio_change_refits_source_geometry_and_rewrites_text(self):
        analysis = self._review_analysis(
            {"enabled": True, "x": 0.15, "y": 0, "w": 0.85, "h": 1, "aspect_ratio": "3:2"}
        )
        auto = apply_derived_aspect_ratio(analysis, 1600, 1600)

        changed = apply_derived_aspect_ratio(auto, 1600, 1600, aspect_ratio_pref="3:2")

        self.assertEqual(changed["recommended_aspect_ratio"], "3:2")
        self.assertAlmostEqual(changed["crop_box"]["w"] / changed["crop_box"]["h"], 3 / 2, places=3)
        self.assertIn("重新構圖：調整為3:2，", changed["retouch_recipe"][0])
        self.assertNotIn("4:5", changed["retouch_recipe"][0])

    def test_explicit_protection_expanding_crop_to_full_frame_removes_reframe_item(self):
        analysis = self._review_analysis(
            {"enabled": True, "x": 0.05, "y": 0, "w": 0.9, "h": 1, "aspect_ratio": "4:5"},
            roi_box={"x": 0.245, "y": 0.27, "w": 0.48, "h": 0.55},
            subject_extent_box={"x": 0.14, "y": 0.247, "w": 0.76, "h": 0.753},
            protected_regions=[
                {"x": 0.02, "y": 0.02, "w": 0.96, "h": 0.96},
            ],
        )

        applied = apply_derived_aspect_ratio(analysis, 1600, 1600)

        self.assertEqual(applied["recommended_aspect_ratio"], "original")
        self.assertFalse(applied["crop_box"]["enabled"])
        self.assertEqual(applied["correction_plan"], ["提升整體曝光"])
        self.assertEqual(applied["correction_axes"], [["exposure"]])
        self.assertEqual(applied["correction_inverse"], ["維持原圖曝光"])
        self.assertEqual(applied["retouch_recipe"], ["稍微提亮照片"])

    def test_original_ratio_still_keeps_a_meaningful_same_ratio_crop(self):
        analysis = self._review_analysis(
            {"enabled": True, "x": 0.05, "y": 0.05, "w": 0.9, "h": 0.9, "aspect_ratio": "original"}
        )

        applied = apply_derived_aspect_ratio(
            analysis, 1600, 1600, aspect_ratio_pref="original"
        )

        self.assertEqual(applied["recommended_aspect_ratio"], "original")
        self.assertTrue(applied["crop_box"]["enabled"])
        self.assertLess(applied["crop_box"]["w"], 0.965)
        self.assertLess(applied["crop_box"]["h"], 0.965)
        self.assertTrue(applied["correction_plan"][0].startswith("維持原圖輸出比例並重新構圖；"))
        self.assertEqual(applied["retouch_recipe"][0], "重新構圖：維持原圖比例，收窄左右空間")

    def test_edge_touching_subject_extent_does_not_cancel_a_valid_crop(self):
        # Production regression: image 2 in the keep-original-ratio reframe
        # batch. The analyzer selected a useful square crop, but its semantic
        # subject extent reached the already-truncated right edge. Treating
        # that extent as hard protection expanded the crop to 98.8% and then
        # disabled it as effectively full-frame.
        analysis = self._review_analysis(
            {"enabled": True, "x": 0.08, "y": 0.08, "w": 0.84, "h": 0.84, "aspect_ratio": "original"},
            roi_box={"x": 0.305, "y": 0.375, "w": 0.49, "h": 0.43},
            subject_extent_box={"x": 0.305, "y": 0.375, "w": 0.695, "h": 0.554},
            protected_regions=[
                {"x": 0.311, "y": 0.382, "w": 0.344, "h": 0.426},
                {"x": 0.758, "y": 0.431, "w": 0.129, "h": 0.197},
                {"x": 0.31, "y": 0.775, "w": 0.48, "h": 0.18},
                {"x": 0.164, "y": 0.42, "w": 0.567, "h": 0.036},
            ],
        )

        applied = apply_derived_aspect_ratio(
            analysis, 1600, 1600, aspect_ratio_pref="original"
        )

        self.assertTrue(applied["crop_box"]["enabled"])
        self.assertLess(applied["crop_box"]["w"], 0.965)
        self.assertLess(applied["crop_box"]["h"], 0.965)

    def test_edge_touching_explicit_region_repositions_same_ratio_crop(self):
        # Production regression: 20260915-104931 proposed a 90% square crop.
        # A hard region touching the right and bottom edges should move that
        # square to the corner, not expand it back toward the complete frame.
        analysis = self._review_analysis(
            {"enabled": True, "x": 0.05, "y": 0.05, "w": 0.9, "h": 0.9, "aspect_ratio": "original"},
            roi_box={"x": 0.292, "y": 0.382, "w": 0.357, "h": 0.444},
            subject_extent_box={"x": 0.292, "y": 0.382, "w": 0.708, "h": 0.618},
            protected_regions=[
                {"x": 0.689, "y": 0.389, "w": 0.311, "h": 0.611},
                {"x": 0.315, "y": 0.425, "w": 0.334, "h": 0.401},
                {"x": 0.321, "y": 0.708, "w": 0.679, "h": 0.292},
            ],
        )

        applied = apply_derived_aspect_ratio(
            analysis, 1248, 1248, aspect_ratio_pref="original"
        )

        self.assertTrue(applied["crop_box"]["enabled"])
        self.assertAlmostEqual(applied["crop_box"]["x"], 0.1, places=6)
        self.assertAlmostEqual(applied["crop_box"]["y"], 0.1, places=6)
        self.assertAlmostEqual(applied["crop_box"]["w"], 0.9, places=6)
        self.assertAlmostEqual(applied["crop_box"]["h"], 0.9, places=6)


class OutputGeometryCheckTests(unittest.TestCase):
    """The deterministic post-check: a stretched result is arithmetic, so it is
    measured rather than left to the QA model's judgment."""

    def test_consistent_crop_and_canvas_produce_no_warning(self):
        crop = {"x": 0.1, "y": 0.0, "w": 0.4, "h": 0.9}   # 0.4*1200 : 0.9*800 = 2:3
        self.assertEqual(
            output_geometry_warnings(_image_b64(848, 1264), "848:1264", crop, 1200, 800),
            [],
        )

    def test_crop_rectangle_disagreeing_with_requested_ratio_warns(self):
        crop = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}   # 3:2, while 4:5 was requested
        warnings = output_geometry_warnings(_image_b64(928, 1152), "4:5", crop, 1200, 800)
        self.assertEqual(len(warnings), 1)
        self.assertIn("裁切框比例", warnings[0])

    def test_returned_canvas_disagreeing_with_requested_ratio_warns(self):
        crop = {"x": 0.1, "y": 0.0, "w": 0.4, "h": 0.9}   # matches the request
        warnings = output_geometry_warnings(_image_b64(1264, 848), "848:1264", crop, 1200, 800)
        self.assertEqual(len(warnings), 1)
        self.assertIn("輸出畫布比例", warnings[0])

    def test_unparseable_label_falls_back_to_the_source_frame(self):
        # "original"/blank means "the source frame is the request".
        self.assertEqual(output_geometry_warnings(_image_b64(1200, 800), "original", None, 1200, 800), [])
        warnings = output_geometry_warnings(_image_b64(800, 1200), "original", None, 1200, 800)
        self.assertEqual(len(warnings), 1)
        self.assertIn("輸出畫布比例", warnings[0])

    def test_grid_snapping_drift_stays_under_the_tolerance(self):
        # 4:3 source -> gpt-image-2 asks for 1456x1088 (1.338): ~0.4% off, not a finding.
        self.assertEqual(
            output_geometry_warnings(_image_b64(1456, 1088), "1456:1088", None, 4032, 3024),
            [],
        )

    def test_undecodable_image_does_not_break_the_run(self):
        self.assertEqual(
            output_geometry_warnings(base64.b64encode(b"not an image").decode(), "4:5", None, 1200, 800),
            [],
        )


class OpenAIAspectRatioTests(unittest.TestCase):
    def setUp(self):
        """These assert the crop-only wording, so they must not read whatever
        the expand knobs happen to be turned to (see ExpandPermissionTests)."""
        patcher = patch.object(prompts, "EXPAND_PERMISSION_RETAINED_THRESHOLD", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_extreme_requested_ratio_stays_within_openai_three_to_one_limit(self):
        for width, height in ((1, 8), (8, 1)):
            output_width, output_height = (
                int(value) for value in openai_edit_size_for_ratio(width, height).split("x")
            )
            self.assertLessEqual(
                max(output_width, output_height) / min(output_width, output_height),
                3.0,
            )
            self.assertEqual(output_width % 16, 0)
            self.assertEqual(output_height % 16, 0)

    def test_output_scale_sizes_stay_within_openai_limits(self):
        ratios = [(1, 8), (1, 3), (9, 16), (2, 3), (3, 4), (4, 5), (1, 1), (5, 4), (4, 3), (3, 2), (16, 9), (3, 1), (8, 1)]
        for scale in ("1K", "2K", "3K", "4K"):
            for width, height in ratios:
                with self.subTest(scale=scale, ratio=f"{width}:{height}"):
                    w, h = (int(v) for v in openai_edit_size_for_ratio(width, height, scale).split("x"))
                    self.assertEqual((w % 16, h % 16), (0, 0))
                    self.assertLessEqual(max(w, h), 3840)
                    self.assertLessEqual(max(w, h) / min(w, h), 3.0)
                    self.assertGreaterEqual(w * h, 655_360)
                    self.assertLessEqual(w * h, 8_294_400)

    def test_output_scale_grows_the_edges_and_1x_is_the_default(self):
        for width, height in ((1, 1), (16, 9), (4, 5)):
            self.assertEqual(openai_edit_size_for_ratio(width, height), openai_edit_size_for_ratio(width, height, "1K"))
            base_w, _ = (int(v) for v in openai_edit_size_for_ratio(width, height).split("x"))
            big_w, _ = (int(v) for v in openai_edit_size_for_ratio(width, height, "3K").split("x"))
            self.assertAlmostEqual(big_w / base_w, 2.0, delta=0.05)
        self.assertEqual(openai_edit_size_for_ratio(3, 2, "unknown"), openai_edit_size_for_ratio(3, 2))
        self.assertEqual(openai_edit_size_for_ratio(16, 9, "4K"), "3840x2160")

    @patch.object(
        openai_client,
        "_post_image_edit",
        return_value={"data": [{"b64_json": base64.b64encode(b"result").decode()}]},
    )
    def test_output_scale_reaches_the_size_param(self, post_edit):
        openai_client.edit_photo(
            _image_bytes(), _analysis("1:1", crop_enabled=False), model="gpt-image-2", aspect_ratio="auto",
            output_scale="3K",
        )
        w, h = (int(v) for v in post_edit.call_args.args[3].split("x"))
        self.assertEqual(w, h)
        self.assertGreater(w, 2000)

    @patch.object(
        openai_client,
        "_post_image_edit",
        return_value={"data": [{"b64_json": base64.b64encode(b"result").decode()}]},
    )
    def test_auto_uses_concrete_size_and_does_not_mutate_analysis(self, post_edit):
        analysis = _analysis("4:5", crop_enabled=False)
        original_analysis = copy.deepcopy(analysis)

        _, prompt = openai_client.edit_photo(
            _image_bytes(), analysis, model="gpt-image-2", aspect_ratio="auto"
        )

        _, _, _, size_param, _ = post_edit.call_args.args
        self.assertNotEqual(size_param, "auto")
        width, height = (int(value) for value in size_param.split("x"))
        self.assertAlmostEqual(width / height, 4 / 5, delta=0.02)
        self.assertIn("as a straight cut from the photograph", prompt)
        self.assertIn("everything kept keeps its source proportions", prompt)
        self.assertNotIn("frame-relative subject scale", prompt)
        self.assertEqual(analysis, original_analysis)

    @patch.object(
        openai_client,
        "_post_image_edit",
        return_value={"data": [{"b64_json": base64.b64encode(b"result").decode()}]},
    )
    def test_auto_original_recommendation_uses_source_ratio_not_auto(self, post_edit):
        openai_client.edit_photo(
            _image_bytes(1200, 800),
            _analysis("original", crop_enabled=False),
            model="gpt-image-2",
            aspect_ratio="auto",
        )

        size_param = post_edit.call_args.args[3]
        width, height = (int(value) for value in size_param.split("x"))
        self.assertAlmostEqual(width / height, 1200 / 800, delta=0.02)


class GeminiAspectRatioTests(unittest.TestCase):
    @patch.object(
        gemini_client,
        "_post_generate_content",
        return_value={
            "candidates": [
                {"content": {"parts": [{"inlineData": {"data": base64.b64encode(b"result").decode()}}]}}
            ]
        },
    )
    def test_auto_sets_matching_image_config_and_keeps_analysis_immutable(self, post_generate):
        analysis = _analysis("4:5", crop_enabled=False)
        original_analysis = copy.deepcopy(analysis)

        _, prompt = gemini_client.edit_photo(
            _image_bytes(), analysis, model=gemini_client.NANO_BANANA_2_MODEL, aspect_ratio="auto"
        )

        payload = post_generate.call_args.args[1]
        self.assertEqual(payload["generationConfig"]["imageConfig"]["aspectRatio"], "4:5")
        # Gemini's actual 1K raster for 4:5 is 928x1152; crop geometry follows
        # that physical ratio, and the prompt names it by the common label.
        self.assertIn("The finished picture is a 4:5 portrait frame", prompt)
        self.assertIn("everything kept keeps its source proportions", prompt)
        self.assertEqual(analysis, original_analysis)

    @patch.object(
        gemini_client,
        "_post_generate_content",
        return_value={
            "candidates": [
                {"content": {"parts": [{"inlineData": {"data": base64.b64encode(b"result").decode()}}]}}
            ]
        },
    )
    def test_lite_keeps_supported_extreme_ratio_and_forces_1k(self, post_generate):
        gemini_client.edit_photo(
            _image_bytes(),
            _analysis("1:8", crop_enabled=False),
            quality="high",
            model=gemini_client.NANO_BANANA_LITE_MODEL,
            aspect_ratio="auto",
        )

        image_config = post_generate.call_args.args[1]["generationConfig"]["imageConfig"]
        self.assertEqual(image_config["aspectRatio"], "1:8")
        self.assertEqual(image_config["imageSize"], "1K")


class GeometryWarningWiringTests(unittest.TestCase):
    """The measurement is only useful if it reaches the report."""

    def test_edit_photo_reports_a_deformed_canvas(self):
        # 4:5 was requested (and the crop was fitted to it); a landscape canvas
        # comes back instead.
        with patch.object(
            openai_client,
            "_post_image_edit",
            return_value={"data": [{"b64_json": _image_b64(1536, 1024)}]},
        ):
            result, _ = openai_client.edit_photo(
                _image_bytes(1200, 800), _analysis("4:5"), model="gpt-image-2", aspect_ratio="auto"
            )
        self.assertTrue(any("輸出畫布比例" in w for w in result["geometry_warnings"]))

    def test_finish_stage_merges_geometry_warnings_into_qa(self):
        import pipeline

        def _fake_edit(image_bytes, analysis, quality, preset_key, **kwargs):
            return {"image_b64": "x", "usage": None, "geometry_warnings": ["測試用幾何警告"]}, "prompt"

        with patch.dict(pipeline.EDIT_PHOTO_BY_MODEL, {"gpt-image-2": _fake_edit}), \
                patch.object(pipeline, "review_user_instruction", return_value=({}, None, None)), \
                patch.object(pipeline, "quality_check_photo", return_value=({"pass": True, "warnings": ["既有警告"]}, "qa prompt", None)):
            out = pipeline.finish_stage(_image_bytes(), _analysis("4:5"), "", "natural", "medium")

        self.assertEqual(out["result_text"]["quality_check"]["warnings"], ["既有警告", "測試用幾何警告"])

    def test_finish_stage_reports_geometry_even_when_qa_is_skipped(self):
        import pipeline

        def _fake_edit(image_bytes, analysis, quality, preset_key, **kwargs):
            return {"image_b64": "x", "usage": None, "geometry_warnings": ["測試用幾何警告"]}, "prompt"

        with patch.dict(pipeline.EDIT_PHOTO_BY_MODEL, {"gpt-image-2": _fake_edit}), \
                patch.object(pipeline, "review_user_instruction", return_value=({}, None, None)):
            out = pipeline.finish_stage(
                _image_bytes(), _analysis("4:5"), "", "natural", "medium", run_qa=False
            )

        self.assertEqual(out["result_text"]["quality_check"]["warnings"], ["測試用幾何警告"])


def _policy_block(instruction):
    return instruction.split("## Aspect-ratio policy")[-1]


class AnalysisAspectRatioPolicyTests(unittest.TestCase):
    """The analysis prompt's three branches."""

    BUILDERS = (build_overall_analysis_instruction,)

    def test_a_fixed_ratio_is_planned_for_not_merely_labelled(self):
        for build in self.BUILDERS:
            with self.subTest(build=build.__name__):
                policy = _policy_block(build("4:5"))
                self.assertIn("The user explicitly requested 4:5", policy)
                self.assertIn("Plan the composition for a 4:5 frame", policy)
                # The supported-set wording belongs to auto, where the model
                # still picks; here the pick is already made.
                self.assertNotIn("Select recommended_aspect_ratio only from", policy)

    def test_a_fixed_ratio_closes_the_fall_back_to_original_escape_hatch(self):
        # The instruction body tells the model to answer "original" when no
        # reframing looks beneficial, which would discard the user's choice.
        for build in self.BUILDERS:
            with self.subTest(build=build.__name__):
                policy = _policy_block(build("16:9"))
                self.assertIn('Never answer "original"', policy)
                self.assertIn("that fallback does not apply", policy)

    def test_auto_and_original_are_unchanged_by_the_new_branch(self):
        for build in self.BUILDERS:
            for pref, marker in (("auto", "Select recommended_aspect_ratio only from"),
                                 ("original", "keep the original photo's aspect ratio")):
                with self.subTest(build=build.__name__, pref=pref):
                    self.assertIn(marker, _policy_block(build(pref)))

    def test_auto_policy_lists_only_the_ratios_allowed_for_this_photo(self):
        for build in self.BUILDERS:
            with self.subTest(build=build.__name__):
                policy = _policy_block(build("auto", source_size=(1000, 1500)))
                self.assertIn("supported output set: original, 1:1, 2:3, 3:4, 4:5.", policy)


class AspectRatioReasonTests(unittest.TestCase):
    """Whether the ratio was planned for or fitted on afterwards."""

    def _reason(self, pref, pref_at_analysis):
        analysis = _analysis("3:2", crop_enabled=True)
        analysis["_aspect_ratio_pref_at_analysis"] = pref_at_analysis
        return apply_derived_aspect_ratio(
            analysis, 1200, 800, aspect_ratio_pref=pref,
        )["aspect_ratio_reason"]

    def test_analysis_planned_for_this_ratio_says_so(self):
        reason = self._reason("4:5", "4:5")
        self.assertIn("分析時即以使用者指定的輸出比例「4:5」規劃構圖", reason)

    def test_a_ratio_picked_after_the_analysis_is_marked_as_fitted_on(self):
        reason = self._reason("4:5", "auto")
        self.assertIn("使用者在分析後改選輸出比例「4:5」", reason)
        self.assertIn("構圖說明仍為分析時比例所寫", reason)

    def test_original_no_longer_reads_as_a_named_output_ratio(self):
        self.assertIn("使用者選擇維持原圖比例", self._reason("original", "original"))


class ExpandPermissionTests(unittest.TestCase):
    """The gate that lets the edit model reach the output shape by extending
    the scene instead of cropping its way there."""

    SQUARE = (1600, 1600)
    PLAN = {"x": 0.198, "y": 0.0, "w": 0.5625, "h": 1.0, "aspectRatio": "9:16"}

    def setUp(self):
        """Pin both knobs to their intended values: the module's own may be
        turned up for hands-on testing, and these assert the logic, not that."""
        for name, value in (
            ("EXPAND_PERMISSION_RETAINED_THRESHOLD", 1.0),
            ("EXPAND_PERMISSION_ANY_PREFERENCE", False),
        ):
            patcher = patch.object(prompts, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _block(self, allow_expand, plan=None, source_size=None):
        return prompts._build_crop_instruction_block(
            plan or self.PLAN, source_size=source_size or self.SQUARE,
            allow_expand=allow_expand,
        )

    def test_analyzer_is_asked_for_every_shape_that_differs_at_all(self):
        """The size of the gap does not decide -- a square filled edge to edge
        by its subject can want extending at 3:4, where a crop costs 25%."""
        for label in ("9:16", "3:4", "4:5"):
            self.assertTrue(expand_permitted(label, label, self.SQUARE), label)
        self.assertTrue(expand_permitted("16:9", "16:9", (960, 1280)))

    def test_nothing_to_decide_when_the_canvas_is_the_photograph_s_shape(self):
        self.assertFalse(expand_permitted("1:1", "1:1", self.SQUARE))
        # Within the shape-comparison tolerance, so not a different shape.
        self.assertFalse(expand_permitted("4:5", "4:5", (1122, 1402)))

    def test_a_shape_the_user_did_not_name_is_not_asked(self):
        self.assertFalse(expand_permitted("auto", "9:16", self.SQUARE))
        self.assertFalse(expand_permitted("original", "9:16", self.SQUARE))

    def test_threshold_of_zero_keeps_every_ratio_crop_only(self):
        with patch.object(prompts, "EXPAND_PERMISSION_RETAINED_THRESHOLD", 0):
            self.assertFalse(expand_permitted("9:16", "9:16", self.SQUARE))

    def test_lowering_the_threshold_restricts_the_question_to_wide_gaps(self):
        with patch.object(prompts, "EXPAND_PERMISSION_RETAINED_THRESHOLD", 0.6):
            self.assertTrue(expand_permitted("9:16", "9:16", self.SQUARE))
            self.assertFalse(expand_permitted("3:4", "3:4", self.SQUARE))

    def test_any_preference_knob_reaches_auto(self):
        with patch.object(prompts, "EXPAND_PERMISSION_ANY_PREFERENCE", True):
            self.assertTrue(expand_permitted("auto", "9:16", self.SQUARE))

    def test_closed_gate_leaves_the_crop_only_wording_untouched(self):
        block = self._block(allow_expand=False)
        self.assertIn("left and right edges cannot both be inside it", block)
        self.assertNotIn("past the photograph", block)
        self.assertIn("Two operations", block)

    def test_open_gate_offers_extending_with_cropping_still_the_default(self):
        block = self._block(allow_expand=True)
        self.assertIn("Cropping is the default answer", block)
        self.assertIn("If the subject already fills the width", block)
        self.assertIn("Three operations", block)
        # The crop-only claim is false once extending is on the table.
        self.assertNotIn("cannot both be inside it", block)

    def test_open_gate_still_forbids_padding_and_foreign_scenery(self):
        block = self._block(allow_expand=True)
        for banned in ("blank borders", "checkerboard padding", "empty bands",
                       "stretched edges", "not already part of this place"):
            self.assertIn(banned, block)

    def test_compact_prompt_carries_the_permission_through(self):
        """The default editor strategy is resolved_compact, which rebuilds the
        crop sentence from scratch -- a permission it does not know about is
        dropped without a trace."""
        compact = _compact_raw_crop_instruction(
            self._block(allow_expand=True), crop_plan=self.PLAN,
            approved_crop_correction=True,
        )
        self.assertIn("continue the same real scene past the photograph", compact)
        self.assertIn("Crop or extend the scene", compact)

        crop_only = _compact_raw_crop_instruction(
            self._block(allow_expand=False), crop_plan=self.PLAN,
            approved_crop_correction=True,
        )
        self.assertNotIn("past the photograph", crop_only)
        self.assertIn("as a straight cut from the photograph", crop_only)

    def test_analysis_policy_asks_the_analyzer_to_decide(self):
        wide = build_overall_analysis_instruction("9:16", source_size=self.SQUARE)
        self.assertIn("Decide reframe_mode before sacrificing subjects to fit the ratio.", wide)
        self.assertIn("removes 44% of the photograph's width", wide)
        self.assertIn("beyond the top and bottom edges, adds at most 35% to the height", wide)
        # A modest gap is asked too: the decision is about the picture.
        modest = build_overall_analysis_instruction("3:4", source_size=self.SQUARE)
        self.assertIn("Decide reframe_mode before sacrificing subjects to fit the ratio.", modest)
        self.assertIn("removes 25% of the photograph's width", modest)
        # Not a shape the user named, and a shape with nothing to extend: the
        # field is still declared (with its "crop" default), the question is not.
        for pref in ("auto", "1:1"):
            self.assertNotIn(
                "Decide reframe_mode before sacrificing subjects to fit the ratio.",
                build_overall_analysis_instruction(pref, source_size=self.SQUARE),
            )

    def test_reframe_mode_is_a_two_value_enum_with_a_stated_default(self):
        """Free text here produced "original", "preserve_original" and "裁切"
        across 45 analyses: strict mode requires the field on every call, so a
        run whose policy never mentions it was filling it blind."""
        self.assertEqual(
            prompts.ANALYSIS_RESPONSE_SCHEMA_OVERALL["schema"]["properties"]["reframe_mode"]["enum"],
            ["crop", "expand"],
        )
        # Named in the skeleton, so the default is stated even on the calls
        # the expand policy is not injected into.
        for pref in ("auto", "original", "9:16"):
            instruction = build_overall_analysis_instruction(pref, source_size=self.SQUARE)
            self.assertIn('"reframe_mode"', instruction)
            self.assertIn('一律填', instruction)

    def test_unknown_source_size_keeps_the_analysis_crop_only(self):
        """The choice depends on how far this shape is from the source's, so
        an analysis that cannot be told the source shape must not be asked."""
        self.assertNotIn(
            "Decide reframe_mode before sacrificing subjects to fit the ratio.", build_overall_analysis_instruction("9:16"))

    def test_extension_plan_splits_the_gap_at_the_cap(self):
        box = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        # 1:1 -> 3:4 needs 1.33, under the cap: extend only, crop nothing.
        under = plan_reframe_extension(box, 1600, 1600, "3:4")
        self.assertEqual(under["axis"], "height")
        self.assertFalse(under["capped"])
        self.assertAlmostEqual(under["kept_other_axis"], 1.0, places=3)
        # 1:1 -> 9:16 needs 1.78: capped, so the rest still comes off the width.
        over = plan_reframe_extension(box, 1600, 1600, "9:16")
        self.assertTrue(over["capped"])
        self.assertAlmostEqual(over["extension"], image_tools.REFRAME_MAX_EXTENSION, places=3)
        self.assertLess(over["kept_other_axis"], 1.0)
        # Still much gentler on the picture than cropping the whole way.
        self.assertGreater(over["kept_other_axis"], 0.5625)
        # A rectangle already at the target has nothing to extend.
        self.assertIsNone(plan_reframe_extension(
            {"x": 0.2, "y": 0.0, "w": 0.5625, "h": 1.0}, 1600, 1600, "9:16"))

    def test_expand_mode_keeps_the_rectangle_off_the_canvas_shape(self):
        """Refitting the crop to the canvas is what silently turns the decision
        back into a crop -- it happens again in the edit client."""
        analysis = {
            "reframe_mode": "expand",
            "crop_box": {"enabled": True, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
            "roi_box": {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8},
        }
        resolved = apply_derived_aspect_ratio(analysis, 1600, 1600, aspect_ratio_pref="9:16")
        plan = resolved["reframe_extension"]
        self.assertIsNotNone(plan)
        self.assertEqual(plan["axis"], "height")
        box = resolved["crop_box"]
        self.assertGreater(box["w"] / box["h"], 0.5625 * 1.2)
        self.assertEqual(
            crop_resolution_ratio_label(resolved, 1600, 1600, "9:16").count(":"), 1)
        self.assertNotEqual(
            crop_resolution_ratio_label(resolved, 1600, 1600, "9:16"), "9:16")

    def test_crop_mode_is_untouched_by_any_of_this(self):
        analysis = {
            "reframe_mode": "crop",
            "crop_box": {"enabled": True, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
        }
        resolved = apply_derived_aspect_ratio(analysis, 1600, 1600, aspect_ratio_pref="9:16")
        self.assertIsNone(resolved["reframe_extension"])
        self.assertEqual(crop_resolution_ratio_label(resolved, 1600, 1600, "9:16"), "9:16")

    def test_decided_plan_reaches_the_edit_model_through_the_compact_prompt(self):
        plan = plan_reframe_extension(
            {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}, 1600, 1600, "9:16")
        raw = prompts._build_crop_instruction_block(
            self.PLAN, source_size=self.SQUARE, expand_plan=plan)
        self.assertIn("is reached by continuing the scene past", raw)
        compact = _compact_raw_crop_instruction(
            raw, crop_plan=self.PLAN, approved_crop_correction=True)
        self.assertIn("continuing the same real scene past the photograph's top and bottom", compact)
        self.assertIn("further in height", compact)
        self.assertIn("of the width go from its outer edges", compact)
        self.assertNotIn("Crop and scale uniformly", compact)

    def test_full_cover_extension_still_reaches_the_editor(self):
        """The best case -- an extension that covers the whole shape difference
        and crops nothing -- used to take the "No crop." branch and hand the
        edit model "do not reframe, expand canvas, add borders" over a canvas
        of a different shape, which is the pair that guarantees a squeeze."""
        analysis = {
            "reframe_mode": "expand",
            "recommended_aspect_ratio": "1200:1600",
            "crop_box": {"enabled": True, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
        }
        resolved = apply_derived_aspect_ratio(analysis, 1600, 1600, aspect_ratio_pref="3:4")
        self.assertFalse(resolved["crop_box"]["enabled"])
        self.assertAlmostEqual(resolved["reframe_extension"]["kept_other_axis"], 1.0)
        # The requested shape is genuinely reached, so the label must not fall
        # back to "original" the way a crop that resolved to nothing does.
        self.assertEqual(resolved["recommended_aspect_ratio"], "3:4")
        self.assertIn("延伸場景", resolved["crop_box"]["reason"])

        resolved["recommended_aspect_ratio"] = "1200:1600"
        block = build_framing_sections(resolved, None, "3:4", self.SQUARE)["crop_instruction"]
        self.assertIn("continuing the scene past the photograph's top and bottom", block)
        self.assertNotIn("No crop.", block)
        compact = _compact_raw_crop_instruction(
            block, crop_plan=None, approved_crop_correction=False)
        self.assertIn("continuing the same real scene past", compact)
        # ...and nothing left in the editor prompt still forbids it.
        prompt, _ = build_compact_editor_prompt(resolved, "", "", None, raw_crop_instruction=block)
        self.assertNotIn("Keep the supplied image boundaries as the final frame", prompt)

    @staticmethod
    def _expand_review_analysis():
        return {
            "reframe_mode": "expand",
            "crop_box": {"enabled": True, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
            "correction_plan": ["裁成3:4並延伸上下天空", "提升整體曝光"],
            "correction_axes": [["crop"], ["exposure"]],
            "correction_inverse": ["維持原圖構圖", "維持原圖曝光"],
            "retouch_recipe": ["重新構圖：裁成3:4，延伸上下天空", "稍微提亮照片"],
        }

    def test_full_cover_extension_keeps_its_reframe_item(self):
        resolved = apply_derived_aspect_ratio(
            self._expand_review_analysis(), 1600, 1600, aspect_ratio_pref="3:4")
        self.assertIsNotNone(resolved["reframe_extension"])
        # A full-cover extension crops nothing but still reframes, so its item stays reviewable.
        self.assertEqual(resolved["retouch_recipe"][0], "重新構圖：調整為3:4，延伸上下天空")
        self.assertEqual(resolved["correction_axes"][0], ["crop"])

    def test_veto_checks_only_the_claim_expand_is_chosen_on(self):
        """Local geometry's veto: "any rectangle of this shape would cut into a
        subject" is arithmetic, and when it is false the crop stands."""
        # Ten people spanning the frame edge to edge -- the real run that
        # motivated this. A 3:4 crop keeps 75% of the width and must cut two.
        crowd = {"subjects": [
            {"box": {"x": 0.00, "y": 0.1, "w": 0.10, "h": 0.5}},
            {"box": {"x": 0.75, "y": 0.4, "w": 0.25, "h": 0.5}},
        ]}
        self.assertTrue(crop_would_cut_a_subject(crowd, 1600, 1600, "3:4"))
        # One subject with room around it: every shape here holds it.
        solo = {"subjects": [{"box": {"x": 0.35, "y": 0.1, "w": 0.30, "h": 0.8}}]}
        self.assertFalse(crop_would_cut_a_subject(solo, 1600, 1600, "3:4"))
        # Nothing to measure, and a canvas of the source's own shape.
        self.assertIsNone(crop_would_cut_a_subject({}, 1600, 1600, "3:4"))
        self.assertIsNone(crop_would_cut_a_subject(solo, 1600, 1600, "1:1"))

    def test_veto_turns_an_unjustified_expand_back_into_a_crop(self):
        analysis = {
            "reframe_mode": "expand",
            "subjects": [{"box": {"x": 0.35, "y": 0.1, "w": 0.30, "h": 0.8}}],
            "roi_box": {"x": 0.38, "y": 0.12, "w": 0.24, "h": 0.5},
            "crop_box": {"enabled": True, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
        }
        resolved = apply_derived_aspect_ratio(analysis, 1600, 1600, aspect_ratio_pref="3:4")
        self.assertIsNone(resolved["reframe_extension"])
        self.assertIsNotNone(resolved["reframe_mode_override"])
        # The override is on the record, not applied silently.
        self.assertIn("改以裁切達成", resolved["aspect_ratio_reason"])

    def test_veto_leaves_a_justified_expand_alone(self):
        analysis = {
            "reframe_mode": "expand",
            "subjects": [
                {"box": {"x": 0.00, "y": 0.1, "w": 0.10, "h": 0.5}},
                {"box": {"x": 0.75, "y": 0.4, "w": 0.25, "h": 0.5}},
            ],
            "crop_box": {"enabled": True, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
        }
        resolved = apply_derived_aspect_ratio(analysis, 1600, 1600, aspect_ratio_pref="3:4")
        self.assertIsNotNone(resolved["reframe_extension"])
        self.assertIsNone(resolved["reframe_mode_override"])

    def test_a_decided_crop_is_not_offered_the_permission_again(self):
        """The threshold permission is for analyses that predate the decision.
        Handing it to one that chose "crop" reopens a settled question."""
        decided = {
            "reframe_mode": "crop", "recommended_aspect_ratio": "944:1680",
            "crop_box": dict(self.PLAN),
        }
        prompt = build_framing_sections(decided, self.PLAN, "9:16", self.SQUARE)["crop_instruction"]
        self.assertNotIn("past the photograph", prompt)
        self.assertIn("cannot both be inside it", prompt)


if __name__ == "__main__":
    unittest.main()
