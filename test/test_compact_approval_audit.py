"""Regression coverage for compaction and approval execution boundaries."""

import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "backend"))

from webot import compression, policy, runtime_store
from webot.permission_context import (
    create_or_reuse_permission_request,
    resolve_permission_context,
    resolve_permission_request,
)


class CompactAuditTests(unittest.TestCase):
    def test_transcript_preserves_tool_action_and_result_pair(self):
        messages = [
            AIMessage(content="", tool_calls=[{
                "name": "run_command", "args": {"command": "git status"}, "id": "call-1",
            }]),
            ToolMessage(content="clean", name="run_command", tool_call_id="call-1"),
        ]
        for text in (
            compression._render_segment_for_prompt(messages),
            compression._mechanical_summarizer("", messages, 1000),
        ):
            self.assertIn("git status", text)
            self.assertIn("run_command", text)
            self.assertIn("call-1", text)
            self.assertIn("clean", text)

    def test_summary_cap_handles_small_limits(self):
        for cap in (1, 10, 25, 100):
            with self.subTest(cap=cap):
                self.assertLessEqual(len(compression._truncate_to_cap("x" * 1000, cap)), cap)

    def test_history_budget_still_applies_with_low_api_usage(self):
        messages = [HumanMessage(content="x" * 2000) for _ in range(20)]
        with tempfile.TemporaryDirectory() as tmp:
            result = compression.apply_compression(
                user_id="alice", session_id="s", messages=messages,
                history_token_budget=2000, measured_input_tokens=15000, measured_budget=128000,
                checkpoint_store_path=str(Path(tmp) / "context.db"),
                preserve_recent=2, summarizer=lambda *args: "Task: deploy application",
            )
        self.assertTrue(result.triggered)

    def test_failed_persistence_keeps_existing_view(self):
        messages = [HumanMessage(content="x" * 2000) for _ in range(20)]
        with patch.object(compression, "get_context_compaction", return_value=None), patch.object(
            compression, "save_context_compaction", side_effect=OSError("disk full"),
        ):
            result = compression.apply_compression(
                user_id="alice", session_id="s", messages=messages,
                history_token_budget=2000, preserve_recent=2, summarizer=lambda *args: "summary",
            )
        self.assertFalse(result.triggered)
        self.assertEqual(result.reason, "persistence_failed")
        self.assertEqual(result.view, messages)
        self.assertEqual(result.compacted_until, 0)

    def test_empty_summary_cannot_discard_context(self):
        messages = [HumanMessage(content="IMPORTANT USER DECISION " + "x" * 2000) for _ in range(20)]
        with tempfile.TemporaryDirectory() as tmp:
            result = compression.apply_compression(
                user_id="alice", session_id="s", messages=messages,
                history_token_budget=2000, preserve_recent=2, summarizer=lambda *args: "",
                checkpoint_store_path=str(Path(tmp) / "context.db"),
            )
        self.assertIn("IMPORTANT USER DECISION", result.summary)

    def test_concurrent_compaction_does_not_resummarize_same_segment(self):
        messages = [HumanMessage(content="x" * 2000) for _ in range(20)]
        summarized = []

        def summarize(previous, segment, target):
            summarized.append(len(segment))
            return "Task: deploy application"

        with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(max_workers=2) as executor:
            def compact(_):
                return compression.apply_compression(
                    user_id="alice", session_id="s", messages=messages,
                    history_token_budget=2000, preserve_recent=2, summarizer=summarize, force=True,
                    checkpoint_store_path=str(Path(tmp) / "context.db"),
                )
            results = list(executor.map(compact, range(2)))
        self.assertEqual(len(summarized), 1)
        self.assertEqual(sum(result.triggered for result in results), 1)


class ApprovalAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        for obj, name, value in (
            (policy, "PROJECT_ROOT", root),
            (policy, "USER_FILES_DIR", root / "data" / "user_files"),
            (runtime_store, "DEFAULT_DB_PATH", root / "runtime.db"),
        ):
            patcher = patch.object(obj, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        policy.save_tool_policy_config("alice", {"tools": {"run_command": {"approval": "manual"}}})

    def request(self, **kwargs):
        return create_or_reuse_permission_request(
            user_id="alice", session_id="s", tool_name="run_command",
            args=kwargs or {"command": "echo hi"},
        )

    def test_single_approval_cannot_be_replayed(self):
        request = self.request()
        resolve_permission_request(user_id="alice", approval_id=request.approval_id, action="approved")
        consumed = runtime_store.update_tool_approval_status(request.approval_id, "alice", status="used")
        self.assertEqual(consumed.status, "used")
        self.assertIsNone(runtime_store.update_tool_approval_status(request.approval_id, "alice", status="used"))
        self.assertIsNone(runtime_store.find_active_approval_for_action("alice", "s", "run_command", {"command": "echo hi"}))
        self.assertIsNone(resolve_permission_request(user_id="alice", approval_id=request.approval_id, action="approved"))

    def test_concurrent_calls_only_consume_once(self):
        request = self.request()
        resolve_permission_request(user_id="alice", approval_id=request.approval_id, action="approved")
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda _: runtime_store.update_tool_approval_status(request.approval_id, "alice", status="used"),
                range(2),
            ))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_expired_or_wrong_user_requests_cannot_be_approved(self):
        request = self.request()
        self.assertIsNone(resolve_permission_request(user_id="bob", approval_id=request.approval_id, action="approved"))
        runtime_store.update_tool_approval_status(request.approval_id, "alice", status="expired")
        self.assertIsNone(resolve_permission_request(user_id="alice", approval_id=request.approval_id, action="approved"))

    def test_remember_approves_only_complete_same_action(self):
        args = {"command": "echo hi", "language": "shell", "mode": "foreground", "cwd": "project"}
        request = self.request(**args)
        resolve_permission_request(user_id="alice", approval_id=request.approval_id, action="approved", remember=True)
        runtime_store.update_tool_approval_status(request.approval_id, "alice", status="used")
        decision = resolve_permission_context(user_id="alice", session_id="s", tool_name="run_command", args=args)
        self.assertTrue(decision.allowed)
        for changed in (
            {**args, "language": "python"}, {**args, "mode": "interactive"},
            {**args, "cwd": "other-project"}, {**args, "command": "echo bye"},
        ):
            with self.subTest(args=changed):
                self.assertTrue(resolve_permission_context(
                    user_id="alice", session_id="s", tool_name="run_command", args=changed,
                ).requires_approval)

    def test_corrupt_policy_does_not_allow_tools(self):
        path = policy.get_tool_policy_path("alice")
        for content in ("{broken", "[]", "null"):
            with self.subTest(content=content):
                path.write_text(content)
                self.assertFalse(policy.evaluate_tool_policy(policy.get_tool_policy("alice"), "run_command", {}).allowed)

    def test_remember_keeps_wildcard_restrictions(self):
        policy.save_tool_policy_config("alice", {
            "tools": {"*": {"approval": "manual", "content_block_patterns": ["rm -rf"]}},
        })
        request = self.request()
        resolve_permission_request(user_id="alice", approval_id=request.approval_id, action="approved", remember=True)
        configured = policy.get_tool_policy("alice")
        self.assertTrue(policy.evaluate_tool_policy(configured, "run_command", {"command": "echo hi"}).allowed)
        blocked = policy.evaluate_tool_policy(configured, "run_command", {"command": "rm -rf project"})
        self.assertFalse(blocked.allowed)
        self.assertFalse(blocked.requires_approval)

    def test_interactive_input_respects_content_block_patterns(self):
        configured = policy.WeBotToolPolicy(tools={
            "background_command_io": policy.ToolPolicyRule(content_block_patterns=("rm -rf",)),
        })
        self.assertFalse(policy.evaluate_tool_policy(configured, "background_command_io", {"input": "rm -rf project\n"}).allowed)
        self.assertTrue(policy.evaluate_tool_policy(configured, "background_command_io", {"input": ""}).allowed)


class ApprovalExecutionAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_compact_keeps_event_loop_responsive(self):
        import asyncio
        from threading import Event
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        from webot.api.session_service import SessionService

        messages = [HumanMessage(content="history")]
        agent = SimpleNamespace(
            agent_app=SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values={"messages": messages}))),
            get_thread_model=lambda thread: "",
            get_thread_last_context_tokens=lambda thread: 0,
            set_thread_context_usage=Mock(),
        )
        service = SessionService(db_path=":memory:", agent=agent, extract_text=str)
        release = Event()
        observed = []

        def compact(**kwargs):
            observed.append(release.wait(timeout=1))
            return compression.CompressionResult(
                view=messages, triggered=False, summary="", compacted_until=0, reason="no_benefit", view_tokens=1,
            )

        with patch("webot.api.session_service.apply_compression", side_effect=compact), patch(
            "webot.api.session_service.static_compression_view", return_value=messages,
        ):
            task = asyncio.create_task(service.compact("alice", "s"))
            await asyncio.sleep(0.02)
            release.set()
            await task
        self.assertEqual(observed, [True])

    async def test_hook_cannot_bypass_block_or_change_identity(self):
        from webot.engine.agent import UserAwareToolNode
        from webot.policy import ToolHookOutcome, ToolPolicyDecision

        node = UserAwareToolNode([], lambda: [])

        class Capture:
            captured = None

            async def ainvoke(self, state, config):
                self.captured = state
                return {"messages": [ToolMessage(content="ok", tool_call_id="call-1")]}

        capture = Capture()
        node.tool_node = capture
        configured = policy.WeBotToolPolicy(tools={
            "run_command": policy.ToolPolicyRule(content_block_patterns=("rm -rf",)),
        })
        state = {
            "user_id": "alice", "session_id": "s", "session_mode": "agent",
            "messages": [AIMessage(content="", tool_calls=[{
                "name": "run_command", "args": {"command": "echo hi"}, "id": "call-1",
            }])],
        }
        with patch("webot.permission_context.get_tool_policy", return_value=configured), patch(
            "webot.engine.agent.run_tool_policy_hooks",
            return_value=ToolHookOutcome(args={"command": "rm -rf project", "username": "bob"}, decision=ToolPolicyDecision(allowed=True)),
        ):
            result = await node(state, {})
        self.assertIsNone(capture.captured)
        self.assertIn("阻止", result["messages"][0].content)

        with patch("webot.permission_context.get_tool_policy", return_value=configured), patch(
            "webot.engine.agent.run_tool_policy_hooks",
            return_value=ToolHookOutcome(args={"command": "echo hi", "username": "bob"}, decision=ToolPolicyDecision(allowed=True)),
        ):
            await node(state, {})
        args = capture.captured["messages"][-1].tool_calls[0]["args"]
        self.assertEqual(args["username"], "alice")

    async def test_review_blocks_interactive_input(self):
        from webot.engine.agent import UserAwareToolNode

        node = UserAwareToolNode([], lambda: [])
        state = {
            "user_id": "alice", "session_id": "s", "session_mode": "review",
            "messages": [AIMessage(content="", tool_calls=[{
                "name": "background_command_io", "args": {"job_id": "job-1", "input": "touch file\n"}, "id": "call-1",
            }])],
        }
        result = await node(state, {})
        self.assertIn("review", result["messages"][0].content)

    async def test_wait_rejects_expired_and_consumed_approvals(self):
        from webot.engine.agent import _wait_for_tool_approval
        from types import SimpleNamespace

        for status, expiry in (("approved", "2000-01-01"), ("used", "2999-01-01")):
            with self.subTest(status=status), patch(
                "webot.engine.agent.get_tool_approval",
                return_value=SimpleNamespace(status=status, expires_at=expiry, resolution_reason=""),
            ):
                allowed, reason = await _wait_for_tool_approval("approval-1", "alice")
                self.assertFalse(allowed)
                self.assertTrue(reason)

    async def test_command_safety_approval_delegates_exact_action(self):
        from webot.mcp import commander
        from webot.approval_review import ApprovalResult
        action = {"job_id": "job-1", "input": "operation", "enter": True, "cwd": "project"}
        with patch.object(commander, "authorize_action", return_value=ApprovalResult(True)) as broker:
            approved, _ = await commander._wait_for_command_approval(
                "alice", "s", action["input"], "high risk",
                tool_name="background_command_io", action_args=action,
            )
        self.assertTrue(approved)
        self.assertEqual(broker.call_args.kwargs["tool_name"], "background_command_io")
        self.assertEqual(broker.call_args.kwargs["args"], action | {"username": "alice", "session_id": "s"})

    async def test_command_safety_propagates_broker_rejection(self):
        from webot.mcp import commander
        from webot.approval_review import ApprovalResult
        with patch.object(commander, "authorize_action", return_value=ApprovalResult(False, "审批已失效或已被使用。")):
            approved, reason = await commander._wait_for_command_approval("alice", "s", "command", "reason")
        self.assertFalse(approved)
        self.assertIn("失效", reason)


if __name__ == "__main__":
    unittest.main()
