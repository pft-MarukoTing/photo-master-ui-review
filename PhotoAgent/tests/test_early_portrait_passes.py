"""Portrait Call B/C started alongside Call A on Stage 2.0's fixed roster."""

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
    return {"subjects": [dict(p, **{k: {"from": "early", "who": p["preview_person_id"]} for k in ISSUE_KEYS})
                         for p in people]}


class AdoptEarlyTest(unittest.TestCase):
    def setUp(self):
        self.skill = PortraitAnalysisSkill()

    def test_copies_each_persons_fields_by_index(self):
        analysis = {"subjects": [_person("pp_a", 1, label="Call A label"), _person("pp_b", 2),
                                 {"label": "tree", "is_person": False, "preview_person_id": None,
                                  "local_detection_index": -1}]}
        early = (_early_roster(_person("pp_a", 1), _person("pp_b", 2)), ["usage_b", "usage_c"])
        with patch.object(config, "ENABLE_BODY_RESHAPE", False):
            adopted, usages = self.skill.adopt_early(analysis, early)
        self.assertEqual(usages, ["usage_b", "usage_c"])
        a, b, tree = adopted["subjects"]
        self.assertEqual(a["label"], "Call A label")  # Call A's own fields stay
        self.assertEqual(a["gaze_issue"], {"from": "early", "who": "pp_a"})
        self.assertEqual(b["lens_issue"], {"from": "early", "who": "pp_b"})
        self.assertIn("gaze_issue", tree)  # non-person gets the not-applicable shape
        self.assertNotEqual(tree["gaze_issue"].get("from"), "early")

    def test_person_call_a_dropped_from_people_keeps_defaults(self):
        analysis = {"subjects": [_person("pp_a", 1), _person("pp_b", 2, is_person=False)]}
        early = (_early_roster(_person("pp_a", 1), _person("pp_b", 2)), [])
        with patch.object(config, "ENABLE_BODY_RESHAPE", False):
            adopted, _ = self.skill.adopt_early(analysis, early)
        self.assertNotEqual(adopted["subjects"][1]["gaze_issue"].get("from"), "early")

    def test_moved_roster_is_not_adopted(self):
        analysis = {"subjects": [_person("pp_b", 2), _person("pp_a", 1)]}
        early = (_early_roster(_person("pp_a", 1), _person("pp_b", 2)), [])
        self.assertIsNone(self.skill.adopt_early(analysis, early))

    def test_run_early_waits_for_call_a_when_body_reshape_is_on(self):
        with patch.object(config, "ENABLE_BODY_RESHAPE", True):
            self.assertIsNone(self.skill.run_early({"subjects": [_person("pp_a", 1)]}, ctx=None))

    def test_run_early_with_no_readable_face_reviews_nobody(self):
        roster = {"subjects": [_person("pp_a", 1, is_person=False)]}
        with patch.object(config, "ENABLE_BODY_RESHAPE", False):
            self.assertEqual(self.skill.run_early(roster, ctx=None), (roster, []))

    def test_face_unreadable_in_stage_2_0_keeps_no_repair_defaults(self):
        # Call A calls pp_b a person but Stage 2.0 judged the face unreadable: no second review.
        analysis = {"subjects": [_person("pp_a", 1), _person("pp_b", 2)]}
        roster = _early_roster(_person("pp_a", 1), _person("pp_b", 2, is_person=False))
        with patch.object(config, "ENABLE_BODY_RESHAPE", False):
            adopted, _ = self.skill.adopt_early(analysis, (roster, []))
        self.assertEqual(adopted["subjects"][0]["gaze_issue"]["from"], "early")
        self.assertNotEqual(adopted["subjects"][1]["gaze_issue"].get("from"), "early")
        self.assertFalse(adopted["subjects"][1]["gaze_issue"].get("should_fix"))


class _FakeSkill:
    name = "fake"

    def __init__(self, early=None, raises=False):
        self.early, self.raises, self.seen = early, raises, {}

    def run_early(self, roster, ctx):
        self.seen["lang"] = i18n.current()
        if self.raises:
            raise RuntimeError("boom")
        return self.early

    def adopt_early(self, analysis, early):
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
            adopted = openai_client._adopt_early_skill_passes(skill, {"subjects": []}, futures["fake"])
        self.assertEqual(skill.seen["lang"], i18n.normalize("en"))
        self.assertEqual(adopted, ({"subjects": [], "adopted": "E"}, ["u"]))

    def test_failed_or_declined_early_pass_falls_back_to_run(self):
        for skill in (_FakeSkill(raises=True), _FakeSkill(early=None)):
            futures = self._start(skill)
            self.assertIsNone(openai_client._adopt_early_skill_passes(skill, {"subjects": []}, futures["fake"]))

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
        self.assertEqual(roster["_source_size"], [400, 300])
        self.assertEqual(len(roster["_local_face_boxes"]), 1)


if __name__ == "__main__":
    unittest.main()
