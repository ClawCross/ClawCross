import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "backend"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import webot.mcp.scheduler as scheduler_mcp  # noqa: E402


class _FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeAsyncClient:
    def __init__(self, *, get_response=None, delete_response=None):
        self.get_response = get_response or _FakeResponse([])
        self.delete_response = delete_response or _FakeResponse({"status": "deleted"})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, *_args, **_kwargs):
        return self.get_response

    async def delete(self, *_args, **_kwargs):
        return self.delete_response


class TestSchedulerMcp(unittest.IsolatedAsyncioTestCase):
    async def test_team_alarm_update_preserves_id_and_rejects_scope_changes(self):
        from scheduler import service
        from types import SimpleNamespace
        from fastapi import HTTPException
        tasks={'t1':{'user_id':'alice','team':'Project','agent':'worker','text':'Old','cron':'0 9 * * *','schedule_type':'cron','run_at':'','created_at':'original'}}
        store=SimpleNamespace(get=lambda owner,aid:SimpleNamespace(agent_id='worker',name='Worker') if (owner,aid)==('alice','worker') else None)
        with mock.patch.object(service,'load_tasks',return_value=tasks), \
             mock.patch.object(service,'save_tasks') as save, \
             mock.patch.object(service,'get_store',return_value=store), \
             mock.patch.object(service,'_add_alarm_job') as schedule, \
             mock.patch.object(service.scheduler,'get_job',return_value=None):
            output=await service.update_task('t1',service.CronTask(user_id='alice',team='Project',agent='worker',text='New',cron='0 10 * * *'))
            self.assertEqual(output['task_id'],'t1')
            self.assertEqual(save.call_args.args[0]['t1']['created_at'],'original')
            self.assertEqual(schedule.call_args.args[0],'t1')
            save.reset_mock();schedule.reset_mock()
            for patch in ({'user_id':'bob'},{'team':'Other'},{'cron':'bad'}):
                with self.assertRaises(HTTPException):
                    await service.update_task('t1',service.CronTask(**{'user_id':'alice','team':'Project','agent':'worker','text':'Bad','cron':'0 10 * * *',**patch}))
            save.assert_not_called();schedule.assert_not_called()

    async def test_list_alarms_reports_scheduler_http_error(self):
        fake = _FakeAsyncClient(
            get_response=_FakeResponse({"detail": "boom"}, status_code=500, text='{"detail":"boom"}')
        )
        with mock.patch("webot.mcp.scheduler.httpx.AsyncClient", return_value=fake):
            result = await scheduler_mcp.list_alarms("alice")

        self.assertIn("读取列表失败", result)
        self.assertIn("HTTP 500", result)
        self.assertIn("boom", result)

    async def test_delete_alarm_reports_query_http_error(self):
        fake = _FakeAsyncClient(
            get_response=_FakeResponse(ValueError("not json"), status_code=502, text="<html>bad gateway</html>")
        )
        with mock.patch("webot.mcp.scheduler.httpx.AsyncClient", return_value=fake):
            result = await scheduler_mcp.delete_alarm("alice", "task-1")

        self.assertIn("删除前查询失败", result)
        self.assertIn("HTTP 502", result)
        self.assertIn("bad gateway", result)

    async def test_delete_alarm_checks_owner_then_deletes(self):
        fake = _FakeAsyncClient(
            get_response=_FakeResponse([{"task_id": "task-1", "user_id": "alice", "agent": "my-agent"}]),
            delete_response=_FakeResponse({"status": "deleted"}),
        )
        with mock.patch("webot.mcp.scheduler.httpx.AsyncClient", return_value=fake):
            result = await scheduler_mcp.delete_alarm("alice", "task-1", session_id="my-agent")

        self.assertIn("已成功删除", result)
