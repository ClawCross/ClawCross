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
from webot.runtime_settings import RuntimeSettings, ApprovalSettings


class CommanderTests(unittest.TestCase):
    def setUp(self):
        # Command execution tests use Bypass; review behavior is tested separately.
        patcher = patch("webot.runtime.effective_session_mode", return_value="bypass")
        patcher.start()
        self.addCleanup(patcher.stop)
        broker = patch("webot.approval_review.effective_session_mode", return_value="bypass")
        broker.start()
        self.addCleanup(broker.stop)
        # Lifecycle tests exercise the host runner explicitly; the production
        # default is SRT and has separate sandbox tests.
        options = patch("webot.runtime_settings.get_runtime_settings", return_value=RuntimeSettings(
            approval=ApprovalSettings(command_sandbox="off")))
        options.start()
        self.addCleanup(options.stop)

    async def _wait_for_not_running(self, job_id: str, *, username: str = "alice", session_id: str = "", attempts: int = 20) -> str:
        status = ""
        for _ in range(attempts):
            status = await commander.background_command_io(
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

    def test_python_code_returns_partial_output_on_timeout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            code = "import time\nprint('ready', flush=True)\ntime.sleep(2)\n"
            with patch.object(commander, "resolve_session_workspace", return_value=workspace):
                result = asyncio.run(
                    commander.run_command(
                        "alice",
                        code,
                        language="python",
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
                start = await commander.run_command("alice", command, mode="background")
                job_id = start.split("job_id: ", 1)[1].splitlines()[0].strip()
                status = await commander.background_command_io(job_id)
                if "状态: running" in status:
                    await asyncio.sleep(0.2)
                    status = await commander.background_command_io(job_id)
                output = await commander.background_command_io(job_id)
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
                start = await commander.run_command("alice", command, mode="background", session_id="sess1")
                job_id = start.split("job_id: ", 1)[1].splitlines()[0].strip()
                await asyncio.sleep(0.2)
                commander._BACKGROUND_JOBS.clear()
                status = await commander.background_command_io(job_id, username="alice", session_id="sess1")
                output = await commander.background_command_io(job_id, username="alice", session_id="sess1")
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
                start = await commander.run_command("alice", command, mode="background", session_id="sess-detached")
                job_id = start.split("job_id: ", 1)[1].splitlines()[0].strip()
                commander._BACKGROUND_JOBS.clear()
                status = await self._wait_for_not_running(job_id, session_id="sess-detached")
                output = await commander.background_command_io(
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
                start = await commander.run_command("alice", command, mode="background", session_id="sess-cancel")
                job_id = start.split("job_id: ", 1)[1].splitlines()[0].strip()
                await asyncio.sleep(0.3)
                cancelled = await commander.cancel_background_command(
                    job_id,
                    username="alice",
                    session_id="sess-cancel",
                )
                commander._BACKGROUND_JOBS.clear()
                status = await commander.background_command_io(
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


    def test_concurrent_python_runs_do_not_share_a_script_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")

            async def _exercise():
                return await asyncio.gather(*(
                    commander.run_command("alice", f"import time; time.sleep(0.2); print('run-{i}')", language="python")
                    for i in range(4)
                ))

            with patch.object(commander, "resolve_session_workspace", return_value=workspace):
                results = asyncio.run(_exercise())
            for i, result in enumerate(results):
                self.assertIn(f"run-{i}", result)
            self.assertEqual(list((root / ".mcp_jobs").glob("py_*.py")), [])

    def test_clean_terminal_text_keeps_what_the_screen_shows(self):
        raw = "\x1b[?2004h\x1b=>>> \x1b[32mok\x1b[0m\r\nprogress 10%\rprogress 100%\r\n"
        self.assertEqual(commander._clean_terminal_text(raw), ">>> ok\nprogress 100%\n")


def _job_id(start: str) -> str:
    return start.split("job_id: ", 1)[1].splitlines()[0].strip()


@unittest.skipIf(sys.platform.startswith("win"), "interactive jobs need a POSIX pseudo-terminal")
class InteractiveCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for target in ("webot.runtime.effective_session_mode", "webot.approval_review.effective_session_mode"):
            mode = patch(target, return_value="bypass")
            mode.start()
            self.addCleanup(mode.stop)
        options = patch("webot.runtime_settings.get_runtime_settings", return_value=RuntimeSettings(
            approval=ApprovalSettings(command_sandbox="off")))
        options.start()
        self.addCleanup(options.stop)
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
        patcher = patch.object(commander, "resolve_session_workspace", return_value=workspace)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    async def _status(self, job_id: str) -> str:
        for _ in range(30):
            out = await commander.background_command_io(job_id, username="alice")
            if "状态: running" not in out:
                return out
            await asyncio.sleep(0.1)
        return out

    async def test_python_repl_round_trip(self):
        start = await commander.run_command("alice", "import math", language="python", mode="interactive")
        self.assertIn(">>>", start)
        job_id = _job_id(start)
        out = await commander.background_command_io(job_id, username="alice", input="print(math.sqrt(1764))")
        self.assertIn("42.0", out)
        self.assertNotIn("\x1b", out)
        await commander.background_command_io(job_id, username="alice", input="exit()", wait_seconds=1)
        self.assertIn("状态: completed", await self._status(job_id))

    async def test_shell_session_interrupt_and_guarded_input(self):
        job_id = _job_id(await commander.run_command("alice", "sh", mode="interactive"))
        self.addAsyncCleanup(commander.cancel_background_command, job_id, username="alice")
        out = await commander.background_command_io(job_id, username="alice", input="echo from-$((2+3))")
        self.assertIn("from-5", out)
        await commander.background_command_io(job_id, username="alice", input="sleep 30", wait_seconds=0)
        await commander.background_command_io(job_id, username="alice", input="\x03", enter=False, wait_seconds=1)
        out = await commander.background_command_io(job_id, username="alice", input="echo after-interrupt")
        self.assertIn("after-interrupt", out)
        # What is typed into the shell goes through the same checks as run_command.
        blocked = await commander.background_command_io(job_id, username="alice", input="rm -rf /")
        self.assertIn("安全策略阻止", blocked)

    async def test_cancel_ends_the_session_and_removes_its_input_pipe(self):
        job_id = _job_id(await commander.run_command("alice", "sh", mode="interactive"))
        job = commander._resolve_background_job(job_id, username="alice")
        cancelled = await commander.cancel_background_command(job_id, username="alice")
        self.assertIn("已取消", cancelled)
        self.assertFalse(Path(job.stdin_path).exists())
        refused = await commander.background_command_io(job_id, username="alice", input="echo hi")
        self.assertIn("已结束", refused)

    async def test_background_jobs_do_not_take_input(self):
        job_id = _job_id(await commander.run_command("alice", "sleep 5", mode="background"))
        self.addAsyncCleanup(commander.cancel_background_command, job_id, username="alice")
        refused = await commander.background_command_io(job_id, username="alice", input="hello")
        self.assertIn("不是交互任务", refused)


if __name__ == "__main__":
    unittest.main()
