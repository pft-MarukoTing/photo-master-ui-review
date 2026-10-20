import unittest

from photo_agent.chat_brief import build_brief, face_people, resolve_prompt_faces


BOX_LEFT = {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}
BOX_RIGHT = {"x": 0.6, "y": 0.1, "w": 0.2, "h": 0.2}


def analysis(**extra):
    return {
        "photo_summary": "兩人在海邊合照，整體偏暗。",
        "aesthetic_diagnosis": "主體曝光不足。",
        "detected_issues": ["臉部偏暗", " ", "背景雜亂"],
        # The panel's first face tab is subject 1, the second is subject 0.
        "_local_face_boxes": [{"box": BOX_RIGHT}, {"box": BOX_LEFT}],
        "subjects": [
            {"label": "左邊女性", "is_person": True, "box": BOX_LEFT,
             "blink_issue": {"blink_failure_confidence": 0.55, "should_fix": True},
             "gaze_issue": {"not_looking_at_lens_confidence": 0.9, "should_fix": True},
             "hair_issue": {"hair_mess_confidence": 0.45, "should_fix": False}},
            {"label": "", "is_person": True, "box": BOX_RIGHT,
             "gaze_issue": {"not_looking_at_lens_confidence": 0.6, "should_fix": False}},
            {"label": "蛋糕", "is_person": False,
             "blink_issue": {"blink_failure_confidence": 0.55, "should_fix": False}},
        ],
        **extra,
    }


class BuildBriefTest(unittest.TestCase):
    def test_summary_fields_are_passed_through(self):
        brief = build_brief(analysis())
        self.assertEqual(brief["summary"], "兩人在海邊合照，整體偏暗。")
        self.assertNotIn("diagnosis", brief)
        self.assertEqual(brief["issues"], ["臉部偏暗", "背景雜亂"])

    def test_people_are_numbered_like_the_panel(self):
        data = analysis(_local_face_boxes=[{"box": {"x": 0.4, "y": 0.8, "w": 0.1, "h": 0.1}},
                                           {"box": BOX_RIGHT}, {"box": BOX_LEFT, "sent_to_model": False}, {"box": None}])
        # An unmatched face the model saw still takes a tab; one it never saw does not.
        self.assertEqual([(p["number"], p["subject_index"], p["name"]) for p in face_people(data)],
                         [(1, -1, "人物 1"), (2, 1, "人物 2"), (3, 0, "左邊女性")])

    def test_nothing_to_say(self):
        self.assertIsNone(build_brief({"subjects": []}))


class ResolvePromptFacesTest(unittest.TestCase):
    # The upload page counts faces largest first, so BOX_LEFT was 人物 2 there;
    # the panel calls that same face 左邊女性.
    PICK = {"box": BOX_LEFT, "ref": "人物 2（照片中從左邊數來第 1 位）", "where": "照片中從左邊數來第 1 位"}

    def test_the_box_names_the_person_the_panel_shows(self):
        self.assertEqual(resolve_prompt_faces(analysis(), [self.PICK]),
                         {self.PICK["ref"]: "左邊女性（照片中從左邊數來第 1 位）"})

    def test_a_person_the_analyzer_did_not_label_keeps_a_number(self):
        pick = {"box": BOX_RIGHT, "ref": "人物 1（照片中從左邊數來第 2 位）", "where": "照片中從左邊數來第 2 位"}
        self.assertEqual(resolve_prompt_faces(analysis(), [pick])[pick["ref"]],
                         "人物 1（照片中從左邊數來第 2 位）")

    def test_a_face_the_panel_does_not_show_is_left_alone(self):
        pick = {**self.PICK, "box": {"x": 0.4, "y": 0.8, "w": 0.1, "h": 0.1}}
        self.assertEqual(resolve_prompt_faces(analysis(), [pick]), {})

    def test_junk_is_skipped(self):
        self.assertEqual(resolve_prompt_faces(analysis(), [{"box": None, "ref": "人物 1"}, "nope"]), {})


if __name__ == "__main__":
    unittest.main()
