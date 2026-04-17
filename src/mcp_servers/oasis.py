import sys as _sys
import os as _os
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

"""
MCP Tool Server: OASIS Forum

Exposes tools for the user's Agent to interact with the OASIS discussion forum:
  - list_oasis_experts: List all available expert personas (public + user custom)
  - add_oasis_expert / update_oasis_expert / delete_oasis_expert: CRUD for expert personas
  - list_oasis_sessions: List oasis-managed sessions (containing #oasis# in session_id)
    by scanning the Agent checkpoint DB — no separate storage needed
  - start_new_oasis: Submit a discussion — supports direct LLM experts and session-backed experts
  - check_oasis_discussion / cancel_oasis_discussion: Monitor or cancel a discussion
  - list_oasis_topics: List all discussion topics

Runs as a stdio MCP server, just like the other mcp_*.py tools.
"""

import json
import os
import re

import httpx
import aiosqlite
import yaml as _yaml
from mcp.server.fastmcp import FastMCP
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

mcp = FastMCP("OASIS Forum")

OASIS_BASE_URL = os.getenv("OASIS_BASE_URL", "http://127.0.0.1:51202")
_FALLBACK_USER = os.getenv("MCP_OASIS_USER", "agent_user")
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_CONN_ERR = "❌ 无法连接 OASIS 论坛服务器。请确认 OASIS 服务已启动 (端口 51202)。"


def _workflow_python_dir(user_id: str, team: str = "") -> str:
    if team:
        return os.path.join(_PROJECT_ROOT, "data", "user_files", user_id, "teams", team, "oasis", "python")
    return os.path.join(_PROJECT_ROOT, "data", "user_files", user_id, "oasis", "python")


def resolve_python_workflow_path(user_id: str, python_file: str, team: str = "") -> tuple[str | None, str | None]:
    if not python_file:
        return None, "未提供 python workflow 文件名"
    target_name = python_file if python_file.endswith(".py") else f"{python_file}.py"
    matches: list[tuple[str, str]] = []
    user_root = os.path.join(_PROJECT_ROOT, "data", "user_files", user_id)
    if team:
        search_dirs = [("team", team, _workflow_python_dir(user_id, team))]
    else:
        search_dirs = [("personal", "", _workflow_python_dir(user_id, ""))]
        teams_root = os.path.join(user_root, "teams")
        if os.path.isdir(teams_root):
            for team_name in sorted(os.listdir(teams_root)):
                team_dir = os.path.join(teams_root, team_name)
                if os.path.isdir(team_dir):
                    search_dirs.append(("team", team_name, _workflow_python_dir(user_id, team_name)))
    for scope, team_name, base_dir in search_dirs:
        path = os.path.join(base_dir, target_name)
        if os.path.isfile(path):
            label = f"team:{team_name}" if scope == "team" else "personal"
            matches.append((label, path))
    if not matches:
        return None, f"未找到 python workflow 文件: {target_name}"
    if len(matches) > 1:
        where = ", ".join(label for label, _ in matches)
        return None, f"找到多个同名 python workflow: {target_name}（{where}），请指定 team"
    return matches[0][1], None

# Checkpoint DB (same as agent.py / mcp_session.py)
_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "agent_memory.db",
)
_serde = JsonPlusSerializer()

# ======================================================================
# Expert persona management tools
# ======================================================================

@mcp.tool()
async def list_oasis_experts(username: str = "") -> str:
    """
    List all available expert personas on the OASIS forum.
    Shows both public (built-in) experts and the current user's custom experts.
    Call this BEFORE start_new_oasis to see which experts can participate.

    Args:
        username: (auto-injected) current user identity; do NOT set manually

    Returns:
        Formatted list of experts with their tags, personas, and source (public/custom)
    """
    effective_user = username or _FALLBACK_USER
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{OASIS_BASE_URL}/experts",
                params={"user_id": effective_user},
            )
            if resp.status_code != 200:
                return f"❌ 查询失败: {resp.text}"

            experts = resp.json().get("experts", [])
            if not experts:
                return "📭 暂无可用专家"

            public = [e for e in experts if e.get("source") == "public"]
            agency = [e for e in experts if e.get("source") == "agency"]
            custom = [e for e in experts if e.get("source") == "custom"]

            lines = [f"🏛️ OASIS 可用专家 - 共 {len(experts)} 位\n"]

            if public:
                lines.append(f"📋 公共专家 ({len(public)} 位):")
                for e in public:
                    persona_preview = e["persona"][:60] + "..." if len(e["persona"]) > 60 else e["persona"]
                    lines.append(f"  • {e['name']} (tag: \"{e['tag']}\") — {persona_preview}")

            if agency:
                lines.append(f"\n🌐 Agency 专业专家库 ({len(agency)} 位):")
                # 按分类分组展示
                from collections import defaultdict
                by_cat = defaultdict(list)
                for e in agency:
                    cat = e.get("category", "other")
                    by_cat[cat].append(e)
                cat_labels = {
                    "design": "🎨 设计", "engineering": "⚙️ 工程",
                    "marketing": "📢 营销", "product": "📦 产品",
                    "project-management": "📋 项目管理",
                    "spatial-computing": "🥽 空间计算",
                    "specialized": "🔬 专项", "support": "🛠️ 支持",
                    "testing": "🧪 测试",
                }
                for cat, items in sorted(by_cat.items()):
                    label = cat_labels.get(cat, cat)
                    lines.append(f"  {label} ({len(items)} 位):")
                    for e in items:
                        desc = e.get("description", "")
                        desc_preview = desc[:50] + "..." if len(desc) > 50 else desc
                        lines.append(f"    • {e['name']} (tag: \"{e['tag']}\") — {desc_preview}")

            if custom:
                lines.append(f"\n🔧 自定义专家 ({len(custom)} 位):")
                for e in custom:
                    persona_preview = e["persona"][:60] + "..." if len(e["persona"]) > 60 else e["persona"]
                    lines.append(f"  • {e['name']} (tag: \"{e['tag']}\") — {persona_preview}")

            lines.append(
                "\n💡 在 schedule_yaml 中使用 expert 的 tag 来指定参与者。"
                "\n   三种格式:"
                "\n   • \"tag#temp#N\"         — 直连LLM，无状态"
                "\n   • \"tag#oasis#name\"     — 内部session agent，按name查找"
                "\n   • \"#oasis#name\"        — 内部session agent（无tag）"
                "\n   • \"tag#ext#id\"         — 外部API（DeepSeek/GPT-4等）"
            )
            return "\n".join(lines)

    except httpx.ConnectError:
        return _CONN_ERR
    except Exception as e:
        return f"❌ 查询异常: {str(e)}"

@mcp.tool()
async def add_oasis_expert(
    username: str,
    name: str,
    tag: str,
    persona: str,
    temperature: float = 0.7,
) -> str:
    """
    Create a custom expert persona for the current user.

    Args:
        username: (auto-injected) current user identity; do NOT set manually
        name: Expert display name (e.g. "产品经理", "前端架构师")
        tag: Unique identifier tag (e.g. "pm", "frontend_arch")
        persona: Expert persona description
        temperature: LLM temperature (0.0-1.0, default 0.7)

    Returns:
        Confirmation with the created expert info
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{OASIS_BASE_URL}/experts/user",
                json={
                    "user_id": username,
                    "name": name,
                    "tag": tag,
                    "persona": persona,
                    "temperature": temperature,
                },
            )
            if resp.status_code != 200:
                return f"❌ 创建失败: {resp.json().get('detail', resp.text)}"

            expert = resp.json()["expert"]
            return (
                f"✅ 自定义专家已创建\n"
                f"  名称: {expert['name']}\n"
                f"  Tag: {expert['tag']}\n"
                f"  Persona: {expert['persona']}\n"
                f"  Temperature: {expert['temperature']}"
            )

    except httpx.ConnectError:
        return _CONN_ERR
    except Exception as e:
        return f"❌ 创建异常: {str(e)}"

@mcp.tool()
async def update_oasis_expert(
    username: str,
    tag: str,
    name: str = "",
    persona: str = "",
    temperature: float = -1,
) -> str:
    """
    Update an existing custom expert persona.

    Args:
        username: (auto-injected) current user identity; do NOT set manually
        tag: The tag of the custom expert to update
        name: New display name (leave empty to keep current)
        persona: New persona description (leave empty to keep current)
        temperature: New temperature (-1 = keep current)

    Returns:
        Confirmation with the updated expert info
    """
    try:
        body: dict = {"user_id": username}
        if name:
            body["name"] = name
        if persona:
            body["persona"] = persona
        if temperature >= 0:
            body["temperature"] = temperature

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.put(
                f"{OASIS_BASE_URL}/experts/user/{tag}",
                json=body,
            )
            if resp.status_code != 200:
                return f"❌ 更新失败: {resp.json().get('detail', resp.text)}"

            expert = resp.json()["expert"]
            return (
                f"✅ 自定义专家已更新\n"
                f"  名称: {expert['name']}\n"
                f"  Tag: {expert['tag']}\n"
                f"  Persona: {expert['persona']}\n"
                f"  Temperature: {expert['temperature']}"
            )

    except httpx.ConnectError:
        return _CONN_ERR
    except Exception as e:
        return f"❌ 更新异常: {str(e)}"

@mcp.tool()
async def delete_oasis_expert(username: str, tag: str) -> str:
    """
    Delete a custom expert persona.

    Args:
        username: (auto-injected) current user identity; do NOT set manually
        tag: The tag of the custom expert to delete

    Returns:
        Confirmation of deletion
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(
                f"{OASIS_BASE_URL}/experts/user/{tag}",
                params={"user_id": username},
            )
            if resp.status_code != 200:
                return f"❌ 删除失败: {resp.json().get('detail', resp.text)}"

            deleted = resp.json()["deleted"]
            return f"✅ 已删除自定义专家: {deleted['name']} (tag: \"{deleted['tag']}\")"

    except httpx.ConnectError:
        return _CONN_ERR
    except Exception as e:
        return f"❌ 删除异常: {str(e)}"

# ======================================================================
# Oasis session discovery (scans checkpoint DB for #oasis# sessions)
# ======================================================================

@mcp.tool()
async def list_oasis_sessions(username: str = "") -> str:
    """
    List all oasis-managed expert sessions for the current user.

    Internal session agents are configured in internal_agents.json with
    name→session_id mappings. In YAML, use "tag#oasis#name" or "#oasis#name"
    format — the engine resolves the name to the actual session_id.
    Append "#new" to force a brand-new session (resolved ID replaced with random UUID).

    Args:
        username: (auto-injected) current user identity; do NOT set manually

    Returns:
        Formatted list of oasis sessions with tag, session_id and message count
    """
    effective_user = username or _FALLBACK_USER
    # Prefer calling OASIS HTTP API so both MCP and curl can access sessions
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{OASIS_BASE_URL}/sessions/oasis", params={"user_id": effective_user})
            if resp.status_code != 200:
                return f"❌ 查询失败: {resp.text}"
            data = resp.json()
            sessions = data.get("sessions", [])

            if not sessions:
                return (
                    "📭 暂无 oasis 专家 session。\n\n"
                    "💡 在 schedule_yaml 中使用\n"
                    "   \"tag#oasis#name\" 或 \"#oasis#name\" 格式即可。\n"
                    "   agent name 会自动映射到对应的 session。\n"
                    "   加 \"#new\" 后缀可确保创建全新 session。"
                )

            lines = [f"🏛️ OASIS 专家 Sessions — 共 {len(sessions)} 个\n"]
            for s in sessions:
                lines.append(
                    f"  • Tag: {s.get('tag')}\n"
                    f"    Session ID: {s.get('session_id')}\n"
                    f"    消息数: {s.get('message_count')}"
                )

            lines.append(
                "\n💡 在 schedule_yaml 中使用 session_id 即可让这些专家参与讨论。"
                "\n   也可在 schedule_yaml 中精确指定发言顺序。"
            )
            return "\n".join(lines)
    except httpx.ConnectError:
        return _CONN_ERR
    except Exception as e:
        return f"❌ 查询失败: {e}"

# ======================================================================
# Discussion tools
# ======================================================================

@mcp.tool()
async def start_new_oasis(
    question: str,
    schedule_yaml: str = "",
    username: str = "",
    max_rounds: int = 5,
    schedule_file: str = "",
    python_file: str = "",
    notify_session: str = "",
    discussion: bool = False,
    team: str = "",
) -> str:
    """
    Submit a question or work task to the OASIS forum for multi-expert discussion or execution.
    Always runs in detach (async) mode — returns immediately with a topic_id.
    Use check_oasis_discussion(topic_id=...) to check progress and get the conclusion later.

    Two modes:
      - discussion=False (default): Execute mode. Agents run tasks sequentially/in parallel per workflow,
        no discussion/voting. Each agent receives the question + instruction + previous agents' outputs
        as context, executes its task, and returns results. Ideal for task automation (e.g. game control).
      - discussion=True: Forum discussion mode. Experts discuss, reply, vote in JSON format.
    
    Note: discussion can also be set in YAML via "discussion: true/false". 
    If not set here (default False), the YAML setting is used. Setting it here overrides the YAML.

    Workflow has two modes:
      - YAML mode: current OASIS graph engine
      - Python mode: provide python_file; script exports async run(ctx)

    In YAML mode, expert pool is built entirely from schedule YAML expert names.
    Either schedule_file or schedule_yaml must be provided.
    If both are provided, schedule_file takes priority.
    If python_file is provided, it takes priority over YAML mode.

    **Three Agent Types** (name must contain '#'; engine dispatches by format):

      Type 1 — Direct LLM (stateless, fast):
        "tag#temp#N"            → ExpertAgent. Stateless single-shot LLM call per round.
                                  tag maps to preset expert name/persona; N is instance number.
                                  Example: "creative#temp#1", "critical#temp#2"

      Type 2 — Internal Session Agent (stateful, has memory):
        "tag#oasis#name"        → SessionExpert. Resolves agent name to session_id via
                                  internal agent JSON (internal_agents.json). tag enables
                                  persona injection from presets.
                                  Example: "test#oasis#test1", "creative#oasis#my_agent"
        "#oasis#name"           → SessionExpert (no tag). Same name→session lookup,
                                  no persona injection unless auto-detected from JSON.
                                  Example: "#oasis#test1"

      Type 3 — External API (DeepSeek, GPT-4, Ollama, etc):
        "tag#ext#id"            → ExternalExpert. Calls any external OpenAI-compatible API directly.
                                  Does NOT go through the local agent. External service assumed stateful.
                                  Supports custom headers via YAML `headers` field.
                                  Example: "deepseek#ext#ds1"

    Session conventions:
      - Agent names are resolved to session_ids via internal agent JSON.
      - Append "#new" to force a brand-new session (resolved session_id replaced with random UUID):
          "tag#oasis#name#new"  → "#new" stripped, resolved session_id replaced with UUID

    For simple all-parallel with all preset experts, use:
      version: 1
      repeat: true
      plan:
        - all_experts: true

    Args:
        question: The question/topic to discuss or work task to assign
        schedule_yaml: YAML defining expert pool AND speaking order.
            Not needed if schedule_file is provided. If both given, schedule_file wins.

            Example:
              version: 1
              repeat: true
              plan:
                - expert: "creative#temp#1"
                  instruction: "请重点分析创新方向"
                - expert: "creative#oasis#ab12cd34"
                - expert: "creative#oasis#new#new"
                - parallel:
                    - expert: "critical#temp#2"
                      instruction: "从风险角度分析"
                    - "data#temp#3"
                - expert: "助手#default"
                - expert: "deepseek#ext#ds1"
                - all_experts: true
                - manual:
                    author: "主持人"
                    content: "请聚焦可行性"

            instruction 字段（可选）：给专家的专项指令，专家会在发言时重点关注该指令。
        username: (auto-injected) current user identity; do NOT set manually
        max_rounds: Maximum number of discussion rounds (1-20, default 5)
        schedule_file: Filename or path to a saved YAML workflow file. Short names (e.g. "review.yaml")
            are resolved under data/user_files/{user}/oasis/yaml/. Takes priority over schedule_yaml.
        notify_session: (auto-injected) Session ID for completion notification.
        discussion: If False (default), execute mode — agents just run tasks without discussion format.
            If True, forum discussion mode with JSON reply/vote.
            Can also be set in YAML via "discussion: true". When False (default), YAML setting is respected.
        team: Team name for scoped agent/expert storage. When provided, internal agents are loaded
            from the team directory, and team-specific custom experts (defined in the team page)
            take priority over public/agency experts for tag→persona resolution.

    Returns:
        The topic_id for later retrieval via check_oasis_discussion()
    """
    effective_user = username or _FALLBACK_USER

    if not python_file and not schedule_yaml and not schedule_file:
        return "❌ 必须提供 python_file 或 schedule_yaml 或 schedule_file（至少一个）。"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout=300.0)) as client:
            body: dict = {
                "question": question,
                "user_id": effective_user,
                "max_rounds": max_rounds,
            }
            # Only send discussion when explicitly set to True (discussion mode)
            # so YAML's own "discussion:" setting is respected by default
            if discussion:
                body["discussion"] = True
            else:
                body["discussion"] = False

            # Always detach mode — set callback for completion notification
            port = os.getenv("PORT_AGENT", "51200")
            body["callback_url"] = f"http://127.0.0.1:{port}/system_trigger"
            body["callback_session_id"] = notify_session or "default"

            if python_file:
                if not os.path.isabs(python_file):
                    resolved_path, resolve_error = resolve_python_workflow_path(
                        effective_user,
                        python_file,
                        team,
                    )
                    if resolve_error:
                        return f"❌ {resolve_error}"
                    python_file = resolved_path or python_file
                body["python_file"] = python_file
            elif schedule_file:
                if not os.path.isabs(schedule_file):
                    resolved_path, resolve_error = _resolve_workflow_path(
                        effective_user,
                        schedule_file,
                        team,
                    )
                    if resolve_error:
                        return f"❌ {resolve_error}"
                    schedule_file = resolved_path or schedule_file
                body["schedule_file"] = schedule_file
                # Do NOT send schedule_yaml when file is provided
            elif schedule_yaml:
                body["schedule_yaml"] = schedule_yaml

            # Pass team name for scoped agent storage
            if team:
                body["team"] = team

            resp = await client.post(
                f"{OASIS_BASE_URL}/topics",
                json=body,
            )
            if resp.status_code != 200:
                return f"❌ Failed to create topic: {resp.text}"

            topic_id = resp.json()["topic_id"]

            return (
                f"🏛️ OASIS 任务已提交\n"
                f"主题: {question[:80]}\n"
                f"Topic ID: {topic_id}\n\n"
                f"💡 使用 check_oasis_discussion(topic_id=\"{topic_id}\") 查看进展和结论。"
            )

    except httpx.ConnectError:
        return _CONN_ERR
    except Exception as e:
        return f"❌ 工具调用异常: {str(e)}"

@mcp.tool()
async def check_oasis_discussion(topic_id: str, username: str = "") -> str:
    """
    Check the current status of a discussion on the OASIS forum.

    Args:
        topic_id: The topic ID returned by start_new_oasis
        username: (auto-injected) current user identity; do NOT set manually

    Returns:
        Formatted discussion status and recent posts
    """
    effective_user = username or _FALLBACK_USER
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{OASIS_BASE_URL}/topics/{topic_id}",
                params={"user_id": effective_user},
            )

            if resp.status_code == 403:
                return f"❌ 无权查看此讨论: {topic_id}"
            if resp.status_code == 404:
                return f"❌ 未找到讨论主题: {topic_id}"
            if resp.status_code != 200:
                return f"❌ 查询失败: {resp.text}"

            data = resp.json()

            lines = [
                f"🏛️ OASIS 讨论详情",
                f"主题: {data['question']}",
                f"状态: {data['status']} ({data['current_round']}/{data['max_rounds']}轮)",
                f"帖子数: {len(data['posts'])}",
                "",
                "--- 最近帖子 ---",
            ]

            for p in data["posts"][-10:]:
                prefix = f"  ↳回复#{p['reply_to']}" if p.get("reply_to") else "📌"
                content_preview = p["content"][:150]
                if len(p["content"]) > 150:
                    content_preview += "..."
                lines.append(
                    f"{prefix} [#{p['id']}] {p['author']} "
                    f"(👍{p['upvotes']} 👎{p['downvotes']}): {content_preview}"
                )

            if data.get("conclusion"):
                lines.extend(["", "🏆 === 最终结论 ===", data["conclusion"]])
            elif data["status"] == "discussing":
                lines.extend(["", "⏳ 讨论进行中..."])

            return "\n".join(lines)

    except httpx.ConnectError:
        return _CONN_ERR
    except Exception as e:
        return f"❌ 查询异常: {str(e)}"

@mcp.tool()
async def cancel_oasis_discussion(topic_id: str, username: str = "") -> str:
    """
    Force-cancel a running OASIS discussion.

    Args:
        topic_id: The topic ID to cancel
        username: (auto-injected) current user identity; do NOT set manually

    Returns:
        Cancellation result
    """
    effective_user = username or _FALLBACK_USER
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(
                f"{OASIS_BASE_URL}/topics/{topic_id}",
                params={"user_id": effective_user},
            )

            if resp.status_code == 403:
                return f"❌ 无权取消此讨论: {topic_id}"
            if resp.status_code == 404:
                return f"❌ 未找到讨论主题: {topic_id}"
            if resp.status_code != 200:
                return f"❌ 取消失败: {resp.text}"

            data = resp.json()
            return f"🛑 讨论已终止\nTopic ID: {topic_id}\n状态: {data.get('status')}\n{data.get('message', '')}"

    except httpx.ConnectError:
        return _CONN_ERR
    except Exception as e:
        return f"❌ 取消异常: {str(e)}"

@mcp.tool()
async def list_oasis_topics(username: str = "") -> str:
    """
    List all discussion topics on the OASIS forum.

    Args:
        username: (auto-injected) current user identity; leave empty to list all.

    Returns:
        Formatted list of all discussion topics
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            effective_user = username or _FALLBACK_USER
            resp = await client.get(
                f"{OASIS_BASE_URL}/topics",
                params={"user_id": effective_user},
            )

            if resp.status_code != 200:
                return f"❌ 查询失败: {resp.text}"

            topics = resp.json()
            if not topics:
                return "📭 论坛暂无讨论主题"

            lines = [f"🏛️ OASIS 论坛 - 共 {len(topics)} 个主题\n"]
            for t in topics:
                status_icon = {
                    "pending": "⏳",
                    "discussing": "💬",
                    "concluded": "✅",
                    "error": "❌",
                }.get(t["status"], "❓")
                lines.append(
                    f"{status_icon} [{t['topic_id']}] {t['question'][:50]} "
                    f"| {t['status']} | {t['post_count']}帖 | {t['current_round']}/{t['max_rounds']}轮"
                )

            return "\n".join(lines)

    except httpx.ConnectError:
        return _CONN_ERR
    except Exception as e:
        return f"❌ 查询异常: {str(e)}"

# ======================================================================
# Workflow management
# ======================================================================

def _iter_workflow_dirs(user_id: str, team: str = "") -> list[tuple[str, str, str]]:
    """Return workflow directories to inspect for a user.

    Tuples are (scope, team_name, yaml_dir), where scope is "personal" or "team".
    When team is omitted, include the personal workflow directory plus all teams.
    """
    if not user_id:
        return []

    user_root = os.path.join(_PROJECT_ROOT, "data", "user_files", user_id)
    if team:
        return [("team", team, os.path.join(user_root, "teams", team, "oasis", "yaml"))]

    dirs: list[tuple[str, str, str]] = [
        ("personal", "", os.path.join(user_root, "oasis", "yaml"))
    ]
    teams_root = os.path.join(user_root, "teams")
    if os.path.isdir(teams_root):
        for team_name in sorted(os.listdir(teams_root)):
            team_dir = os.path.join(teams_root, team_name)
            if os.path.isdir(team_dir):
                dirs.append(("team", team_name, os.path.join(team_dir, "oasis", "yaml")))
    return dirs

def _resolve_workflow_path(user_id: str, schedule_file: str, team: str = "") -> tuple[str | None, str | None]:
    """Resolve a workflow filename to an absolute path for MCP-triggered posts.

    When team is omitted, search the personal workflow directory plus all teams.
    If duplicates are found, require the caller to specify team explicitly.
    """
    if not schedule_file:
        return None, "未提供 workflow 文件名"

    target_name = schedule_file if schedule_file.endswith((".yaml", ".yml")) else f"{schedule_file}.yaml"
    matches: list[tuple[str, str]] = []
    for scope, team_name, yaml_dir in _iter_workflow_dirs(user_id, team):
        path = os.path.join(yaml_dir, target_name)
        if os.path.isfile(path):
            label = f"team:{team_name}" if scope == "team" else "personal"
            matches.append((label, path))

    if not matches:
        return None, f"未找到 workflow 文件: {target_name}"
    if len(matches) > 1:
        where = ", ".join(label for label, _ in matches)
        return None, f"找到多个同名 workflow: {target_name}（{where}），请指定 team"
    return matches[0][1], None

@mcp.tool()
async def set_oasis_workflow(
    username: str = "",
    name: str = "",
    schedule_yaml: str = "",
    description: str = "",
    save_layout: bool = True,
    team: str = "",
) -> str:
    """
    Save a YAML workflow so it can be reused later via start_new_oasis(schedule_file="name.yaml").

    Workflows are stored under data/user_files/{user}/oasis/yaml/ (or teams/{team}/oasis/yaml/ when team is set).
    Use list_oasis_workflows to see saved workflows.

    By default, also generates and saves a visual layout for the orchestrator UI.

    Args:
        username: (auto-injected) current user identity; do NOT set manually
        name: Filename for the workflow (e.g. "code_review"). ".yaml" appended if missing.
        schedule_yaml: The full YAML content to save
        description: Optional one-line description (saved as comment at top of file)
        save_layout: Whether to also generate and save a visual layout (default True)
        team: Team name. When provided, workflow is saved under the team directory.

    Returns:
        Confirmation with the saved file path
    """
    effective_user = username or _FALLBACK_USER
    # Proxy to OASIS HTTP API
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            payload = {
                "user_id": effective_user,
                "name": name,
                "schedule_yaml": schedule_yaml,
                "description": description,
                "save_layout": save_layout,
                "team": team,
            }
            resp = await client.post(f"{OASIS_BASE_URL}/workflows", json=payload)
            if resp.status_code != 200:
                return f"❌ 保存失败: {resp.text}"
            data = resp.json()
            lines = ["✅ Workflow 已保存"]
            lines.append(f"  文件: {data.get('file')}")
            lines.append(f"  路径: {data.get('path')}")
            if data.get("layout"):
                lines.append(f"  📐 Layout: {data.get('layout')}")
            if data.get("layout_warning"):
                lines.append(f"  ⚠️ {data.get('layout_warning')}")
            lines.append(f"\n💡 使用方式: start_new_oasis(schedule_file=\"{data.get('file')}\", ...)")
            return "\n".join(lines)
    except httpx.ConnectError:
        return _CONN_ERR
    except Exception as e:
        return f"❌ 保存失败: {e}"

@mcp.tool()
async def list_oasis_workflows(username: str = "", team: str = "") -> str:
    """
    List all saved YAML workflows for the current user.

    Args:
        username: (auto-injected) current user identity; do NOT set manually
        team: Team name. When provided, lists workflows from the team directory.

    Returns:
        List of saved workflow files with preview
    """
    effective_user = username or _FALLBACK_USER
    try:
        items: list[dict] = []
        for scope, team_name, yaml_dir in _iter_workflow_dirs(effective_user, team):
            if not os.path.isdir(yaml_dir):
                continue
            files = sorted(f for f in os.listdir(yaml_dir) if f.endswith((".yaml", ".yml")))
            for fname in files:
                fpath = os.path.join(yaml_dir, fname)
                desc = ""
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        first = f.readline().strip()
                        if first.startswith("#"):
                            desc = first.lstrip("# ").strip()
                except Exception:
                    pass
                items.append({
                    "file": fname,
                    "description": desc,
                    "scope": scope,
                    "team": team_name,
                })

        if not items:
            return "📭 暂无保存的 workflow"

        lines = [f"📋 已保存的 OASIS Workflows — 共 {len(items)} 个\n"]
        for it in items:
            location = f"[team:{it['team']}]" if it["scope"] == "team" else "[personal]"
            desc = it.get("description", "")
            lines.append(f"  • {location} {it.get('file')}" + (f"  — {desc}" if desc else ""))
        if team:
            lines.append(f"\n💡 当前只显示 team=\"{team}\" 下的 workflows。")
        else:
            lines.append("\n💡 未指定 team，已展示个人目录和全部 team 的 workflows。")
        lines.append("💡 使用: start_new_oasis(schedule_file=\"文件名\", ...)")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 查询失败: {e}"


@mcp.tool()
async def list_oasis_agent_catalog(username: str = "", team: str = "") -> str:
    """
    List all callable agents for OASIS workflowpy under the current user/team.

    Includes temp experts, internal session agents, and external agents with
    their target id, tag, platform, connect_type, session default, and full persona.
    """
    effective_user = username or _FALLBACK_USER
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{OASIS_BASE_URL}/agents/catalog",
                params={"user_id": effective_user, "team": team},
            )
            if resp.status_code != 200:
                return f"❌ 查询失败: {resp.text}"
            items = resp.json().get("agents", [])
        if not items:
            return "📭 暂无可调用 agent"

        lines = [f"📋 OASIS Agent Catalog — 共 {len(items)} 个\n"]
        for item in items:
            lines.append(
                f"  • {item.get('id')}\n"
                f"    name={item.get('name')} | tag={item.get('tag') or '-'} | "
                f"kind={item.get('kind')} | platform={item.get('platform')} | "
                f"connect={item.get('connect_type')} | session={item.get('session') or '-'}"
            )
            persona = str(item.get("persona", "") or "").strip()
            if persona:
                lines.append(f"    persona: {persona}")
        if team:
            lines.append(f"\n💡 当前只显示 team=\"{team}\" 下的 agent。")
        else:
            lines.append("\n💡 未指定 team，显示个人作用域 agent。")
        return "\n".join(lines)
    except httpx.ConnectError:
        return _CONN_ERR
    except Exception as e:
        return f"❌ 查询失败: {e}"

# ======================================================================
# Public Network Info (tunnel / public domain)
# ======================================================================

@mcp.tool()
async def get_publicnet_info() -> str:
    """
    Get public network information — tunnel status, public domain URL, ports, etc.

    Use this to discover the public URL when the cloudflare tunnel is running,
    so you can share the link with the user (e.g. via Telegram).
    This does NOT read .env directly — it queries the OASIS server API.

    IMPORTANT: This is a READ-ONLY query tool. It does NOT start or download
    anything. Starting the tunnel or downloading cloudflared MUST only happen
    when the user EXPLICITLY requests it — never on the agent's own initiative.

    Returns:
        Human-readable public network info including tunnel status and public URL.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{OASIS_BASE_URL}/publicnet/info")
            if resp.status_code != 200:
                return f"❌ 查询失败: {resp.text}"
            data = resp.json()

        tunnel = data.get("tunnel", {})
        ports = data.get("ports", {})

        lines = ["📡 系统信息\n"]

        # Tunnel info
        if tunnel.get("running"):
            lines.append("🌐 公网隧道: ✅ 运行中")
            domain = tunnel.get("public_domain", "")
            if domain:
                lines.append(f"   公网地址: {domain}")
            else:
                lines.append("   ⏳ 公网地址尚未就绪")
            lines.append(f"   PID: {tunnel.get('pid')}")
        else:
            lines.append("🌐 公网隧道: ❌ 未运行")
            lines.append("   💡 可通过 selfskill/scripts/run.sh start-tunnel 启动")
            lines.append("   💡 或在前端 Settings 面板中点击「启动隧道」")

        # Ports
        lines.append(f"\n📌 端口:")
        lines.append(f"   前端: {ports.get('frontend', '?')}")
        lines.append(f"   OASIS: {ports.get('oasis', '?')}")

        return "\n".join(lines)
    except httpx.ConnectError:
        return _CONN_ERR
    except Exception as e:
        return f"❌ 查询失败: {e}"

# ======================================================================
# YAML → Layout conversion helpers
# ======================================================================

# Tag → display info mapping (same as visual/main.py)
_TAG_EMOJI = {
    "creative": "🎨", "critical": "🔍", "data": "📊", "synthesis": "🎯",
    "economist": "📈", "lawyer": "⚖️", "cost_controller": "💰",
    "revenue_planner": "📊", "entrepreneur": "🚀", "common_person": "🧑",
    "manual": "📝", "custom": "⭐",
}
_TAG_NAMES: dict[str, str] = {}

# Try to load names from preset experts JSON
_EXPERTS_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "prompts", "oasis_experts.json",
)
try:
    with open(_EXPERTS_JSON, "r", encoding="utf-8") as _ef:
        for _exp in json.load(_ef):
            _TAG_NAMES[_exp["tag"]] = _exp["name"]
except Exception:
    pass

def _parse_expert_name(raw: str) -> dict:
    """Parse a YAML expert name string into a layout node dict.

    Formats:
      tag#temp#N         → expert, instance=N
      tag#oasis#new      → expert (stateful, auto-create session)
      tag#oasis#<name>   → session_agent (name→session lookup, tag→persona)
      #oasis#<name>      → session_agent (name→session lookup, no tag)
      tag#ext#id         → external (external API agent)
    """
    parts = raw.split("#")
    tag = parts[0]

    if len(parts) >= 3 and parts[1] == "temp":
        inst = int(parts[2]) if parts[2].isdigit() else 1
        return {
            "type": "expert",
            "tag": tag,
            "name": _TAG_NAMES.get(tag, tag),
            "emoji": _TAG_EMOJI.get(tag, "⭐"),
            "temperature": 0.5,
            "instance": inst,
            "session_id": "",
        }

    if len(parts) >= 3 and parts[1] == "oasis":
        oasis_val = parts[2]
        # "tag#oasis#new" → stateful expert (auto-create new session)
        if oasis_val == "new":
            return {
                "type": "expert",
                "tag": tag,
                "name": _TAG_NAMES.get(tag, tag),
                "emoji": _TAG_EMOJI.get(tag, "⭐"),
                "temperature": 0.5,
                "instance": 1,
                "session_id": "",
                "stateful": True,
            }
        # "tag#oasis#<name>" or "#oasis#<name>" → session_agent (name-based)
        inst = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 1
        return {
            "type": "session_agent",
            "tag": tag or "",
            "name": oasis_val,
            "agent_name": oasis_val,
            "emoji": "🤖",
            "temperature": 0.5,
            "instance": inst,
            "session_id": "",
        }

    if len(parts) >= 3 and parts[1] == "ext":
        ext_id = parts[2]
        return {
            "type": "external",
            "tag": tag,
            "name": ext_id,
            "emoji": "🌐",
            "temperature": 0.5,
            "instance": 1,
            "session_id": "",
            "ext_id": ext_id,
        }

    # Unrecognized format — treat as unknown expert
    return {
        "type": "expert",
        "tag": tag or "custom",
        "name": tag or raw,
        "emoji": "❓",
        "temperature": 0.5,
        "instance": 1,
        "session_id": "",
    }

def _yaml_to_layout_data(yaml_str: str) -> dict:
    """Convert OASIS YAML schedule string to visual layout JSON.

    Pure deterministic transformation — no LLM needed.
    Nodes are auto-positioned left-to-right (sequential) / top-to-bottom (parallel).
    Supports DAG mode: steps with ``id`` and ``depends_on`` fields are laid out
    using topological-level positioning (independent branches in parallel columns).
    Supports Version 2 graph mode: explicit edges, conditional_edges, selector_edges.
    """
    data = _yaml.safe_load(yaml_str)
    if not isinstance(data, dict) or "plan" not in data:
        raise ValueError("YAML must contain 'plan' key")

    plan = data.get("plan", [])
    repeat = data.get("repeat", True)
    version = data.get("version", 1)

    # Version 2: explicit graph with edges / conditional_edges / selector_edges
    if version >= 2:
        return _yaml_v2_to_layout(data)

    # Detect DAG mode: any step has an 'id' field
    is_dag = any(isinstance(s, dict) and "id" in s for s in plan)

    if is_dag:
        return _yaml_dag_to_layout(plan, repeat)
    else:
        return _yaml_linear_to_layout(plan, repeat)

def _yaml_v2_to_layout(data: dict) -> dict:
    """Convert Version 2 graph YAML to canvas layout JSON.

    Handles explicit edges, conditional_edges, selector_edges, and selector nodes.
    Nodes are positioned using topological-level layout based on the edges list.
    """
    plan = data.get("plan", [])
    repeat = data.get("repeat", False)
    raw_edges = data.get("edges", [])
    raw_cond_edges = data.get("conditional_edges", [])
    raw_sel_edges = data.get("selector_edges", [])

    nodes: list[dict] = []
    edges: list[dict] = []

    nid = 1
    eid = 1

    # ── Layout constants ──
    MARGIN_X = 60
    MARGIN_Y = 40
    GAP_X = 260
    GAP_Y = 90

    # Build set of selector node step_ids
    selector_step_ids = set()
    for se in raw_sel_edges:
        src = se.get("source", "")
        if src:
            selector_step_ids.add(src)

    # First pass: create nodes, build step_id → node_id mapping
    step_id_to_node_id: dict[str, str] = {}
    step_ids_ordered: list[str] = []

    for step in plan:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id", ""))
        node_id = f"on{nid}"; nid += 1

        if "expert" in step:
            raw = step["expert"]
            info = _parse_expert_name(raw)
            node = {
                "id": node_id,
                "x": 0, "y": 0,
                **info,
                "author": "主持人",
                "content": step.get("instruction", ""),
                "source": "",
            }
            if info.get("type") == "external":
                for _ek in ("api_url", "api_key", "model"):
                    if _ek in step:
                        node[_ek] = step[_ek]
                if "headers" in step and isinstance(step["headers"], dict):
                    node["headers"] = step["headers"]
            # Mark selector nodes
            if step.get("selector") or step_id in selector_step_ids:
                node["isSelector"] = True
                node["emoji"] = "🎯"
        elif "manual" in step:
            manual = step["manual"]
            _author = manual.get("author", "主持人") if isinstance(manual, dict) else "主持人"
            _content = manual.get("content", "") if isinstance(manual, dict) else ""
            # Detect start / end special manual nodes by author
            if _author in ("begin", "bstart"):
                node = {
                    "id": node_id,
                    "x": 0, "y": 0,
                    "type": "manual", "tag": "manual",
                    "name": "开始", "emoji": "🚀",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": "begin",
                    "content": _content,
                    "source": "",
                }
            elif _author == "bend":
                node = {
                    "id": node_id,
                    "x": 0, "y": 0,
                    "type": "manual", "tag": "manual",
                    "name": "结束", "emoji": "🏁",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": "bend",
                    "content": _content,
                    "source": "",
                }
            else:
                node = {
                    "id": node_id,
                    "x": 0, "y": 0,
                    "type": "manual", "tag": "manual",
                    "name": "手动注入", "emoji": "📝",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": _author,
                    "content": _content,
                    "source": "",
                }
        elif "script" in step:
            script = step["script"]
            if isinstance(script, dict):
                command = script.get("command", "")
                unix_command = script.get("unix_command", "")
                windows_command = script.get("windows_command", "")
                timeout = script.get("timeout", "")
                cwd = script.get("cwd", "")
            else:
                command = str(script or "")
                unix_command = ""
                windows_command = ""
                timeout = ""
                cwd = ""
            preview = unix_command or windows_command or command
            node = {
                "id": node_id,
                "x": 0, "y": 0,
                "type": "script", "tag": "script",
                "name": "脚本节点", "emoji": "🧪",
                "temperature": 0, "instance": 1, "session_id": "",
                "author": "script",
                "content": preview,
                "source": "",
                "script_command": command,
                "script_unix_command": unix_command,
                "script_windows_command": windows_command,
                "script_timeout": timeout,
                "script_cwd": cwd,
            }
        elif "human" in step:
            human = step["human"]
            if isinstance(human, dict):
                prompt = human.get("prompt", "")
                author = human.get("author", "主持人")
                reply_to = human.get("reply_to", "")
            else:
                prompt = str(human or "")
                author = "主持人"
                reply_to = ""
            node = {
                "id": node_id,
                "x": 0, "y": 0,
                "type": "human", "tag": "human",
                "name": "人类节点", "emoji": "🙋",
                "temperature": 0, "instance": 1, "session_id": "",
                "author": author,
                "content": prompt,
                "source": "",
                "human_prompt": prompt,
                "human_author": author,
                "human_reply_to": reply_to,
            }
        elif "all_experts" in step:
            node = {
                "id": node_id,
                "x": 0, "y": 0,
                "type": "expert", "tag": "all",
                "name": "全员讨论", "emoji": "👥",
                "temperature": 0.5, "instance": 1, "session_id": "",
                "author": "主持人", "content": "", "source": "",
            }
        else:
            continue

        nodes.append(node)
        if step_id:
            step_id_to_node_id[step_id] = node_id
            step_ids_ordered.append(step_id)

    # Build edges from explicit edges list
    for e in raw_edges:
        if isinstance(e, list) and len(e) >= 2:
            src_nid = step_id_to_node_id.get(str(e[0]))
            tgt_nid = step_id_to_node_id.get(str(e[1]))
        elif isinstance(e, dict):
            src_nid = step_id_to_node_id.get(str(e.get("source", "")))
            tgt_nid = step_id_to_node_id.get(str(e.get("target", "")))
        else:
            continue
        if src_nid and tgt_nid:
            edges.append({"id": f"oe{eid}", "source": src_nid, "target": tgt_nid})
            eid += 1

    # ── Topological layout using edges (longest path) ──
    # Build predecessor map from edges
    preds: dict[str, list[str]] = {sid: [] for sid in step_ids_ordered}
    for e in raw_edges:
        if isinstance(e, list) and len(e) >= 2:
            src_sid, tgt_sid = str(e[0]), str(e[1])
        elif isinstance(e, dict):
            src_sid = str(e.get("source", ""))
            tgt_sid = str(e.get("target", ""))
        else:
            continue
        if tgt_sid in preds and src_sid in step_id_to_node_id:
            preds[tgt_sid].append(src_sid)

    # NOTE: conditional_edges and selector_edges are NOT added to preds
    # because they may form cycles (e.g. else-branch loops back),
    # which would cause infinite recursion in the topological sort.
    # Only fixed edges (which form a DAG) are used for layer computation.

    layer: dict[str, int] = {}
    _visiting: set[str] = set()  # cycle guard
    def _get_layer(sid: str) -> int:
        if sid in layer:
            return layer[sid]
        if sid in _visiting:
            # Cycle detected — break it by treating as root
            layer[sid] = 0
            return 0
        _visiting.add(sid)
        deps = preds.get(sid, [])
        if not deps:
            layer[sid] = 0
        else:
            layer[sid] = max(_get_layer(d) for d in deps) + 1
        _visiting.discard(sid)
        return layer[sid]

    for sid in step_ids_ordered:
        _get_layer(sid)

    # Group by layer
    layers: dict[int, list[tuple[str, dict]]] = {}
    for sid in step_ids_ordered:
        lv = layer.get(sid, 0)
        nd = next((n for n in nodes if n["id"] == step_id_to_node_id.get(sid)), None)
        if nd:
            layers.setdefault(lv, []).append((sid, nd))

    # Barycenter ordering
    node_y: dict[str, float] = {}
    for lv in sorted(layers.keys()):
        layer_items = layers[lv]
        if lv > 0:
            def _bary(sid: str) -> float:
                deps = preds.get(sid, [])
                ys = [node_y[d] for d in deps if d in node_y]
                return sum(ys) / len(ys) if ys else 0.0
            layer_items.sort(key=lambda t: _bary(t[0]))
            layers[lv] = layer_items
        count = len(layer_items)
        total_h = (count - 1) * GAP_Y
        y_start = MARGIN_Y + max(0, (400 - total_h) // 2)
        for i, (sid, _nd) in enumerate(layer_items):
            y = y_start + i * GAP_Y
            node_y[sid] = y

    # Assign final x, y coordinates
    for lv, layer_items in sorted(layers.items()):
        x = MARGIN_X + lv * GAP_X
        for sid, nd in layer_items:
            nd["x"] = x
            nd["y"] = int(node_y.get(sid, MARGIN_Y))

    # Build conditional edges output for frontend
    cond_edges_out = []
    for ce in raw_cond_edges:
        src_nid = step_id_to_node_id.get(str(ce.get("source", "")))
        then_nid = step_id_to_node_id.get(str(ce.get("then", "")))
        else_nid = step_id_to_node_id.get(str(ce.get("else", ""))) if ce.get("else") else ""
        if src_nid and then_nid:
            cond_edges_out.append({
                "source": src_nid,
                "condition": ce.get("condition", ""),
                "then": then_nid,
                "else": else_nid or "",
            })

    # Build selector edges output for frontend
    sel_edges_out = []
    for se in raw_sel_edges:
        src_nid = step_id_to_node_id.get(str(se.get("source", "")))
        choices = se.get("choices", {})
        if src_nid and choices:
            mapped_choices = {}
            for num, tgt_sid in choices.items():
                tgt_nid = step_id_to_node_id.get(str(tgt_sid))
                if tgt_nid:
                    mapped_choices[int(num)] = tgt_nid
            if mapped_choices:
                sel_edges_out.append({"source": src_nid, "choices": mapped_choices})

    layout = {
        "nodes": nodes,
        "edges": edges,
        "conditionalEdges": cond_edges_out,
        "selectorEdges": sel_edges_out,
        "groups": [],
        "settings": {
            "repeat": repeat,
            "max_rounds": 5,
            "cluster_threshold": 150,
        },
    }
    return layout

def _yaml_dag_to_layout(plan: list, repeat: bool) -> dict:
    """Convert DAG-mode plan (steps with id/depends_on) to canvas layout.

    Layout strategy (optimised):
    - Nodes are assigned to layers via longest-path from roots.
    - Horizontal gap adapts to graph width so the canvas stays readable.
    - Within each layer, nodes are sorted by the median y-position of their
      predecessors (barycenter heuristic) to minimise edge crossings.
    - All y-coordinates are guaranteed ≥ margin (no negative positions).
    """
    nodes: list[dict] = []
    edges: list[dict] = []

    nid = 1
    eid = 1

    # ── Layout constants ──
    NODE_W = 160          # approximate rendered width of a canvas-node
    MARGIN_X = 60         # left margin
    MARGIN_Y = 40         # top margin
    GAP_X = 260           # horizontal gap between layers (> NODE_W + breathing room)
    GAP_Y = 90            # vertical gap between nodes in the same layer

    # First pass: create nodes, build step_id → node_id mapping
    step_id_to_node_id: dict[str, str] = {}
    step_items: list[tuple[str, dict, list[str]]] = []  # (step_id, node_dict, depends_on)

    for step in plan:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id", ""))
        depends_on = step.get("depends_on", [])
        if isinstance(depends_on, str):
            depends_on = [depends_on]

        node_id = f"on{nid}"; nid += 1

        if "expert" in step:
            raw = step["expert"]
            info = _parse_expert_name(raw)
            node = {
                "id": node_id,
                "x": 0, "y": 0,
                **info,
                "author": "主持人",
                "content": step.get("instruction", ""),
                "source": "",
            }
            if info.get("type") == "external":
                for _ek in ("api_url", "api_key", "model"):
                    if _ek in step:
                        node[_ek] = step[_ek]
                if "headers" in step and isinstance(step["headers"], dict):
                    node["headers"] = step["headers"]
        elif "manual" in step:
            manual = step["manual"]
            _author = manual.get("author", "主持人") if isinstance(manual, dict) else "主持人"
            _content = manual.get("content", "") if isinstance(manual, dict) else ""
            if _author in ("begin", "bstart"):
                node = {
                    "id": node_id,
                    "x": 0, "y": 0,
                    "type": "manual", "tag": "manual",
                    "name": "开始", "emoji": "🚀",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": "begin",
                    "content": _content,
                    "source": "",
                }
            elif _author == "bend":
                node = {
                    "id": node_id,
                    "x": 0, "y": 0,
                    "type": "manual", "tag": "manual",
                    "name": "结束", "emoji": "🏁",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": "bend",
                    "content": _content,
                    "source": "",
                }
            else:
                node = {
                    "id": node_id,
                    "x": 0, "y": 0,
                    "type": "manual", "tag": "manual",
                    "name": "手动注入", "emoji": "📝",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": _author,
                    "content": _content,
                    "source": "",
                }
        elif "script" in step:
            script = step["script"]
            if isinstance(script, dict):
                command = script.get("command", "")
                unix_command = script.get("unix_command", "")
                windows_command = script.get("windows_command", "")
                timeout = script.get("timeout", "")
                cwd = script.get("cwd", "")
            else:
                command = str(script or "")
                unix_command = ""
                windows_command = ""
                timeout = ""
                cwd = ""
            preview = unix_command or windows_command or command
            node = {
                "id": node_id,
                "x": 0, "y": 0,
                "type": "script", "tag": "script",
                "name": "脚本节点", "emoji": "🧪",
                "temperature": 0, "instance": 1, "session_id": "",
                "author": "script",
                "content": preview,
                "source": "",
                "script_command": command,
                "script_unix_command": unix_command,
                "script_windows_command": windows_command,
                "script_timeout": timeout,
                "script_cwd": cwd,
            }
        elif "human" in step:
            human = step["human"]
            if isinstance(human, dict):
                prompt = human.get("prompt", "")
                author = human.get("author", "主持人")
                reply_to = human.get("reply_to", "")
            else:
                prompt = str(human or "")
                author = "主持人"
                reply_to = ""
            node = {
                "id": node_id,
                "x": 0, "y": 0,
                "type": "human", "tag": "human",
                "name": "人类节点", "emoji": "🙋",
                "temperature": 0, "instance": 1, "session_id": "",
                "author": author,
                "content": prompt,
                "source": "",
                "human_prompt": prompt,
                "human_author": author,
                "human_reply_to": reply_to,
            }
        elif "all_experts" in step:
            node = {
                "id": node_id,
                "x": 0, "y": 0,
                "type": "expert", "tag": "all",
                "name": "全员讨论", "emoji": "👥",
                "temperature": 0.5, "instance": 1, "session_id": "",
                "author": "主持人", "content": "", "source": "",
            }
        else:
            continue

        nodes.append(node)
        if step_id:
            step_id_to_node_id[step_id] = node_id
        step_items.append((step_id, node, depends_on))

    # Build edges from depends_on
    for step_id, node, depends_on in step_items:
        node_id = node["id"]
        for dep in depends_on:
            src_node_id = step_id_to_node_id.get(dep)
            if src_node_id:
                edges.append({"id": f"oe{eid}", "source": src_node_id, "target": node_id})
                eid += 1

    # ── Compute topological layer (longest path from roots) ──
    preds: dict[str, list[str]] = {}
    for step_id, _node, depends_on in step_items:
        preds[step_id] = [d for d in depends_on if d in step_id_to_node_id]

    layer: dict[str, int] = {}
    def _get_layer(sid: str) -> int:
        if sid in layer:
            return layer[sid]
        deps = preds.get(sid, [])
        if not deps:
            layer[sid] = 0
            return 0
        lv = max(_get_layer(d) for d in deps) + 1
        layer[sid] = lv
        return lv

    for step_id, _node, _deps in step_items:
        if step_id:
            _get_layer(step_id)

    # ── Group by layer ──
    layers: dict[int, list[tuple[str, dict]]] = {}
    for step_id, node, _deps in step_items:
        lv = layer.get(step_id, 0)
        layers.setdefault(lv, []).append((step_id, node))

    # ── Barycenter ordering to reduce edge crossings ──
    # For layer 0, keep original YAML order.
    # For subsequent layers, sort nodes by the median y-position of predecessors.
    node_y: dict[str, float] = {}  # step_id → assigned y

    for lv in sorted(layers.keys()):
        layer_items = layers[lv]

        if lv > 0:
            # Compute barycenter for each node
            def _bary(sid: str) -> float:
                deps = preds.get(sid, [])
                ys = [node_y[d] for d in deps if d in node_y]
                return sum(ys) / len(ys) if ys else 0.0
            layer_items.sort(key=lambda t: _bary(t[0]))
            layers[lv] = layer_items

        # Assign y positions — centre the layer vertically
        count = len(layer_items)
        total_h = (count - 1) * GAP_Y
        y_start = MARGIN_Y + max(0, (400 - total_h) // 2)  # aim for ~400px canvas height centre
        for i, (sid, _node) in enumerate(layer_items):
            y = y_start + i * GAP_Y
            node_y[sid] = y

    # ── Assign final x, y coordinates ──
    for lv, layer_items in sorted(layers.items()):
        x = MARGIN_X + lv * GAP_X
        for sid, node in layer_items:
            node["x"] = x
            node["y"] = int(node_y.get(sid, MARGIN_Y))

    layout = {
        "nodes": nodes,
        "edges": edges,
        "groups": [],
        "settings": {
            "repeat": repeat,
            "max_rounds": 5,
            "cluster_threshold": 150,
        },
    }
    return layout

def _yaml_linear_to_layout(plan: list, repeat: bool) -> dict:
    """Convert linear plan (no id/depends_on) to canvas layout.

    Optimised layout:
    - Wider horizontal spacing so nodes don't overlap.
    - Parallel groups: fan-out edges from prev → every member, fan-in edges
      from every member → next step (instead of only first/last member).
    - Vertical centering of parallel members around the baseline.
    - Group boxes with proper padding.
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    groups: list[dict] = []

    nid = 1
    eid = 1
    gid = 1

    # ── Layout constants ──
    MARGIN_X = 60
    BASE_Y = 240           # vertical baseline (enough headroom for parallel groups)
    GAP_X = 260            # horizontal gap between steps
    GAP_Y_PARALLEL = 90    # vertical gap between parallel members
    GROUP_PAD = 30         # padding around group box

    cursor_x = MARGIN_X
    prev_node_ids: list[str] = []  # may be multiple for fan-in after parallel group

    for step in plan:
        if not isinstance(step, dict):
            continue

        # --- expert step ---
        if "expert" in step:
            raw = step["expert"]
            info = _parse_expert_name(raw)
            node_id = f"on{nid}"; nid += 1
            node = {
                "id": node_id,
                "x": cursor_x,
                "y": BASE_Y,
                **info,
                "author": "主持人",
                "content": step.get("instruction", ""),
                "source": "",
            }
            if info.get("type") == "external":
                for _ek in ("api_url", "api_key", "model"):
                    if _ek in step:
                        node[_ek] = step[_ek]
                if "headers" in step and isinstance(step["headers"], dict):
                    node["headers"] = step["headers"]
            nodes.append(node)
            for pid in prev_node_ids:
                edges.append({"id": f"oe{eid}", "source": pid, "target": node_id})
                eid += 1
            prev_node_ids = [node_id]
            cursor_x += GAP_X

        # --- parallel step ---
        elif "parallel" in step:
            members = step["parallel"]
            if not isinstance(members, list):
                continue
            group_node_ids: list[str] = []
            group_x = cursor_x
            count = len(members)
            total_h = (count - 1) * GAP_Y_PARALLEL
            y_start = BASE_Y - total_h // 2  # centre around baseline

            for idx, item in enumerate(members):
                if isinstance(item, str):
                    raw = item
                    instruction = ""
                elif isinstance(item, dict) and "expert" in item:
                    raw = item["expert"]
                    instruction = item.get("instruction", "")
                else:
                    continue

                info = _parse_expert_name(raw)
                node_id = f"on{nid}"; nid += 1
                node = {
                    "id": node_id,
                    "x": group_x,
                    "y": y_start + idx * GAP_Y_PARALLEL,
                    **info,
                    "author": "主持人",
                    "content": instruction,
                    "source": "",
                }
                if info.get("type") == "external" and isinstance(item, dict):
                    for _ek in ("api_url", "api_key", "model"):
                        if _ek in item:
                            node[_ek] = item[_ek]
                    if "headers" in item and isinstance(item["headers"], dict):
                        node["headers"] = item["headers"]
                nodes.append(node)
                group_node_ids.append(node_id)

            # Create group container
            if group_node_ids:
                g_nodes = [n for n in nodes if n["id"] in group_node_ids]
                min_x = min(n["x"] for n in g_nodes) - GROUP_PAD
                min_y = min(n["y"] for n in g_nodes) - GROUP_PAD
                max_x = max(n["x"] for n in g_nodes) + 160 + GROUP_PAD
                max_y = max(n["y"] for n in g_nodes) + 50 + GROUP_PAD
                groups.append({
                    "id": f"og{gid}",
                    "name": "🔀 并行",
                    "type": "parallel",
                    "x": min_x,
                    "y": min_y,
                    "w": max_x - min_x,
                    "h": max_y - min_y,
                    "nodeIds": group_node_ids,
                })
                gid += 1

                # Fan-out: prev → every member
                for pid in prev_node_ids:
                    for mid in group_node_ids:
                        edges.append({"id": f"oe{eid}", "source": pid, "target": mid})
                        eid += 1
                # All members become prev (fan-in into next step)
                prev_node_ids = list(group_node_ids)

            cursor_x += GAP_X

        # --- all_experts step ---
        elif "all_experts" in step:
            node_id = f"on{nid}"; nid += 1
            node = {
                "id": node_id,
                "x": cursor_x,
                "y": BASE_Y,
                "type": "expert",
                "tag": "all",
                "name": "全员讨论",
                "emoji": "👥",
                "temperature": 0.5,
                "instance": 1,
                "session_id": "",
                "author": "主持人",
                "content": "",
                "source": "",
            }
            nodes.append(node)
            groups.append({
                "id": f"og{gid}",
                "name": "👥 全员",
                "type": "all",
                "x": cursor_x - 20,
                "y": BASE_Y - 20,
                "w": 180,
                "h": 80,
                "nodeIds": [node_id],
            })
            gid += 1
            for pid in prev_node_ids:
                edges.append({"id": f"oe{eid}", "source": pid, "target": node_id})
                eid += 1
            prev_node_ids = [node_id]
            cursor_x += GAP_X

        # --- manual step ---
        elif "manual" in step:
            manual = step["manual"]
            node_id = f"on{nid}"; nid += 1
            _author = manual.get("author", "主持人") if isinstance(manual, dict) else "主持人"
            _content = manual.get("content", "") if isinstance(manual, dict) else ""
            if _author in ("begin", "bstart"):
                node = {
                    "id": node_id,
                    "x": cursor_x,
                    "y": BASE_Y,
                    "type": "manual", "tag": "manual",
                    "name": "开始", "emoji": "🚀",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": "begin",
                    "content": _content,
                    "source": "",
                }
            elif _author == "bend":
                node = {
                    "id": node_id,
                    "x": cursor_x,
                    "y": BASE_Y,
                    "type": "manual", "tag": "manual",
                    "name": "结束", "emoji": "🏁",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": "bend",
                    "content": _content,
                    "source": "",
                }
            else:
                node = {
                    "id": node_id,
                    "x": cursor_x,
                    "y": BASE_Y,
                    "type": "manual", "tag": "manual",
                    "name": "手动注入", "emoji": "📝",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": _author,
                    "content": _content,
                    "source": "",
                }
            nodes.append(node)
            for pid in prev_node_ids:
                edges.append({"id": f"oe{eid}", "source": pid, "target": node_id})
                eid += 1
            prev_node_ids = [node_id]
            cursor_x += GAP_X
        elif "script" in step:
            script = step["script"]
            node_id = f"on{nid}"; nid += 1
            if isinstance(script, dict):
                command = script.get("command", "")
                unix_command = script.get("unix_command", "")
                windows_command = script.get("windows_command", "")
                timeout = script.get("timeout", "")
                cwd = script.get("cwd", "")
            else:
                command = str(script or "")
                unix_command = ""
                windows_command = ""
                timeout = ""
                cwd = ""
            preview = unix_command or windows_command or command
            node = {
                "id": node_id,
                "x": cursor_x,
                "y": BASE_Y,
                "type": "script", "tag": "script",
                "name": "脚本节点", "emoji": "🧪",
                "temperature": 0, "instance": 1, "session_id": "",
                "author": "script",
                "content": preview,
                "source": "",
                "script_command": command,
                "script_unix_command": unix_command,
                "script_windows_command": windows_command,
                "script_timeout": timeout,
                "script_cwd": cwd,
            }
            nodes.append(node)
            for pid in prev_node_ids:
                edges.append({"id": f"oe{eid}", "source": pid, "target": node_id})
                eid += 1
            prev_node_ids = [node_id]
            cursor_x += GAP_X
        elif "human" in step:
            human = step["human"]
            node_id = f"on{nid}"; nid += 1
            if isinstance(human, dict):
                prompt = human.get("prompt", "")
                author = human.get("author", "主持人")
                reply_to = human.get("reply_to", "")
            else:
                prompt = str(human or "")
                author = "主持人"
                reply_to = ""
            node = {
                "id": node_id,
                "x": cursor_x,
                "y": BASE_Y,
                "type": "human", "tag": "human",
                "name": "人类节点", "emoji": "🙋",
                "temperature": 0, "instance": 1, "session_id": "",
                "author": author,
                "content": prompt,
                "source": "",
                "human_prompt": prompt,
                "human_author": author,
                "human_reply_to": reply_to,
            }
            nodes.append(node)
            for pid in prev_node_ids:
                edges.append({"id": f"oe{eid}", "source": pid, "target": node_id})
                eid += 1
            prev_node_ids = [node_id]
            cursor_x += GAP_X

    layout = {
        "nodes": nodes,
        "edges": edges,
        "groups": groups,
        "settings": {
            "repeat": repeat,
            "max_rounds": 5,
            "cluster_threshold": 150,
        },
    }
    return layout

@mcp.tool()
async def yaml_to_layout(
    username: str = "",
    yaml_source: str = "",
    layout_name: str = "",
) -> str:
    """
    Convert an OASIS YAML schedule to a visual layout (on-the-fly, no file saved).

    Layout is generated dynamically from YAML; no separate layout JSON is stored.
    The visual orchestrator UI loads layouts by reading YAML and converting in real-time.

    Args:
        username: (auto-injected) current user identity; do NOT set manually
        yaml_source: Either a saved workflow filename (e.g. "review.yaml") or raw YAML content
        layout_name: Layout display name. If empty, auto-derived from yaml_source.

    Returns:
        Confirmation with generated layout summary
    """
    effective_user = username or _FALLBACK_USER

    # Use OASIS HTTP API for layout generation
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            payload = {
                "user_id": effective_user,
                "yaml_source": yaml_source,
                "layout_name": layout_name,
            }
            resp = await client.post(f"{OASIS_BASE_URL}/layouts/from-yaml", json=payload)
            if resp.status_code != 200:
                return f"❌ 转换失败: {resp.text}"
            data = resp.json()
            layout = data.get("data", {})
            node_count = len(layout.get("nodes", []))
            edge_count = len(layout.get("edges", []))
            group_count = len(layout.get("groups", []))
            return (
                f"✅ Layout 已生成（实时转换，无需保存文件）\n"
                f"  名称: {data.get('layout')}\n"
                f"  节点: {node_count} | 连线: {edge_count} | 分组: {group_count}"
            )
    except httpx.ConnectError:
        return _CONN_ERR
    except Exception as e:
        return f"❌ 转换失败: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
