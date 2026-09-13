import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool

from core.agent import USER_INJECTED_TOOLS, DirectToolNode, UserAwareToolNode


_WEBOT_RUNTIME_USER_TOOLS = {
    "write_session_plan",
    "read_session_plan",
    "clear_session_plan",
    "write_session_todos",
    "read_session_todos",
    "clear_session_todos",
    "record_verification",
    "list_verifications",
    "run_verification",
    "list_tool_approvals",
}


class _FakeToolNode:
    def __init__(self):
        self.captured_state = None

    async def ainvoke(self, state, config):
        self.captured_state = state
        call_id = state["messages"][-1].tool_calls[0]["id"]
        return {"messages": [ToolMessage(content="ok", tool_call_id=call_id)]}


def _passthrough_hook_outcome(*_call_args, args=None, **_kwargs):
    return type("HookOutcome", (), {"args": dict(args or {}), "decision": None})()


class UserAwareToolNodeTests(unittest.IsolatedAsyncioTestCase):
    def test_webot_runtime_tools_are_in_user_injection_allowlist(self):
        missing = _WEBOT_RUNTIME_USER_TOOLS.difference(USER_INJECTED_TOOLS)
        self.assertEqual(missing, set())

    async def test_team_tools_auto_inject_username_and_team(self):
        node = UserAwareToolNode(
            [],
            lambda: [],
            find_internal_session_meta_fn=lambda user_id, session_id: {"team": "alpha"},
        )
        fake_tool_node = _FakeToolNode()
        node.tool_node = fake_tool_node

        state = {
            "user_id": "alice",
            "session_id": "sess-1",
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "skill_list", "args": {}, "id": "call_1", "type": "tool_call"}],
                )
            ],
        }

        with patch("core.agent.get_session_mode", return_value={"mode": "default"}), patch(
            "core.agent.resolve_permission_context",
            return_value=type(
                "Permission",
                (),
                {
                    "allowed": True,
                    "requires_approval": False,
                    "reason": "",
                    "matched_rule": None,
                    "policy": {},
                    "approval": None,
                },
            )(),
        ), patch("core.agent.run_tool_policy_hooks", side_effect=_passthrough_hook_outcome):
            result = await node(state, config={})

        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0].content, "ok")
        injected_call = fake_tool_node.captured_state["messages"][-1].tool_calls[0]
        self.assertEqual(injected_call["args"]["username"], "alice")
        self.assertEqual(injected_call["args"]["team"], "alpha")

    async def test_session_runtime_tools_auto_inject_username(self):
        node = UserAwareToolNode([], lambda: [])
        fake_tool_node = _FakeToolNode()
        node.tool_node = fake_tool_node

        state = {
            "user_id": "alice",
            "session_id": "sess-1",
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_session_todos",
                            "args": {"source_session": "exp_entrepreneur_mo0yixp1"},
                            "id": "call_2",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
        }

        with patch("core.agent.get_session_mode", return_value={"mode": "default"}), patch(
            "core.agent.resolve_permission_context",
            return_value=type(
                "Permission",
                (),
                {
                    "allowed": True,
                    "requires_approval": False,
                    "reason": "",
                    "matched_rule": None,
                    "policy": {},
                    "approval": None,
                },
            )(),
        ), patch("core.agent.run_tool_policy_hooks", side_effect=_passthrough_hook_outcome):
            result = await node(state, config={})

        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0].content, "ok")
        injected_call = fake_tool_node.captured_state["messages"][-1].tool_calls[0]
        self.assertEqual(injected_call["args"]["username"], "alice")
        self.assertEqual(injected_call["args"]["source_session"], "exp_entrepreneur_mo0yixp1")


async def _add(a: int, b: int) -> str:
    return str(a + b)


async def _explode(x: str) -> str:
    raise RuntimeError("boom")


async def _with_artifact(q: str):
    return f"content:{q}", {"raw": q}


def _call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


class DirectToolNodeErrorHandlingTests(unittest.IsolatedAsyncioTestCase):
    """DirectToolNode must match langgraph ToolNode's default error handling."""

    def setUp(self):
        self.node = DirectToolNode([
            StructuredTool.from_function(coroutine=_add, name="add", description="add"),
            StructuredTool.from_function(coroutine=_explode, name="explode", description="explode"),
            StructuredTool.from_function(
                coroutine=_with_artifact,
                name="with_artifact",
                description="artifact",
                response_format="content_and_artifact",
            ),
        ])

    async def _run(self, *calls):
        state = {"messages": [AIMessage(content="", tool_calls=list(calls))]}
        return (await self.node.ainvoke(state, config={}))["messages"]

    async def test_unknown_tool_is_per_call_error(self):
        messages = await self._run(_call("nope", {}, "c1"), _call("add", {"a": 1, "b": 2}, "c2"))
        self.assertEqual(messages[0].status, "error")
        self.assertEqual(
            messages[0].content,
            "Error: nope is not a valid tool, try one of [add, explode, with_artifact].",
        )
        self.assertEqual((messages[1].status, messages[1].content), ("success", "3"))

    async def test_invalid_args_do_not_fail_sibling_calls(self):
        messages = await self._run(_call("add", {"a": "x", "b": 2}, "c1"), _call("add", {"a": 1, "b": 2}, "c2"))
        self.assertEqual(messages[0].status, "error")
        self.assertTrue(messages[0].content.startswith("Error invoking tool 'add' with kwargs {'a': 'x', 'b': 2} with error:\n a: "))
        self.assertTrue(messages[0].content.endswith("\n Please fix the error and try again."))
        self.assertEqual(messages[0].tool_call_id, "c1")
        self.assertEqual((messages[1].status, messages[1].content), ("success", "3"))

    async def test_runtime_error_propagates_like_old_tool_node(self):
        with self.assertRaises(RuntimeError):
            await self._run(_call("explode", {"x": "y"}, "c1"))

    async def test_artifact_is_preserved(self):
        (message,) = await self._run(_call("with_artifact", {"q": "hi"}, "c1"))
        self.assertEqual(message.content, "content:hi")
        self.assertEqual(message.artifact, {"raw": "hi"})
        self.assertEqual((message.name, message.tool_call_id), ("with_artifact", "c1"))


if __name__ == "__main__":
    unittest.main()
