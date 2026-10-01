import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'backend'))
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from agents.messages import AgentMessage
from agents.store import AgentStore, HTTP
from external.session import prepare_turn, remember_turn
from common.conversation_context import normalize_group_metadata
from webot.context import render_group_context


class GroupContextTests(unittest.TestCase):
    def test_metadata_excludes_dialogue_and_deduplicates_latest_group(self):
        groups = [{"group_id": "g1", "title": "old", "content": "secret", "summary": "digest"},
                  {"group_id": "g1", "title": "new", "messages": ["secret"],
                   "members": [{"name": "Alice", "kind": "human", "content": "secret"}]}]
        meta = normalize_group_metadata(groups)
        self.assertEqual(len(meta), 1)
        self.assertEqual(meta[0]['title'], 'new')
        self.assertNotIn('secret', str(meta))
        self.assertNotIn('digest', str(meta))

    def test_group_meta_survives_tool_round_and_clears_on_direct_user_turn(self):
        grouped = HumanMessage(content='inbox digest', additional_kwargs={
            'framework_groups': [{"group_id": "g1", "identity": "Builder"}]})
        history = [grouped, AIMessage(content=''), ToolMessage(content='result', tool_call_id='c1')]
        self.assertIn('Builder', render_group_context(history))
        history.append(HumanMessage(content='normal chat'))
        self.assertNotIn('Builder', render_group_context(history))
        self.assertIn('无。', render_group_context(history))

    def test_external_group_meta_is_incremental_and_revoked(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch('external.session.identity_sections', return_value={'base_rules': 'rules'}), \
                patch('webot.skills.build_user_skills_listing', return_value=''), \
                patch('webot.workflow_prompt.build_team_workflow_prompt', return_value=''):
            store = AgentStore(Path(tmp) / 'agents.db')
            agent = store.create('alice', driver=HTTP, config={'api_url': 'http://unused'})
            def turn(context):
                current = store.require('alice', agent.agent_id)
                return current, prepare_turn(current, AgentMessage(text='new input'), context=context,
                    mode=None, enabled_tools=None, response_format=None)
            context = {'groups': [{'group_id': 'g1', 'title': 'Group', 'content': 'SECRET'}]}
            current, first = turn(context)
            self.assertIn('【本轮 groups】', first.text)
            self.assertNotIn('SECRET', first.text)
            remember_turn(store, current, first)
            current, same = turn(context)
            self.assertEqual(same.text, 'new input')
            current, cleared = turn({})
            self.assertIn('【本轮 groups】\n此前提供的此项信息已撤销。', cleared.text)
            remember_turn(store, current, cleared)
            self.assertEqual(turn({})[1].text, 'new input')
