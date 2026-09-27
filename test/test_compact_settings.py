import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from webot import compression as c
from webot.runtime_settings import ContextSettings
from utils.checkpoint_repository import save_context_compaction, get_context_compaction


class CompactSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "context.db")
        self.messages = []
        for i in range(12):
            self.messages.extend([
                HumanMessage(content=f"request-{i}:" + "中文上下文" * 300),
                AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"path": f"file-{i}"}, "id": f"call-{i}"}]),
                ToolMessage(content="tool evidence " * 100, tool_call_id=f"call-{i}"),
                AIMessage(content=f"completed-{i}"),
            ])

    def compact(self, **kwargs):
        return c.apply_compression(user_id="alice", session_id="s", messages=self.messages,
            history_token_budget=6000, checkpoint_store_path=self.path,
            summarizer=lambda *a: "- 已完成: 保留关键决定", **kwargs)

    def test_retains_whole_turns_and_persisted_metrics(self):
        result = self.compact(settings=ContextSettings(preserve_recent_turns=2, summary_tokens=256))
        self.assertTrue(result.triggered)
        self.assertIsInstance(self.messages[result.compacted_until], HumanMessage)
        self.assertLessEqual(result.compacted_until, len(self.messages) - 8)
        self.assertEqual(result.view[-8:], self.messages[-8:])
        stored = get_context_compaction(self.path, "alice#s")
        self.assertEqual(stored.metadata["after_tokens"], result.view_tokens)
        self.assertEqual(stored.metadata["source_range"], [0, result.compacted_until])
        self.assertEqual(c.static_compression_view(user_id="alice", session_id="s", messages=self.messages, checkpoint_store_path=self.path), result.view)

    def test_disabled_auto_retains_existing_view_and_manual_works(self):
        options = ContextSettings(auto_compact=False, preserve_recent_turns=2, summary_tokens=256)
        self.assertEqual(self.compact(settings=options).reason, "disabled")
        manual = self.compact(settings=options, force=True)
        self.assertTrue(manual.triggered)
        self.assertEqual(self.compact(settings=options).view, manual.view)

    def test_recent_turns_can_exceed_target_without_being_dropped(self):
        result = self.compact(settings=ContextSettings(preserve_recent_turns=4, trigger_tokens=4000, target_tokens=3000, summary_tokens=256))
        self.assertTrue(result.triggered)
        self.assertEqual(result.view[-16:], self.messages[-16:])
        self.assertFalse(result.metadata["target_met"])

    def test_minimum_new_messages_selects_a_later_whole_turn(self):
        boundary = c._pick_boundary(self.messages, current_until=0, preserve_recent=8,
            target_tokens=1000000, min_new=6, whole_turns=True)
        self.assertEqual(boundary, 8)

    def test_chinese_summary_token_cap(self):
        text = c._cap_summary_tokens("中文压缩" * 1000, 128)
        self.assertLessEqual(c._approx_tokens(text), 128)

    def test_stale_snapshot_cannot_overwrite_compaction(self):
        self.assertTrue(self.compact(force=True, settings=ContextSettings(summary_tokens=256)).triggered)
        self.messages = self.messages[:-1]
        self.assertEqual(self.compact(force=True).reason, "stale_snapshot")

    def test_cross_worker_compare_and_swap(self):
        save_context_compaction(self.path, "alice#s", summary="one", compacted_until=4,
            source_message_count=8, summary_token_estimate=10, expected_updated_at="")
        with self.assertRaises(RuntimeError):
            save_context_compaction(self.path, "alice#s", summary="stale", compacted_until=2,
                source_message_count=8, summary_token_estimate=10, expected_updated_at="")
        self.assertEqual(get_context_compaction(self.path, "alice#s").summary, "one")

    def test_summarizer_chunks_entire_tool_arguments(self):
        payloads = []
        class Model:
            def invoke(self, messages):
                payloads.append(messages)
                return AIMessage(content="已保留关键记录")
        messages = [AIMessage(content="", tool_calls=[{"id": "call", "name": "run_command",
            "args": {"command": "始" + "中" * 4500 + "末尾必须送入摘要器"}}])]
        with patch("services.llm_factory.create_chat_model", return_value=Model()):
            summarize = c.make_llm_summarizer(max_output_tokens=128, input_token_budget=1600)
            summarize("", messages, 500)
        self.assertGreater(len(payloads), 1)
        self.assertIn("末尾必须送入摘要器", "".join(p[1].content for p in payloads))
        for payload in payloads:
            self.assertLessEqual(c.estimate_messages_tokens(payload), 1600)
        self.assertEqual(summarize.stats["fallback_count"], 0)

