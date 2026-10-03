"""Real production run_command execution with the production Landlock launcher.

Only the model verdict and isolated user/workspace fixtures are substituted. Command execution,
policy checks, permission ceilings, approval persistence and retry are real.
No live user configuration, conversation or production backend is changed.
"""
import asyncio
import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'backend'))

from langchain_core.messages import HumanMessage
from webot import approval_review as review, policy, runtime_settings, runtime_store as store
from webot.command_sandbox import landlock_available
from webot.workspace import SessionWorkspace
from webot.mcp import commander


@unittest.skipUnless(landlock_available(), 'requires Linux, Landlock ABI >= 6 and libseccomp')
class CommandLandlockIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='clawcross-landlock-integration-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'workspace'
        self.root.mkdir()
        self.outside = self.base / 'outside.txt'
        self.outside.write_text('SYNTHETIC_OUTSIDE_DATA')
        self.workspace = SessionWorkspace(root=self.root, cwd=self.root, mode='shared', remote='')
        for obj, name, value in (
            (runtime_settings, 'USER_FILES_DIR', self.base / 'users'),
            (policy, 'USER_FILES_DIR', self.base / 'users'),
            (policy, 'PROJECT_ROOT', self.base),
            (store, 'DEFAULT_DB_PATH', self.base / 'runtime.db'),
        ):
            self.patch(obj, name, value)
        env = patch.dict('os.environ', {
            'WEBOT_COMPRESSION_SUMMARY_TOKENS': '2000',
            'CLAWCROSS_SANDBOX_MAX_READ_PATHS': '[]',
            'CLAWCROSS_SANDBOX_MAX_WRITE_PATHS': '[]',
            'CLAWCROSS_SANDBOX_MAX_DOMAINS': '[]',
        })
        env.start(); self.addCleanup(env.stop)
        self.user, self.session = 'landlock_test', 'isolated_test_session'
        policy.save_tool_policy_config(self.user, {'tools': {'run_command': {'approval': 'manual'}}})
        runtime_settings.save_runtime_settings(self.user, settings={'approval': {'mode': 'auto', 'command_sandbox': 'landlock'}})
        self.patch(commander, 'resolve_session_workspace', lambda *a, **k: self.workspace)
        self.patch(sys.modules['webot.workspace'], 'resolve_session_workspace', lambda *a, **k: self.workspace)
        context = review.review_context([HumanMessage(
            content=f'允许为本次测试读取 {self.outside}，不要修改它。', id='test-original-user',
            additional_kwargs={'input_origin': 'user'},
        )])
        self.patch(review, 'approval_context', lambda *a, **k: dict(context))
        self.foreground = self.patch(commander, '_run_foreground', AsyncMock(wraps=commander._run_foreground))

    def patch(self, obj, name, value):
        p = patch.object(obj, name, value)
        result = p.start(); self.addCleanup(p.stop)
        return result

    async def execute(self, command, **kwargs):
        return await commander.run_command(self.user, command, session_id=self.session, **kwargs)

    async def test_python_and_shell_execute_and_secrets_are_stripped(self):
        with patch.dict('os.environ', {'LLM_API_KEY': 'test-secret'}), patch.object(review, 'run_reviewer') as model:
            result = await self.execute("import os; from pathlib import Path; assert 'LLM_API_KEY' not in os.environ; Path('ok.txt').write_text('WORKSPACE_OK'); print('PYTHON_OK')", language='python')
            shell = await self.execute('cat ok.txt')
        self.assertIn('PYTHON_OK', result)
        self.assertIn('WORKSPACE_OK', shell)
        model.assert_not_called()

    async def test_manual_command_does_not_execute_until_the_user_approves(self):
        runtime_settings.save_runtime_settings(self.user, settings={'approval': {
            'mode': 'manual', 'command_sandbox': 'off'}})
        marker = self.root / 'human-approved.txt'
        code = "from pathlib import Path; Path('human-approved.txt').write_text('MANUAL_OK'); print('MANUAL_OK')"
        with patch.object(review, 'run_reviewer') as model:
            pending = await self.execute(code, language='python')
            self.assertIn('【操作授权请求】', pending)
            self.assertFalse(marker.exists())
            self.foreground.assert_not_called()
            record = store.list_tool_approvals(self.user, self.session, status='pending')[0]
            reply = review.review_context([HumanMessage(content='Y ' + record.approval_id,
                id='formal-manual-yes', additional_kwargs={'input_origin': 'user'})])
            self.assertIn('已批准', review.resolve_conversation_reply(self.user, self.session, reply))
            result = await self.execute(code, language='python')
        self.assertIn('MANUAL_OK', result)
        self.assertEqual(marker.read_text(), 'MANUAL_OK')
        self.foreground.assert_awaited_once()
        model.assert_not_called()
        self.assertEqual(store.get_tool_approval(record.approval_id, self.user).status, 'used')

    async def test_encoded_python_child_cannot_delete_outside_workspace(self):
        import base64
        encoded = base64.b64encode(f'import os; os.remove({str(self.outside)!r})'.encode()).decode()
        code = f'import subprocess, sys; r = subprocess.run([sys.executable, "-c", "import base64; exec(base64.b64decode({encoded!r}))"], capture_output=True, text=True); print(r.stderr); assert r.returncode == 0'
        with patch.object(review, 'run_reviewer') as model:
            result = await self.execute(code, language='python')
        self.assertIn('PermissionError', result)
        self.assertNotIn('执行成功 (exit code: 0)', result)
        self.assertEqual(self.outside.read_text(), 'SYNTHETIC_OUTSIDE_DATA')
        model.assert_not_called()

    async def test_network_is_blocked_by_real_kernel_filter(self):
        result = await self.execute("import socket; s=socket.socket(socket.AF_INET); s.connect(('127.0.0.1',1))", language='python')
        self.assertRegex(result, 'Operation not permitted|Permission denied')

    async def test_missing_permission_exceeding_ceiling_does_not_call_reviewer(self):
        with patch.object(review, 'run_reviewer') as model:
            result = await self.execute('cat ' + shlex.quote(str(self.outside)))
        self.assertIn('提权上限', result)
        self.assertEqual(self.foreground.await_count, 1)
        model.assert_not_called()

    async def test_system_approval_retries_same_command_once_with_read_only_access(self):
        verdict = review.ReviewVerdict(decision='approve', reason='测试用户明确授权读取这个文件',
            risk='low', authorization_sources=['test-original-user'])
        with patch.dict('os.environ', {'CLAWCROSS_SANDBOX_MAX_READ_PATHS': json.dumps([str(self.outside)])}), \
             patch.object(review, 'run_reviewer', new=AsyncMock(return_value=verdict)) as model:
            result = await self.execute('cat ' + shlex.quote(str(self.outside)))
        self.assertIn('SYNTHETIC_OUTSIDE_DATA', result)
        self.assertIn('执行成功 (exit code: 0)', result)
        self.assertEqual(self.foreground.await_count, 2)
        model.assert_awaited_once()
        args = model.await_args.kwargs['args']
        self.assertEqual(args['sandbox_access'], 'read_path')
        self.assertEqual(args['escalation_target'], str(self.outside))
        self.assertIn('Permission denied', json.dumps(model.await_args.kwargs['context']))
        records = store.list_tool_approvals(self.user, self.session)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, 'used')
        self.assertEqual(self.outside.read_text(), 'SYNTHETIC_OUTSIDE_DATA')

    async def test_model_denial_stops_without_retry_or_human_popup(self):
        verdict = review.ReviewVerdict(decision='deny', reason='测试拒绝', risk='medium', authorization_sources=[])
        with patch.dict('os.environ', {'CLAWCROSS_SANDBOX_MAX_READ_PATHS': json.dumps([str(self.outside)])}), \
             patch.object(review, 'run_reviewer', new=AsyncMock(return_value=verdict)) as model:
            result = await self.execute('cat ' + shlex.quote(str(self.outside)))
        self.assertIn('测试拒绝', result)
        self.assertNotIn('【操作授权请求】', result)
        self.assertEqual(self.foreground.await_count, 1)
        model.assert_awaited_once()
        self.assertEqual(store.list_tool_approvals(self.user, self.session)[0].status, 'denied')

    async def test_read_grant_does_not_allow_followup_python_deletion(self):
        verdict = review.ReviewVerdict(decision='approve', reason='测试仅授权读取',
            risk='low', authorization_sources=['test-original-user'])
        deletion = f'import os; os.remove({str(self.outside)!r})'
        command = ('cat ' + shlex.quote(str(self.outside)) + ' && '
                   + shlex.join([sys.executable, '-c', deletion]))
        with patch.dict('os.environ', {'CLAWCROSS_SANDBOX_MAX_READ_PATHS': json.dumps([str(self.outside)])}), \
             patch.object(review, 'run_reviewer', new=AsyncMock(return_value=verdict)) as model:
            result = await self.execute(command)
        self.assertEqual(self.foreground.await_count, 2)
        model.assert_awaited_once()
        self.assertIn('SYNTHETIC_OUTSIDE_DATA', result)
        self.assertIn('PermissionError', result)
        self.assertNotIn('执行成功 (exit code: 0)', result)
        self.assertEqual(self.outside.read_text(), 'SYNTHETIC_OUTSIDE_DATA')

    async def test_auto_selects_a_working_backend_before_running_user_command_once(self):
        runtime_settings.save_runtime_settings(self.user, settings={'approval': {'mode': 'auto', 'command_sandbox': 'auto'}})
        result = await self.execute("printf AUTO_BACKEND_OK")
        self.assertIn('执行成功 (exit code: 0)', result)
        self.assertIn('AUTO_BACKEND_OK', result)
        self.assertEqual(self.foreground.await_count, 1)

    async def test_write_grant_changes_only_the_approved_existing_file(self):
        verdict = review.ReviewVerdict(decision='approve', reason='测试授权修改指定文件', risk='low', authorization_sources=['test-original-user'])
        # Shell redirection reports an unambiguous write failure with its target.
        command = 'printf WRITE_GRANTED > ' + shlex.quote(str(self.outside))
        with patch.dict('os.environ', {'CLAWCROSS_SANDBOX_MAX_WRITE_PATHS': json.dumps([str(self.outside)])}), \
             patch.object(review, 'run_reviewer', new=AsyncMock(return_value=verdict)) as model:
            result = await self.execute(command)
        self.assertIn('执行成功 (exit code: 0)', result)
        self.assertEqual(self.outside.read_text(), 'WRITE_GRANTED')
        self.assertEqual(self.foreground.await_count, 2)
        self.assertEqual(model.await_args.kwargs['args']['sandbox_access'], 'write_path')

    async def test_resource_limit_cannot_be_raised_and_tempfiles_work(self):
        code = "import resource, tempfile; f=tempfile.TemporaryFile(); f.write(b'ok'); resource.setrlimit(resource.RLIMIT_AS, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))"
        result = await self.execute(code, language='python')
        self.assertIn('not allowed', result)
        self.assertFalse(list(self.root.glob('.command-tmp-*')))

    async def test_missing_backend_capability_never_executes_command(self):
        with patch('webot.command_sandbox.landlock_available', return_value=False):
            result = await self.execute('printf MUST_NOT_RUN')
        self.assertIn('不会降级', result)
        self.foreground.assert_not_awaited()

    async def test_child_cannot_escape_supervised_process_group(self):
        result = await self.execute('import os; os.setsid()', language='python')
        self.assertIn('Operation not permitted', result)

    async def test_background_command_uses_real_backend_and_cleans_temporary_directory(self):
        previous = set(commander._BACKGROUND_JOBS)
        await self.execute("printf BACKGROUND_LANDLOCK_OK", mode='background', notify_on_done=False)
        created = set(commander._BACKGROUND_JOBS) - previous
        self.assertEqual(len(created), 1)
        job_id = created.pop()
        job = commander._BACKGROUND_JOBS[job_id]
        try:
            for _ in range(60):
                job = commander._refresh_background_job(job)
                if job.status not in {'running', 'starting'}:
                    break
                await asyncio.sleep(.05)
            self.assertEqual(job.exit_code, 0, Path(job.stderr_path).read_text())
            self.assertIn('BACKGROUND_LANDLOCK_OK', Path(job.stdout_path).read_text())
            for _ in range(20):
                if not list(self.root.glob('.command-tmp-*')):
                    break
                await asyncio.sleep(.05)
            self.assertFalse(list(self.root.glob('.command-tmp-*')))
        finally:
            if job.status in {'running', 'starting'}:
                await commander.cancel_background_command(job_id, username=self.user, session_id=self.session)
            commander._BACKGROUND_JOBS.pop(job_id, None)
            commander._reap_detached_runners()

    @unittest.skipUnless(__import__('os').environ.get('CLAWCROSS_NETWORK_INTEGRATION') == '1', 'explicit real public-network integration')
    async def test_https_permission_failure_is_reviewed_and_retried_once(self):
        from webot.command_sandbox import network_fence_available
        if not network_fence_available():
            self.skipTest('requires a systemd host with network-fence privileges')
        context=review.review_context([HumanMessage(content='读取 https://example.com 的公开网页，禁止上传本地数据。',id='network-user',additional_kwargs={'input_origin':'user'})])
        verdict=review.ReviewVerdict(decision='approve',reason='用户授权公开网页读取',risk='low',authorization_sources=['network-user'])
        with patch.object(review,'approval_context',return_value=context), \
             patch.dict('os.environ',{'CLAWCROSS_SANDBOX_MAX_DOMAINS':'["example.com:443"]'}), \
             patch.object(review,'run_reviewer',new=AsyncMock(return_value=verdict)) as model:
            result=await self.execute('curl --max-time 12 -sS https://example.com',timeout_seconds=25)
        self.assertIn('执行成功 (exit code: 0)',result,result)
        self.assertIn('Example Domain',result)
        self.assertEqual(self.foreground.await_count,2)
        model.assert_awaited_once()
        self.assertEqual(model.await_args.kwargs['args']['escalation_target'],'example.com:443')

    @unittest.skipUnless(__import__('os').environ.get('CLAWCROSS_NETWORK_INTEGRATION') == '1', 'explicit real public-network integration')
    async def test_allowed_https_domain_is_accessible_through_socks(self):
        runtime_settings.save_runtime_settings(self.user,settings={'approval':{'mode':'auto','command_sandbox':'landlock','sandbox_allowed_domains':['example.com:443']}})
        result=await self.execute('curl --max-time 12 -sS --proxy "$ALL_PROXY" https://example.com',timeout_seconds=20)
        self.assertIn('执行成功 (exit code: 0)',result,result)
        self.assertIn('Example Domain',result)
        self.assertEqual(self.foreground.await_count,1)


if __name__ == '__main__':
    unittest.main()
