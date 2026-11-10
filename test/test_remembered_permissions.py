"""Owner-managed grants are scoped to one Agent and cannot override hard limits."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from agents.routes import create_agents_router
from agents.store import AgentStore, WEBOT
from webot import policy, runtime_settings, runtime_store, workspace
from webot.approval_actions import canonical_action_args


class RememberedPermissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)
        root = Path(self.tmp.name)
        self.store = AgentStore(root / 'agents.db')
        self.store.create('alice', driver=WEBOT, agent_id='first')
        self.store.create('alice', driver=WEBOT, agent_id='second')
        patch.object(policy, 'USER_FILES_DIR', root / 'users').start()
        patch.object(runtime_settings, 'USER_FILES_DIR', root / 'users').start()
        patch.object(runtime_store, 'DEFAULT_DB_PATH', root / 'runtime.db').start()
        patch.object(workspace, 'WORKSPACE_DIR', root / 'workspaces').start()
        patch.object(runtime_settings, 'runtime_settings_payload', return_value={}).start()
        patch('agents.store.get_store', return_value=self.store).start()
        patch.dict('os.environ', {'CLAWCROSS_SANDBOX_MAX_DOMAINS': '["example.com"]',
                                 'CLAWCROSS_SANDBOX_MAX_READ_PATHS': '[]',
                                 'CLAWCROSS_SANDBOX_MAX_WRITE_PATHS': '[]'}).start()
        app = FastAPI()
        app.include_router(create_agents_router(internal_token='fixture-token',
            verify_password=lambda user, password: password == 'pw', store=self.store, gateway=SimpleNamespace()))
        self.client = TestClient(app)

    def request(self, method, agent='first', path='', user='alice', **kwargs):
        return self.client.request(method, f'/v1/agents/{agent}/remembered-approvals{path}',
                                   headers={'Authorization': f'Bearer {user}:pw'}, **kwargs)

    def test_network_grant_is_exact_deduplicated_and_owned(self):
        body = {'kind':'network', 'target':'example.com:443'}
        first = self.request('POST', json=body)
        self.assertEqual(first.status_code, 200, first.text)
        grant = first.json()['sandbox_grants'][0]
        self.assertEqual(grant['target'], 'example.com:443')
        self.assertEqual(len(self.request('POST', json=body).json()['sandbox_grants']), 1)
        self.assertEqual(self.request('GET', agent='second').json()['sandbox_grants'], [])
        self.assertEqual(self.request('DELETE', agent='second', path=f"/sandbox/network/{grant['key']}").status_code, 404)
        self.assertEqual(self.request('GET', user='bob').status_code, 404)
        self.assertEqual(self.request('DELETE', path=f"/sandbox/network/{grant['key']}").status_code, 200)
        self.assertEqual(self.request('GET').json()['sandbox_grants'], [])

    def test_admin_network_and_path_maximum_still_apply(self):
        for body in ({'kind':'network','target':'other.com'}, {'kind':'network','target':'*'},
                     {'kind':'read_path','target':self.tmp.name}):
            with self.subTest(body=body):
                result = self.request('POST', json=body)
                self.assertEqual(result.status_code, 400, result.text)
        self.assertEqual(self.request('GET').json()['sandbox_grants'], [])

    def test_strict_keeps_inactive_grants_visible_and_removable(self):
        self.request('POST', json={'kind':'network','target':'example.com:443'})
        runtime_settings.save_runtime_settings('alice', session_id='first',
            settings={'approval': {'sandbox_security':'strict', 'mode':'bypass'}})
        state = self.request('GET').json()
        self.assertEqual(state['sandbox_security'], 'strict')
        self.assertEqual(len(state['sandbox_grants']), 1)
        result = self.request('POST', json={'kind':'network','target':'example.com:443'})
        self.assertEqual(result.status_code, 400)
        key = state['sandbox_grants'][0]['key']
        self.assertEqual(self.request('DELETE', path=f'/sandbox/network/{key}').status_code, 200)

    def test_tool_arguments_match_after_identity_and_default_binding(self):
        body = {'kind':'tool','tool_name':'web_fetch','arguments':{'url':'https://example.com'}}
        first = self.request('POST', json=body)
        self.assertEqual(first.status_code, 200, first.text)
        action = first.json()['actions'][0]
        self.assertEqual(action['tool'], 'web_fetch')
        args = canonical_action_args('web_fetch', {'url':'https://example.com', 'username':'alice',
                                                  'session_id':'first', '_approval_session':'first'})
        approved = policy.evaluate_tool_policy(policy.get_tool_policy('alice'), 'web_fetch', args)
        self.assertTrue(approved.allowed)
        self.assertIn('完整参数', approved.reason)
        changed = {**args, 'url':'https://other.com'}
        self.assertNotIn('完整参数', policy.evaluate_tool_policy(policy.get_tool_policy('alice'), 'web_fetch', changed).reason)
        self.assertEqual(self.request('GET', agent='second').json()['actions'], [])
        self.assertEqual(self.request('DELETE', agent='second', path=f"/web_fetch/{action['key']}").status_code, 404)
        self.assertEqual(self.request('DELETE', path=f"/web_fetch/{action['key']}").status_code, 200)
        self.assertEqual(self.request('GET').json()['actions'], [])

    def test_strict_bypass_cannot_add_outside_web_or_file_permission(self):
        runtime_settings.save_runtime_settings('alice', session_id='first', settings={'approval': {
            'sandbox_security':'strict', 'mode':'bypass', 'sandbox_allowed_domains':['example.com:443']}})
        good = self.request('POST', json={'kind':'tool','tool_name':'web_fetch', 'arguments':{'url':'https://example.com'}})
        self.assertEqual(good.status_code, 200, good.text)
        for name, args in (('web_fetch', {'url':'http://example.com'}),
                           ('web_fetch', {'url':'https://other.com'}),
                           ('read_file', {'filename':str(Path(self.tmp.name) / 'outside.txt')})):
            result = self.request('POST', json={'kind':'tool','tool_name':name,'arguments':args})
            self.assertEqual(result.status_code, 400, result.text)
        self.assertEqual(len(self.request('GET').json()['actions']), 1)

    def test_explicit_deny_and_internal_arguments_cannot_be_overridden(self):
        policy.save_tool_policy_config('alice', {'tools': {'web_fetch': {'approval':'deny'}}})
        body = {'kind':'tool','tool_name':'web_fetch','arguments':{'url':'https://example.com'}}
        self.assertEqual(self.request('POST', json=body).status_code, 400)
        body['tool_name'] = 'web_search'
        body['arguments'] = {'_approval_session':'second'}
        self.assertEqual(self.request('POST', json=body).status_code, 400)
        body['tool_name'] = 'invented_tool'
        body['arguments'] = {}
        self.assertEqual(self.request('POST', json=body).status_code, 400)
        self.assertEqual(self.request('GET').json()['actions'], [])

    def test_secret_fields_are_redacted_in_inspection(self):
        from webot.remembered_approvals import _public_arguments
        result = _public_arguments({'headers':{'Authorization':'private-token'}, 'api_key':'key',
                                    '_approval_session':'first','url':'https://example.com'})
        self.assertEqual(result, {'headers':{'Authorization':'••••'},'api_key':'••••', 'url':'https://example.com'})
