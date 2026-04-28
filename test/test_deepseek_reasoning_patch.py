import sys
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from langchain_core.messages import AIMessage, HumanMessage
from services.llm_factory import create_chat_model


class DeepSeekReasoningPatchTests(unittest.TestCase):
    def test_reasoning_content_is_round_tripped_into_next_payload(self):
        with mock.patch.dict(
            "os.environ",
            {
                "LLM_MODEL": "deepseek-chat",
                "LLM_API_KEY": "test-key",
                "LLM_BASE_URL": "https://api.deepseek.com",
                "LLM_PROVIDER": "deepseek",
            },
            clear=False,
        ):
            model = create_chat_model(
                model="deepseek-chat",
                api_key="test-key",
                base_url="https://api.deepseek.com",
                provider="deepseek",
            )

        payload = model._get_request_payload(
            [
                HumanMessage(content="第一问"),
                AIMessage(
                    content="这是回答",
                    additional_kwargs={"reasoning_content": "这是推理过程"},
                ),
                HumanMessage(content="第二问"),
            ]
        )

        assistant_messages = [
            item for item in payload.get("messages", []) if item.get("role") == "assistant"
        ]
        self.assertEqual(len(assistant_messages), 1)
        self.assertEqual(assistant_messages[0]["content"], "这是回答")
        self.assertEqual(assistant_messages[0]["reasoning_content"], "这是推理过程")


if __name__ == "__main__":
    unittest.main()
