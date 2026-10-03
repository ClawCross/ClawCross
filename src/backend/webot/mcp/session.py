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
from webot.checkpoint_paths import DEFAULT_CHECKPOINT_DB_DIR, checkpoint_store_exists
from webot.checkpoint_repository import (
    list_thread_ids_by_prefix,
)
from webot.context_store import ContextStore

mcp = FastMCP("Session Management")

# Checkpoint DB root — same as mainagent uses
_DB_PATH = str(DEFAULT_CHECKPOINT_DB_DIR)


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
async def list_sessions(
    username: str = "",
    current_session_id: str = "",
) -> str:
    """
    List the current user's conversation sessions — ID, title (first user
    message), last message preview, and message count — with the current
    session marked. Use it to pick a target session for callbacks
    (notify_session) or cross-session workflows.
    """
    if not username:
        return "❌ 无法获取用户信息"

    if not checkpoint_store_exists(_DB_PATH):
        return "❌ 对话记录数据库不存在"

    prefix = f"{username}#"
    sessions = []

    try:
        rows = await list_thread_ids_by_prefix(_DB_PATH, prefix)
        for thread_id in rows:
            sid = thread_id[len(prefix):]

            messages = await ContextStore(_DB_PATH).load_context(thread_id)
            if not messages:
                continue

            first_human = ""
            last_human = ""
            msg_count = 0

            for m in messages:
                # After proper deserialization, messages are LangChain objects
                # Check type by class name (HumanMessage, AIMessage, etc.)
                type_name = type(m).__name__

                if type_name != "HumanMessage":
                    continue

                content = getattr(m, "content", "")
                if not content:
                    continue

                # Handle multimodal content (list of parts)
                if isinstance(content, list):
                    text_parts = []
                    for p in content:
                        if isinstance(p, dict) and p.get("type") == "text":
                            text_parts.append(p.get("text", ""))
                    content = " ".join(text_parts) or "(多媒体消息)"
                elif not isinstance(content, str):
                    content = str(content)

                # Skip system trigger messages
                if content.startswith("[系统触发]"):
                    continue

                msg_count += 1
                if not first_human:
                    first_human = content[:80]
                last_human = content[:80]

            if not first_human:
                continue  # Skip empty or system-only sessions

            sessions.append({
                "session_id": sid,
                "title": first_human,
                "last_message": last_human,
                "message_count": msg_count,
            })

    except Exception as e:
        return f"❌ 查询会话列表失败: {str(e)}"

    current = current_session_id or "(unknown)"
    if not sessions:
        return f"📭 当前没有任何对话记录。当前会话: {current}"

    lines = [f"📋 用户 {username} 的会话列表（共 {len(sessions)} 个，当前会话: {current}）:\n"]
    for s in sessions:
        marker = "（当前）" if s["session_id"] == current_session_id else ""
        lines.append(
            f"  🔹 session_id: \"{s['session_id']}\"{marker}\n"
            f"     标题: {s['title']}\n"
            f"     最新消息: {s['last_message']}\n"
            f"     消息数: {s['message_count']}\n"
        )
    return "\n".join(lines)

if __name__ == "__main__":
    mcp.run(transport="stdio")
