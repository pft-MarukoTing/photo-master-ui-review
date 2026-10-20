import copy
import unittest

from photo_agent.selections import apply_selections, validate_selections


def base_analysis():
    return {
        "subject_category": "portrait",
        "correction_plan": ["brighten the subject", "make eye contact with the camera", "reframe to 4:5"],
        "retouch_recipe": ["提亮主體", "看向鏡頭", "裁成 4:5"],
        "correction_axes": ["brightness", "gaze", "crop"],
        "correction_inverse": ["keep subject exposure", "keep gaze", "keep original frame"],
        "subjects": [
            {"label": "left", "hair_issue": {"should_fix": False}, "smile_issue": {"adjust_safe": False},
             "blink_issue": {"should_fix": True}},
            {"label": "right", "body_reshape": {"should_edit": True, "edit_prompt": "slim waist"}},
        ],
        "style_options": {"pose": "stale"},
        "_advanced_options": {"gaze_contact_detection": True},
    }


class ApplySelectionsTest(unittest.TestCase):
    def test_input_not_mutated(self):
        analysis = base_analysis()
        before = copy.deepcopy(analysis)
        apply_selections(analysis, {
            "recipe_checked": [False, True, True],
            "face_issue_overrides": {"0:hair_issue": {"allowed": True}},
            "body_reshape_enabled": False,
        }, body_reshape_feature_enabled=True)
        self.assertEqual(analysis, before)

    def test_unchecked_recipe_item_becomes_rejected(self):
        out = apply_selections(base_analysis(), {"recipe_checked": [False, None]})
        self.assertEqual(out["correction_plan"], ["make eye contact with the camera", "reframe to 4:5"])
        self.assertEqual(out["retouch_recipe"], ["看向鏡頭", "裁成 4:5"])
        self.assertEqual(out["rejected_corrections"], ["brighten the subject"])
        self.assertEqual(out["rejected_retouch_recipe"], ["提亮主體"])
        self.assertEqual(out["rejected_axes"], ["brightness"])
        self.assertEqual(out["rejected_preserve"], ["keep subject exposure"])

    def test_recipe_indexes_follow_the_stripped_list(self):
        # Gaze off drops item 1, so index 1 now refers to the crop item.
        out = apply_selections(base_analysis(), {
            "advanced_options": {"gaze_contact_detection": False},
            "recipe_checked": [True, False],
        })
        self.assertEqual(out["correction_plan"], ["brighten the subject"])
        self.assertEqual(out["rejected_corrections"], ["reframe to 4:5"])
        self.assertEqual(out["_advanced_options"], {"gaze_contact_detection": False})

    def test_face_override_off_and_forced_on(self):
        out = apply_selections(base_analysis(), {"face_issue_overrides": {
            "0:blink_issue": False,
            "0:smile_issue": False,
            "0:hair_issue": {"allowed": True, "level": "tidy"},
        }})
        left = out["subjects"][0]
        self.assertIs(left["blink_issue"]["should_fix"], False)
        # Smile keeps the analyzer's own verdict; _disabled_face_issues is what excludes it.
        self.assertIs(left["smile_issue"]["adjust_safe"], False)
        self.assertEqual(left["hair_issue"]["_user_level"], "tidy")
        self.assertIs(left["hair_issue"]["_user_forced"], True)
        self.assertEqual(out["_disabled_face_issues"], [
            {"subject_index": 0, "category": "blink_issue"},
            {"subject_index": 0, "category": "smile_issue"},
        ])

    def test_user_disabled_stamped_only_over_a_real_verdict(self):
        out = apply_selections(base_analysis(), {"face_issue_overrides": {
            "0:blink_issue": False,   # analyzer flagged it
            "0:hair_issue": False,    # analyzer found nothing
        }})
        left = out["subjects"][0]
        self.assertIs(left["blink_issue"]["_user_disabled"], True)
        self.assertNotIn("_user_disabled", left["hair_issue"])

        # The next version starts from `out`: turning blink off again keeps
        # the stamp, turning it back on clears it.
        again = apply_selections(out, {"face_issue_overrides": {"0:blink_issue": False}})
        self.assertIs(again["subjects"][0]["blink_issue"]["_user_disabled"], True)
        back_on = apply_selections(out, {"face_issue_overrides": {"0:blink_issue": {"allowed": True}}})
        self.assertNotIn("_user_disabled", back_on["subjects"][0]["blink_issue"])

    def test_body_reshape_review_switch(self):
        out = apply_selections(base_analysis(), {"body_reshape_enabled": False}, body_reshape_feature_enabled=True)
        self.assertIs(out["subjects"][1]["body_reshape"]["should_edit"], False)
        out = apply_selections(base_analysis(), {}, body_reshape_feature_enabled=False)
        self.assertNotIn("body_reshape", out["subjects"][1])

    def test_style_options_always_replaced(self):
        out = apply_selections(base_analysis(), {"options": {"pose": "default", "lighting": "soft", "size": ""}})
        self.assertEqual(out["style_options"], {"lighting": "soft"})
        self.assertEqual(apply_selections(base_analysis(), None)["style_options"], {})

    def test_reapproved_crop_restores_saved_proposal(self):
        analysis = base_analysis()
        analysis["_rejected_reframing_source"] = {"crop_box": {"x": 0.1, "y": 0, "w": 0.8, "h": 1}}
        analysis["_preserve_original_composition"] = True
        out = apply_selections(analysis, {"recipe_checked": [True, True, True]})
        self.assertEqual(out["crop_box"], {"x": 0.1, "y": 0, "w": 0.8, "h": 1})
        self.assertNotIn("_rejected_reframing_source", out)
        self.assertNotIn("_preserve_original_composition", out)

        still_rejected = apply_selections(analysis, {"recipe_checked": [True, True, False]})
        self.assertIn("_rejected_reframing_source", still_rejected)

    def test_validate_selections(self):
        self.assertIsNone(validate_selections({}))
        self.assertIsNone(validate_selections({"recipe_checked": [True, None], "body_reshape_enabled": None}))
        self.assertIsNotNone(validate_selections([]))
        self.assertIsNotNone(validate_selections({"recipe_checked": "1,2"}))
        self.assertIsNotNone(validate_selections({"body_reshape_enabled": "true"}))


if __name__ == "__main__":
    unittest.main()
