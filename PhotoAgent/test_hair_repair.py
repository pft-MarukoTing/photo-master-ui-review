"""Tests for the hair-tidying half of the edit prompt and the `hair` axis.

Imports only prompts.py, so it runs without PIL/numpy/cv2:

    python3 -m unittest test_hair_repair -v
"""

import re
import unittest

from photo_agent.param_validation import normalize_advanced_options
from prompts import (
    AXIS_DEFINITIONS,
    HAIR_MESS_DETECTION_DISABLED_OVERRIDE,
    build_analysis_instruction,
    build_edit_prompt,
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


def _prompt(hair_issues):
    analysis = {**ANALYSIS, "hair_issues": hair_issues}
    return build_edit_prompt(analysis, PRESET, "自然風格", "", CROP_PLAN)


class HairBlockGating(unittest.TestCase):
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
        # negative_block claims to override everything above it, so it has to stay
        # below the blocks that argue for changes -- including the hair one.
        analysis = {
            **ANALYSIS, "hair_issues": [CLUMPED],
            "correction_plan": ["提升整體曝光"], "correction_axes": [["exposure"]],
            "rejected_corrections": ["淡化背景"], "rejected_axes": [["distraction"]],
            "rejected_preserve": ["維持背景原樣"],
        }
        prompt = build_edit_prompt(analysis, PRESET, "自然風格", "", CROP_PLAN)
        self.assertLess(prompt.index("Hair tidying priority"), prompt.index("User-rejected corrections"))


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
            "hair_issues": [FIX_ME],
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
    """The analysis-side rules that decide whether a hair item gets written."""

    def setUp(self):
        self.instruction = build_analysis_instruction("auto")

    def test_hair_states_in_schema_match_the_ones_the_rules_assign(self):
        # A state named in a rule but missing from the enum is a state the model
        # cannot actually return, so the rule silently does nothing.
        for state in (
            "styled_messy", "styled_bangs", "styled_face_framing", "motion",
            "natural_texture", "strands_across_face", "bangs_disturbed",
            "lock_out_of_place", "frizz_halo", "tuft_or_flattened",
            "occluded_or_unclear",
        ):
            with self.subTest(state=state):
                self.assertIn(state, self.instruction)

    def test_fade_wording_is_banned_and_named_in_the_users_own_language(self):
        self.assertIn("Never 淡化, 降低對比, 柔化, or 模糊 hair", self.instruction)

    def test_hair_is_routed_off_the_distraction_axis(self):
        # The distraction toolkit is what produces "淡化某處的頭髮" in the first
        # place, so the routing rule matters as much as the wording ban.
        self.assertIn("Stray hair is never a distraction item", self.instruction)

    def test_wind_is_separated_from_the_persons_own_motion(self):
        # These look alike -- hair off the head, one direction -- but one is the
        # moment and the other is the flaw, so the body decides which.
        self.assertIn("this is about what the person is doing, not about wind", self.instruction)
        self.assertIn("on a body that is otherwise posed or still", self.instruction)

    def test_windblown_work_covers_the_whole_disturbed_area(self):
        self.assertIn("Read wind as a flaw across the whole area it disturbed, not only where it landed on a face", self.instruction)
        self.assertIn("Treat the whole disturbed area as the work", self.instruction)

    def test_settle_names_its_reference(self):
        # Region work with no stated reference is what invents a hairstyle; the
        # undisturbed hair on the same head is the only safe target.
        self.assertIn("settle for a windblown area", self.instruction)
        self.assertIn("the part of the same head the wind did not disturb", self.instruction)
        self.assertIn(">= 0.85", self.instruction)

    def test_remove_can_never_target_forehead_hair(self):
        # A live run labelled a whole clumped fringe "strands_across_face", chose
        # remove, and wrote "移除前額覆蓋眼部的碎髮" -- the edit model obeyed and
        # erased the fringe. remove was bounded by WHERE hair lies, and a fringe
        # on a forehead fits that description exactly, so the bound has to be
        # what the hair IS. Classification stays fragile at 768px; this does not
        # depend on it.
        self.assertIn("remove is only ever for hair that has LEFT the hairstyle", self.instruction)
        # Anchored on where the hair sits, not on the front_hair reading: a
        # 10-run sample of one photo split that reading four ways, so it cannot
        # carry a rule. Position is unambiguous, and it keeps remove available
        # for strands that really have left the hairstyle onto a cheek.
        self.assertIn("Hair lying on the forehead, along the hairline, or at the temples", self.instruction)
        self.assertIn("never remove, whatever state you assigned it", self.instruction)
        self.assertIn("Strands resting further down", self.instruction)

    def test_the_fringe_is_read_as_a_shape_not_by_its_tips(self):
        # The same run's evidence was "幾根碎髮橫跨於右側眉眼附近": it looked at
        # where the fringe ENDED, not at the fringe.
        self.assertIn("Judge the front of the hair as one shape, not by where its tips happen to fall", self.instruction)

    def test_the_reading_cannot_be_pasted_into_the_state(self):
        # try7/image 3 returned hair_state="uneven_clumps" -- a fringe_reading
        # value, not a legal state -- because the two fields share a vocabulary.
        self.assertIn("It is a reading, not a state: never copy its value into hair_state", self.instruction)

    def test_an_uneven_reading_forces_the_fringe_path(self):
        # try7 read uneven_clumps on four photos and still labelled every one
        # strands_across_face, so the consequence has to be spelled out with the
        # wrong answer named.
        self.assertIn('front_hair="clumped" means hair_state="bangs_clumped" and fix_action="redistribute"', self.instruction)
        self.assertIn("not strands_across_face, whatever the strands look like individually", self.instruction)

    def test_vague_item_wording_is_banned_by_name(self):
        # Every try7 hair item was written as 修飾/整理/重組/順服/均勻連續 -- an
        # intention with nothing to render, so the edit came back unchanged.
        for word in ("修飾", "整理", "調整", "重組", "精修", "順服", "自然分布", "均勻連續"):
            with self.subTest(word=word):
                self.assertIn(word, self.instruction)
        self.assertIn("Write that item as the finished picture, in words that can be rendered", self.instruction)

    def test_the_item_must_name_both_halves_in_plain_words(self):
        self.assertIn("去油", self.instruction)
        self.assertIn("梳整齊", self.instruction)
        self.assertIn("Worked example of a whole item", self.instruction)

    def test_fringe_reading_is_a_required_field_not_just_an_instruction(self):
        # Two runs ignored the instruction to check the fringe. A required output
        # field has to be answered for the JSON to be well-formed.
        self.assertIn('"front_hair"', self.instruction)
        self.assertIn("required for anyone with hair in front of the face", self.instruction)

    def test_the_fringe_is_inspected_not_merely_classifiable(self):
        # Two live runs logged this photo's clumped fringe as "少數髮絲分布在額頭"
        # and moved on: the model saw the hair but never asked the fringe question,
        # because nothing made it. Criteria the model has to reach for on its own
        # do not fire; an ordered check placed before state selection does.
        self.assertIn("Front-hair check -- do this for every person with hair falling over the forehead", self.instruction)
        self.assertIn("before choosing any state for them", self.instruction)
        self.assertIn('front_hair="even" is not a flaw by itself', self.instruction)

    def test_stray_strands_cannot_swallow_a_fringe(self):
        # strands_across_face matches anything hair-shaped on a face, fringe
        # included, and is listed first -- it needs an explicit boundary.
        self.assertIn("Hair that still belongs to the fringe, lying where the fringe lies, is a fringe state", self.instruction)

    def test_coarse_clumping_cues_come_before_the_unresolvable_ones(self):
        # The analyzer sees a 768x1024 preview, so root separation and specular
        # streaks are near-unreadable; the two cues it can actually judge have to
        # be enough on their own to reach the threshold.
        cues = self.instruction[self.instruction.index("Confirm it with any two of these"):]
        self.assertLess(cues.index("differ visibly in thickness"), cues.index("separates at the root"))
        self.assertLess(cues.index("the scene explains it"), cues.index("hard specular streak"))

    def test_one_person_may_have_several_hair_problems(self):
        # The old one-item cap meant the first state the model picked used up the
        # person's only slot, so a fringe problem could never be added afterwards.
        self.assertIn("one correction_plan item per distinct hair problem", self.instruction)

    def test_oily_sheen_is_the_one_tonal_instruction_allowed_on_hair(self):
        # It has to be carved out explicitly, or the blanket 淡化/模糊 ban below
        # reads as forbidding the only correct treatment for the shine itself.
        self.assertIn("The one tonal instruction that is right on hair is the oily sheen", self.instruction)
        self.assertIn("recovering texture in a blown highlight, not fading hair away", self.instruction)

    def test_hair_on_a_feature_is_still_fixed(self):
        # Hair over the eyes/nose/mouth is the case users most want fixed. Being on
        # a feature bounds HOW it is written, never whether it is written.
        self.assertIn("Hair over an eye, nose, or mouth is the case users most want fixed", self.instruction)
        self.assertIn("has to come from the original photograph", self.instruction)

    def test_only_a_fully_buried_feature_is_declined(self):
        # The one genuinely impossible case: nothing visible either side to
        # continue from, so there is no original left to reveal.
        self.assertIn("thick enough to bury a whole feature", self.instruction)

    def test_forehead_lock_test_keeps_both_verdicts_and_the_tie_breaker(self):
        self.assertIn("Forehead-or-cheek lock test", self.instruction)
        self.assertIn("Deliberate:", self.instruction)
        self.assertIn("Fallen:", self.instruction)
        # The tie-break is the whole point of a two-of-four rule; without it the
        # model resolves an even split by fixing.
        self.assertIn("one each way means should_fix=false", self.instruction)


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
    """The 頭髮自然放下 switch: a user-elected transformation, not a defect repair."""

    def _prompt(self, calm, **overrides):
        analysis = {**ANALYSIS, "_advanced_options": {"calm_hair": calm}, **overrides}
        return build_edit_prompt(analysis, PRESET, "自然風格", "", CROP_PLAN)

    def test_off_by_default(self):
        self.assertEqual(normalize_advanced_options(None)["calm_hair"], False)
        self.assertNotIn("Still-air hair", self._prompt(False))
        self.assertNotIn("Still-air hair", build_edit_prompt(ANALYSIS, PRESET, "自然風格", "", CROP_PLAN))

    def test_on_reaches_the_edit_prompt(self):
        self.assertIn("Still-air hair (user-selected, mandatory)", self._prompt(True))

    def test_it_does_not_wait_for_the_analysis_to_find_a_defect(self):
        # The point of the switch: the user asked for it, so no hair_issues entry
        # and no confidence threshold gates it.
        prompt = self._prompt(True, hair_issues=[])
        self.assertIn("Still-air hair", prompt)
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
        self.assertIn("This settles hair only; it does not calm the scene", self._prompt(True))

    def test_a_moving_body_keeps_its_moving_hair(self):
        # Hair flat against a mid-jump body is physically incoherent, so the
        # analysis override carves motion out even with the switch on.
        instruction = build_analysis_instruction("auto", {"calm_hair": True})
        self.assertIn("This does not extend to hair moving because the person is moving", instruction)

    def test_items_are_written_as_a_finished_state(self):
        # The two real sentences from the controlled pair, as the contrast example.
        i = build_analysis_instruction("auto")
        self.assertIn("Write it as the state that hair ends in", i)
        self.assertIn("nothing to render towards", i)

    def test_the_analysis_still_routes_it_through_the_reviewable_recipe(self):
        # It goes into correction_plan like everything else, so the review screen
        # can uncheck it and QA can score it.
        instruction = build_analysis_instruction("auto", {"calm_hair": True})
        self.assertIn("Write the matching correction_plan item", instruction)
        self.assertIn('correction_axes ["hair"]', instruction)


class AdvancedSettingsToggle(unittest.TestCase):
    """The upload page's 人像進階設定 hair switch, as it reaches the analysis."""

    FLAW_STATES = {
        "strands_across_face", "lock_out_of_place", "tuft_or_flattened",
        "frizz_halo", "windblown", "bangs_clumped", "bangs_disturbed",
    }

    def test_default_is_on(self):
        self.assertNotIn("turned off by the user", build_analysis_instruction("auto"))
        self.assertNotIn("turned off by the user", build_analysis_instruction("auto", {}))

    def test_turning_it_off_silences_the_hair_repair_only(self):
        off = build_analysis_instruction("auto", {"hair_mess_detection": False})
        self.assertIn("Hair tidying has been turned off", off)
        # The eye feature shares the advanced_options dict and must be untouched.
        self.assertNotIn("Closed-eye / accidental-blink correction has been turned off", off)

    def test_the_analysis_still_records_what_it_saw(self):
        # Off means "do not act on it", not "do not look": the report still shows
        # the judgement, which is what makes the switch debuggable.
        off = build_analysis_instruction("auto", {"hair_mess_detection": False})
        self.assertIn("Still inspect and fill in hair_issues as usual, front_hair included", off)

    def test_every_flaw_state_is_named_by_the_override(self):
        # The override lists states by name, so one added later stays ON with the
        # switch off unless it is added here too. This is that tripwire.
        for state in self.FLAW_STATES:
            with self.subTest(state=state):
                self.assertIn(state, HAIR_MESS_DETECTION_DISABLED_OVERRIDE)

    def test_the_schema_declares_no_flaw_state_the_override_forgot(self):
        instruction = build_analysis_instruction("auto")
        declared = set(re.search(r'"hair_state": "([^"]+)"', instruction).group(1).split(" / "))
        ungoverned = declared - self.FLAW_STATES - {
            "tidy", "uncertain", "occluded_or_unclear", "styled_messy",
            "styled_bangs", "styled_face_framing", "motion", "natural_texture",
        }
        self.assertEqual(ungoverned, set(), f"hair_state(s) the switch cannot turn off: {ungoverned}")

    def test_the_hair_toggle_survives_normalization(self):
        self.assertEqual(
            normalize_advanced_options('{"hair_mess_detection": false}'),
            {
                "eye_closed_detection": True,
                "gaze_contact_detection": True,
                "hair_mess_detection": False,
                "calm_hair": False,
            },
        )

    def test_a_non_boolean_toggle_is_rejected(self):
        with self.assertRaises(RuntimeError):
            normalize_advanced_options('{"hair_mess_detection": "no"}')


if __name__ == "__main__":
    unittest.main()
