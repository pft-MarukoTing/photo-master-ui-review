"""Fixed identities carry local observations into final editor targets; no network."""
import copy
import unittest
from unittest.mock import patch

import config
from photo_agent.analysis_skills import PortraitAnalysisSkill
from photo_agent.analysis_skills.portrait.instructions import build_portrait_qc_instruction
from tests.test_early_portrait_passes import _Ctx, _person, _EYES_OFF_CAMERA
from tests.test_hair_repair import _compact


class PortraitTextHandoffTest(unittest.TestCase):
    def setUp(self):
        self.skill = PortraitAnalysisSkill()
        self.block = patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden'))
        self.block.start()
        self.addCleanup(self.block.stop)
        flag = patch.object(config, 'ENABLE_BODY_RESHAPE', False)
        flag.start()
        self.addCleanup(flag.stop)

    def reading(self, index=0, detection=1, plan='Remove strands across the photograph-left cheek.'):
        r = copy.deepcopy(_EYES_OFF_CAMERA)
        r.update(subject_index=index, local_detection_index=detection, gaze_issue={}, lens_issue={})
        r['hair_issue'] = dict(hair_state='strands_across_face', fix_action='remove',
            front_hair='none', correction_plan=plan, visible_evidence=['Strands cross the photograph-left cheek.'])
        return r

    def run_handoff(self, readings, old=None, final=None):
        old = old or [_person('pp_a', 1, label='PREVIEW_LABEL', _box_is_head=True)]
        final = final or [_person('pp_a', 1, label='FINAL_LABEL', box={'x': .1, 'y': .05, 'w': .7, 'h': .9})]
        ctx = _Ctx({'person_issues': readings})
        early = self.skill.run_early({'subjects': old}, ctx)
        before = copy.deepcopy(early)
        result, usage = self.skill.adopt_early({'subjects': final}, early, ctx)
        self.assertEqual(early, before)
        self.assertEqual(len(ctx.sent), 1)  # No retry / additional model call.
        self.assertEqual(usage, ['usage_1'])
        return result

    def test_final_editor_uses_call_a_label_and_body_box(self):
        from photo_agent.editor_skills.hair import _hair_contracts
        reading = self.reading()
        result = self.run_handoff([reading])
        contract, = _hair_contracts(result)
        self.assertEqual(contract['subject'], 'FINAL_LABEL')
        self.assertEqual(contract['box'], result['subjects'][0]['box'])
        self.assertEqual(contract['box']['h'], .9)
        self.assertEqual(contract['endpoint']['hair_correction_plan'], reading['hair_issue']['correction_plan'])
        prompt = _compact(result)
        self.assertIn('Subject: FINAL_LABEL', prompt)
        self.assertNotIn('PREVIEW_LABEL', prompt)
        self.assertIn(reading['hair_issue']['correction_plan'], prompt)

    def test_ambiguous_labels_do_not_erase_local_directions(self):
        for label, plan in [('左側', '將照片左側眼睛前的髮絲順回相鄰髮流。'),
                            ('left', 'Remove isolated strands across the photograph-left cheek.'),
                            ('Ann', 'Remove isolated strands from this cheek.')]:
            with self.subTest(label=label):
                reading = self.reading(plan=plan)
                reading['hair_issue']['visible_evidence'] = [plan]
                reading['hair_issue']['reason'] = plan
                result = self.run_handoff([reading], old=[_person('pp_a', 1, label=label)])
                issue = result['subjects'][0]['hair_issue']
                for key in ('correction_plan', 'reason', 'visible_evidence'):
                    self.assertEqual(issue[key], reading['hair_issue'][key])
                self.assertIn(plan, _compact(result))

    def test_reordered_readings_with_overlapping_people_keep_operations(self):
        old = [_person('pp_a', 1, label='PREVIEW_A'), _person('pp_b', 2, label='PREVIEW_B')]
        final = [_person('pp_a', 1, label='FINAL_A'), _person('pp_b', 2, label='FINAL_B')]
        a = self.reading()
        b = self.reading(1, 2, 'Lay the photograph-right temple strand back into the adjacent hair flow.')
        b['hair_issue']['fix_action'] = 'lay_back'
        result = self.run_handoff([b, a], old, final)
        for i, reading in enumerate((a, b)):
            self.assertEqual(result['subjects'][i]['hair_issue']['correction_plan'], reading['hair_issue']['correction_plan'])
            self.assertEqual(result['subjects'][i]['preview_person_id'], final[i]['preview_person_id'])
        prompt = _compact(result)
        for label in ('FINAL_A', 'FINAL_B'):
            self.assertIn('Subject: '+label, prompt)
        self.assertNotIn('PREVIEW_', prompt)

    def test_lens_position_evidence_survives_rename(self):
        reading = self.reading()
        evidence = '臉部位於畫面左上邊緣，外側臉頰沿左上方向延展。'
        reading['lens_issue'] = dict(scene_projection='rectilinear', edge_stretch='slight',
            visible_evidence=evidence, reason=evidence, stretch_confidence=.5, should_fix=False)
        result = self.run_handoff([reading], old=[_person('pp_a', 1, label='左上')])
        for key in ('reason', 'visible_evidence'):
            self.assertEqual(result['subjects'][0]['lens_issue'][key], evidence)

    def test_rename_does_not_rewrite_model_prose(self):
        # No semantic sanitization guarantee: even a repeated label is preserved.
        plan = 'Remove PREVIEW_LABEL cheek strands.'
        result = self.run_handoff([self.reading(plan=plan)])
        self.assertEqual(result['subjects'][0]['hair_issue']['correction_plan'], plan)
        self.assertEqual(result['subjects'][0]['label'], 'FINAL_LABEL')

    def test_identity_mismatch_rejects_early_result(self):
        for overrides in ({'preview_person_id': 'different'}, {'local_detection_index': 9}):
            with self.subTest(overrides=overrides):
                ctx = _Ctx({'person_issues': [self.reading()]})
                early = self.skill.run_early({'subjects': [_person('pp_a', 1)]}, ctx)
                final = {**_person('pp_a', 1), **overrides}
                self.assertIsNone(self.skill.adopt_early({'subjects': [final]}, early, ctx))
                self.assertEqual(len(ctx.sent), 1)

    def test_failed_call_b_retains_no_repair_fallback(self):
        ctx = _Ctx(RuntimeError('Call B unavailable'))
        early = self.skill.run_early({'subjects': [_person('pp_a', 1, label='OLD')]}, ctx)
        result, _ = self.skill.adopt_early({'subjects': [_person('pp_a', 1, label='NEW')]}, early, ctx)
        self.assertFalse(result['subjects'][0]['hair_issue']['should_fix'])
        self.assertEqual(len(ctx.sent), 1)

    def test_prompt_distinguishes_geometry_from_person_identification(self):
        prompt = build_portrait_qc_instruction([dict(_person('pp_a', 1, label='LABEL', _box_is_head=True), subject_index=0)])
        self.assertIn('LABEL -- head location', prompt)
        self.assertIn('final person label and location separately', prompt)
        self.assertIn('explicitly allow position relative to the frame center or edge', prompt)
        self.assertIn('not a second person-identification instruction', prompt)
        self.assertIn('photograph-left/right', prompt)


if __name__ == '__main__':
    unittest.main()
