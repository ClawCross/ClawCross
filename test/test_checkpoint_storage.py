import sys
import unittest
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core.lightweight_agent_runtime import AgentRecursionError, LightweightAgentRuntime
from utils.checkpoint_paths import checkpoint_db_path_for_thread
from utils.checkpoint_repository import delete_thread_records, list_thread_ids_by_prefix
from utils.context_store import ContextStore
from webot.session_search import session_search


class CheckpointStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_store_appends_messages_in_one_shard_per_thread(self):
        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "agent_checkpoints"
            async with ContextStore(checkpoint_dir) as store:
                await store.append_messages("alice#agent-one", [HumanMessage(content="hello")])
                await store.append_messages("alice#agent-one", [AIMessage(content="hi")])
                restored = await store.load_context("alice#agent-one")

            self.assertEqual([m.content for m in restored], ["hello", "hi"])
            db_path = checkpoint_db_path_for_thread("alice#agent-one", checkpoint_dir)
            self.assertTrue(db_path.is_file())
            with sqlite3.connect(db_path) as db:
                tables = {
                    row[0] for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                rows = db.execute(
                    "SELECT sequence, message_json FROM context_messages ORDER BY sequence"
                ).fetchall()
                self.assertIn("context_messages", tables)
                self.assertNotIn("agent_state", tables)
                self.assertEqual([row[0] for row in rows], [0, 1])
                self.assertEqual(json.loads(rows[0][1])["type"], "human")

    async def test_read_missing_thread_does_not_create_empty_db(self):
        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "agent_checkpoints"
            async with ContextStore(checkpoint_dir) as store:
                restored = await store.load_context("alice#missing")
            self.assertEqual(restored, [])
            self.assertFalse(checkpoint_db_path_for_thread("alice#missing", checkpoint_dir).exists())

    async def test_context_store_round_trips_multimodal_tool_content(self):
        with TemporaryDirectory() as tmpdir:
            store = ContextStore(Path(tmpdir) / "contexts")
            content = [
                {"type": "text", "text": "image metadata"},
                {"type": "image", "base64": "aGVsbG8=", "mime_type": "image/png"},
            ]
            await store.append_messages(
                "alice#vision",
                [ToolMessage(content=content, tool_call_id="image-1", name="attach_image_to_context")],
            )

            restored = await store.load_context("alice#vision")
            self.assertEqual(len(restored), 1)
            self.assertIsInstance(restored[0], ToolMessage)
            self.assertEqual(restored[0].content, content)
            self.assertEqual(restored[0].tool_call_id, "image-1")

    async def test_repository_lists_and_deletes_contexts(self):
        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "agent_checkpoints"
            async with ContextStore(checkpoint_dir) as store:
                await store.append_messages("alice#alpha", [HumanMessage(content="a")])
                await store.append_messages("alice#beta", [HumanMessage(content="b")])

            self.assertEqual(
                await list_thread_ids_by_prefix(str(checkpoint_dir), "alice#"),
                ["alice#alpha", "alice#beta"],
            )
            await delete_thread_records(str(checkpoint_dir), "alice#alpha")
            self.assertFalse(checkpoint_db_path_for_thread("alice#alpha", checkpoint_dir).exists())

    async def test_session_search_discovers_context(self):
        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "agent_checkpoints"
            async with ContextStore(checkpoint_dir) as store:
                await store.append_messages("alice#remembered", [HumanMessage(content="hello")])

            result = session_search(user_id="alice", query="", db_path=checkpoint_dir)
            self.assertIn("remembered", [item["session_id"] for item in result["matches"]])

    async def test_runtime_runs_model_tool_model_and_persists_messages(self):
        with TemporaryDirectory() as tmpdir:
            store = ContextStore(Path(tmpdir) / "states")

            async def model(state, config):
                if isinstance(state["messages"][-1], HumanMessage):
                    return {"messages": [AIMessage(
                        content="", tool_calls=[{
                            "name": "lookup", "args": {}, "id": "call-1", "type": "tool_call",
                        }],
                    )]}
                return {"messages": [AIMessage(content="done")]}

            async def tools(state, config):
                return {"messages": [ToolMessage(content="result", tool_call_id="call-1", name="lookup")]}

            runtime = LightweightAgentRuntime(
                call_model=model,
                call_tools=tools,
                should_continue=lambda state: bool(getattr(state["messages"][-1], "tool_calls", None)),
                context_store=store,
            )
            config = {"configurable": {"thread_id": "alice#loop"}, "recursion_limit": 5}
            result = await runtime.ainvoke({"messages": [HumanMessage(content="go")]}, config)
            self.assertEqual([type(m).__name__ for m in result["messages"]], [
                "HumanMessage", "AIMessage", "ToolMessage", "AIMessage",
            ])
            self.assertEqual((await runtime.aget_state(config)).values["messages"][-1].content, "done")

    async def test_runtime_enforces_step_limit(self):
        with TemporaryDirectory() as tmpdir:
            store = ContextStore(Path(tmpdir) / "states")

            async def model(state, config):
                return {"messages": [AIMessage(content="", tool_calls=[{
                    "name": "loop", "args": {}, "id": "x", "type": "tool_call",
                }])]}

            async def tools(state, config):
                return {"messages": [ToolMessage(content="again", tool_call_id="x")]}

            runtime = LightweightAgentRuntime(
                call_model=model,
                call_tools=tools,
                should_continue=lambda state: True,
                context_store=store,
            )
            with self.assertRaises(AgentRecursionError):
                await runtime.ainvoke(
                    {"messages": [HumanMessage(content="go")]},
                    {"configurable": {"thread_id": "alice#limit"}, "recursion_limit": 2},
                )

    async def test_stream_events_keep_service_compatible_node_events(self):
        with TemporaryDirectory() as tmpdir:
            store = ContextStore(Path(tmpdir) / "states")

            async def model(state, config):
                await config["callbacks"][-1].on_llm_new_token("done")
                return {"messages": [AIMessage(content="done")]}

            runtime = LightweightAgentRuntime(
                call_model=model,
                call_tools=lambda state, config: None,
                should_continue=lambda state: False,
                context_store=store,
            )
            events = [
                event async for event in runtime.astream_events(
                    {"messages": [HumanMessage(content="go")]},
                    {"configurable": {"thread_id": "alice#events"}},
                    version="v2",
                    durability="exit",
                )
            ]
            self.assertEqual(
                [(event["event"], event["name"]) for event in events],
                [
                    ("on_chain_start", "chatbot"),
                    ("on_chat_model_stream", "chat_model"),
                    ("on_chain_end", "chatbot"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
