"""Spec: left/right body wording is an ordinary edit everywhere -- no angle
question in chat, no rejection in panel mode or the worker, and the editor
gets the user's own words."""
import unittest
from unittest.mock import patch
import pipeline
from photo_agent import capabilities,chat_assistant as assistant,chat_plan
from photo_agent.param_validation import _validate_params
from photo_agent.edit_prompt import assemble_edit_prompt
from tests import test_chat_routes as routes
from tests import test_session_request_spec as stories

class WordingStories(unittest.TestCase):
    def test_capabilities_no_longer_offer_the_switch(self):
        self.assertNotIn('body_side_clarification',capabilities.describe()['features'])

    def test_ask_keeps_left_right_and_literal_without_angle_question(self):
        line='左手拿水壺；在照片上加入文字「777」'
        values=[stories.semantic('左手拿水壺',action='拿水壺',kind='person',owner=True,feature='holding'),stories.semantic('在照片上加入文字「777」',action='加入文字「777」',literal='777',feature='text_overlay')]
        reply=assistant.ask({'subjects':[{'label':'女性','is_person':True}]},chat_plan.default_plan(),'ready',[stories.user(line)],model=lambda _:stories.edit(line,values))
        self.assertEqual(reply['proposal']['patch']['user_prompt'],line)
        self.assertEqual(reply['proposal']['patch']['literal_texts'],['777'])
        self.assertNotIn('clarification_subtype',reply)

    def test_review_tells_the_model_not_to_ask_and_keeps_the_wording(self):
        text='左手拿水壺';seen=[]
        raw=dict(verdict='keep',reply='已記錄',line=text,options=[],semantics=[stories.semantic(text,action='拿水壺',owner=True,kind='person',feature='holding')])
        def model(context):seen.append(context);return raw
        result=assistant.review_line(text,'',people=['照片中從左邊數來第 1 位'],model=model)
        self.assertTrue(result['kept']);self.assertFalse(result['ask'])
        self.assertNotIn('clarification_subtype',result)
        self.assertIn('Do not ask subject-relative',seen[0])
        self.assertEqual(result['line'] or text,text)

    def test_person_question_still_asked(self):
        line='左手拿水壺'
        q=stories.question(line,kind='person',options=('女孩','男孩'),values=[stories.semantic(line,action='拿水壺',owner=True,kind='person')])
        result=assistant.ask({'subjects':[{'label':'女孩'},{'label':'男孩'}]},chat_plan.default_plan(),'ready',[stories.user(line)],model=lambda _:q)
        self.assertEqual(result['question_kind'],'person')

    def test_worker_parameters_accept_unqualified_sides(self):
        # Panel mode and other API callers used to be rejected here.
        value=_validate_params(dict(stage='finish',user_prompt='左手拿水壺'))
        self.assertEqual(value['user_prompt'],'左手拿水壺')

    def test_editor_receives_raw_request_and_no_side_contract(self):
        text='畫面左邊的手拿水壺；在照片上加入文字「777」'
        class Reached(Exception):pass
        def editor(data,analysis,*a,**kw):
            prompt,_,_=assemble_edit_prompt(analysis,None,preset_key='natural',aspect_ratio='original',source_size=(100,100),single_pass_edit=True)
            self.assertIn(text,prompt);self.assertNotIn('BODY SIDE REFERENCE CONTRACT',prompt)
            raise Reached()
        review=dict(user_instruction=dict(normalized_user_direction=text,overwrite_prompt=True),prompt='test',usage={},duration=0)
        with patch.object(pipeline,'get_image_dimensions',return_value=(100,100)),patch.object(pipeline,'apply_derived_aspect_ratio',side_effect=lambda a,*args,**kw:a),patch.object(pipeline,'_editor_for',return_value=editor):
            with self.assertRaises(Reached):pipeline.finish_stage(b'fake',{},text,preset_key='natural',quality='medium',review=review,overwrite_prompt=True,literal_texts=['777'])

    def test_a_retired_side_token_in_a_plan_is_dropped(self):
        plan,error=chat_plan.apply_plan_patch({**chat_plan.default_plan(),'side_reference_token':None},{'side_reference_token':None,'user_prompt':'左手拿水壺'})
        self.assertIsNone(error)
        self.assertNotIn('side_reference_token',plan)
        self.assertEqual(plan['user_prompt'],'左手拿水壺')

class RouteStories(unittest.TestCase):
    setUp=routes.ChatRoutesTest.setUp
    new_session=routes.ChatRoutesTest.new_session
    add_photos=routes.ChatRoutesTest.add_photos
    ready_photo=routes.ChatRoutesTest.ready_photo
    status=routes.ChatRoutesTest.status
    url=routes.ChatRoutesTest.url
    def test_chat_render_queues_unqualified_side(self):
        sid=self.new_session();pid=self.ready_photo(sid)['photo_id']
        r=self.client.patch(self.url(sid,'/photos/'+pid+'/plan'),json=dict(user=routes.USER,plan=dict(user_prompt='左手拿水壺')))
        self.assertEqual(r.status_code,200,r.get_json())
        rendered=self.client.post(self.url(sid,'/render'),json=dict(user=routes.USER,photo_ids=[pid]))
        self.assertEqual(rendered.status_code,202,rendered.get_json())
        self.assertEqual(rendered.get_json()['renders'][0]['photo_id'],pid)
        params=self.enqueued[-1][1]['acts'][0]['params']
        self.assertEqual(params['stage'],'finish')
        self.assertNotIn('chat_body_side_clarification',params)

    def test_public_api_accepts_unqualified_side(self):
        with self.client.application.app_context():
            params,error=routes.api._parse_and_validate_params(dict(user_prompt='左手拿水壺'))
        self.assertIsNone(error)
        self.assertNotIn('chat_body_side_clarification',params)

if __name__=='__main__':unittest.main()
