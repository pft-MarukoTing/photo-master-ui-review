import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


def _import_chat_queue():
    """chat_queue imports aiserver_utils' Redis/queue/S3 clients at module
    level, and those need the container's config -- stand in for them here."""
    stubs = {name: types.ModuleType(name) for name in ("aiserver_utils.pf_redis", "aiserver_utils.queue", "aiserver_utils.s3")}
    stubs["aiserver_utils.pf_redis"].pf_redis_client = MagicMock()
    stubs["aiserver_utils.queue"].get_job_info = MagicMock()
    stubs["aiserver_utils.s3"].S3Access = MagicMock()
    stubs["aiserver_utils.s3"].S3AccessV2 = MagicMock()
    with patch.dict(sys.modules, stubs):
        from backend.services import chat_queue
    return chat_queue


chat_queue = _import_chat_queue()
from photo_agent import chat_jobs  # noqa: E402


class ChatQueueRunTest(unittest.TestCase):
    def setUp(self):
        self.enqueued = []
        self.redis = MagicMock()
        self.infos = []

        def enqueue(params, queue=None):
            self.enqueued.append((params, queue))
            return "job1"

        def get_job_info(job_id, keyToGet=None):
            return self.infos.pop(0) if len(self.infos) > 1 else self.infos[0]

        for p in (patch.object(chat_queue, "enqueue", enqueue),
                  patch.object(chat_queue, "get_redis_conn", lambda: self.redis),
                  patch.object(chat_queue, "get_job_info", get_job_info),
                  patch.object(chat_queue, "POLL_SECONDS", 0)):
            p.start()
            self.addCleanup(p.stop)

    def test_queues_the_params_on_the_chat_queue_and_returns_the_answer(self):
        self.infos = [{"status": "queued"}, {"status": "success", "0_res0": {"answer": {"kept": True}}}]
        self.assertEqual(chat_queue.run({"stage": chat_jobs.STAGE_REVIEW, "line": "x"}), {"kept": True})
        params, queue = self.enqueued[0]
        self.assertEqual(queue, chat_jobs.CHAT_QUEUE)
        self.assertEqual(params["acts"][0]["params"], {"stage": chat_jobs.STAGE_REVIEW, "line": "x"})
        self.assertEqual(params["srcKeys"], [])
        self.redis.expire.assert_called_once()

    def test_an_answer_left_as_json_text_is_decoded(self):
        self.infos = [{"status": "success", "0_res0": json.dumps({"answer": {"reply": "好"}})}]
        self.assertEqual(chat_queue.run({"stage": chat_jobs.STAGE_ASK}), {"reply": "好"})

    def test_a_failed_job_raises(self):
        self.infos = [{"status": "error", "error": "boom"}]
        with self.assertRaises(chat_queue.ChatJobError):
            chat_queue.run({"stage": chat_jobs.STAGE_ASK})

    def test_a_job_nobody_answers_is_taken_back_off_the_queue(self):
        self.infos = [{"status": "queued"}]
        with patch.object(chat_queue, "WAIT_SECONDS", 0), self.assertRaises(chat_queue.ChatJobError):
            chat_queue.run({"stage": chat_jobs.STAGE_ASK})
        self.redis.zrem.assert_called_once_with(chat_jobs.CHAT_QUEUE, "job1")


class ChatJobsRunTest(unittest.TestCase):
    def test_review_runs_review_line(self):
        with patch.object(chat_jobs.chat_assistant, "review_line", return_value={"kept": True}) as review:
            self.assertEqual(chat_jobs.run({"stage": chat_jobs.STAGE_REVIEW, "line": "瘦臉", "people": ["左"]}),
                             {"kept": True})
        review.assert_called_once_with("瘦臉", "", [], ["左"], sections=[], deadline=None, batch=None)
        # 總覽打的帶著那一批的樣子過去。
        batch = {"photos": 2, "unrendered": 2, "analyze": "全部分析", "render": "全部一鍵美化", "where": "上面的照片"}
        with patch.object(chat_jobs.chat_assistant, "review_line", return_value={"kept": True}) as review:
            chat_jobs.run({"stage": chat_jobs.STAGE_REVIEW, "line": "瘦臉", "batch": batch})
        self.assertEqual(review.call_args.kwargs["batch"], batch)

    def test_the_model_calls_a_job_makes_are_handed_back_for_the_log(self):
        def review(*args, **kwargs):
            chat_jobs.chat_assistant.SPENT.get().append({"model": "m", "usage": {
                "input_tokens": 100, "input_tokens_details": {"cached_tokens": 80, "cache_write_tokens": 5},
                "output_tokens": 40, "output_tokens_details": {"reasoning_tokens": 25}}})
            chat_jobs.chat_assistant.SPENT.get().append({"model": "m", "usage": {
                "input_tokens": 50, "input_tokens_details": {"cached_tokens": 30}, "output_tokens": 10}})
            return {"kept": True}
        spent = []
        with patch.object(chat_jobs.chat_assistant, "review_line", review):
            chat_jobs.run({"stage": chat_jobs.STAGE_REVIEW, "line": "瘦臉"}, spent)
        self.assertEqual(chat_jobs.cache_note(spent), " cache 110/150 (+5 written) out 50 (reasoning 25)")
        # A usage without its details still makes a line.
        self.assertEqual(chat_jobs.cache_note([{"usage": {"input_tokens": 7}}]), " cache 0/7 out 0")
        self.assertEqual(chat_jobs.cache_note([]), "")
        self.assertIsNone(chat_jobs.chat_assistant.SPENT.get())

    def test_an_unknown_stage_raises(self):
        with self.assertRaises(RuntimeError):
            chat_jobs.run({"stage": "nope"})

    def test_the_chat_queue_hangs_off_the_job_queue(self):
        self.assertTrue(chat_jobs.CHAT_QUEUE.endswith(":chat"))


if __name__ == "__main__":
    unittest.main()
