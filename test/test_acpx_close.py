"""The acpx adapter's session close."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src" / "backend"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from external.acpx import AcpxAdapter, AcpxError  # noqa: E402


class TestAcpxClose(unittest.IsolatedAsyncioTestCase):
    """Closing an acpx session cancels its turn first, and closes it even when that fails."""

    async def test_acpx_close_session_cancels_before_close(self):
        adapter = AcpxAdapter.__new__(AcpxAdapter)
        adapter._acpx_bin = "/usr/bin/acpx"
        adapter._cwd = str(PROJECT_ROOT)
        calls = []

        async def fake_run_json(args, **kwargs):
            calls.append((args, kwargs))
            return ""

        adapter._run_json = fake_run_json

        await adapter.close_session(
            tool="claude",
            session_key="agent:demo:clawcrosschat",
            acpx_session="agent:demo:clawcrosschat",
            timeout_sec=12,
            ttl_sec=60,
            approve_all=False,
        )

        self.assertEqual(calls[0][0], ["claude", "cancel", "-s", "agent:demo:clawcrosschat"])
        self.assertEqual(calls[1][0], ["claude", "sessions", "close", "agent:demo:clawcrosschat"])
        self.assertTrue(all(call[1]["allow_nonzero"] for call in calls))

    async def test_acpx_close_session_still_closes_when_cancel_fails(self):
        adapter = AcpxAdapter.__new__(AcpxAdapter)
        adapter._acpx_bin = "/usr/bin/acpx"
        adapter._cwd = str(PROJECT_ROOT)
        calls = []

        async def fake_run_json(args, **kwargs):
            calls.append((args, kwargs))
            if args[:2] == ["claude", "cancel"]:
                raise AcpxError("cancel timed out")
            return ""

        adapter._run_json = fake_run_json

        await adapter.close_session(
            tool="claude",
            session_key="agent:demo:clawcrosschat",
            acpx_session="agent:demo:clawcrosschat",
            timeout_sec=12,
            ttl_sec=60,
            approve_all=False,
        )

        self.assertEqual(calls[0][0], ["claude", "cancel", "-s", "agent:demo:clawcrosschat"])
        self.assertEqual(calls[1][0], ["claude", "sessions", "close", "agent:demo:clawcrosschat"])


if __name__ == "__main__":
    unittest.main()
