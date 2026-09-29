import asyncio
import contextlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

if "mcp.server.fastmcp" not in sys.modules:
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")

    class FastMCP:
        def __init__(self, name):
            self.name = name

        def tool(self):
            def _decorator(fn):
                return fn

            return _decorator

        def run(self):
            return None

    fastmcp_module.FastMCP = FastMCP
    sys.modules["mcp.server.fastmcp"] = fastmcp_module

if "httpx" not in sys.modules:
    httpx_module = types.ModuleType("httpx")

    class AsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    httpx_module.AsyncClient = AsyncClient
    sys.modules["httpx"] = httpx_module

if "dotenv" not in sys.modules:
    dotenv_module = types.ModuleType("dotenv")

    def load_dotenv(*args, **kwargs):
        return None

    dotenv_module.load_dotenv = load_dotenv
    sys.modules["dotenv"] = dotenv_module

import webot.subagents as store
import webot.runtime_store as runtime_store
import webot.tools.webot as mcp_webot


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.text)


class _FakeAsyncClient:
    def __init__(self, state, delay=0.0, *args, **kwargs):
        self.state = state
        self.delay = delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, headers=None, json=None):
        self.state["calls"].append((url, json))
        if url.endswith("/v1/chat/completions"):
            if self.delay:
                await asyncio.sleep(self.delay)
            return _FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": f"processed: {json['messages'][0]['content']}",
                            }
                        }
                    ]
                }
            )
        if url.endswith("/system_trigger"):
            self.state["callbacks"].append(json)
            return _FakeResponse({"status": "success"})
        if url.endswith("/session_history"):
            return _FakeResponse(
                {
                    "messages": [
                        {"role": "user", "content": "Inspect runtime"},
                        {
                            "role": "assistant",
                            "content": "processed: Inspect runtime",
                            "tool_calls": [{"name": "read_file"}],
                        },
                    ]
                }
            )
        if url.endswith("/cancel"):
            self.state["cancels"].append(json)
            return _FakeResponse({"status": "success", "cancelled": True})
        return _FakeResponse({"status": "success"})


class WeBotOrchestrationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.original_db_path = store.DEFAULT_DB_PATH
        self.original_runtime_db_path = runtime_store.DEFAULT_DB_PATH
        store.DEFAULT_DB_PATH = Path(self.tmpdir.name) / "webot.subagents.db"
        runtime_store.DEFAULT_DB_PATH = Path(self.tmpdir.name) / "webot.runtime.db"
        mcp_webot._BACKGROUND_TASKS.clear()
        self.addAsyncCleanup(self._cleanup)

    async def _cleanup(self):
        for task in list(mcp_webot._BACKGROUND_TASKS.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        mcp_webot._BACKGROUND_TASKS.clear()
        store.DEFAULT_DB_PATH = self.original_db_path
        runtime_store.DEFAULT_DB_PATH = self.original_runtime_db_path
        self.tmpdir.cleanup()

    async def test_background_spawn_runs_locally_and_notifies_parent(self):
        # spawn_subagent(wait=False) schedules the run on the local scheduler: it
        # calls the internal chat completions endpoint for the subagent session,
        # then reports the result to the parent session via /system_trigger.
        state = {"calls": [], "callbacks": [], "cancels": []}

        def _client_factory(*args, **kwargs):
            return _FakeAsyncClient(state, delay=0.0)

        with patch.object(mcp_webot, "_INTERNAL_TOKEN", "internal-token"), patch(
            "webot.tools.webot.httpx.AsyncClient",
            new=_client_factory,
        ):
            result = await mcp_webot.spawn_subagent(
                username="alice",
                task="Inspect runtime",
                agent_type="research",
                name="Researcher 1",
                wait=False,
                parent_session="parent-1",
            )
            await asyncio.wait_for(
                asyncio.gather(*list(mcp_webot._BACKGROUND_TASKS.values())),
                timeout=10,
            )

        self.assertIn("后台运行", result)
        completion_calls = [url for url, _ in state["calls"] if url.endswith("/v1/chat/completions")]
        self.assertEqual(len(completion_calls), 1)
        self.assertEqual(len(state["callbacks"]), 1)
        notice = state["callbacks"][0]
        self.assertEqual(notice["user_id"], "alice")
        self.assertEqual(notice["session_id"], "parent-1")
        self.assertTrue(notice["text"].startswith("[子 Agent 完成]"))
        self.assertIn("agent_id: researcher-1", notice["text"])
        self.assertIn("processed:", notice["text"])

        latest_run = runtime_store.get_latest_run_for_agent("alice", "researcher-1")
        self.assertEqual(latest_run.status, "completed")

        listed = await mcp_webot.list_subagents(username="alice")
        self.assertIn("researcher-1", listed)

    async def test_cancel_subagent_stops_runtime_and_updates_registry(self):
        state = {"calls": [], "callbacks": [], "cancels": []}

        def _client_factory(*args, **kwargs):
            return _FakeAsyncClient(state, delay=0.2)

        with patch.object(mcp_webot, "_INTERNAL_TOKEN", "internal-token"), patch(
            "webot.tools.webot.httpx.AsyncClient",
            new=_client_factory,
        ):
            await mcp_webot.spawn_subagent(
                username="alice",
                task="Long running work",
                agent_type="general",
                name="Long Runner",
                wait=False,
                parent_session="parent-1",
            )
            await asyncio.sleep(0.05)

            cancelled = await mcp_webot.cancel_subagent(
                username="alice",
                agent_ref="long-runner",
                source_session="parent-1",
            )
            listed = await mcp_webot.list_subagents(username="alice")

        self.assertIn("已取消", cancelled)
        self.assertIn("cancelled", listed)
        self.assertEqual(state["cancels"][0]["session_id"], "subagent__general__long-runner")

    async def test_recover_background_runs_is_safe_noop(self):
        # Background recovery is now handled by the agent main process. The webot
        # MCP subprocess keeps _recover_background_runs as a no-op so existing
        # callers don't need to change; this test pins that contract.
        record = store.create_subagent_record(
            agent_id="recover-me",
            user_id="alice",
            session_id="subagent__research__recover-me",
            agent_type="research",
            name="recover-me",
            description="recover",
            parent_session="parent-1",
            status="queued",
        )
        store.upsert_subagent(record)

        await mcp_webot._recover_background_runs("alice")

        self.assertNotIn("recover-me", mcp_webot._BACKGROUND_TASKS)


if __name__ == "__main__":
    unittest.main()
