"""Settings inheritance, atomic persistence and authenticated API coverage."""
import asyncio
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "backend"))
from webot import runtime_settings as settings


class RuntimeSettingsTests(unittest.TestCase):
    def test_legacy_controls_migrate_once_and_workspace_copy_cannot_override(self):
        import json
        legacy = settings.USER_FILES_DIR / 'alice' / 'webot_runtime_settings.json'
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({'user': {'approval': {'mode': 'manual'}}, 'sessions': {}}))
        self.assertEqual(settings.get_runtime_settings('alice').approval.mode, 'manual')
        self.assertIn('.control', settings.settings_path('alice').parts)
        self.assertFalse(legacy.exists())
        legacy.write_text(json.dumps({'user': {'approval': {'mode': 'bypass'}}, 'sessions': {}}))
        self.assertEqual(settings.get_runtime_settings('alice').approval.mode, 'manual')

    def test_strict_security_requires_sandbox_and_rejects_remembered_escalation(self):
        options = settings.ApprovalSettings(sandbox_security='strict', command_sandbox='off')
        self.assertEqual(options.command_sandbox, 'auto')
        settings.save_runtime_settings('alice', session_id='strict-agent', settings={
            'approval': {'sandbox_security': 'strict'}})
        with self.assertRaises(ValueError):
            settings.remember_sandbox_grant('alice', session_id='strict-agent', access='network', target='example.com:443')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)
        patch.object(settings, "USER_FILES_DIR", Path(self.tmp.name)).start()
        patch.dict("os.environ", {"WEBOT_COMPRESSION_DISABLED": "0", "WEBOT_COMPRESSION_SUMMARY_TOKENS": "2000", "WEBOT_SUMMARIZER_MODEL": ""}).start()

    def test_defaults_session_inheritance_and_reset(self):
        self.assertEqual(settings.get_runtime_settings("alice").approval.approvals_reviewer, "user")
        self.assertEqual(settings.get_runtime_settings("alice").approval.command_sandbox, "off")
        settings.save_runtime_settings("alice", session_id="s", settings={"approval": {"command_sandbox": "srt"}})
        self.assertEqual(settings.get_runtime_settings("alice", "s").approval.command_sandbox, "srt")
        for backend in ('auto', 'landlock'):
            settings.save_runtime_settings("alice", session_id=backend, settings={"approval": {"command_sandbox": backend}})
            self.assertEqual(settings.get_runtime_settings("alice", backend).approval.command_sandbox, backend)
        self.assertEqual(settings.ApprovalSettings.model_validate({"command_sandbox": "container"}).command_sandbox, "srt")
        settings.save_runtime_settings("alice", settings={"context": {"history_tokens": 10000}})
        settings.save_runtime_settings("alice", session_id="s", settings={"context": {"preserve_recent_turns": 2}})
        settings.save_runtime_settings("alice", settings={"context": {"history_tokens": 12000}})
        effective = settings.get_runtime_settings("alice", "s")
        self.assertEqual(effective.context.history_tokens, 12000)
        self.assertEqual(effective.context.preserve_recent_turns, 2)
        self.assertEqual(settings.get_runtime_settings("bob").context.history_tokens, 0)
        settings.save_runtime_settings("alice", session_id="s", settings={}, reset=True)
        self.assertEqual(settings.get_runtime_settings("alice", "s").context.preserve_recent_turns, 4)

    def test_invalid_update_does_not_change_file(self):
        settings.save_runtime_settings("alice", session_id="s", settings={"context": {"trigger_tokens": 10000}})
        original = settings.settings_path("alice").read_bytes()
        for change in (
            {"context": {"history_tokens": 1000}},
            {"context": {"preserve_recent_turns": 0}},
            {"context": {"auto_compact": "true"}},
            {"approval": {"approvals_reviewer": "allow_all"}},
            {"approval": {"command_sandbox": "host"}},
            {"approval": {"sandbox_allowed_domains": ["*"]}},
            {"approval": {"sandbox_allowed_domains": ["https://example.com"]}},
            {"context": {"summarizer_input_tokens": 1024}},
            {"unknown": {}},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                settings.save_runtime_settings("alice", settings=change)
            self.assertEqual(settings.settings_path("alice").read_bytes(), original)

    def test_concurrent_keep_grants_persist_atomically_and_reset_with_session(self):
        import json
        from webot.command_sandbox import active_sandbox_grants
        targets = ['example.com:443', 'example.org:80', 'example.net:443']
        with patch.dict('os.environ', {'CLAWCROSS_SANDBOX_MAX_DOMAINS':json.dumps(targets)}):
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(lambda target: settings.remember_sandbox_grant('alice',session_id='s',
                    access='network',target=target), targets))
            grants = settings.get_runtime_settings('alice','s').approval.sandbox_grants
            self.assertEqual({g.target for g in grants}, set(targets))
            self.assertEqual(settings.get_runtime_settings('alice','other').approval.sandbox_grants, [])
            with patch.dict('os.environ', {'CLAWCROSS_SANDBOX_MAX_DOMAINS':'["example.org:80"]'}):
                self.assertEqual(active_sandbox_grants(grants,Path(self.tmp.name))['network'], ['example.org:80'])
            settings.save_runtime_settings('alice',session_id='s',settings={},reset=True)
            self.assertEqual(settings.get_runtime_settings('alice','s').approval.sandbox_grants, [])

    def test_saved_read_grant_does_not_follow_changed_symlink(self):
        import json
        from webot.command_sandbox import active_sandbox_grants
        root = Path(self.tmp.name) / 'workspace'
        root.mkdir()
        target = Path(self.tmp.name) / 'public.txt'
        replacement = Path(self.tmp.name) / 'another.txt'
        target.write_text('PUBLIC')
        replacement.write_text('OTHER')
        grant = settings.SandboxGrant(access='read_path',target=str(target))
        with patch.dict('os.environ',{'CLAWCROSS_SANDBOX_MAX_READ_PATHS':json.dumps([str(target),str(replacement)])}):
            self.assertEqual(active_sandbox_grants([grant],root)['read_path'],[str(target)])
            target.unlink()
            target.symlink_to(replacement)
            self.assertEqual(active_sandbox_grants([grant],root)['read_path'],[])

    def test_manual_and_bypass_persist_as_distinct_modes(self):
        settings.save_runtime_settings("alice", settings={"approval": {"mode": "manual"}})
        settings.save_runtime_settings("alice", session_id="s", settings={"approval": {"mode": "bypass"}})
        self.assertEqual(settings.get_runtime_settings("alice").approval.mode, "manual")
        self.assertEqual(settings.get_runtime_settings("alice", "s").approval.mode, "bypass")

    def test_rejects_user_path_traversal(self):
        for user in ("", "../bob", "alice/../bob", "/tmp/bob", "alice\\..\\bob", ".", ".."):
            with self.subTest(user=user), self.assertRaises(ValueError):
                settings.settings_path(user)

    def test_concurrent_session_updates_are_not_lost(self):
        def save(i):
            settings.save_runtime_settings("alice", session_id=f"s-{i}", settings={"context": {"preserve_recent_turns": i + 1}})
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(save, range(16)))
        for i in range(16):
            self.assertEqual(settings.get_runtime_settings("alice", f"s-{i}").context.preserve_recent_turns, i + 1)

    def test_service_authentication_runs_before_write(self):
        from fastapi import HTTPException
        from webot.models import WeBotRuntimeSettingsUpdateRequest
        from webot.api.service import WeBotService
        auth = Mock(side_effect=HTTPException(status_code=401, detail="unauthorized"))
        service = WeBotService(system=None, agent=SimpleNamespace(), verify_auth_or_token=auth, extract_text=str)
        req = WeBotRuntimeSettingsUpdateRequest(user_id="alice", settings={"approval": {"approvals_reviewer": "auto_review"}})
        with self.assertRaises(HTTPException):
            asyncio.run(service.update_runtime_settings(req, None))
        self.assertFalse(settings.settings_path("alice").exists())

    def test_front_proxy_uses_authenticated_user(self):
        from flask import Flask
        from frontend.proxies.webot import register_webot_routes
        app = Flask(__name__)
        app.secret_key = "test-secret"
        register_webot_routes(app, port_agent=1, internal_token="test-token")
        client = app.test_client()
        with patch("frontend.proxies.webot.requests.post") as post:
            self.assertEqual(client.post("/proxy_webot_runtime_settings", json={}).status_code, 401)
            post.assert_not_called()
            with client.session_transaction() as session:
                session["user_id"] = "alice"
            post.return_value = SimpleNamespace(status_code=200, json=lambda: {"status": "success"})
            self.assertEqual(client.post("/proxy_webot_runtime_settings", json={"user_id": "bob", "session_id": "s", "settings": {}}).status_code, 200)
            self.assertEqual(post.call_args.kwargs["json"]["user_id"], "alice")


    def test_configured_window_overrides_model_name_guess(self):
        settings.save_runtime_settings('alice', settings={'context': {'context_window_tokens': 1_000_000}})
        config = settings.get_runtime_settings('alice').context
        self.assertEqual(config.context_window_tokens, 1_000_000)
        with patch('webot.context_limits.infer_model_context_window', return_value=64_000):
            self.assertEqual(settings.resolve_context_window(config, "deepseek-flash"), 1_000_000)
            self.assertEqual(settings.resolve_context_history_budget(config, model="deepseek-flash"), 800_000)
        with patch('webot.context_limits.infer_model_context_window', return_value=2_000_000):
            self.assertEqual(settings.resolve_context_window(config), 1_000_000)
        with self.assertRaises(ValueError):
            settings.save_runtime_settings('alice', settings={'approval': {'mode': 'unknown'}})

    def test_manual_context_limits_control_compaction_budget_and_usage(self):
        settings.save_runtime_settings("alice", settings={"context": {"context_window_tokens": 500000}})
        settings.save_runtime_settings("alice", session_id="s", settings={"context": {"context_window_tokens": 20000}})
        user = settings.get_runtime_settings("alice").context
        session = settings.get_runtime_settings("alice", "s").context
        self.assertEqual(settings.resolve_context_history_budget(user, model="deepseek-flash"), 400000)
        self.assertEqual(settings.resolve_context_history_budget(session, prefix_tokens=6000, output_reserve=4000), 10000)
        usage = settings.context_usage_with_window({"tokens": 10000, "budget": 64000, "source": "api", "breakdown": {"messages": 9000}}, 20000)
        self.assertEqual(usage["percent"], 50)
        self.assertEqual(usage["remaining"], 10000)
        self.assertEqual(usage["source"], "api")
        self.assertEqual(usage["breakdown"], {"messages": 9000})

    def test_session_context_usage_uses_new_window_with_existing_api_usage(self):
        from webot.api.session_service import SessionService
        settings.save_runtime_settings("alice", session_id="s", settings={"context": {"context_window_tokens": 20000}})
        agent = SimpleNamespace(
            get_thread_context_usage=lambda _: {"tokens": 10000, "budget": 64000, "percent": 16, "source": "api"},
            get_thread_model=lambda _: "deepseek-flash",
        )
        service = SessionService(db_path=":memory:", agent=agent, extract_text=str)
        result = asyncio.run(service.context_usage("alice", "s"))
        self.assertEqual(result["budget"], 20000)
        self.assertEqual(result["percent"], 50)
        self.assertEqual(result["source"], "api")
