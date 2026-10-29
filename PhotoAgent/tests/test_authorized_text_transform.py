"""Spec: user-authorized text conversion, exact literals, and answerable questions."""
import copy,json,unittest
from unittest.mock import patch
import config
from tests import test_session_request_spec as stories
from photo_agent import chat_assistant as chat, chat_plan
from tests.test_chat_assistant import analysis
from tests.test_session_request_spec import semantic, edit, user, question

OLD='秋夜，車流仍在前進'
NEW='Autumn Night, Traffic Keeps Moving Forward'
COMMAND='加入的文字改成英文'
def printed(text): return '在照片上加入文字「'+text+'」'
def transform(old=OLD,new=NEW,instruction=COMMAND):
    return dict(operation='translate',original=old,replacement=new,instruction=instruction)
def answer(text=NEW,changes=None):
    out=edit(printed(text),[semantic(printed(text),literal=text,feature='text_overlay')])
    out['request']['text_transformations']=[transform()] if changes is None else changes
    return out

class TextTransformStories(unittest.TestCase):
    def ask(self,results,text=COMMAND,plan=None):
        plan=plan or chat_plan.default_plan();plan.setdefault('literal_texts',[OLD])
        if not plan['user_prompt']:plan['user_prompt']=printed(OLD)
        before=copy.deepcopy(plan);calls=[]
        def model(context):
            calls.append(context);return copy.deepcopy(results[min(len(calls)-1,len(results)-1)])
        result=chat.ask(analysis(),plan,'ready',[user(text)],model=model)
        self.assertEqual(plan,before)
        return result,calls

    def test_translate_existing_text_then_protect_the_translated_literal(self):
        result,calls=self.ask([answer()])
        self.assertIsNotNone(result['proposal'])
        self.assertEqual(len(calls),1)
        patch=result['proposal']['patch']
        self.assertEqual(patch['literal_texts'],[NEW]);self.assertNotIn(OLD,patch['user_prompt'])
        plan=chat_plan.default_plan();plan.update(patch)
        retained=edit(printed(NEW)+'；背景改成學校',[
            semantic(printed(NEW),literal=NEW,feature='text_overlay'),semantic('背景改成學校')])
        out,_=self.ask([retained],text='背景改成學校',plan=plan)
        self.assertEqual(out['proposal']['patch']['literal_texts'],[NEW])

    def test_rewrite_and_correction_are_not_translation_keyword_rules(self):
        for operation,instruction,new in [('rewrite','把這段改得更口語','晚上車還是繼續往前'),('correct','把車流改成車潮','秋夜，車潮仍在前進')]:
            with self.subTest(operation=operation):
                change=transform(new=new,instruction=instruction);change['operation']=operation
                out,_=self.ask([answer(new,[change])],text=instruction)
                self.assertIsNotNone(out['proposal'])

    def test_unauthorized_changes_stale_sources_and_stale_instructions_fail_closed(self):
        for enabled in (False,True):
            for changes in ([],[transform(old='不存在的文字')],[transform(instruction='以前說過改成英文')]):
                with self.subTest(patch_continue=enabled,changes=changes), \
                     patch.object(config,'ENABLE_CHAT_PATCH_CONTINUE',enabled):
                    out,calls=self.ask([answer(changes=changes),answer(changes=changes)])
                    self.assertIsNone(out['proposal']);self.assertFalse(out['render'])
                    self.assertEqual(len(calls),3 if enabled else 2)
                    if enabled:
                        self.assertIn('DISPLAY RECOVERY ONLY',calls[-1])
                        self.assertEqual(out['clarification']['application_status'],'not_applied')

    def test_unrelated_printed_text_remains_exact(self):
        plan=chat_plan.default_plan();plan.update(user_prompt=printed(OLD)+'；'+printed('777'),literal_texts=[OLD,'777'])
        def response(second):
            out=edit(printed(NEW)+'；'+printed(second),[
                semantic(printed(NEW),literal=NEW,feature='text_overlay'),semantic(printed(second),literal=second,feature='text_overlay')])
            out['request']['text_transformations']=[transform()]
            return out
        out,_=self.ask([response('778'),response('777')],plan=plan)
        self.assertEqual(out['proposal']['patch']['literal_texts'],[NEW,'777'])

    def test_review_translation_replaces_previous_section_and_keeps_other_sections(self):
        sections=[dict(who='',text=printed(OLD)),dict(who='',text=printed('777'))]
        raw=dict(verdict='keep',reply='已翻譯',line=printed(NEW),options=[],
            semantics=[semantic(printed(NEW),literal=NEW,feature='text_overlay')],
            text_transformations=[transform()],merged=[dict(section=1,text='')],withdraw=False)
        out=chat.review_line(COMMAND,'；'.join(s['text'] for s in sections),sections=sections,model=lambda _:copy.deepcopy(raw))
        self.assertTrue(out['kept']);self.assertEqual(out['request_semantics'][0]['literal_text'],NEW)
        self.assertEqual(out['merged'],[dict(section=0,text='')])

    def test_translation_target_ambiguity_asks_without_mutating_any_text(self):
        q=question(COMMAND,reply='要翻譯哪段文字？',kind='content',options=['第一段','第二段','全部'])
        out,calls=self.ask([q]);self.assertIsNone(out['proposal'])
        self.assertEqual(out['options'],['第一段','第二段','全部']);self.assertEqual(len(calls),1)

    def test_ratio_question_repair_keeps_question_and_ignores_side_effects(self):
        initial=question('改變aspect ratio',reply='想改成哪個長寬比？',kind='content',options=[])
        malicious=edit('不應套用的內容');malicious['options']=['1:1','3:4','16:9']
        out,calls=self.ask([initial,malicious],text='改變aspect ratio')
        self.assertIsNone(out['proposal']);self.assertFalse(out['render'])
        self.assertEqual(out['reply'],initial['reply']);self.assertEqual(out['options'],['1:1','3:4','16:9'])
        self.assertEqual(len(calls),2)

    def test_failed_choice_repair_preserves_answerable_question_without_generic_error(self):
        q=question('改變aspect ratio',reply='想改成哪個長寬比？',kind='content',options=['3:4','3:4'])
        out,calls=self.ask([q,q],text='改變aspect ratio')
        self.assertIsNone(out['proposal']);self.assertEqual(out['reply'],q['reply'])
        self.assertEqual(out['answer_mode'],'free_text');self.assertEqual(out['options'],[])
        self.assertEqual(len(calls),2)

    def test_current_clarification_answer_completes_translation_without_requiring_restatement(self):
        old_plan=chat_plan.default_plan()
        old_plan.update(user_prompt=printed(OLD)+'；'+printed('777'),literal_texts=[OLD,'777'])
        history=[user(COMMAND),dict(role='assistant',id='q1',card=dict(type='llm_reply',
            question_kind='content',options=['第一段','第二段','全部'],text='要翻譯哪段？',
            clarification=dict(original=COMMAND,line=COMMAND,semantics=[]))),user('第一段')]
        raw=edit(printed(NEW)+'；'+printed('777'),[
            semantic(printed(NEW),literal=NEW,feature='text_overlay'),
            semantic(printed('777'),literal='777',feature='text_overlay')])
        raw['request']['text_transformations']=[transform()]
        result=chat.ask(analysis(),old_plan,'ready',history,model=lambda _:copy.deepcopy(raw))
        self.assertIsNotNone(result['proposal'])
        self.assertEqual(result['proposal']['patch']['literal_texts'],[NEW,'777'])

    def test_translation_undo_and_cancel_restore_matching_prompt_and_literals(self):
        plan=chat_plan.default_plan();plan.update(user_prompt=printed(OLD),literal_texts=[OLD])
        out,_=self.ask([answer()],plan=plan)
        changed,error=chat_plan.apply_plan_patch(plan,out['proposal']['patch'])
        self.assertIsNone(error)
        restored,error=chat_plan.apply_plan_patch(changed,out['proposal']['undo'])
        self.assertIsNone(error);self.assertEqual(restored['literal_texts'],[OLD])
        self.assertEqual(restored['user_prompt'],printed(OLD))
        cancelled,_=self.ask([edit('',[])],text='取消全部文字',plan=changed)
        self.assertEqual(cancelled['proposal']['patch']['literal_texts'],[])
        self.assertEqual(cancelled['proposal']['patch']['user_prompt'],'')

    def test_upload_cannot_leave_old_translated_section_or_reuse_inactive_text(self):
        raw=dict(verdict='keep',reply='已翻譯',line=printed(NEW),options=[],
            semantics=[semantic(printed(NEW),literal=NEW,feature='text_overlay')],
            text_transformations=[transform()],merged=[],withdraw=False)
        result=chat.review_line(COMMAND,printed(OLD),model=lambda _:copy.deepcopy(raw))
        self.assertFalse(result['kept']);self.assertTrue(result['request_error'])

    def test_question_repair_timeout_still_leaves_free_text_question(self):
        q=question('改變aspect ratio',reply='想改成哪個長寬比？',kind='content',options=[])
        with patch.object(chat,'call_model',side_effect=[q,TimeoutError()]) as model:
            result=chat.ask(analysis(),chat_plan.default_plan(),'ready',[user('改變aspect ratio')])
        self.assertEqual(model.call_count,2);self.assertEqual(result['reply'],q['reply'])
        self.assertEqual(result['answer_mode'],'free_text');self.assertIsNone(result['proposal'])

    def test_new_strict_schemas_require_transform_array_without_changing_base_schema(self):
        from photo_agent import session_request,text_transform
        base=copy.deepcopy(chat.RESPONSE_SCHEMA)
        expanded=session_request.schema(chat.RESPONSE_SCHEMA)
        request=expanded['schema']['properties']['request']
        self.assertIn('text_transformations',request['required'])
        self.assertEqual(request['properties']['text_transformations'],text_transform.SCHEMA)
        self.assertIn('text_transformations',chat.REVIEW_SCHEMA['schema']['required'])
        self.assertEqual(chat.RESPONSE_SCHEMA,base)
        item=text_transform.SCHEMA['items']
        self.assertFalse(item['additionalProperties'])
        self.assertEqual(set(item['required']),set(item['properties']))

    def test_review_current_question_answer_authorizes_only_selected_paragraph(self):
        raw=dict(verdict='keep',reply='已翻譯',line=printed(NEW),options=[],
            semantics=[semantic(printed(NEW),literal=NEW,feature='text_overlay')],
            text_transformations=[transform()],merged=[dict(section=1,text='')],withdraw=False)
        history=[dict(role='user',text=COMMAND),dict(role='assistant',question=True,
            text='要翻譯哪段？',options=['第一段','第二段','全部'])]
        sections=[dict(who='',text=printed(OLD)),dict(who='',text=printed('777'))]
        out=chat.review_line('第一段','；'.join(s['text'] for s in sections),sections=sections,
            history=history,model=lambda _:copy.deepcopy(raw))
        self.assertTrue(out['kept']);self.assertFalse(out['ask'])
        self.assertEqual(out['request_semantics'][0]['literal_text'],NEW)

class TextTransformRoutes(unittest.TestCase):
    setUp=stories.SessionRouteStories.setUp
    url=stories.SessionRouteStories.url
    new_session=stories.SessionRouteStories.new_session
    ready_photo=stories.SessionRouteStories.ready_photo
    add_photos=stories.SessionRouteStories.add_photos
    status=stories.SessionRouteStories.status
    send=stories.SessionRouteStories.send
    stored=stories.SessionRouteStories.stored

    def test_translation_reaches_saved_plan_and_render_job_in_both_preview_modes(self):
        for enabled in (False,True):
            with self.subTest(preview=enabled),patch.object(stories.support.chat.agent_config,'ENABLE_CHAT_SUBJECT_PREVIEW',enabled):
                sid=self.new_session();pid=self.ready_photo(sid)['photo_id']
                self.send(sid,pid,printed(OLD),edit(printed(OLD),[semantic(printed(OLD),literal=OLD,feature='text_overlay')]))
                messages=self.send(sid,pid,COMMAND,answer())
                self.assertFalse(any('尚未套用' in m.get('card',{}).get('text','') for m in messages))
                saved=self.stored(sid)['plan']
                self.assertEqual(saved['literal_texts'],[NEW]);self.assertEqual(saved['user_prompt'],printed(NEW))
                res=self.client.post(self.url(sid,'/render'),json=dict(user=stories.support.USER,photo_ids=[pid]))
                self.assertEqual(res.status_code,202,res.get_json())
                params=self.enqueued[-1][1]['acts'][0]['params']
                self.assertEqual(params['literal_texts'],[NEW]);self.assertNotIn(OLD,params['user_prompt'])
                from photo_agent.text_overlay import editor_instruction,qa_instruction
                image_analysis={'user_instruction':{'literal_texts':params['literal_texts']}}
                self.assertIn(NEW,editor_instruction(image_analysis));self.assertIn(NEW,qa_instruction(image_analysis))
                self.assertNotIn(OLD,qa_instruction(image_analysis))

    def test_ratio_missing_choices_can_be_answered_once_and_update_real_settings(self):
        sid=self.new_session();pid=self.ready_photo(sid)['photo_id']
        q=question('改變aspect ratio',reply='想改成哪個長寬比？',kind='content',options=[])
        fixed=copy.deepcopy(q);fixed['options']=['1:1','3:4','16:9']
        with patch.object(chat,'call_model',side_effect=[q,fixed]) as model:
            res=self.client.post(self.url(sid,'/photos/'+pid+'/ask'),json=dict(user=stories.support.USER,text='改變aspect ratio'))
        self.assertEqual(res.status_code,200,res.get_json());self.assertEqual(model.call_count,2)
        question_message=next(m for m in res.get_json()['messages'] if m.get('card',{}).get('type')=='llm_reply')
        self.assertEqual(question_message['card']['options'],['1:1','3:4','16:9'])
        result=stories.response(patch_value={'aspect_ratio':'3:4'})
        self.send(sid,pid,'3:4',result,answer_to=question_message['id'])
        self.assertEqual(self.stored(sid)['plan']['aspect_ratio'],'3:4')

if __name__=='__main__':unittest.main()
