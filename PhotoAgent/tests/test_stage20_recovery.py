"""Offline Stage 2.0 recovery contract. No model or network calls."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import config
import openai_client as client
from photo_agent import ab_preview as ab
from tests.test_chat_subject_preview import image_bytes, binding

FIX=json.loads((Path(__file__).parent/'fixtures/ab_candidate_subject_selection.json').read_text())
def reply():
    r=copy.deepcopy(FIX['scenes'][0]['reply']);r['additional_people']=[]
    r['detections']={str(d.pop('detection_index')):d for d in r['detections']}
    r['low_confidence_reviews']={d.pop('candidate_id'):d for d in r['low_confidence_reviews']}
    return r
def response(r):
    m=MagicMock();m.json.return_value={'status':'completed','output_text':json.dumps(r),'usage':{'input_tokens':10,'output_tokens':5,'total_tokens':15}}
    return m
class Recovery(unittest.TestCase):
    def invoke(self,values):
        with patch.object(client,'_auth_headers',return_value={}),patch.object(client.requests,'post',side_effect=values) as post:
            out,metrics=client._request_subject_preview('data:image/jpeg;base64,AA==',[FIX['a_crop']],config.MODELS['analysis'],selection_mode=True,low_confidence_candidates=[FIX['b_candidate']],preview_image_bytes=image_bytes())
        return out,metrics,post
    def bad(self,role='bystander',evidence='insufficient_evidence',tier='detections'):
        r=reply();key='1' if tier=='detections' else 'b1';r[tier][key].update(role=role,selection_evidence=evidence)
        return r
    def test_success_one_call(self):
        out,m,p=self.invoke([response(reply())]);self.assertEqual(out['state'],'ready');self.assertEqual(p.call_count,1)
    def test_validation_repair_once_retains_images_and_error(self):
        out,m,p=self.invoke([response(self.bad()),response(reply())])
        self.assertEqual(out['state'],'ready');self.assertEqual(p.call_count,2)
        first,second=[json.loads(c.kwargs['data']) for c in p.call_args_list]
        self.assertEqual(first['input'][0]['content'],second['input'][0]['content'][:len(first['input'][0]['content'])])
        self.assertIn('invalid_ab_decision',json.dumps(second));self.assertIn('selection_evidence',json.dumps(second))
        self.assertEqual(len(m['diagnostics']['attempts']),2);self.assertEqual(m['usage']['total_tokens'],30)
        self.assertTrue(all(c.kwargs['timeout']==15 for c in p.call_args_list))
    def test_timeout_retry_same_payload(self):
        out,m,p=self.invoke([client.requests.ReadTimeout(),response(reply())]);self.assertEqual(out['state'],'ready');self.assertEqual(p.call_count,2)
        self.assertEqual(p.call_args_list[0].kwargs['data'],p.call_args_list[1].kwargs['data'])
        self.assertEqual(m['diagnostics']['attempts'][0]['error_code'],'request_timeout')
    def test_second_failure_never_third(self):
        for failures in [[client.requests.ReadTimeout(),client.requests.ReadTimeout()],
                         [response(self.bad()),response(self.bad())],
                         [client.requests.ReadTimeout(),response(self.bad())],
                         [response(self.bad()),client.requests.ReadTimeout()]]:
            with self.subTest(failures=str([type(x).__name__ for x in failures])):
                out,m,p=self.invoke(failures+[response(reply())]);self.assertEqual(out['state'],'unavailable');self.assertEqual(p.call_count,2)
    def test_invalid_B_repairs_instead_of_silently_dropping(self):
        out,m,p=self.invoke([response(self.bad(tier='low_confidence_reviews')),response(reply())]);self.assertEqual(p.call_count,2);self.assertEqual(out['low_confidence_state'],'ready')
    def test_both_timeout_types_retry(self):
        out,m,p=self.invoke([client.requests.ConnectTimeout(),response(reply())]);self.assertEqual(out['state'],'ready');self.assertEqual(p.call_count,2)
    def test_invalid_json_is_repaired_with_original_output(self):
        r=response(reply());r.json.return_value['output_text']='not JSON'
        out,m,p=self.invoke([r,response(reply())]);self.assertEqual(out['state'],'ready');self.assertEqual(p.call_count,2)
        self.assertIn('not JSON',p.call_args.kwargs['data']);self.assertEqual(m['diagnostics']['attempts'][0]['error_code'],'invalid_json')
    def test_second_reply_unknown_id_fails_closed(self):
        r=reply();r['detections']['99']=r['detections'].pop('1')
        out,m,p=self.invoke([response(self.bad()),response(r)]);self.assertEqual(out['state'],'unavailable');self.assertEqual(p.call_count,2)
    def test_redirects_disabled_no_hidden_call(self):
        _,_,p=self.invoke([response(reply())]);self.assertFalse(p.call_args.kwargs['allow_redirects'])
    def test_pipeline_recovery_does_not_redetect_or_analyze_stage21(self):
        import pipeline,image_tools
        data=image_bytes();boundary={'face_crops':[dict(FIX['a_crop'],confidence=.9,bytes=data)],'low_confidence_candidates':[dict(FIX['b_candidate'],bytes=None)],'low_confidence_state':'ready'}
        with patch.object(image_tools,'detect_preview_candidates',return_value=boundary) as detect,patch.object(client,'_auth_headers',return_value={}),patch.object(client.requests,'post',side_effect=[response(self.bad()),response(reply())]) as post,patch.object(pipeline,'analyze_photo',side_effect=AssertionError('no Stage 2.1')):
            ctx=pipeline.analyze_preview_stage(data,analysis_model=config.MODELS['analysis'],binding=binding(data),src_keys=['isolated-test'])
        self.assertEqual(ctx['subject_preview']['state'],'ready');self.assertEqual(len(ctx['preview_people']),2)
        self.assertEqual(detect.call_count,1);self.assertEqual(post.call_count,2)
        self.assertEqual(len(ctx['usage']['diagnostics']['attempts']),2)
        from photo_agent.preview_context import validate_preview_context
        self.assertNotIn('usage',validate_preview_context(ctx,image_bytes=data))
    def test_unreadable_A_and_B_remain_members_without_repair(self):
        for tier,key in [('detections','1'),('low_confidence_reviews','b1')]:
            for readable in [False, None]:
                with self.subTest(tier=tier,readable=readable):
                    r=reply();r[tier][key]['face_readable']=readable
                    out,m,p=self.invoke([response(r)])
                    self.assertEqual(out['state'],'ready');self.assertEqual(p.call_count,1)
                    decisions=out[tier]
                    self.assertEqual(decisions[0]['role'],'included')
                    self.assertIs(decisions[0]['face_readable'],readable)
    def test_repair_can_preserve_unreadable_member(self):
        r=reply();r['detections']['1']['face_readable']=False
        out,m,p=self.invoke([response(self.bad()),response(r)])
        self.assertEqual(out['state'],'ready');self.assertEqual(p.call_count,2)
        self.assertNotIn('Included requires face_readable=true',p.call_args.kwargs['data'])
    def test_readability_type_still_validated(self):
        r=reply();r['detections']['1']['face_readable']='false'
        out,m,p=self.invoke([response(r),response(r)])
        self.assertEqual(out['state'],'unavailable');self.assertEqual(p.call_count,2)
    def test_membership_prompt_keeps_anatomy_without_visibility_gate(self):
        instruction=ab.selection_instruction()
        self.assertIn('face_readable is independent of membership',instruction)
        self.assertIn('knuckles',instruction)
        self.assertIn("Resolve the object's contour before naming any eyes or lips",instruction)
        self.assertIn('a palm crease as a mouth',instruction)
        self.assertIn('"clear facial features" is not evidence',instruction)
        self.assertIn('DETECTOR-ONLY POLICY',instruction)
        self.assertNotIn('Face-readability gate',instruction)
        self.assertNotIn('hair-only',instruction)
    def test_api_error_and_refusal_not_retried(self):
        denied=MagicMock();denied.json.return_value={'status':'completed','output':[{'content':[{'type':'refusal'}]}]}
        incomplete=MagicMock();incomplete.json.return_value={'status':'incomplete'}
        for value in [client.requests.HTTPError(),denied,incomplete]:
            out,m,p=self.invoke([value,response(reply())]);self.assertEqual(out['state'],'unavailable');self.assertEqual(p.call_count,1)
    def test_schema_pairs_and_readability(self):
        s=ab.selection_schema(1,[FIX['b_candidate']])['schema'];branches=s['$defs']['ab_decision']['anyOf']
        self.assertEqual(len(branches),4)
        for b in branches:
            props=b['properties'];role=props['role']['enum'][0]
            self.assertEqual(set(props['selection_evidence']['enum']),ab.EVIDENCE[role])
            if role=='included':
                self.assertEqual(set(props['face_readable']['type']),{'boolean','null'})
                self.assertNotIn('enum',props['face_readable'])
        self.assertEqual(s['properties']['detections']['properties']['1'],s['properties']['low_confidence_reviews']['properties']['b1'])
    def test_saved_failed_replies_replay_specific_error(self):
        cases=json.loads((Path(__file__).parent/'fixtures/stage20_recovery_recorded_cases.json').read_text())
        self.assertEqual(len(cases),10)
        for case in cases:
            with self.subTest(filename=case['filename'],repetition=case['repetition']):
                value=ab.normalize_model_selection(case['reply'],case['crops'],case['candidates'])
                if case['error']=='invalid_face_readability':
                    # Historical unreadability rejection is intentionally lifted.
                    ab.validate_selection(value,case['crops'],case['candidates'],low_state=case['low_state'])
                else:
                    with self.assertRaisesRegex(ValueError,case['error']):ab.validate_selection(value,case['crops'],case['candidates'],low_state=case['low_state'])

if __name__=='__main__':unittest.main()
