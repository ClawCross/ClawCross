"""Workspace changes and resets use the existing external context state machine."""
import json
from unittest.mock import patch

from agents.messages import AgentMessage
from agents.store import AgentStore, ACPX
from external import session


def test_workspace_delta_and_reset_preserve_native_cwd(tmp_path):
    store = AgentStore(tmp_path / 'agents.db')
    first_root = tmp_path / 'first'; first_root.mkdir()
    next_root = tmp_path / 'next'; next_root.mkdir()
    agent = store.create('alice', driver=ACPX, config={'platform':'codex', 'workspace_root':str(first_root)})
    def prepare(current):
        return session.prepare_turn(current, AgentMessage(text='USER'), context={}, mode=None,
                                    enabled_tools=None, response_format=None)
    with patch('webot.skills.build_user_skills_listing', return_value=''), \
         patch('common.conversation_context.group_memberships', return_value=[]):
        first = prepare(agent)
        assert '【本轮 workspace】' in first.text
        assert json.loads(first.dynamic_context['workspace'])['cwd'] == str(first_root)
        session.remember(store, agent, acp_cwd=str(first_root))
        session.remember_turn(store, agent, first)
        current = store.require('alice', agent.agent_id)
        assert prepare(current).text == 'USER'
        current = store.update('alice', agent.agent_id, config={**current.config, 'workspace_root':str(next_root)})
        changed = prepare(current)
        assert '【本轮 workspace】' in changed.text
        workspace = json.loads(changed.dynamic_context['workspace'])
        assert workspace['cwd'] == str(next_root)
        assert workspace['native_cwd'] == str(first_root)
        session.remember_turn(store, current, changed)
        session.forget(store, store.require('alice', agent.agent_id))
        reset = prepare(store.require('alice', agent.agent_id))
        assert '【本轮 workspace】' in reset.text
        assert json.loads(reset.dynamic_context['workspace'])['native_cwd'] == str(first_root)
