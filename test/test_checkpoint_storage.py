import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypedDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from utils.checkpoint_paths import (
    checkpoint_db_path_for_thread,
    legacy_hashed_checkpoint_db_path_for_thread,
)
from utils.checkpoint_repository import (
    delete_thread_records,
    fetch_latest_checkpoint_blob,
    list_thread_ids_by_prefix,
)
from utils.routed_checkpoint_saver import ThreadRoutedAsyncSqliteSaver


class CheckpointStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_thread_routed_saver_writes_per_thread_db(self):
        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "agent_checkpoints"
            config = {"configurable": {"thread_id": "alice#agent-one", "checkpoint_ns": ""}}
            checkpoint = empty_checkpoint()

            async with ThreadRoutedAsyncSqliteSaver(checkpoint_dir) as saver:
                stored_config = await saver.aput(config, checkpoint, {}, {})
                restored = await saver.aget_tuple(config)

            self.assertEqual(stored_config["configurable"]["thread_id"], "alice#agent-one")
            self.assertIsNotNone(restored)
            self.assertEqual(restored.checkpoint["id"], checkpoint["id"])
            self.assertEqual(
                checkpoint_db_path_for_thread("alice#agent-one", checkpoint_dir).name,
                "alice#agent-one.db",
            )
            self.assertTrue(
                checkpoint_db_path_for_thread("alice#agent-one", checkpoint_dir).is_file()
            )

    async def test_thread_routed_saver_read_missing_thread_does_not_create_empty_db(self):
        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "agent_checkpoints"
            config = {"configurable": {"thread_id": "alice#missing", "checkpoint_ns": ""}}

            async with ThreadRoutedAsyncSqliteSaver(checkpoint_dir) as saver:
                restored = await saver.aget_tuple(config)

            self.assertIsNone(restored)
            self.assertFalse(checkpoint_db_path_for_thread("alice#missing", checkpoint_dir).exists())

    async def test_repository_lists_threads_across_sharded_dbs(self):
        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "agent_checkpoints"
            alpha_cfg = {"configurable": {"thread_id": "alice#alpha", "checkpoint_ns": ""}}
            beta_cfg = {"configurable": {"thread_id": "alice#beta", "checkpoint_ns": ""}}

            async with ThreadRoutedAsyncSqliteSaver(checkpoint_dir) as saver:
                await saver.aput(alpha_cfg, empty_checkpoint(), {}, {})
                await saver.aput(beta_cfg, empty_checkpoint(), {}, {})

            threads = await list_thread_ids_by_prefix(str(checkpoint_dir), "alice#")
            self.assertEqual(threads, ["alice#alpha", "alice#beta"])

            latest = await fetch_latest_checkpoint_blob(str(checkpoint_dir), "alice#alpha")
            self.assertIsNotNone(latest)

    async def test_thread_routed_saver_reads_legacy_hashed_shard_and_migrates_on_write(self):
        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "agent_checkpoints"
            config = {"configurable": {"thread_id": "alice#legacy", "checkpoint_ns": ""}}
            legacy_path = legacy_hashed_checkpoint_db_path_for_thread("alice#legacy", checkpoint_dir)
            checkpoint = empty_checkpoint()

            async with AsyncSqliteSaver.from_conn_string(str(legacy_path)) as legacy_saver:
                await legacy_saver.aput(config, checkpoint, {}, {})

            async with ThreadRoutedAsyncSqliteSaver(checkpoint_dir) as saver:
                restored = await saver.aget_tuple(config)
                self.assertIsNotNone(restored)
                await saver.aput(config, empty_checkpoint(), {}, {})

            self.assertFalse(legacy_path.exists())
            self.assertTrue(checkpoint_db_path_for_thread("alice#legacy", checkpoint_dir).exists())

    async def test_delete_thread_records_removes_empty_shard_file(self):
        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "agent_checkpoints"
            config = {"configurable": {"thread_id": "alice#cleanup", "checkpoint_ns": ""}}

            async with ThreadRoutedAsyncSqliteSaver(checkpoint_dir) as saver:
                await saver.aput(config, empty_checkpoint(), {}, {})

            shard_path = checkpoint_db_path_for_thread("alice#cleanup", checkpoint_dir)
            self.assertTrue(shard_path.is_file())

            await delete_thread_records(str(checkpoint_dir), "alice#cleanup")
            self.assertFalse(shard_path.exists())

    async def test_thread_routed_saver_supports_langgraph_runtime_channel_versions(self):
        class CounterState(TypedDict):
            count: int

        def step(state: CounterState) -> CounterState:
            return {"count": state.get("count", 0) + 1}

        graph = StateGraph(CounterState)
        graph.add_node("step", step)
        graph.add_edge(START, "step")
        graph.add_edge("step", END)

        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "agent_checkpoints"
            async with ThreadRoutedAsyncSqliteSaver(checkpoint_dir) as saver:
                app = graph.compile(checkpointer=saver)
                result = await app.ainvoke(
                    {"count": 0},
                    {"configurable": {"thread_id": "alice#graph", "checkpoint_ns": ""}},
                )

            self.assertEqual(result["count"], 1)
            self.assertTrue(checkpoint_db_path_for_thread("alice#graph", checkpoint_dir).is_file())


if __name__ == "__main__":
    unittest.main()
