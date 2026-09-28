"""Tool schemas the model is bound to: documented, strict-decodable, and round-tripped.

Tool calls are decoded under each tool's JSON schema (strict tool calling), so
the schema is the whole contract with the model: it is where every argument is
explained, and it must be expressible in the closed form a strict decoder
accepts. These tests hold every MCP tool to that, and check the encoding of
optional arguments (nullable on the way out, dropped on the way back) and the
per-provider binding.
"""

import asyncio
import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool

from core.agent import (
    SESSION_INJECTED_TOOLS,
    USER_INJECTED_TOOLS,
    UserAwareToolNode,
    bind_tool_schema,
    external_tool_schema,
)
from core.tool_schema import (
    StrictSchemaError,
    decode_structured_final,
    drop_null_optionals,
    forced_tool_choice_supported,
    reply_format_binding,
    strict_tool_binding,
    strict_violations,
    to_strict_parameters,
)
from utils.mcp_tool_docs import parse_tool_docstring

MCP_SERVERS = (
    "commander", "filemanager", "llmapi", "oasis", "search",
    "skills", "webot", "scheduler", "session", "notifier",
)
SESSION_ARGUMENTS = {"source_session", "session_id", "parent_session", "notify_session", "current_session_id"}

# JSON size of the strict-bound tool array. The array renders ahead of the
# system prompt on every request; growing it should be a decision, not drift.
STRICT_TOOL_ARRAY_BUDGET_CHARS = 40_000

_inventory_cache = None


def _mcp_inventory():
    """{tool name: (server, MCP Tool)} and the servers' stale-doc reports, loaded once."""
    global _inventory_cache
    if _inventory_cache is None:
        tools, stale = {}, {}
        for name in MCP_SERVERS:
            module = importlib.import_module(f"mcp_servers.{name}")
            for tool in asyncio.run(module.mcp.list_tools()):
                tools[tool.name] = (name, tool)
            stale.update(getattr(module.mcp, "stale_param_docs", {}))
        _inventory_cache = (tools, stale)
    return _inventory_cache


def _as_structured_tool(mcp_tool) -> StructuredTool:
    """What langchain-mcp-adapters hands the agent: the MCP schema as a dict."""
    return StructuredTool(
        name=mcp_tool.name,
        description=mcp_tool.description or "",
        args_schema=mcp_tool.inputSchema,
        func=lambda **kwargs: "",
    )


def _hidden_arguments(tool_name: str) -> set[str]:
    hidden = {SESSION_INJECTED_TOOLS.get(tool_name)} - {None}
    if tool_name in USER_INJECTED_TOOLS:
        hidden.add("username")
    return hidden


class DocstringParsing(unittest.TestCase):
    def test_rest_params_leave_the_description(self):
        doc = parse_tool_docstring(
            """
            Read a file.

            :param username: injected
            :param path: File path,
                relative to the workspace
            :return: File text
            """
        )
        self.assertEqual(doc.description, "Read a file.")
        self.assertEqual(doc.params, {"username": "injected", "path": "File path, relative to the workspace"})

    def test_google_args_and_returns_are_dropped(self):
        doc = parse_tool_docstring(
            """
            List images.

            Args:
                folder: Folder path.
                    Relative to the session.
                max_files: Limit.

            Returns:
                Image paths, one per line.
            """
        )
        self.assertEqual(doc.description, "List images.")
        self.assertEqual(doc.params, {"folder": "Folder path. Relative to the session.", "max_files": "Limit."})

    def test_a_deeper_line_that_looks_like_an_entry_continues_the_argument(self):
        doc = parse_tool_docstring(
            """
            Run a command.

            Args:
                mode: How to run it.
                    note: background keeps running after the reply
                cwd: Directory.
            """
        )
        self.assertEqual(doc.params, {"mode": "How to run it. note: background keeps running after the reply",
                                      "cwd": "Directory."})

    def test_prose_only_docstring_is_kept_whole(self):
        doc = parse_tool_docstring("Do one thing.\n\n- step a\n- step b")
        self.assertEqual(doc.description, "Do one thing.\n\n- step a\n- step b")
        self.assertEqual(doc.params, {})


class StrictParameters(unittest.TestCase):
    SCHEMA = {
        "type": "object",
        "title": "Args",
        "properties": {
            "query": {"type": "string", "title": "Query", "description": "What to find"},
            "limit": {"type": "integer", "default": 5, "description": "How many"},
            "tag": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
        },
        "required": ["query"],
    }

    def test_optional_arguments_become_required_and_nullable(self):
        strict = to_strict_parameters(self.SCHEMA)
        self.assertEqual(strict["required"], ["query", "limit", "tag"])
        self.assertIs(strict["additionalProperties"], False)
        self.assertEqual(strict["properties"]["query"], {"type": "string", "description": "What to find"})
        self.assertEqual(strict["properties"]["limit"]["anyOf"], [{"type": "integer"}, {"type": "null"}])
        # Already nullable: left as is, not double-wrapped.
        self.assertEqual(strict["properties"]["tag"], {"anyOf": [{"type": "string"}, {"type": "null"}]})
        self.assertEqual(strict_violations(strict), [])

    def test_default_moves_into_the_description(self):
        strict = to_strict_parameters(self.SCHEMA)
        self.assertEqual(strict["properties"]["limit"]["description"], "How many (default: 5)")
        chinese = to_strict_parameters({
            "type": "object",
            "properties": {"n": {"type": "integer", "default": 20, "description": "条数"}},
        })
        self.assertEqual(chinese["properties"]["n"]["description"], "条数（默认 20）")

    def test_the_input_is_not_modified(self):
        before = json.dumps(self.SCHEMA, sort_keys=True)
        to_strict_parameters(self.SCHEMA)
        self.assertEqual(json.dumps(self.SCHEMA, sort_keys=True), before)

    def test_refs_are_inlined_and_nested_objects_closed(self):
        strict = to_strict_parameters({
            "type": "object",
            "$defs": {"Step": {"type": "object", "properties": {"step": {"type": "string"}, "notes": {"type": "string", "default": ""}}, "required": ["step"]}},
            "properties": {"items": {"type": "array", "items": {"$ref": "#/$defs/Step"}}},
            "required": ["items"],
        })
        step = strict["properties"]["items"]["items"]
        self.assertEqual(step["required"], ["step", "notes"])
        self.assertIs(step["additionalProperties"], False)
        self.assertNotIn("$defs", strict)
        self.assertEqual(strict_violations(strict), [])

    def test_free_form_objects_are_rejected(self):
        with self.assertRaises(StrictSchemaError):
            to_strict_parameters({"type": "object", "properties": {"meta": {"type": "object", "additionalProperties": True}}})
        with self.assertRaises(StrictSchemaError):
            to_strict_parameters({"type": "object", "properties": {"tags": {"type": "array"}}})

    def test_violations_are_reported(self):
        problems = strict_violations({"type": "object", "properties": {"a": {"type": "string"}}, "required": []})
        self.assertIn("$: additionalProperties must be false", problems)
        self.assertIn("$: every property must be required", problems)


class NullOptionalsRoundTrip(unittest.TestCase):
    SCHEMA = {
        "type": "object",
        "$defs": {"Step": {"type": "object", "properties": {"step": {"type": "string"}, "notes": {"type": "string", "default": ""}}, "required": ["step"]}},
        "properties": {
            "title": {"type": "string"},
            "limit": {"type": "integer", "default": 5},
            "maybe": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "items": {"anyOf": [{"type": "array", "items": {"$ref": "#/$defs/Step"}}, {"type": "null"}], "default": None},
        },
        "required": ["title", "maybe"],
    }

    def test_nulls_for_optional_arguments_are_dropped(self):
        args = {"title": "t", "limit": None, "maybe": None, "items": None}
        # "maybe" is required and nullable: its null is a real value and stays.
        self.assertEqual(drop_null_optionals(args, self.SCHEMA), {"title": "t", "maybe": None})

    def test_nested_objects_are_walked(self):
        args = {"title": "t", "maybe": "x", "items": [{"step": "a", "notes": None}, {"step": "b", "notes": "n"}]}
        self.assertEqual(
            drop_null_optionals(args, self.SCHEMA)["items"],
            [{"step": "a"}, {"step": "b", "notes": "n"}],
        )

    def test_values_and_unknown_keys_pass_through(self):
        args = {"title": "t", "maybe": "x", "limit": 0, "extra": None}
        self.assertEqual(drop_null_optionals(args, self.SCHEMA), args)


class McpToolInventory(unittest.TestCase):
    def test_every_tool_has_a_display_category_and_usage_old_name_is_compatible(self):
        from core.tool_catalog import tool_category
        from core.tool_aliases import resolve_tool_call
        inventory, _ = _mcp_inventory()
        self.assertIn("usage_status", inventory)
        self.assertNotIn("get_insights", inventory)
        self.assertNotIn("skill_view", inventory)
        self.assertNotIn("skill_manage", inventory)
        self.assertNotIn("skill_evolution_apply", inventory)
        self.assertEqual(tool_category("skill_evolution_report"), "usage")
        self.assertEqual(resolve_tool_call("get_insights", {"days": 7}), ("usage_status", {"days": 7}))
        for name in inventory:
            with self.subTest(name=name):
                self.assertNotEqual(tool_category(name), "other")

    """Every MCP tool, as the servers actually register it."""

    @classmethod
    def setUpClass(cls):
        cls.tools, cls.stale = _mcp_inventory()

    def test_every_tool_has_a_description_without_argument_notes(self):
        for name, (_server, tool) in self.tools.items():
            with self.subTest(tool=name):
                self.assertTrue((tool.description or "").strip())
                self.assertNotIn(":param ", tool.description)
                self.assertNotRegex(tool.description, r"(?m)^\s*Args:\s*$")
                # Injected arguments are hidden from the schema; the prose must
                # not bring them back.
                self.assertNotRegex(tool.description, r"自动注入|(?i:auto-inject)")

    def test_documented_arguments_exist(self):
        self.assertEqual(self.stale, {})

    def test_every_argument_the_model_sees_is_documented(self):
        missing = {
            name: sorted(
                arg for arg, prop in (tool.inputSchema.get("properties") or {}).items()
                if arg not in _hidden_arguments(name) and not prop.get("description")
            )
            for name, (_server, tool) in self.tools.items()
        }
        self.assertEqual({k: v for k, v in missing.items() if v}, {})

    def test_identity_arguments_are_injected_not_model_supplied(self):
        # A username the model fills in lets one call act as another user.
        for name, (_server, tool) in self.tools.items():
            properties = set(tool.inputSchema.get("properties") or {})
            with self.subTest(tool=name):
                if "username" in properties:
                    self.assertIn(name, USER_INJECTED_TOOLS)
                session_args = properties & SESSION_ARGUMENTS
                if session_args:
                    self.assertIn(SESSION_INJECTED_TOOLS.get(name), session_args)

    def test_every_tool_binds_strict(self):
        for name, (_server, tool) in self.tools.items():
            with self.subTest(tool=name):
                bound = bind_tool_schema(_as_structured_tool(tool), strict=True)
                self.assertIsInstance(bound, dict)
                self.assertIs(bound["function"].get("strict"), True)
                self.assertEqual(strict_violations(bound["function"]["parameters"]), [])
                self.assertFalse(_hidden_arguments(name) & set(bound["function"]["parameters"]["properties"]))

    def test_strict_tool_array_stays_within_budget(self):
        bound = [bind_tool_schema(_as_structured_tool(tool), strict=True) for _s, tool in self.tools.values()]
        size = len(json.dumps(bound, ensure_ascii=False, separators=(",", ":")))
        self.assertLessEqual(
            size, STRICT_TOOL_ARRAY_BUDGET_CHARS,
            f"strict tool array is {size} chars; raise the budget only on purpose",
        )

    def test_binding_is_deterministic(self):
        # The array is part of the cached prompt prefix: same input, same bytes.
        tool = _as_structured_tool(self.tools["write_session_plan"][1])
        first = json.dumps(bind_tool_schema(tool, strict=True), ensure_ascii=False)
        second = json.dumps(bind_tool_schema(tool, strict=True), ensure_ascii=False)
        self.assertEqual(first, second)


class ExternalTools(unittest.TestCase):
    def test_compliant_external_schema_is_marked_strict_unchanged(self):
        params = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"], "additionalProperties": False}
        bound = external_tool_schema({"name": "ext", "parameters": params}, strict=True)
        self.assertIs(bound["function"]["strict"], True)
        self.assertIs(bound["function"]["parameters"], params)

    def test_non_compliant_external_schema_is_left_alone(self):
        params = {"type": "object", "properties": {"q": {"type": "string"}}}
        bound = external_tool_schema({"name": "ext", "parameters": params}, strict=True)
        self.assertNotIn("strict", bound["function"])
        self.assertIs(bound["function"]["parameters"], params)


def _strict_function():
    params = to_strict_parameters({"type": "object", "properties": {"q": {"type": "string"}, "n": {"type": "integer", "default": 5}}, "required": ["q"]})
    return {"type": "function", "function": {"name": "search", "description": "d", "parameters": params, "strict": True}}


class ProviderBinding(unittest.TestCase):
    """Strict reaches each provider's request payload (built offline, nothing is sent)."""

    def setUp(self):
        patcher = patch.dict(os.environ, {"LLM_TOOL_STRICT": "auto"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _payload(self, model):
        model, per_tool, kwargs = strict_tool_binding(model)
        bound = model.bind_tools([_strict_function()], **kwargs)
        return model, per_tool, model._get_request_payload([HumanMessage("hi")], **bound.kwargs)

    def test_openai_chat_completions(self):
        from langchain_openai import ChatOpenAI

        _m, per_tool, payload = self._payload(ChatOpenAI(model="gpt-5", api_key="test"))
        self.assertTrue(per_tool)
        self.assertIs(payload["tools"][0]["function"]["strict"], True)

    def test_openai_responses_api(self):
        from langchain_openai import ChatOpenAI

        _m, _p, payload = self._payload(ChatOpenAI(model="gpt-5", api_key="test", use_responses_api=True))
        self.assertIs(payload["tools"][0]["strict"], True)

    def test_anthropic(self):
        from langchain_anthropic import ChatAnthropic

        _m, _p, payload = self._payload(ChatAnthropic(model="claude-sonnet-5", api_key="test"))
        self.assertIs(payload["tools"][0]["strict"], True)
        self.assertEqual(payload["tools"][0]["input_schema"]["required"], ["q", "n"])

    def test_deepseek_moves_to_the_beta_endpoint(self):
        from langchain_deepseek import ChatDeepSeek

        model, _p, payload = self._payload(ChatDeepSeek(model="deepseek-chat", api_key="test", api_base="https://api.deepseek.com"))
        self.assertEqual(model.api_base, "https://api.deepseek.com/beta")
        self.assertIn("/beta", str(model.root_client.base_url))
        self.assertIs(payload["tools"][0]["function"]["strict"], True)

    def test_deepseek_on_another_host_is_left_there(self):
        from langchain_deepseek import ChatDeepSeek

        model, per_tool, _kw = strict_tool_binding(
            ChatDeepSeek(model="deepseek-chat", api_key="test", api_base="https://proxy.example.com/v1")
        )
        self.assertEqual(model.api_base, "https://proxy.example.com/v1")
        self.assertTrue(per_tool)

    def test_gemini_uses_validated_function_calling(self):
        from langchain_google_genai import ChatGoogleGenerativeAI

        model = ChatGoogleGenerativeAI(model="gemini-2.5-pro", google_api_key="test")
        model, per_tool, kwargs = strict_tool_binding(model)
        self.assertFalse(per_tool)
        plain = {"type": "function", "function": {"name": "search", "description": "d", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}
        request = model._prepare_request([HumanMessage("hi")], **model.bind_tools([plain], **kwargs).kwargs)
        self.assertEqual(request["config"].tool_config.function_calling_config.mode.value, "VALIDATED")

    def test_off_switch(self):
        from langchain_openai import ChatOpenAI

        with patch.dict(os.environ, {"LLM_TOOL_STRICT": "off"}):
            _m, per_tool, kwargs = strict_tool_binding(ChatOpenAI(model="gpt-5", api_key="test"))
        self.assertFalse(per_tool)
        self.assertEqual(kwargs, {})


class ReplyFormats(unittest.TestCase):
    """A requested reply schema reaches each provider in a form it accepts (built offline)."""

    FORMAT = {"type": "json_schema", "json_schema": {"name": "Reply", "strict": True, "schema": {
        "type": "object", "properties": {"content": {"type": "string"}},
        "required": ["content"], "additionalProperties": False,
    }}}

    def test_openai_decodes_the_schema(self):
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(model="gpt-5", api_key="test")
        self.assertEqual(reply_format_binding(model, self.FORMAT), ({"response_format": self.FORMAT}, ""))
        self.assertTrue(forced_tool_choice_supported(model))

    def test_deepseek_gets_json_mode_and_the_schema_in_the_prompt(self):
        # DeepSeek answers json_schema with 400 "This response_format type is unavailable now".
        from langchain_deepseek import ChatDeepSeek

        model = ChatDeepSeek(model="deepseek-chat", api_key="test", api_base="https://api.deepseek.com")
        kwargs, hint = reply_format_binding(model, self.FORMAT)
        payload = model._get_request_payload([HumanMessage("hi")], **kwargs)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertIn("JSON", hint)  # json_object mode needs the prompt to ask for JSON
        self.assertIn(json.dumps(self.FORMAT["json_schema"]["schema"]), hint)
        self.assertFalse(forced_tool_choice_supported(model))

    def test_other_wires_carry_the_schema_in_the_prompt(self):
        from langchain_anthropic import ChatAnthropic

        kwargs, hint = reply_format_binding(ChatAnthropic(model="claude-sonnet-5", api_key="test"), self.FORMAT)
        self.assertEqual(kwargs, {})
        self.assertIn('"content"', hint)


class StructuredFinalDecoding(unittest.IsolatedAsyncioTestCase):
    FORMAT = ReplyFormats.FORMAT

    async def test_openai_final_uses_strict_response_format(self):
        class Model:
            def bind(self, **kwargs):
                self.kwargs = kwargs
                return self

            async def ainvoke(self, messages, config=None):
                self.messages = messages
                return AIMessage(content='{"content":"finished"}')

        model = Model()
        with patch("core.tool_schema._model_classes", return_value={"BaseChatOpenAI"}):
            result = await decode_structured_final(model, self.FORMAT, [HumanMessage(content="draft")])
        self.assertEqual(json.loads(result.content), {"content": "finished"})
        self.assertTrue(model.kwargs["response_format"]["json_schema"]["strict"])
        self.assertFalse(model.kwargs["response_format"]["json_schema"]["schema"]["additionalProperties"])

    async def test_optional_null_is_removed_from_final_text(self):
        class Model:
            def bind(self, **kwargs):
                return self

            async def ainvoke(self, messages, config=None):
                return AIMessage(content='{"content":"done","note":null}')

        requested = {"type": "json_schema", "json_schema": {"name": "Reply", "schema": {
            "type": "object", "properties": {
                "content": {"type": "string"}, "note": {"type": "string"},
            }, "required": ["content"], "additionalProperties": False,
        }}}
        with patch("core.tool_schema._model_classes", return_value={"BaseChatOpenAI"}):
            result = await decode_structured_final(Model(), requested, [HumanMessage(content="draft")])
        self.assertEqual(json.loads(result.content), {"content": "done"})

    async def test_deepseek_requires_the_constrained_final_tool(self):
        class Model:
            def bind_tools(self, tools, **kwargs):
                self.tools = tools
                self.kwargs = kwargs
                return self

            async def ainvoke(self, messages, config=None):
                return AIMessage(content="unconstrained text")

        model = Model()
        with patch("core.tool_schema._model_classes", return_value={"ChatDeepSeek"}), patch(
            "core.tool_schema.strict_tool_binding", return_value=(model, True, {"strict": True})
        ):
            with self.assertRaisesRegex(RuntimeError, "schema-constrained"):
                await decode_structured_final(model, self.FORMAT, [HumanMessage(content="draft")])
        self.assertTrue(model.tools[0]["function"]["strict"])

    async def test_deepseek_final_tool_arguments_become_text(self):
        class Model:
            def bind_tools(self, tools, **kwargs):
                return self

            async def ainvoke(self, messages, config=None):
                return AIMessage(content="", tool_calls=[{
                    "name": "emit_final_reply", "args": {"content": "done"}, "id": "final-1",
                }])

        model = Model()
        with patch("core.tool_schema._model_classes", return_value={"ChatDeepSeek"}), patch(
            "core.tool_schema.strict_tool_binding", return_value=(model, True, {"strict": True})
        ):
            result = await decode_structured_final(model, self.FORMAT, [HumanMessage(content="draft")])
        self.assertEqual(json.loads(result.content), {"content": "done"})

    async def test_other_provider_uses_native_structured_output(self):
        class Model:
            def with_structured_output(self, schema, method=None, include_raw=False):
                self.schema = schema
                self.method = method
                self.include_raw = include_raw
                return self

            async def ainvoke(self, messages, config=None):
                return {"parsed": {"content": "finished"}, "raw": AIMessage(content=""), "parsing_error": None}

        model = Model()
        with patch("core.tool_schema._model_classes", return_value={"ChatAnthropic"}):
            result = await decode_structured_final(model, self.FORMAT, [HumanMessage(content="draft")])
        self.assertEqual(json.loads(result.content), {"content": "finished"})
        self.assertTrue(model.include_raw)
        self.assertEqual(model.method, "json_schema")

    async def test_anthropic_and_gemini_accept_the_named_schema_offline(self):
        from langchain_anthropic import ChatAnthropic
        from langchain_google_genai import ChatGoogleGenerativeAI

        schema = {**to_strict_parameters(self.FORMAT["json_schema"]["schema"]), "title": "Reply"}
        for model in (
            ChatAnthropic(model="claude-sonnet-4-5", api_key="test"),
            ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key="test"),
        ):
            with self.subTest(provider=type(model).__name__):
                self.assertIsNotNone(model.with_structured_output(schema, method="json_schema", include_raw=True))


class StrictCallsRunOnTheRealServer(unittest.IsolatedAsyncioTestCase):
    """A call shaped by the strict schema, decoded back, passes the server's own validation."""

    async def test_write_session_plan(self):
        webot = importlib.import_module("mcp_servers.webot")
        schema = next(t for t in await webot.mcp.list_tools() if t.name == "write_session_plan").inputSchema
        # What a strict decoder emits: every key present, omitted ones null.
        model_args = {
            "title": "Ship it",
            "items": [
                {"step": "write tests", "status": "completed", "notes": None},
                {"step": "release", "status": None, "notes": "after review"},
            ],
            "status": None,
        }
        args = {**drop_null_optionals(model_args, schema), "username": "alice", "source_session": "s1"}
        saved = {}
        with patch.object(webot, "save_session_plan", side_effect=lambda *a, **kw: saved.update(kw)), \
                patch.object(webot, "get_session_plan", return_value={"items": []}):
            await webot.mcp.call_tool("write_session_plan", args)
        self.assertEqual(saved["status"], "active")
        self.assertEqual(saved["items"], [
            {"step": "write tests", "status": "completed", "notes": ""},
            {"step": "release", "status": "pending", "notes": "after review"},
        ])



def _allow_all_permission(*_args, **_kwargs):
    return type("Permission", (), {
        "allowed": True, "requires_approval": False, "reason": "",
        "matched_rule": None, "policy": {}, "approval": None,
    })()


def _passthrough_hook_outcome(*_call_args, args=None, **_kwargs):
    return type("HookOutcome", (), {"args": dict(args or {}), "decision": None})()


class ToolNodeDropsStrictNulls(unittest.IsolatedAsyncioTestCase):
    async def test_tool_receives_its_own_defaults(self):
        received = {}

        def record(**kwargs):
            received.update(kwargs)
            return "ok"

        tool = StructuredTool(
            name="echo_tool",
            description="echo",
            args_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}, "limit": {"type": "integer", "default": 5}},
                "required": ["text"],
            },
            func=record,
        )
        node = UserAwareToolNode([tool], lambda: [tool])
        state = {
            "user_id": "alice",
            "session_mode": "bypass",
            "session_id": "s1",
            "messages": [AIMessage(content="", tool_calls=[
                {"name": "echo_tool", "args": {"text": "hi", "limit": None}, "id": "c1", "type": "tool_call"},
            ])],
        }
        with patch("core.agent.get_session_mode", return_value={"mode": "default"}), \
                patch("core.agent.resolve_permission_context", side_effect=_allow_all_permission), \
                patch("core.agent.run_tool_policy_hooks", side_effect=_passthrough_hook_outcome):
            await node(state, config={})
        self.assertEqual(received, {"text": "hi"})


if __name__ == "__main__":
    unittest.main()
