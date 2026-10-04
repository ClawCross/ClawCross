"""Unified levels preserve native limits, remap per model and stay per Agent."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from common.reasoning_levels import level_map, mapped_effort


class ReasoningLevelsTests(unittest.TestCase):
    def test_mapping_uses_supported_choices_with_monotonic_conservative_fallback(self):
        self.assertEqual(list(level_map(['high','low','medium']).values()),
                         ['low','low','low','medium','high','high','high'])
        self.assertEqual(list(level_map(['none','low','medium','high','xhigh']).values()),
                         ['none','none','low','medium','high','xhigh','xhigh'])
        self.assertEqual(list(level_map(['low','medium','high','max']).values()),
                         ['low','low','low','medium','high','high','max'])
        self.assertEqual(mapped_effort(['off','high'],1),'off')
        self.assertEqual(level_map(['default','auto','unknown']),{})
        self.assertIsNone(mapped_effort(['low','high'],8))
        self.assertIsNone(mapped_effort(['low','high'],True))

    def test_factory_sends_actual_native_parameter_for_openai_and_anthropic(self):
        from common.llm_factory import create_chat_model
        from langchain_core.messages import HumanMessage
        model = create_chat_model(model='gpt-5.5',provider='openai',api_key='test',
                                  base_url='https://api.openai.com/v1',reasoning_level=7)
        payload = model._get_request_payload([HumanMessage('test')])
        self.assertEqual(payload['reasoning']['effort'],'xhigh')
        model = create_chat_model(model='claude-opus-4-6',provider='anthropic',api_key='test',
                                  base_url='https://api.anthropic.com',reasoning_level=7)
        payload = model._get_request_payload([HumanMessage('test')])
        self.assertEqual(payload['output_config']['effort'],'max')

    def test_unknown_model_does_not_receive_invented_effort(self):
        from common.model_capabilities import reasoning_effort
        self.assertIsNone(reasoning_effort('unknown-private-model','openai','high',level=7))

    def test_unified_level_is_persisted_per_agent_and_preserves_recent_turn_setting(self):
        from webot import runtime_settings as settings
        with TemporaryDirectory() as tmp, patch.object(settings,'USER_FILES_DIR',Path(tmp)):
            settings.save_runtime_settings('alice',session_id='one',settings={'inference':{'reasoning_level':7}})
            settings.save_runtime_settings('alice',session_id='two',settings={'inference':{'reasoning_level':3}})
            self.assertEqual(settings.get_runtime_settings('alice','one').inference.reasoning_level,7)
            self.assertEqual(settings.get_runtime_settings('alice','two').inference.reasoning_level,3)
            self.assertEqual(settings.get_runtime_settings('bob','one').inference.reasoning_level,0)
            self.assertEqual(settings.get_runtime_settings('alice','one').context.preserve_recent_turns,4)
            with self.assertRaises(ValueError):
                settings.save_runtime_settings('alice',session_id='one',settings={'inference':{'reasoning_level':8}})

    def test_acp_capabilities_expose_mapping_and_validate_unified_settings(self):
        from agents.store import AgentStore, ACPX
        from external.acp_settings import capability_card, validate_settings
        with TemporaryDirectory() as tmp:
            store=AgentStore(Path(tmp)/'agents.db')
            agent=store.create('alice',driver=ACPX,config={'platform':'claude'})
            options=[{'id':'effort','currentValue':'medium','options':[{'value':v} for v in ['low','medium','high','max']]}]
            with patch('external.acp_settings.native_options',return_value=options):
                self.assertEqual(capability_card(agent)['config_options'][0]['reasoning_level_map']['7'],'max')
                selected=validate_settings(agent,{'reasoning_level':6,'config_options':{'effort':'high'}})
                self.assertEqual(selected['config_options'],{})
                for bad in (8,True,'7'):
                    with self.assertRaises(ValueError):
                        validate_settings(agent,{'reasoning_level':bad})
                with self.assertRaises(ValueError):
                    validate_settings(agent,{'reasoning_level':7,'config_options':[]})


class AcpxReasoningLevelsTests(unittest.IsolatedAsyncioTestCase):
    async def test_mapping_is_resolved_after_connection_and_not_repeated_or_sent_as_7(self):
        from external.acpx import AcpxAdapter
        adapter=AcpxAdapter.__new__(AcpxAdapter)
        options=[]
        async def connect(**kwargs):
            options[:] = [{'id':'reasoning_effort','currentValue':'medium',
                           'options':[{'value':v} for v in ['low','medium','high','xhigh']]}]
        adapter.ensure_session=AsyncMock(side_effect=connect)
        adapter._local_config_options=lambda _:options
        adapter._local_config_values=lambda _:{'reasoning_effort':'medium'}
        adapter._run_json=AsyncMock(return_value='{}')
        adapter._send_prompt_file=AsyncMock(return_value='{"text":"ok"}')
        adapter.consume_initial_prompt=lambda **kwargs:(kwargs['prompt_text'],False)
        params={'tool':'codex','session_key':'owned','prompt_text':'hello',
                'config_options':{'_clawcross_reasoning_level':7,'reasoning_effort':'low'}}
        await adapter.prompt_with_trace(**params)
        self.assertEqual(adapter._run_json.call_args.args[0],['codex','set','reasoning_effort','xhigh','-s','owned'])
        self.assertNotIn('_clawcross_reasoning_level',adapter._send_prompt_file.call_args.kwargs['config_options'])
        self.assertEqual(params['config_options']['_clawcross_reasoning_level'],7)  # caller state remains intact
        adapter._local_config_values=lambda _:{'reasoning_effort':'xhigh'}
        adapter._run_json.reset_mock()
        await adapter.prompt_with_trace(**params)
        adapter._run_json.assert_not_awaited()
        params['config_options']['_clawcross_reasoning_level']=0
        await adapter.prompt_with_trace(**params)
        adapter._run_json.assert_not_awaited()
