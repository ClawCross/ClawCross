import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

if "fastapi" not in sys.modules:
    fastapi_stub = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code=None, detail=None):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    fastapi_stub.HTTPException = HTTPException
    sys.modules["fastapi"] = fastapi_stub

from webot.models import WeBotLspRequest, WeBotSubagentHistoryRequest, WeBotSubagentRefRequest
from webot.models import (
    WeBotApprovalResolutionRequest,
    WeBotBridgeAttachRequest,
    WeBotBridgeDetachRequest,
    WeBotBuddyActionRequest,
    WeBotDreamRequest,
    WeBotKairosUpdateRequest,
    WeBotPlanUpdateRequest,
    WeBotSessionInboxListRequest,
    WeBotSessionInboxSendRequest,
    WeBotSessionRuntimeRequest,
    WeBotTodoUpdateRequest,
    WeBotVerificationCreateRequest,
    WeBotVoiceStateUpdateRequest,
    WeBotWorkflowPresetApplyRequest,
)
import webot.memory as webot_memory
import webot.policy as policy
import webot.runtime_store as runtime_store
from webot.service import WeBotService
from webot.subagents import create_subagent_record, upsert_subagent


class HumanMessage:
    def __init__(self, content):
        self.content = content


class AIMessage:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class ToolMessage:
    def __init__(self, content, name=""):
        self.content = content
        self.name = name


class _FakeAgentApp:
    def __init__(self, snapshots):
        self._snapshots = snapshots

    async def aget_state(self, config):
        thread_id = config["configurable"]["thread_id"]
        return types.SimpleNamespace(values={"messages": self._snapshots.get(thread_id, [])})


class _FakeAgent:
    def __init__(self, snapshots, active_keys=None, statuses=None):
        self.agent_app = _FakeAgentApp(snapshots)
        self._active_keys = set(active_keys or [])
        self._statuses = statuses or {}
        self.cancelled = []

    def list_active_task_keys(self, prefix=""):
        return [key for key in self._active_keys if key.startswith(prefix)]

    def get_all_thread_status(self, prefix):
        return {
            key: value
            for key, value in self._statuses.items()
            if key.startswith(prefix)
        }

    def is_thread_busy(self, thread_id):
        if thread_id in self._active_keys:
            return True
        info = self._statuses.get(thread_id) or {}
        return bool(info.get("busy"))

    async def cancel_task(self, task_key):
        self.cancelled.append(task_key)
        self._active_keys.discard(task_key)
        return True


class WeBotServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_history_and_cancel_subagent(self):
        with TemporaryDirectory() as tmpdir:
            import webot.subagents as store
            original_db_path = store.DEFAULT_DB_PATH
            original_runtime_db_path = runtime_store.DEFAULT_DB_PATH
            store.DEFAULT_DB_PATH = Path(tmpdir) / "subagents.db"
            runtime_store.DEFAULT_DB_PATH = Path(tmpdir) / "runtime.db"
            try:
                record = create_subagent_record(
                    agent_id="worker1",
                    user_id="alice",
                    session_id="subagent__research__worker1",
                    agent_type="research",
                    name="worker-1",
                    description="Investigate runtime",
                    parent_session="default",
                    status="running",
                )
                upsert_subagent(record)

                snapshots = {
                    "alice#subagent__research__worker1": [
                        HumanMessage("Inspect runtime"),
                        AIMessage("Done", tool_calls=[{"name": "read_file", "args": {"filename": "README.md"}}]),
                        ToolMessage("README content", name="read_file"),
                    ]
                }
                agent = _FakeAgent(
                    snapshots,
                    active_keys={"alice#subagent__research__worker1"},
                    statuses={"alice#subagent__research__worker1": {"busy": True}},
                )
                service = WeBotService(
                    agent=agent,
                    verify_auth_or_token=lambda user_id, password, token: None,
                    extract_text=lambda content: content if isinstance(content, str) else str(content),
                )

                listed = await service.list_subagents("alice", "", None)
                self.assertEqual(listed["status"], "success")
                self.assertEqual(listed["subagents"][0]["status"], "running")

                history = await service.get_subagent_history(
                    WeBotSubagentHistoryRequest(user_id="alice", agent_ref="worker1", limit=5),
                    None,
                )
                self.assertEqual(history["status"], "success")
                self.assertEqual(len(history["messages"]), 3)
                self.assertEqual(history["messages"][1]["tool_calls"][0]["name"], "read_file")

                cancelled = await service.cancel_subagent(
                    WeBotSubagentRefRequest(user_id="alice", agent_ref="worker1"),
                    None,
                )
                self.assertEqual(cancelled["status"], "success")
                self.assertEqual(cancelled["subagent"]["stored_status"], "cancelled")
                self.assertEqual(agent.cancelled, ["alice#subagent__research__worker1"])
            finally:
                store.DEFAULT_DB_PATH = original_db_path
                runtime_store.DEFAULT_DB_PATH = original_runtime_db_path

    async def test_session_runtime_and_approval_resolution(self):
        with TemporaryDirectory() as tmpdir:
            import webot.subagents as store
            original_db_path = store.DEFAULT_DB_PATH
            original_runtime_db_path = runtime_store.DEFAULT_DB_PATH
            original_policy_root = policy.PROJECT_ROOT
            store.DEFAULT_DB_PATH = Path(tmpdir) / "subagents.db"
            runtime_store.DEFAULT_DB_PATH = Path(tmpdir) / "runtime.db"
            policy.PROJECT_ROOT = Path(tmpdir)
            try:
                service = WeBotService(
                    agent=_FakeAgent({}, active_keys=set(), statuses={}),
                    verify_auth_or_token=lambda user_id, password, token: None,
                    extract_text=lambda content: content if isinstance(content, str) else str(content),
                )

                await service.update_session_plan(
                    WeBotPlanUpdateRequest(
                        user_id="alice",
                        session_id="default",
                        title="Implement runtime",
                        items=[{"step": "Patch agent", "status": "in_progress"}],
                    ),
                    None,
                )
                await service.update_session_todos(
                    WeBotTodoUpdateRequest(
                        user_id="alice",
                        session_id="default",
                        items=[{"step": "Run tests", "status": "pending"}],
                    ),
                    None,
                )
                await service.record_verification(
                    WeBotVerificationCreateRequest(
                        user_id="alice",
                        session_id="default",
                        title="Smoke test",
                        status="passed",
                        details="ok",
                    ),
                    None,
                )
                policy.save_tool_policy_config(
                    "alice",
                    {"tools": {"run_command": {"approval": "manual"}}},
                    project_root=tmpdir,
                )
                approval = runtime_store.create_tool_approval_request(
                    "alice",
                    "default",
                    approval_id="approval-1",
                    tool_name="run_command",
                    args={"command": "ls"},
                    request_reason="approve ls",
                )
                resolved = await service.resolve_tool_approval(
                    WeBotApprovalResolutionRequest(
                        user_id="alice",
                        approval_id=approval.approval_id,
                        action="approve",
                        reason="ok",
                    ),
                    None,
                )
                runtime_view = await service.get_session_runtime("alice", "default", "", None)
                self.assertEqual(resolved["approval"]["status"], "approved")
                self.assertEqual(runtime_view["plan"]["title"], "Implement runtime")
                self.assertEqual(runtime_view["todos"]["items"][0]["step"], "Run tests")
                self.assertEqual(runtime_view["verifications"][0]["title"], "Smoke test")
                self.assertEqual(runtime_view["approvals"][0]["approval_id"], "approval-1")
            finally:
                store.DEFAULT_DB_PATH = original_db_path
                runtime_store.DEFAULT_DB_PATH = original_runtime_db_path
                policy.PROJECT_ROOT = original_policy_root

    async def test_workflow_preset_application_and_run_recovery_are_exposed(self):
        with TemporaryDirectory() as tmpdir:
            import webot.subagents as store
            original_db_path = store.DEFAULT_DB_PATH
            original_runtime_db_path = runtime_store.DEFAULT_DB_PATH
            store.DEFAULT_DB_PATH = Path(tmpdir) / "subagents.db"
            runtime_store.DEFAULT_DB_PATH = Path(tmpdir) / "runtime.db"
            try:
                service = WeBotService(
                    agent=_FakeAgent({}, active_keys=set(), statuses={}),
                    verify_auth_or_token=lambda user_id, password, token: None,
                    extract_text=lambda content: content if isinstance(content, str) else str(content),
                )

                applied = await service.apply_session_workflow_preset(
                    WeBotWorkflowPresetApplyRequest(
                        user_id="alice",
                        session_id="default",
                        preset_id="review_gate",
                    ),
                    None,
                )
                self.assertEqual(applied["preset"]["preset_id"], "review_gate")
                self.assertEqual(applied["mode"]["mode"], "review")
                self.assertEqual(applied["plan"]["metadata"]["workflow"]["preset_id"], "review_gate")

                failed_run = runtime_store.create_run_record(
                    run_id="run-1",
                    user_id="alice",
                    agent_id="worker-1",
                    session_id="default",
                    parent_session="default",
                    agent_type="coder",
                    title="Review gate verifier",
                    input_text="verify runtime",
                    status="failed",
                    timeout_seconds=120,
                    max_turns=4,
                    wait_mode=False,
                    run_kind="subagent",
                    mode="review",
                )
                failed_run = runtime_store.upsert_run(failed_run)
                runtime_store.update_run_status(
                    "run-1",
                    "alice",
                    status="failed",
                    last_error="approval pending for run_command",
                )
                runtime_store.record_run_event(
                    run_id="run-1",
                    user_id="alice",
                    agent_id="worker-1",
                    session_id="default",
                    event_type="tool_approval_pending",
                    status="blocked",
                    message="Need manual approval",
                )

                runtime_view = await service.get_session_runtime("alice", "default", "", None)
                self.assertTrue(runtime_view["workflow_presets"])
                self.assertEqual(runtime_view["active_workflow"]["preset_id"], "review_gate")
                self.assertEqual(runtime_view["runs"][0]["recovery"]["kind"], "approval_blocked")
                self.assertIn("Resolve the pending tool approval", runtime_view["runs"][0]["recovery"]["suggestion"])
                artifacts = runtime_view["artifacts"]
                self.assertTrue(any(item["artifact_kind"] == "workflow_preset" for item in artifacts))
            finally:
                store.DEFAULT_DB_PATH = original_db_path
                runtime_store.DEFAULT_DB_PATH = original_runtime_db_path

    async def test_session_control_plane_features(self):
        with TemporaryDirectory() as tmpdir:
            import webot.subagents as store
            import webot.memory
            original_db_path = store.DEFAULT_DB_PATH
            original_runtime_db_path = runtime_store.DEFAULT_DB_PATH
            original_memory_root = webot_memory.PROJECT_ROOT
            original_memory_user_files = webot_memory.USER_FILES_DIR
            original_runtime_root = runtime_store.PROJECT_ROOT
            store.DEFAULT_DB_PATH = Path(tmpdir) / "subagents.db"
            runtime_store.DEFAULT_DB_PATH = Path(tmpdir) / "runtime.db"
            webot_memory.PROJECT_ROOT = Path(tmpdir)
            webot_memory.USER_FILES_DIR = Path(tmpdir) / "user_files"
            runtime_store.PROJECT_ROOT = Path(tmpdir)
            try:
                service = WeBotService(
                    agent=_FakeAgent({}, active_keys=set(), statuses={}),
                    verify_auth_or_token=lambda user_id, password, token: None,
                    extract_text=lambda content: content if isinstance(content, str) else str(content),
                )

                voice = await service.update_voice_state(
                    WeBotVoiceStateUpdateRequest(
                        user_id="alice",
                        session_id="default",
                        enabled=True,
                        auto_read_aloud=True,
                        last_transcript="ship it",
                    ),
                    None,
                )
                bridge = await service.create_bridge_attach(
                    WeBotBridgeAttachRequest(
                        user_id="alice",
                        session_id="default",
                        role="viewer",
                        label="browser",
                    ),
                    None,
                )
                buddy = await service.buddy_action(
                    WeBotBuddyActionRequest(user_id="alice", action="pet"),
                    None,
                )
                kairos = await service.update_kairos_state(
                    WeBotKairosUpdateRequest(
                        user_id="alice",
                        session_id="default",
                        enabled=True,
                        reason="test",
                    ),
                    None,
                )
                dream = await service.run_dream(
                    WeBotDreamRequest(
                        user_id="alice",
                        session_id="default",
                        reason="test-dream",
                    ),
                    None,
                )
                bridge_id = bridge["bridge"]["bridge_id"]
                detached = await service.detach_bridge(
                    WeBotBridgeDetachRequest(user_id="alice", bridge_id=bridge_id),
                    None,
                )
                runtime_view = await service.get_session_runtime("alice", "default", "", None)

                self.assertTrue(voice["voice"]["enabled"])
                self.assertEqual(voice["voice"]["status"], "enabled")
                self.assertEqual(bridge["bridge"]["websocket_path"], f"/webot/ws/alice/{bridge_id}")
                self.assertEqual(detached["bridge"]["status"], "detached")
                self.assertEqual(buddy["buddy"]["species"], runtime_view["buddy"]["species"])
                self.assertTrue(kairos["memory"]["kairos_enabled"])
                self.assertIn("state", dream["memory"])
                self.assertIn("memory", runtime_view)
                self.assertIn("bridge", runtime_view)
                self.assertIn("voice", runtime_view)
                self.assertIn("buddy", runtime_view)
            finally:
                store.DEFAULT_DB_PATH = original_db_path
                runtime_store.DEFAULT_DB_PATH = original_runtime_db_path
                webot_memory.PROJECT_ROOT = original_memory_root
                webot_memory.USER_FILES_DIR = original_memory_user_files
                runtime_store.PROJECT_ROOT = original_runtime_root

    async def test_session_inbox_send_and_deliver(self):
        with TemporaryDirectory() as tmpdir:
            import webot.subagents as store
            original_db_path = store.DEFAULT_DB_PATH
            original_runtime_db_path = runtime_store.DEFAULT_DB_PATH
            store.DEFAULT_DB_PATH = Path(tmpdir) / "subagents.db"
            runtime_store.DEFAULT_DB_PATH = Path(tmpdir) / "runtime.db"
            try:
                target_record = create_subagent_record(
                    agent_id="worker1",
                    user_id="alice",
                    session_id="subagent__research__worker1",
                    agent_type="research",
                    name="worker-1",
                    description="Investigate runtime",
                    parent_session="default",
                    status="idle",
                )
                upsert_subagent(target_record)

                agent = _FakeAgent({}, active_keys=set(), statuses={})
                service = WeBotService(
                    agent=agent,
                    verify_auth_or_token=lambda user_id, password, token: None,
                    extract_text=lambda content: content if isinstance(content, str) else str(content),
                )

                delivered_payloads = []

                async def _fake_push_system_message(*, user_id, session_id, text, timeout=30):
                    delivered_payloads.append((user_id, session_id, text))

                service._push_system_message = _fake_push_system_message

                sent = await service.send_session_inbox(
                    WeBotSessionInboxSendRequest(
                        user_id="alice",
                        session_id="default",
                        target_ref="worker1",
                        body="Auth module needs rate limiting",
                    ),
                    None,
                )
                delivered_list = await service.get_session_inbox(
                    WeBotSessionInboxListRequest(
                        user_id="alice",
                        session_id="subagent__research__worker1",
                        status="delivered",
                    ),
                    None,
                )

                self.assertEqual(sent["created"], 1)
                self.assertEqual(sent["delivered"], 1)
                self.assertEqual(len(delivered_payloads), 1)
                self.assertEqual(delivered_list["items"][0]["status"], "delivered")

                agent._active_keys.add("alice#subagent__research__worker1")
                queued = await service.send_session_inbox(
                    WeBotSessionInboxSendRequest(
                        user_id="alice",
                        session_id="default",
                        target_ref="worker1",
                        body="Build failed again",
                    ),
                    None,
                )
                queued_list = await service.get_session_inbox(
                    WeBotSessionInboxListRequest(
                        user_id="alice",
                        session_id="subagent__research__worker1",
                        status="queued",
                    ),
                    None,
                )

                self.assertEqual(queued["targets"][0]["delivery_state"], "busy")
                self.assertEqual(queued_list["items"][0]["status"], "queued")
                artifacts = runtime_store.list_runtime_artifacts("alice", "subagent__research__worker1", limit=10)
                self.assertTrue(any(item.kind == "session_inbox_delivery" for item in artifacts))
            finally:
                store.DEFAULT_DB_PATH = original_db_path
                runtime_store.DEFAULT_DB_PATH = original_runtime_db_path

    async def test_lsp_diagnostics_and_stubbed_operations(self):
        with TemporaryDirectory() as tmpdir:
            import webot.workspace as workspace

            original_resolver = workspace.resolve_session_workspace
            original_lsp_resolver = sys.modules["webot.lsp"].resolve_session_workspace
            workspace_root = Path(tmpdir)
            sample = workspace_root / "sample.py"
            sample.write_text("def ok():\n    return 1\n", encoding="utf-8")
            workspace_info = types.SimpleNamespace(cwd=str(workspace_root))
            workspace.resolve_session_workspace = lambda username, session_id="": workspace_info
            sys.modules["webot.lsp"].resolve_session_workspace = (
                lambda username, session_id="": workspace_info
            )
            try:
                service = WeBotService(
                    agent=_FakeAgent({}, active_keys=set(), statuses={}),
                    verify_auth_or_token=lambda user_id, password, token: None,
                    extract_text=lambda content: content if isinstance(content, str) else str(content),
                )

                diagnostics = await service.run_lsp(
                    WeBotLspRequest(
                        user_id="alice",
                        session_id="default",
                        file="sample.py",
                        timeout_seconds=5,
                        max_diagnostics=5,
                    ),
                    None,
                )
                self.assertEqual(diagnostics["status"], "success")
                self.assertEqual(diagnostics["language"], "py")
                self.assertEqual(diagnostics["diagnostics"], [])

                references = await service.run_lsp(
                    WeBotLspRequest(
                        user_id="alice",
                        session_id="default",
                        file="sample.py",
                        op="references",
                    ),
                    None,
                )
                self.assertEqual(references["status"], "stub")
                self.assertEqual(references["op"], "references")

                with self.assertRaises(sys.modules["fastapi"].HTTPException) as ctx:
                    await service.run_lsp(
                        WeBotLspRequest(
                            user_id="alice",
                            session_id="default",
                            file="sample.py",
                            op="rename",
                        ),
                        None,
                    )
                self.assertEqual(ctx.exception.status_code, 400)
            finally:
                workspace.resolve_session_workspace = original_resolver
                sys.modules["webot.lsp"].resolve_session_workspace = original_lsp_resolver


if __name__ == "__main__":
    unittest.main()
