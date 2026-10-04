"""Live prompt edits take effect without clearing either kind of session."""

import json
from unittest import mock

import pytest

from agents.messages import AgentMessage
from agents.store import AgentStore, HTTP
from external import session
from webot.engine import agent as internal


@pytest.fixture
def prompt_sources(tmp_path, monkeypatch):
    prompts = tmp_path / "data/prompts"
    prompts.mkdir(parents=True)
    for name, value in {
        "base_system.txt": "BASE-1 {chat_rules}", "conversation_rules.txt": "RULES-1",
        "base_system_subagent.txt": "SUB-1", "system_trigger.txt": "TRIGGER {original_text}",
        "external_agent_system.txt": "EXTERNAL-1",
    }.items():
        (prompts / name).write_text(value)
    monkeypatch.setattr(internal, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(session, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("webot.skills.build_user_skills_listing", lambda *a, **kw: "")
    monkeypatch.setattr("webot.workflow_prompt.build_team_workflow_prompt", lambda *a, **kw: "")
    monkeypatch.setattr("webot.skills.build_user_profile_block", lambda *a: "PROFILE-1")
    monkeypatch.setattr("webot.soul.build_soul_prompt", lambda *a: "SOUL-1")
    return prompts


def prepared(agent):
    return session.prepare_turn(agent, AgentMessage(text="next user input"), context={},
                                mode=None, enabled_tools=None, response_format=None)


def test_internal_rereads_files_persona_profile_and_soul(prompt_sources, monkeypatch):
    engine = internal.TeamAgent.__new__(internal.TeamAgent)
    monkeypatch.setattr(engine, "_get_internal_session_persona_prompt", lambda *a: "PERSONA-1")
    monkeypatch.setattr(internal, "describe_session_workspace", lambda *a: "WORKSPACE")
    monkeypatch.setattr(internal, "build_user_profile_block", lambda *a: "PROFILE-1")
    monkeypatch.setattr(internal, "build_soul_prompt", lambda *a: "SOUL-1")
    first, _ = engine._build_live_system_prompt("alice", "same-session", False)
    assert engine._build_live_system_prompt("alice", "same-session", False)[0] == first
    (prompt_sources / "base_system.txt").write_text("BASE-2 {chat_rules}")
    (prompt_sources / "conversation_rules.txt").write_text("RULES-2")
    monkeypatch.setattr(engine, "_get_internal_session_persona_prompt", lambda *a: "PERSONA-2")
    monkeypatch.setattr(internal, "build_user_profile_block", lambda *a: "PROFILE-2")
    monkeypatch.setattr(internal, "build_soul_prompt", lambda *a: "SOUL-2")
    updated, _ = engine._build_live_system_prompt("alice", "same-session", False)
    for part in ("BASE", "RULES", "PERSONA", "PROFILE", "SOUL"):
        assert f"{part}-2" in updated
        assert f"{part}-1" not in updated
    assert "WORKSPACE" not in updated  # current workspace belongs in the dynamic block


def test_external_identity_uses_the_dynamic_snapshot_and_retries_until_delivered(prompt_sources, tmp_path):
    store = AgentStore(tmp_path / "agents.db")
    agent = store.create("alice", driver=HTTP, config={"api_url": "http://unused"})
    key = session.runtime_session(agent)
    session.remember_turn(store, agent, prepared(agent))
    current = store.require("alice", agent.agent_id)
    assert prepared(current).text == "next user input"
    (prompt_sources / "conversation_rules.txt").write_text("RULES-2")
    pending = prepared(current)
    assert '【本轮 identity_base_rules】' in pending.text
    assert 'RULES-2' in pending.text
    assert '系统提示词补丁' not in pending.text
    assert 'EXTERNAL-1' not in pending.text
    assert prepared(store.require("alice", agent.agent_id)).text == pending.text
    session.remember_turn(store, current, pending)
    delivered = store.require("alice", agent.agent_id)
    assert prepared(delivered).text == "next user input"
    assert session.runtime_session(delivered) == key
    assert 'RULES-2' in delivered.runtime['dynamic_context']['identity_base_rules']


def test_internal_subagent_template_is_live(prompt_sources):
    engine = internal.TeamAgent.__new__(internal.TeamAgent)
    with mock.patch.object(internal, "describe_session_workspace", return_value="WORKSPACE"):
        first, _ = engine._build_live_system_prompt("alice", "oasis_test", True)
        (prompt_sources / "base_system_subagent.txt").write_text("SUB-2")
        updated, _ = engine._build_live_system_prompt("alice", "oasis_test", True)
    assert "SUB-1" in first
    assert "SUB-2" in updated and "SUB-1" not in updated


def test_external_revokes_removed_persona_and_soul(prompt_sources, tmp_path):
    store = AgentStore(tmp_path / "agents.db")
    agent = store.create("alice", driver=HTTP, config={"api_url": "http://unused"})
    with mock.patch("webot.profiles.frame_session_identity", return_value="PERSONA-1"):
        session.remember_turn(store, agent, prepared(agent))
    current = store.require("alice", agent.agent_id)
    with mock.patch("webot.profiles.frame_session_identity", return_value=""), \
            mock.patch("webot.soul.build_soul_prompt", return_value=""):
        change = prepared(current)
    assert '【本轮 identity_persona】\n此前提供的此项信息已撤销。' in change.text
    assert '【本轮 identity_soul】\n此前提供的此项信息已撤销。' in change.text
    assert "PERSONA-1" not in change.text


def test_external_legacy_snapshot_migrates_without_resending_unchanged_identity(prompt_sources, tmp_path):
    store = AgentStore(tmp_path / "agents.db")
    agent = store.create("alice", driver=HTTP, config={"api_url": "http://unused"})
    store.set_runtime("alice", agent.agent_id, {"identity_sections": session.identity_sections(agent), "last_used_at": 1})
    (prompt_sources / "external_agent_system.txt").write_text("EXTERNAL-2")
    change = prepared(store.require("alice", agent.agent_id))
    assert change.identity is None
    assert '【本轮 identity_external_rules】' in change.text
    assert 'EXTERNAL-2' in change.text
    assert 'EXTERNAL-1' not in change.text
    assert 'PROFILE-1' not in change.text
    assert '系统提示词补丁' not in change.text
