"""New tools use the established Agent tool table, without hidden exclusions."""

from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/backend'))

from webot.engine.agent import available_internal_tool_names
from webot import runtime_store
from webot.policy import WeBotToolPolicy, evaluate_tool_policy
from webot.tool_capabilities import MANAGEMENT_TOOLS


def test_null_means_all_empty_means_none_and_explicit_lists_select_tools(monkeypatch,tmp_path):
    monkeypatch.setattr(runtime_store,'AGENT_RUNTIME_DB_DIR',tmp_path/'runtime')
    tools=[SimpleNamespace(name=name) for name in ('read_file','call_llm_api',*MANAGEMENT_TOOLS)]
    for configured,expected in ((None,{'read_file',*MANAGEMENT_TOOLS}),([],set()),(['read_file','manage_team'],{'read_file','manage_team'})):
        assert available_internal_tool_names(tools,user_id='alice',session_id='own',state={'session_mode':'auto'},
            find_session_meta=lambda *_:{'tools':configured})==expected


def test_a_child_cannot_grant_itself_a_tool_its_parent_did_not_enable(monkeypatch,tmp_path):
    monkeypatch.setattr(runtime_store,'AGENT_RUNTIME_DB_DIR',tmp_path/'runtime')
    monkeypatch.setattr('webot.engine.agent.effective_session_mode',lambda *_:'auto')
    monkeypatch.setattr('webot.subagent_permissions.parent_sessions',lambda *_:['parent'])
    tools=[SimpleNamespace(name=name) for name in ('read_file','manage_team')]
    metadata=lambda _owner,agent:{'tools':['read_file','manage_team'] if agent=='child' else ['read_file']}
    assert available_internal_tool_names(tools,user_id='alice',session_id='child',state={},find_session_meta=metadata)=={'read_file'}


def test_privileged_tools_require_review_even_under_the_default_allow_policy():
    for name in MANAGEMENT_TOOLS:
        assert evaluate_tool_policy(WeBotToolPolicy(),name,{}).requires_approval
