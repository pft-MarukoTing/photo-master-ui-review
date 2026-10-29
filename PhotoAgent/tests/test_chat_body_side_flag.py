"""Spec: Chat may omit angle confirmation; all other target/text guards remain."""
import io,json,unittest
from unittest.mock import patch
import config,image_tools,pipeline
from photo_agent import capabilities,chat_assistant as assistant,chat_plan,side_reference
from photo_agent.param_validation import _validate_params
from photo_agent.edit_prompt import assemble_edit_prompt
from tests import test_chat_routes as routes
from tests import test_session_request_spec as stories

class FlagStories(unittest.TestCase):
    def test_default_false_and_capability_matrix(self):
        self.assertIs(config.ENABLE_CHAT_BODY_SIDE_CLARIFICATION,False)
        for enabled in (False,True):
            with patch.object(config,'ENABLE_CHAT_BODY_SIDE_CLARIFICATION',enabled):
                self.assertEqual(capabilities.describe()['features']['body_side_clarification'],enabled)

    def test_ask_off_keeps_left_right_and_literal_without_angle_question(self):
        line='左手拿水壺；在照片上加入文字「777」'
        values=[stories.semantic('左手拿水壺',action='拿水壺',kind='person',owner=True,feature='holding'),stories.semantic('在照片上加入文字「777」',action='加入文字「777」',literal='777',feature='text_overlay')]
        with patch.object(config,'ENABLE_CHAT_BODY_SIDE_CLARIFICATION',False):
            reply=assistant.ask({'subjects':[{'label':'女性','is_person':True}]},chat_plan.default_plan(),'ready',[stories.user(line)],model=lambda _:stories.edit(line,values))
        self.assertEqual(reply['proposal']['patch']['user_prompt'],line)
        self.assertEqual(reply['proposal']['patch']['literal_texts'],['777'])
        self.assertNotIn('clarification_subtype',reply)

    def test_review_off_uses_normal_model_and_preserves_original_side_wording(self):
        text='左手拿水壺';seen=[]
        raw=dict(verdict='keep',reply='已記錄',line=text,options=[],semantics=[stories.semantic(text,action='拿水壺',owner=True,kind='person',feature='holding')])
        def model(context):seen.append(context);return raw
        with patch.object(config,'ENABLE_CHAT_BODY_SIDE_CLARIFICATION',False):
            result=assistant.review_line(text,'',people=['照片中從左邊數來第 1 位'],model=model)
        self.assertTrue(result['kept']);self.assertFalse(result['ask'])
        self.assertNotIn('clarification_subtype',result)
        self.assertIn('Do not ask subject-relative',seen[0])
        self.assertEqual(result['line'] or text,text)

    def test_enabled_ask_preserves_angle_question_without_model_call(self):
        with patch.object(config,'ENABLE_CHAT_BODY_SIDE_CLARIFICATION',True):
            reply=assistant.ask({'subjects':[{'label':'女性','is_person':True}]},chat_plan.default_plan(),'ready',[stories.user('左手拿水壺')],model=lambda _:self.fail('No model before angle clarification'))
        self.assertEqual(reply['clarification_subtype'],'body_side_reference')

    def test_off_still_allows_person_question(self):
        line='左手拿水壺'
        q=stories.question(line,kind='person',options=('女孩','男孩'),values=[stories.semantic(line,action='拿水壺',owner=True,kind='person')])
        with patch.object(config,'ENABLE_CHAT_BODY_SIDE_CLARIFICATION',False):
            result=assistant.ask({'subjects':[{'label':'女孩'},{'label':'男孩'}]},chat_plan.default_plan(),'ready',[stories.user(line)],model=lambda _:q)
        self.assertEqual(result['question_kind'],'person')

    def test_only_internal_chat_policy_bypasses_worker_parameter_side_guard(self):
        for flag in (False,True):
            with patch.object(config,'ENABLE_CHAT_BODY_SIDE_CLARIFICATION',flag):
                with self.assertRaisesRegex(RuntimeError,'unresolved_body_side_reference'):_validate_params(dict(stage='finish',user_prompt='左手拿水壺'))
        value=_validate_params(dict(stage='finish',user_prompt='左手拿水壺',chat_body_side_clarification=False))
        self.assertEqual(value['user_prompt'],'左手拿水壺')
        with self.assertRaisesRegex(RuntimeError,'invalid_chat_side_policy'):_validate_params(dict(stage='finish',chat_body_side_clarification='false'))

    def test_editor_receives_raw_request_and_no_invented_frame(self):
        text='左手拿水壺；在照片上加入文字「777」'
        class Reached(Exception):pass
        def editor(data,analysis,*a,**kw):
            instruction=analysis['user_instruction']
            self.assertFalse(instruction['chat_body_side_clarification'])
            prompt,_,_=assemble_edit_prompt(analysis,None,preset_key='natural',aspect_ratio='original',source_size=(100,100),single_pass_edit=True)
            self.assertIn(text,prompt);self.assertNotIn('BODY SIDE REFERENCE CONTRACT',prompt)
            raise Reached()
        review=dict(user_instruction=dict(normalized_user_direction=text,overwrite_prompt=True),prompt='test',usage={},duration=0)
        with patch.object(pipeline,'get_image_dimensions',return_value=(100,100)),patch.object(pipeline,'apply_derived_aspect_ratio',side_effect=lambda a,*args,**kw:a),patch.object(pipeline,'_editor_for',return_value=editor):
            with self.assertRaises(Reached):pipeline.finish_stage(b'fake',{},text,preset_key='natural',quality='medium',review=review,overwrite_prompt=True,literal_texts=['777'],chat_body_side_clarification=False)

class WorkerPolicy(unittest.TestCase):
    def test_finish_and_full_forward_frozen_chat_policy_without_model_calls(self):
        import importlib.util,sys,tempfile,types
        from pathlib import Path
        from unittest.mock import MagicMock
        queue=types.ModuleType('aiserver_utils.queue');queue.set_job_info=MagicMock();queue.get_job_info=MagicMock()
        spec=importlib.util.spec_from_file_location('side_policy_worker',Path(__file__).resolve().parents[1]/'job_processor.py')
        worker=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'aiserver_utils.queue':queue}):spec.loader.exec_module(worker)
        for stage in ('finish','full'):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as directory:
                source=Path(directory)/'src.jpg';source.write_bytes(b'fake')
                processor=worker.PhotoAgentJobProcessor(MagicMock(),MagicMock(),monitor_dst_dir=directory)
                params=dict(stage=stage,user_prompt='左手拿水壺',analysis={},chat_body_side_clarification=False)
                task=dict(effect='photoAgent',acts=[dict(id=0,params=params)],srcKeys=['source.jpg'],dstKeys=['out.jpg'])
                with patch.object(processor,'download_sources',return_value=[str(source)]),patch.object(processor,'upload_artifacts'),patch.object(worker,'analyze_stage',return_value={}),patch.object(worker,'review_stage',return_value={}),patch.object(worker,'finish_stage',return_value={}) as finish,patch.object(worker,'_write_result_artifacts',return_value=[str(source)]):
                    processor.process_for_api('side-policy-'+stage,task)
                finish.assert_called_once()
                self.assertIs(finish.call_args.kwargs['chat_body_side_clarification'],False)
                self.assertEqual(finish.call_args.kwargs['user_prompt'],'左手拿水壺')

class RouteStories(unittest.TestCase):
    setUp=routes.ChatRoutesTest.setUp
    new_session=routes.ChatRoutesTest.new_session
    add_photos=routes.ChatRoutesTest.add_photos
    ready_photo=routes.ChatRoutesTest.ready_photo
    status=routes.ChatRoutesTest.status
    url=routes.ChatRoutesTest.url
    def test_chat_render_off_queues_unresolved_side_without_affecting_public_api(self):
        with patch.object(config,'ENABLE_CHAT_BODY_SIDE_CLARIFICATION',False):
            sid=self.new_session();pid=self.ready_photo(sid)['photo_id']
            r=self.client.patch(self.url(sid,'/photos/'+pid+'/plan'),json=dict(user=routes.USER,plan=dict(user_prompt='左手拿水壺')))
            self.assertEqual(r.status_code,200,r.get_json())
            rendered=self.client.post(self.url(sid,'/render'),json=dict(user=routes.USER,photo_ids=[pid]))
            self.assertEqual(rendered.status_code,202,rendered.get_json())
            params=self.enqueued[-1][1]['acts'][0]['params']
            _parse_and_validate_params=routes.api._parse_and_validate_params
            with self.client.application.app_context():
                _,error=_parse_and_validate_params(dict(user_prompt='左手拿水壺',chat_body_side_clarification=False))
            self.assertIn('unresolved_body_side_reference',error)
            self.assertIs(params['chat_body_side_clarification'],False)

    def test_switching_off_pending_question_unblocks_render_without_marking_frame_confirmed(self):
        sid=self.new_session();pid=self.ready_photo(sid)['photo_id'];url=self.url(sid,'/photos/'+pid)
        with patch.object(config,'ENABLE_CHAT_BODY_SIDE_CLARIFICATION',True):
            q=self.client.post(url+'/ask',json=dict(user=routes.USER,text='左手拿水壺'));self.assertEqual(q.status_code,200)
        with patch.object(config,'ENABLE_CHAT_BODY_SIDE_CLARIFICATION',False):
            photo=routes.chat_session.load_session(routes.USER,sid)['photos'][0]
            self.assertIsNone(routes.chat._conversation_render_problem(photo))
            self.assertFalse(side_reference.executable(photo['side_reference']))

if __name__=='__main__':unittest.main()
