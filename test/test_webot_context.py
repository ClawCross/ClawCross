"""Tests for the single-pass compression module that replaced the
former multi-layer pipeline in webot/context.py."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from utils import checkpoint_repository
from webot import compression


def _build_messages(count: int, tokens_per_msg: int = 200):
    """Each HumanMessage is ~tokens_per_msg by char heuristic (4 chars/token)."""
    chars = tokens_per_msg * 4
    return [HumanMessage(content=f"msg-{i} " + ("x" * chars)) for i in range(count)]


class StaticCompressionViewTests(unittest.TestCase):
    def test_returns_raw_messages_when_no_record(self):
        msgs = _build_messages(5, tokens_per_msg=50)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "store.sqlite"
            view = compression.static_compression_view(
                user_id="u",
                session_id="s",
                messages=msgs,
                checkpoint_store_path=str(db),
            )
        self.assertEqual(len(view), 5)

    def test_view_substitutes_stored_summary(self):
        msgs = _build_messages(10, tokens_per_msg=50)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "store.sqlite"
            checkpoint_repository.save_context_compaction(
                str(db),
                "u#s",
                summary="prior summary",
                compacted_until=4,
                source_message_count=10,
                summary_token_estimate=50,
                metadata={},
            )
            view = compression.static_compression_view(
                user_id="u",
                session_id="s",
                messages=msgs,
                checkpoint_store_path=str(db),
            )
        self.assertEqual(len(view), 1 + (10 - 4))
        self.assertIsInstance(view[0], HumanMessage)
        self.assertIn("prior summary", view[0].content)


class ApplyCompressionTests(unittest.TestCase):
    def test_below_trigger_returns_raw(self):
        msgs = _build_messages(5, tokens_per_msg=50)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "store.sqlite"
            result = compression.apply_compression(
                user_id="u",
                session_id="s",
                messages=msgs,
                history_token_budget=100_000,
                checkpoint_store_path=str(db),
            )
        self.assertFalse(result.triggered)
        self.assertEqual(result.reason, "below_trigger")
        self.assertEqual(len(result.view), 5)

    def test_above_trigger_folds_and_writes_summary(self):
        msgs = _build_messages(40, tokens_per_msg=200)
        captured = {}

        def fake_summarizer(prev, segment, target_chars):
            captured["segment_len"] = len(segment)
            captured["prev"] = prev
            captured["target_chars"] = target_chars
            return "compact summary OK"

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "store.sqlite"
            result = compression.apply_compression(
                user_id="u",
                session_id="s",
                messages=msgs,
                history_token_budget=2000,
                checkpoint_store_path=str(db),
                preserve_recent=4,
                summarizer=fake_summarizer,
            )
            record = checkpoint_repository.get_context_compaction(str(db), "u#s")
        self.assertTrue(result.triggered)
        self.assertEqual(result.reason, "compressed")
        self.assertGreater(result.compacted_until, 0)
        self.assertIn("compact summary OK", result.summary)
        self.assertIsNotNone(record)
        self.assertEqual(record.compacted_until, result.compacted_until)
        self.assertGreater(captured["segment_len"], 0)
        self.assertEqual(captured["prev"], "")

    def test_min_new_messages_throttles(self):
        msgs = _build_messages(40, tokens_per_msg=200)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "store.sqlite"
            # Pre-existing record already folded up to index 36; only 4 new
            # messages -> below min_new_messages default (6).
            checkpoint_repository.save_context_compaction(
                str(db),
                "u#s",
                summary="prior",
                compacted_until=36,
                source_message_count=40,
                summary_token_estimate=20,
                metadata={},
            )
            result = compression.apply_compression(
                user_id="u",
                session_id="s",
                messages=msgs,
                history_token_budget=2000,
                checkpoint_store_path=str(db),
                preserve_recent=4,
                summarizer=lambda p, s, c: "should-not-be-called",
            )
            record = checkpoint_repository.get_context_compaction(str(db), "u#s")
        self.assertFalse(result.triggered)
        self.assertIn(result.reason, {"min_new_messages", "below_trigger"})
        # Record unchanged
        self.assertEqual(record.summary, "prior")
        self.assertEqual(record.compacted_until, 36)

    def test_boundary_avoids_splitting_ai_tool_pair(self):
        msgs = [
            HumanMessage(content="u1"),
            AIMessage(content="a1", tool_calls=[{"name": "Bash", "args": {}, "id": "t1"}]),
            ToolMessage(content="result1", tool_call_id="t1", name="Bash"),
            HumanMessage(content="u2 " + "x" * 4000),
            AIMessage(content="a2", tool_calls=[{"name": "Bash", "args": {}, "id": "t2"}]),
            ToolMessage(content="result2 " + "y" * 4000, tool_call_id="t2", name="Bash"),
            HumanMessage(content="u3 " + "z" * 4000),
            HumanMessage(content="u4"),
            HumanMessage(content="u5"),
            HumanMessage(content="u6"),
        ]
        b = compression._pick_boundary(msgs, current_until=0, preserve_recent=3, target_tokens=500)
        # Boundary must not land between AIMessage(tool_calls) and its ToolMessage
        if 0 < b < len(msgs):
            prev = msgs[b - 1]
            self.assertFalse(
                isinstance(prev, AIMessage) and getattr(prev, "tool_calls", None),
                f"boundary {b} splits AI+Tool pair",
            )


class TrimNewInputTests(unittest.TestCase):
    def test_short_input_unchanged(self):
        msgs = [HumanMessage(content="hi")]
        out = compression.trim_new_input_if_oversized(msgs, user_id="u", session_id="s")
        self.assertEqual(out, msgs)

    def test_oversized_input_replaced_when_artifacts_enabled(self):
        big = "x" * 50_000
        msgs = [HumanMessage(content=big)]
        with patch("webot.context._runtime_artifacts_enabled", return_value=True), \
                patch("webot.context._store_runtime_text") as store, \
                patch("webot.runtime_store.create_runtime_artifact"):
            fake_path = Path("/tmp/fake-input.txt")
            store.return_value = fake_path
            out = compression.trim_new_input_if_oversized(msgs, user_id="u", session_id="s")
        self.assertEqual(len(out), 1)
        body = out[0].content
        self.assertIn("[User input budgeted]", body)
        self.assertIn("saved_to=", body)
        self.assertLess(len(body), len(big))

    def test_oversized_input_unchanged_when_artifacts_disabled(self):
        big = "x" * 50_000
        msgs = [HumanMessage(content=big)]
        with patch("webot.context._runtime_artifacts_enabled", return_value=False):
            out = compression.trim_new_input_if_oversized(msgs, user_id="u", session_id="s")
        self.assertEqual(out, msgs)


if __name__ == "__main__":
    unittest.main()
