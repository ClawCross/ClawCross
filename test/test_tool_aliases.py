"""Retired tool names keep working, and never grant more than they used to.

Tools were merged (e.g. append_file into write_file, the list_* variants into
their check_* / view tools). Their old names survive in saved presets, stored
enabled_tools, custom subagent profiles, and older prompts.
"""

import asyncio
import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src" / "backend"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from webot.engine.agent import TeamAgent, UserAwareToolNode
from webot.engine.tool_aliases import TOOL_ALIASES, canonical_tool_names, resolve_tool_call

MCP_SERVERS = (
    "commander", "filemanager", "llmapi", "oasis", "search",
    "skills", "webot", "scheduler", "session", "notifier",
)


def _current_tool_names() -> set[str]:
    names = set()
    for server in MCP_SERVERS:
        module = importlib.import_module(f"webot.mcp.{server}")
        names.update(tool.name for tool in asyncio.run(module.mcp.list_tools()))
    return names


class AliasTable(unittest.TestCase):
    def test_every_alias_points_at_a_live_tool_and_no_live_tool_is_aliased(self):
        live = _current_tool_names()
        self.assertEqual({a.target for a in TOOL_ALIASES.values()} - live, set())
        self.assertEqual(set(TOOL_ALIASES) & live, set())

    def test_forced_arguments_reproduce_the_old_tool(self):
        self.assertEqual(
            resolve_tool_call("append_file", {"filename": "a.txt", "content": "x", "mode": "overwrite"}),
            ("write_file", {"filename": "a.txt", "content": "x", "mode": "append"}),
        )
        self.assertEqual(resolve_tool_call("get_yaml_workflow_rules", {}), ("get_workflow_rules", {"kind": "yaml"}))
        self.assertEqual(resolve_tool_call("enter_plan_mode", {"mode": "yolo"})[1]["mode"], "plan")

    def test_defaults_fill_only_missing_arguments(self):
        self.assertEqual(resolve_tool_call("web_research_brief", {"query": "q"})[1]["fetch_top"], 2)
        self.assertEqual(resolve_tool_call("web_research_brief", {"query": "q", "fetch_top": 4})[1]["fetch_top"], 4)

    def test_current_names_pass_through(self):
        self.assertEqual(resolve_tool_call("read_file", {"filename": "a"}), ("read_file", {"filename": "a"}))

    def test_granted_lists_map_to_current_names(self):
        self.assertEqual(
            canonical_tool_names(["append_file", "write_file", "skill_list", "read_file"]),
            ["write_file", "read_file"],
        )

    def test_a_grant_of_a_narrow_old_tool_does_not_grant_its_wider_replacement(self):
        # enter_plan_mode could only enter plan/review; set_session_mode can switch to yolo.
        self.assertNotIn("set_session_mode", canonical_tool_names(["enter_plan_mode", "exit_plan_mode"]))
        self.assertEqual(canonical_tool_names(["skill_manage", "skill_view", "skill_list"]), [])

    def test_retired_skill_calls_use_memory_without_path_reinterpretation(self):
        self.assertEqual(resolve_tool_call("skill_view", {"name": "memo"}),
                         ("read_file", {"filename": "memo", "storage": "memory"}))
        self.assertEqual(resolve_tool_call("skill_view", {}), ("list_files", {"storage": "memory"}))
        name, args = resolve_tool_call("skill_manage", {"action": "patch", "name": "memo", "old_string": "before", "new_string": "after"})
        self.assertEqual(name, "write_file")
        self.assertEqual(args["mode"], "str_replace")
        self.assertEqual(args["storage"], "memory")
        self.assertEqual(resolve_tool_call("skill_manage", {"action": "delete", "name": "memo"})[0], "delete_file")
        _, args = resolve_tool_call("skill_manage", {"action": "patch", "name": "memo", "file_path": "../outside"})
        self.assertEqual(args["mode"], "unsupported_legacy_skill_action")

    def test_subagent_profiles_normalize_old_names(self):
        from webot.profiles import _normalize_allowed_tools

        self.assertEqual(
            _normalize_allowed_tools(["append_file", "read_file", "enter_plan_mode"]),
            ("write_file", "read_file"),
        )


def _allow_all_permission(*_args, **_kwargs):
    return type("Permission", (), {
        "allowed": True, "requires_approval": False, "reason": "",
        "matched_rule": None, "policy": {}, "approval": None,
    })()


def _passthrough_hook_outcome(*_call_args, args=None, **_kwargs):
    return type("HookOutcome", (), {"args": dict(args or {}), "decision": None})()


class OldNameCalls(unittest.IsolatedAsyncioTestCase):
    def _write_file_tool(self, received):
        def record(**kwargs):
            received.update(kwargs)
            return "ok"

        return StructuredTool(
            name="write_file",
            description="write",
            args_schema={
                "type": "object",
                "properties": {"filename": {"type": "string"}, "content": {"type": "string"}, "mode": {"type": "string", "default": "overwrite"}},
                "required": ["filename", "content"],
            },
            func=record,
        )

    async def _run(self, node, calls, **state_extra):
        state = {"session_mode": "bypass", "user_id": "alice", "session_id": "s1", "messages": [AIMessage(content="", tool_calls=calls)], **state_extra}
        with patch("webot.engine.agent.get_session_mode", return_value={"mode": "default"}), \
                patch("webot.engine.agent.resolve_permission_context", side_effect=_allow_all_permission), \
                patch("webot.engine.agent.run_tool_policy_hooks", side_effect=_passthrough_hook_outcome):
            return await node(state, config={})

    async def test_an_old_name_runs_the_replacement(self):
        received = {}
        tool = self._write_file_tool(received)
        node = UserAwareToolNode([tool], lambda: [tool])
        await self._run(node, [{"name": "append_file", "args": {"filename": "a.txt", "content": "x"}, "id": "c1", "type": "tool_call"}])
        self.assertEqual(received["mode"], "append")
        self.assertEqual(received["filename"], "a.txt")

    async def test_stored_enabled_tools_with_old_names_still_allow_the_call(self):
        received = {}
        tool = self._write_file_tool(received)
        node = UserAwareToolNode([tool], lambda: [tool])
        await self._run(
            node,
            [{"name": "write_file", "args": {"filename": "a.txt", "content": "x"}, "id": "c1", "type": "tool_call"}],
            enabled_tools=["append_file"],
        )
        self.assertEqual(received["filename"], "a.txt")

    def test_old_name_routes_to_internal_tools_not_the_caller(self):
        agent = TeamAgent.__new__(TeamAgent)
        agent._internal_tool_names = frozenset({"write_file"})
        state = {"messages": [AIMessage(content="", tool_calls=[{"name": "append_file", "args": {}, "id": "c1"}])]}
        self.assertTrue(agent._should_continue(state))
        # ...unless the caller itself supplied a tool under that name.
        state["external_tools"] = [{"type": "function", "function": {"name": "append_file"}}]
        self.assertFalse(agent._should_continue(state))


if __name__ == "__main__":
    unittest.main()
