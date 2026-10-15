import unittest

from photo_agent import preview_conversation


class ReviseTest(unittest.TestCase):
    # 「把牆壁改成木頭牆」因為有「改成」走到修正流程；模型回 no_change 卻順手附上
    # 紅磚牆那筆，仍然是沒改，不能變成「出了點問題：invalid_no_change」。
    def test_no_change_with_ids_is_still_no_change(self):
        state = {'clauses': [{'wish_id': 'note-1', 'request_id': 'note-1', 'status': 'not_person', 'text': '把牆壁改成紅磚牆'}]}
        raw = {'mode': 'no_change', 'wish_ids': ['note-1'], 'target_ids': [], 'reply': ''}
        self.assertIsNone(preview_conversation.revise(state, [], '把牆壁改成木頭牆', raw))


if __name__ == '__main__':
    unittest.main()
