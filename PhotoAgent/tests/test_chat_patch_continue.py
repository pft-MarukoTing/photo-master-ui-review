"""Spec-first acceptance: terminal ask failures keep dialogue, never a rejected patch."""
import copy,json,unittest
from unittest.mock import patch
import config
from photo_agent import chat_assistant as chat,chat_plan
from tests import test_session_request_spec as stories
from tests.test_chat_assistant import analysis

ORIGINAL='請把衣服換成適合秋天的樣子'
OPTIONS=[' 保留原款式 ','換成暖色系','換成暖色系','選擇一套適合這張照片且不改變人物身分的秋季服裝','我想自己指定','其他風格']
OLD_ERROR='這項需求暫時未能整理完成，尚未套用；請稍後再試。'

def invalid(question=True):
    r=stories.question(ORIGINAL,reply='想保留款式，還是換成其他秋季服裝？',kind='content',options=OPTIONS)
    r.update(patch_json='{broken',action='render',open='設定')
    if not question:r['request']['kind']='edit';r['reply']='已幫你修改好了。'
    return r

class ContinueStories(unittest.TestCase):
    def ask(self,answers,enabled=True,history=None):
        calls=[];plan=chat_plan.default_plan();before=copy.deepcopy(plan)
        def model(context):
            calls.append(context)
            v=answers[min(len(calls)-1,len(answers)-1)]
            if isinstance(v,Exception):raise v
            return copy.deepcopy(v)
        with patch.object(config,'ENABLE_CHAT_PATCH_CONTINUE',enabled,create=True):
            result=chat.ask(analysis(),plan,'ready',history or [stories.user(ORIGINAL)],model=model)
        self.assertEqual(plan,before)
        return result,calls

    def test_flag_true_preserves_original_question_and_options_without_apply(self):
        first=invalid();second=invalid();second['reply']='不應取代最初的問題？'
        out,calls=self.ask([first,second])
        self.assertEqual(len(calls),2)
        self.assertEqual(out['reply'],first['reply']);self.assertEqual(out['options'],OPTIONS)
        self.assertIsNone(out['proposal']);self.assertFalse(out['render']);self.assertFalse(out['close']);self.assertEqual(out['open'],'')
        self.assertTrue(out['clarification']['patch_continue'])
        self.assertEqual(out['clarification']['original'],ORIGINAL)

    def test_flag_false_keeps_exact_existing_terminal_failure(self):
        out,calls=self.ask([invalid(),invalid()],False)
        self.assertEqual(out['reply'],OLD_ERROR);self.assertEqual(out['options'],[])
        self.assertEqual(len(calls),2);self.assertIsNone(out['proposal'])

    def test_no_question_requests_llm_display_recovery_not_success_statement(self):
        recovered=stories.question(ORIGINAL,reply='衣服想換成哪一種風格？',kind='content',options=['休閒','正式'])
        out,calls=self.ask([invalid(False),invalid(False),recovered])
        self.assertEqual(len(calls),3);self.assertIn('DISPLAY RECOVERY ONLY',calls[-1])
        self.assertEqual(out['reply'],recovered['reply']);self.assertEqual(out['options'],recovered['options'])
        self.assertIsNone(out['proposal'])

    def test_recovery_api_failure_retains_typed_input_without_fake_options(self):
        out,calls=self.ask([invalid(False),invalid(False),TimeoutError()])
        self.assertEqual(len(calls),3);self.assertEqual(out['options'],[])
        self.assertNotIn(OLD_ERROR,out['reply']);self.assertNotIn('已幫你修改好了',out['reply'])
        self.assertIsNone(out['proposal']);self.assertEqual(out['answer_mode'],'free_text')

    def test_repeated_invalid_answer_keeps_original_and_confirmed_facts(self):
        first,_=self.ask([invalid(),invalid()])
        history=stories.follow(ORIGINAL,first)+[stories.user('換成暖色系')]
        bad=invalid();bad['reply']='要偏橘色還是棕色？';bad['options']=['偏橘色','偏棕色']
        second,calls=self.ask([bad,bad],history=history)
        self.assertEqual(second['clarification']['original'],ORIGINAL)
        self.assertEqual(second['clarification']['answers'][-1]['answer'],'換成暖色系')
        self.assertEqual(second['options'],bad['options'])
        self.assertNotIn(OLD_ERROR,second['reply'])

    def test_normal_valid_question_settings_and_render_are_identical_for_both_flags(self):
        for r in [stories.edit('背景改成學校'),stories.question('777'),stories.response(patch_value={'aspect_ratio':'3:4'}),stories.response(action='render')]:
            with self.subTest(r=r):
                on,on_calls=self.ask([r],True);off,off_calls=self.ask([r],False)
                self.assertEqual(on,off);self.assertEqual(on_calls,off_calls)

    def test_confirmed_answers_survive_history_window_and_free_text_recovery(self):
        first,_=self.ask([invalid(),invalid()])
        history=stories.follow(ORIGINAL,first)+[stories.user('換成暖色系')]
        second,_=self.ask([invalid(),invalid()],history=history)
        # Only the current question and answer remain in the model's last 20
        # turns, but the original and earlier selected fact remain in pending.
        history=[stories.user('其他對話') for _ in range(22)]+[
            dict(role='assistant',card=dict(type='llm_reply',text=second['reply'],options=second['options'],
                question_kind=second['question_kind'],clarification=second['clarification'])),stories.user('偏棕色')]
        third,calls=self.ask([invalid(),invalid()],history=history)
        self.assertEqual(third['clarification']['original'],ORIGINAL)
        self.assertEqual([v['answer'] for v in third['clarification']['answers']],['換成暖色系','偏棕色'])
        self.assertIn(ORIGINAL,calls[0]);self.assertIn('換成暖色系',calls[0])

    def test_malformed_option_array_is_not_partially_filtered(self):
        first=invalid();first['options']=['可用',None,'仍是原字']
        recovered=stories.question(ORIGINAL,reply='你想選哪種風格？',kind='content',options=['暖色','冷色'])
        out,calls=self.ask([first,first,recovered])
        self.assertEqual(len(calls),3);self.assertEqual(out['options'],recovered['options'])

    def test_failed_repair_api_call_can_still_preserve_initial_question(self):
        first=invalid();out,calls=self.ask([first,TimeoutError()])
        self.assertEqual(len(calls),2);self.assertEqual(out['reply'],first['reply'])
        self.assertEqual(out['options'],OPTIONS)

    def test_already_selected_question_is_not_repeated_as_a_retry(self):
        first,_=self.ask([invalid(),invalid()])
        history=stories.follow(ORIGINAL,first)+[stories.user('換成暖色系')]
        next_question=stories.question(ORIGINAL,reply='要偏橘色還是棕色？',kind='content',options=['偏橘色','偏棕色'])
        out,calls=self.ask([invalid(),invalid(),next_question],history=history)
        self.assertEqual(len(calls),3);self.assertEqual(out['reply'],next_question['reply'])
        self.assertIn('UNAPPLIED CHAT CONTINUATION',calls[0])
        self.assertEqual(out['clarification']['answers'][-1]['answer'],'換成暖色系')

class ContinueRoutes(unittest.TestCase):
    setUp=stories.SessionRouteStories.setUp
    url=stories.SessionRouteStories.url
    new_session=stories.SessionRouteStories.new_session
    ready_photo=stories.SessionRouteStories.ready_photo
    add_photos=stories.SessionRouteStories.add_photos
    status=stories.SessionRouteStories.status
    stored=stories.SessionRouteStories.stored

    def test_failed_patch_then_answer_to_valid_patch_changes_plan_only_on_second_turn(self):
        sid=self.new_session();pid=self.ready_photo(sid)['photo_id'];before=copy.deepcopy(self.stored(sid)['plan'])
        with patch.object(config,'ENABLE_CHAT_PATCH_CONTINUE',True,create=True),patch.object(chat,'call_model',side_effect=[invalid(),invalid()]) as model:
            res=self.client.post(self.url(sid,'/photos/'+pid+'/ask'),json={'user':stories.support.USER,'text':ORIGINAL})
        self.assertEqual(res.status_code,200,res.get_json());self.assertEqual(model.call_count,2)
        self.assertEqual(self.stored(sid)['plan'],before)
        q=next(m for m in res.get_json()['messages'] if m.get('card',{}).get('type')=='llm_reply')
        self.assertEqual(q['card']['options'],OPTIONS);self.assertTrue(q['card']['clarification']['patch_continue'])
        final='將人物衣服改成秋天的暖色系'
        with patch.object(config,'ENABLE_CHAT_PATCH_CONTINUE',True,create=True),patch.object(chat,'call_model',return_value=stories.edit(final)) as model:
            res=self.client.post(self.url(sid,'/photos/'+pid+'/ask'),json={'user':stories.support.USER,'text':OPTIONS[0],'answer_to':q['id']})
        self.assertEqual(res.status_code,200,res.get_json());self.assertEqual(model.call_count,1)
        self.assertIn(ORIGINAL,model.call_args.args[0]);self.assertIn(OPTIONS[0],model.call_args.args[0])
        self.assertIn('"selected_option": true',model.call_args.args[0])
        self.assertEqual(self.stored(sid)['plan']['user_prompt'],final)

    def test_second_invalid_turn_remains_answerable_and_old_answer_is_stale(self):
        sid=self.new_session();pid=self.ready_photo(sid)['photo_id'];before=copy.deepcopy(self.stored(sid)['plan'])
        qs=[]
        for turn,text in enumerate([ORIGINAL,'換成暖色系']):
            body={'user':stories.support.USER,'text':text}
            if qs:body['answer_to']=qs[-1]['id']
            bad=invalid();bad['reply']='要偏橘色還是棕色？' if turn else bad['reply']
            bad['options']=['偏橘色','偏棕色'] if turn else OPTIONS
            with patch.object(config,'ENABLE_CHAT_PATCH_CONTINUE',True,create=True),patch.object(chat,'call_model',side_effect=[bad,bad]):
                res=self.client.post(self.url(sid,'/photos/'+pid+'/ask'),json=body)
            self.assertEqual(res.status_code,200,res.get_json())
            qs.append(next(m for m in res.get_json()['messages'] if m.get('card',{}).get('type')=='llm_reply'))
            self.assertNotIn(OLD_ERROR,qs[-1]['card']['text']);self.assertEqual(self.stored(sid)['plan'],before)
        self.assertEqual(qs[-1]['card']['clarification']['answers'][0]['answer'],'換成暖色系')
        with patch.object(chat,'call_model') as model:
            stale=self.client.post(self.url(sid,'/photos/'+pid+'/ask'),json={'user':stories.support.USER,'text':'偏棕色','answer_to':qs[0]['id']})
        self.assertEqual(stale.status_code,409);model.assert_not_called()
        with patch.object(chat,'call_model',return_value=stories.edit('將衣服改成秋天棕色系')):
            valid=self.client.post(self.url(sid,'/photos/'+pid+'/ask'),json={'user':stories.support.USER,'text':'偏棕色','answer_to':qs[-1]['id']})
        self.assertEqual(valid.status_code,200,valid.get_json())
        self.assertEqual(self.stored(sid)['plan']['user_prompt'],'將衣服改成秋天棕色系')

if __name__=='__main__':unittest.main()
