"""Human controls resume the saved action once, in its original conversation."""
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
from webot.engine.agent import TeamAgent, UserAwareToolNode
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

    async def pending(self):
        self.state = {'user_id': 'alice', 'session_id': 's', 'session_mode': 'manual', 'enabled_tools': ['run_command'],
            'messages': self.messages + [AIMessage(content='', tool_calls=[{'id': 'original-call', 'name': 'run_command', 'args': {'command': 'echo approved'}}])]}
        result = await self.node(self.state, {})
        self.assertFalse(self.output.exists())
        self.assertTrue(result['_conversation_approval_prompts'])
        return runtime_store.list_tool_approvals('alice', 's', status='pending')[0]

    async def resolve(self, record, **overrides):
        req = WeBotApprovalResolutionRequest(user_id='alice', approval_id=record.approval_id, session_id='s', **overrides)
        return await self.service.resolve_tool_approval(req, 'test-token')

    async def test_button_resumes_exact_tool_once_and_preserves_group_and_scope(self):
        record = await self.pending()
        result = await self.resolve(record)
        self.assertEqual(result['continuation'], 'queued')
        req = self.system.run.await_args.args[0]
        self.assertEqual(req.groups, self.groups)
        self.assertEqual(req.enabled_tools, ['run_command'])
        self.assertEqual(req.session_mode, 'manual')
        engine = TeamAgent.__new__(TeamAgent)
        system = SystemService(agent=engine)
        state = system._build_system_input(req, HumanMessage(content=req.text, additional_kwargs={'input_origin': 'system'}))
        state['messages'] = self.state['messages'] + state['messages']
        update = await engine._call_model(state)
        self.assertEqual(update['_approval_resume_id'], '')
        state['messages'] += update['messages']
        tools = await self.node(state, {})
        self.assertIn('COMMAND_COMPLETED', tools['messages'][0].content)
        self.assertEqual(self.executions, [('echo approved', 'alice', 's')])
        self.assertTrue(self.output.exists())
        self.assertEqual(runtime_store.get_tool_approval(record.approval_id, 'alice').status, 'used')
        with self.assertRaises(HTTPException) as caught:
            await self.resolve(record)
        self.assertEqual(caught.exception.status_code, 409)
        self.system.run.assert_awaited_once()
        replay = await engine._call_model({**state, '_approval_resume_id': record.approval_id})
        self.assertFalse(replay['messages'][0].tool_calls)
        self.assertEqual(len(self.executions), 1)

    async def test_deny_continues_without_executing(self):
        record = await self.pending()
        await self.resolve(record, action='deny')
        req = self.system.run.await_args.args[0]
        self.assertEqual(req.approval_resume_id, '')
        self.assertIn('不要重试', req.text)
        self.assertEqual(runtime_store.get_tool_approval(record.approval_id, 'alice').status, 'denied')
        self.assertFalse(self.output.exists())

    async def test_cli_text_reply_resumes_without_sending_bare_y_to_model(self):
        record = await self.pending()
        human = HumanMessage(content='Y', id='reply-1', additional_kwargs={'input_origin': 'user'})
        engine = TeamAgent.__new__(TeamAgent)
        update = await engine._call_model({**self.state, 'messages': self.state['messages'] + [human]})
        self.assertEqual(update['_approval_resume_id'], '')
        self.assertEqual(update['messages'][0].tool_calls[0]['args']['command'], 'echo approved')
        self.assertEqual(runtime_store.get_tool_approval(record.approval_id, 'alice').status, 'approved')

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

    async def test_external_agent_is_continued_through_gateway(self):
        record = await self.pending()
        target = SimpleNamespace(driver='acpx')
        gateway = SimpleNamespace(trigger=AsyncMock(return_value=SimpleNamespace(accepted=True)))
        with patch('agents.store.get_store', return_value=SimpleNamespace(get=lambda *a: target)), \
             patch('agents.gateway.get_gateway', return_value=gateway):
            result = await self.resolve(record)
        self.assertEqual(result['continuation'], 'queued')
        gateway.trigger.assert_awaited_once()
        self.assertEqual(gateway.trigger.await_args.kwargs['context']['groups'], self.groups)
        self.system.run.assert_not_awaited()

    async def test_failed_keep_registration_does_not_resume_as_an_approval(self):
        record = await self.pending()
        with patch('webot.permission_context.remember_approval_in_policy', side_effect=OSError('save failed')):
            result = await self.resolve(record, remember=True)
        self.assertEqual(result['approval']['status'], 'denied')
        self.assertFalse(result['approval']['remember'])
        request = self.system.run.await_args.args[0]
        self.assertEqual(request.approval_resume_id, '')
        self.assertNotIn('系统将重试原操作', request.text)
        self.assertFalse(self.output.exists())
