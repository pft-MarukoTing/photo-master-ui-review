"""Detector-only policy: no unlinked people; real validators and handoff."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import config
from photo_agent import ab_preview
from photo_agent.preview_context import preview_versions, validate_preview_binding, AnalysisPreview, validate_analysis_preview
from tests.test_chat_subject_preview import binding, image_bytes

DATA=json.loads((Path(__file__).parent/'fixtures/ab_candidate_subject_selection.json').read_text())

def scene():
    raw=copy.deepcopy(DATA['scenes'][0]['reply'])
    raw['additional_people']=[dict(label='另一位背影人物',role='included',face_readable=False,
        box=dict(x=.05,y=.05,w=.12,h=.3),thumbnail_candidate_id=None,matched_detection_index=None,
        selection_evidence='intended_subject',reason='全圖可見的另一位拍攝主體')]
    return raw

class DetectorOnly(unittest.TestCase):
    def test_config_removed_and_defaults_are_detector_only(self):
        self.assertFalse(hasattr(config,'ENABLE_CHAT_PREVIEW_DETECTOR_ONLY'))
        props=ab_preview.selection_schema()['schema']['properties']
        self.assertEqual(props['additional_people']['maxItems'],0)
        raw=scene()
        checked=ab_preview.validate_selection(raw,[DATA['a_crop']],[DATA['b_candidate']])
        self.assertEqual(checked['additional_people'],[])
        self.assertEqual(ab_preview.selection_instruction(),ab_preview.selection_instruction(detector_only=True))

    def test_true_schema_no_additional_and_only_real_A_indices(self):
        for n in (0,1,3):
            props=ab_preview.selection_schema(n,[],detector_only=True)['schema']['properties']
            self.assertEqual(props['primary_detection_index']['enum'],[None,*range(1,n+1)])
            self.assertEqual(props['additional_people']['maxItems'],0)

    def test_false_keeps_original_schema_and_additional(self):
        props=ab_preview.selection_schema(1,[],detector_only=False)['schema']['properties']
        self.assertNotIn('enum',props['primary_detection_index'])
        raw=scene()
        result=ab_preview.validate_selection(raw,[DATA['a_crop']],[DATA['b_candidate']],detector_only=False)
        self.assertEqual(len(result['additional_people']),1)
        self.assertEqual(len(ab_preview.build_people(result,[DATA['a_crop']],[DATA['b_candidate']])),3)

    def test_true_discards_even_malformed_additional_keeps_AB(self):
        for extra in (scene()['additional_people'], {'malformed':'ignored'}):
            raw=scene();raw['additional_people']=extra
            result=ab_preview.validate_selection(raw,[DATA['a_crop']],[DATA['b_candidate']],detector_only=True)
            self.assertEqual(result['additional_people'],[])
            people=ab_preview.build_people(result,[DATA['a_crop']],[DATA['b_candidate']])
            self.assertEqual(len(people),2)
            self.assertEqual(people[1]['thumbnail_candidate_id'],'b1')

    def test_zero_A_normalizes_primary_before_validation_and_keeps_B(self):
        raw=scene();raw['detections']=[];raw['primary_detection_index']=1
        result=ab_preview.validate_selection(raw,[],[DATA['b_candidate']],detector_only=True)
        self.assertIsNone(result['primary_detection_index'])
        self.assertEqual(len(ab_preview.build_people(result,[],[DATA['b_candidate']])),1)
        raw['low_confidence_reviews']=[]
        result=ab_preview.validate_selection(raw,[],[],detector_only=True)
        self.assertEqual(result['state'],'ready')
        self.assertEqual(ab_preview.build_people(result,[],[]),[])

    def test_false_reproduces_invalid_primary_from_img2_i(self):
        raw=scene();raw.update(detections=[],low_confidence_reviews=[],additional_people=[],primary_detection_index=1)
        with self.assertRaisesRegex(ValueError,'invalid_primary_detection'):
            ab_preview.validate_selection(raw,[],[],detector_only=False)

    def test_nonzero_A_still_rejects_unknown_primary(self):
        raw=scene();raw['primary_detection_index']=2
        with self.assertRaisesRegex(ValueError,'invalid_primary_detection'):
            ab_preview.validate_selection(raw,[DATA['a_crop']],[DATA['b_candidate']],detector_only=True)

    def test_true_still_excludes_bystanders_and_allows_empty_food_scene(self):
        raw=scene()
        raw['low_confidence_reviews'][0].update(role='bystander',selection_evidence='incidental_background')
        result=ab_preview.validate_selection(raw,[DATA['a_crop']],[DATA['b_candidate']],detector_only=True)
        self.assertEqual([p['label'] for p in ab_preview.build_people(result,[DATA['a_crop']],[DATA['b_candidate']])],['前方男孩'])
        raw.update(main_subject_kind='non_person',detections=[],low_confidence_reviews=[],primary_detection_index=None)
        result=ab_preview.validate_selection(raw,[],[],detector_only=True)
        self.assertEqual(result['state'],'ready')
        self.assertEqual(ab_preview.build_people(result,[],[]),[])

    def test_old_additional_snapshot_is_rejected(self):
        data=image_bytes()
        original_schema=ab_preview.selection_schema
        with patch.object(ab_preview,'selection_instruction',return_value=ab_preview.INSTRUCTION), \
             patch.object(ab_preview,'selection_schema',side_effect=lambda *a,**k:original_schema(*a,detector_only=False)):
            old=binding(data)
        with self.assertRaisesRegex(ValueError,'stale_or_invalid_preview_binding'):
            validate_preview_binding(old)
        owned=AnalysisPreview({},old['source_sha256'],config.MODELS['analysis'],detector_only=False)
        with self.assertRaisesRegex(ValueError,'analysis_preview_policy_mismatch'):
            validate_analysis_preview(owned,image_bytes=data,analysis_model=config.MODELS['analysis'])
        self.assertIs(AnalysisPreview({},old['source_sha256'],config.MODELS['analysis']).detector_only,True)

    def test_instructions_keep_false_exact_and_explain_zero_candidates(self):
        self.assertEqual(ab_preview.selection_instruction(detector_only=False),ab_preview.INSTRUCTION)
        text=ab_preview.selection_instruction(detector_only=True)
        self.assertIn('additional_people=[]',text)
        self.assertIn('zero A',text)
        self.assertNotIn('Such people need a valid normalized',text)

    def test_client_fixed_policy_ignores_stale_false_config_attribute(self):
        import openai_client
        from unittest.mock import MagicMock
        raw=scene();raw.update(detections={},low_confidence_reviews={},primary_detection_index=1)
        response=MagicMock()
        response.json.return_value={'status':'completed','output_text':json.dumps(raw)}
        with patch.object(config,'ENABLE_CHAT_PREVIEW_DETECTOR_ONLY',False,create=True), \
             patch.object(openai_client,'_auth_headers',return_value={}), \
             patch.object(openai_client.requests,'post',return_value=response) as model:
            result,_=openai_client._request_subject_preview('data:image/jpeg;base64,AA==',[],config.MODELS['analysis'],selection_mode=True,preview_image_bytes=image_bytes())
        self.assertEqual(result['state'],'ready')
        self.assertIsNone(result['primary_detection_index'])
        self.assertEqual(result['additional_people'],[])
        payload=json.loads(model.call_args.kwargs['data'])
        self.assertEqual(payload['text']['format']['schema']['properties']['additional_people']['maxItems'],0)

    def test_empty_people_fixed_stage21_does_not_add_humans(self):
        import openai_client,pipeline,image_tools
        from photo_agent.preview_context import source_sha256
        raw=scene();raw.update(detections=[],low_confidence_reviews=[],additional_people=[],primary_detection_index=None)
        preview=ab_preview.validate_selection(raw,[],[],detector_only=True)
        data=image_bytes()
        with patch.object(image_tools,'detect_preview_candidates',return_value=dict(face_crops=[],low_confidence_candidates=[],low_confidence_state='ready')), \
             patch.object(config,'ENABLE_PREVIEW_LOW_CONFIDENCE_CANDIDATES',True), \
             patch.object(openai_client,'_request_subject_preview',return_value=(preview,dict(duration_seconds=0,usage={},model=config.MODELS['analysis']))):
            context=pipeline._analyze_subject_preview(data,analysis_model=config.MODELS['analysis'])
            owned=AnalysisPreview(context,source_sha256(data),config.MODELS['analysis'])
            reply=dict(photo_summary='背影照片，固定可編輯人物名冊為空。',person_details={},scene_subjects=[])
            with patch.object(openai_client,'_post_json',return_value={'output_text':json.dumps(reply)}) as model, \
                 patch.object(openai_client,'ANALYSIS_SKILLS',[]):
                analysis,_=openai_client.analyze_photo(data,precomputed_preview=owned)
        self.assertEqual(analysis['subjects'],[])
        self.assertEqual(analysis['_fixed_preview_person_ids'],[])
        self.assertEqual(model.call_count,1)
        self.assertEqual(model.call_args.args[1]['text']['format']['schema']['properties']['person_details']['required'],[])

if __name__=='__main__':unittest.main()
