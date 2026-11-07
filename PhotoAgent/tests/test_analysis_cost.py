"""What an analysis sends and pays for: face crops at a capped size."""

import io
import unittest

from PIL import Image

import image_tools


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


if __name__ == "__main__":
    unittest.main()
