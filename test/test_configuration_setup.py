"""Private config writes, ownership, validation and Agent settings use real storage."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.env_settings import read_env_all
from ops import configuration_requests as setup
from webot import runtime_settings, runtime_store


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup); self.root = Path(tmp.name)
        self.env = self.root / '.env'
        for module,name,value in [(setup,'DB_PATH',self.root/'forms.db'),(setup,'ENV_FILE',self.env),
            (runtime_settings,'USER_FILES_DIR',self.root/'users'),(runtime_store,'DEFAULT_DB_PATH',self.root/'runtime.db')]:
            p=patch.object(module,name,value);p.start();self.addCleanup(p.stop)

    def test_private_key_never_in_query_request_db_status_or_inbox(self):
        self.env.write_text('LLM_API_KEY=abc\nLLM_MODEL=old\n')
        schema=setup.describe('alice','one','model')[0]
        self.assertNotIn('abc',json.dumps(schema))
        request=setup.create('alice','one','model',{'LLM_MODEL':'new'})
        result=setup.submit('alice',request['id'],{'LLM_API_KEY':'PRIVATE_KEY'})
        self.assertEqual(read_env_all(str(self.env))['LLM_API_KEY'],'PRIVATE_KEY')
        self.assertNotIn('PRIVATE_KEY',json.dumps(result))
        self.assertNotIn('PRIVATE_KEY',(self.root/'forms.db').read_bytes().decode('latin1'))
        self.assertFalse((self.root/'runtime.db').exists())  # No second inbox delivery.
        self.assertEqual(setup.status('alice',request['id'])['status'],'completed')

    def test_agent_cannot_prefill_secret_endpoint_or_security_mode(self):
        for topic,values in [('model',{'LLM_API_KEY':'secret'}),('model',{'LLM_BASE_URL':'https://evil'}),
            ('approval',{'mode':'bypass'}),('approval',{'command_sandbox':'off'}),('model',{'ARBITRARY':'x'})]:
            with self.subTest(topic=topic),self.assertRaises(ValueError):setup.create('alice','one',topic,values)

    def test_agent_runtime_form_preserves_other_fields_and_scopes(self):
        request=setup.create('alice','one','context',{'preserve_recent_turns':'7'})
        setup.submit('alice',request['id'],{'auto_compact':'false'})
        settings=runtime_settings.get_runtime_settings('alice','one')
        self.assertEqual(settings.context.preserve_recent_turns,7);self.assertFalse(settings.context.auto_compact)
        self.assertTrue(runtime_settings.get_runtime_settings('alice','two').context.auto_compact)
        request=setup.create('alice','one','approval')
        setup.submit('alice',request['id'],{'mode':'manual','sandbox_security':'strict'})
        self.assertEqual(runtime_store.get_session_mode('alice','one')['mode'],'manual')
        self.assertEqual(runtime_settings.get_runtime_settings('alice','one').approval.command_sandbox,'auto')

    def test_owner_validation_replacement_cancel_and_env_injection(self):
        old=setup.create('alice','one','model');req=setup.create('alice','one','model')
        self.assertEqual(setup.status('alice',old['id'])['status'],'cancelled')
        self.assertEqual(setup.list_requests('bob'),[])
        with self.assertRaises(ValueError):setup.submit('bob',req['id'],{})
        with self.assertRaises(ValueError):setup.submit('alice',req['id'],{'LLM_MODEL':'x\nSECRET=y'})
        self.assertFalse(self.env.exists())
        setup.submit('alice',req['id'],{},cancel=True);self.assertFalse(self.env.exists())

    def test_authenticated_api_uses_supplied_env_and_returns_no_secret(self):
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient
        from ops.settings_routes import create_settings_router
        def auth(user,password,token):
            if password!='test':raise HTTPException(401)
        app=FastAPI();app.include_router(create_settings_router(env_path=str(self.env),verify_auth_or_token=auth));client=TestClient(app)
        self.assertEqual(client.get('/configuration/setup',params={'user_id':'alice'}).status_code,401)
        req=setup.create('alice','one','model',{'LLM_MODEL':'test-model'})
        result=client.post('/configuration/setup',json={'user_id':'alice','password':'test','request_id':req['id'],'values':{'LLM_API_KEY':'API_SECRET'}})
        self.assertEqual(result.status_code,200,result.text);self.assertNotIn('API_SECRET',result.text)
        self.assertEqual(read_env_all(str(self.env))['LLM_API_KEY'],'API_SECRET')
