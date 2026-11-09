import asyncio
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from api.system_models import SystemTriggerRequest
from api.system_service import SystemService


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


if __name__ == "__main__":
    unittest.main()
