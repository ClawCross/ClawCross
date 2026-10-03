"""Auto command isolation must remain fail-closed and avoid host secrets."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "backend"))

from webot import command_sandbox
from webot.workspace import SessionWorkspace, _ensure_within
import webot.mcp.commander as commander


class CommandSandboxTests(unittest.TestCase):
    def test_unset_network_ceiling_allows_review_of_specific_public_target_only(self):
        key = 'CLAWCROSS_SANDBOX_MAX_DOMAINS'
        with patch.dict('os.environ'):
            __import__('os').environ.pop(key, None)
            self.assertEqual(command_sandbox.escalation_ceiling()['network'], ['*'])
            self.assertEqual(command_sandbox.bounded_escalation('network', 'example.com:443', Path.cwd()), 'example.com:443')
            for target in ('*', 'localhost', '127.0.0.1', '10.0.0.1', '169.254.169.254'):
                with self.subTest(target=target), self.assertRaises(command_sandbox.SandboxUnavailable):
                    command_sandbox.bounded_escalation('network', target, Path.cwd())
        for value in ('[]', 'invalid', '"*"'):
            with self.subTest(value=value), patch.dict('os.environ', {key: value}), \
                 self.assertRaises(command_sandbox.SandboxUnavailable):
                command_sandbox.bounded_escalation('network', 'example.com:443', Path.cwd())

    def test_agent_cannot_request_escalation_through_tool_schema(self):
        import inspect
        self.assertFalse(hasattr(commander, 'request_sandbox_permission'))
        self.assertNotIn('sandbox_access', inspect.signature(commander.run_command).parameters)
        self.assertNotIn('escalation_target', inspect.signature(commander.run_command).parameters)

    def test_failure_scope_respects_maximum_and_rejects_ambiguous_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'workspace'; root.mkdir()
            outside = Path(directory) / 'allowed'; outside.mkdir()
            target = outside / 'notes.txt'; target.write_text('hello')
            error = f"cat: {target}: Permission denied"
            with patch.dict('os.environ', {'CLAWCROSS_SANDBOX_MAX_READ_PATHS': '[]'}):
                with self.assertRaises(command_sandbox.SandboxUnavailable):
                    command_sandbox.permission_failure_target(error, root)
            with patch.dict('os.environ', {'CLAWCROSS_SANDBOX_MAX_READ_PATHS': json.dumps([str(outside)])}):
                self.assertEqual(command_sandbox.permission_failure_target(error, root), ('read_path', str(target)))
                self.assertIsNone(command_sandbox.permission_failure_target(f"PermissionError: '{target}'", root))
                with self.assertRaises(command_sandbox.SandboxUnavailable):
                    command_sandbox.bounded_escalation('host', '', root)

    def test_command_failure_is_reviewed_by_system_then_retried_once(self):
        from webot.approval_review import ApprovalResult
        for allowed in (True, False):
            with self.subTest(allowed=allowed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'workspace'; root.mkdir()
                target = Path(directory) / 'notes.txt'; target.write_text('hello')
                workspace = SessionWorkspace(root=root, cwd=root, mode='shared', remote='')
                options = SimpleNamespace(approval=SimpleNamespace(command_sandbox='srt'))
                calls = []
                async def run(*args, **kwargs):
                    calls.append(args)
                    kwargs['execution_report'].update(exit_code=1 if len(calls) == 1 else 0,
                        timed_out=False, stderr=f'cat: {target}: Permission denied' if len(calls) == 1 else '')
                    return 'first denied' if len(calls) == 1 else 'retry succeeded'
                with patch.dict('os.environ', {'CLAWCROSS_SANDBOX_MAX_READ_PATHS': json.dumps([str(target)])}), \
                     patch('webot.runtime_settings.get_runtime_settings', return_value=options), \
                     patch.object(commander, '_command_safety_gate', new=AsyncMock(return_value=(None, ''))), \
                     patch.object(commander, 'resolve_session_workspace', return_value=workspace), \
                     patch.object(command_sandbox, '_srt_binary', return_value='/usr/bin/srt'), \
                     patch.object(commander, '_run_foreground', side_effect=run), \
                     patch.object(commander, 'authorize_action', new=AsyncMock(return_value=ApprovalResult(allowed, '审核拒绝'))) as reviewer:
                    result = asyncio.run(commander.run_command('alice', f'cat {target}', session_id='s'))
                self.assertEqual(len(calls), 2 if allowed else 1)
                self.assertIn('retry succeeded' if allowed else '审核拒绝', result)
                self.assertEqual(reviewer.await_args.kwargs['args']['escalation_target'], str(target))
                self.assertIn('Permission denied', reviewer.await_args.kwargs['review_evidence'])

    def test_success_and_sandbox_initialization_failure_never_request_escalation(self):
        for code, stderr in ((0, 'cat: /tmp/test: Permission denied'), (1, 'apply-seccomp: write /proc/self/setgroups: Permission denied')):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workspace = SessionWorkspace(root=root, cwd=root, mode='shared', remote='')
                async def run(*args, **kwargs):
                    kwargs['execution_report'].update(exit_code=code, stderr=stderr, timed_out=False)
                    return 'result'
                options = SimpleNamespace(approval=SimpleNamespace(command_sandbox='srt'))
                with patch('webot.runtime_settings.get_runtime_settings', return_value=options), \
                     patch.object(commander, '_command_safety_gate', new=AsyncMock(return_value=(None, ''))), \
                     patch.object(commander, 'resolve_session_workspace', return_value=workspace), \
                     patch.object(command_sandbox, '_srt_binary', return_value='/usr/bin/srt'), \
                     patch.object(commander, '_run_foreground', side_effect=run) as runner, \
                     patch.object(commander, 'authorize_action', new=AsyncMock()) as reviewer:
                    self.assertEqual(asyncio.run(commander.run_command('alice', 'echo ok')), 'result')
                runner.assert_awaited_once()
                reviewer.assert_not_awaited()

    def test_root_deletion_stays_absolute_but_workspace_rm_uses_isolation(self):
        self.assertIsNotNone(commander._validate_command('rm -rf /', isolated=True))
        self.assertIsNotNone(commander._validate_command('rm -rf /', isolated=False))
        self.assertIsNone(commander._validate_command('rm obsolete.txt', isolated=True))
        self.assertIsNotNone(commander._validate_command('rm obsolete.txt', isolated=False))
    def test_initialization_failure_does_not_suggest_host_escalation(self):
        error = ('apply-seccomp: write /proc/self/setgroups '
                 '(nested userns is capability-restricted; caller must provide CAP_SYS_ADMIN): Permission denied')
        hint = command_sandbox.sandbox_failure_hint(error + '\n<sandbox_violations>')
        self.assertIn('命令尚未启动', hint)
        self.assertIn('不要为此自动申请 host', hint)
        self.assertNotIn('申请单次提权', hint)

    def test_workload_denial_retains_scoped_escalation_hint(self):
        hint = command_sandbox.sandbox_failure_hint('<sandbox_violations> denied write')
        self.assertIn('管理员权限上限', hint)
        self.assertEqual(command_sandbox.sandbox_failure_hint('ordinary command error'), '')

    def test_per_user_srt_seccomp_helper_is_readable_without_allowing_its_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            package = Path(directory) / "runtime"
            (package / "dist").mkdir(parents=True)
            helper = package / "vendor" / "seccomp"
            helper.mkdir(parents=True)
            binary = package / "dist" / "cli.js"
            binary.touch()
            with patch.object(command_sandbox.sys, "platform", "linux"):
                policy = command_sandbox._policy(root, root / "settings.json", srt_binary=str(binary))
            self.assertIn(str(helper), policy["filesystem"]["allowRead"])
            self.assertNotIn(str(package), policy["filesystem"]["allowRead"])

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
                self.assertEqual(call.argv[3:6], ("--", sys.executable, "-c"))
                self.assertEqual(call.argv[-2:], ("/opt/venv/bin/python", str(script)))
                policy = json.loads(call.settings_path.read_text(encoding="utf-8"))
                self.assertEqual(policy["network"]["allowedDomains"], [])
                self.assertEqual(policy["network"]["allowUnixSockets"], [])
                self.assertEqual(policy["filesystem"]["allowWrite"][0], str(root))
                self.assertIn(str(call.settings_path), policy["filesystem"]["denyRead"])
                self.assertFalse(call.settings_path.stat().st_mode & 0o077)
            finally:
                call.settings_path.unlink(missing_ok=True)

    @unittest.skipIf(sys.platform.startswith("win"), "POSIX resource limits")
    def test_srt_wrapped_command_has_cpu_memory_and_file_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_srt = root / "fake-srt"
            fake_srt.write_text('#!/bin/sh\nshift 3\nexec "$@"\n', encoding="utf-8")
            fake_srt.chmod(0o755)
            import shlex
            probe = "import json,resource; print(json.dumps({name:resource.getrlimit(getattr(resource,name)) for name in ('RLIMIT_CPU','RLIMIT_AS','RLIMIT_FSIZE','RLIMIT_NOFILE','RLIMIT_NPROC')}))"
            with patch.object(command_sandbox, "_srt_binary", return_value=str(fake_srt)):
                call = command_sandbox.build_srt_command(
                    root=root, cwd=root,
                    command=f"{shlex.quote(sys.executable)} -c {shlex.quote(probe)}",
                    language="shell",
                    python_executable=sys.executable,
                )
            try:
                import subprocess
                result = subprocess.run(call.argv, cwd=root, capture_output=True,
                                        text=True, timeout=5, check=True)
                pairs = json.loads(result.stdout)
                for soft, hard in pairs.values(): self.assertEqual(soft, hard)
                limits = {name: pair[0] for name,pair in pairs.items()}
                self.assertLessEqual(limits["RLIMIT_CPU"], 120)
                self.assertLessEqual(limits["RLIMIT_AS"], 2 * 1024**3)
                self.assertLessEqual(limits["RLIMIT_FSIZE"], 128 * 1024**2)
                self.assertLessEqual(limits["RLIMIT_NOFILE"], 256)
                self.assertGreaterEqual(limits["RLIMIT_NPROC"], 64)
                self.assertLessEqual(limits["RLIMIT_NPROC"], command_sandbox._process_limit() + 64)
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

    def test_background_command_uses_srt_and_runner_owns_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            options = SimpleNamespace(approval=SimpleNamespace(command_sandbox="srt"))
            with patch.object(commander, "_command_safety_gate", new=AsyncMock(return_value=(None, ""))), \
                 patch.object(commander, "resolve_session_workspace", return_value=workspace), \
                 patch("webot.runtime.effective_session_mode", return_value="auto"), \
                 patch("webot.runtime_settings.get_runtime_settings", return_value=options), \
                 patch.object(command_sandbox, "_srt_binary", return_value="/usr/bin/srt"), \
                 patch.object(commander, "_launch_detached_background_job") as launch:
                result = asyncio.run(commander.run_command("alice", "print('hello')", language="python", mode="background"))
            try:
                self.assertIn("后台任务已启动", result)
                sandbox = launch.call_args.kwargs["sandbox"]
                self.assertEqual(sandbox.argv[0], "/usr/bin/srt")
                self.assertTrue(sandbox.settings_path.exists())
            finally:
                sandbox.settings_path.unlink(missing_ok=True)
                Path(launch.call_args.kwargs["cleanup_script"]).unlink(missing_ok=True)

    def test_scoped_sandbox_escalation_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            outside = Path(directory) / "outside.txt"
            root.mkdir()
            outside.write_text("hello", encoding="utf-8")
            with patch.dict('os.environ', {
                    'CLAWCROSS_SANDBOX_MAX_READ_PATHS': json.dumps([str(outside)]),
                    'CLAWCROSS_SANDBOX_MAX_WRITE_PATHS': json.dumps([str(outside)]),
                    'CLAWCROSS_SANDBOX_MAX_DOMAINS': '["example.org:443"]',
                }), patch.object(command_sandbox, "_srt_binary", return_value="/usr/bin/srt"):
                for access, target in (("read_path", str(outside)), ("write_path", str(outside)), ("network", "example.org:443")):
                    with self.subTest(access=access):
                        call = command_sandbox.build_srt_command(
                            root=root, cwd=root, command="true", language="shell",
                            python_executable=sys.executable, access=access, target=target,
                        )
                        try:
                            policy = json.loads(call.settings_path.read_text(encoding="utf-8"))
                            self.assertEqual(policy["network"]["allowedDomains"], [target] if access == "network" else [])
                            self.assertEqual(outside.as_posix() in policy["filesystem"]["allowWrite"], access == "write_path")
                            self.assertIn(str(Path.home().resolve()), policy["filesystem"]["denyRead"])
                        finally:
                            call.settings_path.unlink(missing_ok=True)

    def test_escalation_rejects_broad_or_credential_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            for access, target in (
                ("read_path", "/"), ("write_path", str(root.parent)),
                ("read_path", str(Path.home())), ("network", "*.example.org"),
                ("network", "127.0.0.1:51200"), ("host", "unexpected"),
            ):
                with self.subTest(access=access, target=target), self.assertRaises(command_sandbox.SandboxUnavailable):
                    command_sandbox.normalize_escalation(access, target, root)

    @unittest.skipIf(sys.platform.startswith("win"), "fake SRT runner uses a POSIX shell")
    def test_detached_srt_runner_executes_and_cleans_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_srt = root / "fake-srt"
            fake_srt.write_text('#!/bin/sh\nshift 3\nexec "$@"\n', encoding="utf-8")
            fake_srt.chmod(0o755)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            options = SimpleNamespace(approval=SimpleNamespace(command_sandbox="srt"))
            calls = []

            def build(**kwargs):
                sandbox = command_sandbox.build_srt_command(**kwargs)
                calls.append(sandbox)
                return sandbox

            async def exercise():
                start = await commander.run_command("alice", "echo sandbox-ready", mode="background")
                self.assertIn("job_id:", start)
                job_id = start.split("job_id: ", 1)[1].splitlines()[0]
                for _ in range(40):
                    status = await commander.background_command_io(job_id, username="alice")
                    if "状态: running" not in status:
                        return status
                    await asyncio.sleep(0.1)
                return status

            with patch.object(commander, "_command_safety_gate", new=AsyncMock(return_value=(None, ""))), \
                 patch.object(commander, "resolve_session_workspace", return_value=workspace), \
                 patch("webot.runtime_settings.get_runtime_settings", return_value=options), \
                 patch.object(command_sandbox, "_srt_binary", return_value=str(fake_srt)), \
                 patch.object(commander, "build_srt_command", side_effect=build):
                status = asyncio.run(exercise())
            self.assertIn("状态: completed", status)
            self.assertIn("sandbox-ready", status)
            self.assertEqual(len(calls), 1)
            self.assertFalse(calls[0].settings_path.exists())

    @unittest.skipIf(sys.platform.startswith("win"), "interactive jobs need a POSIX pseudo-terminal")
    def test_interactive_srt_runner_accepts_reviewed_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_srt = root / "fake-srt"
            fake_srt.write_text('#!/bin/sh\nshift 3\nexec "$@"\n', encoding="utf-8")
            fake_srt.chmod(0o755)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            options = SimpleNamespace(approval=SimpleNamespace(command_sandbox="srt"))
            calls = []

            def build(**kwargs):
                sandbox = command_sandbox.build_srt_command(**kwargs)
                calls.append(sandbox)
                return sandbox

            async def exercise():
                start = await commander.run_command("alice", "sh", mode="interactive")
                self.assertIn("job_id:", start)
                job_id = start.split("job_id: ", 1)[1].splitlines()[0]
                output = await commander.background_command_io(job_id, username="alice", input="echo interactive-ready", wait_seconds=1)
                await commander.background_command_io(job_id, username="alice", input="exit", wait_seconds=1)
                for _ in range(30):
                    status = await commander.background_command_io(job_id, username="alice")
                    if "状态: running" not in status:
                        return output, status
                    await asyncio.sleep(0.1)
                return output, status

            with patch.object(commander, "_command_safety_gate", new=AsyncMock(return_value=(None, ""))) as gate, \
                 patch.object(commander, "resolve_session_workspace", return_value=workspace), \
                 patch("webot.runtime_settings.get_runtime_settings", return_value=options), \
                 patch.object(command_sandbox, "_srt_binary", return_value=str(fake_srt)), \
                 patch.object(commander, "build_srt_command", side_effect=build):
                output, status = asyncio.run(exercise())
            self.assertIn("interactive-ready", output)
            self.assertIn("状态: completed", status)
            self.assertGreaterEqual(gate.await_count, 3)
            self.assertFalse(calls[0].settings_path.exists())

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

    def test_explicitly_installed_local_srt_is_found(self):
        with tempfile.TemporaryDirectory() as directory:
            local_bin = Path(directory)
            package = local_bin / "node" / "node_modules" / "@anthropic-ai" / "sandbox-runtime"
            (package / "dist").mkdir(parents=True)
            (package / "package.json").write_text(
                '{"name":"@anthropic-ai/sandbox-runtime","version":"0.0.77"}', encoding="utf-8",
            )
            cli = package / "dist" / "cli.js"
            cli.write_text("", encoding="utf-8")
            shim = local_bin / "node" / "node_modules" / ".bin" / "srt"
            shim.parent.mkdir(parents=True)
            shim.symlink_to(cli)
            with patch.dict("os.environ", {"CLAWCROSS_BIN_DIR": str(local_bin)}), patch.object(
                command_sandbox.shutil, "which",
                side_effect=lambda name: None if name == "srt" else f"/usr/bin/{name}",
            ):
                self.assertEqual(command_sandbox._srt_binary(), str(shim))

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
