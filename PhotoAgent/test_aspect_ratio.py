import base64
import copy
import io
import unittest
from unittest.mock import patch

from PIL import Image

import gemini_client
import openai_client
from image_tools import (
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
        self.assertIn("Geometric fidelity is a hard constraint", prompt)
        self.assertIn("No stretching or squashing", prompt)
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
        self.assertIn("Geometric fidelity is a hard constraint", prompt)
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
                patch.object(pipeline, "review_user_instruction", return_value=({}, None)), \
                patch.object(pipeline, "quality_check_photo", return_value=({"pass": True, "warnings": ["既有警告"]}, "qa prompt")):
            out = pipeline.finish_stage(_image_bytes(), _analysis("4:5"), "", "natural", "medium")

        self.assertEqual(out["result_text"]["quality_check"]["warnings"], ["既有警告", "測試用幾何警告"])

    def test_finish_stage_reports_geometry_even_when_qa_is_skipped(self):
        import pipeline

        def _fake_edit(image_bytes, analysis, quality, preset_key, aspect_ratio="auto"):
            return {"image_b64": "x", "usage": None, "geometry_warnings": ["測試用幾何警告"]}, "prompt"

        with patch.dict(pipeline.EDIT_PHOTO_BY_MODEL, {"gpt-image-2": _fake_edit}), \
                patch.object(pipeline, "review_user_instruction", return_value=({}, None)):
            out = pipeline.finish_stage(
                _image_bytes(), _analysis("4:5"), "", "natural", "medium", run_qa=False
            )

        self.assertEqual(out["result_text"]["quality_check"]["warnings"], ["測試用幾何警告"])


if __name__ == "__main__":
    unittest.main()
