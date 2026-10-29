"""Offline preview/queue/handoff contract tests; no OpenAI, Redis or S3 calls."""
import copy
import io
import json
from pathlib import Path
import tempfile
import time
import types
import sys
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image
import config
import image_tools
import openai_client
import pipeline
from photo_agent.preview_context import (
    COORDINATE_SPACE, FORMAT_VERSION, source_sha256, preview_versions,
    validate_preview_context, validate_preview_binding, PREVIEW_RETENTION_SECONDS,
    PREVIEW_CONTEXT_FIELD,
)
from photo_agent.subject_preview import (
    validate_subject_preview, unavailable_preview, build_fixed_subject_roster,
    validate_fixed_subject_roster, compare_subject_preview,
)
from photo_agent.param_validation import validate_photo_agent_request
from photo_agent.chat_plan import _validated_prompt_faces
from photo_agent.chat_brief import resolve_prompt_faces
from tests.test_chat_routes import chat, USER
from tests import test_chat_routes as route_tests


def image_bytes(orientation=None):
    buf = io.BytesIO()
    img = Image.new("RGB", (160, 120), (120, 140, 160))
    if orientation:
        exif = img.getexif()
        exif[274] = orientation
        img.save(buf, "JPEG", exif=exif)
    else:
        img.save(buf, "PNG")
    return buf.getvalue()


def crops(count=2):
    return [{"box": {"x": .05 + i * .15, "y": .05, "w": .13, "h": .25},
             "face_box": {"x": .07 + i * .15, "y": .1, "w": .08, "h": .1},
             "bytes": b"crop" if i < image_tools._FACE_CROP_MAX_COUNT else None} for i in range(count)]


def raw_preview(roles=("included", "included")):
    return {"main_subject_kind": "group", "primary_detection_index": None,
        "summary": "照片中的人物初步辨識完成。", "detections": [
            {"detection_index": i, "role": role, "face_readable": True,
             "label": f"左側第{i}位男性"} for i, role in enumerate(roles, 1)]}


def binding(data, job="preview1"):
    return {"preview_job_id": job, "user": USER, "source_sha256": source_sha256(data),
        "created_at": time.time() - 1, "versions": preview_versions(),
        "analysis_model": config.MODELS["analysis"]}


def context(data=None, roles=("included", "included"), job="preview1", state="ready"):
    data = data or image_bytes()
    c = crops(len(roles))
    return {"format_version": 1, "binding": binding(data, job),
        "src_keys": [f"demo/photoAgent/input/{job}/src.jpg"], "coordinate_space": COORDINATE_SPACE,
        "image_size": list(image_tools.get_image_dimensions(data)),
        "detections": [{"detection_index": i, "box": item["box"], "face_box": item["face_box"],
                         "sent_to_model": item["bytes"] is not None} for i, item in enumerate(c, 1)],
        "subject_preview": validate_subject_preview(raw_preview(roles), c) if state == "ready"
                           else unavailable_preview("timeout")}


def overall(roles=("included", "included"), reverse=False):
    ids = [i for i, r in enumerate(roles, 1) if r == "included"]
    if reverse:
        ids.reverse()
    return {"photo_summary": "原本的完整照片分析", "composition": {"unchanged": True},
        "subjects": [{"local_detection_index": i, "label": f"第{i}位男性", "is_person": False,
                      "box": {"x": 0, "y": 0, "w": .8, "h": .9}} for i in ids],
        "local_face_review": [{"detection_index": i, "is_real_face": role != "not_real_face",
                               "matched_subject_index": ids.index(i) if i in ids else -1,
                               "reason": "分類"} for i, role in enumerate(roles, 1)]}


def response(value):
    return {"status": "completed", "output": [{"content": [
        {"type": "output_text", "text": json.dumps(value)}]}], "usage": {"input_tokens": 4, "output_tokens": 5}}


class PreviewContracts(unittest.TestCase):
    def test_roles_and_canonical_ids_including_coordinate_only_crop(self):
        cs = crops(5)
        roles = ("included", "bystander", "not_real_face", "uncertain", "included")
        result = validate_subject_preview(raw_preview(roles), cs)
        self.assertEqual([d["detection_index"] for d in result["detections"]], [1, 2, 3, 4, 5])
        self.assertEqual(result["detections"][4]["face_box"], cs[4]["face_box"])
        self.assertFalse(cs[4]["bytes"])

    def test_invalid_id_contracts_reject_whole_response(self):
        for kind in ("extra", "missing", "duplicate", "reorder", "invent", "bool"):
            with self.subTest(kind=kind):
                value = raw_preview()
                ds = value["detections"]
                if kind == "extra": ds.append(copy.deepcopy(ds[0]))
                if kind == "missing": ds.pop()
                if kind == "duplicate": ds[1]["detection_index"] = 1
                if kind == "reorder": ds.reverse()
                if kind == "invent": ds[1]["detection_index"] = 3
                if kind == "bool": ds[0]["detection_index"] = True
                with self.assertRaises(ValueError): validate_subject_preview(value, crops())

    def test_model_geometry_rejected(self):
        value = raw_preview()
        value["detections"][0]["face_box"] = crops()[1]["face_box"]
        with self.assertRaises(ValueError): validate_subject_preview(value, crops())

    def test_valid_empty_roster_is_not_unavailable(self):
        ctx = context(roles=("bystander", "not_real_face", "uncertain"))
        roster = build_fixed_subject_roster(ctx["subject_preview"])
        self.assertIsNotNone(roster)
        self.assertEqual(validate_fixed_subject_roster(roster, overall(("bystander", "not_real_face", "uncertain"))), {})
        self.assertIsNone(build_fixed_subject_roster(unavailable_preview("timeout")))

    def test_rerank_preserves_reciprocal_ids_not_is_person_or_label(self):
        roster = build_fixed_subject_roster(context()["subject_preview"])
        result = overall(reverse=True)
        self.assertEqual(validate_fixed_subject_roster(roster, result), {1: 1, 2: 0})
        result["subjects"][0]["label"] = "重新描述的主角"
        self.assertEqual(validate_fixed_subject_roster(roster, result), {1: 1, 2: 0})
        result["subjects"][0]["local_detection_index"] = 1
        with self.assertRaises(ValueError): validate_fixed_subject_roster(roster, result)

    def test_context_binding_geometry_budget_and_versions(self):
        original = context()
        self.assertEqual(validate_preview_context(original), original)
        for field, value in (("format_version", 9), ("coordinate_space", "result_image"),
                             ("src_keys", ["arbitrary"]), ("image_size", [1, 1])):
            with self.subTest(field=field):
                candidate = {**original, field: value}
                with self.assertRaises(ValueError): validate_preview_context(candidate,
                    image_bytes=image_bytes(), src_keys=original["src_keys"])
        for change in ("version", "hash", "id", "budget", "box", "model_box"):
            with self.subTest(change=change):
                candidate = copy.deepcopy(original)
                if change == "version": candidate["binding"]["versions"]["schema"] = "old"
                if change == "hash": candidate["binding"]["source_sha256"] = "0" * 64
                if change == "id": candidate["detections"][0]["detection_index"] = 2
                if change == "budget": candidate["detections"][0]["sent_to_model"] = False
                if change == "box": candidate["detections"][0]["box"]["x"] = .9
                if change == "model_box": candidate["subject_preview"]["detections"][0]["face_box"]["x"] += .01
                with self.assertRaises(ValueError): validate_preview_context(candidate, image_bytes=image_bytes())

    def test_retention_enforced_before_enqueue_not_after_freezing(self):
        candidate = context()
        candidate["binding"]["created_at"] = time.time() - PREVIEW_RETENTION_SECONDS - 2
        with self.assertRaises(ValueError): validate_preview_binding(candidate["binding"])
        self.assertEqual(validate_preview_context(candidate)["binding"], candidate["binding"])

    def test_exif_crop_restore_has_no_detector_and_keeps_crop_cap(self):
        data = image_bytes(6)
        ctx = context(data, ("included",) * 5)
        with patch.object(image_tools, "_detect_faces_px", side_effect=AssertionError("must not detect")):
            restored = image_tools.restore_face_crops(data, ctx["detections"])
        self.assertEqual(image_tools.get_image_dimensions(data), (120, 160))
        self.assertEqual([c["bytes"] is not None for c in restored], [True, True, True, False, False])
        self.assertEqual([c["face_box"] for c in restored], [c["face_box"] for c in ctx["detections"]])

    def test_restore_is_same_pixel_crop_as_detector(self):
        data = image_bytes(6)
        with patch.object(image_tools, "_detect_faces_px", return_value=[(20, 30, 30, 40, .9)]):
            original = image_tools.detect_face_crops(data)
        metadata = [{"box": c["box"], "face_box": c["face_box"], "sent_to_model": c["bytes"] is not None} for c in original]
        self.assertEqual(image_tools.restore_face_crops(data, metadata),
                         [{k: v for k, v in c.items() if k != "confidence"} for c in original])

    def test_preview_pipeline_only_detector_and_quick_request(self):
        data = image_bytes()
        from tests.test_preview_model_replies import scene_reply_v3
        from photo_agent.ab_preview import validate_selection
        for low_candidates in (False, True):
            with self.subTest(low_candidates=low_candidates):
                low_state = 'ready' if low_candidates else 'disabled'
                preview = validate_selection(scene_reply_v3(raw_preview()), crops(), [], low_state=low_state)
                with patch.object(config, "ENABLE_PREVIEW_LOW_CONFIDENCE_CANDIDATES", low_candidates), \
                     patch.object(image_tools, "detect_face_crops", return_value=crops()) as detect, \
                     patch.object(image_tools, "detect_preview_candidates", return_value={
                         "face_crops": crops(), "low_confidence_candidates": [],
                         "low_confidence_state": low_state}) as detect_ab, \
                     patch.object(openai_client, "_request_subject_preview", return_value=(preview, {"duration_seconds": .1})) as quick, \
                     patch.object(openai_client, "analyze_photo", side_effect=AssertionError("full analysis forbidden")):
                    result = pipeline.analyze_preview_stage(data, analysis_model=config.MODELS["analysis"],
                        binding=binding(data), src_keys=context(data)["src_keys"])
                (detect_ab if low_candidates else detect).assert_called_once_with(data)
                (detect if low_candidates else detect_ab).assert_not_called()
                quick.assert_called_once()
                self.assertTrue(quick.call_args.kwargs["selection_mode"])
                self.assertEqual(quick.call_args.kwargs["low_state"], low_state)
                self.assertEqual(len(result["detections"]), 2)
                self.assertEqual(result["subject_preview"]["state"], "ready")
                self.assertNotIn("subjects", result)
                self.assertIn("detector_seconds", result["timing"])

    def test_quick_request_skips_only_over_budget(self):
        with patch.object(openai_client.requests, "post", side_effect=AssertionError("no network")) as post:
            for cs in (crops(config.SUBJECT_PREVIEW_MAX_DETECTIONS + 1),):
                result, metrics = openai_client._request_subject_preview("data:image/jpeg;base64,xxx", cs, config.MODELS["analysis"])
                self.assertEqual(result["state"], "unavailable")
                self.assertFalse(metrics["attempted"])
        post.assert_not_called()

    def test_quick_request_single_call_timeout_refusal_invalid_and_success(self):
        cases = ("timeout", "refusal", "incomplete", "invalid", "success")
        for case in cases:
            with self.subTest(case=case):
                reply = MagicMock()
                data = response(raw_preview())
                if case == "refusal": data["output"][0]["content"] = [{"type": "refusal"}]
                if case == "incomplete": data["status"] = "incomplete"
                if case == "invalid": data = response(raw_preview(("included",)))
                reply.json.return_value = data
                with patch.object(openai_client.requests, "post", return_value=reply,
                    side_effect=openai_client.requests.Timeout if case == "timeout" else None) as post, \
                     patch.object(openai_client, "_auth_headers", return_value={"Authorization": "mock"}):
                    result, _ = openai_client._request_subject_preview("data:image/jpeg;base64,xxx", crops(), config.MODELS["analysis"])
                post.assert_called_once()
                self.assertEqual(post.call_args.kwargs["timeout"], config.SUBJECT_PREVIEW_TIMEOUT_SECONDS)
                self.assertEqual(result["state"], "ready" if case == "success" else "unavailable")

    def test_formal_reuses_without_detection_or_second_preview(self):
        data = image_bytes()
        for roles, state in ((("included", "included"), "ready"), (("bystander", "bystander"), "ready"),
                             (("included", "included"), "unavailable")):
            with self.subTest(roles=roles, state=state):
                ctx = context(data, roles, state=state)
                with patch.object(openai_client, "detect_face_crops", side_effect=AssertionError("no redetect")), \
                     patch.object(openai_client, "_request_subject_preview", side_effect=AssertionError("no preview rerun")), \
                     patch.object(openai_client, "_post_json", return_value=response(overall(roles, reverse=True))) as post:
                    result, usage = openai_client.analyze_photo(data, precomputed_preview=ctx)
                post.assert_called_once()
                self.assertEqual(result["photo_summary"], "原本的完整照片分析")
                self.assertEqual(result["_subject_preview_identity"]["preview_job_id"], "preview1")
                self.assertEqual(usage["usage"]["input_tokens"], 4)
                text = json.dumps(post.call_args.args[1], ensure_ascii=False)
                self.assertEqual("Roster JSON:" in text, state == "ready")

    def test_legacy_analyze_keeps_detector_original_schema_and_no_preview(self):
        with patch.object(openai_client, "detect_face_crops", return_value=[]) as detector, \
             patch.object(openai_client, "_request_subject_preview", side_effect=AssertionError("legacy no preview")), \
             patch.object(openai_client, "_post_json", return_value=response(overall(()))) as post:
            result, _ = openai_client.analyze_photo(image_bytes())
        detector.assert_called_once()
        self.assertNotIn("_subject_preview_identity", result)
        self.assertNotIn("Roster JSON:", json.dumps(post.call_args.args[1]))

    def test_one_bounded_roster_repair_preserves_photo_fields(self):
        roster = build_fixed_subject_roster(context()["subject_preview"])
        initial = overall()
        initial["subjects"][0]["local_detection_index"] = 9
        repaired = overall(reverse=True)
        with patch.object(openai_client, "_post_json", side_effect=[response(initial),
            response({k: repaired[k] for k in ("subjects", "local_face_review")})]) as post:
            result, usages, diagnostics, bindings = openai_client._analyze_overall_with_roster([], config.MODELS["analysis"], roster)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result["composition"], initial["composition"])
        self.assertEqual(bindings, {1: 1, 2: 0})
        self.assertTrue(diagnostics["repair_attempted"])
        self.assertEqual(len(usages), 2)

    def test_invalid_roster_fails_before_person_passes_never_falls_back(self):
        bad = overall()
        bad["subjects"][0]["local_detection_index"] = 9
        with patch.object(openai_client, "_post_json", return_value=response(bad)) as post, \
             patch.object(openai_client, "select_analysis_skills", side_effect=AssertionError("must validate first")), \
             patch.object(openai_client, "detect_face_crops", side_effect=AssertionError("no retry detect")):
            with self.assertRaises(openai_client.SubjectRosterError):
                openai_client.analyze_photo(image_bytes(), precomputed_preview=context())
        self.assertEqual(post.call_count, 2)

    def test_roster_handoff_runs_selected_skills_and_combines_usage(self):
        data = image_bytes()
        preview_context = context(data)
        selected_skill = MagicMock()
        skipped_skill = MagicMock()
        seen = []

        def run_skill(analysis, ctx):
            seen.append(copy.deepcopy(analysis))
            reply = ctx.request("skill instruction", {"type": "json_object"}, "skill pass")
            analysis["skill_result"] = ctx.parse(reply)
            return analysis, [reply["usage"]]

        selected_skill.run.side_effect = run_skill
        skipped_skill.skip.side_effect = lambda analysis: analysis
        with patch.object(openai_client, "detect_face_crops", side_effect=AssertionError("no redetect")), \
             patch.object(openai_client, "ANALYSIS_SKILLS", (selected_skill, skipped_skill)), \
             patch.object(openai_client, "select_analysis_skills", return_value=[selected_skill]), \
             patch.object(openai_client, "_post_json", side_effect=[
                 response(overall(reverse=True)), response({"done": True})]) as post:
            result, usage = openai_client.analyze_photo(data, precomputed_preview=preview_context)
        selected_skill.run.assert_called_once()
        skipped_skill.skip.assert_called_once()
        skipped_skill.run.assert_not_called()
        self.assertEqual(seen[0]["_subject_preview_identity"]["preview_job_id"], "preview1")
        self.assertEqual(result["skill_result"], {"done": True})
        self.assertEqual(usage["subject_roster_handoff"]["bindings"], {1: 1, 2: 0})
        self.assertEqual(usage["usage"]["input_tokens"], 8)
        self.assertEqual(usage["usage"]["output_tokens"], 10)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.args[1]["input"][0]["content"][0]["text"], "skill instruction")

    def test_face_refs_preserve_ids_and_resolve_after_reranking(self):
        ctx = context()
        ref = {"box": crops()[0]["box"], "ref": "原先的人物", "where": "左側",
            "detection_index": 1, **{k: ctx["binding"][k] for k in ("preview_job_id", "source_sha256")}}
        self.assertEqual(_validated_prompt_faces([ref])[0]["detection_index"], 1)
        formal = overall(reverse=True)
        formal["_subject_preview_identity"] = {k: ctx["binding"][k] for k in ("preview_job_id", "source_sha256")}
        # Wrong geometry must not override a new explicit ID.
        ref["box"] = crops()[1]["box"]
        self.assertEqual(resolve_prompt_faces(formal, [ref]), {"原先的人物": "第1位男性（左側）"})
        for change in ("identity", "missing", "reciprocal"):
            with self.subTest(change=change):
                bad = copy.deepcopy(formal)
                if change == "identity": bad["_subject_preview_identity"]["preview_job_id"] = "another"
                if change == "missing": bad["local_face_review"][0]["matched_subject_index"] = -1
                if change == "reciprocal": bad["subjects"][1]["local_detection_index"] = 2
                with self.assertRaises(ValueError): resolve_prompt_faces(bad, [ref])

    def test_stages_validation_and_preview_context_not_allowed_in_finish(self):
        task = {"effect": "photoAgent", "srcKeys": ["src"], "dstKeys": ["dst"], "acts": [{"id": 0, "params": {}}]}
        for stage in ("full", "analyze", "reframe", "finish"):
            task["acts"][0]["params"] = {"stage": stage}
            self.assertEqual(validate_photo_agent_request(task)["params"]["stage"], stage)
        for params in ({"stage": "unknown"}, {"stage": "analyze_preview"},
                       {"stage": "finish", "precomputed_preview": context()}):
            task["acts"][0]["params"] = params
            with self.assertRaises(RuntimeError): validate_photo_agent_request(task)


class PreviewRoutes(unittest.TestCase):
    def setUp(self):
        route_tests.ChatRoutesTest.setUp(self)
        flag = patch.object(config, "ENABLE_CHAT_SUBJECT_PREVIEW", True)
        flag.start()
        self.addCleanup(flag.stop)
        self.raw_jobs = {}
        self.artifacts = {}
        self.uploads = []

        def enqueue(engine, src_content=None, job_id=None):
            job_id = job_id or next(self.next_job)
            for key in ("srcKeys", "dstKeys"):
                engine[key] = [k.replace("{msg_id}", job_id) for k in engine[key]]
            self.raw_jobs[job_id] = {**copy.deepcopy(engine), "status": "processing"}
            self.enqueued.append((job_id, copy.deepcopy(engine)))
            self.uploads.append(src_content)
            return job_id

        for target, replacement in (("enqueue", enqueue), ("get_job_info", lambda job_id, **kw: self.raw_jobs.get(job_id)),
                                    ("download_bytes", lambda key, job_id: (self.artifacts.get(key), None))):
            p = patch.object(chat, target, replacement)
            p.start()
            self.addCleanup(p.stop)

    def create_preview(self, data=None):
        data = data or image_bytes()
        res = self.client.post("/api/photo-agent/chat/previews", data={"user": USER,
            "image": (io.BytesIO(data), "image.jpg")})
        self.assertEqual(res.status_code, 202, res.get_json())
        return res.get_json()

    def complete(self, job_id, roles=("included", "included"), state="ready"):
        info = self.raw_jobs[job_id]
        info['acts'][0]['params']['preview_format_version'] = 1
        ctx = context(roles=roles, job=job_id, state=state)
        ctx["binding"] = copy.deepcopy(info["acts"][0]["params"]["preview_binding"])
        ctx["src_keys"] = list(info["srcKeys"])
        key = info["dstKeys"][0].rsplit('/', 1)[0] + '/0_res0_preview.json'
        self.artifacts[key] = json.dumps(ctx).encode()
        info.update(status="success", **{"0_res0": {"cusKeys": [key]}})
        return ctx

    def submit(self, preview, data=None):
        sid = route_tests.ChatRoutesTest.new_session(self)
        return self.client.post(f"/api/photo-agent/chat/sessions/{sid}/photos", data={"user": USER,
            "image": (io.BytesIO(data or image_bytes()), "image.jpg"), **preview})

    def publish_snapshot(self, job_id, **kwargs):
        ctx = self.complete(job_id, **kwargs)
        info = self.raw_jobs[job_id]
        info[PREVIEW_CONTEXT_FIELD] = {**copy.deepcopy(ctx), "timing": {"completed_at": time.time()}}
        info["status"] = "processing"
        info.pop("0_res0", None)
        self.artifacts.clear()  # no artifact exists yet
        return ctx

    def test_early_snapshot_displays_and_freezes_without_s3_or_job_success(self):
        preview = self.create_preview()
        job = preview["preview_job_id"]
        ctx = self.publish_snapshot(job)
        with patch.object(chat, "download_bytes", side_effect=AssertionError("no S3 read")):
            body = self.client.get(f"/api/photo-agent/chat/previews/{job}?user={USER}").get_json()
            res = self.submit({k: preview[k] for k in ("preview_job_id", "source_sha256")})
        self.assertEqual(body["status"], "processing")
        self.assertEqual(body["state"], "ready")
        self.assertTrue(body["reusable"])
        self.assertEqual([f["label"] for f in body["faces"]], ["左側第1位男性", "左側第2位男性"])
        self.assertNotIn("binding", body)
        self.assertNotIn("src_keys", body)
        self.assertEqual(res.status_code, 202, res.get_json())
        frozen = copy.deepcopy(self.enqueued[-1][1])
        self.assertIsNone(self.uploads[-1])
        self.assertEqual(frozen["acts"][0]["params"]["precomputed_preview"], validate_preview_context(ctx))
        self.raw_jobs[job][PREVIEW_CONTEXT_FIELD]["detections"].clear()
        self.assertEqual(self.enqueued[-1][1], frozen)

    def test_failed_upload_does_not_discard_completed_preview(self):
        preview = self.create_preview()
        job = preview["preview_job_id"]
        ctx = self.publish_snapshot(job)
        self.raw_jobs[job]["status"] = "error"
        with patch.object(chat, "download_bytes", side_effect=AssertionError("no S3 read")):
            body = self.client.get(f"/api/photo-agent/chat/previews/{job}?user={USER}").get_json()
            res = self.submit({k: preview[k] for k in ("preview_job_id", "source_sha256")})
        self.assertEqual(body["state"], "ready")
        self.assertEqual(body["status"], "error")
        self.assertTrue(body["reusable"])
        self.assertEqual(res.status_code, 202)
        self.assertEqual(self.enqueued[-1][1]["acts"][0]["params"]["precomputed_preview"], validate_preview_context(ctx))

    def test_invalid_cached_binding_or_geometry_cannot_display_or_reuse(self):
        for change in ("source", "binding", "geometry"):
            with self.subTest(change=change):
                preview = self.create_preview()
                job = preview["preview_job_id"]
                self.publish_snapshot(job)
                cached = self.raw_jobs[job][PREVIEW_CONTEXT_FIELD]
                if change == "source": cached["src_keys"] = ["attacker"]
                if change == "binding": cached["binding"]["preview_job_id"] = "other"
                if change == "geometry": cached["detections"][0]["face_box"]["x"] = .99
                body = self.client.get(f"/api/photo-agent/chat/previews/{job}?user={USER}").get_json()
                self.assertEqual(body["state"], "pending")
                self.assertNotIn("faces", body)
                res = self.submit({k: preview[k] for k in ("preview_job_id", "source_sha256")})
                self.assertEqual(res.status_code, 202)
                self.assertNotIn("precomputed_preview", self.enqueued[-1][1]["acts"][0]["params"])

    def test_invalid_cache_can_fall_back_to_valid_completed_artifact(self):
        preview = self.create_preview()
        job = preview["preview_job_id"]
        self.complete(job)
        self.raw_jobs[job][PREVIEW_CONTEXT_FIELD] = {"format_version": 9}
        body = self.client.get(f"/api/photo-agent/chat/previews/{job}?user={USER}").get_json()
        self.assertEqual(body["state"], "ready")
        self.assertTrue(body["reusable"])

    def test_early_snapshot_unavailable_or_no_faces_is_still_reusable(self):
        for roles in ((), ("included", "included")):
            preview = self.create_preview()
            job = preview["preview_job_id"]
            ctx = self.publish_snapshot(job, roles=roles, state="unavailable")
            body = self.client.get(f"/api/photo-agent/chat/previews/{job}?user={USER}").get_json()
            self.assertEqual(body["state"], "unavailable")
            self.assertTrue(body["reusable"])
            res = self.submit({k: preview[k] for k in ("preview_job_id", "source_sha256")})
            self.assertEqual(res.status_code, 202)
            self.assertEqual(self.enqueued[-1][1]["acts"][0]["params"]["precomputed_preview"], validate_preview_context(ctx))

    def test_post_enqueues_once_no_session_history_or_flask_detection(self):
        with patch.object(image_tools, "detect_face_crops", side_effect=AssertionError("worker only")), \
             patch.object(image_tools, "detect_face_boxes", side_effect=AssertionError("no legacy duplicate")):
            result = self.create_preview()
        self.assertEqual(len(self.enqueued), 1)
        self.assertEqual(self.enqueued[0][1]["acts"][0]["params"]["stage"], "analyze_preview")
        self.assertEqual(result["source_sha256"], source_sha256(image_bytes()))
        self.assertEqual(self.history, [])
        self.assertEqual(self.redis.data, {})

    def test_status_pending_ready_unavailable_and_error_are_distinct(self):
        preview = self.create_preview()
        job = preview["preview_job_id"]
        url = f"/api/photo-agent/chat/previews/{job}?user={USER}"
        self.assertEqual(self.client.get(url).get_json()["state"], "pending")
        self.complete(job)
        body = self.client.get(url).get_json()
        self.assertEqual(body["state"], "ready")
        self.assertEqual([f["detection_index"] for f in body["faces"]], [1, 2])
        self.assertNotIn("src_keys", body)
        self.assertNotIn("usage", body)
        self.complete(job, state="unavailable")
        body = self.client.get(url).get_json()
        self.assertEqual(body["state"], "unavailable")
        self.assertEqual(len(body["faces"]), 2)
        self.raw_jobs[job].update(status="error", error="secret internal path")
        body = self.client.get(url).get_json()
        self.assertEqual(body["state"], "error")
        self.assertNotIn("secret", json.dumps(body))

    def test_no_detections_finishes_without_faces(self):
        preview = self.create_preview()
        self.complete(preview["preview_job_id"], roles=(), state="unavailable")
        body = self.client.get(f"/api/photo-agent/chat/previews/{preview['preview_job_id']}?user={USER}").get_json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["faces"], [])

    def test_owner_type_expiry_and_bad_artifact_fail_closed(self):
        preview = self.create_preview()
        job = preview["preview_job_id"]
        self.complete(job)
        self.assertEqual(self.client.get(f"/api/photo-agent/chat/previews/{job}?user=other").status_code, 404)
        self.raw_jobs[job]["acts"][0]["params"]["stage"] = "analyze"
        self.assertEqual(self.client.get(f"/api/photo-agent/chat/previews/{job}?user={USER}").status_code, 404)
        self.raw_jobs[job]["acts"][0]["params"]["stage"] = "analyze_preview"
        self.raw_jobs[job]["0_res0"]["cusKeys"] = ["arbitrary/path"]
        self.assertEqual(self.client.get(f"/api/photo-agent/chat/previews/{job}?user={USER}").status_code, 502)
        self.raw_jobs[job]["acts"][0]["params"]["preview_binding"]["created_at"] = 0
        self.assertEqual(self.client.get(f"/api/photo-agent/chat/previews/{job}?user={USER}").status_code, 404)

    def test_formal_freezes_snapshot_and_reuses_only_server_source(self):
        preview = self.create_preview()
        ctx = self.complete(preview["preview_job_id"])
        response_ = self.submit({"preview_job_id": preview["preview_job_id"], "source_sha256": preview["source_sha256"],
                                 "srcKeys": "attacker"})
        self.assertEqual(response_.status_code, 202, response_.get_json())
        job_id, engine = self.enqueued[-1]
        self.assertNotEqual(job_id, preview["preview_job_id"])
        self.assertIsNone(self.uploads[-1])
        self.assertEqual(engine["srcKeys"], ctx["src_keys"])
        self.assertEqual(engine["acts"][0]["params"]["precomputed_preview"], validate_preview_context(ctx))
        self.assertEqual(len(self.history), 1)

    def test_pending_submission_never_waits_worker_or_late_mutates_snapshot(self):
        preview = self.create_preview()
        res = self.submit({k: preview[k] for k in ("preview_job_id", "source_sha256")})
        self.assertEqual(res.status_code, 202)
        frozen = copy.deepcopy(self.enqueued[-1][1])
        self.assertNotIn("precomputed_preview", frozen["acts"][0]["params"])
        self.complete(preview["preview_job_id"])
        self.assertEqual(self.enqueued[-1][1], frozen)

    def test_wrong_photo_or_wrong_user_cannot_reuse_source(self):
        preview = self.create_preview()
        self.complete(preview["preview_job_id"])
        res = self.submit({k: preview[k] for k in ("preview_job_id", "source_sha256")}, image_bytes(6))
        self.assertEqual(res.status_code, 409)
        self.raw_jobs[preview["preview_job_id"]]["acts"][0]["params"]["preview_binding"]["user"] = "other"
        res = self.submit({k: preview[k] for k in ("preview_job_id", "source_sha256")})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(len(self.enqueued), 1)

    def test_feature_off_restores_ordinary_upload(self):
        with patch.object(config, "ENABLE_CHAT_SUBJECT_PREVIEW", False):
            res = self.client.post("/api/photo-agent/chat/previews", data={"user": USER,
                "image": (io.BytesIO(image_bytes()), "a.jpg")})
            self.assertEqual(res.status_code, 404)
            res = self.submit({"preview_job_id": "ignored", "source_sha256": "ignored"})
        self.assertEqual(res.status_code, 202)
        self.assertIsNotNone(self.uploads[-1])
        self.assertNotIn("precomputed_preview", self.enqueued[-1][1]["acts"][0]["params"])

    def test_invalid_image_and_model_rejected_without_enqueue(self):
        for data, status in ((b"not image", 422), (image_bytes(), 400)):
            res = self.client.post("/api/photo-agent/chat/previews", data={"user": USER,
                "analysis_model": "invalid" if status == 400 else config.MODELS["analysis"],
                "image": (io.BytesIO(data), "a.jpg")})
            self.assertEqual(res.status_code, status)
        self.assertEqual(self.enqueued, [])

    def test_large_upload_rejected_without_enqueue(self):
        with patch.object(chat, "MAX_UPLOAD_BYTES", 16):
            res = self.client.post("/api/photo-agent/chat/previews", data={"user": USER,
                "image": (io.BytesIO(image_bytes()), "a.jpg")})
        self.assertEqual(res.status_code, 413)
        self.assertEqual(self.enqueued, [])

    def test_owned_thumbnail_decodes_exif_and_denies_other_user(self):
        preview = self.create_preview(image_bytes(6))
        job = preview["preview_job_id"]
        self.artifacts[self.raw_jobs[job]["srcKeys"][0]] = image_bytes(6)
        url = f"/api/photo-agent/chat/previews/{job}/image?user={USER}"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(image_tools.get_image_dimensions(res.data), (120, 160))
        self.assertEqual(self.client.get(f"/api/photo-agent/chat/previews/{job}/image?user=other").status_code, 404)

    def test_unresolved_explicit_person_blocks_auto_render_not_status_poll(self):
        preview = self.create_preview()
        ctx = self.complete(preview["preview_job_id"])
        uploaded = self.submit({k: preview[k] for k in ("preview_job_id", "source_sha256")}).get_json()
        photo = uploaded["photos"][0]
        photo["queued_render"] = {"base": "original"}
        photo["plan"] = {"user_prompt": "指定人物：微笑", "prompt_faces": [{
            "box": crops()[0]["box"], "ref": "指定人物", "where": "左側", "detection_index": 1,
            **{k: ctx["binding"][k] for k in ("preview_job_id", "source_sha256")}}]}
        missing = overall(("bystander", "bystander"))
        missing["_subject_preview_identity"] = {k: ctx["binding"][k] for k in ("preview_job_id", "source_sha256")}
        with patch.object(chat, "_job_status", return_value={"status": "success"}), \
             patch.object(chat.store, "claim", return_value=True), \
             patch.object(chat.store, "save_photo_state"), \
             patch.object(chat, "_follow_up_analysis", return_value=missing), \
             patch.object(chat, "_start_render", side_effect=AssertionError("must not render another face")):
            messages = chat._advance_analyzing(USER, "session", photo, {})
        self.assertEqual(photo["status"], "ready")
        self.assertEqual(photo["plan"]["user_prompt"], "指定人物：微笑")
        self.assertEqual(messages[-1]["card"]["reason"], "unresolved_preview_person")
        self.assertEqual(chat._prepare_render(photo)[1][0], "unresolved_preview_person")

    def test_worker_preview_artifact_not_full_fallback(self):
        queue = types.ModuleType("aiserver_utils.queue")
        queue.set_job_info = MagicMock()
        queue.get_job_info = MagicMock()
        with patch.dict(sys.modules, {"aiserver_utils.queue": queue}):
            import job_processor
        preview = self.create_preview()
        job = preview["preview_job_id"]
        ctx = self.complete(job)
        self.raw_jobs[job]["status"] = "processing"
        self.raw_jobs[job].pop("0_res0")
        self.artifacts.clear()
        root = Path(__file__).resolve().parents[1] / "tmp"
        with tempfile.TemporaryDirectory(prefix="preview-test-", dir=root) as tmp:
            image = Path(tmp) / "original.png"
            image.write_bytes(image_bytes())
            worker = job_processor.PhotoAgentJobProcessor(MagicMock(), MagicMock(), monitor_dst_dir=tmp)
            uploaded = {}
            def collect(paths_job, paths, keys):
                logs = [call.args[1] for call in worker.logger.info.call_args_list]
                running = next(i for i, line in enumerate(logs) if "Running analyze_preview_stage" in line)
                finished = next(i for i, line in enumerate(logs) if "Finished analyze_preview_stage" in line)
                reported = next(i for i, line in enumerate(logs) if "Preview snapshot report returned" in line)
                self.assertLess(running, finished)
                self.assertLess(finished, reported)
                self.assertTrue(all(call.args[0] == job for call in worker.logger.info.call_args_list))
                published = report.call_args.kwargs
                self.assertEqual(published["job_id"], job)
                self.assertEqual(set(published), {"job_id", "results"})
                snapshot = published["results"][PREVIEW_CONTEXT_FIELD]
                self.assertEqual(validate_preview_context(snapshot), validate_preview_context(ctx))
                # Simulate the real Redis update, then poll and submit while
                # upload_artifacts is still executing (no S3 result exists).
                self.raw_jobs[job].update(copy.deepcopy(published["results"]))
                with patch.object(chat, "download_bytes", side_effect=AssertionError("not uploaded yet")):
                    body = self.client.get(f"/api/photo-agent/chat/previews/{job}?user={USER}").get_json()
                    submitted = self.submit({k: preview[k] for k in ("preview_job_id", "source_sha256")})
                self.assertTrue(body["reusable"])
                self.assertEqual(body["status"], "processing")
                self.assertEqual(submitted.status_code, 202)
                self.assertEqual(self.enqueued[-1][1]["acts"][0]["params"]["precomputed_preview"],
                                 validate_preview_context(ctx))
                uploaded.update({key: json.loads(Path(path).read_text()) for path, key in zip(paths, keys)})
            with patch.object(worker, "download_sources", return_value=[str(image)]), \
                 patch.object(worker, "upload_artifacts", side_effect=collect) as upload, \
                 patch.object(job_processor, "analyze_preview_stage", return_value=ctx) as quick, \
                 patch.object(job_processor, "set_job_info") as report, \
                 patch.object(job_processor, "analyze_stage", side_effect=AssertionError("not full")), \
                 patch.object(job_processor, "finish_stage", side_effect=AssertionError("not finish")):
                result = worker.process_for_api(job, self.raw_jobs[job])
            self.assertTrue(result["0_res0"]["cusKeys"][0].endswith("0_res0_preview.json"))
            quick.assert_called_once()
            upload.assert_called_once()
            self.assertEqual(uploaded[result["0_res0"]["cusKeys"][0]], ctx)
            logs = [call.args[1] for call in worker.logger.info.call_args_list]
            self.assertTrue(any("Preview artifact upload finished" in line for line in logs))

    def test_worker_reporting_failure_still_uploads_preview_artifact(self):
        queue = types.ModuleType("aiserver_utils.queue")
        queue.set_job_info = MagicMock()
        queue.get_job_info = MagicMock()
        with patch.dict(sys.modules, {"aiserver_utils.queue": queue}):
            import job_processor
        preview = self.create_preview()
        job = preview["preview_job_id"]
        ctx = self.complete(job)
        root = Path(__file__).resolve().parents[1] / "tmp"
        with tempfile.TemporaryDirectory(prefix="preview-report-test-", dir=root) as tmp:
            image = Path(tmp) / "original.png"
            image.write_bytes(image_bytes())
            worker = job_processor.PhotoAgentJobProcessor(MagicMock(), MagicMock(), monitor_dst_dir=tmp)
            with patch.object(worker, "download_sources", return_value=[str(image)]), \
                 patch.object(worker, "upload_artifacts") as upload, \
                 patch.object(job_processor, "analyze_preview_stage", return_value=ctx), \
                 patch.object(job_processor, "set_job_info", side_effect=RuntimeError("Redis unavailable")):
                result = worker.process_for_api(job, self.raw_jobs[job])
            upload.assert_called_once()
            self.assertIn("0_res0", result)

    def test_formal_worker_passes_frozen_context_to_pipeline(self):
        queue = types.ModuleType("aiserver_utils.queue")
        queue.set_job_info = MagicMock()
        queue.get_job_info = MagicMock()
        with patch.dict(sys.modules, {"aiserver_utils.queue": queue}):
            import job_processor
        preview = self.create_preview()
        ctx = self.complete(preview["preview_job_id"])
        res = self.submit({k: preview[k] for k in ("preview_job_id", "source_sha256")})
        self.assertEqual(res.status_code, 202)
        job_id, engine = self.enqueued[-1]
        root = Path(__file__).resolve().parents[1] / "tmp"
        with tempfile.TemporaryDirectory(prefix="preview-formal-test-", dir=root) as tmp:
            image = Path(tmp) / "original.png"
            image.write_bytes(image_bytes())
            worker = job_processor.PhotoAgentJobProcessor(MagicMock(), MagicMock(), monitor_dst_dir=tmp)
            with patch.object(worker, "download_sources", return_value=[str(image)]), \
                 patch.object(worker, "upload_artifacts"), \
                 patch.object(job_processor, "analyze_stage", return_value=overall(reverse=True)) as analyze, \
                 patch.object(job_processor, "analyze_preview_stage", side_effect=AssertionError("not preview")), \
                 patch.object(job_processor, "finish_stage", side_effect=AssertionError("not finish")):
                worker.process_for_api(job_id, engine)
            self.assertEqual(analyze.call_args.kwargs["precomputed_preview"], validate_preview_context(ctx))


if __name__ == "__main__":
    unittest.main()
