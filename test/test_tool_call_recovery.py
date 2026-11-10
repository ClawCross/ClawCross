"""Discovery failures stay in the model/tool loop without widening tool permissions."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from webot import runtime_store
from webot.engine.agent import TeamAgent, UserAwareToolNode
from webot.engine.lazy_tool_discovery import LazyToolRegistry
from webot.engine.lightweight_agent_runtime import LightweightAgentRuntime
from webot.runtime import should_stop_for_turn_limit


class ToolCallRecovery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch = patch.object(runtime_store, 'AGENT_RUNTIME_DB_DIR', Path(self.temp.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.executed = []
        schema = {'type': 'object', 'properties': {
            'filename': {'type': 'string'}, 'username': {'type': 'string'},
            'session_id': {'type': 'string'}, 'content': {'type': 'string'},
            'mode': {'type': 'string'},
        }, 'required': ['filename']}
        def record(**args):
            self.executed.append(args)
            return 'file contents'
        tools = [StructuredTool(name=name, description=name, args_schema=schema, func=record)
                 for name in ('read_file', 'write_file')]
        registry = LazyToolRegistry()
        registry.register_tools(tools)
        registry.set_always_loaded({'read_file', 'write_file'})
        self.node = UserAwareToolNode(tools, lambda *_: {'tools': ['read_file', 'write_file']}, registry)
        self.agent = TeamAgent.__new__(TeamAgent)
        self.agent._internal_tool_names = {'read_file', 'write_file', 'tool_call', 'tool_search'}

    def call(self, target, arguments='{"filename":"notes.txt"}', call_id='c1'):
        return AIMessage(content='', tool_calls=[{'name': 'tool_call', 'id': call_id,
            'args': {'tool_name': target, 'arguments_json': arguments}}])

    def state(self, message, **overrides):
        return {'user_id': 'alice', 'session_id': 'recovery', 'session_mode': 'bypass',
                'messages': [HumanMessage(content='Read my notes'), message], **overrides}

    def permission(self, **_):
        return SimpleNamespace(allowed=True, requires_approval=False, reason='',
                               matched_rule=None, policy={}, approval=None)

    async def invoke(self, state):
        with patch('webot.engine.agent.resolve_permission_context', side_effect=self.permission), \
             patch('webot.engine.agent.run_tool_policy_hooks',
                   side_effect=lambda *_, args=None, **__: SimpleNamespace(args=dict(args or {}), decision=None)):
            return await self.node(state, {})

    async def test_resident_tool_and_retired_alias_use_normal_execution_and_identity(self):
        for name, arguments in [('read_file', '{"filename":"notes.txt"}'),
                                ('append_file', '{"filename":"notes.txt","content":"more"}')]:
            result = await self.invoke(self.state(self.call(name, arguments)))
            self.assertEqual(result['messages'][0].content, 'file contents')
            self.assertEqual(self.executed[-1]['username'], 'alice')
            self.assertEqual(self.executed[-1]['session_id'], 'recovery')
        self.assertEqual(self.executed[-1]['mode'], 'append')

    async def test_wrapped_tool_still_obeys_selection_intrinsic_table_and_mode(self):
        for overrides in ({'enabled_tools': []}, {'session_mode': 'chat'}, {'session_mode': 'readonly'}):
            # write_file must stay blocked even though the wrapper can resolve it.
            result = await self.invoke(self.state(self.call('write_file'), **overrides))
            self.assertNotEqual(result['messages'][0].content, 'file contents')
        self.node._find_internal_session_meta_fn = lambda *_: {'tools': []}
        await self.invoke(self.state(self.call('read_file')))
        self.assertEqual(self.executed, [])

    async def test_invalid_calls_return_repair_feedback_with_original_call_id(self):
        for target, arguments, error in [('missing_tool', '{}', 'unknown_tool'),
                                         ('read_file', '', 'invalid_tool_arguments'),
                                         ('read_file', '{}', 'invalid_tool_arguments')]:
            result = await self.invoke(self.state(self.call(target, arguments)))
            message = result['messages'][0]
            self.assertEqual(message.tool_call_id, 'c1')
            self.assertEqual(message.status, 'error')
            payload = json.loads(message.content)
            self.assertEqual(payload['error_type'], error)
            self.assertTrue(payload['retryable'])
        self.assertEqual(self.executed, [])

    async def test_loop_recovers_from_bad_wrapper_and_unknown_direct_name_then_finishes(self):
        replies = iter([self.call('missing_tool'),
            AIMessage(content='', tool_calls=[{'name': 'missing_tool', 'id': 'c2', 'args': {}}]),
            self.call('read_file', call_id='c3'), AIMessage(content='Here are your notes.')])
        observed = []
        async def model(state, _config):
            observed.append(list(state['messages']))
            return {'messages': [next(replies)]}
        store = SimpleNamespace(load_context=AsyncMock(return_value=[]), append_messages=AsyncMock())
        runtime = LightweightAgentRuntime(call_model=model, call_tools=lambda state, _: self.invoke(state),
            should_continue=self.agent._should_continue, context_store=store)
        result = await runtime.ainvoke(self.state(HumanMessage(content='Read my notes')),
            {'configurable': {'thread_id': 'alice#recovery'}, 'recursion_limit': 10})
        self.assertEqual(result['messages'][-1].content, 'Here are your notes.')
        self.assertEqual(len(observed), 4)
        messages = [m for m in result['messages'] if isinstance(m, ToolMessage)]
        self.assertEqual([m.tool_call_id for m in messages], ['c1', 'c2', 'c3'])
        self.assertEqual(len(self.executed), 1)

    def test_only_declared_external_tools_leave_loop_and_repairs_obey_turn_limit(self):
        state = self.state(AIMessage(content='', tool_calls=[{'name': 'remote_tool', 'id': 'c1', 'args': {}}]))
        self.assertTrue(self.agent._should_continue(state))
        self.assertTrue(should_stop_for_turn_limit(3, 3, state['messages'][-1].tool_calls,
            self.agent._internal_tool_names, external_tool_names=set()))
        state['external_tools'] = [{'name': 'remote_tool'}]
        self.assertFalse(self.agent._should_continue(state))
        self.assertFalse(should_stop_for_turn_limit(3, 3, state['messages'][-1].tool_calls,
            self.agent._internal_tool_names, external_tool_names={'remote_tool'}))
