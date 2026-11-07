"""What an analysis sends and pays for: face crops at a capped size, no prompt
cache writes, and usage summed across calls."""

import io
import unittest
from unittest.mock import patch

from PIL import Image

import image_tools
import openai_client


def _photo(width, height):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "gray").save(buf, "JPEG")
    return buf.getvalue()


def _size(jpeg):
    return Image.open(io.BytesIO(jpeg)).size


class FaceCropSizeTest(unittest.TestCase):
    # 近拍的臉（幾千 px）以前原尺寸送出，一張就八千多 token；現在跟小臉一樣統一到 768。
    def test_a_restored_close_up_face_is_brought_down(self):
        photo = _photo(4000, 3000)
        crops = image_tools.restore_face_crops(photo, [
            {"box": {"x": 0.1, "y": 0.1, "w": 0.6, "h": 0.8}, "face_box": {}, "sent_to_model": True},
            {"box": {"x": 0.1, "y": 0.1, "w": 0.02, "h": 0.03}, "face_box": {}, "sent_to_model": True},
            {"box": {"x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1}, "face_box": {}, "sent_to_model": False}])
        self.assertEqual(max(_size(crops[0]["bytes"])), image_tools._FACE_CROP_MAX_SIDE)
        self.assertEqual(max(_size(crops[1]["bytes"])), image_tools._FACE_CROP_MAX_SIDE)
        self.assertIsNone(crops[2]["bytes"])
        self.assertEqual(crops[0]["box"], {"x": 0.1, "y": 0.1, "w": 0.6, "h": 0.8})

    def test_every_crop_path_shares_the_cap(self):
        big = Image.new("RGB", (2508, 2778))
        small = Image.new("RGB", (120, 90))
        self.assertEqual(image_tools._fit_face_crop(big).size, (693, 768))
        self.assertEqual(image_tools._fit_face_crop(small).size, (768, 576))
        exact = Image.new("RGB", (768, 500))
        self.assertIs(image_tools._fit_face_crop(exact), exact)


class NoCacheWritesTest(unittest.TestCase):
    def payload(self, model):
        return openai_client._responses_payload(model, [{"type": "input_text", "text": "x"}])

    def test_analysis_calls_write_nothing_to_the_prompt_cache(self):
        payload = openai_client._no_cache_writes(self.payload("gpt-5.6-luna"))
        self.assertEqual(payload["prompt_cache_options"], {"mode": "explicit"})
        self.assertEqual(openai_client._no_cache_writes(self.payload("gpt-6-luna"))["prompt_cache_options"],
                         {"mode": "explicit"})

    def test_an_older_model_gets_no_cache_options(self):
        self.assertNotIn("prompt_cache_options", openai_client._no_cache_writes(self.payload("gpt-5.5")))

    def test_the_portrait_passes_send_it(self):
        sent = []
        with patch.object(openai_client, "_post_json", lambda url, payload, label, **kw: sent.append(payload) or {}):
            openai_client._analysis_request("gpt-5.6-luna", "data:a", [], "inst", {"type": "json_schema"}, "x")
        self.assertEqual(sent[0]["prompt_cache_options"], {"mode": "explicit"})


class SumUsageTest(unittest.TestCase):
    # explicit 模式的 usage 多一層 cache_write_tokens_details，以前加總會當掉。
    def test_nested_details_are_summed(self):
        first = {"input_tokens": 100, "output_tokens": 10, "input_tokens_details": {
            "cached_tokens": 0, "cache_write_tokens": 0,
            "cache_write_tokens_details": {"ttl_30m_tokens": 0, "ttl_12h_tokens": 0}},
            "output_tokens_details": {"reasoning_tokens": 4}}
        second = {"input_tokens": 50, "output_tokens": 5, "input_tokens_details": {
            "cached_tokens": 20, "cache_write_tokens": 30,
            "cache_write_tokens_details": {"ttl_30m_tokens": 30, "ttl_12h_tokens": 0}},
            "output_tokens_details": {"reasoning_tokens": 1}}
        total = openai_client._sum_usage(first, second, None)
        self.assertEqual((total["input_tokens"], total["output_tokens"], total["total_tokens"]), (150, 15, 165))
        self.assertEqual(total["input_tokens_details"], {
            "cached_tokens": 20, "cache_write_tokens": 30,
            "cache_write_tokens_details": {"ttl_30m_tokens": 30, "ttl_12h_tokens": 0}})
        self.assertEqual(total["output_tokens_details"], {"reasoning_tokens": 5})


if __name__ == "__main__":
    unittest.main()
