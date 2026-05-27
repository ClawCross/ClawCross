import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

if "fastapi" not in sys.modules:
    fastapi_stub = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code=None, detail=None):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    fastapi_stub.HTTPException = HTTPException
    sys.modules["fastapi"] = fastapi_stub

if "aiosqlite" not in sys.modules:
    aiosqlite_stub = types.ModuleType("aiosqlite")

    class _UnusedAsyncConnection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    def connect(*args, **kwargs):
        return _UnusedAsyncConnection()

    aiosqlite_stub.connect = connect
    sys.modules["aiosqlite"] = aiosqlite_stub

if "pydantic" not in sys.modules:
    pydantic_stub = types.ModuleType("pydantic")

    class BaseModel:
        def __init__(self, **data):
            for key, value in data.items():
                setattr(self, key, value)

    pydantic_stub.BaseModel = BaseModel
    sys.modules["pydantic"] = pydantic_stub

if "utils.logging_utils" not in sys.modules:
    logging_utils_stub = types.ModuleType("utils.logging_utils")

    def get_logger(_name):
        import logging

        return logging.getLogger(_name)

    logging_utils_stub.get_logger = get_logger
    sys.modules["utils.logging_utils"] = logging_utils_stub

from api.session_models import DeleteSessionRequest, SessionListRequest, SessionStatusRequest
from api.session_service import SessionService


class HumanMessage:
    def __init__(self, content):
        self.content = content


class _FakeAgentApp:
    def __init__(self, snapshots: dict[str, list]):
        self._snapshots = snapshots

    async def aget_state(self, config: dict):
        thread_id = config["configurable"]["thread_id"]
        return SimpleNamespace(values={"messages": self._snapshots.get(thread_id, [])})


class _FakeAgent:
    def __init__(self, snapshots: dict[str, list], statuses: dict[str, dict] | None = None):
        self.agent_app = _FakeAgentApp(snapshots)
        self._statuses = statuses or {}
        self.cancelled: list[str] = []

    def get_all_thread_status(self, prefix: str):
        return {
            thread_id: info
            for thread_id, info in self._statuses.items()
            if thread_id.startswith(prefix)
        }

    async def cancel_task(self, task_key: str):
        self.cancelled.append(task_key)
        return True

    def list_active_task_keys(self, prefix: str):
        return [thread_id for thread_id in self._statuses if thread_id.startswith(prefix)]

    def has_pending_system_messages(self, thread_id: str) -> bool:
        return bool(self._statuses.get(thread_id, {}).get("pending_system", 0))

    def consume_pending_system_messages(self, thread_id: str) -> int:
        return int(self._statuses.get(thread_id, {}).get("pending_system", 0))

    def is_thread_busy(self, thread_id: str) -> bool:
        return bool(self._statuses.get(thread_id, {}).get("busy", False))

    def get_thread_busy_source(self, thread_id: str) -> str:
        return str(self._statuses.get(thread_id, {}).get("source", ""))

    def get_thread_context_usage(self, thread_id: str) -> dict:
        return self._statuses.get(thread_id, {}).get(
            "context_usage",
            {"tokens": 0, "budget": 0, "percent": 0, "remaining": 0},
        )

    def set_thread_context_usage(self, thread_id: str, tokens: int, budget: int) -> None:
        usage = self._statuses.setdefault(thread_id, {})
        percent = min(100, round(tokens / budget * 100)) if budget > 0 else 0
        usage["context_usage"] = {
            "tokens": tokens,
            "budget": budget,
            "percent": percent,
            "remaining": max(0, budget - tokens),
        }


class SessionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_sessions_hides_subagent_sidechains(self):
        service = SessionService(
            db_path=":memory:",
            agent=_FakeAgent(
                {
                    "alice#default": [HumanMessage(content="Main chat")],
                    "alice#subagent__research__worker1": [HumanMessage(content="Side task")],
                }
            ),
            verify_auth_or_token=lambda user_id, password, token: None,
            extract_text=lambda content: content if isinstance(content, str) else str(content),
        )

        with patch(
            "api.session_service.list_thread_ids_by_prefix",
            new=AsyncMock(return_value=["alice#default", "alice#subagent__research__worker1"]),
        ):
            result = await service.list_sessions(SessionListRequest(user_id="alice"), None)

        self.assertEqual(result["status"], "success")
        self.assertEqual([item["session_id"] for item in result["sessions"]], ["default"])

    async def test_sessions_status_hides_subagent_sidechains(self):
        service = SessionService(
            db_path=":memory:",
            agent=_FakeAgent(
                {},
                statuses={
                    "alice#default": {"busy": False, "source": "", "pending_system": 0},
                    "alice#subagent__reviewer__audit": {"busy": True, "source": "system", "pending_system": 1},
                },
            ),
            verify_auth_or_token=lambda user_id, password, token: None,
            extract_text=lambda content: content if isinstance(content, str) else str(content),
        )

        result = await service.sessions_status(SessionListRequest(user_id="alice"), None)

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["sessions"],
            [{"session_id": "default", "busy": False, "source": "", "pending_system": 0}],
        )

    async def test_session_status_includes_context_usage(self):
        service = SessionService(
            db_path=":memory:",
            agent=_FakeAgent(
                {},
                statuses={
                    "alice#default": {
                        "busy": True,
                        "source": "user",
                        "pending_system": 2,
                        "context_usage": {
                            "tokens": 64000,
                            "budget": 64000,
                            "percent": 100,
                            "remaining": 0,
                        },
                    }
                },
            ),
            verify_auth_or_token=lambda user_id, password, token: None,
            extract_text=lambda content: content if isinstance(content, str) else str(content),
        )

        result = await service.session_status(
            SessionStatusRequest(user_id="alice", session_id="default"), None
        )

        self.assertEqual(result["busy"], True)
        self.assertEqual(result["context_percent"], 100)
        self.assertEqual(result["context_remaining"], 0)
        self.assertEqual(result["context_tokens"], 64000)
        self.assertEqual(result["context_budget"], 64000)

    async def test_delete_subagent_session_also_cleans_registry_row(self):
        service = SessionService(
            db_path=":memory:",
            agent=_FakeAgent({}),
            verify_auth_or_token=lambda user_id, password, token: None,
            extract_text=lambda content: content if isinstance(content, str) else str(content),
        )

        with patch("api.session_service.delete_thread_records", new=AsyncMock()) as delete_thread_records:
            with patch("api.session_service.delete_subagent_by_session", new=Mock()) as delete_subagent_by_session:
                result = await service.delete_session(
                    DeleteSessionRequest(
                        user_id="alice",
                        session_id="subagent__research__worker1",
                    ),
                    None,
                )

        self.assertEqual(result["status"], "success")
        delete_thread_records.assert_awaited_once_with(":memory:", "alice#subagent__research__worker1")
        delete_subagent_by_session.assert_called_once_with("alice", "subagent__research__worker1")


if __name__ == "__main__":
    unittest.main()
