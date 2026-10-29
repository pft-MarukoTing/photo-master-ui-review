"""Product acceptance for session chat, independent of upload chat_review."""
import copy
import json
import unittest
from unittest.mock import patch
import config
from photo_agent import chat_assistant as assistant, chat_plan, session_request
from tests import test_chat_routes as support
from tests.test_chat_assistant import analysis


def semantic(source,target='',action=None,kind='image',owner=False,literal='',feature='atmosphere'):
    return dict(source=source,target=target,action=action or source,complete=True,target_kind=kind,
        owner_required=owner,scope='edit',feature=feature,part='',intent='set',value='',literal_text=literal,constraints=[])


def response(reply='已記下',patch_value=None,action='none',options=(),opened='',lines=False,request=None):
    return dict(reply=reply,action=action,patch_json=json.dumps(patch_value,ensure_ascii=False) if patch_value is not None else '',
        options=list(options),lines=lines,your_pick=False,open=opened,request=request or dict(
            kind='none',line='',semantics=[],question_kind='',answer_mode=''))


def edit(line,values=None,extra=None):
    return response(patch_value=dict(user_prompt=line,**(extra or {})),request=dict(kind='edit',line=line,
        semantics=values if values is not None else [semantic(line)],question_kind='',answer_mode=''))


def question(original,reply='你想將它作為圖片文字，還是比例？',kind='meaning',options=('加入圖片文字','指定長寬比'),mode='choices',values=()):
    return response(reply=reply,options=options,request=dict(kind='clarify',line=original,semantics=list(values),
        question_kind=kind,answer_mode=mode if kind!='person' else ''))


def user(text):return dict(role='user',text=text,card=dict(type='ask',photo_id='p1'))


def follow(original,reply):
    return [user(original),dict(role='assistant',id='q1',card=dict(type='llm_reply',text=reply['reply'],
        question_kind=reply['question_kind'],clarification=reply['clarification'],options=reply['options']))]


class SessionRequestStories(unittest.TestCase):
    def ask(self,values,history=None,plan=None,status='ready',screen=None):
        calls=[];values=iter(values)
        def model(context):calls.append(context);return next(values)
        result=assistant.ask(analysis(),plan or chat_plan.default_plan(),status,history or [user('請處理')],
            screen=screen,model=model)
        return result,calls

    def test_7777_clarifies_then_saves_complete_printed_text_in_analysis_and_result_conversation(self):
        for after_render in (False,True):
            h=([dict(role='assistant',card=dict(type='render_done',note='完成'))] if after_render else [])+[user('7777')]
            q,calls=self.ask([question('7777')],history=h)
            self.assertIsNone(q['proposal']);self.assertEqual(q['question_kind'],'meaning');self.assertEqual(q['options'],['加入圖片文字','指定長寬比'])
            h=follow('7777',q)+[user('加入圖片文字')]
            line='在照片上加入文字「7777」'
            result,_=self.ask([edit(line,[semantic(line,'照片','加入文字「7777」',literal='7777',feature='text_overlay')])],history=h)
            self.assertEqual(result['proposal']['patch']['user_prompt'],line)
            self.assertEqual(result['proposal']['patch']['literal_texts'],['7777'])
            self.assertFalse(result['render']);self.assertTrue(result['proposal']['direct'])

    def test_weather_answer_internal_field_failure_is_repaired_by_llm_with_precise_feedback(self):
        q,_=self.ask([question('下雨天',reply='要雨天氛圍，還是加入雨絲？',options=['營造雨天氛圍','加入雨絲'])])
        bad=edit('營造雨天氛圍',[semantic('營造雨天氛圍','照片','營造雨天氛圍')])
        goodline='將照片調整為雨天氛圍'
        good=edit(goodline,[semantic(goodline,'照片','調整為雨天氛圍')])
        result,calls=self.ask([bad,good],history=follow('下雨天',q)+[user('營造雨天氛圍')])
        self.assertEqual(len(calls),2);self.assertIn('semantic_target_not_grounded',calls[1])
        self.assertIn('semantics[0].target',calls[1]);self.assertIn('下雨天',calls[1]);self.assertIn('營造雨天氛圍',calls[1])
        self.assertEqual(result['proposal']['patch']['user_prompt'],goodline)

    def test_windmill_question_stores_raw_input_not_question_or_hypothetical_semantics(self):
        text='要在背景新增風車，還是做成風車意象風格？'
        bad=question(text,reply=text,options=['背景新增風車','做成風車意象風格'],
            values=[semantic('風車','照片場景','新增或轉為風車意象')])
        q,calls=self.ask([bad],history=[user('風車')])
        self.assertEqual(len(calls),1)
        self.assertEqual(q['clarification'],dict(line='風車',original='風車',question=text,semantics=[]))
        self.assertIsNone(q['proposal'])
        self.assertIn('SEMANTIC FIELD RULES',calls[0])
        self.assertIn('EXACT CONTIGUOUS',calls[0])

    def test_windmill_selected_answer_completes_in_analysis_and_result_chat_with_existing_wishes(self):
        for rendered in (False,True):
            plan=chat_plan.default_plan();plan['user_prompt']='背景改成學校' if rendered else ''
            q,_=self.ask([question('風車',reply='背景新增風車，還是風車風格？',options=['背景新增風車','風車風格'])],history=[user('風車')])
            h=follow('風車',q)+[user('背景新增風車')]
            if rendered:h.insert(0,dict(role='assistant',card=dict(type='render_done',note='完成')))
            windmill='在照片背景新增一座風車'
            line=(plan['user_prompt']+'；' if rendered else '')+windmill
            values=([semantic('背景改成學校','背景','改成學校')] if rendered else [])+[semantic(windmill,'照片背景','新增一座風車')]
            result,calls=self.ask([edit(line,values)],history=h,plan=plan)
            self.assertEqual(len(calls),1);self.assertFalse(result['render'])
            self.assertEqual(result['proposal']['patch']['user_prompt'],line)
            context=json.loads(calls[0].split('PENDING CLARIFICATION: ',1)[1].split('\nCONVERSATION',1)[0])
            self.assertEqual(context['original'],'風車');self.assertEqual(context['answer'],'背景新增風車')
            self.assertEqual(context['question'],q['reply'])

    def test_legacy_windmill_question_recovers_raw_input_without_mutating_history(self):
        q=dict(reply='要新增風車還是風車風格？',question_kind='meaning',options=['背景新增風車','風車風格'],
            clarification=dict(line='要新增風車還是風車風格？',semantics=[semantic('風車','照片場景','新增或轉為風車意象')]))
        h=follow('風車',q)+[user('背景新增風車')];before=copy.deepcopy(h)
        ctx=session_request.pending(h)
        self.assertEqual(ctx['line'],'風車');self.assertEqual(ctx['semantics'],[])
        self.assertEqual(ctx['original'],'風車');self.assertEqual(ctx['answer'],'背景新增風車')
        self.assertEqual(h,before)
        line='在照片背景新增一座風車'
        result,_=self.ask([edit(line,[semantic(line,'照片背景','新增一座風車')])],history=h)
        self.assertEqual(result['proposal']['patch']['user_prompt'],line)

    def test_windmill_action_and_source_errors_are_repaired_with_exact_field_feedback(self):
        line='在照片背景新增一座風車'
        good=edit(line,[semantic(line,'照片背景','新增一座風車')])
        for field,wrong,code in [('action','放置風車','semantic_span_not_grounded'),
                                 ('source','在背景放置風車','semantic_source_not_grounded')]:
            bad=copy.deepcopy(good);bad['request']['semantics'][0][field]=wrong
            result,calls=self.ask([bad,good],history=[user('風車'),user('背景新增風車')])
            self.assertEqual(len(calls),2);self.assertIn(code,calls[1]);self.assertIn('semantics[0].'+field,calls[1])
            self.assertEqual(result['proposal']['patch']['user_prompt'],line)

    def test_windmill_two_invalid_outputs_keep_plan_and_log_second_failure_field(self):
        line='在照片背景新增一座風車'
        first=edit(line,[semantic(line,'照片背景','放置風車')])
        second=edit(line,[semantic('在背景放置風車','背景','放置風車')])
        recovery=question(line,reply='風車要放在背景的哪個位置？',kind='content',options=['背景左側','背景右側'])
        for enabled in (False,True):
            with self.subTest(patch_continue=enabled):
                plan=chat_plan.default_plan();before=copy.deepcopy(plan)
                with patch.object(config,'ENABLE_CHAT_PATCH_CONTINUE',enabled), \
                     self.assertLogs(session_request.logger,level='WARNING') as logs:
                    result,calls=self.ask([first,second,recovery] if enabled else [first,second],
                        history=[user('背景新增風車')],plan=plan)
                self.assertEqual(len(calls),3 if enabled else 2)
                self.assertIsNone(result['proposal']);self.assertEqual(plan,before)
                self.assertIn('semantic_source_not_grounded field=semantics[0].source','\n'.join(logs.output))
                if enabled:
                    self.assertIn('DISPLAY RECOVERY ONLY',calls[-1])
                    self.assertEqual(result['reply'],recovery['reply'])
                    self.assertEqual(result['options'],recovery['options'])

    def test_person_question_still_requires_valid_complete_operation(self):
        bad=question('眼鏡拿掉',reply='要拿掉誰的眼鏡？',kind='person',options=[],
            values=[semantic('眼鏡拿掉','眼鏡','移除','object',True)])
        fixed=question('眼鏡拿掉',reply='要拿掉誰的眼鏡？',kind='person',options=[],
            values=[semantic('眼鏡拿掉','眼鏡','拿掉','object',True)])
        result,calls=self.ask([bad,fixed],history=[user('眼鏡拿掉')])
        self.assertEqual(len(calls),2);self.assertIn('semantic_span_not_grounded',calls[1])
        self.assertEqual(result['clarification']['line'],'眼鏡拿掉')
        self.assertEqual(result['clarification']['semantics'][0]['action'],'拿掉')

    def test_compatible_additions_keep_old_wishes_once(self):
        plan=chat_plan.default_plan();plan['user_prompt']='背景改成學校；拿粉筆'
        final='背景改成學校；拿粉筆；拿教科書'
        values=[semantic('背景改成學校','背景','改成學校',feature='background'),
            semantic('拿粉筆','粉筆','拿',kind='object',owner=True,feature='held_object'),
            semantic('拿教科書','教科書','拿',kind='object',owner=True,feature='held_object')]
        result,_=self.ask([edit(final,values)],history=[user('拿粉筆'),user('還有教科書')],plan=plan)
        prompt=result['proposal']['patch']['user_prompt'];self.assertEqual(prompt,final);self.assertEqual(prompt.count('拿粉筆'),1)

    def test_two_people_keep_independent_confirmed_wishes(self):
        plan=chat_plan.default_plan();plan['user_prompt']='左邊女性頭髮改成金色'
        final=plan['user_prompt']+'；右邊女孩頭髮改成銀色'
        result,_=self.ask([edit(final,[semantic(plan['user_prompt'],'左邊女性','頭髮改成金色','person',True),
            semantic('右邊女孩頭髮改成銀色','右邊女孩','頭髮改成銀色','person',True)])],plan=plan,history=[user('右邊女孩頭髮改成銀色')])
        prompt=result['proposal']['patch']['user_prompt'];self.assertIn('金色',prompt);self.assertIn('銀色',prompt)
        self.assertIn('右邊女孩',prompt)

    def test_cancel_all_clears_text_contract_and_can_undo_without_resurrecting_wishes(self):
        plan=chat_plan.default_plan();plan.update(user_prompt='在照片上加入文字「7777」',literal_texts=['7777'])
        h=[dict(role='assistant',card=dict(type='llm_reply',options=['取消全部','保留'])),user('取消全部')]
        result,_=self.ask([edit('',[])],plan=plan,history=h)
        proposed=result['proposal'];self.assertEqual(proposed['patch']['user_prompt'],'')
        self.assertEqual(proposed['patch']['literal_texts'],[])
        changed,error=chat_plan.apply_plan_patch(plan,proposed['patch']);self.assertIsNone(error)
        original,error=chat_plan.apply_plan_patch(changed,proposed['undo']);self.assertIsNone(error)
        self.assertEqual(original['user_prompt'],plan['user_prompt']);self.assertEqual(original['literal_texts'],['7777'])

    def test_per_person_face_request_is_still_applied_with_its_prompt(self):
        line='左邊女性臉部左右平衡'
        result,_=self.ask([edit(line,[semantic(line,'左邊女性','臉部左右平衡','person',True)],
            extra={'face_requests':{'0:face_symmetry':True}})])
        self.assertEqual(result['proposal']['patch']['face_requests'],{'0:face_symmetry':True})
        self.assertIn('左邊女性',result['proposal']['patch']['user_prompt'])

    def test_literal_change_is_rejected_and_llm_must_restore_exact_characters(self):
        q,_=self.ask([question('6x8')])
        wrong='在照片上加入文字「6×8」';correct='在照片上加入文字「6x8」'
        result,calls=self.ask([edit(wrong,[semantic(wrong,'照片','加入文字「6×8」',literal='6×8',feature='text_overlay')]),
            edit(correct,[semantic(correct,'照片','加入文字「6x8」',literal='6x8',feature='text_overlay')])],
            history=follow('6x8',q)+[user('加入圖片文字')])
        self.assertIn('changed_literal_text',calls[1]);self.assertEqual(result['proposal']['patch']['literal_texts'],['6x8'])

    def test_canonical_printed_text_is_not_changed_by_legacy_grouping(self):
        literal='Hello； World'
        line='在照片上加入文字「'+literal+'」'
        result,_=self.ask([edit(line,[semantic(line,'照片','加入文字「'+literal+'」',literal=literal,feature='text_overlay')])],history=[user(line)])
        self.assertEqual(result['proposal']['patch']['user_prompt'],line)
        self.assertEqual(result['proposal']['patch']['literal_texts'],[literal])

    def test_settings_panels_render_close_query_and_caption_suggestions_keep_original_behaviors(self):
        settings,_=self.ask([response(patch_value=dict(aspect_ratio='1:1'))]);self.assertEqual(settings['proposal']['patch']['aspect_ratio'],'1:1')
        panel,_=self.ask([response(opened='設定')]);self.assertEqual(panel['open'],'settings')
        render,_=self.ask([response(action='render')]);self.assertTrue(render['render']);self.assertIsNone(render['proposal'])
        closed,_=self.ask([response(action='close')]);self.assertTrue(closed['close'])
        caption,_=self.ask([response(reply='想放哪一句？',options=['晴天日記','週末時光'],lines=True)])
        self.assertTrue(caption['lines']);self.assertIsNone(caption['proposal'])
        query,_=self.ask([response(reply='分析建議先提亮主體。')]);self.assertIsNone(query['proposal'])

    def test_missing_choices_are_repaired_but_never_turn_into_a_guessed_edit(self):
        bad=question('下雨天',reply='想改成雨天氛圍，還是加入雨絲？',options=[])
        fixed=question('下雨天',reply=bad['reply'],options=['雨天氛圍','加入雨絲'])
        result,calls=self.ask([bad,fixed]);self.assertEqual(len(calls),2);self.assertEqual(result['options'],fixed['options'])
        result,calls=self.ask([bad,edit('雨天氛圍')]);self.assertIsNone(result['proposal']);self.assertFalse(result['render'])

    def test_person_and_content_questions_are_explicit(self):
        original='眼鏡拿掉';values=[semantic(original,'眼鏡','拿掉','object',True)]
        q,_=self.ask([question(original,reply='要拿掉哪一位的眼鏡？',kind='person',options=[],values=values)])
        self.assertEqual(q['question_kind'],'person');self.assertEqual(q['options'],[])
        q,_=self.ask([question('加上名字',reply='要放什麼名字？',kind='content',options=[],mode='free_text')])
        self.assertEqual(q['question_kind'],'content');self.assertEqual(q['options'],[])

    def test_invalid_repair_never_applies_and_only_recovers_display_when_enabled(self):
        bad=edit('營造雨天氛圍',[semantic('營造雨天氛圍','照片','營造雨天氛圍')])
        recovery=question('營造雨天氛圍',reply='雨天氛圍想呈現哪一種風格？',options=['柔和','戲劇感'])
        for enabled in (False,True):
            with self.subTest(patch_continue=enabled),patch.object(config,'ENABLE_CHAT_PATCH_CONTINUE',enabled):
                result,calls=self.ask([bad,copy.deepcopy(bad),recovery] if enabled else [bad,copy.deepcopy(bad)])
                self.assertEqual(len(calls),3 if enabled else 2)
                self.assertIsNone(result['proposal']);self.assertFalse(result['render'])
                if enabled:
                    self.assertIn('DISPLAY RECOVERY ONLY',calls[-1])
                    self.assertEqual(result['reply'],recovery['reply'])
                    self.assertEqual(result['clarification']['application_status'],'not_applied')
                else:
                    self.assertNotIn('question_kind',result)

    def test_deadline_is_shared_by_initial_call_and_repair(self):
        bad=edit('營造雨天氛圍',[semantic('營造雨天氛圍','照片','營造雨天氛圍')])
        good=edit('將照片改為雨天氛圍')
        with patch.object(assistant.time,'time',side_effect=[100,100,130]),patch.object(assistant,'call_model',side_effect=[bad,good]) as model:
            result=assistant.ask(analysis(),chat_plan.default_plan(),'ready',[user('營造雨天氛圍')])
        self.assertEqual([c.kwargs['timeout'] for c in model.call_args_list],[45,15]);self.assertIsNotNone(result['proposal'])

    def test_expired_deadline_starts_no_model_call(self):
        with patch.object(assistant,'call_model') as model:
            with self.assertRaises(TimeoutError):assistant.ask(analysis(),chat_plan.default_plan(),'ready',[user('7777')],deadline=0)
        model.assert_not_called()

    def test_session_schema_does_not_mutate_review_schema_or_base_schema(self):
        base=copy.deepcopy(assistant.RESPONSE_SCHEMA);review=copy.deepcopy(assistant.REVIEW_SCHEMA)
        expanded=session_request.schema(assistant.RESPONSE_SCHEMA)
        self.assertIn('request',expanded['schema']['required'])
        self.assertEqual(assistant.RESPONSE_SCHEMA,base);self.assertEqual(assistant.REVIEW_SCHEMA,review)


class SessionRouteStories(unittest.TestCase):
    setUp=support.ChatRoutesTest.setUp
    url=support.ChatRoutesTest.url
    new_session=support.ChatRoutesTest.new_session
    add_photos=support.ChatRoutesTest.add_photos
    status=support.ChatRoutesTest.status
    ready_photo=support.ChatRoutesTest.ready_photo

    def send(self,sid,pid,text,value,**kwargs):
        with patch.object(assistant,'call_model',lambda context,**unused:copy.deepcopy(value)):
            result=self.client.post(self.url(sid,'/photos/'+pid+'/ask'),json=dict(user=support.USER,text=text,**kwargs))
        self.assertEqual(result.status_code,200,result.get_json());return result.get_json()['messages']

    def stored(self,sid):return support.chat_session.load_session(support.USER,sid)['photos'][0]

    def test_windmill_selected_answer_repairs_and_reaches_stored_plan_and_render_task(self):
        sid=self.new_session();pid=self.ready_photo(sid)['photo_id']
        text='要在背景新增風車，還是做成風車意象風格？'
        q=self.send(sid,pid,'風車',question(text,reply=text,options=['背景新增風車','風車意象風格'],
            values=[semantic('風車','照片場景','新增或轉為風車意象')]))[-1]
        self.assertEqual(q['card']['clarification']['original'],'風車')
        self.assertEqual(q['card']['clarification']['question'],text)
        self.assertEqual(q['card']['clarification']['semantics'],[])
        line='在照片背景新增一座風車'
        invalid=edit(line,[semantic(line,'照片背景','放置風車')])
        valid=edit(line,[semantic(line,'照片背景','新增一座風車')])
        with patch.object(assistant,'call_model',side_effect=[invalid,valid]) as model:
            res=self.client.post(self.url(sid,'/photos/'+pid+'/ask'),json=dict(user=support.USER,
                text='背景新增風車',answer_to=q['id']))
        self.assertEqual(res.status_code,200,res.get_json());self.assertEqual(model.call_count,2)
        self.assertEqual(self.stored(sid)['plan']['user_prompt'],line)
        reply=next(m['card'] for m in res.get_json()['messages'] if m.get('card',{}).get('type')=='llm_reply')
        self.assertNotIn('尚未套用',reply['text'])
        self.assertIn('semantic_span_not_grounded',model.call_args_list[1].args[0])
        res=self.client.post(self.url(sid,'/render'),json=dict(user=support.USER,photo_ids=[pid]))
        self.assertEqual(res.status_code,202,res.get_json())
        self.assertEqual(self.enqueued[-1][1]['acts'][0]['params']['user_prompt'],line)

    def test_actual_session_question_answer_and_task_delivery_both_preview_modes(self):
        for enabled in (False,True):
            with patch.object(support.chat.agent_config,'ENABLE_CHAT_SUBJECT_PREVIEW',enabled):
                sid=self.new_session();photo=self.ready_photo(sid);pid=photo['photo_id']
                messages=self.send(sid,pid,'7777',question('7777'))
                q=messages[-1];self.assertEqual(q['card']['question_kind'],'meaning')
                self.assertEqual(self.stored(sid)['plan']['user_prompt'],'')
                line='在照片上加入文字「7777」'
                messages=self.send(sid,pid,'加入圖片文字',edit(line,[semantic(line,'照片','加入文字「7777」',literal='7777',feature='text_overlay')]),answer_to=q['id'])
                self.assertEqual(messages[0]['card']['answers_question_id'],q['id'])
                self.assertEqual(self.stored(sid)['plan']['user_prompt'],line)
                self.assertEqual(self.stored(sid)['plan']['literal_texts'],['7777'])
                res=self.client.post(self.url(sid,'/render'),json=dict(user=support.USER,photo_ids=[pid]))
                self.assertEqual(res.status_code,202,res.get_json())
                params=self.enqueued[-1][1]['acts'][0]['params']
                self.assertEqual(params['user_prompt'],line);self.assertEqual(params['literal_texts'],['7777'])

    def test_wish_added_during_analysis_survives_analysis_completion(self):
        sid=self.new_session();photo=self.add_photos(sid)[0];pid=photo['photo_id']
        self.send(sid,pid,'背景改成學校',edit('背景改成學校'))
        self.assertEqual(self.stored(sid)['status'],'analyzing')
        self.jobs[photo['analyze_job_id']]['status']='success';self.status(sid)
        self.assertEqual(self.stored(sid)['plan']['user_prompt'],'背景改成學校')

    def test_question_from_another_photo_is_rejected(self):
        sid=self.new_session();first=self.ready_photo(sid)['photo_id'];second=self.ready_photo(sid)['photo_id']
        q=self.send(sid,first,'7777',question('7777'))[-1]
        with patch.object(assistant,'call_model') as model:
            res=self.client.post(self.url(sid,'/photos/'+second+'/ask'),json=dict(user=support.USER,text='加入文字',answer_to=q['id']))
        self.assertEqual(res.status_code,409);model.assert_not_called()

    def test_stale_or_other_photo_question_answer_is_rejected_without_calling_model(self):
        sid=self.new_session();pid=self.ready_photo(sid)['photo_id']
        q=self.send(sid,pid,'7777',question('7777'))[-1]
        self.send(sid,pid,'先不改',response(reply='好，先不改。'))
        with patch.object(assistant,'call_model') as model:
            res=self.client.post(self.url(sid,'/photos/'+pid+'/ask'),json=dict(user=support.USER,text='加入文字',answer_to=q['id']))
        self.assertEqual(res.status_code,409);model.assert_not_called()

    def test_edit_during_render_keeps_submitted_snapshot(self):
        sid=self.new_session();pid=self.ready_photo(sid)['photo_id'];p=self.stored(sid)
        p['status']='rendering';p['pending_render']=dict(plan=copy.deepcopy(p['plan']),job_ids=['existing-job'])
        frozen=copy.deepcopy(p['pending_render']);support.chat_session.save_photo_state(support.USER,sid,p)
        self.send(sid,pid,'背景改成學校',edit('背景改成學校'))
        saved=self.stored(sid);self.assertEqual(saved['pending_render'],frozen)
        self.assertEqual(saved['plan']['user_prompt'],'背景改成學校');self.assertEqual(saved['status'],'rendering')

    def test_concurrent_plan_change_is_not_overwritten_by_model_full_prompt(self):
        sid=self.new_session();pid=self.ready_photo(sid)['photo_id']
        def model(context,**kwargs):
            current=self.stored(sid);current['plan']['user_prompt']='保留原本背景'
            support.chat_session.save_photo_plan(support.USER,sid,pid,current['plan'])
            return edit('背景改成學校')
        with patch.object(assistant,'call_model',model):
            res=self.client.post(self.url(sid,'/photos/'+pid+'/ask'),json=dict(user=support.USER,text='背景改成學校'))
        self.assertEqual(res.status_code,200);self.assertEqual(self.stored(sid)['plan']['user_prompt'],'保留原本背景')
        self.assertIsNone(res.get_json()['messages'][-1]['card']['proposal'])

if __name__=='__main__':unittest.main()
