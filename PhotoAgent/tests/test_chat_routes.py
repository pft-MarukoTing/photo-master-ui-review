import io
import json
import sys
import time
import types
import unittest
import uuid
from unittest.mock import MagicMock, patch

from tests.test_selections import base_analysis

# What the upload page says after taking a wish down: the model's acknowledgement,
# then what comes next.
KEPT = "好，這個我記下來了。還有其他想改的地方嗎？沒有的話，可以從下面選「分析」或「一鍵美化」喔！"


def _import_backend():
    """backend.* imports aiserver_utils' Redis/queue/S3 clients at module
    level, and those need the container's config -- stand in for them here."""
    stubs = {name: types.ModuleType(name) for name in ("aiserver_utils.pf_redis", "aiserver_utils.queue", "aiserver_utils.s3")}
    stubs["aiserver_utils.pf_redis"].pf_redis_client = MagicMock()
    stubs["aiserver_utils.queue"].get_job_info = MagicMock()
    stubs["aiserver_utils.s3"].S3Access = MagicMock()
    stubs["aiserver_utils.s3"].S3AccessV2 = MagicMock()
    with patch.dict(sys.modules, stubs):
        from backend import app as app_module
        from backend.routes import api, chat
        from backend.services import account, chat_session, put_job
    return app_module, api, chat, chat_session, account, put_job


app_module, api, chat, chat_session, account, put_job = _import_backend()


# The analysis fields the panel builds its face tabs from.
ONE_FACE = {"_local_face_boxes": [{"box": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}}],
            "local_face_review": [{"detection_index": 1, "matched_subject_index": 0}]}

class FakeRedis:
    """The subset of redis-py the chat store uses, values kept as str."""

    def __init__(self):
        self.data = {}
        self.ttl = {}

    def hset(self, key, field=None, value=None, mapping=None):
        h = self.data.setdefault(key, {})
        if field is not None:
            h[field] = value
        h.update(mapping or {})

    def hgetall(self, key):
        return dict(self.data.get(key, {}))

    def hget(self, key, field):
        return self.data.get(key, {}).get(field)

    def hsetnx(self, key, field, value):
        h = self.data.setdefault(key, {})
        if field in h:
            return 0
        h[field] = value
        return 1

    def expire(self, key, seconds):
        self.ttl[key] = seconds

    def rpush(self, key, *values):
        self.data.setdefault(key, []).extend(values)

    def ltrim(self, key, start, end):
        items = self.data.get(key, [])
        n = len(items)
        start, end = (start + n if start < 0 else start), (end + n if end < 0 else end)
        self.data[key] = items[max(start, 0):end + 1]

    def lrange(self, key, start, end):
        items = self.data.get(key, [])
        n = len(items)
        start, end = (start + n if start < 0 else start), (end + n if end < 0 else end)
        return items[max(start, 0):end + 1]

    def lindex(self, key, index):
        items = self.data.get(key, [])
        return items[index] if -len(items) <= index < len(items) else None

    def lset(self, key, index, value):
        self.data[key][index] = value

    def zadd(self, key, mapping):
        self.data.setdefault(key, {}).update(mapping)

    def zrevrange(self, key, start, end):
        members = sorted(self.data.get(key, {}).items(), key=lambda kv: kv[1], reverse=True)
        return [m for m, _ in members][start:(end + 1) or None]

    def zrem(self, key, member):
        self.data.get(key, {}).pop(member, None)

    def zrange(self, key, start, end):
        members = sorted(self.data.get(key, {}).items(), key=lambda kv: kv[1])
        return [m for m, _ in members][start:(end + 1) or None]

    def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        if ex is not None:
            self.ttl[key] = ex
        return True

    def get(self, key):
        return self.data.get(key)

    def sadd(self, key, *members):
        self.data.setdefault(key, set()).update(members)

    def srem(self, key, *members):
        self.data.get(key, set()).difference_update(members)

    def smembers(self, key):
        return set(self.data.get(key, set()))


USER = "tester"


def offline(*args, **kwargs):
    raise RuntimeError("no model in tests")


class ChatRoutesTest(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.jobs = {}          # job_id -> {"status", "error"}
        self.enqueued = []      # (job_id, engine_params)
        self.history = []       # record_history kwargs
        self.next_job = iter(f"job{i}" for i in range(1000))
        self.analysis = {**base_analysis(), "compatibility_version": 5}
        self.cases = {}         # case_id -> history entry with versions (see self.case)

        def enqueue(engine_params, src_content=None, job_id=None):
            job_id = job_id or next(self.next_job)
            for key in ("srcKeys", "dstKeys"):
                engine_params[key] = [k.replace("{msg_id}", job_id) for k in engine_params[key]]
            self.enqueued.append((job_id, json.loads(json.dumps(engine_params))))
            self.jobs[job_id] = {"status": "processing", "error": None}
            return job_id

        def get_job_info(job_id, keyToGet=None):
            job = self.jobs.get(job_id)
            if job is None:
                return None
            return {"0_res0": {"cusKeys": [f"{job_id}/analysis.json"]}, **job}

        def record_history(username, job_id, path, case_id, version_id, params=None, based_on=None):
            self.history.append({"job_id": job_id, "case_id": case_id, "version_id": version_id,
                                 "params": params, "based_on": based_on})

        self.chat_answers = {}

        def submit_chat(params):
            job_id = f"chat_{len(self.chat_answers)}_{uuid.uuid4().hex[:6]}"
            try:
                self.chat_answers[job_id] = chat.chat_jobs.run(json.loads(json.dumps(params)))
            except Exception:
                self.chat_answers[job_id] = None
            return job_id

        patches = [
            patch.object(chat_session, "_client", lambda: self.redis),
            patch.object(chat, "enqueue", enqueue),
            patch.object(chat, "get_job_info", get_job_info),
            patch.object(chat, "get_session_id", lambda: next(self.next_job)),
            patch.object(chat, "download_bytes", lambda key, job_id: (json.dumps(self.analysis).encode(), None)),
            # Tests swap self.analysis under the same keys; the cache has its own test.
            patch.object(chat, "_ANALYSIS_CACHE_SIZE", 0),
            patch.object(chat, "get_min_compatible_version", lambda: 3),
            # The worker's side of a chat job, run in place: only what survives
            # the trip through the job's info hash gets there.
            patch.object(chat.chat_queue, "run", lambda params, **kwargs: chat.chat_jobs.run(json.loads(json.dumps(params)))),
            # A queued one (a brief, a note) is answered by the next poll.
            patch.object(chat.chat_queue, "submit", submit_chat),
            patch.object(chat.chat_queue, "poll", lambda job_id, since, stage=None: (True, self.chat_answers.pop(job_id))),
            patch.object(api, "record_history", record_history),
            patch.object(chat, "get_history", lambda user, limit=50, offset=0: sorted(
                self.cases.values(), key=lambda e: e["ts"], reverse=True)[offset:offset + limit]),
            patch.object(chat, "get_history_entry", lambda user, case_id, include_versions=True: self.cases.get(case_id)),
            patch.object(api, "get_current_compatibility_version", lambda: 5),
            # 詳細解說 is on by default: no test reaches a real model for the
            # opening or the render note unless it patches one in itself.
            patch.object(chat.chat_assistant, "call_opening", offline),
            patch.object(chat.chat_assistant, "call_render_note", offline),
            patch.object(chat.chat_assistant, "call_continuation", offline),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = app_module.create_app({"TESTING": True}).test_client()

    def test_chat_redirect_preserves_identity_query(self):
        query = "user_id=MmNZsuz813FItZ%2Flhfy2Bw%3D%3D&admin&tag=a&tag=b"
        response = self.client.get("/chat?" + query)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "chat/?" + query)
        final = self.client.get("/chat?" + query, follow_redirects=True)
        self.addCleanup(final.close)
        self.assertEqual(final.request.path, "/chat/")
        self.assertEqual(final.request.query_string.decode("ascii"), query)
        self.assertEqual(final.request.args["user_id"], "MmNZsuz813FItZ/lhfy2Bw==")

    def test_chat_redirect_without_query(self):
        response = self.client.get("/chat")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "chat/")

    # --- helpers -----------------------------------------------------------

    def url(self, sid, suffix=""):
        return f"/api/photo-agent/chat/sessions/{sid}{suffix}"

    def new_session(self):
        res = self.client.post("/api/photo-agent/chat/sessions", json={"user": USER})
        self.assertEqual(res.status_code, 201)
        return res.get_json()["session_id"]

    def test_a_session_keeps_the_language_it_was_made_in(self):
        res = self.client.post("/api/photo-agent/chat/sessions", json={"user": USER},
                               headers={"X-PhotoAgent-Lang": "en"})
        self.assertEqual(res.get_json()["lang"], "en")
        # Adding photos rewrites the session's meta: the language stays.
        sid = res.get_json()["session_id"]
        self.add_photos(sid)
        self.assertEqual(chat_session.load_session(USER, sid)["lang"], "en")
        # A Chinese one stays as it always was.
        self.assertNotIn("lang", chat_session.load_session(USER, self.new_session()))

    def test_the_watcher_writes_in_the_sessions_language(self):
        seen = []
        with patch.object(chat.store, "load_session", lambda user, sid: {"lang": "en", "photos": []}), \
                patch.object(chat, "_watch_loaded", lambda user, sid, session: seen.append(chat.i18n.current())):
            chat._watch_session(USER, "s1")
        self.assertEqual(seen, ["en"])

    def add_photos(self, sid, n=1):
        data = {"user": USER, "image": [(io.BytesIO(b"img"), f"p{i}.jpg") for i in range(n)]}
        res = self.client.post(self.url(sid, "/photos"), data=data, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 202, res.get_json())
        return res.get_json()["photos"]

    def status(self, sid):
        res = self.client.get(self.url(sid, "/status"), query_string={"user": USER})
        self.assertEqual(res.status_code, 200)
        return res.get_json()

    def ready_photo(self, sid):
        photo = self.add_photos(sid)[0]
        self.jobs[photo["analyze_job_id"]]["status"] = "success"
        self.status(sid)
        return photo

    def summary(self, sid):
        """The session's row in the list -- what a history tile renders."""
        listed = self.client.get("/api/photo-agent/chat/sessions", query_string={"user": USER}).get_json()
        return next(s for s in listed["sessions"] if s["session_id"] == sid)

    def case(self, case_id, versions, ts=100.0, path="local"):
        """A history case as account.get_history_entry returns it, from
        (version_id, kind, job_ids) oldest first."""
        rows = [{"version_id": vid, "kind": kind, "runs": list(runs), "path": path, "based_on": None,
                 "created_at": ts, "results": [{"job_id": j, "run": 1, "field": "0_res0"} for j in runs]}
                for vid, kind, runs in versions]
        edits = [v for v in rows if v["kind"] == "edit"]
        self.cases[case_id] = {"image_key": case_id, "path": path, "ts": ts, "uploaded_at": ts,
                               "modified_at": ts if edits else None, "runs": rows[-1]["runs"], "versions": rows}
        return self.cases[case_id]

    def cards(self, sid, kind=None):
        body = self.client.get(self.url(sid), query_string={"user": USER}).get_json()
        return [m["card"] for m in body["messages"] if m.get("card") and (kind is None or m["card"]["type"] == kind)]

    def from_case(self, case_id):
        return self.client.post("/api/photo-agent/chat/sessions/from-case", json={"user": USER, "case_id": case_id})

    def render(self, sid, photo):
        res = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": [photo["photo_id"]]})
        self.assertEqual(res.status_code, 202, res.get_json())
        return res.get_json()["renders"][0]

    # --- sessions ----------------------------------------------------------

    def test_create_get_and_list(self):
        sid = self.new_session()
        res = self.client.get(self.url(sid), query_string={"user": USER})
        body = res.get_json()
        self.assertEqual(body["photos"], [])
        self.assertEqual(body["messages"], [])
        self.assertEqual(body["defaults"]["preset"], "natural")
        listed = self.client.get("/api/photo-agent/chat/sessions", query_string={"user": USER}).get_json()
        self.assertEqual([s["session_id"] for s in listed["sessions"]], [sid])
        self.assertEqual({v for k, v in self.redis.ttl.items() if not k.endswith(":advancing")},
                         {chat_session.SESSION_TTL_SECONDS})

    def test_unknown_session_and_bad_user(self):
        self.assertEqual(self.client.get(self.url("s_nope"), query_string={"user": USER}).status_code, 404)
        self.assertEqual(self.client.get(self.url("bad id!"), query_string={"user": USER}).status_code, 404)
        self.assertEqual(self.client.post("/api/photo-agent/chat/sessions", json={"user": "a b"}).status_code, 400)
        sid = self.new_session()
        self.assertEqual(self.client.get(self.url(sid), query_string={"user": "other"}).status_code, 404)

    def test_batch_sessions_list_as_one_row(self):
        """The photos of one multi-photo upload each get their own session;
        the list shows them as one tile, opened at the one worked on last."""
        ids = []
        for _ in range(3):
            res = self.client.post("/api/photo-agent/chat/sessions", json={"user": USER, "batch_id": "b_1"})
            self.assertEqual(res.status_code, 201)
            ids.append(res.get_json()["session_id"])
            self.ready_photo(ids[-1])
        lone = self.new_session()
        self.ready_photo(lone)
        self.render(ids[0], chat_session.load_session(USER, ids[0])["photos"][0])
        rows = self.client.get("/api/photo-agent/chat/sessions", query_string={"user": USER}).get_json()["sessions"]
        self.assertEqual(len(rows), 2)
        batch = next(r for r in rows if r.get("session_ids"))
        self.assertEqual(batch["session_ids"], ids)
        self.assertEqual(batch["session_id"], ids[0])
        self.assertEqual(batch["photo_count"], 3)
        self.assertEqual(len(batch["thumbnails"]), 3)
        self.assertEqual(batch["status"], "rendering")
        self.assertEqual(len(batch["case_ids"]), 3)
        self.assertEqual(chat_session.load_session(USER, ids[1])["batch_id"], "b_1")

    def test_bad_batch_id(self):
        res = self.client.post("/api/photo-agent/chat/sessions", json={"user": USER, "batch_id": "bad id!"})
        self.assertEqual(res.status_code, 400)

    def overview_url(self, batch_id):
        return f"/api/photo-agent/chat/batches/{batch_id}/overview"

    def test_batch_overview_is_kept_for_the_batch(self):
        """The overview's own conversation comes back when the batch is
        opened again; it is per user, and nothing is there until saved."""
        url = self.overview_url("b_1")
        self.assertEqual(self.client.get(url, query_string={"user": USER}).get_json(), {"overview": None})
        saved = {"turns": [{"id": 1, "kind": "upload", "srcs": ["sid:s_a"]}], "hints": {"1": {"text": "hi"}}}
        res = self.client.put(url, json={"user": USER, "overview": saved})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.client.get(url, query_string={"user": USER}).get_json(), {"overview": saved})
        self.assertEqual(self.client.get(url, query_string={"user": "other"}).get_json(), {"overview": None})

    def test_bad_batch_overview(self):
        self.assertEqual(self.client.put(self.overview_url("bad id!"), json={"user": USER, "overview": {}}).status_code, 400)
        self.assertEqual(self.client.put(self.overview_url("b_1"), json={"user": USER, "overview": []}).status_code, 400)
        big = {"turns": ["x" * chat_session.OVERVIEW_MAX_BYTES]}
        self.assertEqual(self.client.put(self.overview_url("b_1"), json={"user": USER, "overview": big}).status_code, 413)

    def test_deleting_a_batch_drops_its_overview(self):
        self.patch_history_deletes()
        ids = [self.client.post("/api/photo-agent/chat/sessions", json={"user": USER, "batch_id": b}).get_json()["session_id"]
               for b in ("b_1", "b_1", "b_2")]
        for b in ("b_1", "b_2"):
            self.client.put(self.overview_url(b), json={"user": USER, "overview": {"turns": []}})
        self.delete_rows({"rows": ids[:2]})
        overview = lambda b: self.client.get(self.overview_url(b), query_string={"user": USER}).get_json()["overview"]
        self.assertIsNone(overview("b_1"))
        self.assertEqual(overview("b_2"), {"turns": []})
        self.delete_rows({"all": True})
        self.assertIsNone(overview("b_2"))

    # --- retried requests (client_key) ------------------------------------

    def create_keyed(self, key):
        res = self.client.post("/api/photo-agent/chat/sessions",
                               json={"user": USER, "batch_id": "b1", "client_key": key})
        self.assertEqual(res.status_code, 201)
        return res.get_json()["session_id"]

    def upload_keyed(self, sid, key):
        data = {"user": USER, "client_key": key, "image": [(io.BytesIO(b"img"), "p0.jpg")]}
        return self.client.post(self.url(sid, "/photos"), data=data, content_type="multipart/form-data")

    def test_retried_create_gets_the_same_session(self):
        sid = self.create_keyed("k1")
        self.assertEqual(self.create_keyed("k1"), sid)
        self.assertNotEqual(self.create_keyed("k2"), sid)
        listed = self.client.get("/api/photo-agent/chat/sessions", query_string={"user": USER}).get_json()
        self.assertEqual(len(listed["sessions"]), 1)  # one batch row
        self.assertEqual(len(self.redis.data[chat_session._index_key(USER)]), 2)

    def test_retried_create_after_the_session_is_gone_makes_a_new_one(self):
        sid = self.create_keyed("k1")
        chat_session.delete_session(USER, sid)
        again = self.create_keyed("k1")
        self.assertNotEqual(again, sid)
        self.assertEqual(self.create_keyed("k1"), again)

    def test_bad_client_key(self):
        res = self.client.post("/api/photo-agent/chat/sessions", json={"user": USER, "client_key": "bad key!"})
        self.assertEqual(res.status_code, 400)

    def test_retried_upload_returns_the_photo_already_added(self):
        sid = self.new_session()
        first = self.upload_keyed(sid, "u1")
        self.assertEqual(first.status_code, 202)
        again = self.upload_keyed(sid, "u1")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.get_json()["photos"][0]["photo_id"], first.get_json()["photos"][0]["photo_id"])
        self.assertEqual(len(self.enqueued), 1)
        session = self.client.get(self.url(sid), query_string={"user": USER}).get_json()
        self.assertEqual(len(session["photos"]), 1)
        # Released once done, and a different key is a different photo.
        self.assertNotIn(chat_session._upload_claim_key(USER, sid, "u1"), self.redis.data)
        self.assertEqual(self.upload_keyed(sid, "u2").status_code, 202)
        self.assertEqual(len(self.enqueued), 2)

    def test_upload_still_running_answers_409(self):
        sid = self.new_session()
        self.assertTrue(chat_session.claim_upload(USER, sid, "u1"))
        res = self.upload_keyed(sid, "u1")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.get_json()["error"], "upload_in_progress")
        self.assertEqual(self.enqueued, [])

    def test_failed_upload_releases_its_key(self):
        sid = self.new_session()
        with patch.object(chat, "enqueue", side_effect=RuntimeError("s3 down")):
            res = self.upload_keyed(sid, "u1")
        self.assertEqual(res.get_json()["photos"], [])
        self.assertNotIn(chat_session._upload_claim_key(USER, sid, "u1"), self.redis.data)
        self.assertEqual(self.upload_keyed(sid, "u1").status_code, 202)
        self.assertEqual(len(self.enqueued), 1)

    def test_expired_session_drops_out_of_the_list(self):
        sid = self.new_session()
        del self.redis.data[chat_session._session_key(USER, sid)]
        listed = self.client.get("/api/photo-agent/chat/sessions", query_string={"user": USER}).get_json()
        self.assertEqual(listed["sessions"], [])

    def test_session_tile_follows_the_newest_result(self):
        """What a history tile shows: the first photo's original until the
        session has rendered something, then the newest result, with the
        badge's state over it."""
        sid = self.new_session()
        first, second = self.add_photos(sid, 2)
        row = self.summary(sid)
        self.assertEqual(
            (row["status"], row["thumbnail_job_id"], row["thumbnail_result"], row["rendered_count"], row["photo_count"]),
            ("analyzing", first["analyze_job_id"], None, 0, 2),
        )

        for photo in (first, second):
            self.jobs[photo["analyze_job_id"]]["status"] = "success"
        self.status(sid)
        self.assertEqual(self.summary(sid)["status"], "ready")

        # Only the second photo is rendered, so the tile shows its result --
        # while the original fallback still points at the first photo.
        render = self.render(sid, second)
        self.assertEqual(self.summary(sid)["status"], "rendering")
        self.jobs[render["job_ids"][0]]["status"] = "success"
        self.status(sid)
        row = self.summary(sid)
        self.assertEqual((row["status"], row["rendered_count"]), ("done", 1))
        self.assertEqual(row["thumbnail_result"], {"job_id": render["job_ids"][0], "run": 1})
        self.assertEqual(row["thumbnail_job_id"], first["analyze_job_id"])
        # The tile's mosaic: each photo in order, its result if it has one.
        self.assertEqual(row["thumbnails"], [
            {"job_id": first["analyze_job_id"], "run": None},
            {"job_id": render["job_ids"][0], "run": 1},
        ])

        # The first photo renders afterwards, and the tile moves to that newer
        # result even though it comes earlier in the conversation.
        later = self.render(sid, first)
        self.jobs[later["job_ids"][0]]["status"] = "success"
        self.status(sid)
        row = self.summary(sid)
        self.assertEqual(row["thumbnail_result"], {"job_id": later["job_ids"][0], "run": 1})
        self.assertEqual(row["rendered_count"], 2)

    def test_session_tile_lands_a_render_that_finished_after_the_user_left(self):
        """A render that finishes with nobody polling the session: the list
        itself lands it, instead of keeping the tile at 美化中 until the
        session is opened."""
        sid = self.new_session()
        (photo,) = self.add_photos(sid, 1)
        self.jobs[photo["analyze_job_id"]]["status"] = "success"
        self.status(sid)
        render = self.render(sid, photo)
        self.assertEqual(self.summary(sid)["status"], "rendering")
        self.jobs[render["job_ids"][0]]["status"] = "success"
        row = self.summary(sid)
        self.assertEqual((row["status"], row["rendered_count"]), ("done", 1))
        self.assertEqual(row["thumbnail_result"], {"job_id": render["job_ids"][0], "run": 1})
        messages = self.client.get(self.url(sid), query_string={"user": USER}).get_json()["messages"]
        self.assertEqual(sum(m["card"]["type"] == "render_done" for m in messages if m.get("card")), 1)

    def test_history_cases_without_a_session_are_listed(self):
        """A case no live session holds -- edited in 面板模式, or left by an
        expired session -- is a row of its own; one a session holds is not
        listed twice, and an AIPoC-path case is left out."""
        sid = self.new_session()
        photo = self.ready_photo(sid)
        self.case(photo["case_id"], [(photo["analyze_job_id"], "analyze", [photo["analyze_job_id"]])], ts=50)
        self.case("c_custom", [("a1", "analyze", ["a1"]), ("e1", "edit", ["e1"])], ts=10)
        self.case("c_upload", [("a2", "analyze", ["a2"])], ts=5)
        self.case("c_aipoc", [("a3", "edit", ["a3"])], ts=20, path="aipoc")
        rows = self.client.get("/api/photo-agent/chat/sessions", query_string={"user": USER}).get_json()["sessions"]
        self.assertEqual([r["session_id"] or r["case_id"] for r in rows], [sid, "c_custom", "c_upload"])
        custom, upload = rows[1], rows[2]
        self.assertEqual((custom["status"], custom["thumbnail_result"], custom["thumbnail_job_id"]),
                         ("done", {"job_id": "e1", "run": 1}, "e1"))
        self.assertEqual((upload["status"], upload["thumbnail_result"]), ("ready", None))

    # --- deleting rows --------------------------------------------------

    def delete_rows(self, body):
        return self.client.post("/api/photo-agent/chat/sessions/delete", json={"user": USER, **body})

    def listed(self):
        rows = self.client.get("/api/photo-agent/chat/sessions", query_string={"user": USER}).get_json()["sessions"]
        return [r["session_id"] or r["case_id"] for r in rows]

    def patch_history_deletes(self):
        cleared = []
        for p in (patch.object(chat, "delete_history_entry", lambda user, case_id: self.cases.pop(case_id, None)),
                  patch.object(chat, "clear_history", lambda user: cleared.append(user) or self.cases.clear())):
            p.start()
            self.addCleanup(p.stop)
        return cleared

    def test_a_deleted_session_takes_its_photos_history_along(self):
        """Gone from the list, and its photo does not come back as a case
        row -- 面板模式 stops listing it too."""
        self.patch_history_deletes()
        sid, other = self.new_session(), self.new_session()
        photo = self.ready_photo(sid)
        self.ready_photo(other)
        self.case(photo["case_id"], [(photo["analyze_job_id"], "analyze", [photo["analyze_job_id"]])], ts=50)
        res = self.delete_rows({"rows": [sid]})
        self.assertEqual((res.status_code, res.get_json()), (200, {"deleted": [sid]}))
        self.assertNotIn(photo["case_id"], self.cases)
        self.assertEqual(self.listed(), [other])
        self.assertEqual(self.client.get(self.url(sid), query_string={"user": USER}).status_code, 404)

    def test_a_case_row_is_deleted_from_history(self):
        self.patch_history_deletes()
        self.case("c_custom", [("a1", "analyze", ["a1"]), ("e1", "edit", ["e1"])], ts=10)
        self.case("c_keep", [("a2", "analyze", ["a2"])], ts=5)
        res = self.delete_rows({"rows": ["case:c_custom"]})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.listed(), ["c_keep"])

    def test_all_rows_are_deleted_at_once(self):
        cleared = self.patch_history_deletes()
        self.new_session()
        self.ready_photo(self.new_session())
        self.case("c_custom", [("a1", "analyze", ["a1"]), ("e1", "edit", ["e1"])], ts=10)
        res = self.delete_rows({"all": True})
        self.assertEqual((res.status_code, res.get_json()), (200, {"deleted": "all"}))
        self.assertEqual(cleared, [USER])
        self.assertEqual(self.listed(), [])

    def test_a_bad_row_deletes_nothing(self):
        self.patch_history_deletes()
        sid = self.new_session()
        self.ready_photo(sid)
        for body in ({"rows": [sid, "../x"]}, {"rows": []}, {"rows": "all"}, {}):
            with self.subTest(body=body):
                self.assertEqual(self.delete_rows(body).status_code, 400)
        self.assertEqual(self.listed(), [sid])

    def test_a_case_with_a_live_session_reopens_it(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        res = self.from_case(photo["case_id"])
        self.assertEqual((res.status_code, res.get_json()), (200, {"session_id": sid, "created": False}))

    def test_a_case_without_a_session_is_rebuilt_from_history(self):
        self.jobs.update({
            "a1": {"status": "success", "error": None, "srcKeys": ["c1/src.jpg"]},
            "e1": {"status": "success", "error": None},
            "e2": {"status": "error", "error": "boom"},
            "e3": {"status": "success", "error": None},
        })
        self.analysis.update({"photo_summary": "海邊合照", "detected_issues": ["偏暗"]})
        self.case("c1", [("a1", "analyze", ["a1"]), ("e1", "edit", ["e1"]), ("e2", "edit", ["e2"]),
                         ("e3", "edit", ["e3"])])
        res = self.from_case("c1")
        self.assertEqual(res.status_code, 201)
        sid = res.get_json()["session_id"]
        session = self.client.get(self.url(sid), query_string={"user": USER}).get_json()
        photo = session["photos"][0]
        self.assertEqual((photo["case_id"], photo["src_keys"], photo["analyze_job_id"], photo["status"]),
                         ("c1", ["c1/src.jpg"], "a1", "ready"))
        self.assertEqual(photo["active_version"]["version_id"], "e3")
        # The photo and its analysis as a fresh upload shows them, then the
        # versions that came out -- no conversation, and nothing for e2.
        self.assertEqual([m["card"]["type"] for m in session["messages"]],
                         ["photos_added", "analysis_ready", "analysis_brief", "render_done", "render_done"])
        done = [m["card"] for m in session["messages"]][3:]
        self.assertEqual([(c["version_id"], c["via"]) for c in done], [("e1", "custom"), ("e3", "custom")])
        # Opening it again neither rebuilds nor reposts.
        self.assertEqual(self.from_case("c1").get_json(), {"session_id": sid, "created": False})
        self.assertEqual(len(self.cards(sid, "render_done")), 2)

    def test_a_one_shot_case_is_rebuilt_from_its_edit_job(self):
        """A case 面板模式 made in one go (review skipped) has only edit
        versions: the first one's job carries the analysis inside its result
        metadata, and that version still shows as the first render."""
        meta = {"analysis": {**self.analysis, "photo_summary": "一次完成的合照"}}
        self.jobs.update({
            "e1": {"status": "success", "error": None, "srcKeys": ["c1/src.jpg"],
                   "0_res0": {"runs": [{"cusKeys": ["e1/result.jpg", "e1/meta.json"]}]}},
            "e2": {"status": "success", "error": None},
        })
        p = patch.object(chat, "download_bytes", lambda key, job_id: (
            json.dumps(meta if key == "e1/meta.json" else {}).encode(), None))
        p.start()
        self.addCleanup(p.stop)
        self.case("c1", [("e1", "edit", ["e1"]), ("e2", "edit", ["e2"])])
        res = self.from_case("c1")
        self.assertEqual(res.status_code, 201, res.get_json())
        sid = res.get_json()["session_id"]
        session = self.client.get(self.url(sid), query_string={"user": USER}).get_json()
        photo = session["photos"][0]
        self.assertEqual((photo["analyze_job_id"], photo["status"], photo["active_version"]["version_id"]),
                         ("e1", "ready", "e2"))
        self.assertEqual([m["card"]["type"] for m in session["messages"]],
                         ["photos_added", "analysis_ready", "analysis_brief", "render_done", "render_done"])
        self.assertEqual([c["version_id"] for c in self.cards(sid, "render_done")], ["e1", "e2"])
        res = self.client.get(self.url(sid, f"/photos/{photo['photo_id']}/analysis"), query_string={"user": USER})
        self.assertEqual(res.get_json()["result"]["analysis"]["photo_summary"], "一次完成的合照")

    def test_a_case_with_no_analysis_cannot_open_in_chat(self):
        self.case("c1", [("e1", "edit", ["e1"])])
        self.assertEqual(self.from_case("c1").status_code, 409)
        self.case("c2", [("a2", "analyze", ["a2"])])  # its job has expired out of Redis
        self.assertEqual(self.from_case("c2").status_code, 409)
        self.assertEqual(self.from_case("nope").status_code, 404)

    def test_a_custom_mode_version_shows_up_in_the_session(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        a = photo["analyze_job_id"]
        self.jobs["x1"] = {"status": "success", "error": None}
        self.case(photo["case_id"], [(a, "analyze", [a]), ("x1", "edit", ["x1"])])
        self.assertEqual([(c["version_id"], c.get("via")) for c in self.cards(sid, "render_done")], [("x1", "custom")])
        self.assertEqual(len(self.cards(sid, "render_done")), 1)  # posted once

        # One still running is followed like a chat render.
        self.jobs["x2"] = {"status": "processing", "error": None}
        self.case(photo["case_id"], [(a, "analyze", [a]), ("x1", "edit", ["x1"]), ("x2", "edit", ["x2"])])
        session = self.client.get(self.url(sid), query_string={"user": USER}).get_json()
        self.assertEqual(session["photos"][0]["status"], "rendering")
        self.jobs["x2"]["status"] = "success"
        new = self.status(sid)["new_messages"]
        self.assertEqual([(m["card"]["type"], m["card"]["version_id"], m["card"].get("via")) for m in new],
                         [("render_done", "x2", "custom")])
        self.assertNotIn("plan", new[0]["card"])
        self.assertEqual(self.status(sid)["session"]["photos"][0]["active_version"]["version_id"], "x2")

    def test_session_tile_is_failed_only_when_nothing_survived(self):
        sid = self.new_session()
        first, second = self.add_photos(sid, 2)
        self.jobs[first["analyze_job_id"]]["status"] = "error"
        self.status(sid)
        # One photo is still analyzing, so the session is not failed yet.
        self.assertEqual(self.summary(sid)["status"], "analyzing")
        self.jobs[second["analyze_job_id"]]["status"] = "error"
        self.status(sid)
        row = self.summary(sid)
        self.assertEqual((row["status"], row["thumbnail_result"], row["rendered_count"]), ("failed", None, 0))

    def test_messages_are_capped(self):
        sid = self.new_session()
        with patch.object(chat_session, "MESSAGES_MAX_LEN", 3):
            for i in range(5):
                res = self.client.post(self.url(sid, "/messages"), json={"user": USER, "text": f"m{i}"})
                self.assertEqual(res.status_code, 201)
        messages = self.client.get(self.url(sid), query_string={"user": USER}).get_json()["messages"]
        self.assertEqual([m["text"] for m in messages], ["m2", "m3", "m4"])
        self.assertEqual(self.client.post(self.url(sid, "/messages"), json={"user": USER, "role": "system", "text": "x"}).status_code, 400)

    # The upload page hands its whole conversation over in one post.
    def test_several_messages_post_at_once(self):
        sid = self.new_session()
        post = lambda body: self.client.post(self.url(sid, "/messages"), json={"user": USER, **body})
        res = post({"messages": [
            {"text": "頭髮換成棕色", "card": {"type": "ask", "pre": True}},
            {"role": "assistant", "card": {"type": "llm_reply", "pre": True, "text": "好"}},
        ]})
        self.assertEqual(res.status_code, 201)
        self.assertEqual([m["role"] for m in res.get_json()["messages"]], ["user", "assistant"])
        messages = self.client.get(self.url(sid), query_string={"user": USER}).get_json()["messages"]
        self.assertEqual([m["card"]["type"] for m in messages], ["ask", "llm_reply"])
        for bad in ([], "x", [{"role": "system", "text": "x"}], [{}], [1], [{"text": "x"}] * 101):
            with self.subTest(messages=bad):
                self.assertEqual(post({"messages": bad}).status_code, 400)
        # Nothing from a rejected batch lands.
        self.assertEqual(len(self.client.get(self.url(sid), query_string={"user": USER}).get_json()["messages"]), 2)

    def test_only_the_newest_user_message_can_be_replaced(self):
        sid = self.new_session()
        post = lambda body: self.client.post(self.url(sid, "/messages"), json={"user": USER, **body}).get_json()
        put = lambda mid, card: self.client.put(self.url(sid, f"/messages/{mid}"), json={"user": USER, "card": card})
        first = post({"card": {"type": "options_opened", "category": "face"}})
        res = put(first["id"], {"type": "options_opened", "category": "style"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["id"], first["id"])
        messages = self.client.get(self.url(sid), query_string={"user": USER}).get_json()["messages"]
        self.assertEqual([m["card"]["category"] for m in messages], ["style"])
        self.assertEqual(put(first["id"], "x").status_code, 400)
        post({"role": "assistant", "text": "later"})
        self.assertEqual(put(first["id"], {"type": "options_opened"}).status_code, 409)
        last = self.client.get(self.url(sid), query_string={"user": USER}).get_json()["messages"][-1]
        self.assertEqual(put(last["id"], {"type": "x"}).status_code, 409)

    # --- photos and analysis -----------------------------------------------

    def test_add_photos_enqueues_analyze_jobs_like_analyze(self):
        sid = self.new_session()
        photos = self.add_photos(sid, n=2)
        self.assertEqual([p["status"] for p in photos], ["analyzing", "analyzing"])
        job_id, engine_params = self.enqueued[0]
        self.assertEqual(engine_params["acts"][0]["params"]["stage"], "analyze")
        self.assertEqual(photos[0]["case_id"], engine_params["srcKeys"][0])
        self.assertEqual([h["version_id"] for h in self.history], [p["analyze_job_id"] for p in photos])
        session = self.client.get(self.url(sid), query_string={"user": USER}).get_json()
        self.assertEqual(session["active_photo_id"], photos[0]["photo_id"])
        self.assertEqual(session["messages"][0]["card"]["type"], "photos_added")

    def test_photo_cap(self):
        sid = self.new_session()
        with patch.object(chat_session, "MAX_PHOTOS_PER_SESSION", 2):
            self.add_photos(sid, n=2)
            data = {"user": USER, "image": [(io.BytesIO(b"img"), "x.jpg")]}
            res = self.client.post(self.url(sid, "/photos"), data=data, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 400)

    def test_status_finishes_analysis_once(self):
        sid = self.new_session()
        ok, bad = self.add_photos(sid, n=2)
        self.assertEqual(self.status(sid)["new_messages"], [])
        self.jobs[ok["analyze_job_id"]]["status"] = "success"
        self.jobs[bad["analyze_job_id"]] = {"status": "error", "error": "boom"}
        first = self.status(sid)
        self.assertEqual([m["card"]["type"] for m in first["new_messages"]], ["analysis_ready", "analysis_failed"])
        self.assertEqual([p["status"] for p in first["session"]["photos"]], ["ready", "failed"])
        self.assertEqual(self.status(sid)["new_messages"], [])

    def test_analysis_brief_follows_analysis_ready(self):
        self.analysis.update({"photo_summary": "海邊合照", "detected_issues": ["偏暗"], "subjects": [
            {"label": "left", "is_person": True, "blink_issue": {"blink_failure_confidence": 0.5, "should_fix": False}}],
            **ONE_FACE})
        sid = self.new_session()
        self.client.patch(self.url(sid, "/plan"), json={"user": USER, "plan": {"detailed_explanation": False}})
        photo = self.add_photos(sid)[0]
        self.jobs[photo["analyze_job_id"]]["status"] = "success"
        seen = []
        with patch.object(chat.chat_assistant, "call_opening", lambda context: seen.append(context)):
            messages = self.status(sid)["new_messages"]
        self.assertEqual(seen, [])
        self.assertEqual([m["card"]["type"] for m in messages], ["analysis_ready", "analysis_brief"])
        brief = messages[1]["card"]
        self.assertEqual((brief["photo_id"], brief["summary"], brief["issues"]), (photo["photo_id"], "海邊合照", ["偏暗"]))
        self.assertNotIn("questions", brief)

    def test_with_detailed_explanation_the_brief_is_the_opening(self):
        self.analysis.update({"photo_summary": "海邊合照", "detected_issues": ["偏暗"]})
        opening = {"lead": "海邊的光很好，我會先處理這幾件事：", "points": ["把主體提亮，臉會更清楚。"]}
        sid = self.new_session()
        self.client.patch(self.url(sid, "/plan"), json={"user": USER, "plan": {"detailed_explanation": True}})
        photo = self.add_photos(sid)[0]
        self.jobs[photo["analyze_job_id"]]["status"] = "success"
        seen = []
        with patch.object(chat.chat_assistant, "call_opening", lambda context: seen.append(context) or opening):
            brief = self.status(sid)["new_messages"][1]["card"]
        self.assertEqual((brief["summary"], brief["issues"]), (opening["lead"], opening["points"]))
        self.assertIn("WISHES: ", seen[0])

    def test_a_failed_opening_keeps_the_plain_brief(self):
        self.analysis.update({"photo_summary": "海邊合照", "detected_issues": ["偏暗"]})
        sid = self.new_session()
        self.client.patch(self.url(sid, "/plan"), json={"user": USER, "plan": {"detailed_explanation": True}})
        photo = self.add_photos(sid)[0]
        self.jobs[photo["analyze_job_id"]]["status"] = "success"

        def fail(context):
            raise RuntimeError("model down")

        with patch.object(chat.chat_assistant, "call_opening", fail):
            brief = self.status(sid)["new_messages"][1]["card"]
        self.assertEqual((brief["summary"], brief["issues"]), ("海邊合照", ["偏暗"]))

    def test_upload_page_face_picks_are_named_from_the_analysis(self):
        """The 個人化需求 written before the analysis says 人物 1; by the time
        the analysis lands that face is called left, and the box is what says
        so."""
        self.analysis.update({"subjects": [{"label": "left", "is_person": True}], **ONE_FACE})
        sid = self.new_session()
        photo = self.add_photos(sid)[0]
        res = self.client.patch(self.url(sid, f"/photos/{photo['photo_id']}/plan"), json={"user": USER, "plan": {
            "user_prompt": "人物 1（照片中從左邊數來第 1 位）：把眼鏡拿掉\n整體亮一點",
            "prompt_faces": [{"box": ONE_FACE["_local_face_boxes"][0]["box"],
                              "ref": "人物 1（照片中從左邊數來第 1 位）", "where": "照片中從左邊數來第 1 位"}]}})
        self.assertEqual(res.status_code, 200, res.get_json())
        self.jobs[photo["analyze_job_id"]]["status"] = "success"
        self.status(sid)
        plan = next(p["plan"] for p in self.status(sid)["session"]["photos"] if p["photo_id"] == photo["photo_id"])
        self.assertEqual(plan["user_prompt"], "left（照片中從左邊數來第 1 位）：把眼鏡拿掉\n整體亮一點")
        self.assertEqual(plan["prompt_faces"], [])

    def test_a_face_pick_the_analysis_cannot_place_keeps_its_words(self):
        self.analysis.update({"subjects": [{"label": "left", "is_person": True}], **ONE_FACE})
        sid = self.new_session()
        photo = self.add_photos(sid)[0]
        self.client.patch(self.url(sid, f"/photos/{photo['photo_id']}/plan"), json={"user": USER, "plan": {
            "user_prompt": "人物 2（照片中從左邊數來第 2 位）：把眼鏡拿掉",
            "prompt_faces": [{"box": {"x": 0.7, "y": 0.7, "w": 0.1, "h": 0.1},
                              "ref": "人物 2（照片中從左邊數來第 2 位）", "where": "照片中從左邊數來第 2 位"}]}})
        self.jobs[photo["analyze_job_id"]]["status"] = "success"
        self.status(sid)
        plan = next(p["plan"] for p in self.status(sid)["session"]["photos"] if p["photo_id"] == photo["photo_id"])
        self.assertEqual(plan["user_prompt"], "人物 2（照片中從左邊數來第 2 位）：把眼鏡拿掉")
        self.assertEqual(plan["prompt_faces"], [])

    # --- reframe ---------------------------------------------------------

    def per_job_analysis(self, analyses):
        """download_bytes serving a different analysis per job (default: self.analysis)."""
        def download(key, job_id):
            return json.dumps(analyses.get(job_id, self.analysis)).encode(), None
        p = patch.object(chat, "download_bytes", download)
        p.start()
        self.addCleanup(p.stop)

    def patch_plan(self, sid, photo, plan):
        res = self.client.patch(self.url(sid, f"/photos/{photo['photo_id']}/plan"), json={"user": USER, "plan": plan})
        self.assertEqual(res.status_code, 200, res.get_json())
        return res.get_json()

    def photo_row(self, sid, photo):
        return next(p for p in self.status(sid)["session"]["photos"] if p["photo_id"] == photo["photo_id"])

    def cards(self, sid, kind):
        body = self.client.get(self.url(sid), query_string={"user": USER}).get_json()
        return [m["card"] for m in body["messages"] if (m.get("card") or {}).get("type") == kind]

    def test_picking_a_ratio_reframes_right_away(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        before = len(self.enqueued)
        self.assertEqual(self.patch_plan(sid, photo, {"aspect_ratio": "4:5"})["status"], "reframing")
        job_id, engine_params = self.enqueued[before]
        params = engine_params["acts"][0]["params"]
        self.assertEqual((params["stage"], params["aspect_ratio"]), ("reframe", "4:5"))
        self.assertEqual(params["analysis"]["subject_category"], "portrait")
        self.assertEqual(engine_params["srcKeys"], self.enqueued[0][1]["srcKeys"])
        # Back to the ratio the analysis was planned for: nothing to re-plan.
        self.jobs[job_id]["status"] = "error"
        self.status(sid)
        self.patch_plan(sid, photo, {"aspect_ratio": "auto"})
        self.assertEqual(len(self.enqueued), before + 1)

    def test_reframe_adopts_the_new_analysis_and_keeps_recipe_picks(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        self.patch_plan(sid, photo, {"recipe_checked": [False, True, True]})
        self.patch_plan(sid, photo, {"aspect_ratio": "4:5"})
        job_id = self.enqueued[-1][0]
        self.per_job_analysis({job_id: {**self.analysis, "_aspect_ratio_pref_at_analysis": "4:5",
                                        "retouch_recipe": ["重新構圖：裁成 4:5 人像", "提亮主體", "看向鏡頭"]}})
        self.jobs[job_id]["status"] = "success"
        row = self.photo_row(sid, photo)
        self.assertEqual((row["status"], row["analysis_job_id"], row["planned_ratio"]), ("ready", job_id, "4:5"))
        self.assertEqual(row["plan"]["recipe_checked"], [True, False, True])
        self.assertEqual(self.cards(sid, "reframe_done")[0]["aspect_ratio"], "4:5")
        body = self.client.get(self.url(sid, f"/photos/{photo['photo_id']}/analysis"),
                               query_string={"user": USER}).get_json()
        self.assertEqual(body["result"]["analysis"]["retouch_recipe"][0], "重新構圖：裁成 4:5 人像")
        # Asked for the same ratio again: already planned.
        before = len(self.enqueued)
        self.patch_plan(sid, photo, {"aspect_ratio": "4:5"})
        self.assertEqual(len(self.enqueued), before)

    def test_failed_reframe_puts_the_ratio_back(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        self.patch_plan(sid, photo, {"aspect_ratio": "1:1"})
        job_id = self.enqueued[-1][0]
        self.jobs[job_id].update(status="error", error="boom")
        row = self.photo_row(sid, photo)
        self.assertEqual((row["status"], row["plan"]["aspect_ratio"]), ("ready", "auto"))
        card = self.cards(sid, "reframe_failed")[0]
        self.assertEqual((card["aspect_ratio"], card["previous"], card["error"]), ("1:1", "auto", "boom"))

    def test_render_while_reframing_waits_for_it(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        self.patch_plan(sid, photo, {"aspect_ratio": "4:5"})
        job_id = self.enqueued[-1][0]
        res = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": [photo["photo_id"]]})
        self.assertEqual(res.status_code, 202, res.get_json())
        before = len(self.enqueued)
        self.status(sid)
        self.assertEqual(len(self.enqueued), before)
        self.per_job_analysis({job_id: {**self.analysis, "_aspect_ratio_pref_at_analysis": "4:5"}})
        self.jobs[job_id]["status"] = "success"
        self.assertEqual(self.photo_row(sid, photo)["status"], "rendering")
        params = self.enqueued[-1][1]["acts"][0]["params"]
        self.assertEqual((params["stage"], params["aspect_ratio"]), ("finish", "4:5"))
        self.assertEqual(params["analysis"]["_aspect_ratio_pref_at_analysis"], "4:5")

    def test_ratio_picked_while_analyzing_reframes_once_ready(self):
        sid = self.new_session()
        photo = self.add_photos(sid)[0]
        self.patch_plan(sid, photo, {"aspect_ratio": "16:9"})
        self.assertEqual(len(self.enqueued), 1)
        self.jobs[photo["analyze_job_id"]]["status"] = "success"
        self.assertEqual(self.photo_row(sid, photo)["status"], "reframing")
        self.assertEqual(self.enqueued[-1][1]["acts"][0]["params"]["stage"], "reframe")

    def test_analysis_route(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        body = self.client.get(self.url(sid, f"/photos/{photo['photo_id']}/analysis"), query_string={"user": USER}).get_json()
        self.assertEqual(body["status"], "success")
        self.assertTrue(body["result"]["compatible"])
        self.assertEqual(body["result"]["compatibilityVersion"], 5)

    # --- plans -------------------------------------------------------------

    def test_photo_plans_are_independent(self):
        sid = self.new_session()
        a = self.ready_photo(sid)
        b = self.ready_photo(sid)
        res = self.client.patch(self.url(sid, f"/photos/{a['photo_id']}/plan"),
                                json={"user": USER, "plan": {"run_count": 2, "recipe_checked": [False, True, True]}})
        self.assertEqual(res.status_code, 200, res.get_json())
        res = self.client.patch(self.url(sid, f"/photos/{a['photo_id']}/plan"),
                                json={"user": USER, "plan": {"render_option": "nano-banana-2"}})
        self.assertEqual(res.status_code, 400)
        session = self.client.get(self.url(sid), query_string={"user": USER}).get_json()
        plans = {p["photo_id"]: p["plan"] for p in session["photos"]}
        self.assertEqual((plans[a["photo_id"]]["run_count"], plans[a["photo_id"]]["recipe_checked"]), (2, [False, True, True]))
        self.assertEqual((plans[b["photo_id"]]["run_count"], plans[b["photo_id"]]["recipe_checked"]), (1, []))
        self.assertEqual(session["defaults"]["run_count"], 1)

    def test_batch_plan_edit(self):
        sid = self.new_session()
        a = self.ready_photo(sid)
        failed = self.add_photos(sid)[0]
        self.jobs[failed["analyze_job_id"]]["status"] = "error"
        self.status(sid)
        self.client.patch(self.url(sid, f"/photos/{a['photo_id']}/plan"),
                          json={"user": USER, "plan": {"recipe_checked": [False]}})

        res = self.client.patch(self.url(sid, "/plan"), json={"user": USER, "plan": {"recipe_checked": [True]}})
        self.assertEqual(res.status_code, 400)
        res = self.client.patch(self.url(sid, "/plan"), json={"user": USER, "plan": {"run_count": 2}})
        self.assertEqual(res.status_code, 200, res.get_json())
        body = res.get_json()
        self.assertEqual(body["defaults"]["run_count"], 2)
        # The failed photo is left out; the photo-only picks are kept.
        self.assertEqual([p["photo_id"] for p in body["photos"]], [a["photo_id"]])
        self.assertEqual((body["photos"][0]["plan"]["run_count"], body["photos"][0]["plan"]["recipe_checked"]), (2, [False]))

        # A photo added later starts from the new defaults.
        new = self.add_photos(sid)[0]
        self.assertEqual(new["plan"]["run_count"], 2)
        # ...and while it is analyzing, batch edits wait.
        res = self.client.patch(self.url(sid, "/plan"), json={"user": USER, "plan": {"run_count": 3}})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.get_json()["not_ready"], [{"photo_id": new["photo_id"], "reason": "analyzing"}])
        self.assertEqual(self.client.get(self.url(sid), query_string={"user": USER}).get_json()["defaults"]["run_count"], 2)

    # --- render ------------------------------------------------------------

    def test_with_detailed_explanation_a_finished_version_carries_a_note(self):
        sid = self.new_session()
        self.client.patch(self.url(sid, "/plan"), json={"user": USER, "plan": {"detailed_explanation": True}})
        photo = self.ready_photo(sid)
        self.client.patch(self.url(sid, f"/photos/{photo['photo_id']}/plan"),
                          json={"user": USER, "plan": {"user_prompt": "warmer"}})
        render = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": "all"}).get_json()["renders"][0]
        seen = []

        def model(context, images=()):
            seen.append((context, images))
            return {"note": "色調暖了一點。"}

        self.jobs[render["job_ids"][0]]["status"] = "success"
        with patch.object(chat.chat_assistant, "call_render_note", model), \
                patch.object(chat, "_note_images", lambda photo, job_id: ("data:before", "data:after")):
            card = self.status(sid)["new_messages"][0]["card"]
            self.assertEqual(card["type"], "render_done")
            self.assertNotIn("note", card)
            self.assertEqual(self.status(sid)["session"]["photos"][0]["pending_note"], None)
        card = [m["card"] for m in self.client.get(self.url(sid), query_string={"user": USER}).get_json()["messages"]
                if (m.get("card") or {}).get("type") == "render_done"][-1]
        self.assertEqual(card["note"], "色調暖了一點。")
        context, images = seen[0]
        self.assertIn('"user_prompt":"warmer"', context)
        # The original and this version, side by side.
        self.assertEqual(images, [("BEFORE (the original photo):", "data:before"), ("AFTER (this version):", "data:after")])

    def test_the_next_note_knows_what_changed_and_what_was_said(self):
        sid = self.new_session()
        self.client.patch(self.url(sid, "/plan"), json={"user": USER, "plan": {"detailed_explanation": True}})
        photo = self.ready_photo(sid)
        seen = []

        def model(context, images=()):
            seen.append(context)
            return {"note": f"note {len(seen)}"}

        with patch.object(chat.chat_assistant, "call_render_note", model):
            first = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": "all"}).get_json()
            self.jobs[first["renders"][0]["job_ids"][0]]["status"] = "success"
            self.assertEqual(self.status(sid)["new_messages"][0]["card"]["plan"]["user_prompt"], "")
            self.status(sid)
            self.client.patch(self.url(sid, f"/photos/{photo['photo_id']}/plan"),
                              json={"user": USER, "plan": {"user_prompt": "warmer"}})
            second = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": "all"}).get_json()
            self.jobs[second["renders"][0]["job_ids"][0]]["status"] = "success"
            self.status(sid)
        self.assertIn("THIS ROUND: (first version)", seen[0])
        self.assertIn('THIS ROUND: [{"label":"個人化需求","added":["warmer"]}]', seen[1])
        self.assertIn("EARLIER NOTES:\n- note 1", seen[1])

    def test_without_detailed_explanation_there_is_no_note(self):
        sid = self.new_session()
        self.client.patch(self.url(sid, "/plan"), json={"user": USER, "plan": {"detailed_explanation": False}})
        self.ready_photo(sid)
        render = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": "all"}).get_json()["renders"][0]
        self.jobs[render["job_ids"][0]]["status"] = "success"

        def never(context, images=()):
            raise AssertionError("no note without detailed explanation")

        with patch.object(chat.chat_assistant, "call_render_note", never):
            card = self.status(sid)["new_messages"][0]["card"]
        self.assertEqual(card["type"], "render_done")
        self.assertNotIn("note", card)

    def test_a_failed_note_leaves_the_card_without_one(self):
        sid = self.new_session()
        self.client.patch(self.url(sid, "/plan"), json={"user": USER, "plan": {"detailed_explanation": True}})
        self.ready_photo(sid)
        render = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": "all"}).get_json()["renders"][0]
        self.jobs[render["job_ids"][0]]["status"] = "success"

        def fail(context, images=()):
            raise RuntimeError("model down")

        with patch.object(chat.chat_assistant, "call_render_note", fail):
            self.assertEqual(self.status(sid)["new_messages"][0]["card"]["type"], "render_done")
            self.assertEqual(self.status(sid)["session"]["photos"][0]["pending_note"], None)
        card = [m["card"] for m in self.client.get(self.url(sid), query_string={"user": USER}).get_json()["messages"]
                if (m.get("card") or {}).get("type") == "render_done"][-1]
        self.assertNotIn("note", card)

    def test_render_fans_out_and_records_a_version(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        self.client.patch(self.url(sid, f"/photos/{photo['photo_id']}/plan"),
                          json={"user": USER, "plan": {"run_count": 2, "user_prompt": "warmer", "recipe_checked": [False]}})
        self.history.clear()
        res = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": "all"})
        self.assertEqual(res.status_code, 202, res.get_json())
        self.assertEqual(res.get_json()["queued"], [])
        render = res.get_json()["renders"][0]
        self.assertEqual(len(render["job_ids"]), 2)
        self.assertEqual(render["action_id"], render["job_ids"][0])

        finish_jobs = [(j, p) for j, p in self.enqueued if j in render["job_ids"]]
        for _, engine_params in finish_jobs:
            params = engine_params["acts"][0]["params"]
            self.assertEqual(params["stage"], "finish")
            self.assertEqual(params["run_count"], 1)
            self.assertEqual(params["user_prompt"], "warmer")
            self.assertEqual(params["analysis"]["rejected_corrections"], ["brighten the subject"])
            self.assertEqual(engine_params["srcKeys"], [photo["case_id"]])
        self.assertEqual({h["version_id"] for h in self.history}, {render["action_id"]})
        self.assertEqual(self.history[0]["params"]["session_id"], sid)
        self.assertEqual(self.history[0]["params"]["selections"]["recipe_checked"], [False])
        self.assertIsNone(self.history[0]["based_on"])

        # A second render before this one finishes is refused.
        again = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": [photo["photo_id"]]})
        self.assertEqual(again.status_code, 409)

        self.jobs[render["job_ids"][0]]["status"] = "error"
        self.assertEqual(self.status(sid)["new_messages"], [])
        self.jobs[render["job_ids"][1]]["status"] = "success"
        done = self.status(sid)
        self.assertEqual([m["card"]["type"] for m in done["new_messages"]], ["render_done"])
        state = done["session"]["photos"][0]
        self.assertEqual(state["status"], "ready")
        version = state["active_version"]
        self.assertEqual(
            {k: version[k] for k in ("version_id", "job_id", "run")},
            {"version_id": render["action_id"], "job_id": render["job_ids"][1], "run": 1},
        )
        # Stamped so a session summary can tell which photo rendered last.
        self.assertIsInstance(version["at"], float)

        # The next render records which version it was derived from.
        self.history.clear()
        self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": "all"})
        self.assertEqual(self.history[0]["based_on"], render["action_id"])

    # --- 接著這張調整 (render from a version) ----------------------------------

    def finished_version(self, sid, photo, status="success"):
        """Render `photo` from the original, land it with `status`, and list it
        in the photo's history; the version as a source_result."""
        render = self.render(sid, photo)
        job_id = render["job_ids"][0]
        self.jobs[job_id]["status"] = status
        self.status(sid)
        self.case(photo["case_id"], [(photo["analyze_job_id"], "analyze", [photo["analyze_job_id"]]),
                                     (render["action_id"], "edit", [job_id])])
        return {"version_id": render["action_id"], "job_id": job_id, "run": 1}

    def save_draft(self, sid, photo, source, text, revision=0, **extra):
        return self.client.put(self.url(sid, f"/photos/{photo['photo_id']}/continuation-draft"),
                               json={"user": USER, "source_result": source, "text": text, "revision": revision,
                                     **extra})

    def render_from(self, sid, photo, source, **extra):
        return self.client.post(self.url(sid, "/render"), json={
            "user": USER, "photo_ids": [photo["photo_id"]], "base": "version", "source_result": source, **extra})

    def test_render_from_a_version_is_off_without_its_flag(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        source = self.finished_version(sid, photo)
        with patch.object(chat.agent_config, "ENABLE_CHAT_RENDER_FROM_VERSION", False):
            self.assertEqual(self.save_draft(sid, photo, source, "remove the cup").status_code, 404)
            self.assertEqual(self.render_from(sid, photo, source).status_code, 400)

    def test_render_from_a_version_edits_its_output_with_this_rounds_request_only(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        self.client.patch(self.url(sid, f"/photos/{photo['photo_id']}/plan"),
                          json={"user": USER, "plan": {"user_prompt": "warmer"}})
        source = self.finished_version(sid, photo)
        new_change = lambda context: {"needs_original": False, "restore": ""}
        with patch.object(chat.agent_config, "ENABLE_CHAT_RENDER_FROM_VERSION", True), \
                patch.object(chat.chat_assistant, "call_continuation", new_change):
            saved = self.save_draft(sid, photo, source, "  remove the cup  ")
            self.assertEqual(saved.status_code, 200, saved.get_json())
            draft = saved.get_json()["photos"][0]["continuation_draft"]
            self.assertEqual(draft, {"source_result": source, "text": "remove the cup", "style_options": {},
                                     "revision": 1})
            # A stale tab cannot overwrite it.
            self.assertEqual(self.save_draft(sid, photo, source, "older", revision=0).status_code, 409)

            self.history.clear()
            res = self.render_from(sid, photo, source, draft_revision=1)
            self.assertEqual(res.status_code, 202, res.get_json())
            render = res.get_json()["renders"][0]
            (job_id, engine_params), = [(j, p) for j, p in self.enqueued if j in render["job_ids"]]
            # The chosen version's output, not the original.
            self.assertEqual(engine_params["srcKeys"], [f"{source['job_id']}/analysis.json"])
            params = engine_params["acts"][0]["params"]
            self.assertEqual(params["user_prompt"], "remove the cup")
            self.assertTrue(params["overwrite_prompt"])
            self.assertEqual(params["aspect_ratio"], "original")
            self.assertFalse(any(v is True for v in params["advanced_options"].values()))
            self.assertTrue(params["analysis"]["_continuation"])
            self.assertNotIn("_continuation_original", params["analysis"])
            self.assertNotIn("rejected_corrections", params["analysis"])
            self.assertEqual(self.history[0]["case_id"], photo["case_id"])
            self.assertEqual(self.history[0]["based_on"], source["version_id"])
            self.assertEqual(self.history[0]["params"]["source_result"], source)
            self.assertEqual(self.history[0]["params"]["base"], "version")
            self.assertEqual(self.cards(sid, "render_requested")[-1]["source_result"], source)

            self.jobs[job_id]["status"] = "success"
            done = self.status(sid)
        card = done["new_messages"][0]["card"]
        self.assertEqual((card["type"], card["base"], card["source_result"], card["prompt"]),
                         ("render_done", "version", source, "remove the cup"))
        state = done["session"]["photos"][0]
        self.assertEqual(state["active_version"]["version_id"], render["action_id"])
        self.assertIsNone(state["continuation_draft"])
        # The photo's own plan is untouched.
        self.assertEqual(state["plan"]["user_prompt"], "warmer")

    def test_a_continuation_can_be_styles_alone(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        self.client.patch(self.url(sid, f"/photos/{photo['photo_id']}/plan"),
                          json={"user": USER, "plan": {"style_options": {"angle": "high"}}})
        source = self.finished_version(sid, photo)
        read = []
        with patch.object(chat.agent_config, "ENABLE_CHAT_RENDER_FROM_VERSION", True), \
                patch.object(chat.chat_assistant, "call_continuation", lambda context: read.append(context)):
            self.assertEqual(self.save_draft(sid, photo, source, "", style_options={"lighting": "nope"}).status_code,
                             400)
            saved = self.save_draft(sid, photo, source, " ", style_options={"lighting": "gold", "pose": "default"})
            self.assertEqual(saved.status_code, 200, saved.get_json())
            self.assertEqual(saved.get_json()["photos"][0]["continuation_draft"]["style_options"], {"lighting": "gold"})
            res = self.render_from(sid, photo, source)
            self.assertEqual(res.status_code, 202, res.get_json())
            render = res.get_json()["renders"][0]
        (engine_params,) = [p for j, p in self.enqueued if j in render["job_ids"]]
        params = engine_params["acts"][0]["params"]
        self.assertFalse(params.get("user_prompt"))
        # This round's styles only, not the photo's own.
        self.assertEqual(params["analysis"]["style_options"], {"lighting": "gold"})
        # Nothing to bring back, so the request is not read and the original stays out.
        self.assertEqual(read, [])
        self.assertEqual(engine_params["srcKeys"], [f"{source['job_id']}/analysis.json"])
        card = self.cards(sid, "render_requested")[-1]
        self.assertEqual((card["prompt"], card["style_options"]), ("", {"lighting": "gold"}))

    def test_a_continuation_needs_text_or_a_style(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        source = self.finished_version(sid, photo)
        with patch.object(chat.agent_config, "ENABLE_CHAT_RENDER_FROM_VERSION", True):
            saved = self.save_draft(sid, photo, source, "", style_options={"lighting": "default"})
            self.assertIsNone(saved.get_json()["photos"][0]["continuation_draft"])
            self.assertEqual(self.render_from(sid, photo, source).status_code, 409)

    def test_a_continuation_points_the_case_at_the_photos_analysis(self):
        # Its version carries a stub; 面板模式 reads the case's analysis instead.
        sid = self.new_session()
        photo = self.ready_photo(sid)
        source = self.finished_version(sid, photo)
        with patch.object(chat.agent_config, "ENABLE_CHAT_RENDER_FROM_VERSION", True), \
                patch.object(chat.chat_assistant, "call_continuation", lambda context: {"needs_original": False, "restore": ""}), \
                patch.object(chat, "attach_case_analysis") as attach:
            self.save_draft(sid, photo, source, "remove the cup")
            self.assertEqual(self.render_from(sid, photo, source).status_code, 202)
        attach.assert_called_once_with(USER, photo["case_id"], photo["analyze_job_id"])

    def continuation_job(self, sid, photo, source, text):
        self.save_draft(sid, photo, source, text)
        render = self.render_from(sid, photo, source).get_json()["renders"][0]
        (engine_params,) = [p for j, p in self.enqueued if j in render["job_ids"]]
        return engine_params

    def test_a_continuation_that_wants_something_back_also_sends_the_original(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        source = self.finished_version(sid, photo)
        seen = []
        restore = lambda context: seen.append(context) or {"needs_original": True, "restore": " the mouth "}
        with patch.object(chat.agent_config, "ENABLE_CHAT_RENDER_FROM_VERSION", True), \
                patch.object(chat.chat_assistant, "call_continuation", restore):
            engine_params = self.continuation_job(sid, photo, source, "嘴巴不要動到")
        # The version first -- it is what gets edited -- then the original.
        self.assertEqual(engine_params["srcKeys"], [f"{source['job_id']}/analysis.json", photo["src_keys"][0]])
        self.assertEqual(engine_params["acts"][0]["params"]["analysis"]["_continuation_original"],
                         {"restore": "the mouth"})
        self.assertIn("REQUEST: 嘴巴不要動到", seen[0])

    def test_a_continuation_whose_request_cannot_be_read_sends_the_original(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        source = self.finished_version(sid, photo)
        with patch.object(chat.agent_config, "ENABLE_CHAT_RENDER_FROM_VERSION", True):
            engine_params = self.continuation_job(sid, photo, source, "remove the cup")
        self.assertEqual(engine_params["srcKeys"][1:], [photo["src_keys"][0]])
        self.assertEqual(engine_params["acts"][0]["params"]["analysis"]["_continuation_original"], {"restore": ""})

    def test_a_continuation_on_an_editor_without_a_second_image_is_not_read(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        source = self.finished_version(sid, photo)
        gemini = types.SimpleNamespace(provider="gemini")
        with patch.object(chat.agent_config, "ENABLE_CHAT_RENDER_FROM_VERSION", True), \
                patch.object(chat.capabilities, "image_model", lambda model_id: gemini):
            engine_params = self.continuation_job(sid, photo, source, "嘴巴不要動到")
        self.assertEqual(engine_params["srcKeys"], [f"{source['job_id']}/analysis.json"])
        self.assertNotIn("_continuation_original", engine_params["acts"][0]["params"]["analysis"])

    def test_render_from_a_version_refuses_a_source_it_cannot_vouch_for(self):
        sid = self.new_session()
        photo, other = self.ready_photo(sid), self.ready_photo(sid)
        source = self.finished_version(sid, photo)
        failed = self.finished_version(sid, other, status="error")
        with patch.object(chat.agent_config, "ENABLE_CHAT_RENDER_FROM_VERSION", True):
            for bad, code in (({**source, "run": 2}, 404), ({**source, "job_id": "nope"}, 404),
                              ({**source, "version_id": photo["analyze_job_id"]}, 404),
                              ({**source, "run": "1"}, 400), (None, 400)):
                self.assertEqual(self.save_draft(sid, photo, bad, "x").status_code, code, bad)
            # Another photo's version is not this photo's.
            self.assertEqual(self.save_draft(sid, other, source, "x").status_code, 404)
            self.assertEqual(self.save_draft(sid, other, failed, "x").status_code, 409)
            # No draft for that source yet.
            self.assertEqual(self.render_from(sid, photo, source).status_code, 409)
            self.assertEqual(self.save_draft(sid, photo, source, "remove the cup").status_code, 200)
            self.assertEqual(self.render_from(sid, photo, source, draft_revision=5).status_code, 409)
            both = self.client.post(self.url(sid, "/render"), json={
                "user": USER, "photo_ids": [photo["photo_id"], other["photo_id"]], "base": "version",
                "source_result": source})
            self.assertEqual(both.status_code, 400)
            # Nothing above started a render.
            self.assertEqual(self.status(sid)["session"]["photos"][0]["status"], "ready")

    def test_a_failed_continuation_keeps_its_draft(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        source = self.finished_version(sid, photo)
        with patch.object(chat.agent_config, "ENABLE_CHAT_RENDER_FROM_VERSION", True):
            self.save_draft(sid, photo, source, "remove the cup")
            render = self.render_from(sid, photo, source).get_json()["renders"][0]
            self.jobs[render["job_ids"][0]]["status"] = "error"
            state = self.status(sid)["session"]["photos"][0]
        self.assertEqual(state["continuation_draft"]["text"], "remove the cup")

    def test_render_all_fails_render_failed(self):
        sid = self.new_session()
        self.ready_photo(sid)
        render = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": "all"}).get_json()["renders"][0]
        self.jobs[render["job_ids"][0]] = {"status": "error", "error": "editor down"}
        done = self.status(sid)
        self.assertEqual(done["new_messages"][0]["card"]["type"], "render_failed")
        self.assertEqual(done["session"]["photos"][0]["status"], "ready")
        self.assertIsNone(done["session"]["photos"][0]["active_version"])

    def test_render_failed_carries_raw_message_and_code(self):
        # The worker's error is a short code; its error_message is what the UI
        # can tell a safety rejection by.
        sid = self.new_session()
        self.ready_photo(sid)
        render = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": "all"}).get_json()["renders"][0]
        self.jobs[render["job_ids"][0]] = {"status": "error", "error": "unknown_internal_error",
                                           "error_message": "Image edit failed: rejected by the safety system."}
        card = self.status(sid)["new_messages"][0]["card"]
        self.assertEqual((card["error"], card["error_code"]),
                         ("Image edit failed: rejected by the safety system.", "unknown_internal_error"))

    def test_render_is_all_or_nothing(self):
        sid = self.new_session()
        ready = self.ready_photo(sid)
        failed = self.add_photos(sid)[0]
        self.jobs[failed["analyze_job_id"]]["status"] = "error"
        self.status(sid)
        before = len(self.enqueued)
        res = self.client.post(self.url(sid, "/render"),
                               json={"user": USER, "photo_ids": [ready["photo_id"], failed["photo_id"]]})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(len(self.enqueued), before)

    def test_render_all_waits_for_every_photo(self):
        sid = self.new_session()
        ready = self.ready_photo(sid)
        failed = self.add_photos(sid)[0]
        self.jobs[failed["analyze_job_id"]]["status"] = "error"
        self.status(sid)
        analyzing = self.add_photos(sid)[0]
        before = len(self.enqueued)

        res = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": "all"})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.get_json()["not_ready"], [{"photo_id": analyzing["photo_id"], "reason": "analyzing"}])
        self.assertEqual(len(self.enqueued), before)

        # Once it is ready, "all" renders every photo but the failed one.
        self.jobs[analyzing["analyze_job_id"]]["status"] = "success"
        self.status(sid)
        res = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": "all"})
        self.assertEqual(res.status_code, 202, res.get_json())
        self.assertEqual([r["photo_id"] for r in res.get_json()["renders"]], [ready["photo_id"], analyzing["photo_id"]])

    def test_render_of_an_analyzing_photo_is_queued_with_the_latest_plan(self):
        sid = self.new_session()
        photo = self.add_photos(sid)[0]
        res = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": [photo["photo_id"]]})
        self.assertEqual(res.status_code, 202, res.get_json())
        self.assertEqual((res.get_json()["renders"], res.get_json()["queued"]), ([], [photo["photo_id"]]))
        again = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": [photo["photo_id"]]})
        self.assertEqual(again.status_code, 409)

        # Edited after queueing: the render uses the edit.
        self.client.patch(self.url(sid, f"/photos/{photo['photo_id']}/plan"),
                          json={"user": USER, "plan": {"user_prompt": "cooler"}})
        self.assertEqual(self.status(sid)["new_messages"], [])
        self.jobs[photo["analyze_job_id"]]["status"] = "success"
        done = self.status(sid)
        self.assertEqual([m["card"]["type"] for m in done["new_messages"]], ["analysis_ready", "render_started"])
        started = done["new_messages"][1]["card"]
        self.assertTrue(started["queued"])
        state = done["session"]["photos"][0]
        self.assertEqual(state["status"], "rendering")
        self.assertIsNone(state["queued_render"])
        params = dict(self.enqueued)[started["job_ids"][0]]["acts"][0]["params"]
        self.assertEqual(params["user_prompt"], "cooler")

    def only_what_i_said(self, sid):
        """A photo uploaded with 「只照需求改」, its render queued as the
        upload page queues it, and its sizing-only analysis landed."""
        res = self.client.patch(self.url(sid, "/plan"), json={
            "user": USER, "plan": {"user_prompt": "make it warmer", "overwrite_prompt": True}})
        self.assertEqual(res.status_code, 200, res.get_json())
        photo = self.add_photos(sid)[0]
        self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": [photo["photo_id"]]})
        self.analysis.update({"photo_summary": "海邊合照", "detected_issues": ["偏暗"]})
        self.real_analysis, self.analysis = self.analysis, {
            "_skipped_analysis": True, "_aspect_ratio_pref_at_analysis": "auto", "compatibility_version": 5}
        self.jobs[photo["analyze_job_id"]]["status"] = "success"
        return photo, self.status(sid)

    def test_only_what_i_said_skips_the_analysis(self):
        sid = self.new_session()
        photo, done = self.only_what_i_said(sid)
        self.assertTrue(photo["analysis_skipped"])
        self.assertTrue(dict(self.enqueued)[photo["analyze_job_id"]]["acts"][0]["params"]["skip_analysis"])
        # No brief of an analysis that never ran; the render goes straight on.
        self.assertEqual([m["card"]["type"] for m in done["new_messages"]], ["analysis_ready", "render_started"])
        self.assertTrue(done["new_messages"][0]["card"]["skipped"])

    def test_asking_for_the_analysis_runs_it_and_opens_the_check(self):
        sid = self.new_session()
        photo, _ = self.only_what_i_said(sid)
        pid = photo["photo_id"]
        self.jobs[self.enqueued[-1][0]]["status"] = "success"
        self.status(sid)
        res = self.client.post(self.url(sid, f"/photos/{pid}/analyze"), json={"user": USER})
        self.assertEqual(res.status_code, 202, res.get_json())
        job_id, engine_params = self.enqueued[-1]
        self.assertNotIn("skip_analysis", engine_params["acts"][0]["params"])
        self.assertEqual(engine_params["srcKeys"], photo["src_keys"])
        self.assertEqual(self.status(sid)["session"]["photos"][0]["status"], "analyzing")

        self.analysis = self.real_analysis
        self.jobs[job_id]["status"] = "success"
        done = self.status(sid)
        cards = [m["card"] for m in done["new_messages"]]
        self.assertEqual([(c["type"], c.get("via"), c.get("requested")) for c in cards],
                         [("adjust_opened", "check", True), ("analysis_brief", None, None)])
        self.assertTrue(cards[0]["plan"]["overwrite_prompt"])
        state = done["session"]["photos"][0]
        self.assertEqual((state["status"], state["analysis_skipped"], state["analysis_job_id"]),
                         ("ready", False, job_id))
        again = self.client.post(self.url(sid, f"/photos/{pid}/analyze"), json={"user": USER})
        self.assertEqual(again.status_code, 409)

    def test_beautifying_a_skipped_photo_analyzes_it_first(self):
        sid = self.new_session()
        photo, _ = self.only_what_i_said(sid)
        pid = photo["photo_id"]
        self.jobs[self.enqueued[-1][0]]["status"] = "success"
        self.status(sid)
        self.client.patch(self.url(sid, f"/photos/{pid}/plan"), json={"user": USER, "plan": {"overwrite_prompt": False}})
        res = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": [pid]})
        self.assertEqual((res.status_code, res.get_json()["queued"]), (202, [pid]))
        job_id, engine_params = self.enqueued[-1]
        self.assertEqual(engine_params["acts"][0]["params"]["stage"], "analyze")

        self.analysis = self.real_analysis
        self.jobs[job_id]["status"] = "success"
        done = self.status(sid)
        self.assertEqual([m["card"]["type"] for m in done["new_messages"]], ["render_started", "analysis_brief"])
        self.assertEqual(done["session"]["photos"][0]["status"], "rendering")

    def analyzed_from_custom_mode(self, sid, photo, status):
        """自訂模式's 「幫我分析」 on the photo's case: an analyze job attached
        to the case (api.analyze_existing), not a version of it."""
        self.jobs[self.enqueued[-1][0]]["status"] = "success"  # the 只照需求改 render
        self.status(sid)
        self.jobs["z1"] = {"status": status, "error": None}
        a = photo["analyze_job_id"]
        self.case(photo["case_id"], [(a, "analyze", [a])])["analysis_job_id"] = "z1"

    def test_an_analysis_asked_for_in_custom_mode_reaches_the_session(self):
        sid = self.new_session()
        photo, _ = self.only_what_i_said(sid)
        self.analyzed_from_custom_mode(sid, photo, "processing")
        session = self.client.get(self.url(sid), query_string={"user": USER}).get_json()
        self.assertEqual((session["photos"][0]["status"], session["photos"][0]["analysis_job_id"]), ("analyzing", "z1"))

        self.analysis = self.real_analysis
        self.jobs["z1"]["status"] = "success"
        done = self.status(sid)
        # Its brief only: the check is 自訂模式's to open, where it was asked for.
        self.assertEqual([m["card"]["type"] for m in done["new_messages"]], ["analysis_brief"])
        state = done["session"]["photos"][0]
        self.assertEqual((state["status"], state["analysis_skipped"], state["analysis_job_id"]), ("ready", False, "z1"))
        self.assertEqual(self.status(sid)["new_messages"], [])  # adopted once

    def test_a_failed_custom_mode_analysis_is_not_adopted(self):
        sid = self.new_session()
        photo, _ = self.only_what_i_said(sid)
        self.analyzed_from_custom_mode(sid, photo, "error")
        state = self.status(sid)["session"]["photos"][0]
        self.assertEqual((state["status"], state["analysis_skipped"]), ("ready", True))

    def test_the_chats_own_analysis_is_attached_to_the_case(self):
        sid = self.new_session()
        photo, _ = self.only_what_i_said(sid)
        self.jobs[self.enqueued[-1][0]]["status"] = "success"
        self.status(sid)
        with patch.object(chat, "attach_case_analysis") as attach:
            self.client.post(self.url(sid, f"/photos/{photo['photo_id']}/analyze"), json={"user": USER})
        attach.assert_called_once_with(USER, photo["case_id"], self.enqueued[-1][0])

    def test_analyze_existing_runs_on_the_prior_jobs_photo(self):
        prior = {"j0": {"srcKeys": ["case/j0/src.jpg"]}}
        enqueued = []

        def enqueue(engine_params, src_content=None):
            enqueued.append(engine_params)
            return "new1"

        with patch.object(api, "get_job_info", lambda job_id, keyToGet=None: prior.get(job_id)), \
                patch.object(api, "enqueue", enqueue), \
                patch.object(api, "attach_case_analysis") as attach:
            res = self.client.post("/api/photo-agent/analyze/j0", json={
                "user": USER, "preset": "natural", "user_prompt": "make it warmer",
                "advanced_options": {"smile_adjustment": False}})
            self.assertEqual((res.status_code, res.get_json()), (202, {"job_id": "new1"}))
            self.assertEqual(enqueued[0]["srcKeys"], ["case/j0/src.jpg"])
            params = enqueued[0]["acts"][0]["params"]
            self.assertEqual((params["stage"], params["user_prompt"]), ("analyze", "make it warmer"))
            self.assertNotIn("skip_analysis", params)
            attach.assert_called_once_with(USER, "case/j0/src.jpg", "new1")

            missing = self.client.post("/api/photo-agent/analyze/gone", json={"user": USER})
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(len(enqueued), 1)

    def test_queued_render_is_cancelled_when_analysis_fails(self):
        sid = self.new_session()
        photo = self.add_photos(sid)[0]
        self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": [photo["photo_id"]]})
        before = len(self.enqueued)
        self.jobs[photo["analyze_job_id"]] = {"status": "error", "error": "boom"}
        done = self.status(sid)
        card = done["new_messages"][0]["card"]
        self.assertEqual((card["type"], card["queued_render_cancelled"]), ("analysis_failed", True))
        self.assertEqual(done["session"]["photos"][0]["status"], "failed")
        self.assertIsNone(done["session"]["photos"][0]["queued_render"])
        self.assertEqual(len(self.enqueued), before)

    def _lose_job(self, job_id, minutes_ago, queued=False):
        """A job with no status, queued `minutes_ago` -- still on the queue, or
        popped by a worker that died before finishing it."""
        self.jobs[job_id] = {"status": None, "error": None, "ts": int((time.time() - minutes_ago * 60) * 1000)}
        queue = MagicMock()
        queue.zscore.side_effect = lambda q, member: 1.0 if queued else None
        p = patch.object(put_job, "get_redis_conn", lambda: queue)
        p.start()
        self.addCleanup(p.stop)

    def test_a_job_lost_mid_analysis_fails_the_photo(self):
        sid = self.new_session()
        photo = self.add_photos(sid)[0]
        self._lose_job(photo["analyze_job_id"], minutes_ago=30)
        done = self.status(sid)
        card = done["new_messages"][0]["card"]
        self.assertEqual((card["type"], card["error"]), ("analysis_failed", "處理到一半中斷了"))
        self.assertEqual(done["session"]["photos"][0]["status"], "failed")

    def test_a_job_without_status_is_not_lost_while_young_or_queued(self):
        sid = self.new_session()
        photo = self.add_photos(sid)[0]
        self._lose_job(photo["analyze_job_id"], minutes_ago=3)
        self.assertEqual(self.status(sid)["session"]["photos"][0]["status"], "analyzing")
        self._lose_job(photo["analyze_job_id"], minutes_ago=30, queued=True)
        self.assertEqual(self.status(sid)["session"]["photos"][0]["status"], "analyzing")

    def test_status_reports_a_lost_job_as_failed(self):
        self._lose_job("lost1", minutes_ago=30)
        with patch.object(api, "get_job_info", lambda job_id, keyToGet=None: dict(self.jobs[job_id])):
            body = self.client.get("/api/photo-agent/status/lost1").get_json()
        self.assertEqual((body["status"], body["error"], body["error_message"]), ("error", "job_lost", "處理到一半中斷了"))
        self.assertNotIn("ts", body)

    def test_queued_render_that_cannot_start_reports_why(self):
        sid = self.new_session()
        photo = self.add_photos(sid)[0]
        self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": [photo["photo_id"]]})
        self.analysis["compatibility_version"] = 1
        self.jobs[photo["analyze_job_id"]]["status"] = "success"
        done = self.status(sid)
        self.assertEqual([m["card"]["type"] for m in done["new_messages"]], ["analysis_ready", "render_failed"])
        self.assertEqual(done["new_messages"][1]["card"]["reason"], "analysis_compatibility_version_outdated")
        state = done["session"]["photos"][0]
        self.assertEqual((state["status"], state["queued_render"]), ("ready", None))

    def test_render_rejects_bad_requests(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        cases = [
            ({"photo_ids": "all", "base": "latest"}, 400),
            ({"photo_ids": []}, 400),
            ({"photo_ids": ["p_missing"]}, 404),
        ]
        for body, code in cases:
            with self.subTest(body=body):
                self.assertEqual(self.client.post(self.url(sid, "/render"), json={"user": USER, **body}).status_code, code)
        self.analysis["compatibility_version"] = 1
        res = self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": [photo["photo_id"]]})
        self.assertEqual((res.status_code, res.get_json()["error"]), (409, "analysis_compatibility_version_outdated"))

    # --- versions ----------------------------------------------------------

    # --- typed questions ---------------------------------------------------

    def ask(self, sid, photo, text, answer, **extra):
        seen = []

        def model(context, **kwargs):
            seen.append(context)
            if isinstance(answer, Exception):
                raise answer
            return answer

        with patch.object(chat.chat_assistant, "call_model", model):
            res = self.client.post(self.url(sid, f"/photos/{photo['photo_id']}/ask"), json={"user": USER, "text": text, **extra})
        return res, seen

    def test_ask_reply_with_several_changes_is_applied(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        patch_json = '{"style_options":{"lighting":"soft"},"advanced_options":{"smile_adjustment":false}}'
        res, seen = self.ask(sid, photo, "光線柔和一點，笑容別動", {
            "reply": "可以改成柔光、不調笑容，確認後套用。", "action": "none", "patch_json": patch_json})
        self.assertEqual(res.status_code, 200, res.get_json())
        messages = res.get_json()["messages"]
        self.assertEqual([(m["role"], m["card"]["type"]) for m in messages],
                         [("user", "ask"), ("assistant", "llm_reply"), ("user", "proposal_resolved")])
        self.assertEqual(messages[0]["text"], "光線柔和一點，笑容別動")
        proposal = messages[1]["card"]["proposal"]
        self.assertEqual(proposal["patch"]["style_options"], {"lighting": "soft"})
        self.assertTrue(proposal["direct"])
        self.assertTrue(proposal["id"])
        self.assertEqual((messages[2]["card"]["choice"], messages[2]["card"]["auto"]), ("apply", True))
        self.assertIn("user: 光線柔和一點，笑容別動", seen[0])
        session = self.client.get(self.url(sid), query_string={"user": USER}).get_json()
        self.assertEqual(session["photos"][0]["plan"]["style_options"], {"lighting": "soft"})
        self.assertEqual(session["messages"][-3:], messages)
        self.assertEqual(self.enqueued[1:], [])

    def test_a_single_setting_is_applied_without_asking(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        res, _ = self.ask(sid, photo, "光線柔和一點", {
            "reply": "可以改成柔光，確認後套用。", "action": "none", "patch_json": '{"style_options":{"lighting":"soft"}}'},
            screen={"bar": ["看美化項目", "一鍵美化"], "topics": []})
        messages = res.get_json()["messages"]
        self.assertEqual([(m["role"], m["card"]["type"]) for m in messages],
                         [("user", "ask"), ("assistant", "llm_reply"), ("user", "proposal_resolved")])
        reply, resolved = messages[1]["card"], messages[2]["card"]
        self.assertTrue(reply["proposal"]["direct"])
        self.assertEqual(reply["proposal"]["undo"], {"style_options": {"lighting": "default"}})
        self.assertEqual(reply["text"], "好，光線改成「柔光」了。按下面的「一鍵美化」我就開始。")
        self.assertEqual((resolved["choice"], resolved["auto"], resolved["proposal_id"]),
                         ("apply", True, reply["proposal"]["id"]))
        session = self.client.get(self.url(sid), query_string={"user": USER}).get_json()
        self.assertEqual(session["photos"][0]["plan"]["style_options"], {"lighting": "soft"})
        self.assertEqual(self.enqueued[1:], [])

    def test_applying_after_a_render_opens_a_round(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        render = self.render(sid, photo)
        for job in render["job_ids"]:
            self.jobs[job]["status"] = "success"
        self.status(sid)
        res, _ = self.ask(sid, photo, "光線柔和一點", {
            "reply": "", "action": "none", "patch_json": '{"style_options":{"lighting":"soft"}}'})
        messages = res.get_json()["messages"]
        self.assertEqual([m["card"]["type"] for m in messages], ["ask", "llm_reply", "proposal_resolved", "adjust_opened"])
        opened = messages[3]["card"]
        self.assertEqual((opened["via"], opened["auto"], opened["plan"]["style_options"]), ("proposal", True, {}))
        # A round already open is not opened twice.
        res, _ = self.ask(sid, photo, "改成金色光調", {
            "reply": "", "action": "none", "patch_json": '{"style_options":{"lighting":"gold"}}'})
        self.assertEqual([m["card"]["type"] for m in res.get_json()["messages"]], ["ask", "llm_reply", "proposal_resolved"])

    def test_a_reply_that_opens_settings_opens_the_panel(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        said = {"reply": "美化引擎在「設定」裡，打開在下面了。", "action": "none", "patch_json": "", "open": "設定"}
        # 第一輪：直接接一張設定面板，不是使用者點的，所以標 via reply。
        res, _ = self.ask(sid, photo, "我要調整美化引擎", said, screen={"bar": ["照建議直接美化"], "topics": ["畫面"]})
        messages = res.get_json()["messages"]
        self.assertEqual([m["card"]["type"] for m in messages], ["ask", "llm_reply", "options_opened"])
        self.assertEqual((messages[2]["role"], messages[2]["card"]["category"], messages[2]["card"]["via"]),
                         ("user", "settings", "reply"))
        # 美化完：先開一輪調整（跟按「看美化項目」一樣），再打開面板。
        render = self.render(sid, photo)
        for job in render["job_ids"]:
            self.jobs[job]["status"] = "success"
        self.status(sid)
        res, _ = self.ask(sid, photo, "I'd like to change the AI model", said,
                          screen={"bar": ["看美化項目", "再美化"], "topics": []})
        messages = res.get_json()["messages"]
        self.assertEqual([m["card"]["type"] for m in messages], ["ask", "llm_reply", "adjust_opened", "options_opened"])
        opened = messages[2]["card"]
        self.assertEqual((opened["via"], opened["auto"]), ("open", True))
        self.assertEqual(opened["plan"], messages[3]["card"]["plan"])
        # 已經在調整中就不再開一輪。
        res, _ = self.ask(sid, photo, "開啟設定", said, screen={"bar": ["美化"], "topics": ["畫面", "設定"]})
        self.assertEqual([m["card"]["type"] for m in res.get_json()["messages"]], ["ask", "llm_reply", "options_opened"])

    def test_screen_panels_reach_the_model_and_open_a_folded_panel(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        render = self.render(sid, photo)
        for job in render["job_ids"]:
            self.jobs[job]["status"] = "success"
        self.status(sid)
        res, seen = self.ask(sid, photo, "我想改風格", {
            "reply": "風格打開在下面了。", "action": "none", "patch_json": "", "open": "風格"},
            screen={"bar": ["看美化項目", "再美化"], "topics": [], "panels": ["畫面", "風格", "設定"]})
        messages = res.get_json()["messages"]
        self.assertEqual([m["card"]["type"] for m in messages], ["ask", "llm_reply", "adjust_opened", "options_opened"])
        self.assertEqual(messages[3]["card"]["category"], "style")
        self.assertIn('"panels":["畫面","風格","設定"]', seen[0])

    def test_a_picked_option_is_applied_without_asking(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        self.ask(sid, photo, "幫我調一下", {"reply": "想怎麼調？", "action": "none", "patch_json": "",
                                          "options": ["柔光、不調笑容", "維持原樣"]})
        res, _ = self.ask(sid, photo, "柔光、不調笑容", {
            "reply": "", "action": "none",
            "patch_json": '{"style_options":{"lighting":"soft"},"advanced_options":{"smile_adjustment":false}}'})
        messages = res.get_json()["messages"]
        self.assertEqual([m["card"]["type"] for m in messages], ["ask", "llm_reply", "proposal_resolved"])
        self.assertEqual(messages[1]["card"]["text"], "好，已經幫你改好了。")

    def test_winding_down_puts_the_round_back(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        before = photo["plan"]
        self.client.post(self.url(sid, "/messages"), json={"user": USER, "card": {
            "type": "adjust_opened", "photo_id": photo["photo_id"],
            "plan": {"user_prompt": before["user_prompt"], "style_options": before["style_options"]}}})
        changed = self.client.patch(self.url(sid, f"/photos/{photo['photo_id']}/plan"), json={
            "user": USER, "plan": {"style_options": {"lighting": "soft"}, "user_prompt": "背景換成海邊"}})
        self.assertEqual(changed.get_json()["plan"]["style_options"], {"lighting": "soft"})
        res, _ = self.ask(sid, photo, "先不要這些改動", {
            "reply": "好的，這輪改的設定先還原了。", "action": "close", "patch_json": "", "options": []},
            screen={"bar": ["美化"], "topics": ["風格"], "pending": ["風格", "個人化需求"]})
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertTrue(res.get_json()["messages"][1]["card"]["close"])
        plan = self.client.get(self.url(sid), query_string={"user": USER}).get_json()["photos"][0]["plan"]
        self.assertEqual((plan["style_options"], plan["user_prompt"]), (before["style_options"], before["user_prompt"]))

    def test_going_back_keeps_the_changes_and_the_round_start(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        before = photo["plan"]
        self.client.post(self.url(sid, "/messages"), json={"user": USER, "card": {
            "type": "adjust_opened", "photo_id": photo["photo_id"],
            "plan": {"user_prompt": before["user_prompt"], "style_options": before["style_options"]}}})
        self.client.patch(self.url(sid, f"/photos/{photo['photo_id']}/plan"), json={
            "user": USER, "plan": {"style_options": {"lighting": "soft"}, "user_prompt": "背景換成海邊"}})
        res = self.client.post(self.url(sid, "/messages"), json={"user": USER, "card": {
            "type": "adjust_cancelled", "photo_id": photo["photo_id"], "kept": ["lighting"]}})
        self.assertEqual(res.status_code, 201, res.get_json())
        plan = self.client.get(self.url(sid), query_string={"user": USER}).get_json()["photos"][0]["plan"]
        self.assertEqual((plan["style_options"]["lighting"], plan["user_prompt"]), ("soft", "背景換成海邊"))
        # The next round starts where the parked one did, so the change still shows.
        log = chat.store.get_messages(USER, sid)
        self.assertEqual(chat._parked_round_start(log, photo["photo_id"])["user_prompt"], before["user_prompt"])
        self.assertIsNone(chat._parked_round_start(log + [{"card": {
            "type": "render_done", "photo_id": photo["photo_id"]}}], photo["photo_id"]))
        # 接著這張調整 does not use the plan, so its render leaves the changes parked.
        self.assertIsNotNone(chat._parked_round_start(log + [{"card": {
            "type": "render_done", "photo_id": photo["photo_id"], "base": "version"}}], photo["photo_id"]))

    def test_ask_to_fix_it_renders_right_away(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        res, _ = self.ask(sid, photo, "幫我修好", {"reply": "好，馬上美化。", "action": "render", "patch_json": ""})
        messages = res.get_json()["messages"]
        self.assertEqual([m["card"]["type"] for m in messages], ["ask", "llm_reply", "render_requested"])
        requested = messages[2]["card"]
        self.assertEqual((requested["label"], requested["photo_id"], requested["queued"]), ("一鍵美化", photo["photo_id"], []))
        self.assertEqual(len(requested["renders"]), 1)
        self.assertEqual(self.status(sid)["session"]["photos"][0]["status"], "rendering")

    def test_ask_to_fix_it_while_analyzing_queues_it(self):
        sid = self.new_session()
        photo = self.add_photos(sid)[0]
        res, seen = self.ask(sid, photo, "幫我修好", {"reply": "", "action": "render", "patch_json": ""})
        messages = res.get_json()["messages"]
        self.assertEqual([m["card"]["type"] for m in messages], ["ask", "render_requested"])
        self.assertEqual(messages[1]["card"]["queued"], [photo["photo_id"]])
        self.assertIn("ANALYSIS: (not available yet)", seen[0])
        self.jobs[photo["analyze_job_id"]]["status"] = "success"
        self.assertEqual(self.status(sid)["new_messages"][-1]["card"]["type"], "render_started")

    def test_ask_to_fix_it_while_rendering_says_so(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        self.client.post(self.url(sid, "/render"), json={"user": USER, "photo_ids": [photo["photo_id"]]})
        res, _ = self.ask(sid, photo, "幫我修好", {"reply": "好", "action": "render", "patch_json": ""})
        self.assertEqual([m["card"]["type"] for m in res.get_json()["messages"]], ["ask", "llm_reply", "render_failed"])
        self.assertEqual(res.get_json()["messages"][2]["card"]["reason"], "photo_busy")

    def test_ask_about_a_tapped_face(self):
        self.analysis.update({"subjects": [
            {"label": "left", "is_person": True, "blink_issue": {"blink_failure_confidence": 0.9, "should_fix": True}}],
            **ONE_FACE})
        sid = self.new_session()
        photo = self.ready_photo(sid)
        res, seen = self.ask(sid, photo, "他的眼睛不要動", {
            "reply": "可以，確認後套用。", "action": "none", "patch_json": '{"face_issue_overrides":{"0:blink_issue":false}}'},
            subject_index=0)
        messages = res.get_json()["messages"]
        self.assertEqual(messages[0]["card"]["subject_indices"], [0])
        self.assertIn("user: [pointing at left] 他的眼睛不要動", seen[0])
        self.assertEqual(messages[1]["card"]["proposal"]["changes"][0]["subject_index"], 0)
        for bad in (-1, "0", True):
            with self.subTest(subject_index=bad):
                self.assertEqual(self.ask(sid, photo, "hi", {}, subject_index=bad)[0].status_code, 400)
            with self.subTest(subject_indices=bad):
                self.assertEqual(self.ask(sid, photo, "hi", {}, subject_indices=[0, bad])[0].status_code, 400)
        self.assertEqual(self.ask(sid, photo, "hi", {}, subject_indices=0)[0].status_code, 400)

    def test_ask_about_several_tapped_faces(self):
        self.analysis.update({"subjects": [
            {"label": "left", "is_person": True}, {"label": "right", "is_person": True}],
            "_local_face_boxes": [{"box": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}},
                                  {"box": {"x": 0.6, "y": 0.1, "w": 0.2, "h": 0.2}}],
            "local_face_review": [{"detection_index": 1, "matched_subject_index": 0},
                                  {"detection_index": 2, "matched_subject_index": 1}]})
        sid = self.new_session()
        photo = self.ready_photo(sid)
        res, seen = self.ask(sid, photo, "他們都笑一下", {"reply": "好。", "action": "none"},
                             subject_indices=[1, 0, 1])
        self.assertEqual(res.get_json()["messages"][0]["card"]["subject_indices"], [1, 0])
        self.assertIn("user: [pointing at right, left] 他們都笑一下", seen[0])

    def test_ask_about_faces_of_a_photo_with_no_analysis(self):
        # 「只照需求改」: no subjects, so a tap is the detector's box and where it is.
        sid = self.new_session()
        photo = self.ready_photo(sid)
        face = {"box": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}, "where": "照片中從左邊數來第 1 位"}
        res, seen = self.ask(sid, photo, "眼睛睜開", {"reply": "好。", "action": "none"}, faces=[face])
        self.assertEqual(res.get_json()["messages"][0]["card"]["faces"], [face])
        self.assertIn("user: [pointing at 照片中從左邊數來第 1 位] 眼睛睜開", seen[0])
        for bad in ({"box": face["box"]}, {"box": {"x": 0.9, "y": 0, "w": 0.2, "h": 0.2}, "where": "x"}, "face"):
            with self.subTest(face=bad):
                self.assertEqual(self.ask(sid, photo, "hi", {}, faces=[bad])[0].status_code, 400)
        self.assertEqual(self.ask(sid, photo, "hi", {}, faces=[face], subject_indices=[0])[0].status_code, 400)

    def test_ask_model_failure_is_a_message_not_an_error(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        res, _ = self.ask(sid, photo, "你好", RuntimeError("timeout"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual([m["card"]["type"] for m in res.get_json()["messages"]], ["ask", "llm_error"])

    # A question the upload page could not answer comes along as a waiting ask
    # and is answered once the analysis is in, with the question as the line
    # to answer -- and only once.
    def test_a_waiting_question_is_answered_after_the_analysis(self):
        sid = self.new_session()
        photo = self.add_photos(sid)[0]
        posted = self.client.post(self.url(sid, "/messages"), json={
            "user": USER, "text": "這張做成啤酒海報可以用什麼文案？",
            "card": {"type": "ask", "photo_id": photo["photo_id"], "waiting": True}}).get_json()
        reply = {"reply": "可以用「乾杯夏天」。", "action": "none", "patch_json": "", "options": []}
        early, _ = self.ask(sid, photo, None, reply, waiting=posted["id"])
        self.assertEqual(early.status_code, 409)
        self.jobs[photo["analyze_job_id"]]["status"] = "success"
        self.status(sid)
        res, seen = self.ask(sid, photo, None, reply, waiting=posted["id"])
        self.assertEqual(res.status_code, 200, res.get_json())
        messages = res.get_json()["messages"]
        self.assertEqual([(m["card"]["type"], m["card"]["answers"]) for m in messages], [("llm_reply", posted["id"])])
        self.assertTrue(seen[0].rstrip().endswith("user: 這張做成啤酒海報可以用什麼文案？"))
        self.assertNotIn("not available yet", seen[0])
        self.assertIn("REPLY PLACEMENT: embedded_answer", seen[0])
        again, seen = self.ask(sid, photo, None, reply, waiting=posted["id"])
        self.assertEqual((again.get_json(), seen), ({"messages": []}, []))
        self.assertEqual(self.ask(sid, photo, None, reply, waiting="nope")[0].status_code, 404)

    # After 一鍵美化 the question waits for the retouched photo, not just the
    # analysis.
    def test_a_waiting_question_is_answered_after_the_retouch(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        posted = self.client.post(self.url(sid, "/messages"), json={
            "user": USER, "text": "幫這張想個標題",
            "card": {"type": "ask", "photo_id": photo["photo_id"], "waiting": True}}).get_json()
        started = self.render(sid, photo)
        reply = {"reply": "可以用「夏日午後」。", "action": "none", "patch_json": "", "options": []}
        early, seen = self.ask(sid, photo, None, reply, waiting=posted["id"])
        self.assertEqual((early.status_code, seen), (409, []))
        self.jobs[started["job_ids"][0]]["status"] = "success"
        self.status(sid)
        res, seen = self.ask(sid, photo, None, reply, waiting=posted["id"])
        self.assertIn("REPLY PLACEMENT: embedded_answer", seen[0])
        self.assertEqual([m["card"]["answers"] for m in res.get_json()["messages"]], [posted["id"]])

    def test_ask_rejects_bad_requests(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        answer = {"reply": "x", "action": "none", "patch_json": ""}
        self.assertEqual(self.ask(sid, photo, "  ", answer)[0].status_code, 400)
        self.assertEqual(self.ask(sid, {"photo_id": "nope"}, "hi", answer)[0].status_code, 404)
        for bad in ("調整", {"bar": "調整"}, {"topics": ["x" * 41]}, {"bar": ["a"] * 9}):
            with self.subTest(screen=bad):
                self.assertEqual(self.ask(sid, photo, "hi", answer, screen=bad)[0].status_code, 400)
        failed = self.add_photos(sid)[0]
        self.jobs[failed["analyze_job_id"]] = {"status": "error", "error": "boom"}
        self.status(sid)
        self.assertEqual(self.ask(sid, failed, "hi", answer)[0].status_code, 409)

    # --- the upload page's typed requests ----------------------------------

    def review(self, text, answer, **extra):
        seen = []

        def model(context, **kwargs):
            seen.append(context)
            if isinstance(answer, Exception):
                raise answer
            return answer

        with patch.object(chat.chat_assistant, "call_review", model):
            res = self.client.post("/api/photo-agent/chat/prompt-review", json={"text": text, **extra})
        return res, seen

    def test_a_wish_typed_before_the_session_is_taken_down(self):
        res, seen = self.review("外套換成藍色", {"verdict": "keep", "reply": "好，這個我記下來了。"},
                                prompt="左側男性的帽子拿掉")
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(res.get_json(), {"accepted": True, "reply": KEPT})
        # The model is asked about one line, against the list as it stands.
        self.assertIn("LINES SO FAR:\n[1] 未指定對象：左側男性的帽子拿掉", seen[0])
        self.assertIn("NEW LINE:\n外套換成藍色", seen[0])

    def test_the_overview_sends_its_batch_along(self):
        batch = {"photos": 3, "unrendered": 2, "analyze": "", "render": "一鍵美化其餘 2", "where": "左邊的「我的照片」"}
        res, seen = self.review("第二張背景換掉", {"verdict": "answer", "reply": "請從左邊的「我的照片」選那一張。",
                                                   "line": "", "options": []}, batch=batch)
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertFalse(res.get_json()["accepted"])
        self.assertIn("OVERVIEW of 3 photos", seen[0])
        self.assertIn("「一鍵美化其餘 2」", seen[0])
        for bad in ({"photos": "3"}, {**batch, "unrendered": -1}, {**batch, "where": "x" * 41}, "3"):
            res, _ = self.review("背景換成海邊", {"verdict": "keep"}, batch=bad)
            self.assertEqual(res.status_code, 400, bad)

    def test_a_line_that_takes_a_wish_back_is_withdrawn(self):
        res, _ = self.review("帽子留著", {"verdict": "keep", "reply": "好，帽子就不拿掉了。", "line": "", "options": [],
                                          "merged": [{"section": 1, "text": "瘦臉"}], "withdraw": True},
                             sections=[{"who": "照片中從左邊數來第 1 位", "text": "帽子拿掉、瘦臉"}])
        out = res.get_json()
        self.assertEqual(out["merged"], [{"section": 0, "text": "瘦臉"}])
        self.assertTrue(out["withdrawn"])

    def test_a_wish_that_names_a_shape_sets_the_ratio(self):
        res, _ = self.review("做成限動", {"verdict": "keep", "reply": "", "line": "", "options": [], "aspect_ratio": "9:16"})
        self.assertEqual(res.get_json()["aspect_ratio"], "9:16")
        self.assertIn("「9:16（限動）」", res.get_json()["reply"])
        res, _ = self.review("外套換成藍色", {"verdict": "keep", "reply": "", "line": "", "options": [], "aspect_ratio": ""})
        self.assertNotIn("aspect_ratio", res.get_json())

    def test_a_line_only_about_settings_changes_them_without_a_wish(self):
        res, _ = self.review("做成限動，閉眼不用修", {"verdict": "setting", "reply": "", "line": "", "options": [],
                                                 "aspect_ratio": "9:16",
                                                 "advanced_options": [{"key": "eye_closed_detection", "on": False}]})
        out = res.get_json()
        self.assertFalse(out["accepted"])
        self.assertEqual(out["aspect_ratio"], "9:16")
        self.assertEqual(out["advanced_options"], {"eye_closed_detection": False})
        self.assertIn("關掉「閉眼修正」", out["reply"])

    # The sentence the user reads and the fate of the line are one answer, so
    # a model that forgets to say what to do with the line cannot leave it
    # saying 「好，這個我記下來了。」 over a line that was thrown away.
    def test_a_verdict_that_did_not_come_back_keeps_the_line(self):
        res, _ = self.review("牛仔褲都改成裙子", {"reply": "好，這個我記下來了。"})
        self.assertEqual(res.get_json(), {"accepted": True, "reply": KEPT})

    # The same two sources disagreeing out loud: the verdict threw the line
    # away while the sentence told the user it had been taken down. The user
    # only ever sees the sentence, so the sentence is the one that holds.
    def test_a_drop_acknowledgement_without_an_interpretation_asks_first(self):
        res, _ = self.review("把桌上的垃圾都移掉", {"verdict": "drop", "reply": "好，這個我記下來了。"})
        self.assertEqual(res.get_json(), {"accepted": False, "reply": chat.chat_assistant.INTERPRETATION_CLARIFY_REPLY,
            "ask": True, "options": ['把「把桌上的垃圾都移掉」放到照片中'], "question_kind": "meaning"})

    def test_unrelated_text_without_a_supported_interpretation_asks_first(self):
        res, _ = self.review("幫我翻譯這段英文", {
            "verdict": "drop", "reply": "這個我沒辦法回答，我只能幫你處理這張照片的修圖。"})
        self.assertEqual(res.get_json(), {
            "accepted": False, "reply": chat.chat_assistant.INTERPRETATION_CLARIFY_REPLY,
            "ask": True, "options": ['把「幫我翻譯這段英文」放到照片中'], "question_kind": "meaning"})

    # The two start buttons in words: the page presses the one asked for.
    def test_a_typed_start_starts_it(self):
        res, _ = self.review("幫我修好", {"verdict": "render", "reply": "好，馬上美化。"})
        self.assertEqual(res.get_json(), {"accepted": False, "start": "render",
                                          "reply": chat.PROMPT_REVIEW_START_REPLIES["render"]})
        res, _ = self.review("先幫我分析", {"verdict": "analyze", "reply": "好。"})
        self.assertEqual(res.get_json(), {"accepted": False, "start": "analyze",
                                          "reply": chat.PROMPT_REVIEW_START_REPLIES["analyze"]})

    # Only an outright 「幫我分析」 starts it. A guessed intent is offered first,
    # with the outright commands to tap, and starts nothing.
    def test_a_guessed_start_is_offered_not_started(self):
        res, _ = self.review("這張要怎麼修？", {"verdict": "offer", "reply": "要先幫你分析這張照片，還是直接一鍵美化？",
                                            "line": "", "options": ["幫我分析", "一鍵美化"]})
        self.assertEqual(res.get_json(), {"accepted": False, "reply": "要先幫你分析這張照片，還是直接一鍵美化？",
                                          "offer": True, "options": ["幫我分析", "一鍵美化"]})
        res, _ = self.review("適合什麼風格？", {"verdict": "offer", "reply": "", "line": "", "options": []})
        self.assertEqual(res.get_json(), {"accepted": False, "reply": chat.chat_assistant.OFFER_REPLY,
                                          "offer": True, "options": chat.chat_assistant.OFFER_OPTIONS})

    # The upload page only ever leads to the two starts: a 「先不用」 the model
    # adds is dropped, and a missing start is put back.
    def test_an_offer_carries_both_starts_and_nothing_else(self):
        res, _ = self.review("這樣就好", {"verdict": "offer", "reply": "要不要直接幫你一鍵美化？", "line": "",
                                       "options": ["一鍵美化", "先不用"]})
        self.assertEqual(res.get_json()["options"], ["一鍵美化", "幫我分析"])

    # A question only the analysis can answer is flagged, so a yes carries it
    # into the session; an offer of 一鍵美化 never is.
    def test_a_question_for_after_the_analysis_is_flagged(self):
        res, _ = self.review("幫這張想個標題", {"verdict": "offer", "reply": "要不要我先幫你分析這張照片？",
                                             "line": "", "options": ["幫我分析", "一鍵美化"], "later": True})
        self.assertTrue(res.get_json()["later"])
        # Button order is presentation, not evidence that the question is answered.
        res, _ = self.review("這張適合什麼風格？", {"verdict": "offer", "reply": "分析或美化後會提供風格建議。要先開始嗎？",
                                       "line": "", "options": ["一鍵美化", "幫我分析"], "later": True})
        self.assertTrue(res.get_json()["later"])
        # Being ready to retouch has no outstanding question.
        res, _ = self.review("這樣就好", {"verdict": "offer", "reply": "要不要直接幫你一鍵美化？",
                                       "line": "", "options": ["一鍵美化", "幫我分析"], "later": False})
        self.assertNotIn("later", res.get_json())

    # The buttons under an answer offer 分析, so its last sentence must too: a
    # closing question about something else is swapped for the offer.
    def test_an_answer_closes_with_the_offer_its_buttons_answer(self):
        res, _ = self.review("長寬比要選哪個？", {"verdict": "answer", "line": "", "options": ["幫我分析", "一鍵美化"],
                                             "reply": "長寬比可以選 Auto。你想要我再說明其他美化設定嗎？"})
        self.assertEqual(res.get_json()["reply"], "長寬比可以選 Auto。要先幫你分析看看，還是直接一鍵美化？")
        res, _ = self.review("一鍵美化是什麼？", {"verdict": "answer", "line": "", "options": ["一鍵美化", "幫我分析"],
                                             "reply": "會直接修好。要不要直接幫你一鍵美化？"})
        self.assertEqual(res.get_json()["reply"], "會直接修好。要直接幫你一鍵美化，還是先分析看看？")
        res, _ = self.review("分析在做什麼？", {"verdict": "answer", "line": "", "options": ["幫我分析", "一鍵美化"],
                                            "reply": "分析會先看過這張照片。"})
        self.assertEqual(res.get_json()["reply"], "分析會先看過這張照片。要先幫你分析看看，還是直接一鍵美化？")
        # An offer mid-reply, with the promise after it, is already the offer.
        promise = "要先幫你分析這張照片，還是直接一鍵美化？分析或美化好之後就回答你。"
        res, _ = self.review("這是在哪裡拍的？", {"verdict": "answer", "line": "", "options": ["幫我分析", "一鍵美化"],
                                             "reply": promise})
        self.assertEqual(res.get_json()["reply"], promise)
        # An answer that just stops gets the offer too, but not right after one.
        res, _ = self.review("美化引擎是什麼？", {"verdict": "answer", "line": "", "options": [],
                                             "reply": "美化引擎是負責修圖的 AI 模型。"})
        self.assertEqual(res.get_json()["reply"], "美化引擎是負責修圖的 AI 模型。要先幫你分析看看，還是直接一鍵美化？")
        self.assertEqual(res.get_json()["options"], ["幫我分析", "一鍵美化"])
        history = [{"role": "assistant", "text": "要不要我先幫你分析？", "question": True, "offer": True,
                    "options": ["幫我分析", "一鍵美化"]}]
        res, _ = self.review("不用", {"verdict": "answer", "line": "", "options": [],
                                     "reply": "好的，想到要怎麼修再跟我說喔！"}, history=history)
        self.assertEqual(res.get_json()["reply"], "好的，想到要怎麼修再跟我說喔！")

    # An answer to the offer is not the answer to a question about a wish: a
    # 「好」 there is never stitched onto the line before it, and a question
    # right after an offer is still asked.
    def test_prompt_review_carries_deferred_answer_outside_offer_branch(self):
        for verdict in ("clarify", "answer", "offer"):
            with self.subTest(verdict=verdict):
                res, _ = self.review("Suggest something for this photo", {
                    "verdict": verdict, "reply": "I can suggest it after analysis. Any preferences?",
                    "line": "", "options": [], "question_kind": "content", "answer_mode": "free_text", "later": True})
                self.assertEqual(res.status_code, 200)
                answer = res.get_json()
                self.assertTrue(answer["later"])
                self.assertFalse(answer["accepted"])
                self.assertNotIn("start", answer)
                self.assertEqual(bool(answer.get("ask")), verdict == "clarify")
                self.assertEqual(bool(answer.get("offer")), verdict == "offer")

    def test_after_an_offer_the_next_line_stands_alone(self):
        history = [{"role": "user", "text": "頭髮換顏色"},
                   {"role": "assistant", "text": "要不要我先幫你分析？", "question": True, "offer": True,
                    "options": ["幫我分析", "一鍵美化"]}]
        res, seen = self.review("好", {"verdict": "analyze", "reply": "", "line": "", "options": []},
                                history=history)
        self.assertEqual(res.get_json()["start"], "analyze")
        self.assertIn("assistant (offer): 要不要我先幫你分析？ [options: 幫我分析 / 一鍵美化]", seen[0])
        res, _ = self.review("他的眼睛放大", {"verdict": "clarify", "reply": "你說的是哪一位？", "line": "",
                                          "options": []}, history=history,
                             people=["照片中從左邊數來第 1 位", "照片中從左邊數來第 2 位"])
        self.assertTrue(res.get_json()["ask"])

    # A question about the tool gets its answer and leaves the list alone, even
    # when the answer happens to sound like 「沒問題」.
    def test_a_question_about_the_tool_is_answered_not_kept(self):
        res, seen = self.review("可以換背景嗎？", {"verdict": "answer", "reply": "可以喔！想換成什麼樣的背景？直接跟我說就好。"})
        self.assertEqual(res.get_json(), {"accepted": False, "reply": "可以喔！想換成什麼樣的背景？直接跟我說就好。"})
        self.assertIn("TOOL GUIDE", seen[0])
        # An answer that ends by offering the next step carries the options.
        res, _ = self.review("分析在做什麼？", {"verdict": "answer", "reply": "…要先幫你分析看看，還是直接一鍵美化？",
                                            "options": ["幫我分析", "一鍵美化"]})
        self.assertEqual(res.get_json(), {"accepted": False, "reply": "…要先幫你分析看看，還是直接一鍵美化？",
                                          "offer": True, "options": ["幫我分析", "一鍵美化"]})

    # A model that says nothing still has to say something here.
    def test_a_silent_model_still_gets_a_sentence(self):
        res, _ = self.review("外套換成藍色", {"verdict": "keep", "reply": ""})
        self.assertEqual(res.get_json(), {"accepted": True, "reply": chat.PROMPT_REVIEW_KEPT_REPLY})
        res, _ = self.review("??", {"verdict": "drop", "reply": ""})
        self.assertEqual(res.get_json(), {"accepted": False, "reply": chat.chat_assistant.INTERPRETATION_CLARIFY_REPLY,
            "ask": True, "options": ['把「??」放到照片中'], "question_kind": "meaning"})

    # The model is not a gate the user can be left standing behind.
    def test_a_model_failure_lets_the_line_through(self):
        res, _ = self.review("外套換成藍色", RuntimeError("timeout"))
        self.assertTrue(res.get_json()["accepted"])
        self.assertTrue(res.get_json()["semantic_pending"])
        self.assertIn("保留", res.get_json()["reply"])

    # The page sends what it has: the faces found so far and the conversation,
    # so a wish can be read against who is in the photo and what was asked.
    def test_the_model_sees_the_people_and_the_conversation(self):
        history = [{"role": "user", "text": "外套換成藍色"},
                   {"role": "assistant", "text": "好，這個我記下來了。"}]
        _, seen = self.review("他的頭髮弄蓬一點", {"verdict": "keep", "reply": "好，這個我記下來了。"},
                              history=history, people=["照片中從左邊數來第 1 位", "照片中從左邊數來第 2 位"])
        self.assertIn("PEOPLE IN PHOTO:\n照片中從左邊數來第 1 位\n照片中從左邊數來第 2 位", seen[0])
        self.assertIn("CONVERSATION SO FAR:\nuser: 外套換成藍色\nassistant: 好，這個我記下來了。", seen[0])

    # A wish with a gap only the user can fill is asked about, not kept yet.
    def test_an_unfinished_wish_is_asked_about(self):
        res, _ = self.review("頭髮換顏色", {"verdict": "clarify", "reply": "想換成什麼顏色？", "line": "",
                                          "options": [" 棕色", "黑色", "棕色", "", "金色", "紅色", "灰色"], "question_kind": "content"})
        self.assertEqual(res.get_json(), {"accepted": False, "reply": "想換成什麼顏色？", "ask": True,
                                          "options": ["棕色", "黑色", "金色", "紅色"], "question_kind": "content"})
        res, _ = self.review("他的眼睛放大", {"verdict": "clarify", "reply": "", "line": "", "options": []},
                             people=["照片中從左邊數來第 1 位", "照片中從左邊數來第 2 位"])
        self.assertEqual(res.get_json(), {"accepted": False, "reply": chat.chat_assistant.CLARIFY_REPLY,
                                          "ask": True, "options": ['把「他的眼睛放大」放到照片中'], "question_kind": "meaning"})

    # The answer comes back as the whole wish, with the person split off for
    # the page to attach.
    def test_an_answer_completes_the_wish(self):
        history = [{"role": "user", "text": "頭髮換顏色"},
                   {"role": "assistant", "text": "想換成什麼顏色？", "question": True,
                    "options": ["棕色", "黑色"]}]
        semantic = dict(source="頭髮換成棕色", target="頭髮", action="換成棕色", complete=True,
                        target_kind="object", owner_required=True, scope="edit", feature="hair_color",
                        part="頭髮", intent="set", value="棕色", literal_text="", constraints=[])
        res, seen = self.review("棕色", {"verdict": "keep", "reply": "好，這個我記下來了。",
                                       "line": "頭髮換成棕色", "semantics": [semantic], "options": []},
                                history=history)
        answer = res.get_json()
        self.assertTrue(answer["accepted"])
        self.assertTrue(answer["interpreted"])
        self.assertEqual(answer["line"], "頭髮換成棕色")
        self.assertEqual(answer["request_semantics"][0]["value"], "棕色")
        self.assertIn("assistant (question): 想換成什麼顏色？ [options: 棕色 / 黑色]", seen[0])
        # A second incomplete reply must not be silently saved as a complete wish.
        res, _ = self.review("隨便", {"verdict": "clarify", "reply": "哪一種棕色？", "line": "",
                                    "options": ["深棕", "淺棕"]}, history=history)
        self.assertFalse(res.get_json()["accepted"])
        self.assertTrue(res.get_json()["ask"])

    # Outside an answer, the line is what was typed.
    def test_a_line_carrying_on_comes_back_written_out(self):
        people = ["照片中從左邊數來第 1 位", "照片中從左邊數來第 2 位"]
        res, _ = self.review("褲子也是", {"verdict": "keep", "reply": "好，這個我記下來了。",
                                       "line": "照片中從左邊數來第 2 位：褲子也換成白色", "options": []},
                             people=people)
        self.assertEqual(res.get_json(), {"accepted": True, "reply": KEPT,
                                          "line": "褲子也換成白色", "persons": ["照片中從左邊數來第 2 位"]})

    def test_a_line_about_several_people_comes_back_with_all_of_them(self):
        people = ["照片中從左邊數來第 1 位", "照片中從左邊數來第 2 位", "照片中從左邊數來第 3 位"]
        res, _ = self.review("鞋子也是", {"verdict": "keep", "reply": "好，這個我記下來了。",
                                       "line": "照片中從左邊數來第 1 位、照片中從左邊數來第 3 位：鞋子也換成白色",
                                       "options": []}, people=people)
        self.assertEqual(res.get_json(), {"accepted": True, "reply": KEPT, "line": "鞋子也換成白色",
                                          "persons": ["照片中從左邊數來第 1 位", "照片中從左邊數來第 3 位"]})

    def test_a_line_that_stands_on_its_own_is_not_sent_back(self):
        res, _ = self.review("外套換成藍色", {"verdict": "keep", "reply": "好，這個我記下來了。",
                                          "line": "外套換成藍色", "options": ["x"]})
        self.assertEqual(res.get_json(), {"accepted": True, "reply": KEPT})

    def test_a_dropped_line_requires_validated_interpretation_before_saving(self):
        res, _ = self.review("今天天氣如何", {"verdict": "drop", "reply": "這個我沒辦法幫忙。",
                                          "line": "模型改寫的內容", "options": []})
        self.assertNotIn('line', res.get_json())
        self.assertFalse(res.get_json()['accepted'])
        self.assertTrue(res.get_json()['ask'])

    def test_prompt_review_rejects_bad_requests(self):
        answer = {"verdict": "keep", "reply": "x"}
        self.assertEqual(self.review("  ", answer)[0].status_code, 400)
        self.assertEqual(self.review("hi", answer, prompt=["no"])[0].status_code, 400)
        self.assertEqual(self.review("hi", answer, prompt="x" * 2001)[0].status_code, 400)
        for bad in ("no", [{"role": "system", "text": "x"}], [{"role": "user"}],
                    [{"role": "assistant", "text": "x", "options": "a"}]):
            with self.subTest(history=bad):
                self.assertEqual(self.review("hi", answer, history=bad)[0].status_code, 400)
        for bad in ("no", [""], [1], ["x" * 41], ["x"] * 21):
            with self.subTest(people=bad):
                self.assertEqual(self.review("hi", answer, people=bad)[0].status_code, 400)

    def test_active_version_must_exist_in_history(self):
        sid = self.new_session()
        photo = self.ready_photo(sid)
        entry = {"versions": [{"version_id": "v1", "results": [{"job_id": "j1", "run": 1, "field": "0_res0"}]}]}
        url = self.url(sid, f"/photos/{photo['photo_id']}/active-version")
        with patch.object(chat, "get_history_entry", lambda user, case_id: entry if case_id == photo["case_id"] else None):
            ok = self.client.put(url, json={"user": USER, "version_id": "v1", "job_id": "j1", "run": 1})
            missing = self.client.put(url, json={"user": USER, "version_id": "v1", "job_id": "j2", "run": 1})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.get_json()["photos"][0]["active_version"]["version_id"], "v1")
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()


class CaseAnalysisHistoryTest(unittest.TestCase):
    """account's side of 「幫我分析」 on a 「只照需求改」 case: the analysis is
    filed beside the case, not as one of its versions."""

    def setUp(self):
        self.account = account

        class Redis(FakeRedis):
            def hget(self, key, field):
                return self.data.get(key, {}).get(field)

            def hdel(self, key, *fields):
                for field in fields:
                    self.data.get(key, {}).pop(field, None)

        self.redis = Redis()
        p = patch.object(account, "pf_redis_client", self.redis)
        p.start()
        self.addCleanup(p.stop)
        for case_id in ("c1", "c2"):
            self.redis.hset(account._cases_key(USER), case_id, json.dumps({"ts": 1.0, "runs": ["e1"]}))

    def test_an_attached_analysis_is_handed_back_with_its_case(self):
        self.account.attach_case_analysis(USER, "c1", "z1")
        self.assertEqual(self.account.get_history_entry(USER, "c1", include_versions=False)["analysis_job_id"], "z1")
        self.assertNotIn("analysis_job_id", self.account.get_history_entry(USER, "c2", include_versions=False))
        listed = {e["image_key"]: e.get("analysis_job_id") for e in self.account.get_history(USER)}
        self.assertEqual(listed, {"c1": "z1", "c2": None})
        # Not a version: the case's runs are what they were.
        self.assertEqual(self.account.get_history_entry(USER, "c1", include_versions=False)["runs"], ["e1"])

    def test_deleting_the_case_forgets_its_analysis(self):
        self.account.attach_case_analysis(USER, "c1", "z1")
        self.account.attach_case_analysis(USER, "c2", "z2")
        self.account.delete_history_entry(USER, "c1")
        self.assertEqual(self.redis.hgetall(self.account._case_analysis_key(USER)), {"c2": "z2"})
        self.account.clear_history(USER)
        self.assertEqual(self.redis.hgetall(self.account._case_analysis_key(USER)), {})


class ChatReportTest(unittest.TestCase):
    """回報 from the chat UI records which conversation was on screen, so the
    admin can open it; the panel UI's reports stay as they were."""

    def setUp(self):
        self.redis = FakeRedis()
        report = api.get_reports.__globals__
        # backend.services.report, as api.py sees it: no history or job to look up.
        p = patch.dict(report, {"pf_redis_client": self.redis, "get_job_info": lambda job_id, keyToGet=None: None,
                                "_case_for_report": lambda *a, **k: None})
        p.start()
        self.addCleanup(p.stop)
        self.client = app_module.create_app({"TESTING": True}).test_client()

    def reports(self):
        response = self.client.get("/api/photo-agent/reports/admin")
        self.assertEqual(response.status_code, 200)
        return response.get_json()["reports"]

    def test_a_chat_report_keeps_the_conversation_it_came_from(self):
        response = self.client.post("/api/photo-agent/report", data={
            "user": "someone", "job_id": "j1", "path": "local", "note": "怪怪的", "source": "chat",
            "owner": USER, "session_id": "s1", "photo_id": "p1", "batch_id": "b1"})
        self.assertEqual(response.status_code, 200)
        [entry] = self.reports()
        self.assertEqual({k: entry.get(k) for k in ("user", "source", "owner", "session_id", "photo_id", "batch_id")},
                         {"user": "someone", "source": "chat", "owner": USER, "session_id": "s1",
                          "photo_id": "p1", "batch_id": "b1"})

    def test_a_panel_report_has_no_chat_fields(self):
        # Chat fields without source=chat are not the chat UI's, so they are not kept.
        self.client.post("/api/photo-agent/report", data={"user": "someone", "job_id": "j1", "session_id": "s1"})
        [entry] = self.reports()
        for key in ("source", "owner", "session_id", "photo_id", "batch_id"):
            self.assertNotIn(key, entry)

    def test_a_report_brings_the_settings_its_job_was_made_with(self):
        # Looked up under the conversation's account, not the name typed into the form.
        looked_up = []
        case = {"uploaded_at": 1, "modified_at": 2, "versions": [
            {"runs": ["j0"], "params": {"user_prompt": "舊的"}},
            {"runs": ["j1"], "params": {"user_prompt": "皮膚亮一點", "preset": "vivid", "aspect_ratio": "4:5"}}]}

        def case_for_report(user, job_id, image_key=None, include_versions=False):
            looked_up.append(user)
            return case
        with patch.dict(api.get_reports.__globals__, {"_case_for_report": case_for_report}):
            self.client.post("/api/photo-agent/report", data={
                "user": "someone", "job_id": "j1", "source": "chat", "owner": USER, "session_id": "s1"})
            [entry] = self.reports()
        self.assertEqual(entry["version_params"], {"user_prompt": "皮膚亮一點", "preset": "vivid", "aspect_ratio": "4:5"})
        self.assertEqual(set(looked_up), {USER})


class ReportSessionTest(unittest.TestCase):
    """The admin's read-only look at the conversation a chat report came from."""

    def setUp(self):
        self.redis = FakeRedis()
        patches = [
            patch.dict(api.get_reports.__globals__, {"pf_redis_client": self.redis, "get_job_info": lambda job_id, keyToGet=None: None,
                                                     "_case_for_report": lambda *a, **k: None}),
            patch.object(chat_session, "_client", lambda: self.redis),
            # Reading for the admin must leave the owner's session as it was.
            patch.object(chat, "_advance_photos", MagicMock(side_effect=AssertionError("advanced"))),
            patch.object(chat, "_sync_from_history", MagicMock(side_effect=AssertionError("synced"))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = app_module.create_app({"TESTING": True}).test_client()
        self.session_id = chat_session.create_session(USER)["session_id"]
        chat_session.append_messages(USER, self.session_id, [chat_session.make_message("user", "幫我修亮一點")])

    def report(self, **fields):
        data = {"user": "someone", "job_id": "j1", "source": "chat", **fields}
        return self.client.post("/api/photo-agent/report", data=data).get_json()["report_id"]

    def open(self, report_id, viewer="admin"):
        return self.client.get("/api/photo-agent/chat/report-session", query_string={"user": viewer, "report": report_id})

    def test_the_admin_reads_the_owners_conversation(self):
        report_id = self.report(owner=USER, session_id=self.session_id, note="太暗")
        response = self.open(report_id)
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual([s["session_id"] for s in body["sessions"]], [self.session_id])
        self.assertEqual([m["text"] for m in body["sessions"][0]["messages"]], ["幫我修亮一點"])
        self.assertEqual((body["report"]["owner"], body["report"]["note"]), (USER, "太暗"))
        self.assertIsNone(body["overview"])

    def batch(self):
        batch_id = chat_session.new_id("b")
        ids = [chat_session.create_session(USER, batch_id=batch_id)["session_id"] for _ in range(2)]
        chat_session.create_session(USER, batch_id=chat_session.new_id("b"))
        chat_session.save_batch_overview(USER, batch_id, {"turns": [{"id": 1, "kind": "wish", "text": "都亮一點"}], "hints": {}})
        return batch_id, ids

    def test_a_photo_of_a_batch_brings_the_whole_batch(self):
        batch_id, ids = self.batch()
        body = self.open(self.report(owner=USER, session_id=ids[1], batch_id=batch_id)).get_json()
        self.assertEqual([s["session_id"] for s in body["sessions"]], ids)
        self.assertEqual(body["overview"]["turns"][0]["text"], "都亮一點")
        self.assertEqual(body["report"]["session_id"], ids[1])

    def test_a_report_from_the_batch_overview_opens_the_batch(self):
        batch_id, ids = self.batch()
        self.redis.ttl.clear()
        body = self.open(self.report(job_id="", owner=USER, batch_id=batch_id)).get_json()
        self.assertEqual([s["session_id"] for s in body["sessions"]], ids)
        self.assertIsNotNone(body["overview"])
        # Looking doesn't keep the user's overview alive.
        self.assertEqual(self.redis.ttl, {})

    def test_analysis_only_from_the_reported_batch(self):
        batch_id, ids = self.batch()
        report_id = self.report(owner=USER, session_id=ids[0], batch_id=batch_id)
        response = self.client.get("/api/photo-agent/chat/report-session/analysis", query_string={
            "user": "admin", "report": report_id, "session_id": self.session_id, "photo_id": "p1"})
        self.assertEqual(response.status_code, 404)

    def test_only_an_admin_can(self):
        report_id = self.report(owner=USER, session_id=self.session_id)
        self.assertEqual(self.open(report_id, viewer=USER).status_code, 403)

    def test_a_report_without_a_conversation_has_nothing_to_open(self):
        self.assertEqual(self.open(self.report()).status_code, 404)

    def panel_report(self, **fields):
        data = {"user": "someone", "job_id": "j1", "path": "local", **fields}
        return self.client.post("/api/photo-agent/report", data=data).get_json()["report_id"]

    def test_a_panel_report_opens_the_conversation_of_its_case(self):
        # 對話｜面板 switched to the panel, then 回報 there: the case still has its conversation.
        chat_session.add_photos(USER, self.session_id, chat_session.load_session(USER, self.session_id),
                                [{"photo_id": "p1", "case_id": "c1"}])
        response = self.open(self.panel_report(owner=USER, case_id="c1"))
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual([s["session_id"] for s in body["sessions"]], [self.session_id])
        self.assertEqual((body["report"]["owner"], body["report"]["photo_id"]), (USER, "p1"))

    def test_a_panel_report_of_a_case_without_a_conversation_stays_a_panel_one(self):
        report_id = self.panel_report(owner=USER, case_id="c9")
        self.assertEqual(self.open(report_id).status_code, 404)
        [entry] = self.client.get("/api/photo-agent/reports/admin").get_json()["reports"]
        for key in ("source", "owner", "session_id"):
            self.assertNotIn(key, entry)

    def test_the_panel_switch_reads_one_report_like_the_list(self):
        # 唯讀檢視的「面板」切到舊版 #report=<id>，那邊用這支拿到跟清單同一個樣子的回報。
        report_id = self.report(owner=USER, session_id=self.session_id, note="太暗")
        response = self.client.get(f"/api/photo-agent/reports/admin/{report_id}")
        self.assertEqual(response.status_code, 200)
        [listed] = self.client.get("/api/photo-agent/reports/admin").get_json()["reports"]
        self.assertEqual(response.get_json()["report"], listed)
        self.assertEqual(self.client.get(f"/api/photo-agent/reports/{USER}/{report_id}").status_code, 403)
        self.assertEqual(self.client.get("/api/photo-agent/reports/admin/nope").status_code, 404)


class AnalysisCacheTest(unittest.TestCase):
    """A finished analysis is read from S3 once; later asks reuse the bytes."""

    def setUp(self):
        self.downloads = []
        info = {"status": "success", "0_res0": {"cusKeys": ["a/analysis.json"]}}
        cache = patch.object(chat, "_analysis_bytes", chat.OrderedDict())
        for p in (cache, patch.object(chat, "get_job_info", lambda job_id, keyToGet=None: info),
                  patch.object(chat, "download_bytes", self.download)):
            p.start()
            self.addCleanup(p.stop)

    def download(self, key, job_id):
        self.downloads.append(key)
        return json.dumps({"faces": [1]}).encode(), None

    def test_a_second_load_skips_s3_and_hands_back_its_own_copy(self):
        first, _ = chat._load_analysis("j1")
        first["faces"].append(2)
        second, error = chat._load_analysis("j1")
        self.assertIsNone(error)
        self.assertEqual(second, {"faces": [1]})
        self.assertEqual(self.downloads, ["a/analysis.json"])

    def test_a_failed_download_is_not_kept(self):
        with patch.object(chat, "download_bytes", lambda key, job_id: (None, "timed out")):
            self.assertEqual(chat._load_analysis("j1"), (None, "failed to download analysis: timed out"))
        self.assertEqual(chat._load_analysis("j1")[0], {"faces": [1]})
