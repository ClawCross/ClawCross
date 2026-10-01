import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'backend'))
from langchain_core.messages import HumanMessage
from webot.engine.background_compaction import BackgroundCompressionManager
from webot.runtime_settings import ContextSettings
from webot.compression import CompressionResult


class BackgroundCompactionTests(unittest.IsolatedAsyncioTestCase):
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
