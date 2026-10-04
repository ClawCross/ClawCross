"""Workspace changes and resets use the existing external context state machine."""
import json
from unittest.mock import patch

from agents.messages import AgentMessage
from agents.store import AgentStore, ACPX
from external import session, tool_bridge


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


def test_file_changes_arrive_in_current_tool_result_without_new_user_turn(tmp_path):
    root = tmp_path / 'workspace'; root.mkdir()
    existing = root / 'existing.txt'; existing.write_text('CONTENT_MUST_NOT_ENTER_DYNAMIC_BLOCK')
    removed = root / 'removed.txt'; removed.write_text('old')
    store = AgentStore(tmp_path / 'agents.db')
    agent = store.create('alice', driver=ACPX, config={'platform':'codex', 'workspace_root':str(root)})
    msg = AgentMessage(text='Continue current task')
    with patch('webot.skills.build_user_skills_listing', return_value=''), \
         patch('common.conversation_context.group_memberships', return_value=[]):
        prepared = session.prepare_turn(agent, msg, context={}, mode='auto', enabled_tools=None, response_format=None)
        assert 'CONTENT_MUST_NOT_ENTER_DYNAMIC_BLOCK' not in prepared.text
        turn = {'dynamic_context': prepared.dynamic_context, 'message': msg,
                'context': {}, 'mode': 'auto', 'enabled_tools': None, 'response_format': None}
        assert 'runtime_context' not in tool_bridge.attach_runtime_context({'results':[]}, agent, turn)
        existing.write_text('changed contents')
        removed.unlink()
        (root / 'new.txt').write_text('new contents')
        update = tool_bridge.attach_runtime_context({'results':[{'content':'tool completed'}]}, agent, turn)
        assert '【本轮 workspace】' in update['runtime_context']
        changes = json.loads(turn['dynamic_context']['workspace'])['files']['recent_changes']
        assert changes == {'added':['new.txt'], 'modified':['existing.txt'], 'removed':['removed.txt']}
        assert 'runtime_context' not in tool_bridge.attach_runtime_context({'tools':[]}, agent, turn)
        # An unchanged observation must not erase the change before a failed delivery retries.
        before = turn['dynamic_context']['workspace']
        assert session.workspace_context(agent) == before


def test_internal_workspace_description_refreshes_files_on_each_observation(tmp_path):
    from webot.workspace import describe_session_workspace
    store = AgentStore(tmp_path / 'agents.db')
    root = tmp_path / 'workspace'; root.mkdir()
    store.create('alice', driver='webot', config={'workspace_root':str(root)}, agent_id='internal')
    with patch('agents.store.get_store', return_value=store):
        before = describe_session_workspace('alice', 'internal')
        (root / 'result.txt').write_text('result')
        after = describe_session_workspace('alice', 'internal')
    assert before != after
    assert 'result.txt' in after


def test_partial_observation_never_reports_unseen_files_as_deleted(tmp_path):
    from webot import workspace_state
    root = tmp_path / 'workspace'; root.mkdir()
    (root / 'first.txt').write_text('first')
    baseline = workspace_state.observe_workspace(root, user_id='alice', session_id='bounded')
    with patch.object(workspace_state, 'MAX_ENTRIES', 1):
        (root / 'other.txt').write_text('other')
        limited = workspace_state.observe_workspace(root, user_id='alice', session_id='bounded')
    assert baseline['limited'] is False
    assert limited['limited'] is True
    assert limited['recent_changes']['removed'] == []
