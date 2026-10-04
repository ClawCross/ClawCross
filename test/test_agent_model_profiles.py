"""Profiles switch only one Agent, secrets stay server-side, model calls follow changes."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test.test_agents import ApiCase
from agents import model_profiles
from src.cli.commands import models_store


class AgentModelProfilesTests(ApiCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(patch.stopall)
        self.config = Path(self.tmp.name) / 'config'
        patch.object(model_profiles, 'CONFIG_DIR', self.config).start()
        patch.object(models_store, '_store_path', return_value=self.config / 'models.json').start()
        patch('agents.store.get_store', return_value=self.store).start()
        patch.dict(os.environ, {'LLM_MODEL':'platform-model', 'LLM_PROVIDER':'openai',
                                'LLM_BASE_URL':'https://platform.example/v1'}).start()
        self.webot(session='one'); self.webot(session='two')

    def save(self, user='alice', **updates):
        return self.call('POST', '/v1/agents/model-profiles', user=user, json={
            'name':'coding', 'model':'gpt-5.5', 'provider':'openai',
            'base_url':'https://api.example/v1', 'api_key':'SECRET_NOT_PUBLIC', **updates})

    def test_catalog_masks_keys_and_isolates_users_without_switching_default(self):
        models_store.upsert_profile('shared', 'deepseek', 'deepseek-chat', 'SHARED_SECRET', make_active=True)
        self.assertEqual(self.save().status_code, 200)
        alice = self.call('GET', '/v1/agents/model-profiles')
        bob = self.call('GET', '/v1/agents/model-profiles', user='bob')
        self.assertNotIn('SECRET', alice.text)
        self.assertEqual([p['id'] for p in alice.json()['profiles']], ['user:coding', 'platform:shared'])
        self.assertEqual([p['id'] for p in bob.json()['profiles']], ['platform:shared'])
        self.assertEqual(alice.json()['default']['model'], 'platform-model')
        self.assertEqual(models_store.load().active, 'shared')
        if os.name != 'nt':
            self.assertEqual(model_profiles._user_path('alice').stat().st_mode & 0o777, 0o600)

    def test_apply_and_reset_affect_only_current_agent_not_platform_or_other_user(self):
        self.save()
        path = '/v1/agents/one/model-profile'
        result = self.call('POST', path, json={'profile_id':'user:coding'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertNotIn('SECRET', result.text)
        self.assertEqual(result.json()['settings']['llm']['model'], 'gpt-5.5')
        self.assertEqual(self.store.get('alice', 'one').config['llm']['api_key'], 'SECRET_NOT_PUBLIC')
        self.assertNotIn('llm', self.store.get('alice', 'two').config)
        self.assertEqual(self.call('POST', path, user='bob', json={'profile_id':'user:coding'}).status_code, 400)
        self.assertIsNone(self.store.get('bob', 'one'))  # cannot read/apply Alice's private profile
        self.assertEqual(self.call('POST', path, json={}).status_code, 200)
        self.assertEqual(self.store.get('alice', 'one').config['llm'], {})
        self.assertEqual(os.environ['LLM_MODEL'], 'platform-model')

    def test_updates_keep_secret_and_require_reapply(self):
        self.save()
        self.call('POST', '/v1/agents/one/model-profile', json={'profile_id':'user:coding'})
        updated = self.save(model='new-model', api_key='')
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(model_profiles.profile_override('alice', 'user:coding')['api_key'], 'SECRET_NOT_PUBLIC')
        self.assertEqual(self.store.get('alice', 'one').config['llm']['model'], 'gpt-5.5')
        self.assertEqual(self.save(name='new', api_key='').status_code, 400)
        self.assertEqual(self.save(base_url='https://user:secret@example.com').status_code, 400)

    def test_external_agent_uses_native_model_configuration(self):
        agent = self.codex()
        result = self.call('POST', f'/v1/agents/{agent.agent_id}/model-profile', json={})
        self.assertEqual(result.status_code, 400)
        self.assertNotIn('llm', self.store.get('alice', agent.agent_id).config)

    def test_new_agent_is_created_only_when_user_explicitly_applies_configuration(self):
        self.save()
        self.assertEqual(self.call('GET', '/v1/agents/new-agent').status_code, 404)
        self.assertIsNone(self.store.get('alice', 'new-agent'))
        self.assertEqual(self.call('POST', '/v1/agents/new-agent/model-profile', json={'profile_id':'user:missing'}).status_code, 400)
        self.assertIsNone(self.store.get('alice', 'new-agent'))
        result = self.call('POST', '/v1/agents/new-agent/model-profile', json={'profile_id':'user:coding'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.store.get('alice','new-agent').config['llm']['model'], 'gpt-5.5')

    def test_model_and_reasoning_are_refreshed_for_each_call_including_tool_continuations(self):
        from webot.engine.agent import TeamAgent
        turn = SimpleNamespace(user_id='alice', session_id='one', max_tokens=1234, profile=None, is_subagent=False)
        self.save()
        self.call('POST', '/v1/agents/one/model-profile', json={'profile_id':'user:coding'})
        with patch('webot.engine.agent.llm_factory.create_chat_model') as factory, \
             patch('webot.engine.agent.get_runtime_settings', return_value=SimpleNamespace(inference=SimpleNamespace(reasoning_effort='high'))):
            TeamAgent._select_model({'messages':[]}, turn)
            self.assertEqual(factory.call_args.kwargs['model'], 'gpt-5.5')
            self.assertEqual(factory.call_args.kwargs['reasoning_effort'], 'high')
            turn.profile = SimpleNamespace(preferred_model='profile-fallback')
            TeamAgent._select_model({'messages':[]}, turn)
            self.assertEqual(factory.call_args.kwargs['model'], 'gpt-5.5')  # explicit Agent selection wins
            self.save(model='new-model')
            self.call('POST', '/v1/agents/one/model-profile', json={'profile_id':'user:coding'})
            TeamAgent._select_model({'messages':[]}, turn)
            self.assertEqual(factory.call_args.kwargs['model'], 'new-model')
            TeamAgent._select_model({'messages':[], 'llm_override':{'model':'one-request'}}, turn)
            self.assertEqual(factory.call_args.kwargs['model'], 'one-request')

    def test_runtime_capabilities_follow_selected_agent_model(self):
        from webot import runtime_settings
        self.save()
        self.call('POST', '/v1/agents/one/model-profile', json={'profile_id':'user:coding'})
        with patch.object(runtime_settings, '_load', return_value={}), \
             patch('common.model_capabilities.model_capabilities', side_effect=lambda model, provider: {'model':model}):
            self.assertEqual(runtime_settings.runtime_settings_payload('alice','one')['model_capabilities']['model'], 'gpt-5.5')
            self.assertEqual(runtime_settings.runtime_settings_payload('alice','two')['model_capabilities']['model'], 'platform-model')

    def test_remembered_approvals_list_and_revoke_only_own_agent_exact_action(self):
        from webot import remembered_approvals, policy
        first = json.dumps({'command':'SECRET_COMMAND', '_approval_session':'one'})
        other = json.dumps({'command':'other', '_approval_session':'two'})
        legacy = json.dumps({'command':'legacy'})
        rules = policy._normalize_policy({'tools':{'run_command':{'approved_args':[first,other,legacy]}}},
                                        source='user', definition_path='test')
        with patch.object(remembered_approvals, 'get_tool_policy', return_value=rules), \
             patch.object(remembered_approvals, 'save_tool_policy_config') as save:
            result = self.call('GET', '/v1/agents/one/remembered-approvals')
            self.assertNotIn('SECRET', result.text)
            self.assertEqual(len(result.json()['actions']), 1)
            key = result.json()['actions'][0]['key']
            path = f'/v1/agents/one/remembered-approvals/run_command/{key}'
            self.assertEqual(self.call('DELETE', path, user='bob').status_code, 404)
            self.assertEqual(self.call('DELETE', path.replace('/one/', '/two/')).status_code, 404)
            self.assertEqual(self.call('DELETE', path).status_code, 200)
            self.assertEqual(save.call_args.args[0], 'alice')
            self.assertEqual(save.call_args.args[1]['tools']['run_command']['approved_args'], [other,legacy])
