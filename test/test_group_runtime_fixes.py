import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import webot.runtime_store as runtime_store
import webot.subagents as subagents
from webot.api.system_service import SystemService
from webot.driver import WebotRuntime
from webot.mcp import llmapi, webot


class RuntimeFixTests(unittest.IsolatedAsyncioTestCase):
    async def test_reset_stops_old_delivery_and_clears_runtime_but_keeps_mode(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(runtime_store, 'AGENT_RUNTIME_DB_DIR', Path(tmp)), \
                patch.object(runtime_store, 'DEFAULT_DB_PATH', None):
            runtime_store.save_session_mode('alice', 's', mode='readonly')
            runtime_store.create_inbox_message('alice', target_session='s', content='old group message')
            engine = SimpleNamespace(_db_path=str(Path(tmp) / 'checkpoints'), cancel_task=AsyncMock(),
                                     close_thread_checkpoint=AsyncMock(), forget_thread_state=Mock())
            system = SimpleNamespace(cancel_session=AsyncMock())
            sessions = SimpleNamespace(cancel_compaction=AsyncMock())
            runtime = WebotRuntime(engine=engine, chat_service=None, system=system, sessions=sessions)
            agent = SimpleNamespace(owner='alice', agent_id='s')
            self.assertEqual(await runtime.control(agent, 'reset'), {'reset': True})
            system.cancel_session.assert_awaited_once_with('alice#s')
            engine.forget_thread_state.assert_called_once_with('alice#s')
            self.assertEqual(runtime_store.get_session_mode('alice', 's')['mode'], 'readonly')
            self.assertEqual(runtime_store.list_inbox_messages('alice', 's'), [])

    async def test_delivery_workers_are_cancelled_for_only_the_reset_session(self):
        service = SystemService(agent=None)
        worker = asyncio.create_task(asyncio.Event().wait())
        other = asyncio.create_task(asyncio.Event().wait())
        service._inbox_tasks.update({'alice#s': worker, 'alice#other': other})
        await service.cancel_session('alice#s')
        self.assertTrue(worker.cancelled())
        self.assertFalse(other.done())
        other.cancel()
        await asyncio.gather(other, return_exceptions=True)

    async def test_delete_does_not_recreate_the_child_runtime_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(runtime_store, 'AGENT_RUNTIME_DB_DIR', Path(tmp) / 'runtime'), \
                patch.object(runtime_store, 'DEFAULT_DB_PATH', None), \
                patch.object(subagents, 'DEFAULT_DB_PATH', Path(tmp) / 'subagents.db'):
            record = subagents.create_subagent_record(agent_id='child', user_id='alice',
                session_id='subagent__general__child', agent_type='general', name='Child',
                description='', parent_session='parent')
            subagents.upsert_subagent(record)
            runtime_store.get_session_state('alice', record.session_id)
            path = runtime_store.get_agent_runtime_db_path('alice', record.session_id)
            self.assertTrue(path.exists())
            worker = asyncio.create_task(asyncio.Event().wait())
            webot._BACKGROUND_TASKS['child'] = worker
            latest = SimpleNamespace(status='completed', run_id='r1')
            async def destroy(**kwargs):
                self.assertTrue(worker.done())
                runtime_store.delete_agent_runtime_db('alice', record.session_id)
                return {'deleted': record.session_id}
            with patch.object(webot, '_recover_background_runs', AsyncMock()), \
                    patch.object(webot, '_cancel_internal_subagent', AsyncMock()), \
                    patch.object(webot, '_delete_internal_session', AsyncMock(side_effect=destroy)), \
                    patch.object(webot, 'get_latest_run_for_agent', return_value=latest), \
                    patch.object(webot, 'update_run_status'), \
                    patch.object(webot, 'record_run_event') as events, \
                    patch.object(webot, '_notify_parent_session', AsyncMock()):
                result = await webot.delete_subagent('alice', 'child', source_session='parent')
                self.assertIn('已删除', result)
                self.assertFalse(path.exists())
                events.assert_not_called()
                self.assertIn('已删除或不存在', await webot.delete_subagent('alice', 'child', source_session='parent'))

    async def test_send_group_requires_calling_agent(self):
        with patch.object(llmapi, '_INTERNAL_TOKEN', 'test-token'), \
                patch.object(llmapi.httpx, 'AsyncClient') as client:
            self.assertIn('缺少调用 agent', await llmapi.send_to_group('alice', 'g1', 'hello'))
            client.assert_not_called()
