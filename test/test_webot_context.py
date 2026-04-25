import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import webot.context as webot_context
from webot.context import budget_tool_messages, compact_history_messages
from webot.context import budget_user_messages


class WeBotContextTests(unittest.TestCase):
    def test_budget_user_messages_preserves_latest_human_message(self):
        old_text = "old-" * 80
        latest_text = "latest-" * 120

        budgeted = budget_user_messages(
            user_id="alice",
            session_id="session-1",
            messages=[
                HumanMessage(content=old_text),
                HumanMessage(content=latest_text),
            ],
            total_char_budget=100,
            item_char_limit=80,
            preserve_latest_human_messages=1,
        )

        self.assertEqual(len(budgeted), 2)
        self.assertIn("[User input budgeted]", budgeted[0].content)
        self.assertEqual(budgeted[1].content, latest_text)

    def test_budget_user_messages_supports_env_unlimited_limits(self):
        message_text = "x" * 20000

        with patch.dict(
            os.environ,
            {
                "WEBOT_USER_INPUT_CHAR_BUDGET": "0",
                "WEBOT_USER_INPUT_ITEM_LIMIT": "0",
                "WEBOT_SKIP_LATEST_USER_INPUT_BUDGET": "0",
            },
            clear=False,
        ):
            budgeted = budget_user_messages(
                user_id="alice",
                session_id="session-1",
                messages=[HumanMessage(content=message_text)],
            )

        self.assertEqual(len(budgeted), 1)
        self.assertEqual(budgeted[0].content, message_text)

    def test_budget_tool_messages_replaces_large_payload_with_reference(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"WEBOT_RUNTIME_ARTIFACTS_ENABLED": "1"}):
                with patch.object(webot_context, "USER_FILES_DIR", Path(tmpdir)):
                    messages = [
                        ToolMessage(content="x" * 5000, tool_call_id="call-1", name="read_file"),
                    ]
                    budgeted = budget_tool_messages(
                        user_id="alice",
                        session_id="session-1",
                        messages=messages,
                        total_char_budget=100,
                        item_char_limit=80,
                    )
                    self.assertEqual(len(budgeted), 1)
                    text = budgeted[0].content
                    self.assertIn("[Tool result budgeted]", text)
                    self.assertIn("saved_to=", text)

    def test_compact_history_messages_inserts_summary_and_keeps_recent(self):
        messages = [HumanMessage(content=f"message-{index} " * 20) for index in range(20)]
        compacted = compact_history_messages(messages, max_messages=8, preserve_recent=4, context_token_budget=200)
        self.assertLessEqual(len(compacted), 8)
        self.assertIsInstance(compacted[0], HumanMessage)
        self.assertIn("压缩摘要", compacted[0].content)
        self.assertIn("message-19", compacted[-1].content)

    def test_context_compressor_evict_preserves_summary_and_latest_user_request(self):
        from utils.context_compressor import level_evict

        messages = [
            SystemMessage(content="system " + ("s" * 5000)),
            HumanMessage(content="压缩摘要 " + ("x" * 5000)),
            HumanMessage(content="latest user request must remain"),
        ]
        result = level_evict(messages, token_budget=10, preserve_recent=1)

        self.assertTrue(
            any(isinstance(msg, HumanMessage) and str(msg.content).startswith("压缩摘要") for msg in result)
        )
        self.assertTrue(
            any(isinstance(msg, HumanMessage) and msg.content == "latest user request must remain" for msg in result)
        )

    def test_context_limits_support_model_defaults_and_user_override(self):
        from utils.context_limits import infer_model_context_window, resolve_history_token_budget

        with patch.dict(
            os.environ,
            {
                "LLM_MODEL": "MiniMax-M2.7",
            },
            clear=False,
        ):
            os.environ.pop("LLM_CONTEXT_WINDOW", None)
            os.environ.pop("WEBOT_CONTEXT_TOKEN_BUDGET", None)
            self.assertEqual(infer_model_context_window(), 1_000_000)
            self.assertEqual(resolve_history_token_budget(), 128_000)

        with patch.dict(os.environ, {"WEBOT_CONTEXT_TOKEN_BUDGET": "77777"}, clear=False):
            self.assertEqual(resolve_history_token_budget(), 77777)


if __name__ == "__main__":
    unittest.main()
