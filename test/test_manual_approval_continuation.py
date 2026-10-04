"""Human controls resume the saved action once, in its original conversation."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool

from webot import approval_review, policy, runtime_settings, runtime_store
from webot.api.service import WeBotService
from webot.api.system_service import SystemService
from webot.engine.agent import UserAwareToolNode
from webot.models import WeBotApprovalResolutionRequest


class ManualContinuationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for obj, key, value in (
            (runtime_settings, 'USER_FILES_DIR', root / 'users'),
            (policy, 'USER_FILES_DIR', root / 'users'),
            (policy, 'PROJECT_ROOT', root),
            (runtime_store, 'DEFAULT_DB_PATH', root / 'runtime.db'),
        ):
            p = patch.object(obj, key, value)
            p.start()
            self.addCleanup(p.stop)
        policy.save_tool_policy_config('alice', {'tools': {'run_command': {'approval': 'manual'}}})
        runtime_settings.save_runtime_settings('alice', settings={'approval': {'command_sandbox': 'off'}})
        runtime_store.save_session_mode('alice', 's', mode='manual')
        self.groups = [{'group_id': 'g', 'reply_channel': 'send_to_group', 'title': 'Test'}]
        self.messages = [HumanMessage(content='写入测试标记', id='human-1', additional_kwargs={
            'input_origin': 'user', 'framework_groups': self.groups,
        })]
        self.system = SimpleNamespace(run=AsyncMock(return_value={'status': 'received'}))
        self.service = WeBotService(agent=None, system=self.system, verify_auth_or_token=lambda *a: None, extract_text=str)
        p = patch('agents.store.get_store', return_value=SimpleNamespace(get=lambda *a: None))
        p.start()
        self.addCleanup(p.stop)
        self.output = root / 'executed.txt'
        self.executions = []

        async def execute(command: str, username: str, session_id: str, cwd: str = '', timeout: int = 120,
                          sandbox_access: str = 'default', escalation_target: str = '', escalation_reason: str = '') -> str:
            self.executions.append((command, username, session_id))
            self.output.write_text('Executed after approval')
            return 'COMMAND_COMPLETED'

        self.node = UserAwareToolNode([StructuredTool.from_function(coroutine=execute, name='run_command', description='Test command')])

    async def pending(self, *, trigger_source='user'):
        self.state = {'user_id': 'alice', 'session_id': 's', 'session_mode': 'manual',
            'trigger_source': trigger_source, 'enabled_tools': ['run_command'],
            'messages': self.messages + [AIMessage(content='', tool_calls=[{
                'id': 'original-call', 'name': 'run_command', 'args': {'command': 'echo approved'}}])]}
        self.task = asyncio.create_task(self.node(self.state, {}))
        async def cleanup():
            if not self.task.done():
                self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.addAsyncCleanup(cleanup)
        for _ in range(100):
            records = runtime_store.list_tool_approvals('alice', 's', status='pending')
            if records and approval_review.has_live_approval_waiter(records[0]):
                self.assertFalse(self.task.done())
                self.assertFalse(self.output.exists())
                return records[0]
            await asyncio.sleep(.01)
        self.fail('The original tool did not enter human approval wait')

    async def resolve(self, record, **overrides):
        req = WeBotApprovalResolutionRequest(user_id='alice', approval_id=record.approval_id, session_id='s', **overrides)
        return await self.service.resolve_tool_approval(req, 'test-token')

    async def result(self):
        return await asyncio.wait_for(self.task, 2)

    async def test_button_continues_original_tool_once_without_pending_result_or_new_turn(self):
        record = await self.pending()
        metadata = json.loads(record.review_metadata_json)
        self.assertEqual(metadata['continuation']['groups'], self.groups)
        self.assertEqual(metadata['continuation']['enabled_tools'], ['run_command'])
        result = await self.resolve(record)
        self.assertEqual(result['continuation'], 'resumed')
        tools = await self.result()
        self.assertIn('COMMAND_COMPLETED', tools['messages'][0].content)
        self.assertFalse(tools.get('_conversation_approval_prompts'))
        self.assertEqual(tools['messages'][0].tool_call_id, 'original-call')
        self.assertEqual(self.executions, [('echo approved', 'alice', 's')])
        used = runtime_store.get_tool_approval(record.approval_id, 'alice')
        self.assertEqual(used.status, 'used')
        self.assertFalse(approval_review.has_live_approval_waiter(used))
        with self.assertRaises(HTTPException) as caught:
            await self.resolve(record)
        self.assertEqual(caught.exception.status_code, 409)
        self.system.run.assert_not_awaited()

    async def test_deny_returns_only_final_denial_without_executing(self):
        record = await self.pending()
        result = await self.resolve(record, action='deny')
        self.assertEqual(result['continuation'], 'resumed')
        tools = await self.result()
        self.assertFalse(tools.get('_conversation_approval_prompts'))
        self.assertNotIn('等待批准', tools['messages'][0].content)
        self.assertEqual(runtime_store.get_tool_approval(record.approval_id, 'alice').status, 'denied')
        self.assertFalse(self.output.exists())
        self.system.run.assert_not_awaited()

    async def test_cli_text_reply_continues_without_sending_bare_y_to_model(self):
        record = await self.pending()
        human = HumanMessage(content='Y', id='reply-1', additional_kwargs={'input_origin': 'user'})
        self.assertIn('已批准', approval_review.resolve_conversation_reply('alice', 's', approval_review.review_context([human])))
        self.assertIn('COMMAND_COMPLETED', (await self.result())['messages'][0].content)
        self.assertEqual(len(self.executions), 1)

    async def test_gateway_chat_y_does_not_cancel_or_start_an_agent_turn(self):
        from agents.gateway import AgentGateway
        from agents.openai import ChatCompletionRequest, ChatMessage
        record = await self.pending()
        gateway = AgentGateway.__new__(AgentGateway)
        gateway.runtime = AsyncMock(side_effect=AssertionError('Must not enter runtime for approval reply'))
        agent = SimpleNamespace(owner='alice', agent_id='s', platform='webot')
        result = await gateway.chat(agent, ChatCompletionRequest(messages=[ChatMessage(role='user', content='Y')]))
        self.assertIn('已批准', result['choices'][0]['message']['content'])
        self.assertIn('COMMAND_COMPLETED', (await self.result())['messages'][0].content)
        gateway.runtime.assert_not_called()

    async def test_system_trigger_owner_y_is_handled_before_the_inbox_queue(self):
        from webot.api.system_models import SystemTriggerRequest
        await self.pending(trigger_source='system')
        system = SystemService(agent=None)
        result = await system.run(SystemTriggerRequest(user_id='alice', session_id='s', text='Y',
            inbox_source_session='group', group_human_requests=[{
                'id':'group-reply', 'text':'Y', 'source_kind':'group_human', 'sender_user':'alice', 'group_id':'g'}]))
        self.assertIn('已批准', result['reply'])
        self.assertIn('COMMAND_COMPLETED', (await self.result())['messages'][0].content)

    async def test_agent_cannot_supply_system_network_retry_parameters(self):
        state = {'user_id':'alice','session_id':'s','session_mode':'manual','messages':[
            AIMessage(content='',tool_calls=[{'id':'agent-call','name':'run_command','args':{
                'command':'echo hello','sandbox_access':'network','escalation_target':'example.com:443',
                'escalation_reason':'invented by agent'}}])]}
        result=await self.node(state,{})
        self.assertIn('不接受 Agent 自行申请提权',result['messages'][0].content)
        self.assertFalse(self.output.exists())
        self.assertEqual(runtime_store.list_tool_approvals('alice','s'),[])

    async def test_wrong_session_or_changed_policy_cannot_resume(self):
        record = await self.pending()
        req = WeBotApprovalResolutionRequest(user_id='alice', approval_id=record.approval_id, session_id='other')
        with self.assertRaises(HTTPException) as caught:
            await self.service.resolve_tool_approval(req, None)
        self.assertEqual(caught.exception.status_code, 404)
        policy.save_tool_policy_config('alice', {'tools': {'run_command': {'approval': 'deny'}}})
        with self.assertRaises(HTTPException) as caught:
            await self.resolve(record)
        self.assertEqual(caught.exception.status_code, 409)
        self.system.run.assert_not_awaited()

    async def test_auto_result_cannot_be_overridden_by_human_button(self):
        record = await self.pending()
        meta = json.loads(record.review_metadata_json)
        meta['reviewer'] = 'auto_review'
        runtime_store.set_approval_review_metadata(record.approval_id, 'alice', meta)
        with self.assertRaises(HTTPException) as caught:
            await self.resolve(record)
        self.assertEqual(caught.exception.status_code, 409)
        self.system.run.assert_not_awaited()

    async def test_external_agent_approval_does_not_prompt_native_agent_again(self):
        record = await self.pending()
        target = SimpleNamespace(driver='acpx')
        gateway = SimpleNamespace(trigger=AsyncMock())
        with patch('agents.store.get_store', return_value=SimpleNamespace(get=lambda *a: target)), \
             patch('agents.gateway.get_gateway', return_value=gateway):
            result = await self.resolve(record)
        self.assertEqual(result['continuation'], 'resumed')
        self.assertIn('COMMAND_COMPLETED', (await self.result())['messages'][0].content)
        gateway.trigger.assert_not_awaited()
        self.system.run.assert_not_awaited()

    async def test_failed_keep_registration_returns_final_denial(self):
        record = await self.pending()
        with patch('webot.permission_context.remember_approval_in_policy', side_effect=OSError('save failed')):
            result = await self.resolve(record, remember=True)
        self.assertEqual(result['approval']['status'], 'denied')
        self.assertFalse(result['approval']['remember'])
        self.assertFalse((await self.result()).get('_conversation_approval_prompts'))
        self.system.run.assert_not_awaited()
        self.assertFalse(self.output.exists())

    async def test_cancellation_expires_waiter_without_executing(self):
        record = await self.pending()
        self.task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await self.task
        expired = runtime_store.get_tool_approval(record.approval_id, 'alice')
        self.assertEqual(expired.status, 'expired')
        self.assertFalse(approval_review.has_live_approval_waiter(expired))
        self.assertFalse(self.output.exists())

    async def test_windows_waiter_probe_never_sends_a_process_signal(self):
        record = SimpleNamespace(approval_id='approval-other', review_metadata_json=json.dumps({'waiter_pid':42}))
        with patch('os.name', 'nt'), patch('os.kill') as signal, \
                patch('subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='"python.exe","42"')):
            self.assertTrue(approval_review.has_live_approval_waiter(record))
            signal.assert_not_called()

    async def test_detached_old_request_still_queues_its_saved_action(self):
        result = await approval_review.authorize_action(user_id='alice', session_id='s', tool_name='run_command',
            args={'command':'echo approved'}, messages=self.messages, wait_for_user=False)
        self.assertTrue(result.pending)
        record = runtime_store.get_tool_approval(result.approval_id, 'alice')
        resolved = await self.resolve(record)
        self.assertEqual(resolved['continuation'], 'queued')
        self.assertEqual(self.system.run.await_args.args[0].approval_resume_id, record.approval_id)
