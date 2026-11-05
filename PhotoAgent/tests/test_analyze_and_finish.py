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


class WorkerReadsContinuationTest(unittest.TestCase):
    """A 接著這張調整 that may need the original carries it second; the worker
    reads the request and decides whether the editor gets it."""

    def run_finish(self, analysis, read=None, sources=2):
        queue = types.ModuleType("aiserver_utils.queue")
        queue.set_job_info = MagicMock()
        queue.get_job_info = MagicMock()
        with patch.dict(sys.modules, {"aiserver_utils.queue": queue}):
            import job_processor
        finished, asked, downloaded = [], [], []

        def call(context):
            asked.append(context)
            if isinstance(read, Exception):
                raise read
            return read

        params = {"stage": "finish", "user_prompt": "嘴巴不要動到", "overwrite_prompt": True, "analysis": analysis}
        task_info = {"effect": "photoAgent", "srcKeys": ["version.jpg", "original.jpg"][:sources],
                     "dstKeys": ["result/job/result.png"], "acts": [{"id": 0, "params": params}]}
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name in ("version.jpg", "original.jpg")[:sources]:
                (Path(tmp) / name).write_bytes(name.encode())
                paths.append(str(Path(tmp) / name))
            worker = job_processor.PhotoAgentJobProcessor(MagicMock(), MagicMock(), monitor_dst_dir=tmp)
            by_key = dict(zip(task_info["srcKeys"], paths))
            fetch = lambda task_id, keys, session_dir: downloaded.extend(keys) or [by_key[k] for k in keys]
            with patch.object(worker, "download_sources", side_effect=fetch), \
                 patch.object(job_processor.chat_assistant, "call_continuation", call), \
                 patch.object(job_processor, "finish_stage",
                              side_effect=lambda **k: finished.append(k) or (_ for _ in ()).throw(RuntimeError("stop"))):
                with self.assertRaises(RuntimeError):
                    worker.process_for_api("job", task_info)
        return finished[0], asked, downloaded

    def stub(self, **extra):
        return {"_skipped_analysis": True, "_continuation": True, **extra}

    def test_a_request_that_wants_something_back_gets_the_original(self):
        finish, asked, downloaded = self.run_finish(self.stub(_continuation_reference=True),
                                        read={"needs_original": True, "restore": " the mouth "})
        self.assertIn("REQUEST: 嘴巴不要動到", asked[0])
        self.assertEqual(finish["reference_image_bytes"], b"original.jpg")
        self.assertEqual(finish["analysis"]["_continuation_original"], {"restore": "the mouth"})
        self.assertEqual(downloaded, ["version.jpg", "original.jpg"])

    def test_a_new_change_goes_alone(self):
        finish, _, downloaded = self.run_finish(self.stub(_continuation_reference=True),
                                    read={"needs_original": False, "restore": ""})
        self.assertNotIn("reference_image_bytes", finish)
        self.assertNotIn("_continuation_original", finish["analysis"])
        # The original is never fetched when the editor does not get it.
        self.assertEqual(downloaded, ["version.jpg"])

    def test_a_failed_read_sends_the_original(self):
        finish, _, downloaded = self.run_finish(self.stub(_continuation_reference=True), read=RuntimeError("timeout"))
        self.assertEqual(finish["reference_image_bytes"], b"original.jpg")
        self.assertEqual(finish["analysis"]["_continuation_original"], {"restore": ""})

    def test_a_job_queued_with_the_read_already_done_is_not_read_again(self):
        finish, asked, downloaded = self.run_finish(self.stub(_continuation_original={"restore": "the mouth"}))
        self.assertEqual(asked, [])
        self.assertEqual(finish["reference_image_bytes"], b"original.jpg")

    def test_without_the_original_nothing_is_read(self):
        finish, asked, downloaded = self.run_finish(self.stub(_continuation_reference=True), sources=1)
        self.assertEqual(asked, [])
        self.assertNotIn("reference_image_bytes", finish)


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
