import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import mcp_servers.scheduler as scheduler_mcp  # noqa: E402


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
    async def test_list_alarms_reports_scheduler_http_error(self):
        fake = _FakeAsyncClient(
            get_response=_FakeResponse({"detail": "boom"}, status_code=500, text='{"detail":"boom"}')
        )
        with mock.patch("mcp_servers.scheduler.httpx.AsyncClient", return_value=fake):
            result = await scheduler_mcp.list_alarms("alice")

        self.assertIn("读取列表失败", result)
        self.assertIn("HTTP 500", result)
        self.assertIn("boom", result)

    async def test_delete_alarm_reports_query_http_error(self):
        fake = _FakeAsyncClient(
            get_response=_FakeResponse(ValueError("not json"), status_code=502, text="<html>bad gateway</html>")
        )
        with mock.patch("mcp_servers.scheduler.httpx.AsyncClient", return_value=fake):
            result = await scheduler_mcp.delete_alarm("alice", "task-1")

        self.assertIn("删除前查询失败", result)
        self.assertIn("HTTP 502", result)
        self.assertIn("bad gateway", result)

    async def test_delete_alarm_checks_owner_then_deletes(self):
        fake = _FakeAsyncClient(
            get_response=_FakeResponse([{"task_id": "task-1", "user_id": "alice"}]),
            delete_response=_FakeResponse({"status": "deleted"}),
        )
        with mock.patch("mcp_servers.scheduler.httpx.AsyncClient", return_value=fake):
            result = await scheduler_mcp.delete_alarm("alice", "task-1")

        self.assertIn("已成功删除", result)
