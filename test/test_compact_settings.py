import sys
import tempfile
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "backend"))
from webot import compression as c
from webot.runtime_settings import ContextSettings
from webot.checkpoint_repository import save_context_compaction, get_context_compaction, delete_context_compaction


class HistoryViewRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_input_respects_recent_turns_and_disabled_compaction(self):
        from webot.engine.agent import TeamAgent
        engine = TeamAgent.__new__(TeamAgent)
        engine._db_path = '/tmp/unused-history-retention.db'
        engine.restore_context_usage = AsyncMock()
        engine.get_thread_last_context_tokens = lambda _: 0
        engine._background_compression = SimpleNamespace(prepare_for_model=AsyncMock(return_value=None))
        turn = SimpleNamespace(user_id='alice', session_id='s', thread_id='alice#s')
        history = [message for index in range(8) for message in (
            HumanMessage(content=f'user-{index} ' + '中' * 500), AIMessage(content=f'reply-{index}'))]
        for enabled in (True, False):
            with self.subTest(auto_compact=enabled), \
                    patch('webot.engine.agent.get_context_compaction', return_value=None):
                view, _, _ = await engine._history_view({'messages': history}, turn, history,
                    settings=ContextSettings(auto_compact=enabled, preserve_recent_turns=3),
                    history_budget=600, preserve_recent=8, prefix_tokens=10,
                    output_reserve=100, context_window=100000, model_name='test')
                if enabled:
                    self.assertEqual(view[-2:], history[-2:])
                    self.assertLessEqual(c.estimate_messages_tokens(view), 600)
                else:
                    self.assertEqual(view, history)


class CompactSettingsTests(unittest.TestCase):
    def test_compaction_history_keeps_versions_and_reset_removes_them(self):
        first = save_context_compaction(self.path, 'alice#s', summary='first',
            compacted_until=2, source_message_count=4, summary_token_estimate=2,
            expected_updated_at='')
        # Simulate an existing latest-only database from before this change.
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TABLE context_compaction_history')
        save_context_compaction(self.path, 'alice#s', summary='second',
            compacted_until=4, source_message_count=6, summary_token_estimate=2,
            expected_updated_at=first.updated_at)
        with self.assertRaises(RuntimeError):
            save_context_compaction(self.path, 'alice#s', summary='stale',
                compacted_until=1, source_message_count=2, summary_token_estimate=2,
                expected_updated_at=first.updated_at)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT summary FROM context_compaction_history ORDER BY id').fetchall(),
                             [('first',), ('second',)])
        delete_context_compaction(self.path, 'alice#s')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM context_compaction_history').fetchone()[0], 0)

    def test_emergency_compacts_inside_one_turn_without_orphaning_tools(self):
        self.messages = [HumanMessage(content='Original authorization: inspect files')]
        for i in range(8):
            self.messages.extend([
                AIMessage(content='', tool_calls=[{'name': 'read_file', 'args': {}, 'id': str(i)}]),
                ToolMessage(content='中' * 1000, tool_call_id=str(i)),
            ])
        options = ContextSettings(summary_tokens=256)
        automatic = self.compact(settings=options)
        self.assertTrue(automatic.triggered)
        self.assertTrue(automatic.metadata['retention_limited_by_budget'])
        self.assertEqual(automatic.view[-2:], self.messages[-2:])
        delete_context_compaction(self.path, 'alice#s')
        result = self.compact(settings=options, emergency=True)
        self.assertTrue(result.triggered)
        self.assertEqual(result.metadata['strategy'], 'emergency_tool_boundary')
        self.assertIsInstance(self.messages[result.compacted_until], AIMessage)
        self.assertEqual(result.view[-2:], self.messages[-2:])
        self.assertEqual(self.messages[0].content, 'Original authorization: inspect files')

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
        result = self.compact(settings=ContextSettings(preserve_recent_turns=2, summary_tokens=256, target_tokens=5000))
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

    def test_token_target_takes_priority_over_recent_turn_count(self):
        result = self.compact(settings=ContextSettings(preserve_recent_turns=4, trigger_tokens=4000, target_tokens=3000, summary_tokens=256))
        self.assertTrue(result.triggered)
        self.assertTrue(result.metadata["target_met"])
        self.assertTrue(result.metadata['retention_limited_by_budget'])
        self.assertLessEqual(result.view_tokens, 3000)
        call_ids = {call['id'] for msg in result.view for call in getattr(msg, 'tool_calls', [])}
        self.assertTrue(all(msg.tool_call_id in call_ids for msg in result.view if isinstance(msg, ToolMessage)))

    def test_temporary_budget_keeps_complete_turns_that_fit(self):
        original = list(self.messages)
        view = c.temporary_bounded_view(self.messages, 5000, preserve_recent_turns=4)
        self.assertEqual(view[-8:], original[-8:])
        self.assertLessEqual(c.estimate_messages_tokens(view), 5000)
        self.assertEqual(self.messages, original)

    def test_temporary_budget_does_not_omit_a_short_history(self):
        recent = [HumanMessage(content='hello'), AIMessage(content='hi')]
        self.assertEqual(c.temporary_bounded_view(recent, 1000, preserve_recent_turns=4), recent)

    def test_temporary_budget_preserves_summary_and_protected_turns(self):
        result = self.compact(settings=ContextSettings(preserve_recent_turns=4, summary_tokens=256))
        view = c.temporary_bounded_view(result.view, 1000, preserve_recent_turns=4)
        self.assertEqual(view, result.view)

    def test_large_window_uses_a_small_automatic_target(self):
        messages = [HumanMessage(content='Inspect the files; do not delete anything.')]
        for i in range(40):
            messages.extend([AIMessage(content='', tool_calls=[{'name':'read_file','args':{},'id':str(i)}]),
                             ToolMessage(content='中' * 1000, tool_call_id=str(i))])
        result = c.apply_compression(user_id='alice', session_id='s', messages=messages,
            history_token_budget=1_000_000, checkpoint_store_path=self.path,
            settings=ContextSettings(trigger_tokens=20_000), summarizer=lambda *a: 'Task: inspect files; never delete.')
        self.assertTrue(result.triggered)
        self.assertEqual(result.metadata['target_tokens'], 10_000)
        self.assertEqual(result.metadata['summary_budget_tokens'], 8000)
        self.assertLessEqual(result.view_tokens, 10_000)
        self.assertEqual(result.view[-2:], messages[-2:])
        self.assertIn('never delete', result.summary)
        self.assertEqual(messages[0].content, 'Inspect the files; do not delete anything.')

    def test_summary_can_use_most_of_the_target_and_scales_for_legacy_small_inputs(self):
        from webot.runtime_settings import resolve_compaction_summary_budget
        defaults = ContextSettings()
        self.assertEqual(defaults.summary_tokens, 8000)
        self.assertEqual(defaults.summarizer_input_tokens, 32000)
        self.assertEqual(resolve_compaction_summary_budget(defaults, 10000), 8000)
        self.assertEqual(resolve_compaction_summary_budget(defaults, 1000), 800)
        legacy = ContextSettings(summarizer_input_tokens=8000)
        self.assertEqual(resolve_compaction_summary_budget(legacy, 10000), 4000)
        small_target = ContextSettings(target_tokens=1000)
        self.assertEqual(resolve_compaction_summary_budget(small_target, 1000), 800)

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
        with patch("common.llm_factory.create_chat_model", return_value=Model()):
            summarize = c.make_llm_summarizer(max_output_tokens=128, input_token_budget=1600)
            summarize("", messages, 500)
        self.assertGreater(len(payloads), 1)
        self.assertIn("末尾必须送入摘要器", "".join(p[1].content for p in payloads))
        for payload in payloads:
            self.assertLessEqual(c.estimate_messages_tokens(payload), 1600)
        self.assertEqual(summarize.stats["fallback_count"], 0)

    def test_runtime_deltas_count_towards_history_budget(self):
        message = HumanMessage(content="short", additional_kwargs={"framework_runtime_delta": "中" * 1000})
        self.assertGreaterEqual(c.estimate_messages_tokens([message]), 1000)

    def test_rebased_state_counts_full_snapshot_without_mutating_history(self):
        message = HumanMessage(content="short", additional_kwargs={
            "framework_runtime_state": "中" * 1000, "framework_runtime_delta": "+ changed"})
        rebased = c._rebase_runtime_view([message])
        self.assertGreaterEqual(c.estimate_messages_tokens(rebased), 1000)
        self.assertEqual(message.additional_kwargs["framework_runtime_delta"], "+ changed")

    def test_summary_cap_applies_without_runtime_settings(self):
        result = c.apply_compression(user_id="alice", session_id="s", messages=self.messages,
            history_token_budget=6000, checkpoint_store_path=self.path, preserve_recent=4,
            summarizer=lambda *a: "中" * 4000)
        self.assertTrue(result.triggered)
        self.assertLessEqual(c._approx_tokens(result.view[0].content), result.metadata["target_tokens"] * 4 // 5)

    def test_mechanical_fallback_retains_latest_decision_within_char_cap(self):
        segment = [HumanMessage(content='old ' + 'x' * 1000) for _ in range(10)]
        segment.append(HumanMessage(content='newest decision: KEEP THIS'))
        summary = c._mechanical_summarizer('previous ' + 'y' * 2000, segment, 800)
        self.assertLessEqual(len(summary), 800)
        self.assertIn('KEEP THIS', summary)

    def test_small_summary_token_cap_is_still_enforced(self):
        self.assertLessEqual(c._approx_tokens(c._cap_summary_tokens('中' * 100, 3)), 3)

    def test_manual_compression_folds_all_eligible_history_with_a_large_window(self):
        result = c.apply_compression(user_id='alice', session_id='s', messages=self.messages,
            history_token_budget=800000, checkpoint_store_path=self.path, force=True,
            settings=ContextSettings(preserve_recent_turns=2, summary_tokens=256), summarizer=lambda *a: 'key decisions')
        self.assertTrue(result.triggered)
        self.assertEqual(result.compacted_until, len(self.messages) - 8)
        self.assertEqual(result.metadata['strategy'], 'manual_all_eligible')
        self.assertEqual(result.view[-8:], self.messages[-8:])
