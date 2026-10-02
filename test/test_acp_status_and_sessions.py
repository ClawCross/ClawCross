"""ACP polling is passive and session tracking is scoped to the owner."""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'backend'))
from agents.store import AgentStore, ACPX
from external.acp import AcpRuntime
from external.acpx import AcpxAdapter
from external import session
from ops.service import OpsService
from fastapi import HTTPException


class PassiveStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_model_is_scoped_to_prompt_command(self):
        adapter = AcpxAdapter.__new__(AcpxAdapter)
        adapter._acpx_bin = '/acpx'
        adapter._cwd = '/workspace'
        command, prompt_file = adapter.prepare_prompt_command(
            tool='codex', session_key='owned', acpx_session='owned', prompt_text='hello',
            attachments=None, ttl_sec=300, approve_all=False, permission_policy='approve-reads',
            non_interactive_permissions='deny', allowed_tools=None, model='gpt-5.5')
        try:
            self.assertEqual(command[command.index('--model') + 1], 'gpt-5.5')
            self.assertLess(command.index('--model'), command.index('codex'))
        finally:
            Path(prompt_file).unlink()

    async def test_status_uses_turn_lock_without_external_process(self):
        with TemporaryDirectory() as tmp:
            store = AgentStore(Path(tmp) / 'agents.db')
            agent = store.create('alice', driver=ACPX, config={'platform': 'codex'})
            runtime = AcpRuntime(store)
            with patch('external.acp.adapter', side_effect=AssertionError('must not spawn')):
                self.assertEqual((await runtime.status(agent))['state'], 'idle')
                async with session.turn(store, agent):
                    self.assertEqual((await runtime.status(agent))['state'], 'running')
                self.assertFalse(runtime.is_busy(agent))

    async def test_list_queries_local_records(self):
        adapter = AcpxAdapter.__new__(AcpxAdapter)
        adapter._run_json = AsyncMock(return_value='[]')
        await adapter.list_sessions(tool='codex')
        self.assertEqual(adapter._run_json.call_args.args[0], ['codex', 'sessions', 'list', '--local'])

    async def test_tracker_excludes_foreign_and_unregistered_sessions(self):
        with TemporaryDirectory() as tmp:
            store = AgentStore(Path(tmp) / 'agents.db')
            alice = store.create('alice', driver=ACPX, config={'platform': 'codex'}, name='Alice Codex')
            bob = store.create('bob', driver=ACPX, config={'platform': 'codex'})
            adapter = AsyncMock()
            adapter.list_sessions.return_value = [
                {'name': session.runtime_session(alice)},
                {'name': session.runtime_session(bob)}, {'name': 'unregistered'}]
            service = OpsService(internal_token='test', agent=None,
                                 verify_password=lambda *_: False, verify_auth_or_token=lambda *_: None)
            with patch('agents.store.get_store', return_value=store), \
                 patch('ops.components.binary_path', return_value='/acpx'), \
                 patch('external.acpx.get_acpx_adapter', return_value=adapter):
                result = await service.list_all_sessions('alice')
                self.assertEqual([s['agent_id'] for s in result['acpx_sessions']], [alice.agent_id])
                adapter.list_sessions.assert_awaited_once_with(tool='codex')
                with self.assertRaises(HTTPException) as rejected:
                    await service.close_acp_session('codex', session.runtime_session(bob), user_id='alice')
                self.assertEqual(rejected.exception.status_code, 404)


if __name__ == '__main__':
    unittest.main()
