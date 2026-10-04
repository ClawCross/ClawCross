"""ACP streams before completion and MCP cannot exceed the active Agent grant."""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'backend'))
from agents.messages import AgentMessage, AgentReply
from agents.openai import ChatCompletionRequest
from agents.store import ACPX, AgentStore
from external.acp import AcpRuntime
from external.acp_events import normalize_event
from external.acp_settings import validate_settings, capability_card
from external.acpx import AcpxAdapter, AcpxError, public_command_error, bridge_permission_flags
from external import tool_bridge


def packet(kind, **data):
    return {'method': 'session/update', 'params': {'update': {'sessionUpdate': kind, **data}}}


class ACPStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_protocol_error_is_filtered_only_for_presentation(self):
        from external import session
        with TemporaryDirectory() as tmp:
            store=AgentStore(Path(tmp)/'agents.db')
            agent=store.create('alice',driver=ACPX,config={'platform':'claude'})
            content='acpx failed (5): '+json.dumps({'jsonrpc':'2.0','method':'session/resume','params':{'token':'private-secret'}})
            history=AsyncMock()
            history.list_messages.return_value=[{'role':'assistant','direction':'error','content':content}]
            with patch('external.history.get_store',AsyncMock(return_value=history)):
                result=await session.log(agent,20)
            self.assertEqual(result[0]['content'],'External Agent tool permission denied')
            self.assertEqual(history.list_messages.return_value[0]['content'],content)

    async def test_permission_error_does_not_expose_protocol_credentials(self):
        output = json.dumps({'jsonrpc':'2.0','method':'session/resume','params':{
            'mcpServers':[{'env':[{'name':'TOKEN','value':'private-secret'}]}]}})
        error = public_command_error(output,5)
        self.assertEqual(error,'External Agent tool permission denied')
        self.assertNotIn('private-secret',error)
        output += '\n' + json.dumps({'jsonrpc':'2.0','error':{'message':'Conflict','data':{'detailCode':'QUEUE_MCP_CONFIG_CONFLICT'}}})
        self.assertIn('QUEUE_MCP_CONFIG_CONFLICT',public_command_error(output,1))

    async def test_native_policy_delegates_only_qualified_bridge_wrappers(self):
        with TemporaryDirectory() as tmp:
            config=Path(tmp)/'mcp.json'
            config.write_text(json.dumps({'mcpServers':[{'name':'ClawCross'}]}))
            command=['acpx','--approve-reads']
            bridge_permission_flags(command,str(config))
            self.assertNotIn('--approve-all',command)
            policy=json.loads(command[command.index('--permission-policy')+1])
            self.assertEqual(set(policy),{'autoApprove'})
            self.assertEqual(len(policy['autoApprove']),4)
            self.assertNotIn('*',policy['autoApprove'])
            self.assertNotIn('exec_command',policy['autoApprove'])

    async def test_connector_conflict_reconnects_without_closing_session(self):
        adapter = AcpxAdapter.__new__(AcpxAdapter)
        adapter._session_exists = AsyncMock(return_value=True)
        adapter._run_json = AsyncMock(side_effect=[AcpxError('QUEUE_MCP_CONFIG_CONFLICT'), '{}'])
        adapter._reconnect_transport = AsyncMock()
        adapter.close_session = AsyncMock(side_effect=AssertionError('must preserve native session'))
        created = await adapter.ensure_session(tool='codex',session_key='owned',acpx_session='owned',mcp_config='/scoped.json')
        self.assertFalse(created)
        adapter._reconnect_transport.assert_awaited_once_with('owned')
        self.assertEqual(adapter._run_json.await_count,2)
        adapter.close_session.assert_not_awaited()

    async def test_tool_only_turn_never_becomes_protocol_text(self):
        values = [{'jsonrpc':'2.0','id':5,'method':'session/prompt','params':{'prompt':[{'type':'text','text':'private prompt'}]}},
                  {'jsonrpc':'2.0',**packet('tool_call',toolCallId='a',title='Read',status='completed')},
                  {'jsonrpc':'2.0','id':5,'result':{'stopReason':'end_turn'}}]
        output='\n'.join(json.dumps(value) for value in values)
        self.assertEqual(AcpxAdapter._extract_text(output),'')
        trace=AcpxAdapter._extract_trace(output)
        self.assertEqual(trace.text,'')
        self.assertEqual(trace.tool_uses[0]['name'],'Read')
    async def test_stdout_delivers_tool_events_while_process_is_running(self):
        adapter = AcpxAdapter.__new__(AcpxAdapter)
        values = [packet('tool_call', toolCallId='a', title='Read file', status='in_progress'),
                  packet('tool_call_update', toolCallId='a', status='completed', rawOutput='safe result')]
        code = f'import time; print({json.dumps(values[0])!r},flush=True); time.sleep(.15); print({json.dumps(values[1])!r},flush=True)'
        seen = []
        finished = False
        async def event(value):
            self.assertFalse(finished)
            seen.append(value)
        output = await adapter._run_json_command([sys.executable, '-c', code], timeout_sec=3,
                                                  allow_nonzero=False, on_event=event)
        finished = True
        self.assertEqual([e['type'] for e in seen], ['acpx_tool_start', 'acpx_tool_end'])
        self.assertEqual(seen[-1]['content_text'], 'safe result')
        trace = adapter._extract_trace(output)
        self.assertEqual(trace.tool_uses[0]['name'], 'Read file')

    async def test_chat_yields_tool_event_before_final_reply(self):
        with TemporaryDirectory() as tmp:
            store = AgentStore(Path(tmp) / 'agents.db')
            agent = store.create('alice', driver=ACPX, config={'platform': 'codex'})
            runtime = AcpRuntime(store)
            release = asyncio.Event()
            async def ask_turn(current, msg, **kwargs):
                await kwargs['context']['_acp_event_sink']({'type':'acpx_tool_start','tool_call_id':'a','title':'Read'})
                await release.wait()
                await kwargs['context']['_acp_event_sink']({'type':'text','text':'answer'})
                return AgentReply(ok=True, content='answer')
            runtime._ask_turn = ask_turn
            response = await runtime.chat(agent, ChatCompletionRequest(stream=True, messages=[{'role':'user','content':'read'}]))
            stream = response.body_iterator
            await anext(stream)
            tool = await asyncio.wait_for(anext(stream), 1)
            self.assertIn('acpx_tool_start', tool)
            self.assertTrue(runtime.is_busy(agent))
            release.set()
            rest = ''.join([value async for value in stream])
            self.assertEqual(rest.count('"content": "answer"'), 1)
            self.assertIn('[DONE]', rest)
            self.assertFalse(runtime.is_busy(agent))

    async def test_persistent_settings_use_set_only_when_changed(self):
        adapter = AcpxAdapter.__new__(AcpxAdapter)
        adapter.ensure_session = AsyncMock()
        adapter._local_config_values = lambda _: {'reasoning_effort':'medium'}
        adapter._run_json = AsyncMock(return_value='{}')
        adapter._send_prompt_file = AsyncMock(return_value='{"text":"ok"}')
        adapter.consume_initial_prompt = lambda **k: (k['prompt_text'], False)
        await adapter.prompt_with_trace(tool='codex', session_key='owned', prompt_text='hello',
            config_options={'model':'gpt-5.5','reasoning_effort':'high'}, model='gpt-5.5', mcp_config='/scoped.json')
        self.assertEqual(adapter._run_json.call_args.args[0], ['codex','set','reasoning_effort','high','-s','owned'])
        self.assertEqual(adapter.ensure_session.call_args.kwargs['mcp_config'], '/scoped.json')
        adapter._local_config_values = lambda _: {'reasoning_effort':'high'}
        adapter._run_json.reset_mock()
        await adapter.prompt_with_trace(tool='codex', session_key='owned', prompt_text='hello',
                                         config_options={'reasoning_effort':'high'})
        adapter._run_json.assert_not_awaited()


class ConnectionProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_initializes_without_prompt_or_identity_delivery(self):
        with TemporaryDirectory() as tmp:
            store = AgentStore(Path(tmp)/'agents.db')
            agent = store.create('alice', driver=ACPX, config={'platform':'codex','model':'gpt-5.5'})
            runtime = AcpRuntime(store=store)
            adapter = SimpleNamespace(ensure_session=AsyncMock(), prompt_with_trace=AsyncMock())
            with patch('external.acpx.get_acpx_adapter',return_value=adapter), \
                 patch('external.acp_settings.initial_config_options',return_value={}), \
                 patch('external.tool_bridge.connector_file',return_value='/scoped-mcp.json'):
                await runtime.test_connection(agent)
            adapter.ensure_session.assert_awaited_once()
            self.assertIsNone(adapter.ensure_session.call_args.kwargs['system_prompt'])
            self.assertEqual(adapter.ensure_session.call_args.kwargs['model'],'gpt-5.5')
            adapter.prompt_with_trace.assert_not_awaited()
            state=store.require('alice',agent.agent_id).runtime
            self.assertEqual(set(state),{'acp_cwd','last_used_at'})  # connection pins cwd without delivering identity


class ScopedBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_ending_native_turn_cancels_an_unfinished_bridge_call(self):
        import httpx
        from fastapi import FastAPI
        from langchain_core.tools import tool
        @tool
        def safe_read(path: str) -> str:
            """Read safe test data."""
            return path
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def blocked(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        with TemporaryDirectory() as tmp:
            store = AgentStore(Path(tmp) / 'agents.db')
            agent = store.create('alice', driver=ACPX, config={'platform':'codex'})
            engine = SimpleNamespace(_mcp_tools=[safe_read], _tool_registry=None)
            app = FastAPI(); app.include_router(tool_bridge.bridge_router())
            tool_bridge._tokens['cancel-token'] = ('alice', agent.agent_id)
            try:
                with patch('external.tool_bridge.get_store', return_value=store), \
                     patch('external.tool_bridge.get_gateway', return_value=SimpleNamespace(runtimes={'webot':SimpleNamespace(engine=engine)})), \
                     patch('webot.engine.agent.available_internal_tool_names', return_value={'safe_read'}), \
                     patch('webot.engine.agent.UserAwareToolNode', return_value=AsyncMock(side_effect=blocked)):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://bridge') as client:
                        with tool_bridge.active_turn(agent, AgentMessage(text='Read',sender='u:alice'), {}, 'manual', ['safe_read']):
                            request = asyncio.create_task(client.post('/external/tool-bridge',
                                headers={'Authorization':'Bearer cancel-token'},
                                json={'action':'call','name':'safe_read','arguments':{'path':'x'}}))
                            await asyncio.wait_for(started.wait(), 2)
                            self.assertFalse(request.done())
                        await asyncio.wait_for(cancelled.wait(), 2)
                        with self.assertRaises(asyncio.CancelledError):
                            await request
            finally:
                tool_bridge._tokens.pop('cancel-token', None)

    async def test_tools_default_enabled_and_explicit_disable_is_preserved(self):
        with TemporaryDirectory() as tmp:
            store = AgentStore(Path(tmp) / 'agents.db')
            agent = store.create('alice', driver=ACPX, config={'platform':'codex'})
            with patch('external.acp_settings.native_options', return_value=[]), patch('external.tool_bridge.STATE_DIR', Path(tmp)):
                card = capability_card(agent)
                self.assertTrue(card['clawcross_tools'])
                self.assertTrue(card['settings']['clawcross_tools'])
                path = Path(tool_bridge.connector_file(agent))
                self.assertEqual(json.loads(path.read_text())['mcpServers'][0]['name'], 'ClawCross')
                disabled = store.update('alice', agent.agent_id, config={'platform':'codex','meta':{'acp':{'clawcross_tools':False}}})
                self.assertFalse(capability_card(disabled)['clawcross_tools'])
                self.assertEqual(json.loads(Path(tool_bridge.connector_file(disabled)).read_text())['mcpServers'], [])
                for token, principal in list(tool_bridge._tokens.items()):
                    if principal == ('alice',agent.agent_id): tool_bridge._tokens.pop(token,None)

    async def test_grant_requires_active_turn_and_agent_enablement(self):
        import httpx
        from fastapi import FastAPI
        with TemporaryDirectory() as tmp:
            store = AgentStore(Path(tmp) / 'agents.db')
            agent = store.create('alice', driver=ACPX, config={'platform':'codex','meta':{'acp':{'clawcross_tools':True}}})
            app = FastAPI()
            app.include_router(tool_bridge.bridge_router())
            tool_bridge._tokens['test-token'] = ('alice', agent.agent_id)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://bridge') as client:
                    auth = {'Authorization':'Bearer test-token'}
                    body = {'action':'search','query':'read'}
                    self.assertEqual((await client.post('/external/tool-bridge',json=body,headers=auth)).status_code,403)
                    with patch('external.tool_bridge.get_store', return_value=store):
                        with tool_bridge.active_turn(agent,AgentMessage(text='read'),{},'chat',None):
                            self.assertEqual((await client.post('/external/tool-bridge',json=body,headers=auth)).status_code,403)
                        disabled = store.update('alice', agent.agent_id,config={'platform':'codex','meta':{'acp':{'clawcross_tools':False}}})
                        with tool_bridge.active_turn(disabled,AgentMessage(text='read'),{},'readonly',None):
                            self.assertEqual((await client.post('/external/tool-bridge',json=body,headers=auth)).status_code,403)
            finally:
                tool_bridge._tokens.pop('test-token',None)

    async def test_tool_allowlist_schema_and_original_authorization(self):
        import httpx
        from fastapi import FastAPI
        from langchain_core.tools import tool
        from langchain_core.messages import ToolMessage
        @tool
        def safe_read(path: str) -> str:
            """Read safe test data."""
            return path
        engine = SimpleNamespace(_mcp_tools=[safe_read],_tool_registry=None)
        node = AsyncMock(return_value={'messages':[ToolMessage(content='ok',tool_call_id='a')]})
        with TemporaryDirectory() as tmp:
            store = AgentStore(Path(tmp) / 'agents.db')
            agent = store.create('alice',driver=ACPX,config={'platform':'codex','meta':{'acp':{'clawcross_tools':True}}})
            app=FastAPI(); app.include_router(tool_bridge.bridge_router())
            tool_bridge._tokens['test-token']=('alice',agent.agent_id)
            try:
                with patch('external.tool_bridge.get_store',return_value=store), \
                     patch('external.tool_bridge.get_gateway',return_value=SimpleNamespace(runtimes={'webot':SimpleNamespace(engine=engine)})), \
                     patch('webot.engine.agent.available_internal_tool_names',return_value={'safe_read'}), \
                     patch('webot.engine.agent.UserAwareToolNode',return_value=node):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://bridge') as client:
                        auth={'Authorization':'Bearer test-token'}
                        with tool_bridge.active_turn(agent,AgentMessage(text='Read my file',sender='u:alice'),{},'readonly',['safe_read']):
                            spoof=await client.post('/external/tool-bridge',json={'action':'call','name':'safe_read','arguments':{'path':'x','username':'bob'}},headers=auth)
                            self.assertEqual(spoof.status_code,400)
                            hidden=await client.post('/external/tool-bridge',json={'action':'call','name':'run_command','arguments':{}},headers=auth)
                            self.assertEqual(hidden.status_code,403)
                            response=await client.post('/external/tool-bridge',json={'action':'call','name':'safe_read','arguments':{'path':'x'}},headers=auth)
                            self.assertEqual(response.status_code,200)
                        state=node.call_args.args[0]
                        self.assertEqual(state['user_id'],'alice')
                        self.assertEqual(state['session_id'],agent.agent_id)
                        self.assertEqual(state['messages'][0].additional_kwargs['input_origin'],'user')
                        self.assertEqual(state['messages'][0].content,'Read my file')
            finally:
                tool_bridge._tokens.pop('test-token',None)

    async def test_native_settings_reject_another_adapter_key(self):
        options=[{'id':'effort','options':[{'value':'low'},{'value':'high'}]}]
        with patch('external.acp_settings.native_options',return_value=options):
            self.assertEqual(validate_settings(None,{'config_options':{'effort':'high'}})['config_options'],{'effort':'high'})
            with self.assertRaises(ValueError):
                validate_settings(None,{'config_options':{'reasoning_effort':'high'}})
            with self.assertRaises(ValueError):
                validate_settings(None,{'config_options':{'effort':'invalid'}})


if __name__ == '__main__':
    unittest.main()
