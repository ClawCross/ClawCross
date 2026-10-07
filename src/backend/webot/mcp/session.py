import sys as _sys
import os as _os
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

"""
MCP Tool Server: Session Management

Exposes tools for the Agent to be aware of its own session context
and query existing sessions:
  - set_session_title: Names the work this session is doing (its sidebar title)
  - list_sessions: Lists all sessions for the current user with summaries

Runs as a stdio MCP server, just like the other mcp_*.py tools.
"""

import json
import os
from dotenv import dotenv_values
from agents.client import AgentClient
from common.runtime_paths import ENV_FILE
from webot.mcp_tool_docs import DocumentedFastMCP as FastMCP

mcp = FastMCP("Session Management")

@mcp.tool()
async def fork_session(username: str = "", current_session_id: str = "", name: str = "", reason: str = "") -> str:
    """Create a new Agent from this session's completed conversation turns.

    The new Agent has its own context, inbox, approvals, permits, and runs.
    It shares the user's normal workspace and tool policy.

    :param username: Current user, injected by the runtime
    :param current_session_id: Current session, injected by the runtime
    :param name: Optional name for the new Agent
    :param reason: Short reason for exploring a separate branch
    """
    if not username or not current_session_id:
        return "❌ 无法获取当前会话。"
    try:
        token = os.getenv("INTERNAL_TOKEN", "").strip() or str(dotenv_values(ENV_FILE).get("INTERNAL_TOKEN") or "")
        result = await AgentClient(username, internal_token=token).fork(current_session_id, name=name, reason=reason)
    except Exception as exc:
        return f"❌ 创建分支失败: {exc}"
    return json.dumps(result, ensure_ascii=False)

@mcp.tool()
async def set_session_title(title: str, username: str = "", source_session: str = "") -> str:
    """Set the title this conversation shows in the sidebar: a short phrase naming the
    work, e.g. "排查登录超时" or "Q3 sales report". Call it once the work is clear and
    again when it changes; it replaces any earlier title, including one the user set.
    Your own agent name stays as it is.

    :param title: The work title, one line, at most 30 characters
    :param username: Current user, injected by the runtime
    :param source_session: Current session, injected by the runtime
    """
    if not username or not source_session:
        return "❌ 无法获取当前会话。"
    title = " ".join(str(title or "").split())
    if not title:
        return "❌ title 不能为空。"
    try:
        token = os.getenv("INTERNAL_TOKEN", "").strip() or str(dotenv_values(ENV_FILE).get("INTERNAL_TOKEN") or "")
        agent = await AgentClient(username, internal_token=token).update(source_session, settings={"title": title})
    except Exception as exc:
        return f"❌ 设置标题失败: {exc}"
    return f"✅ 会话标题：{agent['settings'].get('title', title)}"


@mcp.tool()
async def list_sessions(username: str = "", current_session_id: str = "", query: str = "", limit: int = 30, platform: str = "") -> str:
    """List or search the user's registered Agent/session IDs, including new Agents with no history.
    Use get_session_details(target_session=ID) after choosing a result. Reads the Agent registry;
    does not start external CLIs or replay conversations. For content search use search_sessions.

    :param username: Runtime user identity; injected.
    :param current_session_id: Current Agent; injected.
    :param query: Match name, ID, title or Team name; empty lists recent Agents.
    :param limit: Maximum results, 1 to 100.
    :param platform: Optional runtime platform filter, such as webot, codex or claude.
    """
    from agents.store import get_store, canonical_platform
    if not username:
        return json.dumps({"ok":False,"error":"Missing user identity"})
    keyword=query.strip().casefold()
    rows=[]
    for agent in sorted(get_store().list(username),key=lambda agent:agent.updated_at,reverse=True):
        if platform and agent.platform!=canonical_platform(platform):continue
        title=str(agent.config.get('title') or '')
        if keyword and keyword not in ' '.join([agent.agent_id,agent.name,title,*agent.teams]).casefold():continue
        rows.append({'session_id':agent.agent_id,'agent_id':agent.agent_id,'name':agent.name,'title':title,
                     'platform':agent.platform,'teams':agent.teams,'updated_at':agent.updated_at,
                     'is_current':agent.agent_id==current_session_id})
        if len(rows)>=max(1,min(100,limit)):break
    return json.dumps({'ok':True,'sessions':rows},ensure_ascii=False)


@mcp.tool()
async def get_session_details(username: str, target_session: str, history_limit: int = 0, source_session: str = "") -> str:
    """查看 Agent/会话详情与近期历史：身份、模式、工具、沙盒和工作区；不启动外部 CLI。
    Read one owned Agent/session's identity, mode, tools, sandbox and workspace settings.
    Does not require this Agent to join the target's Team. Never returns API keys or connector secrets.
    history_limit=0 returns metadata only; set 1 to 80 to retrieve recent ClawCross history.
    Reads stored state without initializing a native CLI.

    :param username: Runtime user identity; injected.
    :param target_session: Exact Agent/session ID returned by list_sessions or search_sessions.
    :param history_limit: Optional recent history count, 0 to 80.
    :param source_session: Calling Agent; injected.
    """
    from agents.store import get_store
    from webot.runtime import effective_session_mode
    from webot.runtime_settings import get_runtime_settings
    from webot.workspace import workspace_card
    from webot.mcp.management import local_request, result
    try:
        if not 0<=history_limit<=80:raise ValueError('history_limit must be 0 to 80')
        agent=get_store().require(username,target_session)
        settings=get_runtime_settings(username,target_session)
        tools=((agent.config.get('meta') or {}).get('acp') or {}).get('tools') if agent.platform!='webot' else agent.config.get('tools')
        payload={'agent_id':agent.agent_id,'session_id':agent.agent_id,'name':agent.name,'platform':agent.platform,
                 'title':agent.config.get('title',''),'persona':str(agent.config.get('persona') or '')[:4000],
                 'teams':agent.teams,'tools':tools,'mode':effective_session_mode(username,target_session),
                 'model':(agent.config.get('llm') or {}).get('model') or agent.config.get('model',''),
                 'sandbox':{'backend':settings.approval.command_sandbox,'security':settings.approval.sandbox_security,
                            'allowed_domains':settings.approval.sandbox_allowed_domains},
                 'workspace':workspace_card(username,target_session,agent_config=agent.config),
                 'created_at':agent.created_at,'updated_at':agent.updated_at}
        if history_limit:payload['history']=await local_request(username,'GET','/v1/agents/'+target_session+'/history?limit='+str(history_limit))
        return result(payload)
    except Exception as error:return result(error=str(error))

if __name__ == "__main__":
    mcp.run(transport="stdio")
