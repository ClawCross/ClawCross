"""External sessions receive/read the same durable, isolated inbox as WeBot."""
import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "backend"))
from agents.messages import AgentMessage, AgentReply
from agents.store import ACPX, AgentStore
from external import session, tool_bridge
from external.acp import AcpRuntime
from webot import runtime_store
from webot.mcp.webot import read_session_inbox


class ExternalInboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.enterContext(patch.object(runtime_store, "DEFAULT_DB_PATH", Path(self.tmp.name) / "runtime.db"))
        self.store = AgentStore(Path(self.tmp.name) / "agents.db")
        self.agent = self.store.create("inbox-test", driver=ACPX, config={"platform": "codex"})
        self.runtime = AcpRuntime(self.store)
        self.calls = []
        async def send(agent, msg, **kwargs):
            self.calls.append((agent, msg, kwargs))
            return AgentReply(ok=True, content="done")
        self.runtime._ask_turn = send

    async def drain(self, runtime=None):
        tasks = list((runtime or self.runtime)._inbox_tasks.values())
        await asyncio.wait_for(asyncio.gather(*tasks), 2)

    async def test_idle_delivery_is_durable_and_body_is_read_on_demand(self):
        async with session.turn(self.store, self.agent):
            await self.runtime.inbox(self.agent, AgentMessage(text="FULL BODY", sender="peer", summary="short preview"),
                context={"delivery_id": "delivery-one"}, mode="readonly", on_complete=None)
            await asyncio.sleep(0)
            self.assertEqual(self.calls, [])
            self.assertEqual(runtime_store.count_inbox_messages("inbox-test", self.agent.agent_id, status="queued"), 1)
        await self.drain()
        self.assertNotIn("FULL BODY", self.calls[0][1].text)
        self.assertEqual(runtime_store.count_inbox_messages("inbox-test", self.agent.agent_id, status="unread"), 1)
        result = await read_session_inbox("inbox-test", source_session=self.agent.agent_id)
        self.assertIn("FULL BODY", result)
        self.assertEqual(runtime_store.count_inbox_messages("inbox-test", self.agent.agent_id, status="unread"), 0)

    async def test_mcp_result_gets_new_notice_and_read_cancels_duplicate_wake(self):
        prepare = lambda: session.prepare_turn(self.agent, AgentMessage(text="work"), context={},
            mode="readonly", enabled_tools=None, response_format=None)
        async with session.turn(self.store, self.agent):
            prepared = prepare()
            with tool_bridge.active_turn(self.agent, AgentMessage(text="work"), {}, "readonly", None, prepared=prepared):
                await self.runtime.inbox(self.agent, AgentMessage(text="MESSAGE SECRET", sender="peer", summary="new message"),
                    context={}, mode=None, on_complete=None)
                turn = tool_bridge._active[(self.agent.owner, self.agent.agent_id)]
                notice = tool_bridge.attach_runtime_context({}, self.agent, turn)
                self.assertIn("【本轮 inbox】", notice["runtime_context"])
                self.assertNotIn("MESSAGE SECRET", notice["runtime_context"])
                result = await read_session_inbox(self.agent.owner, source_session=self.agent.agent_id)
                self.assertIn("MESSAGE SECRET", result)
                cleared = tool_bridge.attach_runtime_context({}, self.agent, turn)
                self.assertIn("此前提供的此项信息已撤销", cleared["runtime_context"])
        await self.drain()
        self.assertEqual(self.calls, [])

    async def test_failure_is_queued_and_restart_resume_uses_current_settings(self):
        self.runtime._ask_turn = unittest.mock.AsyncMock(return_value=AgentReply(ok=False, error="offline"))
        await self.runtime.inbox(self.agent, AgentMessage(text="recover", sender="peer"), context={}, mode=None, on_complete=None)
        await self.drain()
        self.assertEqual(runtime_store.count_inbox_messages(self.agent.owner, self.agent.agent_id, status="queued"), 1)
        restarted = AcpRuntime(self.store)
        restarted._ask_turn = self.runtime._ask_turn = unittest.mock.AsyncMock(return_value=AgentReply(ok=True))
        restarted.resume_inbox(self.agent)
        await self.drain(restarted)
        self.assertEqual(runtime_store.count_inbox_messages(self.agent.owner, self.agent.agent_id, status="queued"), 0)

    async def test_group_context_dedup_and_plain_chat_reply(self):
        callbacks = []
        context = {"conversation_id": "group-one", "delivery_id": "relay:one", "groups": [{"group_id": "group-one"}],
            "group_human_requests": [{"id": "request-one", "text": "hello"}]}
        await self.runtime.inbox(self.agent, AgentMessage(text="hello", sender="u:friend"), context=context,
            mode="chat", on_complete=callbacks.append)
        await self.drain()
        self.assertEqual(self.calls[0][1].text, "hello")
        self.assertEqual(self.calls[0][2]["context"]["groups"], context["groups"])
        self.assertEqual(callbacks[0].content, "done")
        await self.runtime.inbox(self.agent, AgentMessage(text="hello", sender="u:friend"), context=context,
            mode="chat", on_complete=None)
        self.assertEqual(len(self.calls), 1)

    async def test_reader_cannot_read_another_user_or_agent(self):
        other = runtime_store.create_inbox_message("other-user", target_session=self.agent.agent_id, content="PRIVATE")
        result = await read_session_inbox(self.agent.owner, [other.message_id], source_session=self.agent.agent_id)
        self.assertNotIn("PRIVATE", result)
        self.assertIn("未找到", result)

    async def test_startup_routes_external_inbox_to_its_runtime(self):
        from webot.api.system_service import SystemService
        from types import SimpleNamespace
        runtime_store.create_inbox_message(self.agent.owner, target_session=self.agent.agent_id, content="recover")
        service = SystemService(agent=SimpleNamespace())
        with patch('agents.store.get_store', return_value=self.store), patch('agents.gateway.get_gateway',
            return_value=SimpleNamespace(runtime=lambda agent:self.runtime)):
            await service.resume_queued_inbox()
            await self.drain()
        self.assertEqual(service._inbox_tasks, {})
        self.assertEqual(len(self.calls), 1)

    async def test_workspace_can_change_before_first_prompt_and_preserves_native_history(self):
        root = Path(self.tmp.name)
        first = root / 'first'; second = root / 'second'
        first.mkdir(); second.mkdir()
        config = {**self.agent.config, 'workspaces': {'companion':False, 'user_shared':False,
            'teams':False, 'cli':False, 'paths':[str(second)]}}
        agent = self.store.update(self.agent.owner, self.agent.agent_id, config=config)
        self.store.patch_runtime(agent.owner, agent.agent_id, {'acp_cwd':str(first)})
        agent = self.store.require(agent.owner, agent.agent_id)
        self.assertEqual(session.native_workspace_cwd(agent), str(second))
        self.store.patch_runtime(agent.owner, agent.agent_id, {'native_resume_id':'native-thread'})
        agent = self.store.require(agent.owner, agent.agent_id)
        self.assertEqual(session.native_workspace_cwd(agent), str(first))

    async def test_external_mcp_read_injects_identity_and_clears_live_notice(self):
        import httpx
        from fastapi import FastAPI
        from types import SimpleNamespace
        from langchain_core.tools import StructuredTool
        tool = StructuredTool.from_function(coroutine=read_session_inbox, name='read_session_inbox')
        engine = SimpleNamespace(_mcp_tools=[tool], _tool_registry=None)
        record = runtime_store.create_inbox_message(self.agent.owner, target_session=self.agent.agent_id,
            content='MCP FULL BODY', title='preview', source_session='peer')
        msg = AgentMessage(text='read my mailbox', sender='u:'+self.agent.owner)
        prepared = session.prepare_turn(self.agent, msg, context={}, mode='readonly', enabled_tools=None, response_format=None)
        app = FastAPI(); app.include_router(tool_bridge.bridge_router())
        tool_bridge._tokens['inbox-test-token'] = (self.agent.owner, self.agent.agent_id)
        try:
            with patch('external.tool_bridge.get_store', return_value=self.store), patch('external.tool_bridge.get_gateway',
                return_value=SimpleNamespace(runtimes={'webot':SimpleNamespace(engine=engine)})):
                with tool_bridge.active_turn(self.agent, msg, {}, 'readonly', None, prepared=prepared):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                        response = await client.post('/external/tool-bridge', headers={'Authorization':'Bearer inbox-test-token'},
                            json={'action':'call','name':'read_session_inbox','arguments':{}})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('MCP FULL BODY', response.json()['results'][0]['content'])
            self.assertIn('此前提供的此项信息已撤销', response.json()['runtime_context'])
            self.assertTrue(runtime_store.get_inbox_message(self.agent.owner, self.agent.agent_id, record.message_id).read_at)
        finally:
            tool_bridge._tokens.pop('inbox-test-token', None)


if __name__ == "__main__":
    unittest.main()
