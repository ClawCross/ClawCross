import sys as _sys
import os as _os
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

"""
MCP Tool Server: Session Management

Exposes tools for the Agent to be aware of its own session context
and query existing sessions:
  - list_sessions: Lists all sessions for the current user with summaries

Runs as a stdio MCP server, just like the other mcp_*.py tools.
"""

import json
from utils.mcp_tool_docs import DocumentedFastMCP as FastMCP
from utils.checkpoint_paths import DEFAULT_CHECKPOINT_DB_DIR, checkpoint_store_exists
from utils.checkpoint_repository import (
    list_thread_ids_by_prefix,
)
from utils.context_store import ContextStore

mcp = FastMCP("Session Management")

# Checkpoint DB root — same as mainagent uses
_DB_PATH = str(DEFAULT_CHECKPOINT_DB_DIR)

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
