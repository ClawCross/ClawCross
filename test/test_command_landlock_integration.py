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

    async def test_multiple_y_reviews_accumulate_only_in_one_command_call(self):
        second = self.base/'second.txt'
        second.write_text('SECOND_ALLOWED_DATA')
        command = 'cat ' + shlex.quote(str(self.outside)) + ' && cat ' + shlex.quote(str(second))
        context = review.review_context([HumanMessage(content=f'读取 {self.outside} 和 {second}，不修改任何文件。',
            id='test-original-user',additional_kwargs={'input_origin':'user'})])
        verdict = review.ReviewVerdict(decision='Y',reason='用户授权读取两个指定文件',risk='low',authorization_sources=['test-original-user'])
        with patch.dict('os.environ',{'CLAWCROSS_SANDBOX_MAX_READ_PATHS':json.dumps([str(self.outside),str(second)])}), \
             patch.object(review,'approval_context',return_value=context), \
             patch.object(review,'run_reviewer',new=AsyncMock(return_value=verdict)) as model:
            result = await self.execute(command)
            self.assertIn('SYNTHETIC_OUTSIDE_DATA',result,result)
            self.assertIn('SECOND_ALLOWED_DATA',result,result)
            self.assertEqual(self.foreground.await_count,3)
            self.assertEqual(model.await_count,2)
            snapshot = model.await_args_list[1].kwargs['context']['sandbox_permissions']
            self.assertEqual(snapshot['requested'],{'access':'read_path','target':str(second)})
            self.assertEqual(snapshot['review_number'],2)
            self.assertEqual([(g['access'],g['target']) for g in snapshot['already_granted']], [('read_path',str(self.outside))])
            self.assertTrue(model.await_args_list[1].kwargs['args']['sandbox_approval_chain'])
            self.assertEqual(runtime_settings.get_runtime_settings(self.user,self.session).approval.sandbox_grants,[])
            model.return_value = review.ReviewVerdict(decision='N',reason='新调用不继承此前 Y',risk='medium',authorization_sources=[])
            fresh = await self.execute('cat ' + shlex.quote(str(self.outside)))
            self.assertIn('新调用不继承',fresh)
            self.assertEqual(self.foreground.await_count,4)
            self.assertEqual(model.await_count,3)

    async def test_second_review_can_deny_continuing_after_first_y(self):
        second = self.base/'second.txt'
        second.write_text('SECOND_NOT_ALLOWED')
        y = review.ReviewVerdict(decision='Y',reason='批准首次只读',risk='low',authorization_sources=['test-original-user'])
        n = review.ReviewVerdict(decision='N',reason='累计权限超出任务授权',risk='medium',authorization_sources=[])
        with patch.dict('os.environ',{'CLAWCROSS_SANDBOX_MAX_READ_PATHS':json.dumps([str(self.outside),str(second)])}), \
             patch.object(review,'run_reviewer',new=AsyncMock(side_effect=[y,n])) as model:
            result = await self.execute('cat '+shlex.quote(str(self.outside))+' && cat '+shlex.quote(str(second)))
        self.assertIn('累计权限超出任务授权',result)
        self.assertNotIn('SECOND_NOT_ALLOWED',result)
        self.assertEqual(self.foreground.await_count,2)
        self.assertEqual(model.await_count,2)

    async def test_manual_y_continuation_retains_previous_grants_and_reviews_next_target(self):
        from webot.permission_context import resolve_permission_request
        runtime_settings.save_runtime_settings(self.user,session_id=self.session,settings={'approval':{'mode':'manual'}})
        second = self.base/'second.txt'
        second.write_text('SECOND_MANUAL_ALLOWED')
        command = 'cat '+shlex.quote(str(self.outside))+' && cat '+shlex.quote(str(second))
        async def resume(record):
            args = json.loads(record.args_json)
            args.pop('username'); args.pop('session_id')
            return await self.execute(**args)
        with patch.dict('os.environ',{'CLAWCROSS_SANDBOX_MAX_READ_PATHS':json.dumps([str(self.outside),str(second)])}), \
             patch.object(review,'run_reviewer') as model:
            first = await self.execute(command)
            self.assertIn('【操作授权请求】',first)
            one = store.list_tool_approvals(self.user,self.session,status='pending')[0]
            resolve_permission_request(user_id=self.user,approval_id=one.approval_id,action='approved')
            next_request = await resume(one)
            self.assertIn('【操作授权请求】',next_request,next_request)
            two = store.list_tool_approvals(self.user,self.session,status='pending')[0]
            self.assertEqual(json.loads(two.args_json)['sandbox_approval_chain'],[one.approval_id])
            snapshot = json.loads(two.review_metadata_json)['sandbox_permissions']
            self.assertEqual(snapshot['already_granted'][0]['target'],str(self.outside))
            resolve_permission_request(user_id=self.user,approval_id=two.approval_id,action='approved')
            result = await resume(two)
        self.assertIn('SECOND_MANUAL_ALLOWED',result,result)
        self.assertEqual(self.foreground.await_count,3)
        model.assert_not_called()
        self.assertEqual(runtime_settings.get_runtime_settings(self.user,self.session).approval.sandbox_grants,[])

    async def test_retry_chain_cannot_be_forged_or_moved_to_another_command(self):
        verdict = review.ReviewVerdict(decision='Y',reason='测试授权读取',risk='low',authorization_sources=['test-original-user'])
        with patch.dict('os.environ',{'CLAWCROSS_SANDBOX_MAX_READ_PATHS':json.dumps([str(self.outside)])}), \
             patch.object(review,'run_reviewer',new=AsyncMock(return_value=verdict)):
            await self.execute('cat '+shlex.quote(str(self.outside)))
            record = store.list_tool_approvals(self.user,self.session)[0]
            calls = self.foreground.await_count
            other = await self.execute('head '+shlex.quote(str(self.outside)),sandbox_access='read_path',
                escalation_target=str(self.outside),escalation_reason='恢复',sandbox_approval_chain=[record.approval_id])
            forged = await self.execute('cat '+shlex.quote(str(self.outside)),sandbox_access='read_path',
                escalation_target=str(self.outside),escalation_reason='恢复',sandbox_approval_chain=['approval-doesnotexist'])
            old = await self.execute('cat '+shlex.quote(str(self.outside)),sandbox_access='read_path',
                escalation_target=str(self.outside),escalation_reason='恢复',sandbox_approval_chain=[record.approval_id])
            self.assertIn('不匹配',other)
            self.assertIn('已失效',forged)
            self.assertIn('调用已结束',old)
            self.assertEqual(self.foreground.await_count,calls)

    async def test_ai_keep_read_grant_persists_without_writes_or_cross_session_access(self):
        verdict = review.ReviewVerdict(decision='KEEP Y', reason='用户授权本会话持续只读此文件',
            risk='low', authorization_sources=['test-original-user'])
        context = review.review_context([HumanMessage(content=f'允许本会话持续读取 {self.outside}，不要修改它。',
            id='test-original-user', additional_kwargs={'input_origin':'user'})])
        with patch.dict('os.environ', {'CLAWCROSS_SANDBOX_MAX_READ_PATHS':json.dumps([str(self.outside)])}), \
             patch.object(review,'approval_context',return_value=context), \
             patch.object(review,'run_reviewer',new=AsyncMock(return_value=verdict)) as model:
            first = await self.execute('cat ' + shlex.quote(str(self.outside)))
            self.assertIn('SYNTHETIC_OUTSIDE_DATA', first, first)
            self.assertEqual(self.foreground.await_count, 2)
            followup = await self.execute('head -c 22 ' + shlex.quote(str(self.outside)))
            self.assertIn('SYNTHETIC_OUTSIDE_DATA', followup, followup)
            self.assertEqual(self.foreground.await_count, 3)
            mutation = await self.execute(f'import os; os.remove({str(self.outside)!r})', language='python')
            self.assertIn('PermissionError', mutation, mutation)
            cross_session = await commander.run_command(self.user, 'cat ' + shlex.quote(str(self.outside)),
                session_id='different_agent')
            # Different session starts without the grant; the denied first run gets a new review.
            self.assertEqual(self.foreground.await_count, 6)
            self.assertIn('SYNTHETIC_OUTSIDE_DATA', cross_session, cross_session)
            self.assertEqual(model.await_count, 2)
        self.assertEqual(self.outside.read_text(), 'SYNTHETIC_OUTSIDE_DATA')
        saved = runtime_settings.settings_path(self.user).read_bytes()
        self.assertIn(b'read_path', saved)
        with patch.object(review,'run_reviewer') as model:
            # Lowering the administrator ceiling disables existing grants on the next command.
            revoked = await self.execute('cat ' + shlex.quote(str(self.outside)))
            self.assertIn('提权上限', revoked, revoked)
            model.assert_not_called()
        with patch.dict('os.environ', {'CLAWCROSS_SANDBOX_MAX_READ_PATHS':json.dumps([str(self.outside)])}), \
             patch.object(review,'run_reviewer',new=AsyncMock(return_value=review.ReviewVerdict(
                 decision='N',reason='测试拒绝新授权',risk='medium',authorization_sources=[]))) as model:
            runtime_settings.save_runtime_settings(self.user,session_id=self.session,
                settings={'approval':{'sandbox_grants':[]}})
            denied = await self.execute('cat ' + shlex.quote(str(self.outside)))
            self.assertNotIn('SYNTHETIC_OUTSIDE_DATA', denied)
            model.assert_awaited_once()

    async def test_human_keep_read_grant_is_available_to_later_default_commands(self):
        from webot.permission_context import resolve_permission_request
        runtime_settings.save_runtime_settings(self.user, session_id=self.session, settings={'approval':{'mode':'manual'}})
        with patch.dict('os.environ', {'CLAWCROSS_SANDBOX_MAX_READ_PATHS':json.dumps([str(self.outside)])}), \
             patch.object(review,'run_reviewer') as model:
            pending = await self.execute('cat ' + shlex.quote(str(self.outside)))
            self.assertIn('【操作授权请求】', pending)
            record = store.list_tool_approvals(self.user,self.session,status='pending')[0]
            resolve_permission_request(user_id=self.user,approval_id=record.approval_id,action='approved',remember=True)
            result = await self.execute('head ' + shlex.quote(str(self.outside)))
            self.assertIn('SYNTHETIC_OUTSIDE_DATA', result, result)
            self.assertEqual(self.foreground.await_count, 2)
            model.assert_not_called()

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
    async def test_ai_keep_network_grant_allows_later_https_without_second_review(self):
        from webot.command_sandbox import network_fence_available
        if not network_fence_available():
            self.skipTest('requires a systemd host with network-fence privileges')
        context = review.review_context([HumanMessage(content='此会话持续读取 example.com 的公开网页，禁止上传本地数据。',
            id='network-user', additional_kwargs={'input_origin':'user'})])
        verdict = review.ReviewVerdict(decision='KEEP Y',reason='用户授权本会话持续访问该公开网站',
            risk='low',authorization_sources=['network-user'])
        with patch.object(review,'approval_context',return_value=context), \
             patch.dict('os.environ',{'CLAWCROSS_SANDBOX_MAX_DOMAINS':'["example.com:443"]'}), \
             patch.object(review,'run_reviewer',new=AsyncMock(return_value=verdict)) as model:
            first = await self.execute('curl --max-time 12 -sS https://example.com',timeout_seconds=25)
            self.assertIn('Example Domain',first,first)
            self.assertEqual(self.foreground.await_count,2)
            second = await self.execute('curl --max-time 12 -sS --head https://example.com/',timeout_seconds=25)
            self.assertIn('执行成功 (exit code: 0)',second,second)
            self.assertEqual(self.foreground.await_count,3)
            model.assert_awaited_once()
            settings = runtime_settings.get_runtime_settings(self.user,self.session).approval
            self.assertEqual([g.model_dump() for g in settings.sandbox_grants],
                [{'access':'network','target':'example.com:443'}])
            self.assertEqual(settings.sandbox_allowed_domains,[])
            self.assertEqual(runtime_settings.get_runtime_settings(self.user,'other').approval.sandbox_grants,[])

    @unittest.skipUnless(__import__('os').environ.get('CLAWCROSS_NETWORK_INTEGRATION') == '1', 'explicit real public-network integration')
    async def test_allowed_https_domain_is_accessible_through_socks(self):
        runtime_settings.save_runtime_settings(self.user,settings={'approval':{'mode':'auto','command_sandbox':'landlock','sandbox_allowed_domains':['example.com:443']}})
        result=await self.execute('curl --max-time 12 -sS --proxy "$ALL_PROXY" https://example.com',timeout_seconds=20)
        self.assertIn('执行成功 (exit code: 0)',result,result)
        self.assertIn('Example Domain',result)
        self.assertEqual(self.foreground.await_count,1)

    @unittest.skipUnless(__import__('os').environ.get('CLAWCROSS_NETWORK_INTEGRATION') == '1', 'explicit real public-network integration')
    async def test_default_network_ceiling_reviews_python_https_without_operator_setup(self):
        import os
        context = review.review_context([HumanMessage(content='用 Python 读取 https://example.com 公开网页。',
            id='python-network-user', additional_kwargs={'input_origin': 'user'})])
        verdict = review.ReviewVerdict(decision='approve', reason='用户授权公开网页读取',
            risk='low', authorization_sources=['python-network-user'])
        with patch.dict('os.environ'), patch.object(review, 'approval_context', return_value=context), \
             patch.object(review, 'run_reviewer', new=AsyncMock(return_value=verdict)) as model:
            os.environ.pop('CLAWCROSS_SANDBOX_MAX_DOMAINS', None)
            result = await self.execute('import urllib.request; print(urllib.request.urlopen("https://example.com", timeout=10).read(1000).decode())',
                language='python', timeout_seconds=25)
        self.assertIn('Example Domain', result, result)
        self.assertEqual(self.foreground.await_count, 2)
        model.assert_awaited_once()
        self.assertEqual(model.await_args.kwargs['args']['escalation_target'], 'example.com:443')

    @unittest.skipUnless(__import__('os').environ.get('CLAWCROSS_NETWORK_INTEGRATION') == '1', 'explicit real public-network integration')
    async def test_python_requests_uses_granted_proxy_and_direct_sockets_stay_blocked(self):
        runtime_settings.save_runtime_settings(self.user, settings={'approval': {
            'mode': 'auto', 'command_sandbox': 'landlock', 'sandbox_allowed_domains': ['example.com:443']}})
        result = await self.execute('import requests; print("HTTPS_STATUS", requests.get("https://example.com", timeout=10).status_code)',
            language='python', timeout_seconds=20)
        self.assertIn('HTTPS_STATUS 200', result, result)
        denied = await self.execute('import socket; socket.create_connection(("1.1.1.1", 443), timeout=1)',
            language='python', timeout_seconds=10)
        self.assertRegex(denied, 'Operation not permitted|Permission denied')

    @unittest.skipUnless(__import__('os').environ.get('CLAWCROSS_NETWORK_INTEGRATION') == '1', 'explicit real public-network integration')
    async def test_auto_http_zero_exit_reviews_network_and_retries_once(self):
        context = review.review_context([HumanMessage(content='允许本次测试通过代理访问 http://example.com。',
            id='network-test-user', additional_kwargs={'input_origin':'user'})])
        verdict = review.ReviewVerdict(decision='approve', reason='用户明确授权这个公开网站',
            risk='low', authorization_sources=['network-test-user'])
        command = "curl --max-time 10 -sS -o /dev/null -w 'AUTO_HTTP_OK %{http_code}' http://example.com"
        with patch.dict('os.environ', {'CLAWCROSS_SANDBOX_MAX_DOMAINS':'["*"]'}), \
             patch.object(review, 'approval_context', return_value=context), \
             patch.object(review, 'run_reviewer', new=AsyncMock(return_value=verdict)) as model:
            result = await self.execute(command, timeout_seconds=25)
        self.assertIn('AUTO_HTTP_OK 200', result, result)
        self.assertEqual(self.foreground.await_count, 2)
        model.assert_awaited_once()
        self.assertEqual(store.list_tool_approvals(self.user, self.session, status='pending'), [])
        self.assertEqual(runtime_settings.get_runtime_settings(self.user, self.session).approval.sandbox_allowed_domains, [])

    @unittest.skipUnless(__import__('os').environ.get('CLAWCROSS_NETWORK_INTEGRATION') == '1', 'explicit real public-network integration')
    async def test_manual_https_approval_reaches_real_mcp_tool_and_retries_once(self):
        code = 'import urllib.request; print("MCP_HTTPS_OK", urllib.request.urlopen("https://example.com", timeout=10).status)'
        await self._manual_network_approval_reaches_real_mcp_tool(code, 'python', 'example.com:443', 'MCP_HTTPS_OK 200')

    @unittest.skipUnless(__import__('os').environ.get('CLAWCROSS_NETWORK_INTEGRATION') == '1', 'explicit real public-network integration')
    async def test_manual_http_curl_zero_exit_still_requests_network_approval(self):
        command = "curl --max-time 10 -sS -o /dev/null -w 'MCP_HTTP_OK %{http_code}' http://example.com"
        await self._manual_network_approval_reaches_real_mcp_tool(command, 'shell', 'example.com:80', 'MCP_HTTP_OK 200', zero_exit=True)

    @unittest.skipUnless(__import__('os').environ.get('CLAWCROSS_NETWORK_INTEGRATION') == '1', 'explicit real public-network integration')
    async def test_manual_python_caught_http_error_still_requests_network_approval(self):
        code = ('import urllib.request, urllib.error\n'
                'try:\n    print("MCP_CAUGHT_OK", urllib.request.urlopen("http://example.com", timeout=10).status)\n'
                'except urllib.error.HTTPError as exc:\n    print("CAUGHT_HTTP_ERROR", exc.code)')
        await self._manual_network_approval_reaches_real_mcp_tool(code, 'python', 'example.com:80', 'MCP_CAUGHT_OK 200', zero_exit=True)

    async def _manual_network_approval_reaches_real_mcp_tool(self, command, language, target, expected, *, zero_exit=False):
        from types import SimpleNamespace
        from langchain_core.tools import StructuredTool
        from webot.api.service import WeBotService
        from webot.models import WeBotApprovalResolutionRequest
        from webot.engine.agent import TeamAgent, UserAwareToolNode, _visible_tool_parameters
        runtime_settings.save_runtime_settings(self.user, settings={'approval':{'mode':'manual','command_sandbox':'landlock'}})
        store.save_session_mode(self.user, self.session, mode='manual')
        with patch.dict('os.environ', {'CLAWCROSS_SANDBOX_MAX_DOMAINS':json.dumps([target])}):
            initial = await commander.mcp.call_tool('run_command', {'username':self.user,'session_id':self.session,
                'command':command,'language':language,'timeout_seconds':25})
            self.assertIn('【操作授权请求】', str(initial))
            if zero_exit:
                self.assertEqual(self.foreground.await_args.kwargs['execution_report']['exit_code'], 0)
                self.assertNotIn('执行成功 (exit code: 0)', str(initial))
                self.assertIn('网络访问被沙盒代理拒绝', str(initial))
            record = store.list_tool_approvals(self.user, self.session, status='pending')[0]
            self.assertEqual(json.loads(record.args_json)['sandbox_access'], 'network')
            self.assertEqual(json.loads(record.args_json)['escalation_target'], target)
            system = SimpleNamespace(run=AsyncMock(return_value={'status':'received'}))
            service = WeBotService(agent=None,system=system,verify_auth_or_token=lambda *a:None,extract_text=str)
            with patch('agents.store.get_store',return_value=SimpleNamespace(get=lambda *a:None)):
                await service.resolve_tool_approval(WeBotApprovalResolutionRequest(user_id=self.user,
                    session_id=self.session,approval_id=record.approval_id,action='approve'),None)
            engine = TeamAgent.__new__(TeamAgent)
            state = {'user_id':self.user,'session_id':self.session,'session_mode':'manual',
                     'enabled_tools':['run_command'],'messages':[], '_approval_resume_id':record.approval_id}
            update = await engine._call_model(state)
            state.update(update)
            tool = StructuredTool.from_function(coroutine=commander.run_command, name='run_command', description='Command')
            self.assertNotIn('sandbox_access', _visible_tool_parameters(tool)['properties'])
            node = UserAwareToolNode([tool])
            # Run through FastMCP's argument validation, as the production RPC does.
            async def invoke_mcp(call_state, config):
                from langchain_core.messages import ToolMessage
                call=call_state['messages'][-1].tool_calls[0]
                content=await commander.mcp.call_tool(call['name'],call['args'])
                return {'messages':[ToolMessage(content=str(content),tool_call_id=call['id'],name=call['name'])]}
            node.tool_node.ainvoke=invoke_mcp
            result = await node(state, {})
        self.assertIn(expected, result['messages'][0].content, result)
        self.assertNotIn('ClawCross proxy denied network target:', result['messages'][0].content)
        self.assertEqual(self.foreground.await_count,2)
        self.assertEqual(store.get_tool_approval(record.approval_id,self.user).status,'used')
        self.assertEqual(runtime_settings.get_runtime_settings(self.user,self.session).approval.sandbox_allowed_domains,[])


if __name__ == '__main__':
    unittest.main()
