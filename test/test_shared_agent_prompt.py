"""Shared content and incremental MCP updates, without live adapters or LLM calls."""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'/'backend'))
from agents.store import AgentStore, ACPX, HTTP
from agents.messages import AgentMessage
from common.agent_prompt import identity_sections, render_section_updates
from external import session, tool_bridge
from webot.runtime import build_session_mode_message

class SharedPromptTests(unittest.TestCase):
    def test_shared_rules_and_mcp_catalog_without_workflow_or_cli_repetition(self):
        with TemporaryDirectory() as tmp, \
             patch('webot.skills.build_user_skills_listing',return_value='SKILL CATALOG') as skills, \
             patch('webot.workflow_prompt.build_team_workflow_prompt',return_value='FULL WORKFLOW') as workflow, \
             patch('webot.skills.build_user_profile_block',return_value='PROFILE'), \
             patch('webot.soul.build_soul_prompt',return_value='SOUL'):
            store=AgentStore(Path(tmp)/'agents.db')
            agent=store.create('alice',driver=ACPX,config={'platform':'codex'})
            prompts={'base_system.txt':'BASE {chat_rules}', 'conversation_rules.txt':'GROUP RULES',
                     'external_agent_system.txt':'TRANSPORT RULES'}
            with patch('external.session._prompt_file',side_effect=lambda n:prompts.get(n,'')):
                sections=session.identity_sections(agent)
            self.assertEqual(sections['base_rules'],identity_sections(base='BASE {chat_rules}',conversation='GROUP RULES')['base_rules'])
            dynamic=session.build_dynamic_context(agent,AgentMessage(text='hello'),context={},mode='auto',enabled_tools=None,response_format=None)
            self.assertEqual(dynamic['mode'],build_session_mode_message('auto'))
            self.assertEqual(dynamic['cli_entry'],'')
            self.assertEqual(dynamic['workflows'],'')
            self.assertEqual(skills.call_args.kwargs['tool_mode'],'mcp')
            workflow.assert_not_called()
            http=store.create('alice',driver=HTTP,config={'api_url':'http://unused'})
            fallback=session.build_dynamic_context(http,AgentMessage(text='hello'),context={},mode=None,enabled_tools=None,response_format=None)
            self.assertIn('未开启',fallback['tool_connector'])
            self.assertEqual(skills.call_args.kwargs['tool_mode'],'cli')

    def test_mcp_results_refresh_live_group_state_once_and_reset_resends(self):
        with TemporaryDirectory() as tmp, \
             patch('external.session.identity_sections',return_value={'base_rules':'RULES'}), \
             patch('webot.skills.build_user_skills_listing',return_value=''), \
             patch('webot.workflow_prompt.build_team_workflow_prompt',return_value=''), \
             patch('common.conversation_context._membership_provider') as provider:
            provider.return_value=[{'group_id':'g1','title':'Initial'}]
            store=AgentStore(Path(tmp)/'agents.db')
            agent=store.create('alice',driver=ACPX,config={'platform':'codex'})
            msg=AgentMessage(text='USER TEXT')
            context={'groups':[{'group_id':'g1','title':'STALE','content':'PRIVATE BODY'}]}
            def prepare(a):return session.prepare_turn(a,msg,context=context,mode='readonly',enabled_tools=None,response_format=None)
            prepared=prepare(agent)
            self.assertIn('Initial',prepared.text)
            self.assertNotIn('STALE',prepared.text)
            self.assertNotIn('PRIVATE BODY',prepared.text)
            with tool_bridge.active_turn(agent,msg,context,'readonly',None,prepared=prepared):
                turn=tool_bridge._active[('alice',agent.agent_id)]
                provider.return_value=[{'group_id':'g1','title':'Renamed'}]
                update=tool_bridge.attach_runtime_context({'results':[{'content':'safe'}]},agent,turn)
                self.assertIn('Renamed',update['runtime_context'])
                self.assertNotIn('USER TEXT',update['runtime_context'])
                self.assertNotIn('runtime_context',tool_bridge.attach_runtime_context({'tools':[]},agent,turn))
                provider.return_value=[]
                revoked=tool_bridge.attach_runtime_context({'tools':[]},agent,turn)
                self.assertIn('此前提供的此项信息已撤销',revoked['runtime_context'])
            session.remember_turn(store,agent,prepared)
            current=store.require('alice',agent.agent_id)
            self.assertEqual(prepare(current).text,'USER TEXT')
            session.forget(store,current)
            reset=prepare(store.require('alice',agent.agent_id))
            self.assertIsNotNone(reset.identity)
            self.assertEqual(reset.dynamic_context['groups'],'')

    def test_internal_rebuilds_the_same_shared_identity_from_live_sources(self):
        from webot.engine import agent as internal
        with TemporaryDirectory() as tmp:
            root=Path(tmp); prompts=root/'data'/'prompts';prompts.mkdir(parents=True)
            (prompts/'base_system.txt').write_text('BASE {chat_rules}')
            (prompts/'conversation_rules.txt').write_text('RULE ONE')
            (prompts/'base_system_subagent.txt').write_text('SUB')
            (prompts/'system_trigger.txt').write_text('TRIGGER')
            engine=internal.TeamAgent.__new__(internal.TeamAgent)
            with patch.object(internal,'PROJECT_ROOT',root), \
                 patch.object(internal,'describe_session_workspace',return_value='WORKSPACE'), \
                 patch.object(internal,'build_user_profile_block',return_value='PROFILE'), \
                 patch.object(internal,'build_soul_prompt',return_value='SOUL'), \
                 patch.object(engine,'_get_internal_session_persona_prompt',return_value='PERSONA'):
                first=engine._build_live_system_prompt('alice','same-session',False)[0]
                self.assertIn('BASE RULE ONE',first)
                for part in ('PERSONA','PROFILE','SOUL','WORKSPACE'):self.assertIn(part,first)
                (prompts/'conversation_rules.txt').write_text('RULE TWO')
                current=engine._build_live_system_prompt('alice','same-session',False)[0]
                self.assertIn('BASE RULE TWO',current)
                self.assertNotIn('RULE ONE',current)

    def test_new_codex_session_reuses_catalog_without_other_sessions_selections(self):
        import json
        from external.acp_settings import native_options
        with TemporaryDirectory() as tmp:
            root=Path(tmp); records=root/'.acpx'/'sessions';records.mkdir(parents=True)
            store=AgentStore(root/'agents.db');agent=store.create('alice',driver=ACPX,config={'platform':'codex'})
            record={'name':'clawcross-alice-existing','cwd':str(root),'agent_command':'npx -y @agentclientprotocol/codex-acp@^1.1.5',
                    'acpx':{'config_options':[{'id':'model','currentValue':'other-model','options':[{'value':'model-a'}]}]}}
            path=records/'catalog.json';path.write_text(json.dumps(record))
            with patch('external.acp_settings.Path.home',return_value=root),patch('external.acpx._default_acpx_cwd',return_value=str(root)):
                options=native_options(agent)
                self.assertTrue(options[0]['provisional'])
                self.assertEqual(options[0]['currentValue'],'model-a')
                self.assertNotEqual(options[0]['currentValue'],'other-model')
                record['name']=session.runtime_session(agent);path.write_text(json.dumps(record))
                self.assertEqual(native_options(agent)[0]['currentValue'],'other-model')

    def test_obsolete_dynamic_sections_are_explicitly_revoked(self):
        self.assertIn('撤销',render_section_updates({'obsolete':'OLD'},{}))
