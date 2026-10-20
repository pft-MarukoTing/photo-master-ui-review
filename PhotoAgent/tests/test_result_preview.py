import io
import unittest
from unittest.mock import patch

from PIL import Image

from tests.test_chat_routes import api, app_module

JOB = "job_res"
RESULT = "demo/photoAgent/result/job_res/0_res0_thumb_0.jpg"
THUMB = "demo/photoAgent/result/job_res/0_res0_thumb_0_thumb.jpg"
PREVIEW = "demo/photoAgent/result/job_res/0_res0_thumb_0_preview.jpg"


def _jpeg(width, height):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(buf, "JPEG")
    return buf.getvalue()


class ResultPreviewTest(unittest.TestCase):
    """/result/<job_id>/preview serves a mid-size copy of the result, made once
    and kept at its own sibling key, next to the 320px thumbnail."""

    def setUp(self):
        self.store = {RESULT: _jpeg(960, 1632)}
        self.cached = []

        def download_bytes(key, job_id):
            return (self.store[key], None) if key in self.store else (None, "not found")

        def cache_thumbnail(data, key, job_id):
            self.cached.append(key)
            self.store[key] = data

        info = {"status": "success", "0_res0": {"cusKeys": [RESULT]}}
        for p in (patch.object(api, "get_job_info", lambda job_id, keyToGet=None: info),
                  patch.object(api, "download_bytes", download_bytes),
                  patch.object(api, "cache_thumbnail", cache_thumbnail)):
            p.start()
            self.addCleanup(p.stop)
        self.client = app_module.create_app({"TESTING": True}).test_client()

    def size(self, response):
        return Image.open(io.BytesIO(response.data)).size

    def test_makes_the_preview_once_then_serves_the_cached_copy(self):
        first = self.client.get(f"/api/photo-agent/result/{JOB}/preview")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.mimetype, "image/jpeg")
        self.assertEqual(max(self.size(first)), 512)
        self.assertEqual(self.cached, [PREVIEW])
        self.assertIn("immutable", first.headers["Cache-Control"])

        del self.store[RESULT]  # the full result is not read again
        second = self.client.get(f"/api/photo-agent/result/{JOB}/preview")
        self.assertEqual((second.status_code, second.data), (200, first.data))
        self.assertEqual(self.cached, [PREVIEW])

    def test_the_thumbnail_keeps_its_own_size_and_key(self):
        thumb = self.client.get(f"/api/photo-agent/result/{JOB}/thumbnail")
        self.assertEqual((thumb.status_code, max(self.size(thumb))), (200, 320))
        self.assertEqual(self.cached, [THUMB])


if __name__ == "__main__":
    unittest.main()
