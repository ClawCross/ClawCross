import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from utils.external_agent_history import (
    ExternalAgentHistoryStore,
    HistoryContext,
    attach_history_context,
    history_db_name_for,
    history_db_path_for,
    history_options_disabled,
    iter_history_db_paths,
)


class HelperTests(unittest.TestCase):
    def test_db_name_sanitizes_unsafe_chars(self):
        name = history_db_name_for("openclaw", "user/abc:42")
        self.assertEqual(name, "openclaw#user_abc_42.db")

    def test_db_name_falls_back_when_session_missing(self):
        name = history_db_name_for("claude", None)
        self.assertEqual(name, "claude#__default__.db")

    def test_attach_history_context_preserves_inner_values(self):
        opts = {"_history_user_id": "outer"}
        merged = attach_history_context(opts, user_id="inner", group_id="g1")
        self.assertEqual(merged["_history_user_id"], "outer")
        self.assertEqual(merged["_history_group_id"], "g1")

    def test_history_options_disabled_flag(self):
        self.assertTrue(history_options_disabled({"_history_disabled": True}))
        self.assertFalse(history_options_disabled({}))
        self.assertFalse(history_options_disabled(None))

    def test_history_context_from_options(self):
        ctx = HistoryContext.from_options(
            {
                "_history_user_id": "u1",
                "_history_group_id": "g1",
                "_history_global_name": "agent_one",
            }
        )
        self.assertEqual(ctx.user_id, "u1")
        self.assertEqual(ctx.group_id, "g1")
        self.assertEqual(ctx.global_name, "agent_one")


class StoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_record_send_recv_roundtrip(self):
        with TemporaryDirectory() as tmp:
            store = ExternalAgentHistoryStore(tmp)
            options = {"_history_user_id": "alice", "_history_group_id": "g42"}
            rid = await store.record_send(
                platform="claude",
                session_key="sess1",
                connect_type="acp",
                prompt="hello world",
                options=options,
            )
            self.assertTrue(rid)

            await store.record_recv(
                platform="claude",
                session_key="sess1",
                connect_type="acp",
                request_id=rid,
                ok=True,
                content="hi back",
                raw_response={"messages": [{"role": "assistant"}]},
                error=None,
                options=options,
            )

            messages = await store.list_messages(
                platform="claude", session_key="sess1"
            )
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0]["direction"], "send")
            self.assertEqual(messages[0]["content"], "hello world")
            self.assertEqual(messages[0]["user_id"], "alice")
            self.assertEqual(messages[1]["direction"], "recv")
            self.assertEqual(messages[1]["content"], "hi back")

            meta = await store.get_session_meta(
                platform="claude", session_key="sess1"
            )
            self.assertIsNotNone(meta)
            self.assertEqual(meta["user_id"], "alice")
            self.assertEqual(meta["group_id"], "g42")
            self.assertEqual(meta["connect_type"], "acp")

    async def test_disabled_flag_skips_recording(self):
        with TemporaryDirectory() as tmp:
            store = ExternalAgentHistoryStore(tmp)
            rid = await store.record_send(
                platform="claude",
                session_key="off",
                connect_type="acp",
                prompt="should not persist",
                options={"_history_disabled": True},
            )
            self.assertTrue(rid)
            messages = await store.list_messages(
                platform="claude", session_key="off"
            )
            self.assertEqual(messages, [])

    async def test_separate_files_per_platform_and_session(self):
        with TemporaryDirectory() as tmp:
            store = ExternalAgentHistoryStore(tmp)
            await store.record_send(
                platform="claude",
                session_key="A",
                connect_type="acp",
                prompt="a1",
                options=None,
            )
            await store.record_send(
                platform="claude",
                session_key="B",
                connect_type="acp",
                prompt="b1",
                options=None,
            )
            await store.record_send(
                platform="codex",
                session_key="A",
                connect_type="acp",
                prompt="c1",
                options=None,
            )
            paths = iter_history_db_paths(tmp)
            self.assertEqual(len(paths), 3)
            names = sorted(p.name for p in paths)
            self.assertEqual(
                names,
                ["claude#A.db", "claude#B.db", "codex#A.db"],
            )

    async def test_error_recording(self):
        with TemporaryDirectory() as tmp:
            store = ExternalAgentHistoryStore(tmp)
            rid = await store.record_send(
                platform="openclaw",
                session_key="s1",
                connect_type="http",
                prompt="hi",
                options=None,
            )
            await store.record_recv(
                platform="openclaw",
                session_key="s1",
                connect_type="http",
                request_id=rid,
                ok=False,
                content=None,
                raw_response=None,
                error="missing api_url",
                options=None,
            )
            messages = await store.list_messages(
                platform="openclaw", session_key="s1"
            )
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[1]["direction"], "error")
            self.assertEqual(messages[1]["content"], "missing api_url")
            self.assertEqual(messages[1]["meta"]["ok"], False)

    async def test_record_recv_with_dict_raw_response_inlines_tools(self):
        """Validate that tool_uses/tool_results in raw_response (the
        return_trace=True path) get recorded automatically by record_recv."""
        with TemporaryDirectory() as tmp:
            store = ExternalAgentHistoryStore(tmp)
            rid = await store.record_send(
                platform="claude",
                session_key="recv-tools",
                connect_type="acp",
                prompt="run ls",
                options=None,
            )
            await store.record_recv(
                platform="claude",
                session_key="recv-tools",
                connect_type="acp",
                request_id=rid,
                ok=True,
                content="here you go",
                raw_response={
                    "messages": [{"role": "assistant", "content": "here you go"}],
                    "tool_uses": [{"name": "ls", "args": {"path": "/"}}],
                    "tool_results": [{"name": "ls", "output": "a b c"}],
                },
                error=None,
                options=None,
            )
            messages = await store.list_messages(
                platform="claude", session_key="recv-tools"
            )
            directions = [m["direction"] for m in messages]
            self.assertEqual(directions, ["send", "recv", "tool_call", "tool_result"])
            tool_call_row = messages[2]
            self.assertEqual(tool_call_row["meta"]["tool_name"], "ls")

    async def test_acpx_trace_records_tool_calls_and_results(self):
        with TemporaryDirectory() as tmp:
            store = ExternalAgentHistoryStore(tmp)
            rid = await store.record_send(
                platform="claude",
                session_key="trace1",
                connect_type="acp",
                prompt="run tool",
                options=None,
            )
            trace = {
                "messages": [{"role": "assistant", "content": "ok"}],
                "tool_uses": [{"name": "ls", "args": {"path": "/"}}],
                "tool_results": [{"name": "ls", "output": "foo bar"}],
            }
            await store.record_acpx_trace(
                platform="claude",
                session_key="trace1",
                connect_type="acp",
                request_id=rid,
                trace=trace,
                options=None,
            )
            messages = await store.list_messages(
                platform="claude", session_key="trace1"
            )
            directions = [m["direction"] for m in messages]
            self.assertIn("tool_call", directions)
            self.assertIn("tool_result", directions)

    async def test_purge_keeps_last_n(self):
        with TemporaryDirectory() as tmp:
            store = ExternalAgentHistoryStore(tmp)
            for i in range(10):
                rid = await store.record_send(
                    platform="claude",
                    session_key="purge",
                    connect_type="acp",
                    prompt=f"p{i}",
                    options=None,
                )
                await store.record_recv(
                    platform="claude",
                    session_key="purge",
                    connect_type="acp",
                    request_id=rid,
                    ok=True,
                    content=f"r{i}",
                    raw_response=None,
                    error=None,
                    options=None,
                )
            before = await store.list_messages(
                platform="claude", session_key="purge", limit=100
            )
            self.assertEqual(len(before), 20)
            deleted = await store.purge_old_messages(
                platform="claude", session_key="purge", keep_last=5
            )
            self.assertEqual(deleted, 15)
            after = await store.list_messages(
                platform="claude", session_key="purge", limit=100
            )
            self.assertEqual(len(after), 5)

    async def test_list_sessions_aggregates_across_files(self):
        with TemporaryDirectory() as tmp:
            store = ExternalAgentHistoryStore(tmp)
            await store.record_send(
                platform="claude",
                session_key="a",
                connect_type="acp",
                prompt="x",
                options={"_history_user_id": "u1"},
            )
            await store.record_send(
                platform="codex",
                session_key="b",
                connect_type="acp",
                prompt="y",
                options={"_history_user_id": "u2"},
            )
            sessions = await store.list_sessions()
            self.assertEqual(len(sessions), 2)
            users = sorted(s["user_id"] for s in sessions)
            self.assertEqual(users, ["u1", "u2"])

    async def test_db_path_helper_creates_directory(self):
        with TemporaryDirectory() as tmp:
            sub = Path(tmp) / "history_sub"
            path = history_db_path_for("claude", "sess", sub)
            self.assertTrue(sub.is_dir())
            self.assertEqual(path.name, "claude#sess.db")


if __name__ == "__main__":
    unittest.main()
