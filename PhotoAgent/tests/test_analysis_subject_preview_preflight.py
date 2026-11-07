"""Acceptance: every formal analyze gets Stage 2.0 only when the flag is on."""
import copy
import threading
import unittest
from unittest.mock import patch
import image_tools
import pipeline
import config
from tests.test_chat_subject_preview import image_bytes, context


class AnalysisPreviewPreflight(unittest.TestCase):
    def setUp(self):
        self.data=image_bytes()
        p=patch.object(pipeline,'run_call0',return_value=({'state':'unavailable'},None))
        p.start();self.addCleanup(p.stop)
        # Post-analysis geometry and skills are independent of the preflight.
        p=patch.object(pipeline,'analyze_photo',return_value=({'subjects':[]},{}))
        self.enter=p.start();self.addCleanup(p.stop)
        p=patch.object(pipeline,'apply_derived_aspect_ratio',side_effect=lambda value,*a,**k:value);p.start();self.addCleanup(p.stop)
        p=patch.object(pipeline,'ANALYSIS_SKILLS',[]);p.start();self.addCleanup(p.stop)
        self.flag=patch.object(config,'ENABLE_CHAT_SUBJECT_PREVIEW',True);self.flag.start();self.addCleanup(self.flag.stop)
        p=patch.object(config,'ENABLE_PREVIEW_LOW_CONFIDENCE_CANDIDATES',True);p.start();self.addCleanup(p.stop)

    def test_off_keeps_legacy_call_shape_without_running_stage_2_0(self):
        config.ENABLE_CHAT_SUBJECT_PREVIEW=False
        with patch.object(image_tools,'detect_preview_candidates',side_effect=AssertionError('unexpected detector')):
            pipeline.analyze_stage(self.data)
        self.assertNotIn('precomputed_preview',self.enter.call_args.kwargs)

    def test_off_preserves_existing_snapshot_handoff_without_new_preflight_validation(self):
        config.ENABLE_CHAT_SUBJECT_PREVIEW=False
        snapshot=context(self.data)
        with patch.object(pipeline,'_prepare_analysis_preview',side_effect=AssertionError('preflight while off')):
            pipeline.analyze_stage(self.data,precomputed_preview=snapshot)
        self.assertIs(self.enter.call_args.kwargs['precomputed_preview'],snapshot)

    def test_on_valid_ready_snapshot_reuses_the_exact_person_ids(self):
        snapshot=context(self.data)
        with patch.object(image_tools,'detect_preview_candidates',side_effect=AssertionError('duplicate detector')):
            pipeline.analyze_stage(self.data,precomputed_preview=snapshot)
        self.assertEqual(self.enter.call_args.kwargs['precomputed_preview']['subject_preview'],snapshot['subject_preview'])

    def test_on_without_snapshot_runs_stage_2_0_before_formal_analysis(self):
        # If this boundary is missing, formal analysis must not silently proceed.
        with patch.object(image_tools,'detect_preview_candidates',side_effect=RuntimeError('preflight sentinel')) as detect:
            with self.assertRaisesRegex(RuntimeError,'preflight sentinel'):
                pipeline.analyze_stage(self.data)
        detect.assert_called_once();self.enter.assert_not_called()

    def test_on_wrong_model_snapshot_is_rejected_without_analysis(self):
        snapshot=context(self.data);snapshot['binding']['analysis_model']='another-model'
        with self.assertRaises(ValueError):pipeline.analyze_stage(self.data,precomputed_preview=snapshot)
        self.enter.assert_not_called()


class InlineStage20Stories(unittest.TestCase):
    """Actual A/B core and snapshot validators; only detector/LLM boundaries mocked."""
    def setUp(self):
        import json
        from pathlib import Path
        from photo_agent import ab_preview
        self.data=image_bytes()
        p=patch.object(pipeline,'run_call0',return_value=({'state':'unavailable'},None))
        p.start();self.addCleanup(p.stop)
        fixture=json.loads((Path(__file__).parent/'fixtures/ab_candidate_subject_selection.json').read_text())
        a={**fixture['a_crop'],'confidence':.9176,'bytes':b'crop'}
        b={**fixture['b_candidate'],'bytes':None}
        self.boundary=dict(face_crops=[a],low_confidence_candidates=[b],low_confidence_state='ready')
        self.raw_scene=copy.deepcopy(fixture['scenes'][0]['reply'])
        self.preview=ab_preview.validate_selection(copy.deepcopy(self.raw_scene),[a],[b],low_state='ready')
        self.metrics=dict(duration_seconds=.1,model=config.MODELS['analysis'],usage={'input_tokens':10,'output_tokens':5},attempted=True)
        self.patches=[]
        self.start(patch.object(config,'ENABLE_CHAT_SUBJECT_PREVIEW',True))
        self.start(patch.object(config,'ENABLE_PREVIEW_LOW_CONFIDENCE_CANDIDATES',True))
        self.detect=self.start(patch.object(image_tools,'detect_preview_candidates',return_value=self.boundary))
        import openai_client
        self.quick=self.start(patch.object(openai_client,'_request_subject_preview',return_value=(self.preview,self.metrics)))

    def start(self,p):
        self.addCleanup(p.stop)
        return p.start()

    def analyze(self):
        self.formal=self.start(patch.object(pipeline,'analyze_photo',return_value=({'subjects':[]},{})))
        self.start(patch.object(pipeline,'apply_derived_aspect_ratio',side_effect=lambda value,*a,**k:value))
        self.start(patch.object(pipeline,'ANALYSIS_SKILLS',[]))
        return pipeline.analyze_stage(self.data)

    def test_inline_stage_2_0_passes_A_and_B_to_fixed_roster_without_fake_ownership(self):
        from photo_agent.preview_context import AnalysisPreview, validate_analysis_preview
        result=self.analyze()
        self.detect.assert_called_once();self.quick.assert_called_once()
        captured=self.formal.call_args.kwargs['precomputed_preview']
        self.assertIsInstance(captured,AnalysisPreview)
        parsed=validate_analysis_preview(captured,image_bytes=self.data,analysis_model=config.MODELS['analysis'])
        self.assertEqual([p['label'] for p in parsed['preview_people']],['前方男孩','後方女孩'])
        self.assertNotIn('binding',parsed);self.assertNotIn('src_keys',parsed)
        self.assertIn('Stage 2.0 - subject_preview',result['usage'])

    def test_inline_context_rejects_different_photo_and_model_and_public_dict_conversion(self):
        from photo_agent.preview_context import AnalysisPreview, source_sha256, validate_analysis_preview, validate_preview_context
        from dataclasses import asdict
        raw=pipeline._analyze_subject_preview(self.data,analysis_model=config.MODELS['analysis'])
        captured=AnalysisPreview(raw,source_sha256(self.data),config.MODELS['analysis'])
        with self.assertRaises(ValueError):validate_analysis_preview(captured,image_bytes=b'other',analysis_model=config.MODELS['analysis'])
        with self.assertRaises(ValueError):validate_analysis_preview(captured,image_bytes=self.data,analysis_model='other')
        with self.assertRaises(ValueError):validate_preview_context(asdict(captured))

    def test_stage_2_0_unavailable_stops_before_formal_analysis(self):
        from photo_agent.subject_preview import unavailable_preview
        self.quick.return_value=(unavailable_preview('request_failed'),self.metrics)
        with self.assertRaises(ValueError):self.analyze()
        self.formal.assert_not_called()

    def test_unavailable_supplied_snapshot_is_replaced_before_formal_analysis(self):
        self.start(patch.object(pipeline,'analyze_photo',return_value=({'subjects':[]},{})))
        self.start(patch.object(pipeline,'apply_derived_aspect_ratio',side_effect=lambda value,*a,**k:value))
        self.start(patch.object(pipeline,'ANALYSIS_SKILLS',[]))
        pipeline.analyze_stage(self.data,precomputed_preview=context(self.data,state='unavailable'))
        self.quick.assert_called_once()

    def test_core_serves_standalone_chat_preview_with_unchanged_public_contract(self):
        from tests.test_chat_subject_preview import binding
        from photo_agent.preview_context import validate_preview_context
        result=pipeline.analyze_preview_stage(self.data,analysis_model=config.MODELS['analysis'],binding=binding(self.data),src_keys=['real/source.png'])
        parsed=validate_preview_context(result,image_bytes=self.data,src_keys=['real/source.png'])
        self.assertEqual(parsed['binding']['user'],binding(self.data)['user'])
        self.assertEqual(len(parsed['preview_people']),2)
        self.assertIn('timing',result);self.assertIn('usage',result)

    def test_formal_analyzer_uses_existing_fixed_roster_path_for_inline_result(self):
        import openai_client
        from photo_agent.preview_context import AnalysisPreview, source_sha256
        raw=pipeline._analyze_subject_preview(self.data,analysis_model=config.MODELS['analysis'])
        preview=AnalysisPreview(raw,source_sha256(self.data),config.MODELS['analysis'])
        subjects=[dict(preview_person_id=p['preview_person_id'],is_person=True) for p in raw['preview_people']]
        fixed=self.start(patch.object(openai_client,'_analyze_overall_fixed',return_value=({'subjects':subjects},[],{},[])))
        legacy=self.start(patch.object(openai_client,'_analyze_overall_with_roster',side_effect=AssertionError('legacy path')))
        self.start(patch.object(openai_client,'_stamp_local_face_debug_fields',side_effect=lambda value,*a,**k:value))
        self.start(patch.object(openai_client,'_fixed_person_crop_items',return_value=[]))
        self.start(patch.object(openai_client,'compare_subject_preview',return_value={}))
        self.start(patch.object(openai_client,'select_analysis_skills',return_value=[]))
        self.start(patch.object(openai_client,'ANALYSIS_SKILLS',[]))
        result,_=openai_client.analyze_photo(self.data,precomputed_preview=preview)
        fixed.assert_called_once();legacy.assert_not_called()
        self.assertEqual([s['preview_person_id'] for s in result['subjects']],[p['preview_person_id'] for p in raw['preview_people']])
        self.assertNotIn('_subject_preview_identity',result)

    def test_non_chat_full_pipeline_also_runs_the_common_preflight(self):
        formal=self.start(patch.object(pipeline,'analyze_photo',return_value=({'subjects':[]},{})))
        self.start(patch.object(pipeline,'apply_derived_aspect_ratio',side_effect=lambda value,*a,**k:value))
        self.start(patch.object(pipeline,'ANALYSIS_SKILLS',[]))
        finish=self.start(patch.object(pipeline,'finish_stage',return_value={'done':True}))
        result=pipeline.run_pipeline(self.data,'背景調亮','natural','high')
        self.assertTrue(result['done']);self.quick.assert_called_once();formal.assert_called_once()
        self.assertIn('Stage 2.0 - subject_preview',finish.call_args.args[1]['usage'])

    def test_wrong_photo_snapshot_is_rejected_without_generating_replacement_ids(self):
        formal=self.start(patch.object(pipeline,'analyze_photo'))
        snapshot=context(self.data);snapshot['binding']['source_sha256']='0'*64
        with self.assertRaises(ValueError):pipeline.analyze_stage(self.data,precomputed_preview=snapshot)
        self.quick.assert_not_called();formal.assert_not_called()

    def test_on_with_low_candidates_disabled_keeps_the_existing_detector_choice(self):
        from photo_agent import ab_preview
        self.start(patch.object(config,'ENABLE_PREVIEW_LOW_CONFIDENCE_CANDIDATES',False))
        a=self.boundary['face_crops'][0]
        raw=copy.deepcopy(self.raw_scene)
        raw['low_confidence_reviews']=[];raw['additional_people']=[]
        self.quick.return_value=(ab_preview.validate_selection(raw,[a],[],low_state='disabled'),self.metrics)
        detect=self.start(patch.object(image_tools,'detect_face_crops',return_value=[a]))
        self.analyze()
        detect.assert_called_once();self.detect.assert_not_called()


class ParallelPreviewAndRoute(unittest.TestCase):
    def setUp(self):
        AnalysisPreviewPreflight.setUp(self)
        self.enter.side_effect = None
        self.router = self.enterContext(patch.object(pipeline, 'run_call0',
            return_value=({'state': 'ready', 'main_category': 'portrait', 'photo_summary': 'A portrait', 'scene_category': 'indoor', 'scene_type': 'room'}, {'usage': {}})))
        self.enterContext(patch.object(config, 'ENABLE_PHOTO_ROUTER', True))
        self.enterContext(patch('requests.sessions.Session.request',
            side_effect=AssertionError('Network forbidden in unit tests')))

    # unittest.TestCase.enterContext is not available on Python 3.10.
    def enterContext(self, manager):
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return value

    def test_overlap_preserves_language_and_joins_before_analysis(self):
        rendezvous = threading.Barrier(2, timeout=3)
        completed = set()
        snapshot = context(self.data)
        from photo_agent import i18n

        def preview(*args):
            rendezvous.wait()
            completed.add('preview')
            return snapshot, None

        def route(*args):
            self.assertEqual(i18n.current(), 'en')
            rendezvous.wait()
            completed.add('route')
            return {'state': 'ready', 'main_category': 'portrait', 'photo_summary': 'A portrait', 'scene_category': 'indoor', 'scene_type': 'room'}, {'usage': {}}

        def formal(*args, **kwargs):
            self.assertEqual(completed, {'preview', 'route'})
            self.assertIs(kwargs['precomputed_preview'], snapshot)
            self.assertEqual(kwargs['photo_facts']['main_category'], 'portrait')
            return {'subjects': []}, {}

        self.router.side_effect = route
        self.enter.side_effect = formal
        with patch.object(pipeline, '_prepare_analysis_preview', side_effect=preview), i18n.use('en'):
            result = pipeline.analyze_stage(self.data)
        self.router.assert_called_once()
        self.enter.assert_called_once()
        self.assertGreaterEqual(result['timings']['Step 2.0 - photo_route'], 0)

    def test_ready_snapshot_does_not_regenerate_preview(self):
        with patch.object(pipeline, '_prepare_analysis_preview', side_effect=AssertionError('duplicate preview')):
            pipeline.analyze_stage(self.data, precomputed_preview=context(self.data))
        self.router.assert_called_once()
        self.enter.assert_called_once()

    def test_invalid_snapshot_stops_before_router_or_formal_analysis(self):
        snapshot = context(self.data)
        snapshot['binding']['analysis_model'] = 'wrong-model'
        with self.assertRaises(ValueError):
            pipeline.analyze_stage(self.data, precomputed_preview=snapshot)
        self.router.assert_not_called()
        self.enter.assert_not_called()

    def test_router_disabled_runs_only_preview(self):
        with patch.object(config, 'ENABLE_PHOTO_ROUTER', False), patch.object(
                pipeline, '_prepare_analysis_preview', return_value=(context(self.data), None)) as preview:
            pipeline.analyze_stage(self.data)
        preview.assert_called_once()
        self.router.assert_not_called()
        self.assertNotIn('photo_facts', self.enter.call_args.kwargs)

    def test_classification_failure_keeps_formal_analysis_fallback(self):
        from photo_agent.photo_router import run_call0
        self.router.side_effect = run_call0
        with patch('photo_agent.photo_router.route_photo', side_effect=RuntimeError('classification unavailable')), patch.object(
                pipeline, '_prepare_analysis_preview', return_value=(context(self.data), None)):
            result = pipeline.analyze_stage(self.data)
        self.enter.assert_called_once()
        self.assertNotIn('photo_facts', self.enter.call_args.kwargs)
        self.assertEqual(result['photo_route']['state'], 'unavailable')

    def test_failed_preview_does_not_wait_for_running_router(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()

        def route(*args):
            started.set()
            try:
                release.wait(3)
                return {'state': 'unavailable'}, None
            finally:
                finished.set()

        def preview(*args):
            self.assertTrue(started.wait(3))
            raise ValueError('preview failed')

        self.router.side_effect = route
        try:
            with patch.object(pipeline, '_prepare_analysis_preview', side_effect=preview):
                with self.assertRaisesRegex(ValueError, 'preview failed'):
                    pipeline.analyze_stage(self.data)
            self.assertFalse(finished.is_set())
            self.enter.assert_not_called()
        finally:
            release.set()
            self.assertTrue(finished.wait(3))
