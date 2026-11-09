import asyncio
import contextlib
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.agent_runtime_state import TaskRegistry, ThreadStateRegistry


class TaskRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_and_unregister_tracks_keys(self):
        registry = TaskRegistry()

        async def sleeper():
            await asyncio.sleep(3600)

        task = asyncio.create_task(sleeper())
        registry.register("user:alpha", task)

        self.assertEqual(registry.list_keys(), ["user:alpha"])
        self.assertEqual(registry.list_keys("user:"), ["user:alpha"])
        self.assertEqual(registry.list_keys("other:"), [])

        registry.unregister("user:alpha")
        self.assertEqual(registry.list_keys(), [])

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def test_cancel_returns_false_for_missing_task(self):
        registry = TaskRegistry()
        self.assertFalse(await registry.cancel("missing-task"))

    async def test_cancel_active_task_returns_true_and_removes_key(self):
        registry = TaskRegistry()
        started = asyncio.Event()
        finished = asyncio.Event()

        async def worker():
            started.set()
            try:
                await asyncio.sleep(3600)
            finally:
                finished.set()

        task = asyncio.create_task(worker())
        await started.wait()
        registry.register("session:123", task)

        cancelled = await registry.cancel("session:123", timeout_seconds=0.2)

        self.assertTrue(cancelled)
        self.assertEqual(registry.list_keys(), [])
        await asyncio.wait_for(finished.wait(), timeout=0.5)
        self.assertTrue(task.cancelled())


class ThreadStateRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_messages_and_busy_state_are_reported(self):
        registry = ThreadStateRegistry()
        thread_id = "thread-42"

        lock = await registry.get_lock(thread_id)
        self.assertFalse(registry.is_thread_busy(thread_id))
        self.assertEqual(registry.consume_pending_system_messages(thread_id), 0)

        registry.add_pending_system_message(thread_id)
        registry.add_pending_system_message(thread_id)
        self.assertTrue(registry.has_pending_system_messages(thread_id))

        async with lock:
            registry.set_thread_busy_source(thread_id, "system")
            status = registry.get_all_thread_status("thread-")
            self.assertEqual(
                status[thread_id],
                {
                    "busy": True,
                    "source": "system",
                    "pending_system": 2,
                },
            )
            self.assertEqual(registry.get_thread_busy_source(thread_id), "system")

        self.assertFalse(registry.is_thread_busy(thread_id))
        self.assertEqual(registry.get_thread_busy_source(thread_id), "")
        self.assertEqual(registry.consume_pending_system_messages(thread_id), 2)
        self.assertFalse(registry.has_pending_system_messages(thread_id))


if __name__ == "__main__":
    unittest.main()
