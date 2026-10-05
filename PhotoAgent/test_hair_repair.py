"""Tests for the hair-tidying half of the edit prompt and the `hair` axis.

Imports only prompts.py, so it runs without PIL/numpy/cv2:

    python3 -m unittest test_hair_repair -v
"""

import unittest

from photo_agent.param_validation import normalize_advanced_options
from prompts import (
    AXIS_DEFINITIONS,
    build_edit_prompt,
    build_portrait_qc_instruction,
    derive_hair_decisions,
    resolve_user_hair_request,
    resolve_rejected_axes,
)

PRESET = {"intent": "intent", "label": "label"}
CROP_PLAN = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "aspectRatio": "3:4"}
ANALYSIS = {
    "photo_summary": "海邊的一位女性",
    "main_subject": "女性",
    "subjects": [{"label": "女性", "is_person": True, "prominence": 1}],
    "correction_plan": ["把右臉頰前的三四根碎髮順回原本的髮流"],
    "correction_axes": [["hair"]],
    "retouch_recipe": ["把擋在臉上的幾根碎髮順好。"],
}
FIX_ME = {
    "subject_label": "中間女性",
    "hair_state": "strands_across_face",
    "fix_action": "remove",
    "should_fix": True,
    "reason": "髮絲橫過右眼",
}
CLUMPED = {
    "subject_label": "中間女性",
    "hair_state": "bangs_clumped",
    "fix_action": "redistribute",
    "should_fix": True,
    "reason": "夜間戶外活動，瀏海出油結成粗細不一的髮束，束間露出額頭",
}
SETTLE_ME = {
    "subject_label": "中間女性",
    "hair_state": "windblown",
    "fix_action": "settle",
    "should_fix": True,
    "reason": "站定拍照但頭髮被側風整片吹開，與髮型本身的形狀衝突",
}
LEAVE_ME = {
    "subject_label": "左側女性",
    "hair_state": "motion",
    "fix_action": "none",
    "should_fix": False,
    "reason": "甩髮動作本身就是這張照片的內容",
}


def _subjects_from_hair_issues(hair_issues):
    """hair_issue now lives nested under subjects[] rather than in a
    top-level hair_issues array -- build one subject per legacy flat item,
    carrying its subject_label forward as that subject's own label."""
    return [
        {
            "label": item.get("subject_label") or "unnamed",
            "is_person": True,
            "box": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1},
            "hair_issue": {k: v for k, v in item.items() if k != "subject_label"},
        }
        for item in hair_issues
    ]


def _prompt(hair_issues):
    analysis = {**ANALYSIS, "subjects": _subjects_from_hair_issues(hair_issues)}
    return build_edit_prompt(analysis, PRESET, "自然風格", "", CROP_PLAN)



def _portrait_qc_instruction():
    """Call B's prompt for one person; it carries the hair analysis section."""
    return build_portrait_qc_instruction([{
        "subject_index": 0, "local_detection_index": 0,
        "label": "中間女性", "box": {"x": .2, "y": .1, "w": .3, "h": .6},
    }])

class HairBlockGating(unittest.TestCase):
    def test_manual_motion_cleanup_keeps_the_motion_endpoint(self):
        issue = resolve_user_hair_request({
            **LEAVE_ME, "_user_forced": True, "_user_level": "dynamic",
        })
        self.assertEqual(issue["fix_action"], "lay_back")
        self.assertIn("visible wind or body-motion flow", issue["correction_plan"])
        self.assertIn("preserving its lifted volume and motion", issue["correction_plan"])
        self.assertIn("do not add hair", issue["correction_plan"])

    def test_manual_bangs_cleanup_preserves_the_fringe_design(self):
        issue = resolve_user_hair_request({
            "hair_state": "styled_bangs", "front_hair": "even",
            "should_fix": False, "_user_forced": True, "_user_level": "dynamic",
        })
        self.assertIn("existing front fringe above the eyes", issue["correction_plan"])
        self.assertIn("redistribute those same strands", issue["correction_plan"])
        self.assertIn("roots, and lower endpoints", issue["correction_plan"])
        self.assertIn("do not add hair", issue["correction_plan"])
        self.assertIn("create a new lock", issue["correction_plan"])

    def test_manual_styled_hair_requests_visible_whole_style_cleanup(self):
        issue = resolve_user_hair_request({
            "hair_state": "styled_face_framing", "front_hair": "none",
            "should_fix": False, "_user_forced": True, "_user_level": "dynamic",
        })
        self.assertIn("source-visible front and face-framing strands", issue["correction_plan"])
        self.assertIn("exact number, roots, endpoints, length, and curve", issue["correction_plan"])
        self.assertIn("Leave crown, side lengths, and rear hair unchanged", issue["correction_plan"])
        self.assertIn("Keep the head, face, eyelids, expression, and skin unchanged", issue["correction_plan"])

    def test_manual_tidy_level_has_a_renderable_still_hair_endpoint(self):
        issue = resolve_user_hair_request({
            **LEAVE_ME, "_user_forced": True, "_user_level": "tidy",
        })
        self.assertEqual(issue["fix_action"], "settle")
        self.assertIn("existing hairstyle into visibly neater versions", issue["correction_plan"])
        self.assertIn("do not add hair, create a new lock", issue["correction_plan"])
        self.assertIn("head pose, face, eyelids, expression, or skin", issue["correction_plan"])

    def test_manual_cleanup_reaches_the_legacy_edit_prompt(self):
        manual = {
            **LEAVE_ME, "_user_forced": True, "_user_level": "dynamic",
        }
        prompt = _prompt([manual])
        self.assertIn("Hair tidying priority", prompt)
        self.assertIn("preserving its lifted volume and motion", prompt)

    def test_no_hair_issues_means_no_hair_block(self):
        self.assertNotIn("Hair tidying priority", _prompt([]))

    def test_should_fix_false_alone_means_no_hair_block(self):
        # An analysis that recorded "this mess is intentional, leaving it" must
        # not itself turn into a licence to touch hair.
        self.assertNotIn("Hair tidying priority", _prompt([LEAVE_ME]))

    def test_should_fix_true_adds_the_block(self):
        prompt = _prompt([LEAVE_ME, FIX_ME])
        self.assertIn("Hair tidying priority", prompt)
        self.assertIn("中間女性", prompt)

    def test_leave_alone_people_are_not_named_to_the_edit_model(self):
        # Only the should_fix items travel: naming the moving-hair person at all
        # puts her hair in front of a model that tends to act on what it is shown.
        prompt = _prompt([LEAVE_ME, FIX_ME])
        self.assertNotIn("左側女性", prompt)
        self.assertNotIn('"hair_state": "motion"', prompt)

    def test_the_bounds_reach_the_prompt(self):
        prompt = _prompt([FIX_ME])
        for bound in ("parting", "hairline position", "curl pattern",
                      "The amount of hair never increases anywhere"):
            self.assertIn(bound, prompt)

    def test_the_work_is_bounded_to_the_strands_own_footprint(self):
        # The identity anchor: a strand is a thin line, and the feature it crosses
        # is visible around it, so the visible part dictates the sliver underneath.
        # Losing this is what let "rebuild what was behind them" become a repainted
        # eye, and a repainted eye is a different face.
        prompt = _prompt([FIX_ME])
        self.assertIn("change nothing outside its own footprint", prompt)
        self.assertIn("stays pixel-faithful", prompt)
        self.assertIn("Do not re-render the eye, nose, or mouth region", prompt)

    def test_settle_is_the_only_action_allowed_past_a_strands_footprint(self):
        prompt = _prompt([SETTLE_ME])
        self.assertIn("this is the only place that wider scope is licensed", prompt)
        # The footprint rule must survive as the rule for the other two actions,
        # or widening the scope for wind quietly widens it for everything.
        self.assertIn("For lay_back and remove, a strand is a thin line", prompt)

    def test_settle_is_guarded_against_the_helmet(self):
        # Region-scale smoothing makes a glossy mass the cheapest way to look
        # tidy, so this is the failure mode most worth naming.
        prompt = _prompt([SETTLE_ME])
        self.assertIn("Settled is not sculpted", prompt)
        self.assertIn("no more length, volume, or coverage than the rest of the head has", prompt)

    def test_the_editor_refuses_to_uncover_a_forehead(self):
        # Second line of defence: even handed a remove item aimed at fringe hair,
        # the edit must not end with more forehead showing than it started with.
        prompt = _prompt([FIX_ME])
        self.assertIn("the hairline stays exactly where it is", prompt)
        self.assertIn("no more forehead is visible at the end than at the start", prompt)
        self.assertIn("never an instruction to remove the fringe", prompt)

    def test_closing_a_gap_is_not_read_as_adding_hair(self):
        # try7/image 10 left the fringe's bald gaps untouched. The block said both
        # "close the gaps with the hair that is there" and "add no hair to fill a
        # gap" -- and covering forehead skin necessarily renders hair where skin
        # was, so the prohibition swallowed the instruction.
        prompt = _prompt([CLUMPED])
        self.assertIn('Combing hair across a gap is the operation, not a violation of "add no hair"', prompt)
        self.assertIn("The same hair, spread evenly, covers more area than the same hair bunched into cords", prompt)
        self.assertNotIn("Add no hair to fill a gap", prompt)

    def test_redistribute_names_both_halves_of_the_job(self):
        prompt = _prompt([CLUMPED])
        self.assertIn("First, de-grease it", prompt)
        self.assertIn("Second, comb it out", prompt)

    def test_analysis_uses_recall_first_clumped_fringe_geometry(self):
        instruction = _portrait_qc_instruction()
        self.assertIn("inspect its left, center, and right zones", instruction)
        self.assertIn("One such local contradiction is sufficient", instruction)
        self.assertIn("primarily a distribution-geometry decision, not an oil-detection decision", instruction)
        self.assertIn("Hair recall check", instruction)
        self.assertIn('front_hair="clumped" plus hair_state="bangs_clumped"', instruction)
        self.assertIn("Do not require visible oil, multiple defective zones, or whole-fringe disorder", instruction)

    def test_redistribute_keeps_the_fringe_see_through(self):
        # Closing the gaps is the job; closing the fringe is the failure. Real
        # bangs show skin through them, and an opaque one reads as a wig.
        prompt = _prompt([CLUMPED])
        self.assertIn("A fringe comes out as one even band, whichever action was named for it", prompt)
        # try9 produced the defect the other way round: a remove item thinned the
        # fringe into cords with bare skin between them. The guarantee was worded
        # for redistribute only, so a remove item never picked it up.
        self.assertIn("builds that same pattern from the other direction", prompt)
        self.assertIn("never the fringe's own transparency", prompt)
        self.assertIn("never by darkening, flattening, or blurring the fringe", prompt)

    def test_each_action_is_defined_as_the_state_the_hair_ends_in(self):
        # A controlled pair -- same photo, crop, output size, analysis and hair
        # item, differing only by a user_prompt -- came back unfixed, then fixed.
        # The instruction that worked described a finished look; the automatic one
        # named hair to take away, inside a block that is otherwise all limits, so
        # nothing in the prompt said what to render.
        prompt = _prompt([CLUMPED])
        self.assertIn("Each is a picture of how that hair ends up", prompt)
        self.assertIn("none of it is the goal", prompt)

    def test_removal_is_all_or_nothing(self):
        # 淡化 is what the analysis model reaches for on its own, and a faded
        # strand leaves a grey trace on skin that reads worse than the strand.
        prompt = _prompt([FIX_ME])
        self.assertIn("Do not fade, blur, soften, desaturate, or half-erase hair", prompt)
        self.assertIn("continues unbroken", prompt)

    def test_the_exit_door_is_stated_as_correct_not_as_failure(self):
        prompt = _prompt([FIX_ME])
        self.assertIn("leave it exactly as captured -- that is the correct outcome here", prompt)


class BlockOrder(unittest.TestCase):
    def test_the_hair_block_sits_with_the_plan_it_elaborates(self):
        # try9.1: correct analysis, a plan naming the finished state, the block
        # present in the prompt -- and the hair came back untouched. Content and
        # classification were no longer the variable; weight was. The hair block
        # was 11th of 15, while the one instruction that ever worked arrived in
        # user_block at 6th.
        prompt = _prompt([CLUMPED])
        plan = prompt.index("User-approved correction plan")
        hair = prompt.index("Hair tidying priority")
        rejected_or_skin = prompt.index("Skin in the finished photograph")
        self.assertLess(plan, hair)
        self.assertLess(hair, rejected_or_skin)

    def test_rejected_corrections_still_override_everything_above_them(self):
        # The reviewed source-state choices have priority over analyzer-derived
        # blocks, so they stay below the blocks that may still argue for changes.
        analysis = {
            **ANALYSIS, "subjects": _subjects_from_hair_issues([CLUMPED]),
            "correction_plan": ["提升整體曝光"], "correction_axes": [["exposure"]],
            "rejected_corrections": ["淡化背景"], "rejected_axes": [["distraction"]],
            "rejected_preserve": ["維持背景原樣"],
        }
        prompt = build_edit_prompt(analysis, PRESET, "自然風格", "", CROP_PLAN)
        self.assertLess(
            prompt.index("Hair tidying priority"),
            prompt.index("User-preserved choices (highest priority for these scopes)"),
        )


class UncheckedHairItem(unittest.TestCase):
    """hair_issues is analysis output, so should_fix stays true after the user
    unchecks the hair item on the review screen. The block has to read the recipe
    choice too, or the prompt asks for the hair it was just told to leave alone."""

    def _rejected(self, **overrides):
        analysis = {
            **ANALYSIS,
            "correction_plan": ["提升整體曝光 0.3 EV"],
            "correction_axes": [["exposure"]],
            "rejected_corrections": ["清掉黏在左臉頰上的兩三根碎髮"],
            "rejected_axes": [["hair"]],
            "rejected_preserve": ["維持左臉頰上原本的碎髮，完全照原圖呈現"],
            "subjects": _subjects_from_hair_issues([FIX_ME]),
            **overrides,
        }
        return build_edit_prompt(analysis, PRESET, "自然風格", "", CROP_PLAN)

    def test_unchecking_it_drops_the_block(self):
        prompt = self._rejected()
        self.assertNotIn("Hair tidying priority", prompt)
        self.assertNotIn("strands_across_face", prompt)

    def test_the_preservation_instruction_still_arrives(self):
        # Dropping the block must not also drop the reason the hair is off-limits.
        self.assertIn("維持左臉頰上原本的碎髮", self._rejected())

    def test_a_synonym_tag_on_the_rejected_item_still_suppresses(self):
        self.assertNotIn("Hair tidying priority", self._rejected(rejected_axes=[["flyaways"]]))

    def test_a_kept_hair_item_keeps_the_block(self):
        # Two hair corrections, one unchecked and one kept: the kept one still
        # needs its instructions, so the block stays.
        prompt = self._rejected(
            correction_plan=["提升整體曝光 0.3 EV", "把右側瀏海一撮順回髮流"],
            correction_axes=[["exposure"], ["hair"]],
        )
        self.assertIn("Hair tidying priority", prompt)


class AnalysisInstruction(unittest.TestCase):
    """The model observes hair; Python owns the edit verdict."""

    def setUp(self):
        self.instruction = _portrait_qc_instruction()

    def test_analysis_is_ordered_and_observation_only(self):
        for heading in (
            "### Role", "### Output fields", "### Inspection order",
            "### State and candidate-action routing", "### Action meanings",
            "### Face and hairstyle safety", "### Output invariants",
        ):
            self.assertIn(heading, self.instruction)
        self.assertIn("Always output should_fix=false as a placeholder", self.instruction)
        self.assertIn("Python is the only authority that changes it", self.instruction)

    def test_motion_and_wind_are_physical_states_not_user_preferences(self):
        self.assertIn("use motion when the person's jump, spin, flip, throw, or turn drives the hair", self.instruction)
        self.assertIn("A coherent wind direction confirms windblown", self.instruction)
        self.assertIn("This state does not prevent the user's later fully-groomed transformation", self.instruction)

    def test_general_disorder_is_distinct_from_a_deliberate_messy_style(self):
        self.assertIn("disheveled", self.instruction)
        self.assertIn("several unrelated disruptions", self.instruction)
        self.assertIn("Do not use this state for a consistent tousled style or natural texture", self.instruction)

    def test_candidate_plan_can_exist_while_placeholder_is_false(self):
        self.assertIn("Writing a plan does not set should_fix", self.instruction)
        self.assertIn("provide the routed candidate fix_action/correction_plan. Always leave hair_issue.should_fix=false", self.instruction)

    def test_hair_is_kept_out_of_general_corrections(self):
        self.assertIn("Never add hair work to top-level correction_plan", self.instruction)
        self.assertIn("Stray hair is never a distraction item", self.instruction)

    def test_fringe_and_face_safety_survive_the_compaction(self):
        self.assertIn("it may be blunt, curtain/八字, see-through, or side-swept", self.instruction)
        self.assertIn("A side part or full-length front section", self.instruction)
        self.assertIn("never create a fringe or close forehead gaps", self.instruction)
        self.assertIn("repair only the strand-width footprint", self.instruction)

    def test_single_oily_lock_is_not_misread_as_styled_face_framing(self):
        self.assertIn("Do not use this state for one isolated front lock", self.instruction)
        self.assertIn("Following the general parting direction alone does not prove intentional face framing", self.instruction)
        self.assertIn("one visibly stuck-together coarse front lock", self.instruction)
        self.assertIn("directional agreement does not make it styled", self.instruction)
        self.assertIn('use front_hair="clumped" and bangs_clumped even when that lock follows the parting direction', self.instruction)

    def test_portrait_qc_uses_nested_candidate_plan(self):
        instruction = build_portrait_qc_instruction([{
            "subject_index": 0, "local_detection_index": 0,
            "label": "中間女性", "box": {"x": .2, "y": .1, "w": .3, "h": .6},
        }])
        self.assertIn("Per-person candidate hair correction plan", instruction)
        self.assertIn("Python decides whether that candidate is enabled", instruction)
        self.assertIn("Always leave hair_issue.should_fix=false", instruction)


class HairDecisionDerivation(unittest.TestCase):
    def _derive(self, issue):
        analysis = {"subjects": [{"label": "人物", "is_person": True, "hair_issue": issue}]}
        return derive_hair_decisions(analysis)["subjects"][0]["hair_issue"]

    def test_model_verdict_is_overwritten_for_actionable_state(self):
        issue = self._derive({
            "hair_state": "windblown", "fix_action": "none",
            "should_fix": False, "correction_plan": "整理右側受風區",
        })
        self.assertTrue(issue["should_fix"])
        self.assertEqual(issue["fix_action"], "settle")
        self.assertEqual(issue["correction_plan"], "整理右側受風區")

    def test_model_verdict_is_overwritten_for_non_actionable_state(self):
        issue = self._derive({
            "hair_state": "tidy", "fix_action": "settle",
            "should_fix": True, "correction_plan": "整頭梳整",
        })
        self.assertFalse(issue["should_fix"])
        self.assertEqual(issue["fix_action"], "none")
        self.assertEqual(issue["correction_plan"], "不適用")

    def test_clean_body_motion_is_not_a_local_defect(self):
        issue = self._derive({
            "hair_state": "motion", "fix_action": "settle",
            "should_fix": True, "correction_plan": "整頭梳整",
        })
        self.assertFalse(issue["should_fix"])
        self.assertEqual(issue["fix_action"], "none")

    def test_missing_actionable_plan_gets_deterministic_fallback(self):
        issue = self._derive({
            "hair_state": "strands_across_face", "fix_action": "lay_back",
            "should_fix": False, "correction_plan": "不適用",
        })
        self.assertTrue(issue["should_fix"])
        self.assertIn("落在臉上的髮絲", issue["correction_plan"])

    def test_disheveled_state_is_actionable_without_wind_or_motion(self):
        issue = self._derive({
            "hair_state": "disheveled", "fix_action": "none",
            "should_fix": False, "correction_plan": "不適用",
        })
        self.assertTrue(issue["should_fix"])
        self.assertEqual(issue["fix_action"], "settle")
        self.assertIn("多處失序髮流", issue["correction_plan"])

    def test_clumped_bangs_require_a_confirmed_fringe(self):
        issue = self._derive({
            "hair_state": "bangs_clumped", "front_hair": "none",
            "fix_action": "redistribute", "should_fix": False,
            "correction_plan": "梳散瀏海",
        })
        self.assertFalse(issue["should_fix"])
        self.assertEqual(issue["fix_action"], "none")


class HairAxis(unittest.TestCase):
    def test_axis_exists(self):
        self.assertIn("hair", AXIS_DEFINITIONS)

    def test_unchecking_the_hair_item_rejects_the_axis(self):
        rejected = resolve_rejected_axes({
            "correction_plan": [],
            "correction_axes": [],
            "rejected_corrections": ANALYSIS["correction_plan"],
            "rejected_axes": [["hair"]],
        })
        self.assertEqual(rejected, ["hair"])

    def test_hair_synonyms_do_not_disable_axis_gating(self):
        # An unrecognized axis on a KEPT item makes resolve_rejected_axes give up
        # on gating for the whole edit, so the synonyms the model actually writes
        # have to fold into "hair" instead of being seen as unknown.
        for synonym in ("hairs", "flyaways", "bangs", "frizz", "hairstyle"):
            with self.subTest(synonym=synonym):
                rejected = resolve_rejected_axes({
                    "correction_plan": ["提升整體曝光"],
                    "correction_axes": [[synonym]],
                    "rejected_corrections": ["把碎髮順好"],
                    "rejected_axes": [["hair"]],
                })
                self.assertEqual(rejected, [])


class CalmHair(unittest.TestCase):
    """Legacy prompt coverage for the upload-page hair preference."""

    def _prompt(self, calm, **overrides):
        analysis = {**ANALYSIS, "_advanced_options": {"calm_hair": calm}, **overrides}
        return build_edit_prompt(analysis, PRESET, "自然風格", "", CROP_PLAN)

    def test_off_by_default(self):
        self.assertEqual(normalize_advanced_options(None)["calm_hair"], False)
        self.assertNotIn("Still-air hair", self._prompt(False))
        self.assertNotIn("Still-air hair", build_edit_prompt(ANALYSIS, PRESET, "自然風格", "", CROP_PLAN))

    def test_on_reaches_the_edit_prompt(self):
        prompt = self._prompt(True, subjects=_subjects_from_hair_issues([FIX_ME]))
        self.assertIn("Still-air hair (user-selected, mandatory)", prompt)
        self.assertIn("Mandatory visible change", prompt)
        self.assertIn("entire visible hairstyle", prompt)

    def test_it_still_requires_the_analysis_to_find_a_defect(self):
        prompt = self._prompt(True, subjects=[])
        self.assertNotIn("Still-air hair", prompt)
        self.assertNotIn("Hair tidying priority", prompt)

    def test_unchecking_the_hair_item_still_switches_it_off(self):
        # Otherwise the rejected-corrections block and this one contradict.
        prompt = self._prompt(
            True,
            correction_plan=[], correction_axes=[],
            rejected_corrections=["把被風吹起的頭髮自然放下"], rejected_axes=[["hair"]],
        )
        self.assertNotIn("Still-air hair", prompt)

    def test_the_scene_keeps_its_weather(self):
        # "No wind" applied to the whole frame would rebuild the scene; only hair
        # settles, and the flags and foliage still blow.
        self.assertIn(
            "This settles hair only; it does not calm the scene",
            self._prompt(True, subjects=_subjects_from_hair_issues([FIX_ME])),
        )

    def test_a_moving_body_keeps_its_moving_hair(self):
        # Detection records body-driven motion without making the edit verdict.
        # The user-selected fully-groomed transformation is applied at edit time.
        instruction = _portrait_qc_instruction()
        self.assertIn("use motion when the person's jump, spin, flip, throw, or turn drives the hair", instruction)
        self.assertIn("does not prevent the user's later fully-groomed transformation", instruction)

    def test_items_are_written_as_a_finished_state(self):
        i = _portrait_qc_instruction()
        self.assertIn("name the exact visible target, the operation, and the visible final state", i)
        self.assertIn("The instruction must stand on its own for the editor", i)

    def test_analysis_never_routes_hair_through_the_global_recipe(self):
        instruction = _portrait_qc_instruction()
        self.assertIn("hair_issue.correction_plan", instruction)
        self.assertIn("Never add hair work to top-level correction_plan", instruction)


class AdvancedSettingsToggle(unittest.TestCase):
    """The upload page's 人像進階設定 hair switch.

    No longer reaches the analysis instruction at all -- eye_closed_detection/
    hair_mess_detection/gaze_contact_detection now only gate the edit stage
    (build_edit_prompt's strip_disabled_topic_items), so analysis always runs
    full detection regardless of these three. These tests just cover that the
    toggle itself still normalizes correctly.
    """

    def test_the_hair_toggle_survives_normalization(self):
        self.assertEqual(
            normalize_advanced_options('{"hair_mess_detection": false}'),
            {
                "eye_closed_detection": True,
                "gaze_contact_detection": True,
                "hair_mess_detection": False,
                "calm_hair": False,
                "smile_adjustment": True,
                "smile_level": "auto",
                "editor_prompt_strategy": "resolved_compact",
            },
        )

    def test_a_non_boolean_toggle_is_rejected(self):
        with self.assertRaises(RuntimeError):
            normalize_advanced_options('{"hair_mess_detection": "no"}')


if __name__ == "__main__":
    unittest.main()
