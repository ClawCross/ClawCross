"""The Auto command sandbox must not silently execute on the host."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webot import command_sandbox
from webot.workspace import SessionWorkspace, _ensure_within
import mcp_servers.commander as commander


class CommandSandboxTests(unittest.TestCase):
    def test_container_argv_has_isolation_and_no_shell_interpolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "project"
            child.mkdir()
            code = "print('hello; $(whoami)')"
            with patch.object(command_sandbox, "_runtime_and_image", return_value=("/usr/bin/podman", "python:3.12-slim")):
                call = command_sandbox.build_container_command(
                    root=root, cwd=child, command=code, language="python",
                )
            argv = list(call.argv)
            self.assertEqual(argv[:2], ["/usr/bin/podman", "run"])
            for flag in ("--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
                         "--security-opt=no-new-privileges", "--pids-limit=128", "--memory=512m", "--cpus=1"):
                self.assertIn(flag, argv)
            self.assertEqual(argv[argv.index("--workdir") + 1], "/workspace/project")
            self.assertEqual(argv[-2:], ["-c", code])
            self.assertEqual(argv[argv.index("--entrypoint") + 1], "/usr/local/bin/python")
            self.assertEqual(argv[argv.index("--mount") + 1], f"type=bind,source={root},target=/workspace")
            self.assertNotIn("--privileged", argv)

    def test_invalid_workspace_cannot_be_mounted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            sibling = Path(directory) / "workspace-extra"
            sibling.mkdir()
            with patch.object(command_sandbox, "_runtime_and_image", return_value=("docker", "python:3.12-slim")):
                with self.assertRaises(command_sandbox.SandboxUnavailable):
                    command_sandbox.build_container_command(root=root, cwd=sibling, command="pwd", language="shell")
                with self.assertRaises(command_sandbox.SandboxUnavailable):
                    command_sandbox.build_container_command(root=root, cwd=root, command="print(1)",
                                                            language="python", script_path=sibling / "x.py")
            with self.assertRaises(ValueError):
                _ensure_within(root, sibling)

    def test_auto_python_uses_container_and_removes_script(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            options = SimpleNamespace(approval=SimpleNamespace(command_sandbox="container"))
            with patch.object(commander, "_command_safety_gate", new=AsyncMock(return_value=(None, ""))), \
                 patch.object(commander, "resolve_session_workspace", return_value=workspace), \
                 patch("webot.runtime.effective_session_mode", return_value="auto"), \
                 patch("webot.runtime_settings.get_runtime_settings", return_value=options), \
                 patch.object(command_sandbox, "_runtime_and_image", return_value=("/usr/bin/podman", "python:3.12-slim")), \
                 patch.object(commander, "_run_foreground", new=AsyncMock(return_value="container result")) as runner:
                result = asyncio.run(commander.run_command("alice", "print('hello')", language="python"))
            self.assertEqual(result, "container result")
            args, kwargs = runner.await_args
            self.assertIn("--network=none", args[0])
            self.assertIsNotNone(kwargs["container"])
            self.assertEqual(list((root / ".mcp_jobs").glob("py_*.py")), [])

    def test_missing_runtime_blocks_auto_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            options = SimpleNamespace(approval=SimpleNamespace(command_sandbox="container"))
            with patch.object(commander, "_command_safety_gate", new=AsyncMock(return_value=(None, ""))), \
                 patch.object(commander, "resolve_session_workspace", return_value=workspace), \
                 patch("webot.runtime.effective_session_mode", return_value="auto"), \
                 patch("webot.runtime_settings.get_runtime_settings", return_value=options), \
                 patch.object(command_sandbox, "_runtime_and_image", side_effect=command_sandbox.SandboxUnavailable("missing")), \
                 patch.object(commander, "_run_foreground", new=AsyncMock()) as host_run:
                result = asyncio.run(commander.run_command("alice", "print('hello')", language="python"))
            self.assertIn("missing", result)
            host_run.assert_not_awaited()

    def test_background_command_is_rejected_when_container_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            options = SimpleNamespace(approval=SimpleNamespace(command_sandbox="container"))
            with patch.object(commander, "_command_safety_gate", new=AsyncMock(return_value=(None, ""))), \
                 patch.object(commander, "resolve_session_workspace", return_value=workspace), \
                 patch("webot.runtime.effective_session_mode", return_value="auto"), \
                 patch("webot.runtime_settings.get_runtime_settings", return_value=options), \
                 patch.object(commander, "_launch_detached_background_job") as launch:
                result = asyncio.run(commander.run_command("alice", "print('hello')", language="python", mode="background"))
            self.assertIn("只支持前台", result)
            launch.assert_not_called()

    def test_runtime_image_check_never_pulls(self):
        with patch.object(command_sandbox.shutil, "which", side_effect=lambda name: "/usr/bin/podman" if name == "podman" else None), \
             patch.object(command_sandbox.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run, \
             patch.dict(command_sandbox.os.environ, {"WEBOT_SANDBOX_RUNTIME": "auto", "WEBOT_SANDBOX_IMAGE": "python:3.12-slim"}):
            self.assertEqual(command_sandbox._runtime_and_image(), ("/usr/bin/podman", "python:3.12-slim"))
        self.assertEqual(run.call_args.args[0], ["/usr/bin/podman", "image", "inspect", "python:3.12-slim"])

    def test_auto_runtime_tries_docker_when_podman_is_unready(self):
        def inspect(argv, **_kwargs):
            return SimpleNamespace(returncode=1 if argv[0].endswith("podman") else 0)

        with patch.object(command_sandbox.shutil, "which", side_effect=lambda name: "/usr/bin/" + name), \
             patch.object(command_sandbox.subprocess, "run", side_effect=inspect) as run, \
             patch.dict(command_sandbox.os.environ, {"WEBOT_SANDBOX_RUNTIME": "auto", "WEBOT_SANDBOX_IMAGE": "python:3.12-slim"}):
            self.assertEqual(command_sandbox._runtime_and_image(), ("/usr/bin/docker", "python:3.12-slim"))
        self.assertEqual(run.call_count, 2)
