"""Review-only mode must retain Stage 2.0 handoff without conversation bindings."""
from unittest.mock import patch
import unittest
from tests import test_analyze_preview_migration_spec as migration
from tests.test_chat_routes import chat, USER

class ReviewOnlyHandoff(unittest.TestCase):
    setUp=migration.AnalyzePreviewMigrationAcceptance.setUp
    start_preview=migration.AnalyzePreviewMigrationAcceptance.start_preview
    snapshot=migration.AnalyzePreviewMigrationAcceptance.snapshot
    submit=migration.AnalyzePreviewMigrationAcceptance.submit

    def test_ready_preview_is_reused_without_conversation_call_or_pending_bindings(self):
        preview=self.start_preview();self.snapshot(preview)
        with patch.object(chat.chat_queue,'run',side_effect=AssertionError('Upload must not call a chat model')):
            response=self.submit(preview)
        self.assertEqual(response.status_code,202,response.get_json())
        params=self.enqueue_calls[-1][1]['acts'][0]['params']
        self.assertIn('precomputed_preview',params)
        self.assertNotIn('preview_wish_bindings',params)
        self.assertEqual(params['stage'],'analyze')
        self.assertEqual(params['precomputed_preview']['binding']['preview_job_id'],preview['preview_job_id'])

if __name__=='__main__':unittest.main()
