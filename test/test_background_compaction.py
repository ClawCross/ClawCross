import asyncio
import sys
import unittest
import threading
import tempfile
from unittest.mock import AsyncMock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'backend'))
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from webot.engine.background_compaction import BackgroundCompressionManager
from webot.runtime_settings import ContextSettings
from webot.compression import CompressionResult
from webot.checkpoint_repository import get_context_compaction


class BackgroundCompactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_tool_turn_compacts_to_a_small_target_without_waiting_for_overflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = BackgroundCompressionManager(str(Path(tmp) / 'context.db'))
            messages = [HumanMessage(content='inspect files')]
            for i in range(40):
                messages.extend([AIMessage(content='', tool_calls=[{'name':'read_file','args':{},'id':str(i)}]),
                                 ToolMessage(content='中' * 1000, tool_call_id=str(i))])
            with patch('webot.engine.background_compaction.make_llm_summarizer', return_value=lambda *a: 'key evidence'), \
                    patch('webot.engine.background_compaction.fetch_thread_message_count', AsyncMock(return_value=len(messages))):
                record = await manager.prepare_for_model(user_id='alice', session_id='s', messages=messages,
                    history_token_budget=1_000_000, preserve_recent=8,
                    settings=ContextSettings(trigger_tokens=20_000), prefix_tokens=100,
                    output_reserve=500, context_window=1_000_000)
                self.assertIsNone(record)
                await manager._tasks['alice#s']
            record = get_context_compaction(manager.checkpoint_store_path, 'alice#s')
            self.assertEqual(record.metadata['target_tokens'], 10_000)
            self.assertLessEqual(record.metadata['after_tokens'], 10_000)
            self.assertTrue(record.metadata['retention_limited_by_budget'])
            await manager.close()

    async def test_critical_compaction_failure_is_propagated(self):
        manager = BackgroundCompressionManager('/tmp/unused-context.db')
        with patch('webot.engine.background_compaction.get_context_compaction', return_value=None), \
                patch.object(manager, '_prepare_and_commit', AsyncMock(side_effect=RuntimeError('disk unavailable'))):
            with self.assertRaisesRegex(RuntimeError, 'disk unavailable'):
                await manager.prepare_for_model(user_id='alice', session_id='s', messages=[HumanMessage(content='中' * 10000)],
                    history_token_budget=7000, preserve_recent=8, settings=ContextSettings(),
                    prefix_tokens=100, output_reserve=500, context_window=10000)
        self.assertEqual(manager.status('alice#s')['state'], 'failed')
        await manager.close()

    async def test_cancelled_waiter_does_not_cancel_background_job(self):
        manager = BackgroundCompressionManager('/tmp/unused-context.db')
        started, gate = asyncio.Event(), asyncio.Event()
        async def prepare(**kwargs):
            started.set()
            await gate.wait()
        with patch('webot.engine.background_compaction.get_context_compaction', return_value=None), \
                patch.object(manager, '_prepare_and_commit', side_effect=prepare):
            waiter = asyncio.create_task(manager.prepare_for_model(user_id='alice', session_id='s',
                messages=[HumanMessage(content='中' * 10000)], history_token_budget=7000,
                preserve_recent=8, settings=ContextSettings(), prefix_tokens=100,
                output_reserve=500, context_window=10000))
            await started.wait()
            job = manager._tasks['alice#s']
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            self.assertFalse(job.cancelled())
            gate.set()
            await job
        await manager.close()

    async def test_long_tool_turn_waits_and_uses_committed_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = BackgroundCompressionManager(str(Path(tmp) / 'context.db'))
            messages = [HumanMessage(content='inspect files')]
            for i in range(10):
                messages.extend([
                    AIMessage(content='', tool_calls=[{'name': 'read_file', 'args': {}, 'id': str(i)}]),
                    ToolMessage(content='中' * 1000, tool_call_id=str(i)),
                ])
            with patch('webot.engine.background_compaction.make_llm_summarizer', return_value=lambda *a: 'key evidence') as factory, \
                    patch('webot.engine.background_compaction.run_tool_policy_hooks'), \
                    patch('webot.engine.background_compaction.fetch_thread_message_count', AsyncMock(return_value=len(messages))):
                record = await manager.prepare_for_model(user_id='alice', session_id='s', messages=messages,
                    history_token_budget=7000, preserve_recent=8, settings=ContextSettings(summary_tokens=256),
                    prefix_tokens=100, output_reserve=500, context_window=10000, model='session-model')
            self.assertIsNotNone(record)
            self.assertGreater(record.compacted_until, 0)
            self.assertEqual(record.metadata['strategy'], 'emergency_tool_boundary')
            self.assertEqual(record, get_context_compaction(manager.checkpoint_store_path, 'alice#s'))
            self.assertEqual(factory.call_args.kwargs['model'], 'session-model')
            self.assertEqual(manager.status('alice#s')['state'], 'completed')
            await manager.close()

    async def test_oversized_latest_tool_result_stops_before_another_model_call(self):
        manager = BackgroundCompressionManager('/tmp/unused-context.db')
        messages = [HumanMessage(content='inspect'),
            AIMessage(content='', tool_calls=[{'name': 'read_file', 'args': {}, 'id': 'call'}]),
            ToolMessage(content='中' * 12000, tool_call_id='call')]
        with patch('webot.engine.background_compaction.get_context_compaction', return_value=None), \
                patch.object(manager, '_prepare_and_commit', AsyncMock()) as prepare:
            with self.assertRaisesRegex(RuntimeError, '仍超过安全窗口'):
                await manager.prepare_for_model(user_id='alice', session_id='s', messages=messages,
                    history_token_budget=7000, preserve_recent=8, settings=ContextSettings(),
                    prefix_tokens=100, output_reserve=500, context_window=10000)
            self.assertEqual(prepare.await_count, 2)
        await manager.close()

    async def test_disabled_auto_does_not_start_or_wait(self):
        manager = BackgroundCompressionManager('/tmp/unused-context.db')
        with patch('webot.engine.background_compaction.get_context_compaction', return_value=None), \
                patch.object(manager, 'schedule') as schedule:
            await manager.prepare_for_model(user_id='alice', session_id='s', messages=[HumanMessage(content='中' * 10000)],
                history_token_budget=7000, preserve_recent=8, settings=ContextSettings(auto_compact=False),
                prefix_tokens=100, output_reserve=500, context_window=10000)
        schedule.assert_not_called()

    async def test_early_job_does_not_block_model_call_and_refreshes_next_call(self):
        manager = BackgroundCompressionManager('/tmp/unused-context.db')
        gate = asyncio.Event()
        async def prepare(**kwargs):
            await gate.wait()
        record = object()
        with patch('webot.engine.background_compaction.get_context_compaction', return_value=None), \
                patch('webot.engine.background_compaction.compression_view_from_record', side_effect=lambda r, m: m), \
                patch.object(manager, '_prepare_and_commit', side_effect=prepare):
            result = await manager.prepare_for_model(user_id='alice', session_id='s', messages=[HumanMessage(content='中' * 5500)],
                history_token_budget=7000, preserve_recent=8, settings=ContextSettings(),
                prefix_tokens=100, output_reserve=500, context_window=10000)
            self.assertIsNone(result)
            task = manager._tasks['alice#s']
            self.assertFalse(task.done())
            gate.set()
            await task
            with patch('webot.engine.background_compaction.get_context_compaction', return_value=record):
                result = await manager.prepare_for_model(user_id='alice', session_id='s', messages=[HumanMessage(content='short')],
                    history_token_budget=7000, preserve_recent=8, settings=ContextSettings(),
                    prefix_tokens=100, output_reserve=500, context_window=10000)
            self.assertIs(result, record)
        await manager.close()

    async def test_automatic_progress_is_visible_until_completion(self):
        manager = BackgroundCompressionManager('/tmp/unused-context.db')
        gate = threading.Event()
        result = CompressionResult([], True, 'summary', 4, 'prepared', 10, source_message_count=8)
        def prepare(**kwargs):
            kwargs['before_summary']()
            gate.wait(5)
            return result
        with patch('webot.engine.background_compaction.make_llm_summarizer', return_value=None), \
                patch('webot.engine.background_compaction.run_tool_policy_hooks'), \
                patch('webot.engine.background_compaction.apply_compression', side_effect=prepare), \
                patch('webot.engine.background_compaction.fetch_thread_message_count', AsyncMock(return_value=8)), \
                patch('webot.engine.background_compaction.commit_prepared_compression'):
            manager.schedule(user_id='alice', session_id='s', messages=[HumanMessage(content='old')],
                history_token_budget=6000, preserve_recent=8, settings=ContextSettings())
            self.assertEqual(manager.status('alice#s')['state'], 'checking')
            task = manager._tasks['alice#s']
            try:
                for _ in range(100):
                    if manager.status('alice#s')['state'] == 'running':
                        break
                    await asyncio.sleep(.01)
                status = manager.status('alice#s')
                self.assertEqual(status['state'], 'running')
                self.assertEqual(status['kind'], 'automatic')
                self.assertIn('elapsed_seconds', status)
            finally:
                gate.set()
            await task
        self.assertEqual(manager.status('alice#s')['state'], 'completed')
        await manager.invalidate('alice#s')
        self.assertEqual(manager.status('alice#s')['state'], 'idle')

    async def test_scheduled_input_is_frozen_before_the_worker_runs(self):
        manager = BackgroundCompressionManager('/tmp/unused-context.db')
        prepared = AsyncMock()
        source = HumanMessage(content='original', additional_kwargs={'framework_runtime_delta': 'state'})
        with patch.object(manager, '_prepare_and_commit', prepared):
            manager.schedule(user_id='alice', session_id='s', messages=[source],
                history_token_budget=6000, preserve_recent=8, settings=ContextSettings())
            task = manager._tasks['alice#s']
            source.content = 'changed later'
            source.additional_kwargs['framework_runtime_delta'] = 'changed state'
            await task
        snapshot = prepared.call_args.kwargs['messages'][0]
        self.assertEqual(snapshot.content, 'original')
        self.assertEqual(snapshot.additional_kwargs['framework_runtime_delta'], 'state')
        await manager.close()

    async def test_reset_generation_drops_a_prepared_summary(self):
        manager = BackgroundCompressionManager('/tmp/unused-context.db')
        result = CompressionResult([], True, 'summary', 4, 'prepared', 10, source_message_count=8)
        manager._generation['alice#s'] = 1
        with patch('webot.engine.background_compaction.make_llm_summarizer', return_value=None), \
                patch('webot.engine.background_compaction.apply_compression', return_value=result), \
                patch('webot.engine.background_compaction.commit_prepared_compression') as commit:
            await manager._prepare_and_commit(thread_id='alice#s', user_id='alice', session_id='s',
                messages=[HumanMessage(content='old')], history_token_budget=6000, preserve_recent=8,
                settings=ContextSettings(), measured_input_tokens=0, measured_budget=0, generation=0)
        commit.assert_not_called()
