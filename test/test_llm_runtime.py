"""An llm agent: one direct model call, structured when asked."""

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for path in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src" / "backend")):
    if path not in sys.path:
        sys.path.insert(0, path)

from agents.client import response_format_of  # noqa: E402
from agents.messages import AgentMessage  # noqa: E402
from agents.store import LLM, Agent  # noqa: E402
from external.llm import LlmRuntime  # noqa: E402
from oasis.schemas import OasisReplyOut  # noqa: E402


class DeepSeekStructuredReply(unittest.TestCase):
    """DeepSeek's thinking models reject a forced tool_choice (the way
    with_structured_output works there), so the schema is offered as an
    optional strict tool. Requests are captured on the wire; nothing is sent."""

    def ask(self, message: dict, finish_reason: str = "stop") -> tuple[object, list]:
        from langchain_deepseek import ChatDeepSeek

        sent = []

        def answer(request: httpx.Request) -> httpx.Response:
            sent.append((str(request.url), json.loads(request.content)))
            return httpx.Response(200, json={
                "id": "r1", "object": "chat.completion", "created": 0, "model": "deepseek-flash",
                "choices": [{"index": 0, "message": {"role": "assistant", **message}, "finish_reason": finish_reason}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })

        model = ChatDeepSeek(
            model="deepseek-flash", api_key="test", api_base="https://api.deepseek.com",
            http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
        )
        agent = Agent(agent_id="tmp__t__critic__1", owner="alice", name="Critic", driver=LLM, config={})
        with patch("common.llm_factory.create_chat_model", return_value=model):
            result = asyncio.run(LlmRuntime().ask(
                agent, AgentMessage(text="谈谈测试"), context={}, mode=None, enabled_tools=None,
                response_format=response_format_of(OasisReplyOut), timeout=None))
        return result, sent

    def test_a_tool_call_is_the_reply(self):
        args = {"clawcross_type": "oasis reply", "reply_to": None, "content": "有用", "votes": None}
        result, sent = self.ask({"content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "OasisReplyOut", "arguments": json.dumps(args, ensure_ascii=False)},
        }]})

        self.assertTrue(result.ok, result.error)
        self.assertEqual(json.loads(result.content), {"clawcross_type": "oasis reply", "content": "有用"})
        url, body = sent[0]
        self.assertTrue(url.endswith("/beta/chat/completions"))  # where DeepSeek enforces strict
        self.assertEqual(body["tool_choice"], "auto")
        self.assertIs(body["tools"][0]["function"]["strict"], True)
        self.assertNotIn("response_format", body)

    def test_a_text_answer_is_passed_on_for_the_caller_to_parse(self):
        text = '{"clawcross_type": "oasis reply", "reply_to": null, "content": "有用", "votes": []}'
        result, sent = self.ask({"content": text})

        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.content, text)
        self.assertIn("JSON schema", json.dumps(sent[0][1]["messages"], ensure_ascii=False))

    def test_a_reply_cut_off_by_max_tokens_is_an_error_not_an_empty_post(self):
        # Reasoning counts against max_tokens; the arguments stop mid-JSON.
        result, _sent = self.ask({"content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "OasisReplyOut", "arguments": '{"clawcross_type": "oasis reply", "content": "半'},
        }]}, finish_reason="length")

        self.assertFalse(result.ok)
        self.assertIn("max_tokens", result.error)


if __name__ == "__main__":
    unittest.main()
