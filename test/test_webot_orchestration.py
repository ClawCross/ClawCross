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
import mcp_servers.webot as mcp_webot


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

    async def test_background_spawn_dispatches_via_system_trigger(self):
        # Background execution moved out of the webot MCP subprocess; spawn_subagent
        # now fire-and-forgets to the agent main process via /system_trigger and
        # only marks the run as "running" locally. Verify the dispatch contract.
        state = {"calls": [], "callbacks": [], "cancels": []}

        def _client_factory(*args, **kwargs):
            return _FakeAsyncClient(state, delay=0.0)

        with patch.object(mcp_webot, "_INTERNAL_TOKEN", "internal-token"), patch(
            "mcp_servers.webot.httpx.AsyncClient",
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

        self.assertIn("后台运行", result)
        self.assertEqual(len(state["callbacks"]), 1)
        dispatch = state["callbacks"][0]
        self.assertEqual(dispatch["user_id"], "alice")
        self.assertEqual(dispatch["text"], "Inspect runtime")
        self.assertTrue(dispatch["session_id"].startswith("subagent__research__"))

        latest_run = runtime_store.get_latest_run_for_agent("alice", "researcher-1")
        self.assertEqual(latest_run.status, "running")

        listed = await mcp_webot.list_subagents(username="alice")
        self.assertIn("researcher-1", listed)

    async def test_cancel_subagent_stops_runtime_and_updates_registry(self):
        state = {"calls": [], "callbacks": [], "cancels": []}

        def _client_factory(*args, **kwargs):
            return _FakeAsyncClient(state, delay=0.2)

        with patch.object(mcp_webot, "_INTERNAL_TOKEN", "internal-token"), patch(
            "mcp_servers.webot.httpx.AsyncClient",
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

    async def test_ultraplan_start_and_status_create_plan_artifact(self):
        original_runtime_root = mcp_webot._RUNTIME_ROOT
        mcp_webot._RUNTIME_ROOT = Path(self.tmpdir.name) / "runtime_artifacts"
        calls = []

        async def _fake_spawn_subagent(**kwargs):
            self.assertTrue(kwargs.get("wait"))
            calls.append(kwargs)
            is_execute = kwargs["agent_type"] == "coder"
            run_id = "run-execute-1" if is_execute else "run-plan-1"
            agent_id = "coder-agent" if is_execute else "planner-agent"
            session_id = "subagent__coder__plan-alpha-execute" if is_execute else "subagent__planner__plan-alpha"
            runtime_store.upsert_run(
                runtime_store.create_run_record(
                    run_id=run_id,
                    user_id=kwargs["username"],
                    agent_id=agent_id,
                    session_id=session_id,
                    parent_session=kwargs.get("parent_session") or "default",
                    agent_type=kwargs["agent_type"],
                    title=kwargs.get("description") or kwargs["task"][:80],
                    input_text=kwargs["task"],
                    status="completed",
                    timeout_seconds=kwargs.get("timeout", 300),
                    max_turns=kwargs.get("max_turns"),
                    wait_mode=bool(kwargs.get("wait")),
                )
            )
            runtime_store.update_run_status(
                run_id,
                kwargs["username"],
                last_result="Executed migration changes." if is_execute else "Plan complete: add checkpoints.",
            )
            return f"run_id: {run_id}\nsession_id: {session_id}\nagent_id: {agent_id}"

        try:
            with patch.object(mcp_webot, "spawn_subagent", side_effect=_fake_spawn_subagent):
                started = await mcp_webot.ultraplan_start(
                    username="alice",
                    task="Design a resilient auth migration",
                    source_session="default",
                    name="plan-alpha",
                    workspace_mode="worktree",
                )

            self.assertIn("ULTRAPLAN 已完成", started)
            self.assertEqual([item["agent_type"] for item in calls], ["planner", "coder"])
            self.assertIn("不要只输出计划文档", calls[1]["task"])
            plan_run = runtime_store.get_run("run-plan-1", "alice")
            execute_run = runtime_store.get_run("run-execute-1", "alice")
            self.assertIsNotNone(plan_run)
            self.assertIsNotNone(execute_run)
            self.assertEqual(plan_run.run_kind, "ultraplan_plan")
            self.assertEqual(plan_run.mode, "plan")
            self.assertEqual(execute_run.run_kind, "ultraplan")
            self.assertEqual(execute_run.mode, "execute")
            self.assertEqual(
                runtime_store.get_session_mode("alice", "subagent__planner__plan-alpha")["mode"],
                "plan",
            )
            self.assertEqual(
                runtime_store.get_session_mode("alice", "subagent__coder__plan-alpha-execute")["mode"],
                "execute",
            )

            runtime_store.update_run_status(
                "run-execute-1",
                "alice",
                status="completed",
                last_result="Executed migration changes and validation.",
            )

            status = await mcp_webot.ultraplan_status(username="alice", run_id="run-execute-1")
            refreshed = runtime_store.get_run("run-execute-1", "alice")
            artifacts = runtime_store.list_runtime_artifacts("alice", "subagent__coder__plan-alpha-execute", limit=10)

            self.assertIn("status: completed", status)
            self.assertIn("Executed migration", status)
            self.assertTrue(refreshed.metadata.get("artifact_path"))
            self.assertTrue(any(item.kind == "ultraplan_result" for item in artifacts))

            status_from_session = await mcp_webot.ultraplan_status(
                username="alice",
                source_session="default",
            )
            self.assertIn("run_id: run-execute-1", status_from_session)
            self.assertIn("status: completed", status_from_session)
        finally:
            mcp_webot._RUNTIME_ROOT = original_runtime_root

    async def test_ultraplan_start_runs_planner_without_mcp_background_task(self):
        state = {"calls": [], "callbacks": [], "cancels": []}

        def _client_factory(*args, **kwargs):
            return _FakeAsyncClient(state, delay=0.0)

        with patch.object(mcp_webot, "_INTERNAL_TOKEN", "internal-token"), patch(
            "mcp_servers.webot.httpx.AsyncClient",
            new=_client_factory,
        ):
            started = await mcp_webot.ultraplan_start(
                username="alice",
                task="Plan a resilient migration",
                source_session="parent-1",
                name="plan-sync",
            )

        execute_run = runtime_store.get_latest_run_for_agent("alice", "plan-sync-execute")
        plan_run = runtime_store.get_latest_run_for_agent("alice", "plan-sync")
        self.assertIn("ULTRAPLAN 已完成", started)
        self.assertIn("processed: 你是一个专门负责大范围项目规划的 Planner 子 Agent", started)
        self.assertIn("不要只输出计划文档", state["calls"][1][1]["messages"][0]["content"])
        self.assertIsNotNone(plan_run)
        self.assertIsNotNone(execute_run)
        self.assertEqual(plan_run.run_kind, "ultraplan_plan")
        self.assertEqual(execute_run.run_kind, "ultraplan")
        self.assertEqual(execute_run.status, "completed")
        self.assertFalse(mcp_webot._BACKGROUND_TASKS)

    async def test_ultraplan_start_reuses_active_parent_session_plan(self):
        runtime_store.upsert_run(
            runtime_store.create_run_record(
                run_id="run-active-plan",
                user_id="alice",
                agent_id="planner-agent",
                session_id="subagent__planner__plan-alpha",
                parent_session="default",
                agent_type="planner",
                title="Active plan",
                input_text="Plan active work",
                status="running",
                timeout_seconds=300,
                max_turns=24,
                wait_mode=False,
                run_kind="ultraplan",
                mode="plan",
            )
        )

        with patch.object(mcp_webot, "spawn_subagent") as mocked_spawn:
            started = await mcp_webot.ultraplan_start(
                username="alice",
                task="Do not duplicate this plan",
                source_session="default",
            )

        self.assertIn("已有 ULTRAPLAN 正在运行", started)
        self.assertIn("run_id: run-active-plan", started)
        mocked_spawn.assert_not_called()

    async def test_ultrareview_start_and_status_aggregate_child_findings(self):
        original_runtime_root = mcp_webot._RUNTIME_ROOT
        mcp_webot._RUNTIME_ROOT = Path(self.tmpdir.name) / "runtime_artifacts"

        counter = {"value": 0}

        async def _fake_spawn_subagent(**kwargs):
            counter["value"] += 1
            angle_name = kwargs.get("description", f"review-{counter['value']}").split("::")[-1]
            run_id = f"run-review-{counter['value']}"
            session_id = f"subagent__reviewer__review-{counter['value']}"
            agent_id = f"reviewer-{counter['value']}"
            runtime_store.upsert_run(
                runtime_store.create_run_record(
                    run_id=run_id,
                    user_id=kwargs["username"],
                    agent_id=agent_id,
                    session_id=session_id,
                    parent_session=kwargs.get("parent_session") or "default",
                    agent_type=kwargs["agent_type"],
                    title=kwargs.get("description") or kwargs["task"][:80],
                    input_text=kwargs["task"],
                    status="queued",
                    timeout_seconds=kwargs.get("timeout", 300),
                    max_turns=kwargs.get("max_turns"),
                    wait_mode=bool(kwargs.get("wait")),
                    metadata={"angle": angle_name},
                )
            )
            return f"run_id: {run_id}\nsession_id: {session_id}\nagent_id: {agent_id}"

        try:
            with patch.object(mcp_webot, "spawn_subagent", side_effect=_fake_spawn_subagent):
                started = await mcp_webot.ultrareview_start(
                    username="alice",
                    target="/repo",
                    agent_count=2,
                    source_session="default",
                    angles=["security", "logic"],
                )

            self.assertIn("ULTRAREVIEW 已启动", started)
            coordinator = next(
                record
                for record in runtime_store.list_runs_for_session("alice", "default", limit=20)
                if record.run_kind == "ultrareview"
            )
            metadata = json.loads(coordinator.metadata_json)
            self.assertEqual(len(metadata["child_runs"]), 2)

            for child in metadata["child_runs"]:
                runtime_store.update_run_status(
                    child["run_id"],
                    "alice",
                    status="completed",
                    last_result=f"Finding from {child['angle']}",
                )

            status = await mcp_webot.ultrareview_status(username="alice", run_id=coordinator.run_id)
            refreshed = runtime_store.get_run(coordinator.run_id, "alice")
            artifacts = runtime_store.list_runtime_artifacts("alice", "default", limit=20)

            self.assertIn("completed: 2", status)
            self.assertIn("Finding from security", status)
            self.assertEqual(refreshed.status, "completed")
            self.assertTrue(json.loads(refreshed.metadata_json).get("artifact_path"))
            self.assertTrue(any(item.kind == "ultrareview_summary" for item in artifacts))
        finally:
            mcp_webot._RUNTIME_ROOT = original_runtime_root


if __name__ == "__main__":
    unittest.main()
