"""Explicit live smoke: only dedicated fixtures, installed adapters and existing login.

CLAWCROSS_NATIVE_SESSION_INTEGRATION=1 enables model API calls. No user history
is selected and no components are downloaded. Run outside a socket-blocked
outer test sandbox. CLAWCROSS_NATIVE_SESSION_PLATFORMS can select one platform.
"""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import uuid


@unittest.skipUnless(os.getenv('CLAWCROSS_NATIVE_SESSION_INTEGRATION') == '1', 'explicit live integration opt-in required')
class NativeSessionResumeIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_persisted_fixture_keeps_native_id_and_memory(self):
        binary_dir = str(Path.home() / '.clawcross' / 'bin')
        platforms = os.getenv('CLAWCROSS_NATIVE_SESSION_PLATFORMS', 'codex,claude').split(',')
        for platform in platforms:
            self.assertIn(platform, ('codex', 'claude'))
            with self.subTest(platform=platform), TemporaryDirectory(prefix='clawcross-native-live-') as home, \
                 patch.dict(os.environ, {'CLAWCROSS_HOME':home, 'CLAWCROSS_BIN_DIR':binary_dir, 'npm_config_offline':'true'}):
                from external.acpx import AcpxAdapter
                adapter = AcpxAdapter(cwd=home)
                first = 'clawcross-native-live-' + uuid.uuid4().hex[:12]
                resumed = first + '-resumed'
                marker = 'NATIVE_MEMORY_' + uuid.uuid4().hex[:10]
                options = dict(approve_all=False, permission_policy='deny-all', non_interactive_permissions='deny')
                try:
                    trace = await adapter.prompt_with_trace(
                        tool=platform, session_key=first,
                        prompt_text=f'这是独立验证会话。记住代号 {marker}。只回复 FIXTURE_SAVED，不使用工具。',
                        model='gpt-5.5' if platform == 'codex' else 'sonnet', timeout_sec=45, **options)
                    self.assertIn('FIXTURE_SAVED', trace.text)
                    metadata = await adapter.show_session(tool=platform, name=first)
                    native_id = metadata.get('agentSessionId') or metadata['sessionId']
                    self.assertTrue(native_id)
                    await adapter.close_session(tool=platform, session_key=first, acpx_session=first, **options)
                    await adapter.ensure_session(tool=platform, session_key=resumed, acpx_session=resumed,
                                                 resume_session_id=native_id, timeout_sec=35, **options)
                    metadata = await adapter.show_session(tool=platform, name=resumed)
                    self.assertEqual(metadata.get('agentSessionId') or metadata['sessionId'], native_id)
                    trace = await adapter.prompt_with_trace(
                        tool=platform, session_key=resumed, resume_session_id=native_id,
                        prompt_text='之前记住的代号是什么？只回复代号，不使用工具。', timeout_sec=45, **options)
                    self.assertIn(marker, trace.text)
                finally:
                    for name in (first, resumed):
                        await adapter.close_session(tool=platform, session_key=name, acpx_session=name, **options)
