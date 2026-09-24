import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from core.agent import TeamAgent
from core.agent_runtime_state import ThreadStateRegistry
from utils.checkpoint_repository import (
    delete_thread_records,
    get_context_usage_record,
    save_context_usage_record,
)
from webot.compression import _summary_to_message
from webot.context_usage import count_tokens, estimate_context_components, scale_components, tool_schemas


async def _read_file(path: str) -> str:
    return ""


def _bare_agent(db_path: str) -> TeamAgent:
    agent = TeamAgent.__new__(TeamAgent)
    agent._db_path = db_path
    agent._thread_state_registry = ThreadStateRegistry()
    return agent


class ContextComponentTests(unittest.TestCase):
    def test_scaled_parts_sum_to_api_total(self):
        scaled = scale_components({"system_prompt": 1, "tools": 1, "messages": 1, "summary": 0}, 100)
        self.assertEqual(sum(scaled.values()), 100)
        self.assertEqual(sorted(scaled.values()), [33, 33, 34])
        self.assertNotIn("summary", scaled)
        self.assertEqual(scale_components({"messages": 5}, 0), {})
        self.assertEqual(scale_components({"messages": 0}, 100), {})

    def test_messages_are_classified_by_role(self):
        messages = [
            _summary_to_message("早期讨论了部署方案"),
            HumanMessage(content="hello"),
            AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"path": "a.py"}, "id": "c1", "type": "tool_call"}]),
            ToolMessage(content="file body " * 50, tool_call_id="c1"),
        ]
        components = estimate_context_components(
            system_prompt="你是助手", tools=[], runtime_state="", messages=messages,
        )
        self.assertGreater(components["system_prompt"], 0)
        self.assertGreater(components["summary"], 0)
        self.assertGreater(components["messages"], 0)
        self.assertGreater(components["tool_results"], components["messages"])
        self.assertEqual((components["tools"], components["runtime_state"]), (0, 0))

    def test_special_token_text_is_counted(self):
        self.assertGreater(count_tokens("<|endoftext|> hi"), 0)

    def test_tool_schemas_skip_unconvertible_entries(self):
        tool = StructuredTool.from_function(coroutine=_read_file, name="read_file", description="Read a file")
        external = {"type": "function", "function": {"name": "ext", "description": "x", "parameters": {"type": "object", "properties": {}}}}
        schemas = tool_schemas([tool, external, object()])
        self.assertEqual([s["function"]["name"] for s in schemas], ["read_file", "ext"])


class ContextUsagePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_record_round_trip_and_delete(self):
        with TemporaryDirectory() as tmpdir:
            self.assertIsNone(get_context_usage_record(tmpdir, "alice#s1"))
            save_context_usage_record(tmpdir, "alice#s1", {"input_tokens": 10})
            self.assertEqual(get_context_usage_record(tmpdir, "alice#s1"), {"input_tokens": 10})
            await delete_thread_records(tmpdir, "alice#s1")
            self.assertIsNone(get_context_usage_record(tmpdir, "alice#s1"))

    async def test_api_usage_and_breakdown_survive_restart(self):
        with TemporaryDirectory() as tmpdir:
            agent = _bare_agent(tmpdir)
            await agent.record_context_usage(
                "alice#s1",
                input_tokens=1000,
                output_tokens=50,
                cache_read_tokens=600,
                model="deepseek-v4-flash",
                context_window=128000,
                components={"system_prompt": 30, "tools": 10, "messages": 60},
            )
            usage = agent.get_thread_context_usage("alice#s1")
            self.assertEqual(usage["source"], "api")
            self.assertEqual((usage["tokens"], usage["budget"]), (1050, 128000))
            self.assertEqual(
                usage["breakdown"],
                {"system_prompt": 300, "tools": 100, "messages": 600, "output": 50},
            )
            self.assertEqual(usage["cache_read_tokens"], 600)

            restarted = _bare_agent(tmpdir)
            self.assertEqual(restarted.get_thread_last_context_tokens("alice#s1"), 0)
            self.assertTrue(await restarted.restore_context_usage("alice#s1"))
            self.assertEqual(restarted.get_thread_context_usage("alice#s1"), usage)
            self.assertEqual(restarted.get_thread_last_context_tokens("alice#s1"), 1050)
            self.assertEqual(restarted.get_thread_model("alice#s1"), "deepseek-v4-flash")
            # Already in memory: no second database read.
            self.assertFalse(await restarted.restore_context_usage("alice#s1"))

    async def test_restore_without_record_only_reads_once(self):
        with TemporaryDirectory() as tmpdir:
            agent = _bare_agent(tmpdir)
            self.assertFalse(await agent.restore_context_usage("alice#never"))
            self.assertFalse(agent._thread_state_registry.claim_context_usage_restore("alice#never"))


if __name__ == "__main__":
    unittest.main()
