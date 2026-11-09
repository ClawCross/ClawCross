import json
import sys
import unittest
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from api.openai_models import ChatMessage
from api.openai_protocol import OpenAIProtocolHelper


class OpenAIProtocolHelperTests(unittest.TestCase):
    def setUp(self):
        self.build_calls = []

        def build_human_message(text, images, files, audios):
            self.build_calls.append(
                {
                    "text": text,
                    "images": images,
                    "files": files,
                    "audios": audios,
                }
            )
            return HumanMessage(content="stubbed")

        self.helper = OpenAIProtocolHelper(build_human_message=build_human_message)

    def _decode_chunk(self, chunk_text):
        self.assertTrue(chunk_text.startswith("data: "))
        self.assertTrue(chunk_text.endswith("\n\n"))
        return json.loads(chunk_text[len("data: ") : -2])

    def test_openai_msg_to_human_message_handles_none_and_plain_text(self):
        empty_msg = ChatMessage(role="user", content=None)
        text_msg = ChatMessage(role="user", content="hello Clawcross")

        self.assertEqual(self.helper.openai_msg_to_human_message(empty_msg).content, "(空消息)")
        self.assertEqual(self.helper.openai_msg_to_human_message(text_msg).content, "hello Clawcross")
        self.assertEqual(self.build_calls, [])

    def test_openai_msg_to_human_message_collects_multimodal_parts(self):
        msg = ChatMessage(
            role="user",
            content=[
                {"type": "text", "text": "alpha"},
                {"type": "text", "text": "beta"},
                {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
                {"type": "input_audio", "input_audio": {"data": "ZmFrZQ==", "format": "mp3"}},
                {"type": "file", "file": {"filename": "notes.txt", "file_data": "ZmlsZS1ib2R5"}},
            ],
        )

        result = self.helper.openai_msg_to_human_message(msg)

        self.assertEqual(result.content, "stubbed")
        self.assertEqual(
            self.build_calls[-1],
            {
                "text": "alpha\nbeta",
                "images": ["https://example.test/a.png"],
                "files": [{"name": "notes.txt", "content": "ZmlsZS1ib2R5", "type": "text"}],
                "audios": [{"base64": "ZmFrZQ==", "format": "mp3", "name": "recording.mp3"}],
            },
        )

    def test_make_openai_response_sets_tool_calls_finish_reason(self):
        response = self.helper.make_openai_response(
            "done",
            model="gpt-5.4",
            tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "search", "arguments": "{}"}}],
        )

        self.assertEqual(response["object"], "chat.completion")
        self.assertEqual(response["model"], "gpt-5.4")
        self.assertEqual(response["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(response["choices"][0]["message"]["tool_calls"][0]["function"]["name"], "search")

    def test_make_openai_chunk_serializes_role_content_and_meta(self):
        role_chunk = self.helper.make_openai_chunk(completion_id="chatcmpl-test", model="gpt-5.4")
        content_chunk = self.helper.make_openai_chunk(
            completion_id="chatcmpl-test",
            content="hello",
            model="gpt-5.4",
            meta={"round": 1, "type": "delta"},
        )

        parsed_role = self._decode_chunk(role_chunk)
        parsed_content = self._decode_chunk(content_chunk)

        self.assertEqual(parsed_role["choices"][0]["delta"]["role"], "assistant")
        self.assertEqual(parsed_content["choices"][0]["delta"]["content"], "hello")
        self.assertEqual(parsed_content["choices"][0]["delta"]["meta"]["round"], 1)

    def test_extract_external_tool_names_and_format_tool_calls(self):
        tools = [
            {"type": "function", "function": {"name": "search"}},
            {"name": "legacy_lookup"},
        ]
        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {"id": "call_search", "name": "search", "args": {"q": "clawcross"}},
                {"id": "call_skip", "name": "internal_only", "args": {"q": "skip"}},
            ],
        )

        external_names = self.helper.extract_external_tool_names(tools)
        formatted = self.helper.format_tool_calls_for_openai(ai_msg, external_names)
        tool_chunk = self.helper.make_tool_calls_chunk(
            completion_id="chatcmpl-tool",
            model="gpt-5.4",
            tool_calls=formatted,
        )
        parsed_chunk = self._decode_chunk(tool_chunk)

        self.assertEqual(external_names, {"search", "legacy_lookup"})
        self.assertEqual(len(formatted), 1)
        self.assertEqual(formatted[0]["function"]["name"], "search")
        self.assertEqual(formatted[0]["function"]["arguments"], json.dumps({"q": "clawcross"}, ensure_ascii=False))
        self.assertEqual(parsed_chunk["choices"][0]["finish_reason"], "tool_calls")

    def test_list_models_payload_matches_openai_shape(self):
        payload = self.helper.list_models_payload()
        self.assertEqual(payload["object"], "list")
        self.assertEqual(payload["data"][0]["id"], "webot")
        self.assertEqual(payload["data"][0]["object"], "model")


if __name__ == "__main__":
    unittest.main()
