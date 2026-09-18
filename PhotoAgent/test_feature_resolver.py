import unittest

from config import STYLE_OPTION_INSTRUCTIONS
from photo_agent.feature_resolver import (
    _compact_raw_crop_instruction,
    _compact_raw_style_instruction,
    build_compact_editor_prompt,
    build_feature_contracts,
    resolve_feature_contracts,
)
from prompts import (
    _BLINK_DETECTION_LUNA_SECTION,
    build_body_reshape_edit_block,
    build_edit_prompt,
    build_eye_repair_planning_instruction,
    build_quality_check_prompt,
)


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
        }],
        "correction_plan": ["Reopen the left man's eyes."],
    }


def body_reshape_analysis():
    return {
        "_advanced_options": {},
        "subjects": [{
            "label": "central woman",
            "box": {"x": .2, "y": .1, "w": .5, "h": .8},
            "is_person": True,
        }],
        "body_reshape": {
            "edit_prompt": "自然修飾整體身形比例並保持真實協調",
        },
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

    def test_explicit_smile_level_does_not_force_an_unreadable_face(self):
        analysis = interaction_analysis()
        analysis["subjects"][0]["smile_issue"]["smile_intensity"] = "unclear"
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


class BodyReshapePromptRouting(unittest.TestCase):
    def test_compact_editor_prompt_routes_one_holistic_prompt_verbatim(self):
        analysis = body_reshape_analysis()
        shared_block = build_body_reshape_edit_block(analysis)
        prompt, metadata = build_compact_editor_prompt(analysis, "", "", {})

        self.assertTrue(shared_block)
        self.assertIn(shared_block, prompt)
        self.assertEqual(
            prompt.count("自然修飾整體身形比例並保持真實協調"),
            1,
        )
        self.assertNotIn("key_points", prompt)
        self.assertNotIn("target_pct", prompt)
        self.assertEqual(metadata["body_reshape_edit_prompt"], "自然修飾整體身形比例並保持真實協調")


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
        self.assertIn("into a natural open resting state", prompt)
        self.assertIn("source closed-lid bulge as transient pose geometry, not final anatomy", prompt)
        self.assertIn("Do not cut an opening into that bulge or lift it as one mass", prompt)
        self.assertIn("folding the broad closed-lid panel into the person's existing lid structure", prompt)
        self.assertIn("do not copy either captured aperture, lid-margin shape, or transient fold width", prompt)
        self.assertIn("neither eye puffy or visibly smaller", prompt)

    def test_full_closed_and_half_closed_pair_uses_strong_repair(self):
        analysis = blink_analysis()
        analysis["subjects"][0]["blink_issue"].update({
            "left_eye_state": "fully_closed",
            "right_eye_state": "half_closed",
            "blink_repair_strategy": blink_strategy(),
        })
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        self.assertIn("Apply a strong eyelid correction", prompt)
        self.assertIn("Both source eyes are blink-affected; neither is an open-eye reference", prompt)
        self.assertIn("Correct both together to one coherent resting-open eyelid shape", prompt)
        self.assertIn("not the apparent fold width or crease-to-lash distance of a half-closed eye", prompt)
        self.assertIn("comparable modest visible crease-to-lash bands", prompt)
        self.assertIn("do not make the formerly closed eye an inner double lid", prompt)
        self.assertNotIn("Target eyelid state: open, matching the left man's existing expression", prompt)
        self.assertNotIn("Eyelid method: Lift the upper lids", prompt)
        self.assertTrue(audit["resolved_features"][0]["final_state"]["paired_blink_repair"])

    def test_closed_and_narrowed_open_pair_gets_two_part_eye_target(self):
        analysis = blink_analysis()
        analysis["subjects"][0]["blink_issue"].update({
            "left_eye_state": "fully_closed",
            "right_eye_state": "narrowed_open",
            "blink_repair_strategy": blink_strategy(),
        })
        prompt, audit = build_compact_editor_prompt(analysis, "", "", {})
        legacy_prompt = build_edit_prompt(analysis, {}, "", "", {})
        qa_prompt = build_quality_check_prompt(analysis)

        self.assertTrue(audit["resolved_features"][0]["final_state"]["paired_blink_repair"])
        self.assertIn("Apply a strong eyelid correction", prompt)
        self.assertIn("eye-opening height and iris exposure, AND comparable modest visible crease-to-lash bands", prompt)
        self.assertIn("Resolve a blink-compressed thick lid roll into a soft crease", prompt)
        self.assertNotIn("Eyelid method: Lift the upper lids", prompt)
        self.assertIn("narrowed-open eye's apparent crease width", legacy_prompt)
        self.assertIn("iris exposure separately from the visible crease-to-lash bands", legacy_prompt)
        self.assertIn("Check two independent outcomes at normal viewing size", qa_prompt)

    def test_legacy_editor_and_qa_reject_blink_induced_crease_width_mismatch(self):
        analysis = blink_analysis()
        analysis["subjects"][0]["blink_issue"].update({
            "left_eye_state": "fully_closed",
            "right_eye_state": "half_closed",
            "blink_repair_strategy": blink_strategy(),
        })
        prompt = build_edit_prompt(analysis, {}, "", "", {})
        qa_prompt = build_quality_check_prompt(analysis)

        self.assertIn("Do not copy the half-closed eye's or narrowed-open eye's apparent crease width", prompt)
        self.assertIn("give both reopened eyes comparable visible double-lid bands", prompt)
        self.assertIn("Preserve genuine stable left/right crease differences", prompt)
        self.assertNotIn("Lift the upper lids to the captured lash line", prompt)
        self.assertIn("A formerly closed eye whose crease disappears against the lash line", qa_prompt)

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
        self.assertEqual(
            prompt.count("Shared blink-repair anatomy rule for every named blink target"),
            1,
        )
        self.assertEqual(prompt.count("source closed-lid bulge as transient pose geometry"), 1)
        self.assertEqual(prompt.count("Do not cut an opening into that bulge or lift it as one mass"), 1)

    def test_no_strategy_keeps_the_generic_sentence(self):
        prompt, _ = build_compact_editor_prompt(blink_analysis(), "", "", {})
        self.assertIn(
            "- Open both eyes clearly and comfortably from the captured blink into a natural open resting state.",
            prompt,
        )
        self.assertIn("Finish both eyes naturally open", prompt)
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
        self.assertIn("Shape the eyelids for the final expression first", prompt)
        self.assertIn("Eyelid method: Lift the upper lids to the captured lash line", prompt)
        self.assertIn("Finish both eyes naturally open", prompt)
        self.assertNotIn("Target eyelid state:", prompt)

    def test_analyzer_treats_closed_half_closed_difference_as_capture_state(self):
        self.assertIn("mismatched transitional eyelid states", _BLINK_DETECTION_LUNA_SECTION)
        self.assertIn("not to this captured difference in eyelid openness", _BLINK_DETECTION_LUNA_SECTION)

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
        self.assertIn("source closed-lid bulge as transient pose geometry", instruction)
        self.assertIn("Do not cut an opening into that bulge or lift it as one mass", instruction)
        self.assertIn('Never call an eye "already open" unless its detected state is open', instruction)
        self.assertIn("a fully-closed or half-closed eye paired with a fully-closed, half-closed, or narrowed-open eye", instruction)
        self.assertIn("closely matched upper-lid curvature, peak position, and inner/outer taper", instruction)
        self.assertIn("Match eye-opening height and iris exposure separately from the visible crease-to-lash bands", instruction)
        self.assertIn("neither eye puffy or visibly smaller", instruction)
        self.assertIn("Use strong repair strength when either source eye is fully closed", instruction)
        self.assertIn("Preserve canthi, eye-globe size, brow position, and source-supported crease type", instruction)
        self.assertIn("its apparent width and crease-to-lash distance are distorted by the blink", instruction)
        self.assertIn("comparable visible crease-to-lash bands after opening", instruction)
        self.assertIn("Preserve a genuine one-monolid/one-double-lid pattern", instruction)

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
        self.assertIn("Reframe to 4:3", compact)
        self.assertIn("moderate tightening", compact)
        self.assertIn("Trim side content", compact)
        self.assertIn("Come in visibly closer", compact)
        self.assertIn("Preserve internal proportions, tilt, relative sizes, spacing, and overlap", compact)
        self.assertLess(len(compact), len(raw))

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
        self.assertIn("head/face width-to-length ratio", prompt)
        self.assertIn("skeletal jaw/chin contour, cheekbone placement", prompt)
        self.assertIn("baseline eye–nose–mouth landmark layout", prompt)
        self.assertIn("eye-globe size and interocular/canthus positions", prompt)
        self.assertIn("Preserve stable, source-supported upper-eyelid differences", prompt)
        self.assertIn("including a genuine one-monolid/one-double-lid pattern", prompt)
        self.assertIn("never create a new fold", prompt)
        self.assertIn("nose bridge/tip/nostril geometry, philtrum length, ear shape", prompt)
        self.assertIn("limit expression movement to mouth-corner position and opening", prompt)
        self.assertIn("eyelid aperture over the fixed landmarks above", prompt)
        self.assertIn("using the captured mouth center, lip contour/volume", prompt)
        self.assertIn("visible dental characteristics rather than regenerating or beautifying the whole face", prompt)
        self.assertIn("original skin age and fold pattern", prompt)
        self.assertIn("crow's-feet, under-eye creases, and nasolabial folds", prompt)
        self.assertIn("Preserve lashes/brows, makeup", prompt)
        self.assertIn("Use a subtle eyes-only correction and keep the original head/face angle fixed", prompt)
        self.assertIn("gaze changes iris/pupil direction only", prompt)
        self.assertIn("avoid both fixed-open staring and exaggerated squinting", prompt)
        self.assertIn("narrow the eyelids modestly through natural cheek lift", prompt)
        self.assertIn("recognizable eye openness, and iris visibility", prompt)
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
        self.assertIn("Shape the eyelids for the final expression first", prompt)
        self.assertIn("naturally open both eyes within that expression", prompt)
        self.assertIn("Finish both eyes naturally open", prompt)
        self.assertIn("neither eye puffy or visibly smaller", prompt)
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

    def test_no_direction_claims_no_priority(self):
        block = self._block("")
        self.assertEqual(block, "No additional direction.")


if __name__ == "__main__":
    unittest.main()
