"""Credentials bypass tool history; ownership, validation and merge use real storage."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from channels import setup_requests as setup
from common.env_settings import read_env_all
from webot import runtime_store


class ChannelSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = self.root / '.env'
        for obj, key, value in [(setup, 'DB_PATH', self.root / 'requests.db'), (setup, 'PID_DIR', self.root / 'run'),
                                (setup, 'ENV_FILE', self.env), (runtime_store, 'DEFAULT_DB_PATH', self.root / 'runtime.db')]:
            p = patch.object(obj, key, value); p.start(); self.addCleanup(p.stop)

    def test_user_secret_saved_without_tool_result_database_or_inbox_leak(self):
        request = setup.create('alice', 'agent-one', 'telegram', {'name': 'My bot'})
        secret = '123456789:' + 'A' * 30
        result = setup.submit('alice', request['id'], {'token': secret})
        self.assertEqual(result['status'], 'completed')
        raw = read_env_all(str(self.env))
        self.assertEqual(json.loads(raw['TELEGRAM_BOTS'])[0], {'name': 'My bot', 'token': secret})
        self.assertIn('telegram', raw['NONEBOT_ADAPTERS'])
        self.assertNotIn(secret, (self.root / 'requests.db').read_bytes().decode('latin1'))
        self.assertNotIn(secret, (self.root / 'runtime.db').read_bytes().decode('latin1'))
        self.assertNotIn(secret, json.dumps(setup.status('alice', request['id'])))
        with self.assertRaises(ValueError): setup.submit('alice', request['id'], {'token': secret})

    def test_agent_cannot_supply_secrets_urls_or_program_paths(self):
        for channel, values in [('telegram', {'token': 'KEY'}), ('weclaw', {'WECLAW_BIN': 'danger'}),
                                 ('webhook', {'WEBHOOK_WEBHOOK_URL': 'https://private/token'})]:
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                setup.create('alice', 'agent-one', channel, values)

    def test_other_user_cannot_read_or_submit_and_cancellation_does_not_write(self):
        request = setup.create('alice', 'agent-one', 'telegram')
        self.assertEqual(setup.list_requests('bob'), [])
        with self.assertRaises(ValueError): setup.status('bob', request['id'])
        with self.assertRaises(ValueError): setup.submit('bob', request['id'], {})
        setup.submit('alice', request['id'], {}, cancel=True)
        self.assertFalse(self.env.exists())

    def test_existing_secret_second_bot_and_enabled_adapters_survive_partial_update(self):
        self.env.write_text("NONEBOT_ADAPTERS=qq\nTELEGRAM_BOTS='" + json.dumps([
            {'token': 'saved-secret', 'name': 'old'}, {'token': 'second-secret'}]) + "'\n")
        request = setup.create('alice', 'agent-one', 'telegram', {'name': 'new'})
        setup.submit('alice', request['id'], {})
        raw = read_env_all(str(self.env))
        self.assertEqual(json.loads(raw['TELEGRAM_BOTS']), [{'token':'saved-secret','name':'new'}, {'token':'second-secret'}])
        self.assertEqual(raw['NONEBOT_ADAPTERS'], 'qq,telegram')

    def test_invalid_required_or_newline_env_value_does_not_save_or_disclose(self):
        request = setup.create('alice', 'agent-one', 'telegram')
        with self.assertRaises(ValueError): setup.submit('alice', request['id'], {})
        self.assertFalse(self.env.exists())
        self.assertEqual(setup.status('alice', request['id'])['status'], 'pending')
        request = setup.create('alice', 'agent-one', 'webhook')
        with self.assertRaises(ValueError): setup.submit('alice', request['id'], {'WEBHOOK_WEBHOOK_URL':'url\nLLM_API_KEY=secret'})
        self.assertFalse(self.env.exists())

    def test_mail_nested_settings_and_numeric_ports_use_adapter_schema(self):
        request = setup.create('alice', 'agent-one', 'mail', {'id':'mail-bot', 'imap_host':'imap.example.com', 'imap_port':'993'})
        setup.submit('alice', request['id'], {'password':'PRIVATE_MAIL_PASSWORD'})
        bot = json.loads(read_env_all(str(self.env))['MAIL_BOTS'])[0]
        self.assertEqual(bot['imap']['host'], 'imap.example.com')
        self.assertEqual(bot['imap']['port'], 993)
        self.assertNotIn('imap_port', bot)

    def test_authenticated_routes_use_same_storage_without_echoing_secret(self):
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient
        from ops.settings_routes import create_settings_router
        def auth(user_id, password, token):
            if password != 'test-pass': raise HTTPException(401)
        app = FastAPI(); app.include_router(create_settings_router(env_path=str(self.env), verify_auth_or_token=auth))
        client = TestClient(app)
        self.assertEqual(client.get('/channels/setup', params={'user_id':'alice'}).status_code, 401)
        request = setup.create('alice', 'agent-one', 'telegram')
        secret = '123456789:' + 'B' * 30
        result = client.post('/channels/setup', json={'user_id':'alice','password':'test-pass','request_id':request['id'],'values':{'token':secret}})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertNotIn(secret, result.text)
        listed = client.get('/channels/setup', params={'user_id':'alice','password':'test-pass'})
        self.assertNotIn(secret, listed.text)
