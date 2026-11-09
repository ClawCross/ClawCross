import sys
import types
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from api.openai_models import ChatCompletionRequest
from api.openai_service import OpenAIChatService


class OpenAIServiceToolResultTests(unittest.TestCase):
    def _service(self) -> OpenAIChatService:
        agent = types.SimpleNamespace(agent_app=None)
        return OpenAIChatService(
            internal_token="test",
            verify_password=lambda u, p: True,
            agent=agent,
            extract_text=lambda content: content if isinstance(content, str) else str(content or ""),
            build_human_message=lambda text, images, files, audios: types.SimpleNamespace(content=text),
        )

    def test_trailing_tool_results_include_preceding_assistant_tool_call(self):
        svc = self._service()
        req = ChatCompletionRequest(
            model="webot",
            messages=[
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {"name": "start_background_command", "arguments": "{\"command\":\"echo hi\"}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_123",
                    "name": "start_background_command",
                    "content": "ok",
                },
            ],
        )

        built = svc._build_input_messages(req)
        self.assertEqual(type(built[0]).__name__, "AIMessage")
        self.assertEqual(built[0].tool_calls[0]["id"], "call_123")
        self.assertEqual(type(built[1]).__name__, "ToolMessage")
        self.assertEqual(built[1].tool_call_id, "call_123")


if __name__ == "__main__":
    unittest.main()
