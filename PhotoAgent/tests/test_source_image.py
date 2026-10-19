import io
import unittest
from unittest.mock import patch

from PIL import Image

from tests.test_chat_routes import api, app_module

JOB = "job_src"
SRC = "demo/photoAgent/input/job_src/src.jpg"
VIEW = "demo/photoAgent/input/job_src/src_view.jpg"


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "PNG")
    return buf.getvalue()


class SourceImageTest(unittest.TestCase):
    """/source/<job_id>/image serves the upload as a JPEG, converted once
    and kept at a sibling key."""

    def setUp(self):
        self.store = {SRC: _png()}
        self.cached = []

        def download_bytes(key, job_id):
            return (self.store[key], None) if key in self.store else (None, "not found")

        def cache_thumbnail(data, key, job_id):
            self.cached.append(key)
            self.store[key] = data

        for p in (patch.object(api, "get_job_info", lambda job_id, keyToGet=None: {"srcKeys": [SRC]}),
                  patch.object(api, "download_bytes", download_bytes),
                  patch.object(api, "cache_thumbnail", cache_thumbnail)):
            p.start()
            self.addCleanup(p.stop)
        self.client = app_module.create_app({"TESTING": True}).test_client()

    def get(self):
        return self.client.get(f"/api/photo-agent/source/{JOB}/image")

    def test_converts_once_then_serves_the_cached_jpeg(self):
        first = self.get()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.mimetype, "image/jpeg")
        self.assertTrue(first.data.startswith(b"\xff\xd8"))
        self.assertEqual(self.cached, [VIEW])
        self.assertIn("immutable", first.headers["Cache-Control"])

        del self.store[SRC]  # the original is not read again
        second = self.get()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data, first.data)
        self.assertEqual(self.cached, [VIEW])
        self.assertIn("immutable", second.headers["Cache-Control"])


if __name__ == "__main__":
    unittest.main()
