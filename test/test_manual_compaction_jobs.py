import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'backend'))
from webot.api.session_service import SessionService


class ManualCompactionJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_summary_returns_immediately_and_reuses_running_job(self):
        service = SessionService(db_path='/tmp/unused', agent=None, extract_text=str)
        gate = asyncio.Event()
        async def slow(*args):
            await gate.wait()
            return {'triggered': True, 'saved_tokens': 100}
        with patch.object(service, 'compact', AsyncMock(side_effect=slow)) as compact:
            first = service.start_compaction('alice', 's')
            await asyncio.sleep(0)
            self.assertEqual(first['state'], 'running')
            self.assertEqual(service.start_compaction('alice', 's')['job_id'], first['job_id'])
            compact.assert_awaited_once()
            gate.set()
            await service._compaction_jobs['alice#s']['task']
            done = service.compaction_status('alice', 's')
            self.assertEqual(done['state'], 'completed')
            self.assertEqual(done['result']['saved_tokens'], 100)
            self.assertNotIn('task', done)
        await service.close()

    async def test_failure_is_visible_and_cancellation_invalidates_generation(self):
        service = SessionService(db_path='/tmp/unused', agent=None, extract_text=str)
        with patch.object(service, 'compact', AsyncMock(side_effect=ValueError('provider failed'))):
            service.start_compaction('alice', 's')
            await service._compaction_jobs['alice#s']['task']
        self.assertEqual(service.compaction_status('alice', 's')['error'], 'provider failed')
        with patch.object(service, 'compact', AsyncMock()):
            service.start_compaction('alice', 's')
            await service.cancel_compaction('alice', 's')
        self.assertEqual(service._compaction_generations['alice#s'], 1)
        self.assertEqual(service.compaction_status('alice', 's')['state'], 'missing')
        await service.close()
