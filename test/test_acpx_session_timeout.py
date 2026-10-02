"""Session startup follows the same timeout policy as external Agent prompts."""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "backend"))
from external.acpx import AcpxAdapter


class TestSessionTimeout(unittest.IsolatedAsyncioTestCase):
    def adapter(self):
        adapter = AcpxAdapter.__new__(AcpxAdapter)
        adapter._pending_initial_prompt = {}
        adapter._session_exists = AsyncMock(return_value=False)
        adapter._run_json = AsyncMock(return_value="")
        return adapter

    async def test_startup_default_and_explicit_timeouts(self):
        for options, expected in [({}, 180), ({"timeout_sec": 300}, 300),
                                  ({"timeout_sec": 5}, 5), ({"timeout_sec": None}, None)]:
            with self.subTest(options=options):
                adapter = self.adapter()
                created = await adapter.ensure_session(
                    tool="codex", session_key="test", acpx_session="test",
                    system_prompt="identity", **options)
                self.assertTrue(created)
                self.assertEqual(adapter._run_json.call_args.kwargs["timeout_sec"], expected)
                self.assertEqual(adapter.consume_initial_prompt(
                    tool="codex", acpx_session="test", prompt_text="hello")[1], True)

    async def test_both_prompt_paths_forward_startup_timeout(self):
        for method in ["prompt", "prompt_with_trace"]:
            for timeout in [300, None]:
                with self.subTest(method=method, timeout=timeout):
                    adapter = self.adapter()
                    adapter.ensure_session = AsyncMock(return_value=False)
                    adapter._send_prompt_file = AsyncMock(return_value='{"reply":"ok"}')
                    await getattr(adapter, method)(tool="codex", session_key="test",
                                                  prompt_text="hello", timeout_sec=timeout)
                    self.assertEqual(adapter.ensure_session.call_args.kwargs["timeout_sec"], timeout)
                    self.assertEqual(adapter._send_prompt_file.call_args.kwargs["timeout_sec"], timeout)


if __name__ == "__main__":
    unittest.main()
