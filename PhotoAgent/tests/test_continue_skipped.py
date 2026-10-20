import unittest
from unittest.mock import patch

from tests.test_chat_routes import api, app_module

STUB = {"_skipped_analysis": True, "prompts": {}, "timings": {}, "usage": {}}


class ContinueSkippedAnalysisTest(unittest.TestCase):
    """/continue from a 「只照需求改」 case: the panel re-renders it from the
    skipped-analysis stub, which has no subject_category."""

    def setUp(self):
        self.enqueued = []

        def enqueue(engine_params, src_content=None):
            self.enqueued.append(engine_params["acts"][0]["params"])
            return "job_new"

        for p in (patch.object(api, "get_job_info", lambda job_id, keyToGet=None: {"srcKeys": ["src.jpg"]}),
                  patch.object(api, "is_compatible", lambda analysis, min_version=None: True),
                  patch.object(api, "get_min_compatible_version", lambda: None),
                  patch.object(api, "enqueue", enqueue),
                  patch.object(api, "_record_history_safely", lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)
        self.client = app_module.create_app({"TESTING": True}).test_client()

    def post(self, **body):
        return self.client.post("/api/photo-agent/continue/job_old", json={"analysis": STUB, **body})

    def test_a_prompt_only_rerender_with_a_picked_style_goes_through(self):
        res = self.post(user_prompt="把背景的人移掉", overwrite_prompt=True,
                        selections={"options": {"lighting": "soft"}})
        self.assertEqual(res.status_code, 202)
        params = self.enqueued[0]
        self.assertEqual(params["stage"], "finish")
        self.assertTrue(params["overwrite_prompt"])
        self.assertEqual(params["analysis"]["style_options"], {"lighting": "soft"})

    def test_the_stub_alone_has_nothing_to_retouch(self):
        for body in ({"user_prompt": "把背景的人移掉", "overwrite_prompt": False},
                     {"user_prompt": "  ", "overwrite_prompt": True}):
            res = self.post(**body)
            self.assertEqual(res.status_code, 400)
            self.assertEqual(res.get_json()["error"], api.SKIPPED_NEEDS_PROMPT)
        self.assertEqual(self.enqueued, [])

    def test_an_analysis_without_subject_category_is_still_refused(self):
        res = self.client.post("/api/photo-agent/continue/job_old",
                               json={"analysis": {"prompts": {}}, "user_prompt": "x", "overwrite_prompt": True})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.enqueued, [])


if __name__ == "__main__":
    unittest.main()
