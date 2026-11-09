import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import mcp_servers.commander as commander
from webot.workspace import SessionWorkspace


class CommanderTests(unittest.TestCase):
    async def _wait_for_not_running(self, job_id: str, *, username: str = "alice", session_id: str = "", attempts: int = 20) -> str:
        status = ""
        for _ in range(attempts):
            status = await commander.get_background_command_status(
                job_id,
                username=username,
                session_id=session_id,
            )
            if "状态: running" not in status:
                return status
            await asyncio.sleep(0.1)
        return status

    def test_run_command_truncates_large_stream_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            command = f'{sys.executable} -c "print(\'x\' * 2000)"'
            with patch.object(commander, "resolve_session_workspace", return_value=workspace), patch.object(
                commander, "ALLOWED_COMMANDS", {Path(sys.executable).name}
            ):
                result = asyncio.run(
                    commander.run_command(
                        "alice",
                        command,
                        max_output_chars=300,
                    )
                )
            self.assertIn("命令执行成功", result)
            self.assertIn("已截断", result)

    def test_run_python_code_returns_partial_output_on_timeout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            code = "import time\nprint('ready', flush=True)\ntime.sleep(2)\n"
            with patch.object(commander, "resolve_session_workspace", return_value=workspace):
                result = asyncio.run(
                    commander.run_python_code(
                        "alice",
                        code,
                        timeout_seconds=1,
                    )
                )
            self.assertIn("执行超时", result)
            self.assertIn("ready", result)

    def test_background_command_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            command = f'{sys.executable} -c "print(\'bg-ready\')"'

            async def _exercise() -> tuple[str, str, str]:
                start = await commander.start_background_command("alice", command)
                job_id = start.split("job_id: ", 1)[1].splitlines()[0].strip()
                status = await commander.get_background_command_status(job_id)
                if "状态: running" in status:
                    await asyncio.sleep(0.2)
                    status = await commander.get_background_command_status(job_id)
                output = await commander.read_background_command_output(job_id)
                return start, status, output

            with patch.object(commander, "resolve_session_workspace", return_value=workspace), patch.object(
                commander, "ALLOWED_COMMANDS", {Path(sys.executable).name}
            ):
                start, status, output = asyncio.run(_exercise())
            self.assertIn("job_id", start)
            self.assertIn("状态:", status)
            self.assertIn("bg-ready", output)

    def test_background_command_status_survives_memory_reset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            command = f'{sys.executable} -c "print(\'persisted\')"'

            async def _exercise() -> tuple[str, str]:
                start = await commander.start_background_command("alice", command, session_id="sess1")
                job_id = start.split("job_id: ", 1)[1].splitlines()[0].strip()
                await asyncio.sleep(0.2)
                commander._BACKGROUND_JOBS.clear()
                status = await commander.get_background_command_status(job_id, username="alice", session_id="sess1")
                output = await commander.read_background_command_output(job_id, username="alice", session_id="sess1")
                return status, output

            with patch.object(commander, "resolve_session_workspace", return_value=workspace), patch.object(
                commander, "ALLOWED_COMMANDS", {Path(sys.executable).name}
            ):
                status, output = asyncio.run(_exercise())
            self.assertIn("状态:", status)
            self.assertIn("persisted", output)

    def test_background_command_status_and_stdout_survive_stdio_process_end(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            command = f'{sys.executable} -c "print(\'detached-output\', flush=True)"'

            async def _exercise() -> tuple[str, str, str, Path]:
                start = await commander.start_background_command("alice", command, session_id="sess-detached")
                job_id = start.split("job_id: ", 1)[1].splitlines()[0].strip()
                commander._BACKGROUND_JOBS.clear()
                status = await self._wait_for_not_running(job_id, session_id="sess-detached")
                output = await commander.read_background_command_output(
                    job_id,
                    username="alice",
                    session_id="sess-detached",
                )
                return start, status, output, root / ".mcp_jobs" / f"{job_id}.stdout.log"

            with patch.object(commander, "resolve_session_workspace", return_value=workspace), patch.object(
                commander, "ALLOWED_COMMANDS", {Path(sys.executable).name}
            ):
                start, status, output, stdout_path = asyncio.run(_exercise())
            self.assertIn("job_id", start)
            self.assertIn("状态: completed", status)
            self.assertIn("detached-output", output)
            self.assertGreater(stdout_path.stat().st_size, 0)

    def test_cancel_background_command_persists_cancelled_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            command = f'{sys.executable} -c "import time; print(\'started\', flush=True); time.sleep(10)"'

            async def _exercise() -> tuple[str, str, str]:
                start = await commander.start_background_command("alice", command, session_id="sess-cancel")
                job_id = start.split("job_id: ", 1)[1].splitlines()[0].strip()
                await asyncio.sleep(0.3)
                cancelled = await commander.cancel_background_command(
                    job_id,
                    username="alice",
                    session_id="sess-cancel",
                )
                commander._BACKGROUND_JOBS.clear()
                status = await commander.get_background_command_status(
                    job_id,
                    username="alice",
                    session_id="sess-cancel",
                )
                return start, cancelled, status

            with patch.object(commander, "resolve_session_workspace", return_value=workspace), patch.object(
                commander, "ALLOWED_COMMANDS", {Path(sys.executable).name}
            ):
                start, cancelled, status = asyncio.run(_exercise())
            self.assertIn("job_id", start)
            self.assertIn("已取消", cancelled)
            self.assertIn("状态: cancelled", status)


if __name__ == "__main__":
    unittest.main()
