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

from webot.api.session_service import SessionService


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

    def set_thread_context_usage(self, thread_id: str, tokens: int, budget: int, **kwargs) -> None:
        usage = self._statuses.setdefault(thread_id, {})
        percent = min(100, round(tokens / budget * 100)) if budget > 0 else 0
        usage["context_usage"] = {
            "tokens": tokens,
            "budget": budget,
            "percent": percent,
            "remaining": max(0, budget - tokens),
            "source": kwargs.get("source", "estimate"),
            "breakdown": dict(kwargs.get("breakdown") or {}),
            "cache_read_tokens": int(kwargs.get("cache_read_tokens", 0)),
        }


def _service(agent) -> SessionService:
    return SessionService(db_path=":memory:", agent=agent,
                          extract_text=lambda content: content if isinstance(content, str) else str(content))


class SessionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_is_empty_for_subagent_sidechains(self):
        service = _service(_FakeAgent({
            "alice#default": [HumanMessage(content="Main chat")],
            "alice#subagent__research__worker1": [HumanMessage(content="Side task")],
        }))

        with patch("webot.api.session_service.fetch_thread_checkpoint_times", new=AsyncMock(return_value={})):
            main = await service.summary("alice", "default")
            side = await service.summary("alice", "subagent__research__worker1")

        self.assertEqual((main["title"], main["message_count"]), ("Main chat", 1))
        self.assertEqual(side, {})

    async def test_summary_is_empty_before_anyone_writes(self):
        self.assertEqual(await _service(_FakeAgent({})).summary("alice", "fresh"), {})

    async def test_context_usage_uses_the_configured_window(self):
        service = _service(_FakeAgent({}, statuses={"alice#default": {"context_usage": {
            "tokens": 64000, "budget": 64000, "percent": 100, "remaining": 0,
        }}}))

        with patch("webot.api.session_service.get_runtime_settings", return_value=SimpleNamespace(context=SimpleNamespace(context_window_tokens=1000000))):
            usage = await service.context_usage("alice", "default")

        self.assertEqual((usage["percent"], usage["remaining"], usage["tokens"], usage["budget"]), (6, 936000, 64000, 1000000))

    async def test_context_usage_restores_persisted_api_usage(self):
        agent = _FakeAgent({}, statuses={"alice#default": {"busy": False}})
        restored: list[str] = []

        async def restore_context_usage(thread_id: str) -> bool:
            restored.append(thread_id)
            agent.set_thread_context_usage(
                thread_id, 1050, 128000, source="api",
                breakdown={"system_prompt": 300, "messages": 700, "output": 50},
                cache_read_tokens=600,
            )
            return True

        agent.restore_context_usage = restore_context_usage
        usage = await _service(agent).context_usage("alice", "default")

        self.assertEqual(restored, ["alice#default"])
        self.assertEqual((usage["tokens"], usage["source"]), (1050, "api"))
        self.assertEqual(usage["breakdown"], {"system_prompt": 300, "messages": 700, "output": 50})
        self.assertEqual(usage["cache_read_tokens"], 600)

    async def test_messages_keep_a_users_images(self):
        image = [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "data:x"}}]
        service = _service(_FakeAgent({"alice#default": [HumanMessage(content=image)]}))

        self.assertEqual(await service.messages("alice", "default"), [{"role": "user", "content": image}])

    async def test_compact_keeps_api_usage_instead_of_estimate(self):
        agent = _FakeAgent({"alice#default": [HumanMessage(content="hi")]})
        agent.set_thread_context_usage(
            "alice#default", 1050, 128000, source="api", breakdown={"messages": 1000, "output": 50},
        )
        agent.get_thread_last_context_tokens = lambda thread_id: 1050
        agent.get_thread_model = lambda thread_id: ""
        compression = SimpleNamespace(
            triggered=True, reason="", view_tokens=300, summary="s", compacted_until=1, view=[],
        )

        with patch("webot.api.session_service.static_compression_view", return_value=[]), patch(
            "webot.api.session_service.estimate_messages_tokens", return_value=900
        ), patch("webot.api.session_service.make_llm_summarizer", return_value=None), patch(
            "webot.api.session_service.apply_compression", return_value=compression
        ):
            result = await _service(agent).compact("alice", "default")

        self.assertEqual((result["before_tokens"], result["after_tokens"]), (900, 300))
        usage = agent.get_thread_context_usage("alice#default")
        self.assertEqual((usage["tokens"], usage["source"]), (1050, "api"))

    async def test_delete_subagent_session_also_cleans_registry_row(self):
        with patch("webot.api.session_service.delete_thread_records", new=AsyncMock()) as delete_thread_records:
            with patch("webot.api.session_service.delete_subagent_by_session", new=Mock()) as delete_subagent_by_session:
                await _service(_FakeAgent({})).delete("alice", "subagent__research__worker1")

        delete_thread_records.assert_awaited_once_with(":memory:", "alice#subagent__research__worker1")
        delete_subagent_by_session.assert_called_once_with("alice", "subagent__research__worker1")


if __name__ == "__main__":
    unittest.main()
