import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from api.system_models import SystemTriggerRequest
from api.system_service import SystemService
from webot import runtime_store


class _FakeAgentApp:
    def __init__(self):
        self.inputs = []

    async def astream_events(self, system_input, config, version, durability):
        self.inputs.append(system_input)
        yield {"event": "done", "config": config, "version": version, "durability": durability}

    async def aget_state(self, config):
        class Snapshot:
            values = {"messages": []}

        return Snapshot()

    async def aupdate_state(self, config, values):
        return None


class _FakeAgent:
    def __init__(self):
        self.agent_app = _FakeAgentApp()
        self.locks = {}
        self.pending_count = 0
        self.registered = []
        self.purged = []

    async def get_thread_lock(self, thread_id):
        if thread_id not in self.locks:
            self.locks[thread_id] = asyncio.Lock()
        return self.locks[thread_id]

    def register_task(self, task_key, task):
        self.registered.append((task_key, task))

    def unregister_task(self, task_key):
        self.registered.append((task_key, None))

    def set_thread_busy_source(self, thread_id, source):
        return None

    def clear_thread_busy_source(self, thread_id):
        return None

    def add_pending_system_message(self, thread_id):
        self.pending_count += 1

    async def purge_checkpoints(self, thread_id):
        self.purged.append(thread_id)


async def _wait_for(predicate, timeout=1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


class SystemServiceCoalescingTests(unittest.IsolatedAsyncioTestCase):
    async def test_coalesces_group_triggers_waiting_on_thread_lock(self):
        agent = _FakeAgent()
        service = SystemService(
            agent=agent,
            verify_internal_token=lambda token: None,
            coalesce_debounce_seconds=0.01,
        )
        thread_id = "alice#agent-session"
        thread_lock = await agent.get_thread_lock(thread_id)
        await thread_lock.acquire()
        try:
            first = await service.system_trigger(
                SystemTriggerRequest(
                    user_id="alice",
                    session_id="agent-session",
                    text="第一条：先看这个需求",
                    coalesce_key="group:demo:agent:agent-session",
                ),
                "token",
            )
            second = await service.system_trigger(
                SystemTriggerRequest(
                    user_id="alice",
                    session_id="agent-session",
                    text="第二条：补充一个限制条件",
                    coalesce_key="group:demo:agent:agent-session",
                ),
                "token",
            )
            await asyncio.sleep(0.05)
            self.assertTrue(first["coalesced"])
            self.assertTrue(second["coalesced"])
            self.assertEqual(len(agent.agent_app.inputs), 0)
        finally:
            thread_lock.release()

        await _wait_for(lambda: len(agent.agent_app.inputs) == 1)
        message = agent.agent_app.inputs[0]["messages"][0]
        self.assertIsInstance(message.content, str)
        self.assertIn("[群聊未读消息批量投递]", message.content)
        self.assertIn("==================== 群聊消息 1/2 开始 ====================", message.content)
        self.assertIn("第一条：先看这个需求", message.content)
        self.assertIn("==================== 群聊消息 1/2 结束 ====================", message.content)
        self.assertIn("==================== 群聊消息 2/2 开始 ====================", message.content)
        self.assertIn("第二条：补充一个限制条件", message.content)
        self.assertIn("==================== 群聊消息 2/2 结束 ====================", message.content)
        self.assertEqual(agent.pending_count, 1)

    async def test_non_coalesced_trigger_keeps_single_message_behavior(self):
        agent = _FakeAgent()
        service = SystemService(
            agent=agent,
            verify_internal_token=lambda token: None,
            coalesce_debounce_seconds=0.01,
        )

        result = await service.system_trigger(
            SystemTriggerRequest(
                user_id="alice",
                session_id="agent-session",
                text="普通系统触发",
            ),
            "token",
        )

        await _wait_for(lambda: len(agent.agent_app.inputs) == 1)
        message = agent.agent_app.inputs[0]["messages"][0]
        self.assertFalse(result["coalesced"])
        self.assertEqual(message.content, "普通系统触发")

    async def test_new_trigger_clears_previous_reply_schema(self):
        agent = _FakeAgent()
        service = SystemService(agent=agent, verify_internal_token=lambda token: None)
        first = service._build_system_input(
            SystemTriggerRequest(
                user_id="alice", session_id="agent-session", text="first",
                response_format={"type": "json_schema", "json_schema": {"name": "Reply", "schema": {"type": "object"}}},
            ),
            None,
        )
        second = service._build_system_input(
            SystemTriggerRequest(user_id="alice", session_id="agent-session", text="second"),
            None,
        )
        self.assertIsNotNone(first["response_format"])
        self.assertIn("response_format", second)
        self.assertIsNone(second["response_format"])


class _ConversationApp(_FakeAgentApp):
    """Appends one turn to a running transcript, like the real graph does."""

    def __init__(self, turn):
        super().__init__()
        self.messages = []
        self.turn = turn

    async def astream_events(self, system_input, config, version, durability):
        self.inputs.append(system_input)
        self.messages.extend(system_input["messages"])
        self.messages.extend(self.turn)
        yield {"event": "done"}

    async def aget_state(self, config):
        messages = list(self.messages)

        class Snapshot:
            values = {"messages": messages}

        return Snapshot()


class SystemTriggerWaitReplyTests(unittest.IsolatedAsyncioTestCase):
    def _service(self, turn):
        from langchain_core.messages import AIMessage  # noqa: F401  (turn built by callers)

        agent = _FakeAgent()
        agent.agent_app = _ConversationApp(turn)
        # No cancel_task on the fake: a waiting trigger must never reach for it.
        return agent, SystemService(agent=agent, verify_internal_token=lambda token: None)

    async def test_waits_behind_the_running_turn_then_returns_the_reply(self):
        from langchain_core.messages import AIMessage, ToolMessage

        agent, service = self._service([
            AIMessage(content="", tool_calls=[{"name": "read_file", "args": {}, "id": "c1"}]),
            ToolMessage(content="file text", tool_call_id="c1"),
            AIMessage(content="the answer"),
        ])
        lock = await agent.get_thread_lock("bob#s1")
        await lock.acquire()  # the session is busy with its current turn
        call = asyncio.create_task(service.system_trigger(
            SystemTriggerRequest(user_id="bob", session_id="s1", text="question", wait_reply=True), None
        ))
        await asyncio.sleep(0.05)
        self.assertFalse(call.done())
        self.assertEqual(agent.agent_app.inputs, [])  # queued, not run, not interrupting
        lock.release()
        result = await asyncio.wait_for(call, 1)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reply"], "the answer")

    async def test_a_turn_without_a_final_answer_replies_empty_not_an_older_answer(self):
        from langchain_core.messages import AIMessage, HumanMessage

        agent, service = self._service([AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "c1"}])])
        agent.agent_app.messages = [HumanMessage(content="earlier"), AIMessage(content="earlier answer")]
        result = await service.system_trigger(
            SystemTriggerRequest(user_id="bob", session_id="s1", text="question", wait_reply=True), None
        )
        self.assertEqual(result["reply"], "")


class DurableInboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_session_message_waits_for_idle_then_marks_delivered(self):
        with TemporaryDirectory() as tmpdir, patch.object(runtime_store, "DEFAULT_DB_PATH", Path(tmpdir) / "runtime.db"):
            agent = _FakeAgent()
            service = SystemService(agent=agent, verify_internal_token=lambda token: None)
            lock = await agent.get_thread_lock("alice#worker")
            await lock.acquire()
            try:
                result = await service.system_trigger(SystemTriggerRequest(
                    user_id="alice", session_id="worker", text="Check the build",
                    inbox_source_session="main", inbox_source_user="alice",
                ), None)
                self.assertEqual(result["status"], "queued")
                self.assertEqual(len(runtime_store.list_inbox_messages("alice", "worker", status="queued")), 1)
                self.assertEqual(agent.agent_app.inputs, [])
            finally:
                lock.release()
            await _wait_for(lambda: bool(runtime_store.list_inbox_messages("alice", "worker", status="delivered")))
            notice = agent.agent_app.inputs[0]["messages"][0].content
            self.assertIn("你有 1 条未读消息", notice)
            self.assertIn("Check the build", notice)
            self.assertIn("read_session_inbox", notice)
            self.assertFalse(runtime_store.list_inbox_messages("alice", "worker", status="delivered")[0].read_at)

    async def test_busy_session_gets_one_digest_without_full_bodies(self):
        with TemporaryDirectory() as tmpdir, patch.object(runtime_store, "DEFAULT_DB_PATH", Path(tmpdir) / "runtime.db"):
            agent = _FakeAgent()
            service = SystemService(agent=agent, verify_internal_token=lambda token: None)
            lock = await agent.get_thread_lock("alice#worker")
            await lock.acquire()
            try:
                for index in (1, 2):
                    await service.system_trigger(SystemTriggerRequest(
                        user_id="alice", session_id="worker",
                        text=f"private body {index} " + "x" * 110 + " SECRET",
                        inbox_source_session="main", inbox_summary=f"Task {index}",
                    ), None)
                self.assertEqual(agent.agent_app.inputs, [])
            finally:
                lock.release()
            await _wait_for(lambda: len(runtime_store.list_inbox_messages("alice", "worker", status="delivered")) == 2)
            self.assertEqual(len(agent.agent_app.inputs), 1)
            notice = agent.agent_app.inputs[0]["messages"][0].content
            self.assertIn("你有 2 条未读消息", notice)
            self.assertIn("Task 1", notice)
            self.assertIn("Task 2", notice)
            self.assertNotIn("SECRET", notice)

    async def test_what_came_with_an_entry_comes_with_its_notice(self):
        with TemporaryDirectory() as tmpdir, patch.object(runtime_store, "DEFAULT_DB_PATH", Path(tmpdir) / "runtime.db"), \
                patch("services.message_builder._is_vision_model", return_value=True):
            agent = _FakeAgent()
            service = SystemService(agent=agent, verify_internal_token=lambda token: None)
            await service.system_trigger(SystemTriggerRequest(
                user_id="alice", session_id="worker", text="看这张图", inbox_source_session="u:alice",
                attachments=[{"type": "image", "name": "a.png", "mime_type": "image/png", "data": "iVBORw0KGgo="}],
            ), None)
            await _wait_for(lambda: bool(runtime_store.list_inbox_messages("alice", "worker", status="delivered")))
            parts = agent.agent_app.inputs[0]["messages"][0].content
            self.assertIsInstance(parts, list)
            self.assertIn("[收件箱通知]", parts[0]["text"])
            self.assertTrue(any(p.get("type") == "image_url" for p in parts))

    async def test_wait_reply_uses_the_same_durable_inbox(self):
        from langchain_core.messages import AIMessage

        with TemporaryDirectory() as tmpdir, patch.object(runtime_store, "DEFAULT_DB_PATH", Path(tmpdir) / "runtime.db"):
            agent = _FakeAgent()
            agent.agent_app = _ConversationApp([AIMessage(content="done")])
            service = SystemService(agent=agent, verify_internal_token=lambda token: None)
            result = await service.system_trigger(SystemTriggerRequest(
                user_id="alice", session_id="worker", text="Do it",
                inbox_source_session="main", wait_reply=True,
            ), None)
            self.assertEqual(result["reply"], "done")
            self.assertEqual(len(runtime_store.list_inbox_messages("alice", "worker", status="delivered")), 1)
            self.assertTrue(runtime_store.list_inbox_messages("alice", "worker", status="delivered")[0].read_at)
            self.assertIn("直接用文字回答", agent.agent_app.inputs[0]["messages"][0].content)

    async def test_passive_digest_then_waiting_message_keep_distinct_replies(self):
        from langchain_core.messages import AIMessage

        with TemporaryDirectory() as tmpdir, patch.object(runtime_store, "DEFAULT_DB_PATH", Path(tmpdir) / "runtime.db"):
            agent = _FakeAgent()
            agent.agent_app = _ConversationApp([AIMessage(content="reply")])
            service = SystemService(agent=agent, verify_internal_token=lambda token: None)
            lock = await agent.get_thread_lock("alice#worker")
            await lock.acquire()
            try:
                await service.system_trigger(SystemTriggerRequest(
                    user_id="alice", session_id="worker", text="FYI",
                    inbox_source_session="main",
                ), None)
                waiting = asyncio.create_task(service.system_trigger(SystemTriggerRequest(
                    user_id="alice", session_id="worker", text="please answer",
                    inbox_source_session="main", wait_reply=True,
                ), None))
                await _wait_for(lambda: runtime_store.count_inbox_messages("alice", "worker", status="queued") == 2)
            finally:
                lock.release()
            result = await asyncio.wait_for(waiting, 1)
            self.assertEqual(result["reply"], "reply")
            self.assertEqual(len(agent.agent_app.inputs), 2)
            self.assertIn("[收件箱通知]", agent.agent_app.inputs[0]["messages"][0].content)
            self.assertIn("please answer", agent.agent_app.inputs[1]["messages"][0].content)
            self.assertEqual(runtime_store.count_inbox_messages("alice", "worker", status="unread"), 1)

    async def test_restart_resumes_queued_inbox(self):
        with TemporaryDirectory() as tmpdir, patch.object(runtime_store, "DEFAULT_DB_PATH", Path(tmpdir) / "runtime.db"):
            runtime_store.create_inbox_message("alice", source_session="main", target_session="worker", content="after restart")
            agent = _FakeAgent()
            service = SystemService(agent=agent, verify_internal_token=lambda token: None)
            await service.resume_queued_inbox()
            await _wait_for(lambda: bool(runtime_store.list_inbox_messages("alice", "worker", status="delivered")))
            self.assertIn("after restart", agent.agent_app.inputs[0]["messages"][0].content)

    async def test_failed_delivery_stays_queued_and_waiter_returns(self):
        class FailingApp(_FakeAgentApp):
            async def astream_events(self, system_input, config, version, durability):
                raise RuntimeError("provider unavailable")
                yield  # pragma: no cover

        with TemporaryDirectory() as tmpdir, patch.object(runtime_store, "DEFAULT_DB_PATH", Path(tmpdir) / "runtime.db"):
            agent = _FakeAgent()
            agent.agent_app = FailingApp()
            service = SystemService(agent=agent, verify_internal_token=lambda token: None)
            result = await asyncio.wait_for(service.system_trigger(SystemTriggerRequest(
                user_id="alice", session_id="worker", text="try later",
                inbox_source_session="main", wait_reply=True,
            ), None), 1)
            self.assertEqual(result["status"], "queued")
            self.assertEqual(len(runtime_store.list_inbox_messages("alice", "worker", status="queued")), 1)

    async def test_failed_digest_stays_queued_for_restart(self):
        class FailingApp(_FakeAgentApp):
            async def astream_events(self, system_input, config, version, durability):
                raise RuntimeError("provider unavailable")
                yield  # pragma: no cover

        with TemporaryDirectory() as tmpdir, patch.object(runtime_store, "DEFAULT_DB_PATH", Path(tmpdir) / "runtime.db"):
            agent = _FakeAgent()
            agent.agent_app = FailingApp()
            service = SystemService(agent=agent, verify_internal_token=lambda token: None)
            await service.system_trigger(SystemTriggerRequest(
                user_id="alice", session_id="worker", text="passive",
                inbox_source_session="main",
            ), None)
            await _wait_for(lambda: not service._inbox_tasks)
            self.assertEqual(runtime_store.count_inbox_messages("alice", "worker", status="queued"), 1)


if __name__ == "__main__":
    unittest.main()
