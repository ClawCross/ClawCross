"""Auto command isolation must remain fail-closed and avoid host secrets."""

import asyncio
import json
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
    def test_srt_uses_exact_arguments_and_private_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "project"
            child.mkdir()
            script = child / "code.py"
            script.write_text("print('hello; $(whoami)')", encoding="utf-8")
            with patch.object(command_sandbox, "_srt_binary", return_value="/usr/bin/srt"):
                call = command_sandbox.build_srt_command(
                    root=root, cwd=child, command=script.read_text(), language="python",
                    python_executable="/opt/venv/bin/python", script_path=script,
                )
            try:
                self.assertEqual(call.argv[:2], ("/usr/bin/srt", "--settings"))
                self.assertEqual(call.argv[3:], ("--", "/opt/venv/bin/python", str(script)))
                policy = json.loads(call.settings_path.read_text(encoding="utf-8"))
                self.assertEqual(policy["network"]["allowedDomains"], [])
                self.assertEqual(policy["network"]["allowUnixSockets"], [])
                self.assertEqual(policy["filesystem"]["allowWrite"][0], str(root))
                self.assertIn(str(call.settings_path), policy["filesystem"]["denyRead"])
                self.assertFalse(call.settings_path.stat().st_mode & 0o077)
            finally:
                call.settings_path.unlink(missing_ok=True)

    def test_workspace_and_script_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            sibling = Path(directory) / "workspace-extra"
            root.mkdir()
            sibling.mkdir()
            with patch.object(command_sandbox, "_srt_binary", return_value="srt"):
                with self.assertRaises(command_sandbox.SandboxUnavailable):
                    command_sandbox.build_srt_command(root=root, cwd=sibling, command="pwd", language="shell",
                                                      python_executable=sys.executable)
                with self.assertRaises(command_sandbox.SandboxUnavailable):
                    command_sandbox.build_srt_command(root=root, cwd=root, command="print(1)", language="python",
                                                      python_executable=sys.executable, script_path=sibling / "x.py")
            with self.assertRaises(ValueError):
                _ensure_within(root, sibling)

    def test_missing_srt_blocks_auto_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            options = SimpleNamespace(approval=SimpleNamespace(command_sandbox="srt"))
            with patch.object(commander, "_command_safety_gate", new=AsyncMock(return_value=(None, ""))), \
                 patch.object(commander, "resolve_session_workspace", return_value=workspace), \
                 patch("webot.runtime.effective_session_mode", return_value="auto"), \
                 patch("webot.runtime_settings.get_runtime_settings", return_value=options), \
                 patch.object(command_sandbox, "_srt_binary", side_effect=command_sandbox.SandboxUnavailable("missing")), \
                 patch.object(commander, "_run_foreground", new=AsyncMock()) as host_run:
                result = asyncio.run(commander.run_command("alice", "print('hello')", language="python"))
            self.assertIn("missing", result)
            host_run.assert_not_awaited()
            self.assertEqual(list((root / ".mcp_jobs").glob("py_*.py")), [])

    def test_auto_python_uses_srt_and_cleans_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            options = SimpleNamespace(approval=SimpleNamespace(command_sandbox="srt"))
            with patch.object(commander, "_command_safety_gate", new=AsyncMock(return_value=(None, ""))), \
                 patch.object(commander, "resolve_session_workspace", return_value=workspace), \
                 patch("webot.runtime.effective_session_mode", return_value="auto"), \
                 patch("webot.runtime_settings.get_runtime_settings", return_value=options), \
                 patch.object(command_sandbox, "_srt_binary", return_value="/usr/bin/srt"), \
                 patch.object(commander, "_run_foreground", new=AsyncMock(return_value="srt result")) as runner:
                result = asyncio.run(commander.run_command("alice", "print('hello')", language="python"))
            self.assertEqual(result, "srt result")
            args, kwargs = runner.await_args
            self.assertEqual(args[0][0], "/usr/bin/srt")
            self.assertFalse(kwargs["sandbox"].settings_path.exists())
            self.assertEqual(list((root / ".mcp_jobs").glob("py_*.py")), [])

    def test_background_command_is_rejected_when_srt_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            options = SimpleNamespace(approval=SimpleNamespace(command_sandbox="srt"))
            with patch.object(commander, "_command_safety_gate", new=AsyncMock(return_value=(None, ""))), \
                 patch.object(commander, "resolve_session_workspace", return_value=workspace), \
                 patch("webot.runtime.effective_session_mode", return_value="auto"), \
                 patch("webot.runtime_settings.get_runtime_settings", return_value=options), \
                 patch.object(commander, "_launch_detached_background_job") as launch:
                result = asyncio.run(commander.run_command("alice", "print('hello')", language="python", mode="background"))
            self.assertIn("只支持前台", result)
            launch.assert_not_called()

    def test_linux_dependency_check_is_fail_closed(self):
        with patch.object(command_sandbox.sys, "platform", "linux"), \
             patch.object(command_sandbox.shutil, "which", side_effect=lambda name: "/usr/bin/" + name if name != "socat" else None):
            with self.assertRaisesRegex(command_sandbox.SandboxUnavailable, "socat"):
                command_sandbox._srt_binary()

    def test_old_srt_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "package"
            (package / "dist").mkdir(parents=True)
            binary = package / "dist" / "cli.js"
            binary.write_text("", encoding="utf-8")
            (package / "package.json").write_text(
                json.dumps({"name": "@anthropic-ai/sandbox-runtime", "version": "0.0.35"}), encoding="utf-8",
            )
            with patch.object(command_sandbox.sys, "platform", "linux"), \
                 patch.object(command_sandbox.shutil, "which", side_effect=lambda name: str(binary) if name == "srt" else "/usr/bin/" + name):
                with self.assertRaisesRegex(command_sandbox.SandboxUnavailable, "0.0.77"):
                    command_sandbox._srt_binary()

    def test_srt_process_does_not_inherit_host_secret_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings_file = root / "policy.json"
            settings_file.write_text("{}", encoding="utf-8")
            sandbox = command_sandbox.SrtCommand((sys.executable,), settings_file)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            code = "import os; print(os.getenv('CLAWCROSS_TEST_SECRET', 'not-present'))"
            with patch.dict(command_sandbox.os.environ, {"CLAWCROSS_TEST_SECRET": "must-not-leak"}):
                result = asyncio.run(commander._run_foreground(
                    [sys.executable, "-c", code], label="test", workspace_state=workspace,
                    username="alice", timeout_value=5, capture_limit=1000,
                    approval_note="", sandbox=sandbox,
                ))
            self.assertIn("not-present", result)
            self.assertNotIn("must-not-leak", result)
            self.assertFalse(settings_file.exists())
