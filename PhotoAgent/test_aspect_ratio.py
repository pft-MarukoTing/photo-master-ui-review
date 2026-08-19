import base64
import copy
import io
import unittest
from unittest.mock import patch

from PIL import Image

import gemini_client
import openai_client
from image_tools import (
    AUTO_ASPECT_RATIO_LABELS,
    _allowed_auto_ratio_labels,
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
        square = _allowed_auto_ratio_labels(1.0)
        self.assertNotIn("16:9", square)
        self.assertNotIn("9:16", square)
        self.assertEqual(
            [l for l in AUTO_ASPECT_RATIO_LABELS if l not in ("16:9", "9:16")], list(square)
        )
        # Exactly 16:9 / 9:16 qualifies, and so does anything more elongated
        # than that in the same direction (21:9, 1:2) -- those photos are
        # already past the extreme, so offering it takes nothing extra away.
        self.assertIn("16:9", _allowed_auto_ratio_labels(16 / 9))
        self.assertIn("16:9", _allowed_auto_ratio_labels(21 / 9))
        self.assertIn("9:16", _allowed_auto_ratio_labels(9 / 16))
        self.assertIn("9:16", _allowed_auto_ratio_labels(1 / 2))
        # Less elongated stays out, and neither extreme unlocks its transpose.
        self.assertNotIn("16:9", _allowed_auto_ratio_labels(3 / 2))
        self.assertNotIn("9:16", _allowed_auto_ratio_labels(2 / 3))
        self.assertNotIn("9:16", _allowed_auto_ratio_labels(21 / 9))
        self.assertNotIn("16:9", _allowed_auto_ratio_labels(1 / 2))

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
        self.assertIn("Resizing a single axis is not available", prompt)
        self.assertIn("keeps the proportions, the tilt", prompt)
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
        # Gemini's actual 1K raster for 4:5 is 928x1152, so prompt/crop
        # geometry follows that physical ratio rather than the approximate
        # API label.
        self.assertIn("Target output aspect ratio: 928:1152", prompt)
        self.assertIn("Resizing a single axis is not available", prompt)
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

        def _fake_edit(image_bytes, analysis, quality, preset_key, aspect_ratio="auto"):
            return {"image_b64": "x", "usage": None, "geometry_warnings": ["測試用幾何警告"]}, "prompt"

        with patch.dict(pipeline.EDIT_PHOTO_BY_MODEL, {"gpt-image-2": _fake_edit}), \
                patch.object(pipeline, "review_user_instruction", return_value=({}, None, None)), \
                patch.object(pipeline, "quality_check_photo", return_value=({"pass": True, "warnings": ["既有警告"]}, "qa prompt", None)):
            out = pipeline.finish_stage(_image_bytes(), _analysis("4:5"), "", "natural", "medium")

        self.assertEqual(out["result_text"]["quality_check"]["warnings"], ["既有警告", "測試用幾何警告"])

    def test_finish_stage_reports_geometry_even_when_qa_is_skipped(self):
        import pipeline

        def _fake_edit(image_bytes, analysis, quality, preset_key, aspect_ratio="auto"):
            return {"image_b64": "x", "usage": None, "geometry_warnings": ["測試用幾何警告"]}, "prompt"

        with patch.dict(pipeline.EDIT_PHOTO_BY_MODEL, {"gpt-image-2": _fake_edit}), \
                patch.object(pipeline, "review_user_instruction", return_value=({}, None, None)):
            out = pipeline.finish_stage(
                _image_bytes(), _analysis("4:5"), "", "natural", "medium", run_qa=False
            )

        self.assertEqual(out["result_text"]["quality_check"]["warnings"], ["測試用幾何警告"])


if __name__ == "__main__":
    unittest.main()
