import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from agents import native_sessions
from agents.store import AgentStore
from external.acpx import AcpxAdapter, AcpxError
from external import session


class NativeSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        p=patch.object(native_sessions, 'DB_PATH', self.root/'native.db');p.start();self.addCleanup(p.stop)
        p=patch.object(native_sessions, '_foreign_native_ids', return_value={'foreign-native'});p.start();self.addCleanup(p.stop)
        self.store=AgentStore(self.root/'agents.db')
        self.adapter=AsyncMock()
        self.adapter.list_native_sessions.return_value={'sessions':[
            {'session_id':'native-one','cwd':str(self.root),'title':'Remembered task','updated_at':'2026-10-04'},
            {'session_id':'foreign-native','cwd':str(self.root),'title':'Other user','updated_at':''}], 'next_cursor':'next'}

    async def test_catalog_register_is_explicit_owned_idempotent_and_preserves_native_memory_seed(self):
        rows=await native_sessions.catalog('alice','codex', adapter=self.adapter)
        self.assertEqual(len(rows['sessions']),1)
        self.assertEqual(self.store.list('alice'),[])
        ticket=rows['sessions'][0]['ticket']
        with self.assertRaises(ValueError): native_sessions.register('bob',ticket,'',self.store)
        agent=native_sessions.register('alice',ticket,'My Codex',self.store)
        self.assertEqual(agent.runtime,{'native_resume_id':'native-one','acp_cwd':str(self.root)})
        self.assertEqual(agent.config['meta']['acp']['clawcross_tools'],True)
        self.assertEqual(native_sessions.register('alice',ticket,'Again',self.store).agent_id,agent.agent_id)
        rows=await native_sessions.catalog('bob','codex', adapter=self.adapter)
        self.assertEqual(rows['sessions'],[])
        session.forget(self.store,agent,new_session=True)
        reset=self.store.require('alice',agent.agent_id)
        self.assertNotIn('native_resume_id',reset.runtime)
        self.assertEqual(reset.runtime['acp_cwd'],str(self.root))

    async def test_expired_or_forged_ticket_cannot_select_arbitrary_native_session(self):
        with self.assertRaises(ValueError): native_sessions.register('alice','guessed','',self.store)
        data=await native_sessions.catalog('alice','claude',adapter=self.adapter)
        ticket=data['sessions'][0]['ticket']
        with native_sessions.database() as db: db.execute('UPDATE tickets SET expires=0 WHERE ticket=?',(ticket,))
        with self.assertRaises(ValueError): native_sessions.register('alice',ticket,'',self.store)
        self.assertEqual(self.store.list('alice'),[])

    async def test_adapter_native_list_and_resume_commands_use_protocol_not_local_and_keep_permissions(self):
        adapter=AcpxAdapter.__new__(AcpxAdapter)
        adapter._pending_initial_prompt={}
        adapter._local_config_options=lambda _: []
        adapter._run_json=AsyncMock(return_value=json.dumps({'sessions':[{'sessionId':'native-one','cwd':str(self.root),'title':'Task','updatedAt':'now'}],'nextCursor':'next'}))
        rows=await adapter.list_native_sessions(tool='codex',cursor='cursor')
        self.assertEqual(rows['sessions'][0]['session_id'],'native-one')
        self.assertEqual(adapter._run_json.call_args.args[0],['codex','sessions','list','--cursor','cursor'])
        self.assertEqual(adapter._run_json.call_args.kwargs['permission_policy'],'deny-all')
        self.assertTrue(adapter._run_json.call_args.kwargs['offline'])
        adapter._session_exists=AsyncMock(return_value=False)
        await adapter.ensure_session(tool='codex',session_key='owned',acpx_session='owned',resume_session_id='native-one')
        self.assertIn('--resume-session',adapter._run_json.call_args.args[0])
        adapter._session_exists=AsyncMock(return_value=True)
        await adapter.ensure_session(tool='codex',session_key='owned',acpx_session='owned',resume_session_id='native-one')
        self.assertNotIn('--resume-session',adapter._run_json.call_args.args[0])

    async def test_import_repairs_only_an_unavailable_native_model_using_advertised_choices(self):
        adapter = AcpxAdapter.__new__(AcpxAdapter)
        adapter._pending_initial_prompt = {}
        adapter._session_exists = AsyncMock(return_value=False)
        adapter._run_json = AsyncMock(return_value='{}')
        adapter._local_config_options = lambda _: [{'id':'model', 'currentValue':'unsupported-host-model',
                                                   'options':[{'value':'unsupported-host-model','description':None},
                                                              {'value':'gpt-5.5','description':'Legacy coding model'}]}]
        await adapter.ensure_session(tool='codex',session_key='owned',acpx_session='owned',resume_session_id='native')
        self.assertEqual(adapter._run_json.call_args.args[0], ['codex','set','model','gpt-5.5','-s','owned'])
        adapter._run_json.reset_mock()
        await adapter.ensure_session(tool='codex',session_key='owned',acpx_session='owned',resume_session_id='native',model='explicit-model')
        self.assertEqual(adapter._run_json.await_count, 1)
        adapter._local_config_options = lambda _: [{'id':'model', 'currentValue':'gpt-5.5', 'options':[{'value':'gpt-5.5'}]}]
        adapter._run_json.reset_mock()
        await adapter.ensure_session(tool='codex',session_key='owned',acpx_session='owned',resume_session_id='native')
        self.assertEqual(adapter._run_json.await_count, 1)

    async def test_imported_prompt_keeps_the_native_model_across_queue_restarts(self):
        adapter = AcpxAdapter.__new__(AcpxAdapter)
        adapter.ensure_session = AsyncMock()
        adapter._local_config_values = lambda _: {'model':'gpt-5.5'}
        adapter.consume_initial_prompt = lambda **kw: (kw['prompt_text'], False)
        adapter._send_prompt_file = AsyncMock(return_value='{"reply":"answer"}')
        await adapter.prompt_with_trace(tool='codex',session_key='owned',prompt_text='question',resume_session_id='native')
        self.assertEqual(adapter._send_prompt_file.call_args.kwargs['model'],'gpt-5.5')
        await adapter.prompt_with_trace(tool='codex',session_key='owned',prompt_text='question',resume_session_id='native',model='selected-model')
        self.assertEqual(adapter._send_prompt_file.call_args.kwargs['model'],'selected-model')

    async def test_native_list_without_capability_fails_instead_of_showing_local_records(self):
        adapter=AcpxAdapter.__new__(AcpxAdapter);adapter._run_json=AsyncMock(return_value='[]')
        with self.assertRaises(AcpxError): await adapter.list_native_sessions(tool='claude')

    async def test_show_metadata_accepts_current_json_without_returning_history(self):
        adapter=AcpxAdapter.__new__(AcpxAdapter)
        adapter._run_json=AsyncMock(return_value=json.dumps({'acpSessionId':'native-one','agentSessionId':'vendor-id',
                                                            'messages':[{'text':'PRIVATE_HISTORY'}]}))
        value=await adapter.show_session(tool='codex',name='test')
        self.assertEqual(value['sessionId'],'native-one')
        self.assertEqual(value['agentSessionId'],'vendor-id')
        self.assertNotIn('PRIVATE_HISTORY',json.dumps(value))

    async def test_registered_runtime_uses_original_cwd_and_native_id_without_replaying_history(self):
        from agents.messages import AgentMessage
        from external.acp import AcpRuntime
        from external.acpx import AcpxPromptTrace
        rows = await native_sessions.catalog('alice', 'codex', adapter=self.adapter)
        agent = native_sessions.register('alice', rows['sessions'][0]['ticket'], '', self.store)
        runtime = AcpRuntime(self.store)
        adapter = AsyncMock()
        adapter.prompt_with_trace.return_value = AcpxPromptTrace('answer', [], [], [], [], '')
        history = AsyncMock()
        with patch('external.acpx.get_acpx_adapter', return_value=adapter) as factory, \
             patch.object(runtime, '_native_session', return_value=(None, None, {})), \
             patch('external.history.get_store', return_value=history):
            await runtime.test_connection(agent)
            factory.assert_called_with(cwd=str(self.root))
            self.assertEqual(adapter.ensure_session.call_args.kwargs['resume_session_id'], 'native-one')
            adapter.prompt_with_trace.assert_not_awaited()
            reply = await runtime.ask(agent, AgentMessage(text='new question'), context={}, mode='auto',
                                      enabled_tools=None, response_format=None, timeout=30)
            self.assertTrue(reply.ok, reply.error)
            self.assertEqual(reply.content, 'answer')
            self.assertEqual(adapter.prompt_with_trace.call_args.kwargs['resume_session_id'], 'native-one')
            self.assertIn('new question', adapter.prompt_with_trace.call_args.kwargs['prompt_text'])
            self.assertNotIn('Remembered task', adapter.prompt_with_trace.call_args.kwargs['prompt_text'])

    async def test_deleted_agent_releases_native_registration(self):
        rows = await native_sessions.catalog('alice', 'codex', adapter=self.adapter)
        agent = native_sessions.register('alice', rows['sessions'][0]['ticket'], '', self.store)
        self.store.delete('alice', agent.agent_id)
        native_sessions.release_agent('alice', agent.agent_id)
        rows = await native_sessions.catalog('bob', 'codex', adapter=self.adapter)
        self.assertEqual(len(rows['sessions']), 1)

    async def test_routes_allow_all_authenticated_users_by_default(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agents.routes import create_agents_router
        app=FastAPI();app.include_router(create_agents_router(internal_token='internal',verify_password=lambda u,p:p=='pass',store=self.store,gateway=AsyncMock()))
        client=TestClient(app)
        with patch.dict('os.environ', {'CLAWCROSS_NATIVE_SESSION_USERS':''}), patch.object(native_sessions,'catalog',new=self.adapter.list_native_sessions):
            self.assertEqual(client.get('/v1/agents/native-sessions',headers={'Authorization':'Bearer alice:pass'}).status_code,200)
            self.assertEqual(client.get('/v1/agents/native-sessions',headers={'Authorization':'Bearer bob:pass'}).status_code,200)
            self.assertEqual(client.get('/v1/agents/native-sessions').status_code,401)
            result=client.get('/v1/agents/native-sessions',headers={'Authorization':'Bearer internal:alice','X-ClawCross-Host-Browse':'internal'})
            self.assertEqual(result.status_code,200,result.text)
        with patch.dict('os.environ', {'CLAWCROSS_NATIVE_SESSION_USERS':'alice'}), patch.object(native_sessions,'catalog',new=self.adapter.list_native_sessions):
            self.assertEqual(client.get('/v1/agents/native-sessions',headers={'Authorization':'Bearer bob:pass'}).status_code,403)
            self.assertEqual(client.get('/v1/agents/native-sessions',headers={'Authorization':'Bearer alice:pass'}).status_code,200)
