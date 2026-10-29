"""Configurable option bounds and complete follow-up answers (offline)."""
import copy
import unittest
from unittest.mock import patch

import config
from photo_agent import chat_assistant as assistant, chat_plan
from tests.test_chat_routes import chat
from tests import test_session_request_spec as stories
from tests.test_session_request_spec import question, follow, user, edit, semantic


class ChatOptionLengthTest(unittest.TestCase):
    def test_format_order_boundaries_and_four_choices(self):
        choices = ['中' * 200, ('English choice ' * 10).strip(), '混合 English 描述' * 5, '第四個', '第五個']
        self.assertEqual(assistant._options([' ', None, ' ' + choices[0] + ' ', choices[0], 'x' * 201] + choices[1:]), choices[:4])

    def test_configuration_is_shared_with_history_without_changing_labels(self):
        with patch.object(config, 'CHAT_OPTION_MAX_LEN', 60, create=True):
            value = 'x' * 60
            self.assertEqual(assistant._options([value, 'x' * 61]), [value])
            self.assertEqual(chat._review_history([dict(role='assistant', text='Choose?', options=[value])])[0]['options'], [value])
            with self.assertRaises(chat._RequestError):
                chat._review_history([dict(role='assistant', text='Choose?', options=['x' * 61])])
        self.assertEqual(chat.PROMPT_REVIEW_LABEL_MAX_LEN, 40)

    def test_legacy_literal_history_exception_is_preserved(self):
        literal = '把「' + '原文' * 150 + '」放到照片中'
        self.assertEqual(chat._review_history([dict(role='assistant', text='?', options=[literal])])[0]['options'], [literal])

    def test_long_choices_do_not_trigger_repair_and_followup_keeps_full_answer(self):
        harness = stories.SessionRequestStories()
        choices = ['將照片的背景改成柔和的雨天氛圍，保留人物原本的外觀與衣著',
                   'Add subtle rain streaks in the background while keeping the person and clothing unchanged']
        result, calls = harness.ask([question('下雨天', options=choices)])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['options'], choices)
        line = '將照片調整為雨天氛圍'
        answer, calls = harness.ask([edit(line, [semantic(line, '照片', '調整為雨天氛圍')])],
                                   history=follow('下雨天', result) + [user(choices[0])])
        self.assertIn(choices[0], calls[0])
        self.assertIn('下雨天', calls[0])
        self.assertEqual(answer['proposal']['patch']['user_prompt'], line)

    def test_review_keeps_complete_long_choices(self):
        choices = ['將衣服換成偏暖的米白色，保留原本的版型與布料細節',
                   'Change the clothing to navy blue while preserving its original shape and fabric texture']
        result = assistant.review_line('衣服換顏色', '', model=lambda context: dict(
            verdict='clarify', reply='想換成哪一種顏色？', line='', options=choices, question_kind='content'))
        self.assertTrue(result['ask'])
        self.assertEqual(result['options'], choices)

    def test_long_choices_do_not_allow_an_invalid_patch(self):
        harness = stories.SessionRequestStories()
        plan = chat_plan.default_plan()
        before = copy.deepcopy(plan)
        bad = edit('營造雨天氛圍', [semantic('營造雨天氛圍', '照片', '營造雨天氛圍')])
        bad['options'] = ['x' * 100, 'y' * 100]
        with patch.object(config, 'ENABLE_CHAT_PATCH_CONTINUE', False):
            result, calls = harness.ask([bad, copy.deepcopy(bad)], plan=plan)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(result['proposal'])
        self.assertEqual(plan, before)


    def test_too_long_choices_still_use_choice_repair(self):
        harness = stories.SessionRequestStories()
        bad = question('下雨天', options=['x' * 201, '加入雨絲'])
        fixed = question('下雨天', options=['改為柔和的雨天氛圍並保留人物原本的外觀與衣著', '加入雨絲'])
        result, calls = harness.ask([bad, fixed])
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['options'], fixed['options'])

    def test_flask_answer_to_keeps_long_choice_and_updates_stored_plan(self):
        harness = stories.SessionRouteStories()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        sid = harness.new_session()
        pid = harness.ready_photo(sid)['photo_id']
        choices = ['將照片的背景改成柔和的雨天氛圍，保留人物原本的外觀與衣著',
                   'Add subtle rain streaks in the background while keeping the person and clothing unchanged']
        q = harness.send(sid, pid, '下雨天', question('下雨天', options=choices))[-1]
        self.assertEqual(q['card']['options'], choices)
        line = '將照片調整為雨天氛圍'
        harness.send(sid, pid, choices[0], edit(line, [semantic(line, '照片', '調整為雨天氛圍')]), answer_to=q['id'])
        self.assertEqual(harness.stored(sid)['plan']['user_prompt'], line)
