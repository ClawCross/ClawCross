"""Offline checks for DeepSeek structured replies via the Responses API."""

import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for path in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src" / "backend")):
    if path not in sys.path:
        sys.path.insert(0, path)

from agents.client import response_format_of  # noqa: E402
from agents.messages import AgentMessage  # noqa: E402
from agents.store import LLM, Agent  # noqa: E402
from external.llm import LlmRuntime  # noqa: E402
from oasis.schemas import OasisReplyOut  # noqa: E402
from webot.engine.deepseek_responses import deepseek_structured_turn  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402


def _response(*, text: str = "", calls=(), status="completed"):
    return SimpleNamespace(
        status=status, output_text=text,
        output=[SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)
                for name, args, call_id in calls],
        usage=SimpleNamespace(input_tokens=20, output_tokens=4,
                              input_tokens_details=SimpleNamespace(cached_tokens=5)),
        incomplete_details=SimpleNamespace(reason="max_output_tokens") if status != "completed" else None,
        error=None,
    )


class DeepSeekStructuredReply(unittest.TestCase):
    def setUp(self):
        from langchain_deepseek import ChatDeepSeek

        self.model = ChatDeepSeek(model="deepseek-flash", api_key="test", api_base="https://api.deepseek.com")
        self.format = response_format_of(OasisReplyOut)
        self.agent = Agent(agent_id="tmp__t__critic__1", owner="alice", name="Critic", driver=LLM, config={})

    def _client(self, response):
        return SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=response)), close=AsyncMock())

    def test_schema_is_text_format_without_reply_tool(self):
        body = {"clawcross_type": "oasis reply", "reply_to": None, "content": "有用", "votes": None}
        client = self._client(_response(text=json.dumps(body, ensure_ascii=False)))
        with patch("common.llm_factory.create_chat_model", return_value=self.model), patch(
            "webot.engine.deepseek_responses.AsyncOpenAI", return_value=client,
        ):
            result = asyncio.run(LlmRuntime().ask(
                self.agent, AgentMessage(text="谈谈测试"), context={}, mode=None,
                enabled_tools=None, response_format=self.format, timeout=None,
            ))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(json.loads(result.content), {"clawcross_type": "oasis reply", "content": "有用"})
        kwargs = client.responses.create.await_args.kwargs
        self.assertEqual(kwargs["text"]["format"]["type"], "json_schema")
        self.assertNotIn("tools", kwargs)
        self.assertEqual(kwargs["input"][0]["role"], "user")

    def test_tools_and_schema_share_one_request(self):
        client = self._client(_response(calls=[("read_file", {"path": "a.py"}, "call-1")]))
        tools = [{"type": "function", "function": {
            "name": "read_file", "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                           "required": ["path"], "additionalProperties": False},
        }}]
        with patch("webot.engine.deepseek_responses.AsyncOpenAI", return_value=client):
            reply = asyncio.run(deepseek_structured_turn(
                self.model, [HumanMessage(content="read a.py")], self.format, tools,
            ))
        self.assertEqual(reply.tool_calls[0]["name"], "read_file")
        self.assertEqual(reply.usage_metadata["input_token_details"]["cache_read"], 5)
        kwargs = client.responses.create.await_args.kwargs
        self.assertEqual(kwargs["tools"][0]["name"], "read_file")
        self.assertEqual(kwargs["text"]["format"]["type"], "json_schema")
        self.assertFalse(any(t["name"] == "emit_final_reply" for t in kwargs["tools"]))

    def test_tool_history_replays_call_and_result(self):
        client = self._client(_response(text='{"clawcross_type":"oasis reply","reply_to":null,"content":"done","votes":null}'))
        history = [HumanMessage(content="read"), AIMessage(content="", tool_calls=[
            {"name": "read_file", "args": {"path": "a.py"}, "id": "call-1", "type": "tool_call"},
        ]), ToolMessage(content="file contents", tool_call_id="call-1")]
        with patch("webot.engine.deepseek_responses.AsyncOpenAI", return_value=client):
            asyncio.run(deepseek_structured_turn(self.model, history, self.format))
        items = client.responses.create.await_args.kwargs["input"]
        self.assertIn("function_call", [item["type"] for item in items])
        self.assertIn("function_call_output", [item["type"] for item in items])

    def test_incomplete_response_is_an_error(self):
        client = self._client(_response(status="incomplete"))
        with patch("webot.engine.deepseek_responses.AsyncOpenAI", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "max_output_tokens"):
                asyncio.run(deepseek_structured_turn(
                    self.model, [HumanMessage(content="hello")], self.format,
                ))


if __name__ == "__main__":
    unittest.main()
