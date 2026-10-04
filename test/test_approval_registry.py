import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from webot import policy, runtime_store, runtime_settings
from webot.approval_registry import approval_registry
from webot.permission_context import create_or_reuse_permission_request, resolve_permission_request


class RegistryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for module, name, value in [(policy, 'USER_FILES_DIR', root / 'users'),
            (runtime_settings, 'USER_FILES_DIR', root / 'users'),
            (runtime_store, 'DEFAULT_DB_PATH', root / 'runtime.db')]:
            p = patch.object(module, name, value); p.start(); self.addCleanup(p.stop)

    def test_remembered_action_is_session_scoped_and_does_not_expose_arguments(self):
        request = create_or_reuse_permission_request(user_id='alice', session_id='one',
            tool_name='web_fetch', args={'url': 'https://example.com/PRIVATE_VALUE'})
        resolve_permission_request(user_id='alice', approval_id=request.approval_id, action='approved', remember=True)
        runtime_store.update_tool_approval_status(request.approval_id, 'alice', status='used')
        runtime_store.record_tool_execution(request.approval_id, 'alice', status='success', detail='PRIVATE_RESULT')
        listing = approval_registry('alice', 'one')
        self.assertTrue(listing['records'][0]['remembered'])
        self.assertEqual(listing['records'][0]['execution']['status'], 'success')
        self.assertEqual(len(listing['saved_permissions']['actions']), 1)
        self.assertNotIn('PRIVATE_VALUE', json.dumps(listing))
        self.assertNotIn('PRIVATE_RESULT', json.dumps(listing))
        self.assertEqual(approval_registry('alice', 'two')['saved_permissions']['actions'], [])
        self.assertEqual(approval_registry('bob', 'one')['records'], [])

    def test_failed_keep_registration_is_denied_and_audited(self):
        request = create_or_reuse_permission_request(user_id='alice', session_id='one', tool_name='web_fetch', args={})
        with patch('webot.permission_context.remember_approval_in_policy', side_effect=ValueError('PRIVATE')):
            record = resolve_permission_request(user_id='alice', approval_id=request.approval_id, action='approved', remember=True)
        self.assertEqual(record.status, 'denied')
        entry = approval_registry('alice', 'one')['records'][0]
        self.assertEqual(entry['remember_error'], 'ValueError')
        self.assertFalse(entry['remembered'])

    def test_expired_pending_records_are_not_reported_as_waiting(self):
        runtime_store.create_tool_approval_request('alice', 'one', approval_id='approval-old', tool_name='read_file',
            args={}, request_reason='read', expiry_hours=-1)
        self.assertEqual(approval_registry('alice', 'one', status='pending')['records'], [])
        self.assertEqual(approval_registry('alice', 'one', status='expired')['records'][0]['status'], 'expired')
