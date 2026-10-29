import unittest
from unittest.mock import patch

from config import STYLE_OPTION_INSTRUCTIONS
from photo_agent.analysis_skills.portrait.decisions import (
    _fallback_blink_repair_strategy,
    derive_blink_decisions,
)
from photo_agent.analysis_skills.portrait.instructions import (
    _BLINK_DETECTION_SECTION,
    build_eye_repair_planning_instruction,
)
from photo_agent.feature_resolver import (
    _compact_crop_goal_text,
    _compact_raw_crop_instruction,
    _compact_raw_style_instruction,
    build_compact_editor_prompt,
    build_feature_contracts,
    resolve_feature_contracts,
)
from photo_agent.quality_check_prompt import build_quality_check_prompt
from prompts import USER_SCENE_CONSISTENCY_RULE, build_body_reshape_edit_block


def interaction_analysis():
    box = {"x": .25, "y": .15, "w": .5, "h": .5}
    return {
        "_advanced_options": {
            "smile_adjustment": True,
            "smile_level": "laughing",
            "gaze_contact_detection": True,
            "eye_closed_detection": True,
        },
        "subjects": [{
            "label": "central woman", "box": box, "is_person": True,
            "smile_issue": {"smile_intensity": "neutral", "adjust_safe": True},
            "gaze_issue": {
                "should_fix": True,
                "gaze_repair_strategy": {"strategy_type": "eyes_only", "repair_strength": "subtle"},
            },
        }],
        "correction_plan": [
            "Redirect the central woman's gaze to the camera.",
            "Reduce the bright background distraction.",
        ],
        "recommended_aspect_ratio": "1:1",
    }


def blink_analysis():
    return {
        "_advanced_options": {"eye_closed_detection": True, "smile_adjustment": False},
        "subjects": [{
            "label": "left man", "box": {"x": .1, "y": .1, "w": .3, "h": .4},
            "is_person": True,
            "blink_issue": {"should_fix": True, "reason": "eyes half closed"},
            "gaze_issue": {"head_pose": "frontal"},
        }],
        "correction_plan": ["Reopen the left man's eyes."],
    }


def blink_detection_analysis(*, observability="both_eyes", right_state="narrowed_open"):
    return {
        "subjects": [{
            "label": "central woman",
            "box": {"x": .1, "y": .1, "w": .8, "h": .8},
            "is_person": True,
            "blink_issue": {
                "blink_observability": observability,
                "eyelid_detail_quality": "readable",
                "left_eye_state": "fully_closed",
                "right_eye_state": right_state,
                "physical_eye_state": "one_eye_closed",
                "eye_closure_confidence": .99,
                "mouth_laughter_state": "not_laughing",
                "periocular_contraction": "none_or_mild",
                "closure_expression_dependence": "independent_or_transitional",
                "explicit_closure_context": "none",
                "intent_evidence_confidence": .05,
                "overall_demeanor": "alert_or_camera_ready",
                "reason": "照片左眼完全閉合，右眼仍可見開口。",
            },
            "gaze_issue": {"gaze_target_in_frame": {"present": False}},
        }],
    }


class BlinkDecisionGate(unittest.TestCase):
    def test_one_eye_summary_is_reconciled_when_both_eyelids_have_strong_concrete_evidence(self):
        result = derive_blink_decisions(blink_detection_analysis(observability="one_eye"))
        issue = result["subjects"][0]["blink_issue"]

        self.assertEqual(issue["blink_observability"], "both_eyes")
        self.assertEqual(issue["physical_eye_state"], "one_eye_closed")
        self.assertTrue(issue["blink_candidate"])
        self.assertTrue(issue["should_fix"])
        self.assertEqual(issue["closure_intent"], "accidental_capture")

        rerun = derive_blink_decisions(result)["subjects"][0]["blink_issue"]
        self.assertEqual(rerun["reason"].count("Python gate："), 1)
        self.assertNotIn("只有單眼幾何可讀", rerun["reason"])

    def test_actual_single_eye_evidence_stays_non_actionable(self):
        analysis = blink_detection_analysis(observability="one_eye", right_state="not_visible")
        result = derive_blink_decisions(analysis)
        issue = result["subjects"][0]["blink_issue"]

        self.assertEqual(issue["blink_observability"], "one_eye")
        self.assertTrue(issue["blink_candidate"])
        self.assertFalse(issue["should_fix"])


def body_reshape_analysis():
    return {
        "_advanced_options": {},
        "subjects": [{
            "label": "central woman",
            "box": {"x": .2, "y": .1, "w": .5, "h": .8},
            "is_person": True,
            "body_reshape": {
                "should_edit": True,
                "edit_prompt": "自然修飾人物可見腰腹與身形輪廓，保持衣物邊界及背景透視連續",
            },
        }],
        "correction_plan": [],
        "correction_axes": [],
    }


class NestedIssueReading(unittest.TestCase):
    def test_localized_gentle_smile_still_reaches_explicit_laughing_endpoint(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["smile_issue"]["smile_intensity"] = "溫和微笑"
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        self.assertEqual(
            set(audit["resolved_features"][0]["features"]), {"smile", "gaze"}
        )
        self.assertEqual(audit["resolved_features"][0]["source_state"]["smile_level"], "gentle")
        self.assertEqual(audit["resolved_features"][0]["final_state"]["smile_level"], "laughing")
        self.assertIn("expression of genuine laughter", prompt)
        self.assertNotIn("facial expression,", prompt)
        self.assertNotIn("mouth expression,", prompt)

    def test_explicit_smile_level_survives_a_missing_analysis_reading(self):
        analysis = interaction_analysis()
        analysis["subjects"][0].pop("smile_issue")
        contracts = build_feature_contracts(analysis)
        smile = next(item for item in contracts if item["feature"] == "smile")
        self.assertEqual(smile["source_state"], {})
        self.assertEqual(smile["endpoint"]["smile_level"], "laughing")

    def test_explicit_smile_level_reaches_an_unreadable_face(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["smile_issue"].update({"smile_intensity": "unclear", "adjust_safe": False})
        smile = next(item for item in build_feature_contracts(analysis) if item["feature"] == "smile")
        self.assertEqual(smile["source_state"], {})
        self.assertEqual(smile["endpoint"]["smile_level"], "laughing")

        analysis["_advanced_options"]["smile_level"] = "auto"
        self.assertNotIn("smile", [item["feature"] for item in build_feature_contracts(analysis)])

    def test_per_person_smile_level_overrides_global_default(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["smile_issue"]["_user_level"] = "gentle"
        smile = next(item for item in build_feature_contracts(analysis) if item["feature"] == "smile")
        self.assertEqual(smile["endpoint"]["smile_level"], "gentle")

    def test_user_can_force_auto_smile_on_an_unsafe_face(self):
        analysis = interaction_analysis()
        issue = analysis["subjects"][0]["smile_issue"]
        issue.update({"adjust_safe": False, "_user_level": "auto", "_user_forced": True})
        smile = next(item for item in build_feature_contracts(analysis) if item["feature"] == "smile")
        self.assertEqual(smile["endpoint"]["smile_level"], "gentle")

    def test_nested_blink_issue_becomes_a_contract(self):
        contracts = build_feature_contracts(blink_analysis())
        self.assertEqual([c["feature"] for c in contracts], ["blink"])
        self.assertEqual(contracts[0]["subject"], "left man")

    def test_per_person_opt_out_removes_that_feature(self):
        analysis = interaction_analysis()
        analysis["_disabled_face_issues"] = [{"subject_index": 0, "category": "gaze_issue"}]
        contracts = build_feature_contracts(analysis)
        self.assertEqual([c["feature"] for c in contracts], ["smile"])

    def test_opting_out_of_blink_leaves_no_contract(self):
        analysis = blink_analysis()
        analysis["_disabled_face_issues"] = [{"subject_index": 0, "category": "blink_issue"}]
        self.assertEqual(build_feature_contracts(analysis), [])

    def test_overlapping_faces_keep_their_own_final_states(self):
        """Two people whose boxes nearly share a centre must not be merged."""
        analysis = {
            "_advanced_options": {
                "eye_closed_detection": True,
                "gaze_contact_detection": True,
                "smile_adjustment": False,
            },
            "subjects": [
                {
                    "label": "front woman", "is_person": True,
                    "box": {"x": .47, "y": .12, "w": .49, "h": .88},
                    "blink_issue": {"should_fix": True, "reason": "both eyes closed"},
                },
                {
                    "label": "back woman", "is_person": True,
                    "box": {"x": .49, "y": .08, "w": .4, "h": .61},
                    "gaze_issue": {"should_fix": True, "head_pose": "frontal"},
                },
            ],
        }
        resolved = resolve_feature_contracts(
            build_feature_contracts(analysis), subject_count=2
        )
        self.assertEqual(
            sorted((item["subject"], tuple(item["features"])) for item in resolved),
            [("back woman", ("gaze",)), ("front woman", ("blink",))],
        )


class BodyReshapePromptRouting(unittest.TestCase):
    @patch("config.ENABLE_BODY_RESHAPE", True)
    def test_compact_editor_prompt_routes_one_holistic_prompt_verbatim(self):
        analysis = body_reshape_analysis()
        body_prompt = analysis["subjects"][0]["body_reshape"]["edit_prompt"]
        shared_block = build_body_reshape_edit_block(analysis)
        prompt, metadata = build_compact_editor_prompt(analysis, "", "", {})

        self.assertTrue(shared_block)
        self.assertIn(shared_block, prompt)
        self.assertEqual(
            prompt.count(body_prompt),
            1,
        )
        self.assertNotIn("key_points", prompt)
        self.assertNotIn("target_pct", prompt)
        self.assertEqual(metadata["body_reshape_edit_prompt"], body_prompt)


def blink_strategy():
    return {
        "target_eye_state": "open, matching the left man's existing expression",
        "eyelid_adjustment": "Lift the upper lids to the captured lash line and keep the lower lids where they are.",
        "repair_strength": "subtle",
        "edit_instruction": "Reopen the left man's half-closed eyes.",
        "success_condition": "eyes read naturally open at viewing size",
        "preserve_during_repair": ["identity"],
        "failure_to_avoid": ["doll-like eyes"],
    }


class BlinkRepairStrategy(unittest.TestCase):
    def test_planned_strategy_reaches_the_compact_prompt(self):
        analysis = blink_analysis()
        analysis["subjects"][0]["blink_issue"]["blink_repair_strategy"] = blink_strategy()
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn("Apply a subtle eyelid correction", prompt)
        self.assertIn("Target eyelid state: open, matching the left man's existing expression", prompt)
        self.assertIn("Eyelid method: Lift the upper lids to the captured lash line", prompt)
        self.assertIn("to the analysis-defined final eyelid state", prompt)
        self.assertIn("Shared eye-pair target for every named blink repair", prompt)
        self.assertIn("adjust only the eyelid margins, crease", prompt)

    def test_full_closed_and_half_closed_pair_uses_strong_repair(self):
        analysis = blink_analysis()
        analysis["subjects"][0]["blink_issue"].update({
            "left_eye_state": "fully_closed",
            "right_eye_state": "half_closed",
            "blink_repair_strategy": blink_strategy(),
        })
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn("Eye-pair repair (strong bilateral correction)", prompt)
        self.assertIn("open both eyes together to the same natural, comfortably open final aperture", prompt)
        self.assertIn("Lift the photograph-left upper lid farther than the photograph-right upper lid", prompt)
        self.assertNotIn("fold family", prompt)
        self.assertNotIn("Target eyelid state: open, matching the left man's existing expression", prompt)
        self.assertNotIn("Eyelid method: Lift the upper lids", prompt)
        self.assertIn("match the final upper- and lower-lid contours across the facial midline", prompt)
        self.assertIn("opening size and shape", prompt)
        self.assertIn("visible iris width", prompt)
        self.assertIn("double-eyelid fold shape and visible width", prompt)
        self.assertEqual(prompt.count("Shared eye-pair target for every named blink repair"), 1)
        self.assertNotIn("PRIMARY EYE-PAIR REPAIR", prompt)
        self.assertNotIn("FINAL VISUAL GATE", prompt)
        self.assertNotIn("VISIBLE FINAL STATE", prompt)
        self.assertNotIn("Mandatory final blink-repair gate", prompt)
        self.assertNotIn("Photograph-side source states", prompt)
        self.assertNotIn("【", prompt)
        self.assertLess(
            prompt.index("Other approved corrections:"),
            prompt.index("Resolved subject final states:"),
        )
        self.assertTrue(audit["resolved_features"][0]["final_state"]["paired_blink_repair"])
        self.assertTrue(audit["resolved_features"][0]["final_state"]["asymmetric_blink_pair"])
        self.assertNotIn("reference_photograph_side", audit["resolved_features"][0]["final_state"])

    def test_mixed_pair_lifts_the_more_closed_side_farther(self):
        for left_state, right_state, more_closed, less_closed in (
            ("fully_closed", "half_closed", "photograph-left", "photograph-right"),
            ("half_closed", "fully_closed", "photograph-right", "photograph-left"),
        ):
            with self.subTest(left_state=left_state, right_state=right_state):
                analysis = blink_analysis()
                analysis["subjects"][0]["blink_issue"].update({
                    "left_eye_state": left_state,
                    "right_eye_state": right_state,
                    "blink_repair_strategy": blink_strategy(),
                })
                prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
                eye_line = next(line for line in prompt.splitlines() if "Eye-pair repair" in line)
                self.assertIn("open both eyes together to the same natural, comfortably open final aperture", eye_line)
                self.assertIn(
                    f"Lift the {more_closed} upper lid farther than the {less_closed} upper lid",
                    eye_line,
                )
                self.assertIn("matching opening height and iris exposure", eye_line)

    def test_equal_source_blink_states_receive_shared_pair_contract(self):
        for eye_state in ("fully_closed", "half_closed"):
            with self.subTest(eye_state=eye_state):
                analysis = blink_analysis()
                analysis["subjects"][0]["blink_issue"].update({
                    "left_eye_state": eye_state,
                    "right_eye_state": eye_state,
                    "blink_repair_strategy": blink_strategy(),
                })

                prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
                final_state = audit["resolved_features"][0]["final_state"]
                self.assertTrue(final_state["paired_blink_repair"])
                self.assertNotIn("asymmetric_blink_pair", final_state)
                self.assertIn("open both blink-affected eyes to the same natural, comfortably open final aperture", prompt)
                self.assertIn("Retract each upper-lid margin by the amount it needs", prompt)
                self.assertIn("visible iris width", prompt)
                self.assertEqual(prompt.count("Shared eye-pair target for every named blink repair"), 1)

    def test_turned_face_uses_local_eye_axes_instead_of_frontal_mirroring(self):
        analysis = blink_analysis()
        analysis["subjects"][0]["gaze_issue"]["head_pose"] = "strong_yaw"
        analysis["subjects"][0]["blink_issue"].update({
            "left_eye_state": "fully_closed",
            "right_eye_state": "half_closed",
        })

        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})

        self.assertIn("match the final lid contours in each eye's local axis", prompt)
        self.assertIn("natural perspective projection", prompt)
        self.assertNotIn("For this frontal face", prompt)

    def test_closed_and_narrowed_open_pair_gets_two_part_eye_target(self):
        analysis = blink_analysis()
        analysis["subjects"][0]["blink_issue"].update({
            "left_eye_state": "fully_closed",
            "right_eye_state": "narrowed_open",
            "blink_repair_strategy": blink_strategy(),
        })
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        qa_prompt = build_quality_check_prompt(analysis)

        self.assertTrue(audit["resolved_features"][0]["final_state"]["paired_blink_repair"])
        self.assertIn("Eye-pair repair (strong bilateral correction)", prompt)
        self.assertIn("open both eyes together to the same natural, comfortably open final aperture", prompt)
        self.assertIn("Lift the photograph-left upper lid farther than the photograph-right upper lid", prompt)
        self.assertIn("visible iris width", prompt)
        self.assertNotIn("Eyelid method: Lift the upper lids", prompt)
        self.assertNotIn("Keep the captured iris/pupil direction, sclera balance, and catchlights", prompt)
        self.assertIn("Set both irises, pupils, sclera balances, and catchlights coherently as one binocular pair", prompt)
        self.assertIn("two independent mandatory outcomes", qa_prompt)

    def test_qa_rejects_blink_induced_crease_width_mismatch(self):
        analysis = blink_analysis()
        analysis["subjects"][0]["blink_issue"].update({
            "left_eye_state": "fully_closed",
            "right_eye_state": "half_closed",
            "blink_repair_strategy": blink_strategy(),
        })
        qa_prompt = build_quality_check_prompt(analysis)

        self.assertIn("same double-eyelid fold shape as that reference eye", qa_prompt)
        self.assertIn("same visible crease-to-lash band width", qa_prompt)
        self.assertIn("score must be 4 or lower and pass must be false", qa_prompt)

    def test_planner_sentence_endings_do_not_create_double_periods(self):
        analysis = blink_analysis()
        analysis["subjects"][0]["blink_issue"]["blink_repair_strategy"] = blink_strategy()
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertNotIn("where they are.. Final eyelid target", prompt)

    def test_multiple_blink_subjects_share_the_full_final_target_once(self):
        analysis = blink_analysis()
        analysis["subjects"].append({
            "label": "right woman",
            "box": {"x": .6, "y": .1, "w": .3, "h": .4},
            "is_person": True,
            "blink_issue": {"should_fix": True, "reason": "eyes half closed"},
        })
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})

        self.assertIn("Subject: left man", prompt)
        self.assertIn("Subject: right woman", prompt)
        self.assertEqual(prompt.count("Shared eye-pair target for every named blink repair"), 1)
        self.assertEqual(prompt.count("visible iris width"), 1)

    def test_no_strategy_keeps_the_generic_sentence(self):
        prompt, _ = build_compact_editor_prompt(blink_analysis(), "", "", {})
        self.assertIn(
            "- Open both eyes clearly and comfortably from the captured blink into a natural open resting state.",
            prompt,
        )
        self.assertIn("Shared eye-pair target for every named blink repair", prompt)
        self.assertNotIn("Apply a", prompt)
        self.assertNotIn("Eyelid method:", prompt)

    def test_smile_owns_the_expression_but_the_eyelid_method_survives(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["blink_issue"] = {
            "should_fix": True,
            "reason": "accidental half blink",
            "blink_repair_strategy": blink_strategy(),
        }
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn("the same smile- or laughter-shaped final aperture", prompt)
        self.assertIn("visible iris width", prompt)
        self.assertIn("double-eyelid fold shape and visible width", prompt)
        self.assertIn("Eyelid method: Lift the upper lids to the captured lash line", prompt)
        self.assertNotIn("Analysis final eyelid state:", prompt)
        self.assertNotIn("blink endpoint, not independent smile-induced squint", prompt)

    def test_laughing_asymmetric_pair_uses_concise_shared_eye_contract(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["blink_issue"] = {
            "should_fix": True,
            "reason": "mismatched transitional eyelid states",
            "left_eye_state": "fully_closed",
            "right_eye_state": "half_closed",
            "blink_repair_strategy": blink_strategy(),
        }

        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})

        self.assertIn("the same smile- or laughter-shaped final aperture", prompt)
        self.assertIn("matched natural periocular narrowing", prompt)
        self.assertIn("Lift the photograph-left upper lid farther than the photograph-right upper lid", prompt)
        self.assertIn("opening size and shape", prompt)
        self.assertIn("visible iris width", prompt)
        self.assertIn("double-eyelid fold shape and visible width", prompt)
        self.assertEqual(prompt.count("Shared eye-pair target for every named blink repair"), 1)
        self.assertNotIn("five bilateral matches", prompt)
        self.assertNotIn("FINAL VISUAL GATE", prompt)
        self.assertNotIn("may not reduce either final aperture", prompt)
        self.assertNotIn("may not independently narrow either eyelid", prompt)
        self.assertTrue(audit["resolved_features"][0]["final_state"]["asymmetric_blink_pair"])

    def test_laughing_single_closed_eye_uses_expression_shaped_reference(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["blink_issue"] = {
            "should_fix": True,
            "reason": "one captured eye is closed",
            "left_eye_state": "fully_closed",
            "right_eye_state": "open",
            "blink_repair_strategy": blink_strategy(),
        }

        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})

        self.assertIn(
            "photograph-right eye's finished expression-shaped eyelid geometry as the common design",
            prompt,
        )
        self.assertNotIn("photograph-right eye's finished open eyelid geometry", prompt)
        self.assertIn("visible iris width", prompt)

    def test_analyzer_treats_closed_half_closed_difference_as_capture_state(self):
        self.assertIn("mismatched transitional eyelid states", _BLINK_DETECTION_SECTION)
        self.assertIn("not to this captured difference in eyelid openness", _BLINK_DETECTION_SECTION)
        self.assertIn("A fully_closed eye is still observable as an eyelid", _BLINK_DETECTION_SECTION)
        self.assertIn("blink_observability must be both_eyes", _BLINK_DETECTION_SECTION)

    def test_blink_planner_targets_balanced_openness_without_geometric_cloning(self):
        instruction = build_eye_repair_planning_instruction([{
            "subject_index": 0,
            "label": "central woman",
            "needs_gaze_repair": False,
            "needs_blink_repair": True,
            "blink_issue": {
                "left_eye_state": "fully_closed",
                "right_eye_state": "half_closed",
            },
        }])
        self.assertIn("diagnostic planner inputs only", instruction)
        self.assertIn("Do not repeat these source-state labels", instruction)
        self.assertIn("Treat both eyes as one coupled bilateral structure", instruction)
        self.assertIn("Only an eye detected as open may supply final geometry", instruction)
        self.assertIn("neither captured aperture is a reference", instruction)
        self.assertIn("one naturally-open target", instruction)
        self.assertIn("When the two diagnostic source states differ", instruction)
        self.assertIn("two independent final matches", instruction)
        self.assertIn("Do not compensate for a mismatch in one by changing the other", instruction)
        self.assertIn("do not preserve the blink-induced left/right difference as natural asymmetry", instruction)
        self.assertIn("This stricter rule is local to that mixed-state blink pair", instruction)
        self.assertIn("double-eyelid crease type, path, peak, visible length", instruction)
        self.assertIn("facial midline as the bilateral comparison axis", instruction)
        self.assertIn("Keep each model-facing field concise", instruction)
        self.assertIn("Use strong repair strength when either source eye is fully closed", instruction)
        self.assertIn('"photograph_left_eye_state": "fully_closed"', instruction)
        self.assertIn('"photograph_right_eye_state": "half_closed"', instruction)
        self.assertNotIn('"left_eye_state"', instruction)
        self.assertNotIn('"right_eye_state"', instruction)

    def test_single_closed_eye_matches_the_open_eye_on_either_photograph_side(self):
        for closed_side in ("photograph-left", "photograph-right"):
            with self.subTest(closed_side=closed_side):
                analysis = blink_analysis()
                strategy = blink_strategy()
                strategy["target_eye_state"] = "planner-authored final eyelid state"
                strategy["eyelid_adjustment"] = "planner-authored final eyelid method"
                states = {
                    "left_eye_state": "fully_closed" if closed_side == "photograph-left" else "open",
                    "right_eye_state": "fully_closed" if closed_side == "photograph-right" else "open",
                }
                analysis["subjects"][0]["blink_issue"].update(states)
                analysis["subjects"][0]["blink_issue"]["blink_repair_strategy"] = strategy

                prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
                reference_side = "photograph-right" if closed_side == "photograph-left" else "photograph-left"
                self.assertNotIn("planner-authored final eyelid state", prompt)
                self.assertNotIn("planner-authored final eyelid method", prompt)
                self.assertIn(
                    f"use the {reference_side} eye's finished open eyelid geometry as the common design",
                    prompt,
                )
                self.assertIn(
                    f"for the repaired {closed_side} eye",
                    prompt,
                )
                self.assertIn("double-eyelid fold shape and visible width", prompt)
                self.assertNotIn("preserve any genuine stable left/right crease-type difference", prompt.lower())
                final_state = audit["resolved_features"][0]["final_state"]
                self.assertEqual(final_state["closed_photograph_side"], closed_side)
                self.assertEqual(final_state["reference_photograph_side"], reference_side)
                self.assertEqual(final_state["reference_eye_state"], "open")
                self.assertNotIn("asymmetric_blink_pair", final_state)
                self.assertNotIn("For this asymmetric blink-pair repair", prompt)

    def test_closed_eye_beside_a_narrowed_eye_opens_both_instead_of_copying_it(self):
        for opposite_state in ("half_closed", "narrowed_open"):
            with self.subTest(opposite_state=opposite_state):
                analysis = blink_analysis()
                analysis["subjects"][0]["blink_issue"].update({
                    "left_eye_state": "fully_closed",
                    "right_eye_state": opposite_state,
                    "blink_repair_strategy": blink_strategy(),
                })

                prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
                final_state = audit["resolved_features"][0]["final_state"]
                self.assertTrue(final_state["paired_blink_repair"])
                self.assertTrue(final_state["asymmetric_blink_pair"])
                self.assertNotIn("reference_photograph_side", final_state)
                self.assertIn("open both eyes together to the same natural, comfortably open final aperture", prompt)
                self.assertIn("Lift the photograph-left upper lid farther than the photograph-right upper lid", prompt)
                self.assertNotIn("as the common design for the repaired", prompt)
                self.assertNotIn(opposite_state, prompt)

    def test_fallback_strategy_names_photograph_side_and_matches_reference_final_state(self):
        strategy = _fallback_blink_repair_strategy({
            "left_eye_state": "open",
            "right_eye_state": "fully_closed",
        }, "central woman")

        self.assertNotIn("fully_closed", strategy["target_eye_state"])
        self.assertIn("photograph-left eye's finished open geometry", strategy["eyelid_adjustment"])
        self.assertIn("template for the repaired photograph-right eye", strategy["edit_instruction"])
        self.assertIn("read as the same size", strategy["success_condition"])
        self.assertIn("same lid-contour and double-eyelid fold design", strategy["success_condition"])
        self.assertIn("Treat both eyes as one coupled bilateral structure", strategy["eyelid_adjustment"])

    def test_fallback_strategy_opens_a_narrowed_opposite_eye_instead_of_matching_it(self):
        strategy = _fallback_blink_repair_strategy({
            "left_eye_state": "narrowed_open",
            "right_eye_state": "fully_closed",
        }, "central woman")

        self.assertIn("Both of central woman's eyes are naturally open", strategy["target_eye_state"])
        self.assertNotIn("source-supported reference", strategy["eyelid_adjustment"])
        self.assertIn("Raise both upper-lid margins", strategy["eyelid_adjustment"])
        self.assertIn("one coupled bilateral structure", strategy["edit_instruction"])
        self.assertIn("read as the same size", strategy["success_condition"])
        self.assertEqual(strategy["repair_strength"], "strong")

    def test_blink_and_gaze_render_one_coordinated_eye_edit(self):
        analysis = blink_analysis()
        analysis["_advanced_options"]["gaze_contact_detection"] = True
        analysis["subjects"][0]["gaze_issue"] = {
            "should_fix": True,
            "gaze_repair_strategy": {"strategy_type": "eyes_only", "repair_strength": "moderate"},
        }
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn("as one coordinated local eye edit", prompt)
        self.assertNotIn("Keep the captured iris/pupil direction", prompt)
        self.assertEqual(audit["unresolved_conflicts"], [])

    def test_blink_and_gaze_strengths_do_not_overwrite_each_other(self):
        analysis = blink_analysis()
        analysis["_advanced_options"]["gaze_contact_detection"] = True
        analysis["subjects"][0]["blink_issue"]["blink_repair_strategy"] = blink_strategy()
        analysis["subjects"][0]["gaze_issue"] = {
            "should_fix": True,
            "gaze_repair_strategy": {"strategy_type": "eyes_only", "repair_strength": "moderate"},
        }
        _, audit = build_compact_editor_prompt(analysis, "", "", {})
        final_state = audit["resolved_features"][0]["final_state"]
        self.assertEqual(final_state["eyelid_strength"], "subtle")
        self.assertEqual(final_state["strength"], "moderate")
        self.assertEqual(audit["unresolved_conflicts"], [])


class FeatureResolverTest(unittest.TestCase):
    def test_laugh_and_gaze_form_one_joint_final_state(self):
        resolved = resolve_feature_contracts(build_feature_contracts(interaction_analysis()))
        self.assertEqual(len(resolved), 1)
        subject = resolved[0]
        self.assertEqual(set(subject["features"]), {"smile", "gaze"})
        self.assertEqual(subject["final_state"]["smile_level"], "laughing")
        self.assertEqual(subject["source_state"]["smile_level"], "neutral")
        self.assertEqual(subject["final_state"]["gaze"], "direct_camera")
        self.assertEqual(subject["final_state"]["method"], "eyes_only")
        self.assertEqual(subject["final_state"]["strength"], "subtle")
        self.assertIn("eyelid_aperture", subject["change_footprint"])
        self.assertNotIn("eyelid_aperture", subject["preserve"])
        self.assertNotIn("mouth_expression", subject["preserve"])
        self.assertNotIn("gaze_direction", subject["preserve"])
        self.assertIn("head_pose", subject["preserve"])
        self.assertIn("identity", subject["preserve"])

    def test_eyes_plus_head_gaze_owns_only_bounded_head_orientation(self):
        analysis = interaction_analysis()
        strategy = analysis["subjects"][0]["gaze_issue"]["gaze_repair_strategy"]
        strategy.update({
            "strategy_type": "eyes_plus_head_face_alignment",
            "head_face_adjustment": "slight yaw toward camera",
        })
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        subject = audit["resolved_features"][0]
        self.assertIn("head_pose", subject["change_footprint"])
        self.assertIn("face_orientation", subject["change_footprint"])
        self.assertIn("viewpoint_projected_facial_geometry", subject["change_footprint"])
        self.assertNotIn("head_pose", subject["preserve"])
        self.assertIn("limited to slight yaw toward camera", prompt)
        self.assertIn("Only the resolved eyes-plus-head/face gaze endpoint", prompt)
        self.assertIn("change only the specified orientation and its natural projection", prompt)
        self.assertIn("anatomical proportions remain fixed", prompt)
        self.assertNotIn("without moving the head, body", prompt)

    def test_frontal_face_cannot_expand_gaze_into_face_reprojection(self):
        analysis = interaction_analysis()
        issue = analysis["subjects"][0]["gaze_issue"]
        issue["head_pose"] = "frontal"
        issue["gaze_repair_strategy"].update({
            "strategy_type": "eyes_plus_head_face_alignment",
            "head_face_adjustment": "slight face alignment if needed",
        })
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        subject = audit["resolved_features"][0]
        self.assertEqual(subject["final_state"]["method"], "eyes_only")
        self.assertNotIn("slight face alignment", prompt)
        self.assertNotIn("face_orientation", subject["change_footprint"])

    def test_generative_eye_wording_is_not_forwarded_to_editor(self):
        analysis = interaction_analysis()
        strategy = analysis["subjects"][0]["gaze_issue"]["gaze_repair_strategy"]
        strategy["eye_adjustment"] = "Reconstruct both eyes with pupils aimed at the camera."
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertNotIn("Reconstruct both eyes", prompt)
        self.assertIn("reposition the existing irises and pupils inside the unchanged eye openings", prompt)

    def test_two_stage_first_pass_excludes_gaze(self):
        contracts = build_feature_contracts(interaction_analysis(), include_gaze=False)
        self.assertEqual([item["feature"] for item in contracts], ["smile"])

    def test_empty_crop_plan_is_returned_without_inferring_a_crop_decision(self):
        prompt, _ = build_compact_editor_prompt(
            interaction_analysis(), "", "", {}, include_gaze=True,
        )
        self.assertIn("Keep the supplied image boundaries as the final frame", prompt)
        self.assertNotIn("Preserve the original composition and camera viewpoint", prompt)
        self.assertIn("Locations identify subjects only; they are not target positions", prompt)
        self.assertIn("relative positions, depth order, spacing, overlap, scale, visible extent, framing", prompt)
        self.assertIn("Apply facial edits locally while keeping the head, body, and surrounding content source-matched", prompt)

    def test_nonempty_crop_plan_keeps_resolved_crop_instruction(self):
        prompt, audit = build_compact_editor_prompt(
            interaction_analysis(), "", "", {"x": 0, "y": 0, "w": .9, "h": .9},
        )
        self.assertIn("Use the approved crop to create the 1:1 final frame", prompt)
        framing = prompt.split("Required framing:", 1)[1].split("Finished light and color target:", 1)[0]
        self.assertNotIn('"x":', framing)
        self.assertTrue(audit["framing_change_active"])

    def test_raw_prompt_crop_section_is_authoritative_for_compact_prompt(self):
        raw_crop = (
            "No crop. The supplied image boundaries are the final frame. "
            "Do not reframe or expand canvas. Scale uniformly only."
        )
        prompt, audit = build_compact_editor_prompt(
            interaction_analysis(), "", "", {}, raw_crop_instruction=raw_crop,
        )
        self.assertIn("Keep the supplied image boundaries as the final frame", prompt)
        self.assertIn("Preserve all internal proportions, tilt, relative sizes, and spacing", prompt)
        self.assertNotIn(raw_crop, prompt)
        self.assertNotIn("Resolved crop plan: {}", prompt)
        self.assertEqual(audit["crop_instruction_source"], "raw_edit_prompt")
        self.assertEqual(audit["raw_crop_instruction"], raw_crop)
        self.assertEqual(audit["compact_crop_instruction"], _compact_raw_crop_instruction(raw_crop))

    def test_raw_reframing_section_is_compacted_without_losing_geometry(self):
        raw = """Reframing:
- Target output aspect ratio: 4:3. The output canvas has to be this shape.
- Roughly how tight: a moderate tightening of the original frame.
This frame is narrower than the photograph you were given: the photograph's left and right edges cannot both be inside it.
Frame the photograph on what it is actually of. Closer framing was requested, so come in closer than the original frame.
Everything keeps the proportions, the tilt, and the relative sizes and spacing it has in the input photograph."""
        compact = _compact_raw_crop_instruction(raw)
        self.assertIn("The finished picture is a 4:3 landscape frame, narrower than the photograph", compact)
        self.assertIn("moderate tightening", compact)
        self.assertIn("as a straight cut from the photograph", compact)
        self.assertIn("Come in visibly closer", compact)
        self.assertIn("shrink the whole photograph evenly", compact)
        self.assertIn("never narrow anything", compact)

    def test_same_shape_near_full_frame_does_not_become_an_active_reframe(self):
        raw = """Reframing:
- Target output aspect ratio: 1024:1536. The output canvas has to be this shape.
- Roughly how tight: close to the full frame; little more than the outer edges come off.
This frame is the same shape as the photograph you were given, so nothing has to be given up to reach it. If the photograph is already well framed, a small trim is a fine answer.
Frame the photograph on what it is actually of.
Everything keeps the proportions, the tilt, and the relative sizes and spacing it has in the input photograph."""
        compact = _compact_raw_crop_instruction(raw)
        self.assertIn("Keep the supplied framing and subject scale", compact)
        self.assertIn("at most trim a small amount", compact)
        self.assertIn("Do not zoom out, outpaint, or recompose", compact)
        self.assertNotIn("Reframe to", compact)

    def test_visible_same_shape_axis_trim_remains_an_active_reframe(self):
        analysis = interaction_analysis()
        crop_item = "右側裁去約14.5%的原始畫面與椅子，讓河景成為清楚主體。"
        analysis.update({
            "correction_plan": [crop_item],
            "correction_axes": [["crop"]],
            "recommended_aspect_ratio": "original",
        })
        raw = """Reframing:
- Target output aspect ratio: 1536:1024. The output canvas has to be this shape.
- Roughly how tight: close to the full frame; little more than the outer edges come off.
This frame is the same shape as the photograph you were given, so nothing has to be given up to reach it. If the photograph is already well framed, a small trim is a fine answer.
Frame the photograph on what it is actually of."""
        crop_plan = {"x": 0, "y": 0, "w": .9275, "h": .9275, "aspectRatio": "1536:1024"}
        prompt, audit = build_compact_editor_prompt(
            analysis, "", "", crop_plan, raw_crop_instruction=raw,
        )
        framing = prompt.split("Required framing:", 1)[1].split("Finished light and color target:", 1)[0]
        self.assertIn("Reframe to 1536:1024; a visible tightening", framing)
        self.assertIn("Framing goal: 右側裁去原始畫面與椅子，讓河景成為清楚主體。", framing)
        self.assertNotIn("14.5%", framing)
        self.assertNotIn("Do not zoom out, outpaint, or recompose", framing)
        self.assertTrue(audit["framing_change_active"])

    def test_approved_near_full_crop_is_not_demoted_to_keep_original_framing(self):
        analysis = interaction_analysis()
        crop_item = "裁切四周較鬆散的邊緣，集中人物互動並保留必要環境。"
        analysis.update({
            "correction_plan": [crop_item],
            "correction_axes": [["crop"]],
            "recommended_aspect_ratio": "original",
        })
        raw = """Reframing:
- Target output aspect ratio: 1248:1248. The output canvas has to be this shape.
- Roughly how tight: close to the full frame; little more than the outer edges come off.
This frame is the same shape as the photograph you were given, so nothing has to be given up to reach it. If the photograph is already well framed, a small trim is a fine answer.
Frame the photograph on what it is actually of."""
        crop_plan = {"x": .05, "y": .048, "w": .95, "h": .952, "aspectRatio": "1248:1248"}
        prompt, audit = build_compact_editor_prompt(
            analysis, "", "", crop_plan, raw_crop_instruction=raw,
        )
        framing = prompt.split("Required framing:", 1)[1].split("Finished light and color target:", 1)[0]
        self.assertIn("Reframe to 1248:1248; a visible tightening", framing)
        self.assertIn(f"Framing goal: {crop_item}", framing)
        self.assertNotIn("Keep the supplied framing and subject scale", framing)
        self.assertTrue(audit["framing_change_active"])

    def test_analysis_crop_item_is_not_repeated_after_required_framing(self):
        analysis = interaction_analysis()
        crop_item = "裁切畫面左側多餘空白，強化主體聚焦與構圖平衡。"
        analysis["correction_plan"].append(crop_item)
        analysis["correction_axes"] = [[], [], ["crop"]]
        prompt, _ = build_compact_editor_prompt(
            analysis, "", "", {}, raw_crop_instruction="No crop. Keep the supplied frame.",
        )
        self.assertIn("Keep the supplied image boundaries as the final frame", prompt)
        self.assertNotIn(crop_item, prompt)

    def test_alignment_correction_overrides_no_crop_tilt_preservation(self):
        analysis = interaction_analysis()
        alignment_item = "將照片逆時針旋轉約2至3度，以海平面為水平基準並自動裁切邊緣。"
        analysis.update({
            "correction_plan": [alignment_item, "提升岩壁中間調。"],
            "correction_axes": [["alignment"], ["exposure"]],
            "crop_box": {"enabled": False, "reason": "保留原始比例。"},
            "protected_regions": [{"label": "遠方島岸"}],
        })
        raw_crop = (
            "No crop. The supplied image boundaries are the final frame. "
            "Do not reframe or expand canvas. Scale uniformly only."
        )
        prompt, audit = build_compact_editor_prompt(
            analysis, "", "", {}, raw_crop_instruction=raw_crop,
        )
        framing = prompt.split("Required framing:", 1)[1].split("Finished light and color target:", 1)[0]
        other = prompt.split("Other approved corrections:", 1)[1].split("Resolved subject final states:", 1)[0]
        preserve = next(
            line for line in prompt.splitlines()
            if line.startswith("- Preserve these unselected properties")
        )
        self.assertIn("Use the supplied image boundaries as the base frame", framing)
        self.assertIn("trim only the exposed outer wedges", framing)
        self.assertNotIn("preserve internal proportions, tilt", framing.lower())
        self.assertIn(alignment_item, other)
        self.assertIn("scale", preserve)
        self.assertNotIn("visible extent", preserve)
        self.assertNotIn("framing", preserve)
        self.assertIn("Approved alignment owns tilt, perspective", prompt)
        self.assertTrue(audit["alignment_change_active"])
        self.assertTrue(audit["framing_change_active"])

    def test_active_crop_keeps_semantic_goal_and_removes_framing_conflicts(self):
        analysis = interaction_analysis()
        crop_item = "裁去右側車門與下方多餘沙地，保留左側岩壁與中央峽谷。"
        analysis.update({
            "correction_plan": [crop_item, "提升岩壁中間調。"],
            "correction_axes": [["crop"], ["exposure"]],
            "crop_intent": {
                "trim_right": True,
                "trim_bottom": True,
                "subject_anchor": "center-left",
                "remove_edge_distractions": ["右側車門", "後視鏡"],
            },
            "subject_extent_box": {"reason": "左側岩壁與中央峽谷"},
            "protected_regions": [{"label": "中央峽谷開口"}],
        })
        raw = """Reframing:
- Target output aspect ratio: 3:2. The output canvas has to be this shape.
- Roughly how tight: a moderate tightening of the original frame.
Frame the photograph on what it is actually of."""
        prompt, audit = build_compact_editor_prompt(
            analysis, "", "", {"x": 0, "y": .05, "w": .9, "h": .8},
            raw_crop_instruction=raw,
        )
        framing = prompt.split("Required framing:", 1)[1].split("Finished light and color target:", 1)[0]
        other = prompt.split("Other approved corrections:", 1)[1].split("Resolved subject final states:", 1)[0]
        preserve = next(
            line for line in prompt.splitlines()
            if line.startswith("- Preserve these unselected properties")
        )
        self.assertIn(f"Framing goal: {crop_item}", framing)
        self.assertNotIn("trimming the right, bottom edges", framing)
        self.assertNotIn("Place outside the final frame", framing)
        self.assertIn("Main visual anchor: center-left", framing)
        self.assertIn("Keep comfortably inside the final frame: 中央峽谷開口", framing)
        self.assertNotIn(crop_item, other)
        for conflicting_property in ("scale", "visible extent", "framing"):
            self.assertNotIn(conflicting_property, preserve)
        self.assertIn("Required framing owns the final frame edges", prompt)
        self.assertTrue(audit["framing_change_active"])
        self.assertIn(crop_item, audit["compact_crop_guidance"])

    def test_active_crop_normalizes_stale_ratio_and_drops_box_coordinates(self):
        analysis = interaction_analysis()
        analysis.update({
            "recommended_aspect_ratio": "3:2",
            "correction_plan": [
                "裁切並輕微水平校正為16:9，使用約x=0.03、y=0.00、w=0.933、h=0.70的框線，移除底部頭髮並保持山脊連續。",
            ],
            "correction_axes": [["crop"]],
        })
        raw = """Reframing:
- Target output aspect ratio: 3:2. The output canvas has to be this shape.
- Roughly how tight: a moderate tightening of the original frame."""
        prompt, _ = build_compact_editor_prompt(
            analysis, "", "", {"x": .03, "y": 0, "w": .933, "h": .7},
            raw_crop_instruction=raw,
        )
        framing = prompt.split("Required framing:", 1)[1].split("Finished light and color target:", 1)[0]
        self.assertIn("Framing goal: 裁切並輕微水平校正為3:2，移除底部頭髮並保持山脊連續。", framing)
        self.assertNotIn("16:9", framing)
        self.assertNotIn("x=", framing)
        self.assertNotIn("Preserve internal proportions, tilt", framing)

    def test_numeric_frame_positions_are_dropped_whole_not_left_as_broken_clauses(self):
        goals = {
            "以9:16輸出比例重新構圖；左右取景範圍設定在原圖約22%至78.25%之間；接受尾羽右側外緣出框。":
                "以9:16輸出比例重新構圖；接受尾羽右側外緣出框。",
            "以9:16輸出比例重新構圖；將原始正方形畫面裁為左起0.18、上起0.00、寬0.5625、高1.00的直幅；裁去右側大段尾羽。":
                "以9:16輸出比例重新構圖；裁去右側大段尾羽。",
            "將原始方形畫面裁切為從左側百分之二十四起、完整保留高度的直式畫框；保留鳥頭與喙。":
                "保留鳥頭與喙。",
            "右側裁去約14.5%的原始畫面與椅子，讓河景成為清楚主體。":
                "右側裁去原始畫面與椅子，讓河景成為清楚主體。",
        }
        for raw, expected in goals.items():
            with self.subTest(raw=raw):
                self.assertEqual(_compact_crop_goal_text(raw, "944:1680"), expected)

    def test_crop_intent_supplies_direction_when_reviewed_plan_has_no_crop_item(self):
        analysis = interaction_analysis()
        analysis.update({
            "correction_axes": [[], []],
            "crop_recommendation": "",
            "crop_box": {"reason": ""},
            "crop_intent": {
                "trim_right": True,
                "trim_bottom": True,
                "remove_edge_distractions": ["右側車門", "後視鏡"],
            },
        })
        raw = """Reframing:
- Target output aspect ratio: 3:2. The output canvas has to be this shape.
- Roughly how tight: a moderate tightening of the original frame."""
        prompt, _ = build_compact_editor_prompt(
            analysis, "", "", {"x": 0, "y": 0, "w": .9, "h": .8},
            raw_crop_instruction=raw,
        )
        framing = prompt.split("Required framing:", 1)[1].split("Finished light and color target:", 1)[0]
        self.assertIn("trimming the right, bottom edges", framing)
        self.assertIn("Place outside the final frame: 右側車門; 後視鏡", framing)

    def test_unknown_crop_instruction_falls_back_verbatim(self):
        raw = "A future crop format with semantics this compactor does not know."
        self.assertEqual(_compact_raw_crop_instruction(raw), raw)

    def test_raw_style_section_becomes_compact_executable_instructions(self):
        analysis = interaction_analysis()
        analysis["style_options"] = {"pose": "playful", "lighting": "soft"}
        raw_style = """User-selected style overrides are mandatory same-scene reshoot requirements.
- Pose: Playful: Transform the subject into a clearly playful pose, not just a slightly nicer version of the original. The result should feel lively.
- Lighting: Soft Glow: Transform the image with a natural soft-glow lighting style inspired by softly lit street portraits and gentle ambient backlight. The result should feel luminous.

Pose override requirement: Do not preserve the original pose. Replace the original body language with a visibly different pose."""
        prompt, audit = build_compact_editor_prompt(
            analysis, "", "", {}, raw_style_instruction=raw_style,
        )
        compact = _compact_raw_style_instruction(raw_style)
        self.assertIn("Pose: Playful: Transform the subject into a clearly playful pose", prompt)
        self.assertIn("Lighting: Soft Glow: Transform the image with a natural soft-glow lighting style", prompt)
        self.assertIn("must visibly change body language", prompt)
        self.assertIn("overrides preserving its original state", prompt)
        self.assertNotIn('{"pose": "playful"', prompt)
        self.assertIn("The result should feel lively", compact)
        self.assertIn("The result should feel luminous", compact)
        self.assertNotIn("body_pose", audit["resolved_features"][0]["preserve"])
        self.assertNotIn("head_pose", audit["resolved_features"][0]["preserve"])
        self.assertNotIn("lighting", audit["resolved_features"][0]["preserve"])
        self.assertEqual(audit["style_instruction_source"], "raw_edit_prompt")
        self.assertEqual(audit["raw_style_instruction"], raw_style)
        self.assertEqual(audit["compact_style_instruction"], compact)
        self.assertLess(
            prompt.index("Mandatory style overrides:"),
            prompt.index("Resolved subject final states:"),
        )
        preserve_line = next(
            line for line in prompt.splitlines()
            if line.startswith("- Preserve these unselected properties")
        )
        for pose_owned_property in (
            "relative positions", "spacing", "overlap", "scale", "visible extent",
        ):
            self.assertNotIn(pose_owned_property, preserve_line)
        self.assertIn("positions of unselected people and objects", preserve_line)
        self.assertIn("Pose-first execution order", prompt)

    def test_pose_override_removes_stale_gaze_planner_body_preservation(self):
        analysis = interaction_analysis()
        analysis["style_options"] = {"pose": "playful"}
        strategy = analysis["subjects"][0]["gaze_issue"]["gaze_repair_strategy"]
        strategy.update({
            "strategy_type": "eyes_plus_head_face_alignment",
            "head_face_adjustment": (
                "Turn the head visibly toward the camera while retaining facial proportions. "
                "Keep the torso, shoulders, seated pose, and camera framing unchanged."
            ),
        })
        raw_style = """User-selected style overrides are mandatory same-scene reshoot requirements.
- Pose: Playful: Transform the subject into a clearly playful pose with visibly changed body language.

Pose override requirement: Do not preserve the original pose."""
        prompt, _ = build_compact_editor_prompt(
            analysis,
            "",
            "",
            {},
            raw_crop_instruction="No crop. Keep the supplied frame.",
            raw_style_instruction=raw_style,
        )
        self.assertIn("Turn the head visibly toward the camera", prompt)
        self.assertNotIn("Keep the torso, shoulders, seated pose", prompt)
        self.assertIn("Preserve all internal proportions, tilt, relative sizes, and spacing", prompt)
        self.assertNotIn("let the selected subject's head, limbs, silhouette", prompt)

    def test_cheer_pose_preserves_occupied_supporting_arms(self):
        analysis = interaction_analysis()
        analysis["style_options"] = {"pose": "cheer"}
        raw_style = (
            "User-selected style overrides are mandatory same-scene reshoot requirements.\n"
            f"- Pose: Cheer: {STYLE_OPTION_INSTRUCTIONS['pose']['cheer']}\n\n"
            "Pose override requirement: Do not preserve the original pose."
        )
        prompt, _ = build_compact_editor_prompt(
            analysis, "", "", {}, raw_style_instruction=raw_style,
        )
        self.assertIn("When one hand holds an object or supports another person", prompt)
        self.assertIn("retain that interaction and lift only the free arm", prompt)
        self.assertIn("both hands hold or support a child", prompt)
        self.assertIn("retain the two-handed interaction", prompt)
        self.assertIn("exactly two arms and two hands per person", prompt)

    def test_cool_pose_uses_pockets_only_when_the_scene_supports_them(self):
        instruction = STYLE_OPTION_INSTRUCTIONS["pose"]["cool"]
        self.assertIn("place one or both hands in existing pockets", instruction)
        self.assertIn("visible clothing, held items, and scene support it", instruction)
        self.assertIn("otherwise hook a thumb", instruction)

    def test_non_pose_edit_keeps_existing_gaze_body_preservation(self):
        analysis = interaction_analysis()
        strategy = analysis["subjects"][0]["gaze_issue"]["gaze_repair_strategy"]
        strategy.update({
            "strategy_type": "eyes_plus_head_face_alignment",
            "head_face_adjustment": (
                "Turn the head visibly toward the camera. "
                "Keep the torso, shoulders, body pose, and camera framing unchanged."
            ),
        })
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn("Keep the torso, shoulders, body pose, and camera framing unchanged", prompt)

    def test_gaze_planner_does_not_request_body_pose_preservation(self):
        instruction = build_eye_repair_planning_instruction([{
            "subject_index": 0,
            "label": "central woman",
            "needs_gaze_repair": True,
            "needs_blink_repair": False,
            "gaze_issue": {"head_pose": "strong_yaw", "gaze_state": "off_camera"},
        }])
        self.assertNotIn("Keep the torso, shoulders, body pose", instruction)
        self.assertNotIn("skin, torso, shoulders, body pose, clothing", instruction)
        self.assertIn("Do not write any instruction that preserves, freezes, or restores", instruction)

    def test_no_raw_style_section_does_not_recreate_json_metadata(self):
        analysis = interaction_analysis()
        analysis["style_options"] = {"pose": "playful"}
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn("Mandatory style overrides:\nNo style override.", prompt)
        self.assertNotIn('{"pose": "playful"}', prompt)
        self.assertEqual(audit["style_instruction_source"], "none")

    def test_without_pose_keeps_style_block_after_resolved_states(self):
        prompt, _ = build_compact_editor_prompt(interaction_analysis(), "", "", {})
        self.assertLess(
            prompt.index("Resolved subject final states:"),
            prompt.index("Mandatory style overrides:"),
        )
        self.assertNotIn("Pose-first execution order", prompt)
        self.assertNotIn("selected pose", prompt)

    def test_size_and_lighting_styles_narrow_general_preservation_and_color_checks(self):
        analysis = interaction_analysis()
        analysis["style_options"] = {"size": "wide", "lighting": "soft"}
        analysis["memory_color_strategy"] = "Preserve the captured hard light and original white balance."
        raw_style = """User-selected style overrides are mandatory same-scene reshoot requirements.
- Lighting: Soft Glow: Transform the image with a natural soft-glow lighting style. Soften shadow transitions and add coherent ambient glow to subject and background.
- Framing size: Wide: Reframe this as the same photograph captured from further back. Scale the original content to about 80% and continue the same scene around it.

Framing-size override requirement: This is a true wider-framing transformation. Quantified wide-framing target: make the main subject's visible frame ratio about 20% smaller than in the original image."""
        prompt, audit = build_compact_editor_prompt(
            analysis, "", "", {}, raw_style_instruction=raw_style,
        )
        preserve_line = next(
            line for line in prompt.splitlines()
            if line.startswith("- Preserve these unselected properties")
        )
        for conflicting_property in ("scale", "visible extent", "framing", "lighting logic"):
            self.assertNotIn(conflicting_property, preserve_line)
        self.assertIn("Lighting and color plan: Use the selected Soft Glow style as the final atmosphere", prompt)
        self.assertIn("Apply the selected Soft Glow atmosphere coherently across the scene", prompt)
        self.assertIn("main subject visibly smaller by about 20%", prompt)
        self.assertNotIn("retains the original photograph's intentional color character", prompt)
        self.assertNotIn("Preserve the captured hard light and original white balance", prompt)
        self.assertNotIn("selected pose", prompt)
        self.assertLess(
            prompt.index("Resolved subject final states:"),
            prompt.index("Mandatory style overrides:"),
        )
        self.assertNotIn("lighting", audit["resolved_features"][0]["preserve"])
        self.assertIn("framing", audit["style_change_footprint"])
        self.assertIn("lighting logic", audit["style_change_footprint"])

    def test_vertical_angle_and_size_assign_conflicting_properties_once(self):
        analysis = interaction_analysis()
        analysis["style_options"] = {"angle": "high", "size": "wide"}
        raw_style = """User-selected style overrides are mandatory same-scene reshoot requirements.
- Angle: High Shot: Take this exact photograph and re-angle it into a clearly high-angle shot looking down on the subject. Crucially, this is a high angle shot from the SAME distance as the original. The subject spans the same fraction of the frame and stays the same size and crop as in the original. Do not zoom or move the camera closer or farther.
- Framing size: Wide: Reframe this as the same photograph captured from further back. Scale the original content down to about 80%.

Angle override requirement (vertical only): Change only the height the shot is seen from.

Framing-size override requirement: This is a true wider-framing transformation. Quantified wide-framing target: make the main subject's visible frame ratio about 20% smaller than in the original image."""
        prompt, _ = build_compact_editor_prompt(
            analysis, "", "", {}, raw_style_instruction=raw_style,
        )
        self.assertIn("angle owns the up/down viewing direction", prompt)
        self.assertIn("size owns camera distance, subject scale, crop, and frame extent", prompt)
        self.assertNotIn("SAME distance", prompt)
        self.assertNotIn("same size and crop", prompt)
        self.assertNotIn("Do not zoom", prompt)

    def test_compact_prompt_has_one_joint_instruction_and_keeps_other_corrections(self):
        prompt, audit = build_compact_editor_prompt(
            interaction_analysis(), "Natural photographic finish.", "", {"x": 0, "y": 0, "w": 1, "h": 1},
        )
        self.assertIn("unmistakably happy, intense and natural expression of genuine laughter", prompt)
        self.assertIn("not a generic replacement or merely a broad posed smile", prompt)
        self.assertIn("existing neutral expression into an unmistakably happy", prompt)
        self.assertIn("existing face—not a generic replacement", prompt)
        self.assertIn("show spontaneous joy coherently through the mouth", prompt)
        self.assertIn("do not leave a neutral, worried, stern, or angry upper face", prompt)
        self.assertIn("not an oversized mouth or reshaped facial features", prompt)
        self.assertIn("Fixed identity geometry", prompt)
        self.assertIn("head/face proportions", prompt)
        self.assertIn("jaw and cheekbone structure", prompt)
        self.assertIn("baseline eye–nose–mouth layout", prompt)
        self.assertIn("eye-globe size, eye centers and interocular distance", prompt)
        self.assertNotIn("Preserve stable, source-supported upper-eyelid differences", prompt)
        self.assertNotIn("including a genuine one-monolid/one-double-lid pattern", prompt)
        self.assertNotIn("captured left/right eyelid asymmetry is not protected identity", prompt)
        self.assertIn("nose geometry, philtrum length, and ear shape", prompt)
        self.assertIn("limit expression movement to mouth-corner position and opening", prompt)
        self.assertIn("eyelid aperture over the fixed landmarks above", prompt)
        self.assertIn("using the captured mouth center, lip contour/volume", prompt)
        self.assertIn("visible dental characteristics rather than regenerating or beautifying the whole face", prompt)
        self.assertIn("original skin age and fold pattern", prompt)
        self.assertIn("crow's-feet, under-eye creases, and nasolabial folds", prompt)
        self.assertIn("Use a subtle eyes-only correction and keep the original head/face angle fixed", prompt)
        self.assertIn("gaze changes iris/pupil direction only", prompt)
        self.assertIn("narrow both eyelids together through natural cheek lift", prompt)
        self.assertIn("clear iris visibility", prompt)
        self.assertIn("retain iris size/color/texture and pupil proportions", prompt)
        self.assertIn("catchlight count, shape, brightness, and light-source logic", prompt)
        self.assertIn("original lash line/lashes, brow design, eyeliner, eyeshadow, mascara, false lashes", prompt)
        self.assertIn("without redesigning, enhancing, removing, or replacing them", prompt)
        self.assertIn("reach clear direct lens contact", prompt.lower())
        self.assertIn("Reduce the bright background distraction", prompt)
        self.assertNotIn("Redirect the central woman's gaze", prompt)
        self.assertEqual(audit["strategy"], "resolved_compact")
        removed = audit["removed_preservations"][0]["properties"]
        self.assertIn("eyelid_aperture", removed)
        self.assertIn("gaze_direction", removed)
        for identity_property in (
            "eye_landmark_geometry", "iris_identity", "eyelash_identity",
            "eye_makeup", "glasses_and_reflections",
        ):
            self.assertIn(identity_property, audit["resolved_features"][0]["preserve"])

    def test_shared_face_rules_are_not_repeated_for_multiple_people(self):
        analysis = interaction_analysis()
        analysis["subjects"].append({
            "label": "right man",
            "box": {"x": .65, "y": .2, "w": .25, "h": .45},
            "is_person": True,
            "smile_issue": {"smile_intensity": "gentle", "adjust_safe": True},
            "gaze_issue": {
                "should_fix": True,
                "gaze_repair_strategy": {
                    "strategy_type": "eyes_only", "repair_strength": "subtle",
                },
            },
        })
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertEqual(prompt.count("Subject:"), 2)
        self.assertEqual(prompt.count("Shared facial execution rules:"), 1)
        self.assertEqual(prompt.count("Fixed identity geometry"), 1)
        self.assertEqual(prompt.count("Carry the original lash line/lashes"), 1)

    def test_laugh_only_preserves_captured_gaze_and_eye_identity(self):
        prompt, _ = build_compact_editor_prompt(
            interaction_analysis(), "", "", {}, include_gaze=False,
        )
        self.assertIn("existing neutral expression into an unmistakably happy, intense and natural expression of genuine laughter", prompt)
        self.assertIn("brow/periocular tension, and eyelid aperture over the fixed landmarks", prompt)
        self.assertIn("Keep the captured iris/pupil direction, sclera balance, and catchlights", prompt)

    def test_laugh_and_blink_resolve_to_balanced_natural_openness(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["blink_issue"] = {
            "should_fix": True, "reason": "accidental half blink",
        }
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn("the same smile- or laughter-shaped final aperture", prompt)
        self.assertIn("matched natural periocular narrowing", prompt)
        self.assertIn("Shared eye-pair target for every named blink repair", prompt)
        self.assertIn("visible iris width", prompt)
        self.assertNotIn("Primary local correction", prompt)
        self.assertNotIn("may not independently narrow either eyelid after repair", prompt)
        self.assertNotIn("blink endpoint, not independent smile-induced squint", prompt)
        self.assertNotIn("preserve eyelid aperture", prompt.lower())
        self.assertEqual(
            set(audit["resolved_features"][0]["features"]),
            {"smile", "gaze", "blink"},
        )

    def test_hair_cleanup_cannot_become_a_face_redraw(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["hair_issue"] = {
            "should_fix": True, "fix_action": "redistribute",
            "hair_state": "bangs_clumped", "front_hair": "clumped",
            "correction_plan": "Redistribute the clumped fringe.",
        }
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        self.assertNotIn("Shared hair execution rules", prompt)
        self.assertIn("Hair target — existing fringe: Redistribute the clumped fringe.", prompt)
        self.assertNotIn("Hair level — local cleanup", prompt)
        self.assertIn("spread those same strands across the adjacent abnormal wide gaps", prompt)
        self.assertIn("Hair execution boundary", prompt)
        self.assertEqual(prompt.count("Redistribute the clumped fringe."), 1)
        self.assertIn("Fixed identity geometry", prompt)
        self.assertTrue(any("hair" in item["features"] for item in audit["resolved_features"]))

    def test_inconsistent_redistribute_does_not_expand_face_strands_into_a_fringe_edit(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["hair_issue"] = {
            "should_fix": True,
            "fix_action": "redistribute",
            "hair_state": "strands_across_face",
            "front_hair": "clumped",
            "correction_plan": "梳整覆蓋右臉及鼻樑的散亂髮絲。",
        }
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        self.assertEqual(prompt.count("梳整覆蓋右臉及鼻樑的散亂髮絲。"), 1)
        self.assertIn("Redistribute only the specifically named strands", prompt)
        self.assertIn("do not reinterpret them as a fringe", prompt)
        self.assertNotIn("reduce clumped/oily sheen", prompt)
        self.assertNotIn("even but translucent curtain", prompt)
        self.assertTrue(any("hair" in item["features"] for item in audit["resolved_features"]))

    def test_local_hair_mode_preserves_motion_without_a_mapped_plan_sentence(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["hair_issue"] = {
            "should_fix": True, "fix_action": "lay_back",
        }
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertNotIn("Analysis-specific hair target", prompt)
        self.assertNotIn("Shared hair execution rules", prompt)
        self.assertIn("Lay only the named loose strands", prompt)

    def test_hair_only_avoids_inviting_a_full_face_reconstruction(self):
        analysis = interaction_analysis()
        analysis["_advanced_options"].update({
            "smile_adjustment": False,
            "gaze_contact_detection": False,
            "eye_closed_detection": False,
        })
        analysis["subjects"][0]["hair_issue"] = {
            "should_fix": True, "fix_action": "remove",
            "correction_plan": "Remove two thin strands crossing the cheek.",
        }
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertNotIn("Shared facial execution rules", prompt)
        self.assertNotIn("Fixed identity geometry", prompt)
        self.assertNotIn("Shared hair execution rules", prompt)
        self.assertEqual(prompt.count("Remove two thin strands crossing the cheek."), 1)
        self.assertIn("anatomical facial proportions and profile identity", prompt)
        self.assertIn("projected eye–nose–mouth geometry fixed", prompt)

    def test_hair_and_skin_preservation_do_not_cancel_facial_endpoints(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["hair_issue"] = {
            "should_fix": True, "fix_action": "lay_back",
            "correction_plan": "Lay back the strands crossing the face.",
        }
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn("reach clear direct lens contact", prompt)
        self.assertIn("expression of genuine laughter", prompt)
        self.assertEqual(prompt.count("Lay back the strands crossing the face."), 1)
        self.assertNotIn("Shared hair execution rules", prompt)
        self.assertIn("Every required final state above is mandatory", prompt)
        skin_boundary = prompt.split("Skin boundary:", 1)[1].split("Finish:", 1)[0]
        self.assertIn("native complexion depth, undertone, and chroma", skin_boundary)
        self.assertNotIn("eye shape", skin_boundary)
        self.assertNotIn("gaze", skin_boundary)

    def test_fully_groomed_hair_uses_the_second_level(self):
        analysis = interaction_analysis()
        analysis["_advanced_options"]["calm_hair"] = True
        analysis["subjects"][0]["hair_issue"] = {
            "should_fix": True, "fix_action": "lay_back",
        }
        analysis["correction_plan"].append("梳理側臉前後亂髮，讓髮絲更整齊不遮臉。")
        analysis["correction_axes"] = [[], [], ["hair"]]
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn("Hair level — fully groomed (mandatory visible change)", prompt)
        self.assertIn("entire visible hairstyle", prompt)
        self.assertIn("clearly tidier at normal viewing size", prompt)
        self.assertIn("natural strand separation and texture", prompt)
        self.assertNotIn("analyzed messy or face-obscuring hair region", prompt)
        self.assertNotIn("Preserve-motion boundary", prompt)
        self.assertNotIn("Hair level — preserve captured motion", prompt)

    def test_fully_groomed_does_not_apply_without_detected_hair_fix(self):
        analysis = interaction_analysis()
        analysis["_advanced_options"]["calm_hair"] = True
        analysis["subjects"][0]["hair_issue"] = {
            "hair_state": "motion", "fix_action": "none",
            "should_fix": False, "correction_plan": "不適用",
        }
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        self.assertNotIn("Hair level — fully groomed", prompt)
        self.assertFalse(any("hair" in item["features"] for item in audit["resolved_features"]))

    def test_per_person_hair_level_overrides_global_default(self):
        analysis = interaction_analysis()
        analysis["_advanced_options"]["calm_hair"] = False
        analysis["subjects"][0]["hair_issue"] = {
            "should_fix": True, "fix_action": "lay_back", "_user_level": "tidy",
        }
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn("Hair level — fully groomed", prompt)

    def test_manual_motion_cleanup_enters_compact_prompt_without_should_fix(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["hair_issue"] = {
            "hair_state": "motion", "fix_action": "none", "should_fix": False,
            "correction_plan": "不適用", "_user_forced": True, "_user_level": "dynamic",
        }
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn("Hair level — preserve motion", prompt)
        self.assertIn("visible wind or body-motion flow", prompt)
        self.assertTrue(any("hair" in item["features"] for item in audit["resolved_features"]))

    def test_manual_bangs_plan_is_not_narrowed_to_isolated_strands(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["hair_issue"] = {
            "hair_state": "styled_bangs", "front_hair": "even",
            "fix_action": "none", "should_fix": False,
            "correction_plan": "不適用", "_user_forced": True,
            "_user_level": "dynamic",
        }
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn("existing front fringe above the eyes", prompt)
        self.assertIn("do not add hair, increase hair mass, or create a new lock", prompt)
        self.assertNotIn("whole named front-hair region", prompt)
        self.assertNotIn("Hair level — local cleanup", prompt)
        self.assertNotIn("Lay only the named loose strands", prompt)

    def test_automatic_hair_ignores_stale_user_forced_marker(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["hair_issue"] = {
            "hair_state": "lock_out_of_place", "front_hair": "none",
            "fix_action": "lay_back", "should_fix": True,
            "correction_plan": "Return the named loose lock to its adjacent existing flow.",
            "_user_forced": True, "_user_level": "dynamic",
        }
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn("Hair level — local cleanup", prompt)
        self.assertIn("Lay only the named loose strands", prompt)
        self.assertNotIn("User-forced hair-only edit", prompt)
        hair_state = next(
            item["final_state"]
            for item in audit["resolved_features"]
            if item["final_state"].get("hair_level")
        )
        self.assertFalse(hair_state["hair_user_forced"])

    def test_hair_plan_text_is_routed_to_the_named_subject(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["hair_issue"] = {
            "should_fix": True, "fix_action": "redistribute",
            "correction_plan": "梳理central woman結成束的瀏海。",
        }
        analysis["subjects"].append({
            "label": "right man",
            "box": {"x": .65, "y": .2, "w": .25, "h": .45},
            "is_person": True,
            "hair_issue": {"should_fix": True, "fix_action": "lay_back", "correction_plan": "將right man臉旁的亂髮順回髮流。"},
        })
        central = "梳理central woman結成束的瀏海。"
        right = "將right man臉旁的亂髮順回髮流。"
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertEqual(prompt.count(central), 1)
        self.assertEqual(prompt.count(right), 1)
        other_block = prompt.split("Other approved corrections:", 1)[1].split("Resolved subject final states:", 1)[0]
        self.assertNotIn(central, other_block)
        self.assertNotIn(right, other_block)
        self.assertNotIn("Shared hair execution rules", prompt)

    def test_redistribute_is_mandatory_without_changing_bangs_type(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["hair_issue"] = {
            "should_fix": True,
            "hair_state": "bangs_clumped",
            "front_hair": "clumped",
            "fix_action": "redistribute",
            "correction_plan": "梳散瀏海中央油黏粗束並覆蓋其來源處的異常空隙。",
        }
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertNotIn("Hair level — local cleanup", prompt)
        self.assertIn("Hair edit — redistribute (required", prompt)
        self.assertIn("Preserve the source fringe type", prompt)
        self.assertIn("but not the defective clump layout", prompt)
        self.assertIn("changing shine alone is not completion", prompt)

    def test_redistribute_handles_multiple_clumps_as_one_fringe_region(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["hair_issue"] = {
            "should_fix": True,
            "hair_state": "bangs_clumped",
            "front_hair": "clumped",
            "fix_action": "redistribute",
            "visible_evidence": [
                "中央及兩側瀏海束寬明顯不一",
                "部分粗束之間形成較寬的裸露額頭空隙",
            ],
            "correction_plan": "梳散中央與左右粗束並覆蓋相鄰異常空隙。",
        }
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn("across the whole named fringe", prompt)
        self.assertIn("separate every named thick/stuck cord", prompt)
        self.assertIn("across the adjacent abnormal wide gaps", prompt)
        self.assertIn("Keep subtype-defining openings", prompt)

    def test_preserve_motion_level_bounds_a_vague_local_hair_plan(self):
        analysis = interaction_analysis()
        analysis["_advanced_options"].update({
            "calm_hair": False,
            "hair_mess_detection": True,
        })
        vague_plan = "梳理側臉前後亂髮，讓髮絲更整齊不遮臉。"
        analysis["subjects"][0]["hair_issue"] = {
            "should_fix": True, "fix_action": "lay_back", "correction_plan": vague_plan,
        }
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertEqual(prompt.count(vague_plan), 1)
        self.assertIn("Hair execution boundary", prompt)
        self.assertIn("not a head or face regeneration", prompt)
        self.assertNotIn("Shared hair execution rules", prompt)

    def test_hair_axis_with_face_preservation_words_stays_out_of_skin_block(self):
        analysis = interaction_analysis()
        hair_plan = "移除中央女性落在臉上的髮絲；周圍五官、臉部輪廓與皮膚不變。"
        analysis["correction_plan"].append(hair_plan)
        analysis["correction_axes"] = [[], [], ["hair"]]
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        other = prompt.split("Other approved corrections:", 1)[1].split("Light and color execution:", 1)[0]
        skin = prompt.split("Skin boundary:", 1)[1]
        self.assertNotIn(hair_plan, other)
        self.assertNotIn(hair_plan, skin)

    def test_call_a_hair_leak_never_becomes_an_other_correction(self):
        analysis = interaction_analysis()
        leaked = "僅清理少量明顯飛散髮絲與邊緣雜亂，不改變原本瀏海造型"
        analysis["correction_plan"].append(leaked)
        analysis["correction_axes"] = [[], [], ["hair_edge", "detail"]]
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertNotIn(leaked, prompt)

    def test_preserve_motion_does_not_settle_the_whole_windblown_head(self):
        analysis = interaction_analysis()
        analysis["_advanced_options"].update({"calm_hair": False, "hair_mess_detection": True})
        analysis["subjects"][0]["hair_issue"] = {
            "should_fix": True,
            "hair_state": "windblown",
            "fix_action": "lay_back",
            "visible_evidence": ["多根長髮橫跨眼睛、鼻子與嘴部，其餘頭髮向右飄動"],
            "correction_plan": "將前方長髮及外飄髮束全部收回連續的長髮流。",
        }
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn("Hair level — preserve motion (authoritative final state)", prompt)
        self.assertIn("Sweep displaced windblown hair away from the eyes, nose, mouth, and central face", prompt)
        self.assertIn("a small number of clean, coherent locks and broad streaming arcs", prompt)
        self.assertIn("Analysis-specific hair target (use only to locate the affected regions", prompt)

    def test_preserve_motion_cleans_outer_disorder_without_removing_wind(self):
        analysis = interaction_analysis()
        analysis["_advanced_options"].update({"calm_hair": False, "hair_mess_detection": True})
        analysis["subjects"][0]["hair_issue"] = {
            "should_fix": True,
            "hair_state": "windblown",
            "fix_action": "settle",
            "visible_evidence": ["整體髮尾向畫面右方飄動"],
            "correction_plan": "將整頭風吹髮絲梳回靜止輪廓。",
        }
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        target = "Analysis-specific hair target (use only to locate the affected regions"
        endpoint = "Hair level — preserve motion (authoritative final state)"
        self.assertIn(target, prompt)
        self.assertIn("將整頭風吹髮絲梳回靜止輪廓", prompt)
        self.assertIn(endpoint, prompt)
        self.assertLess(prompt.index(target), prompt.index(endpoint))
        self.assertIn("controlled and portrait-ready rather than messy", prompt)
        self.assertIn("do not settle the hair into a still-air shape", prompt)
        self.assertTrue(any("hair" in item["features"] for item in audit["resolved_features"]))

    def test_preserve_motion_method_is_absent_without_a_hair_correction(self):
        prompt, _ = build_compact_editor_prompt(interaction_analysis(), "", "", {})
        self.assertNotIn("Hair level — preserve captured motion", prompt)

    def test_all_rejected_hair_work_does_not_reactivate_from_structured_issue(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["hair_issue"] = {
            "should_fix": True, "fix_action": "redistribute",
        }
        analysis["correction_plan"] = analysis["correction_plan"][:2]
        analysis["correction_axes"] = [[], []]
        analysis["rejected_corrections"] = ["Redistribute the clumped fringe."]
        analysis["rejected_axes"] = [["hair"]]
        analysis["rejected_preserve"] = ["Retain the captured fringe arrangement."]
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        self.assertNotIn("Shared hair execution rules", prompt)
        self.assertTrue(all("hair" not in item["features"] for item in audit["resolved_features"]))

    def test_compact_prompt_uses_photo_specific_target_and_concise_execution(self):
        analysis = interaction_analysis()
        analysis["edit_intent"] = "明亮、清爽、有層次的室內人像。"
        analysis["lighting_strategy"] = "提亮人物與室內中間調，建立清楚的明暗分離。"
        analysis["memory_color_strategy"] = "保留原本偏冷的室內調性，膚色獨立保護。"
        prompt, _ = build_compact_editor_prompt(
            analysis, "", "", {"x": 0, "y": 0, "w": 1, "h": 1},
        )
        target_pos = prompt.index("Finished light and color target:")
        plan_pos = prompt.index("Other approved corrections:")
        execution_pos = prompt.index("Light and color execution:")
        self.assertLess(target_pos, plan_pos)
        self.assertLess(plan_pos, execution_pos)
        self.assertIn("Finished visual result: 明亮、清爽、有層次的室內人像。", prompt)
        self.assertIn("Lighting plan: 提亮人物與室內中間調，建立清楚的明暗分離。", prompt)
        self.assertIn("Color plan: 保留原本偏冷的室內調性，膚色獨立保護。", prompt)
        self.assertIn("Realize the finished target through the approved corrections", prompt)
        self.assertIn("Make every named adjustment clearly visible", prompt)
        self.assertIn("Skin boundary:", prompt)
        self.assertNotIn("open shadows selectively", prompt)
        self.assertNotIn("shape highlights with a smooth roll-off", prompt)
        self.assertNotIn("Final color check", prompt)
        self.assertIn("保留原本偏冷的室內調性", prompt)
        self.assertIn("Let scene exposure and palette change wherever the approved plan calls for it", prompt)
        self.assertIn("Apply scene white balance, hue, and saturation to the non-skin palette independently from complexion", prompt)

    def test_compact_audit_records_the_visual_target(self):
        analysis = interaction_analysis()
        analysis["edit_intent"] = "明亮通透"
        analysis["lighting_strategy"] = "提亮中間調"
        analysis["memory_color_strategy"] = "場景清爽，膚色獨立保護"
        _, audit = build_compact_editor_prompt(analysis, "", "", {})
        self.assertEqual(audit["visual_target"], {
            "edit_intent": "明亮通透",
            "lighting_strategy": "提亮中間調",
            "memory_color_strategy": "場景清爽，膚色獨立保護",
        })

    def test_unchecked_light_item_removes_stale_target_and_adds_preserved_choice(self):
        analysis = interaction_analysis()
        checked_color = "降低背景的綠色偏移。"
        rejected_light = "提亮人物與外套三分之一級。"
        preserve_light = "維持原圖人物與外套的曝光及陰影密度。"
        analysis.update({
            "edit_intent": "提亮人物並保持冷色背景。",
            "lighting_strategy": "將人物提亮三分之一級。",
            "memory_color_strategy": "整理背景青綠色。",
            "correction_plan": [checked_color],
            "correction_axes": [["white_balance"]],
            "rejected_corrections": [rejected_light],
            "rejected_axes": [["exposure", "shadow"]],
            "rejected_preserve": [preserve_light],
        })
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        self.assertNotIn("提亮人物並保持冷色背景", prompt)
        self.assertNotIn("將人物提亮三分之一級", prompt)
        self.assertIn("Color plan: 整理背景青綠色", prompt)
        self.assertIn(checked_color, prompt)
        self.assertNotIn(rejected_light, prompt)
        self.assertIn("User-preserved choices", prompt)
        self.assertIn(preserve_light, prompt)
        self.assertEqual(audit["user_preserved_choices"], [preserve_light])

    def test_unchecked_color_item_keeps_unaffected_lighting_strategy(self):
        analysis = interaction_analysis()
        analysis.update({
            "edit_intent": "提亮人物並調整青綠色。",
            "lighting_strategy": "提亮人物中間調。",
            "memory_color_strategy": "降低青綠色偏移。",
            "rejected_corrections": ["降低青綠色偏移。"],
            "rejected_axes": [["white_balance", "saturation"]],
            "rejected_preserve": ["維持原圖青綠色與飽和度。"],
        })
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertNotIn("提亮人物並調整青綠色", prompt)
        self.assertIn("Lighting plan: 提亮人物中間調", prompt)
        self.assertNotIn("Color plan: 降低青綠色偏移", prompt)
        self.assertIn("維持原圖青綠色與飽和度", prompt)

    def test_any_unchecked_item_removes_freeform_edit_intent(self):
        analysis = interaction_analysis()
        analysis.update({
            "edit_intent": "提亮人物、清除背景雜物並調整青綠色。",
            "lighting_strategy": "提亮人物中間調。",
            "memory_color_strategy": "降低青綠色偏移。",
            "rejected_corrections": ["清除背景紙箱。"],
            "rejected_axes": [["distraction"]],
            "rejected_preserve": ["保留原圖背景紙箱。"],
        })
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertNotIn("提亮人物、清除背景雜物並調整青綠色", prompt)
        self.assertIn("Lighting plan: 提亮人物中間調", prompt)
        self.assertIn("Color plan: 降低青綠色偏移", prompt)
        self.assertIn("保留原圖背景紙箱", prompt)

    def test_unchecked_item_without_axis_metadata_discards_both_freeform_strategies(self):
        analysis = interaction_analysis()
        analysis.update({
            "edit_intent": "提亮人物並調整青綠色。",
            "lighting_strategy": "提亮人物中間調。",
            "memory_color_strategy": "降低青綠色偏移。",
            "rejected_corrections": ["舊資料中的未分類修改。"],
            "rejected_axes": [],
            "rejected_preserve": ["維持原圖對應狀態。"],
        })
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        self.assertNotIn("提亮人物並調整青綠色", prompt)
        self.assertNotIn("Lighting plan: 提亮人物中間調", prompt)
        self.assertNotIn("Color plan: 降低青綠色偏移", prompt)
        self.assertIn("維持原圖對應狀態", prompt)

    def test_skin_warming_is_bounded_to_cast_removal(self):
        analysis = interaction_analysis()
        analysis["subjects"] = [{
            "label": "central woman", "is_person": True,
            "skin": "原生淺膚色、中性底調；室內暖光應保留；非預期偏色：輕微偏青。",
        }]
        analysis["correction_plan"].append("將中央女性膚色微微調暖。")
        analysis["correction_axes"] = [[], [], ["skin", "white_balance"]]
        prompt, audit = build_compact_editor_prompt(
            analysis, "", "", {"x": 0, "y": 0, "w": 1, "h": 1},
        )
        self.assertIn("Approved skin correction: 將中央女性膚色微微調暖", prompt)
        self.assertIn("Apply it as the named cast correction and stop at the recorded native complexion", prompt)
        self.assertIn("Apply scene white balance, hue, and saturation to the non-skin palette independently from complexion", prompt)
        self.assertIn("原生淺膚色、中性底調", prompt)
        self.assertNotIn("- 將中央女性膚色微微調暖。\n\nLight and color execution", prompt)
        self.assertEqual(audit["color_contract"]["skin_corrections"], ["將中央女性膚色微微調暖。"])

    def test_face_exposure_correction_is_scoped_as_tonal_not_face_redraw(self):
        analysis = interaction_analysis()
        correction = "提高主體臉部局部曝光與陰影細節，增強輪廓層次感。"
        analysis["correction_plan"].append(correction)
        analysis["correction_axes"] = [[], [], ["exposure", "shadow"]]
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        face_line = f"Approved local face tonal correction: {correction}"
        self.assertIn(face_line, prompt)
        self.assertIn("full named region, including clothing when specified", prompt)
        self.assertIn("brighten skin through exposure while retaining facial geometry and complexion", prompt)
        other_block = prompt.split("Other approved corrections:", 1)[1].split("Light and color execution:", 1)[0]
        self.assertNotIn(correction, other_block)
        self.assertEqual(audit["color_contract"]["face_tonal_corrections"], [correction])


    def test_scene_exposure_and_non_skin_grade_are_preserved(self):
        analysis = interaction_analysis()
        scene = "提升月台陰影0.5 EV，保留透視線，使視線回到城市。"
        color = "在非皮膚區域降低綠色偏色。"
        face = "提升人物臉部與黑色上衣中間調0.4 EV。"
        analysis["correction_plan"] = [scene, color, face]
        analysis["correction_axes"] = [["exposure", "shadow"], ["white_balance"], ["exposure"]]
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        other = prompt.split("Other approved corrections:", 1)[1].split("Light and color execution:", 1)[0]
        self.assertIn(scene, other)
        self.assertIn(color, other)
        self.assertEqual(audit["color_contract"]["skin_corrections"], [])
        self.assertEqual(audit["color_contract"]["face_tonal_corrections"], [face])
        analysis["correction_plan"] = ["將人物膚色偏綠修正為自然膚色。"]
        analysis["correction_axes"] = [["skin"]]
        _, audit = build_compact_editor_prompt(analysis, "", "", {})
        self.assertEqual(audit["color_contract"]["skin_corrections"], analysis["correction_plan"])

    def test_removal_naming_a_colored_face_is_not_skin_work(self):
        removal = "移除畫面中央的黑色路牌桿，依照被遮住的雕塑輪廓補足，確保前方雕塑臉部自然連續。"
        for subjects in ([], interaction_analysis()["subjects"]):
            analysis = {"subjects": subjects, "correction_plan": [removal], "correction_axes": [["distraction"]]}
            prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
            other = prompt.split("Other approved corrections:", 1)[1].split("Light and color execution:", 1)[0]
            self.assertIn(removal, other)
            self.assertEqual(audit["color_contract"]["skin_corrections"], [])

    def test_skin_tagged_item_without_a_person_stays_in_the_prompt(self):
        item = "將膚色偏綠修正為自然膚色。"
        analysis = {"subjects": [], "correction_plan": [item], "correction_axes": [["skin"]]}
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn(f"- {item}", prompt)
        self.assertEqual(audit["color_contract"]["skin_corrections"], [])

    def _other_corrections(self, items, axes):
        analysis = interaction_analysis()
        analysis["correction_plan"] = items
        analysis["correction_axes"] = axes
        prompt, _ = build_compact_editor_prompt(analysis, "", "", {})
        return prompt.split("Other approved corrections:", 1)[1].split("Light and color execution:", 1)[0]

    def test_items_merely_mentioning_hair_eyes_or_smiles_are_kept(self):
        items = [
            "移除畫面右側的金髮路人，以背景建築補足。",
            "Remove the white plastic chair on the left edge.",
            "移除桌上的髮夾與雜物。",
            "Darken the bright sign so the viewer's gaze returns to the couple.",
            "略提亮眼神光，讓人物更有精神。",
            "降低背景中嬉笑人群的存在感。",
            "輕微提升中央偏右女性長髮的暗部細節。",
        ]
        axes = [["distraction"]] * 6 + [["shadow"]]
        axes[3] = ["distraction", "contrast"]
        axes[4] = ["highlight"]
        other = self._other_corrections(items, axes)
        for item in items:
            self.assertIn(item, other)

    def test_dedicated_route_items_are_dropped_by_axis_not_wording(self):
        blink = "修正女性主體左眼閉合表情，將眼睛自然睜開並保持身份特徵。"
        strands = "Tuck the loose strands at the crown back into the part."
        aliased = "Redirect the subject's eyes toward the lens."
        other = self._other_corrections(
            [blink, strands, aliased], [["expression"], ["hair_edge"], ["eye_contact"]],
        )
        for item in (blink, strands, aliased):
            self.assertNotIn(item, other)

    def test_untagged_legacy_items_still_fall_back_to_wording(self):
        hair = "將頭頂飛翹髮絲順回原位。"
        gaze = "Redirect the woman's gaze to the camera."
        other = self._other_corrections([hair, gaze], [])
        self.assertNotIn(hair, other)
        self.assertNotIn(gaze, other)


class UserDirectionPriority(unittest.TestCase):
    """The reviewed user direction outranks the rest of the prompt."""

    REVIEWED = "使用者希望加入文字與圖示，但此要求需受不得新增文字、標誌或物件的保真規則限制。"

    def _block(self, user_direction):
        prompt, _ = build_compact_editor_prompt(
            {"subjects": [{"index": 0, "location": "center"}]}, "", user_direction, {},
        )
        return prompt.split("User direction:", 1)[1].split("Global preservation:", 1)[0].strip()

    def test_a_reviewed_direction_gets_the_priority_line(self):
        block = self._block(self.REVIEWED)
        self.assertIn(self.REVIEWED, block)
        self.assertIn("highest priority", block)

    def test_a_direction_carries_its_scene_through(self):
        # 「改成白天」 must also bring shadows and matching light on the people.
        self.assertIn(USER_SCENE_CONSISTENCY_RULE, self._block("將夜間街景改成白天"))

    def test_no_direction_claims_no_priority(self):
        block = self._block("")
        self.assertEqual(block, "No additional direction.")
        self.assertNotIn(USER_SCENE_CONSISTENCY_RULE, block)


if __name__ == "__main__":
    unittest.main()
