import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from photo_agent.param_validation import _validate_params
from tests.test_chat_routes import api, app_module

SELECTIONS = {"options": {"lighting": "soft"}}


class SelectionsParamTest(unittest.TestCase):
    """`selections` ride only on the combined job, which makes the analysis
    they apply onto."""

    def test_the_full_stage_takes_them(self):
        params = _validate_params({"stage": "full", "selections": SELECTIONS})
        self.assertEqual(params["selections"], SELECTIONS)

    def test_any_other_stage_refuses_them(self):
        for stage in ("analyze", "finish"):
            with self.assertRaises(RuntimeError):
                _validate_params({"stage": stage, "selections": SELECTIONS})

    def test_malformed_ones_are_refused(self):
        for selections in ("soft", {"options": {"lighting": "nope"}}):
            with self.assertRaises(RuntimeError):
                _validate_params({"stage": "full", "selections": selections})


class WorkerAppliesSelectionsTest(unittest.TestCase):
    def run_job(self, params):
        queue = types.ModuleType("aiserver_utils.queue")
        queue.set_job_info = MagicMock()
        queue.get_job_info = MagicMock()
        with patch.dict(sys.modules, {"aiserver_utils.queue": queue}):
            import job_processor
        reviewed = []
        task_info = {"effect": "photoAgent", "srcKeys": ["src.jpg"], "dstKeys": ["result/job/result.png"],
                     "acts": [{"id": 0, "params": params}]}
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "src.jpg"
            image.write_bytes(b"jpg")
            worker = job_processor.PhotoAgentJobProcessor(MagicMock(), MagicMock(), monitor_dst_dir=tmp)
            with patch.object(worker, "download_sources", return_value=[str(image)]), \
                 patch.object(job_processor, "set_job_info"), \
                 patch.object(job_processor, "analyze_stage", return_value={"style_options": {}}), \
                 patch.object(job_processor, "review_stage",
                              side_effect=lambda analysis, *a, **k: reviewed.append(analysis) or {}), \
                 patch.object(job_processor, "finish_stage", side_effect=RuntimeError("stop")):
                with self.assertRaises(RuntimeError):
                    worker.process_for_api("job", task_info)
        return reviewed[0]

    def test_the_edit_works_off_the_analysis_with_them_applied(self):
        analysis = self.run_job({"stage": "full", "selections": SELECTIONS})
        self.assertEqual(analysis["style_options"], {"lighting": "soft"})

    def test_without_them_the_analysis_is_untouched(self):
        self.assertEqual(self.run_job({"stage": "full"}), {"style_options": {}})


class AnalyzeAndFinishRouteTest(unittest.TestCase):
    def setUp(self):
        self.enqueued, self.history, self.attached = [], [], []

        def enqueue(engine_params, src_content=None):
            self.enqueued.append(engine_params)
            return "job_new"

        for p in (patch.object(api, "get_job_info", lambda job_id, keyToGet=None: {"srcKeys": ["case.jpg"]}),
                  patch.object(api, "enqueue", enqueue),
                  patch.object(api, "_record_history_safely", lambda *a, **k: self.history.append(a)),
                  patch.object(api, "attach_case_analysis", lambda *a: self.attached.append(a))):
            p.start()
            self.addCleanup(p.stop)
        self.client = app_module.create_app({"TESTING": True}).test_client()

    def post(self, **body):
        return self.client.post("/api/photo-agent/analyze-and-finish/job_old", json={"user": "amy", **body})

    def test_one_combined_job_on_the_case_photo(self):
        res = self.post(selections=SELECTIONS, user_prompt="背景亮一點", run_count=2)
        self.assertEqual(res.status_code, 202)
        self.assertEqual(res.get_json(), {"job_id": "job_new"})
        engine_params = self.enqueued[0]
        params = engine_params["acts"][0]["params"]
        self.assertEqual(engine_params["srcKeys"], ["case.jpg"])
        self.assertEqual(params["stage"], "full")
        self.assertEqual(params["selections"], SELECTIONS)
        self.assertEqual(params["run_count"], 2)
        self.assertEqual(params["user_prompt"], "背景亮一點")
        self.assertEqual(self.history[0][1:5], ("job_new", "local", "case.jpg", "job_new"))
        self.assertEqual(self.attached, [("amy", "case.jpg", "job_new")])

    def test_selections_may_come_as_a_json_string(self):
        self.assertEqual(self.post(selections=json.dumps(SELECTIONS)).status_code, 202)
        self.assertEqual(self.enqueued[0]["acts"][0]["params"]["selections"], SELECTIONS)

    def test_bad_selections_enqueue_nothing(self):
        for selections in ("{nope", {"options": {"lighting": "nope"}}):
            self.assertEqual(self.post(selections=selections).status_code, 400)
        self.assertEqual(self.enqueued, [])

    def test_an_unknown_job_is_not_found(self):
        with patch.object(api, "get_job_info", lambda job_id, keyToGet=None: None):
            self.assertEqual(self.post(selections=SELECTIONS).status_code, 404)
        self.assertEqual(self.enqueued, [])


class AnalyzeResultOfCombinedJobTest(unittest.TestCase):
    """/analyze/result on the job attached to a case after analyze_and_finish:
    the analysis rides in the first RUN's result metadata."""

    def setUp(self):
        info = {"status": "success", "0_res0": {"runs": [{"cusKeys": ["run0.png", "run0.json"]}]}}
        self.downloaded = []

        def download(key, job_id):
            self.downloaded.append(key)
            return json.dumps({"analysis": {"subject_category": "people"}}).encode(), None

        for p in (patch.object(api, "_readable_job_info", lambda job_id, keys: info),
                  patch.object(api, "download_bytes", download),
                  patch.object(api, "get_min_compatible_version", lambda: None)):
            p.start()
            self.addCleanup(p.stop)
        self.client = app_module.create_app({"TESTING": True}).test_client()

    def test_reads_the_first_runs_analysis(self):
        body = self.client.get("/api/photo-agent/analyze/result/job_new").get_json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["result"]["analysis"], {"subject_category": "people"})
        self.assertEqual(self.downloaded, ["run0.json"])


if __name__ == "__main__":
    unittest.main()
