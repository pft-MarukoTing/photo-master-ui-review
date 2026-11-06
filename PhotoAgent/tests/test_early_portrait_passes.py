"""Portrait Call B/C started alongside Call A on Stage 2.0's fixed roster."""

import json
import unittest
from unittest.mock import patch

import config
import openai_client
from photo_agent import i18n
from photo_agent.analysis_skills import PortraitAnalysisSkill

ISSUE_KEYS = PortraitAnalysisSkill.PEOPLE_FIELDS


def _person(pid, detection, **extra):
    return {"label": pid, "box": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.4}, "is_person": True,
            "preview_person_id": pid, "local_detection_index": detection, **extra}


def _early_roster(*people):
    return {"_portrait_qc_ok": True,
            "subjects": [dict(p, **{k: {"from": "early", "who": p["preview_person_id"]} for k in ISSUE_KEYS})
                         for p in people]}


class _Ctx:
    """Records each instruction sent; answers with the queued responses in order."""

    def __init__(self, *responses):
        self.sent, self.responses = [], list(responses)

    def request(self, instruction, *_):
        self.sent.append(instruction)
        return {"usage": "usage_{}".format(len(self.sent))}

    def parse(self, data):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


# A Call B reading that passes the gaze gate (open eyes looking off-camera) and the lens gate.
_EYES_OFF_CAMERA = {
    "subject_index": 0, "local_detection_index": 1, "hair_issue": {}, "smile_issue": {},
    "blink_issue": {"blink_observability": "both_eyes", "eyelid_detail_quality": "readable",
                    "left_eye_state": "open", "right_eye_state": "open", "physical_eye_state": "open"},
    "gaze_issue": {"gaze_observability": "both_eyes", "eye_detail_quality": "readable",
                   "gaze_state": "off_camera", "head_pose": "frontal",
                   "left_eye_direction": "left_of_lens", "right_eye_direction": "left_of_lens",
                   "binocular_alignment": "convergent", "lens_contact_decision": "not_looking_at_lens",
                   "lens_contact_confidence": 0.05, "not_looking_at_lens_confidence": 0.95},
    "lens_issue": {"edge_stretch": "visible", "scene_projection": "straight"},
}
_FACE_AT_CORNER = {"_local_face_boxes": [{"face_box": {"x": 0.8, "y": 0.8, "w": 0.15, "h": 0.15}}],
                   "_source_size": [100, 100]}


class AdoptEarlyTest(unittest.TestCase):
    def setUp(self):
        self.skill = PortraitAnalysisSkill()

    def test_copies_each_persons_fields_by_index(self):
        analysis = {"subjects": [_person("pp_a", 1, label="Call A label"), _person("pp_b", 2),
                                 {"label": "tree", "is_person": False, "preview_person_id": None,
                                  "local_detection_index": -1}]}
        early = (_early_roster(_person("pp_a", 1), _person("pp_b", 2)), ["usage_b", "usage_c"])
        with patch.object(config, "ENABLE_BODY_RESHAPE", False):
            adopted, usages = self.skill.adopt_early(analysis, early, None)
        self.assertEqual(usages, ["usage_b", "usage_c"])
        a, b, tree = adopted["subjects"]
        self.assertEqual(a["label"], "Call A label")  # Call A's own fields stay
        self.assertEqual(a["gaze_issue"]["who"], "pp_a")
        self.assertEqual(b["lens_issue"]["who"], "pp_b")
        self.assertIn("gaze_issue", tree)  # non-person gets the not-applicable shape
        self.assertNotEqual(tree["gaze_issue"].get("from"), "early")

    def test_person_call_a_dropped_from_people_keeps_defaults(self):
        analysis = {"subjects": [_person("pp_a", 1), _person("pp_b", 2, is_person=False)]}
        early = (_early_roster(_person("pp_a", 1), _person("pp_b", 2)), [])
        with patch.object(config, "ENABLE_BODY_RESHAPE", False):
            adopted, _ = self.skill.adopt_early(analysis, early, None)
        self.assertNotEqual(adopted["subjects"][1]["gaze_issue"].get("from"), "early")

    def test_moved_roster_is_not_adopted(self):
        analysis = {"subjects": [_person("pp_b", 2), _person("pp_a", 1)]}
        early = (_early_roster(_person("pp_a", 1), _person("pp_b", 2)), [])
        self.assertIsNone(self.skill.adopt_early(analysis, early, None))

    def test_run_early_waits_for_call_a_when_body_reshape_is_on(self):
        with patch.object(config, "ENABLE_BODY_RESHAPE", True):
            self.assertIsNone(self.skill.run_early({"subjects": [_person("pp_a", 1)]}, ctx=None))

    def test_run_early_with_no_readable_face_reviews_nobody(self):
        roster = {"subjects": [_person("pp_a", 1, is_person=False)]}
        with patch.object(config, "ENABLE_BODY_RESHAPE", False):
            early, usages = self.skill.run_early(roster, ctx=None)
        self.assertEqual((early["subjects"], usages), (roster["subjects"], []))
        # Nothing was sent, so the debug record must not show a rebuilt Call B prompt.
        analysis = {"subjects": [_person("pp_a", 1)], **{k: v for k, v in early.items() if k != "subjects"}}
        with patch.object(config, "ENABLE_BODY_RESHAPE", False):
            records = self.skill.prompt_records(analysis)
        self.assertIsNone(records["photo_analysis_portrait_qc"])

    def test_prompt_records_keep_what_the_early_pass_sent(self):
        # Call A relabels pp_a, but the record shows the Stage 2.0 label and head location B was sent.
        roster = _early_roster(_person("pp_a", 1, label="2.0 label", _box_is_head=True))
        ctx = _Ctx(RuntimeError("no Call B reply"))
        with patch.object(config, "ENABLE_BODY_RESHAPE", False):
            early = self.skill.run_early(roster, ctx)
            adopted, _ = self.skill.adopt_early({"subjects": [_person("pp_a", 1, label="Call A label")]}, early, ctx)
            records = self.skill.prompt_records(adopted)
        self.assertEqual(records["photo_analysis_portrait_qc"], ctx.sent[0])
        self.assertIn("2.0 label -- head location x=", ctx.sent[0])
        self.assertNotIn("_portrait_prompts_sent", adopted)  # popped out of the stored analysis

    def test_face_unreadable_in_stage_2_0_keeps_no_repair_defaults(self):
        # Call A calls pp_b a person but Stage 2.0 judged the face unreadable: no second review.
        analysis = {"subjects": [_person("pp_a", 1), _person("pp_b", 2)]}
        roster = _early_roster(_person("pp_a", 1), _person("pp_b", 2, is_person=False))
        with patch.object(config, "ENABLE_BODY_RESHAPE", False):
            adopted, _ = self.skill.adopt_early(analysis, (roster, []), None)
        self.assertEqual(adopted["subjects"][0]["gaze_issue"].get("from"), "early")
        self.assertNotEqual(adopted["subjects"][1]["gaze_issue"].get("from"), "early")
        self.assertFalse(adopted["subjects"][1]["gaze_issue"].get("should_fix"))


    def test_call_c_and_every_label_written_after_call_b_are_call_as(self):
        # Call B runs on Stage 2.0's label and head location; Call A then relabels pp_a and
        # gives a body box. Call C, the fallback repair strategy and the lens correction item
        # must all name Call A's label, and Call C must see Call A's box.
        roster = {"subjects": [_person("pp_a", 1, label="2.0 label", _box_is_head=True)], **_FACE_AT_CORNER}
        body_box = {"x": 0.7, "y": 0.5, "w": 0.3, "h": 0.5}
        analysis = {"subjects": [_person("pp_a", 1, label="Call A label", box=body_box)], **_FACE_AT_CORNER}
        ctx = _Ctx({"person_issues": [_EYES_OFF_CAMERA]}, RuntimeError("no Call C reply"))
        with patch.object(config, "ENABLE_BODY_RESHAPE", False):
            early = self.skill.run_early(roster, ctx)
            self.assertEqual(len(ctx.sent), 1)  # only Call B runs before Call A
            adopted, usages = self.skill.adopt_early(analysis, early, ctx)
            records = self.skill.prompt_records(adopted)
        self.assertEqual(len(ctx.sent), 2)
        call_c = ctx.sent[1]
        self.assertIn("Call A label", call_c)
        self.assertNotIn("2.0 label", call_c)
        self.assertIn('"x": 0.7,', call_c)  # Call A's body box, not the head location
        self.assertEqual(records["photo_analysis_eye_repair_planning"], call_c)
        self.assertEqual(records["photo_analysis_portrait_qc"], ctx.sent[0])
        gaze = adopted["subjects"][0]["gaze_issue"]
        self.assertTrue(gaze["should_fix"])
        self.assertIn("Call A label", json.dumps(gaze["gaze_repair_strategy"], ensure_ascii=False))
        self.assertNotIn("2.0 label", json.dumps(gaze, ensure_ascii=False))
        self.assertTrue(adopted["subjects"][0]["lens_issue"]["should_fix"])
        self.assertIn("Call A label", " ".join(adopted["correction_plan"]))
        self.assertEqual(usages, ["usage_1"])  # Call C failed: no usage counted

    def test_failed_call_b_keeps_defaults_and_skips_call_c(self):
        roster = {"subjects": [_person("pp_a", 1)], **_FACE_AT_CORNER}
        ctx = _Ctx(RuntimeError("no Call B reply"))
        with patch.object(config, "ENABLE_BODY_RESHAPE", False):
            early = self.skill.run_early(roster, ctx)
            adopted, _ = self.skill.adopt_early({"subjects": [_person("pp_a", 1)], **_FACE_AT_CORNER}, early, ctx)
        self.assertEqual(len(ctx.sent), 1)
        self.assertFalse(adopted["subjects"][0]["gaze_issue"].get("should_fix"))
        self.assertNotIn("correction_plan", adopted)


class _FakeSkill:
    name = "fake"

    def __init__(self, early=None, raises=False):
        self.early, self.raises, self.seen = early, raises, {}

    def run_early(self, roster, ctx):
        self.seen["lang"] = i18n.current()
        if self.raises:
            raise RuntimeError("boom")
        return self.early

    def adopt_early(self, analysis, early, ctx):
        return ({**analysis, "adopted": early}, ["u"])


class EarlyHelpersTest(unittest.TestCase):
    def _start(self, skill):
        fixed = {"preview_people": [{"label": "a", "box": {"x": 0, "y": 0, "w": 1, "h": 1},
                                     "preview_person_id": "pp_a", "source": "full_image", "detection_index": None}]}
        with patch.object(openai_client, "ANALYSIS_SKILLS", (skill,)), \
                patch.object(openai_client, "get_image_dimensions", return_value=(100, 100)):
            return openai_client._start_early_skill_passes(b"img", fixed, [], ctx=None)

    def test_background_pass_sees_the_jobs_language_and_is_adopted(self):
        skill = _FakeSkill(early="E")
        with i18n.use("en"):
            futures = self._start(skill)
            adopted = openai_client._adopt_early_skill_passes(skill, {"subjects": []}, futures["fake"], None)
        self.assertEqual(skill.seen["lang"], i18n.normalize("en"))
        self.assertEqual(adopted, ({"subjects": [], "adopted": "E"}, ["u"]))

    def test_failed_or_declined_early_pass_falls_back_to_run(self):
        for skill in (_FakeSkill(raises=True), _FakeSkill(early=None)):
            futures = self._start(skill)
            self.assertIsNone(openai_client._adopt_early_skill_passes(skill, {"subjects": []}, futures["fake"], None))

    def test_no_people_starts_nothing(self):
        self.assertEqual(openai_client._start_early_skill_passes(b"img", {"preview_people": []}, [], None), {})

    def test_roster_matches_call_a_subject_shape(self):
        people = [{"label": "left", "box": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}, "preview_person_id": "pp_1",
                   "source": "detector", "detection_index": 1, "face_readable": True},
                  {"label": "back", "box": {"x": 0.6, "y": 0.1, "w": 0.2, "h": 0.2}, "preview_person_id": "pp_2",
                   "source": "full_image", "detection_index": None, "face_readable": False}]
        crops = [{"box": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2},
                  "face_box": {"x": 0.12, "y": 0.12, "w": 0.1, "h": 0.1}, "bytes": None}]
        with patch.object(openai_client, "get_image_dimensions", return_value=(400, 300)):
            roster = openai_client._early_skill_roster(b"img", {"preview_people": people}, crops)
        self.assertEqual([s["preview_person_id"] for s in roster["subjects"]], ["pp_1", "pp_2"])
        self.assertEqual([s["local_detection_index"] for s in roster["subjects"]], [1, -1])
        self.assertEqual([s["is_person"] for s in roster["subjects"]], [True, False])  # from face_readable
        self.assertTrue(all(s["_box_is_head"] for s in roster["subjects"]))  # B is told it is a head location
        self.assertEqual(roster["_source_size"], [400, 300])
        self.assertEqual(len(roster["_local_face_boxes"]), 1)


class SkillCropCaptionTest(unittest.TestCase):
    def test_skill_captions_name_the_person_without_call_a_instructions(self):
        context = {"low_confidence_candidates": [], "preview_people": [
            {"preview_person_id": "pp_1", "source": "detector", "detection_index": 1,
             "box": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}},
            {"preview_person_id": "pp_2", "source": "full_image", "detection_index": None,
             "thumbnail_candidate_id": None, "box": {"x": 0.6, "y": 0.1, "w": 0.2, "h": 0.2}}]}
        crops = [{"face_box": {"x": 0.12, "y": 0.12, "w": 0.1, "h": 0.1}, "bytes": b"jpg"}]
        with patch.object(openai_client, "image_to_data_url", return_value="data:x"):
            items = openai_client._fixed_person_crop_items(b"img", context, crops, for_skills=True)
        texts = [i["text"] for i in items if i["type"] == "input_text"]
        self.assertEqual([i["type"] for i in items], ["input_text", "input_image", "input_text"])
        self.assertTrue(texts[0].startswith("Face crop for subject_index=0; face location="))
        self.assertTrue(texts[1].startswith("No face crop for subject_index=1"))
        for text in texts:
            self.assertNotIn("body", text)
            self.assertNotIn("membership", text)


if __name__ == "__main__":
    unittest.main()
