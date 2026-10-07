import sys as _sys
import os as _os
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

"""
MCP Tool Server: OASIS Forum

Exposes tools for the user's Agent to interact with the OASIS discussion forum:
  - list_oasis_experts: List all available expert personas (public + user custom)
  - save_oasis_expert / delete_oasis_expert: create, update, or delete expert personas
    by scanning the Agent checkpoint DB — no separate storage needed
  - start_new_oasis: Submit a discussion — supports direct LLM experts and session-backed experts
  - check_oasis_discussion / cancel_oasis_discussion: List, monitor, or cancel discussions

Runs as a stdio MCP server, just like the other mcp_*.py tools.
"""

import asyncio
import json
import os
import re
import subprocess
import uuid
from typing import Any, Literal

import httpx
import yaml as _yaml
from dotenv import load_dotenv
from webot.mcp_tool_docs import DocumentedFastMCP as FastMCP
from common.runtime_paths import DATA_DIR, ENV_FILE, PROJECT_ROOT, USER_FILES_DIR, WORKSPACE_DIR, ensure_runtime_dirs, set_subprocess_env, venv_python

mcp = FastMCP("OASIS Forum")

load_dotenv(dotenv_path=str(ENV_FILE))

OASIS_BASE_URL = os.getenv("OASIS_BASE_URL", "http://127.0.0.1:51202")
_FALLBACK_USER = os.getenv("MCP_OASIS_USER", "agent_user")
_PROJECT_ROOT = str(PROJECT_ROOT)
ensure_runtime_dirs()
_WORKING_DIR = str(WORKSPACE_DIR)
_WORKFLOW_PYTHON = str(venv_python())
if not os.path.isfile(_WORKFLOW_PYTHON):
    _WORKFLOW_PYTHON = _sys.executable
_WORKFLOW_IMPORT_PATHS = os.pathsep.join([_PROJECT_ROOT, _src_dir])

_CONN_ERR = "❌ 无法连接 OASIS 论坛服务器。请确认 OASIS 服务已启动 (端口 51202)。"


def _resolve_effective_user(username: str = "") -> str:
    """Resolve the MCP user more robustly than the old agent_user-only fallback.

    In production the MCP layer should auto-inject username. When that does not
    happen, prefer obvious local user scopes so Python workflow discovery and
    startup still behave like the rest of OASIS tooling.
    """
    explicit = str(username or "").strip()
    if explicit:
        return explicit

    users_root = os.path.join(str(USER_FILES_DIR))
    candidates: list[str] = []
    if os.path.isdir(users_root):
        for entry in sorted(os.listdir(users_root)):
            path = os.path.join(users_root, entry)
            if os.path.isdir(path):
                candidates.append(entry)

    if "default" in candidates:
        return "default"
    if _FALLBACK_USER in candidates:
        return _FALLBACK_USER
    if len(candidates) == 1:
        return candidates[0]
    return _FALLBACK_USER


def _workflow_python_dir(user_id: str, team: str = "") -> str:
    if team:
        return os.path.join(str(USER_FILES_DIR), user_id, "teams", team, "oasis", "python")
    return os.path.join(str(USER_FILES_DIR), user_id, "oasis", "python")


def _python_runs_dir() -> str:
    return os.path.join(str(DATA_DIR), "python_workflow_runs")


def resolve_python_workflow_path(user_id: str, python_file: str, team: str = "") -> tuple[str | None, str | None]:
    if not python_file:
        return None, "未提供 python workflow 文件名"
    target_name = python_file if python_file.endswith(".py") else f"{python_file}.py"
    matches: list[tuple[str, str]] = []
    user_root = os.path.join(str(USER_FILES_DIR), user_id)
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


def _spawn_standalone_python_workflow(
    *,
    user_id: str,
    python_file: str,
    question: str,
    team: str = "",
) -> dict[str, str | int]:
    runs_dir = os.path.join(str(DATA_DIR), "python_workflow_runs")
    os.makedirs(runs_dir, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    log_path = os.path.join(runs_dir, f"{run_id}.log")
    result_path = os.path.join(runs_dir, f"{run_id}.json")
    meta_path = os.path.join(runs_dir, f"{run_id}.meta.json")
    cmd = [
        _WORKFLOW_PYTHON,
        python_file,
        "--user-id",
        user_id or _FALLBACK_USER,
        "--question",
        question or "",
        "--run-id",
        run_id,
        "--meta-file",
        meta_path,
        "--result-file",
        result_path,
    ]
    if team:
        cmd.extend(["--team", team])

    log_file = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=_WORKING_DIR,
        env=set_subprocess_env({
            **os.environ,
            "CLAWCROSS_PROJECT_ROOT": _PROJECT_ROOT,
            "CLAWCROSS_PYTHONPATH": _WORKFLOW_IMPORT_PATHS,
            "PYTHONPATH": _WORKFLOW_IMPORT_PATHS + (
                os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""
            ),
        }),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_file.close()
    meta = {
        "run_id": run_id,
        "pid": proc.pid,
        "log_file": log_path,
        "result_file": result_path,
        "python_file": python_file,
        "python_executable": _WORKFLOW_PYTHON,
        "user_id": user_id,
        "team": team,
        "question": question,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return {
        "run_id": run_id,
        "pid": proc.pid,
        "log_file": log_path,
        "result_file": result_path,
        "python_file": python_file,
        "meta_file": meta_path,
    }


def _iter_python_workflow_dirs(user_id: str, team: str = "") -> list[tuple[str, str, str]]:
    if not user_id:
        return []

    user_root = os.path.join(str(USER_FILES_DIR), user_id)
    if team:
        return [("team", team, os.path.join(user_root, "teams", team, "oasis", "python"))]

    dirs: list[tuple[str, str, str]] = [
        ("personal", "", os.path.join(user_root, "oasis", "python"))
    ]
    teams_root = os.path.join(user_root, "teams")
    if os.path.isdir(teams_root):
        for team_name in sorted(os.listdir(teams_root)):
            team_dir = os.path.join(teams_root, team_name)
            if os.path.isdir(team_dir):
                dirs.append(("team", team_name, os.path.join(team_dir, "oasis", "python")))
    return dirs


def _is_python_run(run_id: str) -> bool:
    """Whether *run_id* names a Python workflow run (as opposed to a discussion topic)."""
    safe_run_id = re.sub(r"[^a-zA-Z0-9]", "", str(run_id or "").strip())
    return bool(safe_run_id) and (
        os.path.isfile(os.path.join(_python_runs_dir(), f"{safe_run_id}.meta.json"))
        or os.path.isfile(os.path.join(_python_runs_dir(), f"{safe_run_id}.json"))
    )


def _load_python_run_payload(run_id: str) -> tuple[dict | None, str | None]:
    safe_run_id = re.sub(r"[^a-zA-Z0-9]", "", str(run_id or "").strip())
    if not safe_run_id:
        return None, "无效的 run_id"
    meta_path = os.path.join(_python_runs_dir(), f"{safe_run_id}.meta.json")
    result_path = os.path.join(_python_runs_dir(), f"{safe_run_id}.json")
    meta: dict[str, Any] = {}
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                meta = loaded
        except Exception:
            meta = {}
    if not os.path.isfile(result_path):
        if meta:
            meta["_result_file"] = result_path
            meta["_log_file"] = os.path.join(_python_runs_dir(), f"{safe_run_id}.log")
            meta["_meta_file"] = meta_path
            meta["_running"] = _pid_is_running(int(meta.get("pid") or 0))
            return meta, None
        return None, f"未找到运行结果文件: {safe_run_id}"
    try:
        with open(result_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None, f"运行结果格式无效: {safe_run_id}"
        for key, value in meta.items():
            data.setdefault(key, value)
        data["_result_file"] = result_path
        data["_log_file"] = os.path.join(_python_runs_dir(), f"{safe_run_id}.log")
        data["_meta_file"] = meta_path
        data["_running"] = False
        return data, None
    except Exception as e:
        return None, f"读取运行结果失败: {e}"


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


async def _cancel_oasis_topic_for_python_run(data: dict, user_id: str) -> str:
    topic_id = str(data.get("topic_id") or "").strip()
    if not topic_id:
        question = str(data.get("question") or "").strip()
        if question:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        f"{OASIS_BASE_URL}/topics",
                        params={"user_id": user_id},
                    )
                if resp.status_code == 200:
                    matches = [
                        item for item in resp.json()
                        if str(item.get("question") or "").strip() == question
                        and str(item.get("status") or "") == "discussing"
                    ]
                    if len(matches) == 1:
                        topic_id = str(matches[0].get("topic_id") or "").strip()
            except Exception:
                topic_id = ""
    if not topic_id:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(
                f"{OASIS_BASE_URL}/topics/{topic_id}",
                params={"user_id": user_id},
            )
        if resp.status_code == 200:
            return topic_id
    except Exception:
        pass
    return ""

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
    effective_user = _resolve_effective_user(username)
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
                "\n💡 在 schedule_yaml 里这样写参与者："
                "\n   • persona: <tag>，tools: none | all | [工具名] — 用上面的人设临时创建一个 agent，话题结束即删除"
                "\n   • agent: <角色名 | handle | 用户/handle | ag_…> — 你已有的任意 agent"
            )
            return "\n".join(lines)

    except httpx.ConnectError:
        return _CONN_ERR
    except Exception as e:
        return f"❌ 查询异常: {str(e)}"

@mcp.tool()
async def save_oasis_expert(
    username: str,
    tag: str,
    name: str = "",
    persona: str = "",
    temperature: float = -1,
) -> str:
    """
    Create or update a custom expert persona for the current user, keyed by tag:
    updates the expert if the tag exists, otherwise creates it (then name and
    persona are required).

    Args:
        username: (auto-injected) current user identity; do NOT set manually
        tag: Unique identifier tag (e.g. "pm", "frontend_arch")
        name: Display name (e.g. "产品经理"); empty keeps the current one on update
        persona: Persona description; empty keeps the current one on update
        temperature: LLM temperature 0.0-1.0; -1 keeps the current value (0.7 for a new expert)
    """
    body: dict = {"user_id": username}
    if name:
        body["name"] = name
    if persona:
        body["persona"] = persona
    if temperature >= 0:
        body["temperature"] = temperature
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.put(f"{OASIS_BASE_URL}/experts/user/{tag}", json=body)
            action = "更新"
            if resp.status_code == 400 and "未找到" in str(resp.json().get("detail", "")):
                if not name or not persona:
                    return f"❌ 专家 tag=\"{tag}\" 不存在；新建需要同时提供 name 和 persona。"
                resp = await client.post(
                    f"{OASIS_BASE_URL}/experts/user",
                    json={**body, "tag": tag, "temperature": body.get("temperature", 0.7)},
                )
                action = "创建"
            if resp.status_code != 200:
                return f"❌ {action}失败: {resp.json().get('detail', resp.text)}"

            expert = resp.json()["expert"]
            return (
                f"✅ 自定义专家已{action}\n"
                f"  名称: {expert['name']}\n"
                f"  Tag: {expert['tag']}\n"
                f"  Persona: {expert['persona']}\n"
                f"  Temperature: {expert['temperature']}"
            )

    except httpx.ConnectError:
        return _CONN_ERR
    except Exception as e:
        return f"❌ 保存异常: {str(e)}"


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
    Submit a question or task to the OASIS forum. Asynchronous: returns a
    topic_id (YAML) or run_id (Python) at once; poll either with
    check_oasis_discussion. Give one workflow source — python_file,
    schedule_file, or schedule_yaml (that precedence if several);
    get_workflow_rules explains the formats, list_oasis_workflows lists saved ones.
    Only start a workflow or sub-workflow when the user or assigned task requests
    it. Keep intermediate work and expert discussion in the workflow channel;
    send it to a group only when explicitly requested. If a run stalls or fails,
    check its status and report before starting again; do not restart repeatedly.

    Args:
        question: the question to discuss, or the task to carry out
        schedule_yaml: inline workflow YAML
        username: (auto-injected) do NOT set manually
        max_rounds: discussion rounds, 1-20
        schedule_file: saved YAML, short names resolve under the user's oasis/yaml/
        python_file: saved workflowpy script
        notify_session: (auto-injected) session to notify on completion
        discussion: true forces a forum with JSON replies and voting instead of a task pipeline where each agent sees earlier output; false keeps the YAML's own setting
        team: scope agents and experts to this team
    """
    effective_user = _resolve_effective_user(username)

    if not python_file and not schedule_yaml and not schedule_file:
        return "❌ 必须提供 python_file 或 schedule_yaml 或 schedule_file（至少一个）。"

    try:
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
            payload = await asyncio.to_thread(
                _spawn_standalone_python_workflow,
                user_id=effective_user,
                python_file=python_file,
                question=question,
                team=team,
            )
            return (
                f"🐍 Python 工作流已启动（standalone）\n"
                f"任务: {question[:80]}\n"
                f"Run ID: {payload['run_id']}\n"
                f"PID: {payload['pid']}\n"
                f"Log: {payload['log_file']}\n"
                f"Result: {payload['result_file']}\n"
            )

        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout=300.0)) as client:
            body: dict = {
                "question": question,
                "user_id": effective_user,
                "max_rounds": max_rounds,
            }
            # Only send discussion when explicitly set to True (discussion mode)
            # so YAML's own "discussion:" setting is respected by default.
            # Sending False would override a YAML that asks for discussion.
            if discussion:
                body["discussion"] = True

            # Detached: the calling agent is told when it ends
            if notify_session:
                port = os.getenv("PORT_AGENT", "51200")
                body["callback_url"] = f"http://127.0.0.1:{port}/system_trigger"
                body["callback_session_id"] = notify_session

            if schedule_file:
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
async def check_oasis_discussion(topic_id: str = "", username: str = "", team: str = "") -> str:
    """
    Check an OASIS run started by start_new_oasis — a discussion's status and
    recent posts, or a Python workflow run's result; with no id, list all
    discussion topics and recent Python runs.

    Args:
        topic_id: topic_id or Python run_id returned by start_new_oasis; empty lists everything
        username: (auto-injected) current user identity; do NOT set manually
        team: When listing, only show Python runs of this team
    """
    if not (topic_id or "").strip():
        topics = await _list_oasis_topics(username)
        runs = await _list_oasis_python_runs(username, team)
        return f"{topics}\n\n{runs}"
    if _is_python_run(topic_id):
        return await _check_oasis_python_run(topic_id, username)
    effective_user = _resolve_effective_user(username)
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
    Force-cancel a running OASIS discussion or Python workflow run.

    Args:
        topic_id: topic_id or Python run_id returned by start_new_oasis
        username: (auto-injected) current user identity; do NOT set manually
    """
    if _is_python_run(topic_id):
        return await _cancel_oasis_python_run(topic_id, username)
    effective_user = _resolve_effective_user(username)
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

async def _list_oasis_topics(username: str = "") -> str:
    """
    List all discussion topics on the OASIS forum.

    Args:
        username: (auto-injected) current user identity; leave empty to list all.

    Returns:
        Formatted list of all discussion topics
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            effective_user = _resolve_effective_user(username)
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

    user_root = os.path.join(str(USER_FILES_DIR), user_id)
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
async def save_oasis_workflow(
    username: str,
    name: str,
    content: str,
    kind: Literal["yaml", "python"],
    description: str = "",
    save_layout: bool = True,
    team: str = "",
) -> str:
    """
    Save a reusable OASIS workflow for start_new_oasis (schedule_file=... for
    YAML, python_file=... for Python). Read get_workflow_rules(kind) first; a
    YAML workflow must have a top-level `plan`.

    :param name: Workflow file name; the extension is added if missing
    :param content: The Version-2 YAML, or the full workflowpy source
    :param kind: "yaml" or "python"
    :param description: YAML only: one-line description saved as a header comment
    :param save_layout: YAML only: also generate a visual layout
    :param team: Save under this team's directory instead of the user's
    """
    if kind == "python":
        return await _set_oasis_python_workflow(username, name, content, team)
    return await _set_oasis_yaml_workflow(username, name, content, description, save_layout, team)


async def _set_oasis_yaml_workflow(
    username: str = "",
    name: str = "",
    schedule_yaml: str = "",
    description: str = "",
    save_layout: bool = True,
    team: str = "",
) -> str:
    """
    Save a reusable OASIS YAML workflow (Version 2 graph) for
    start_new_oasis(schedule_file=...). Read get_workflow_rules("yaml") first;
    the YAML must have a top-level `plan`.

    Args:
        username: (auto-injected) do NOT set manually
        name: workflow filename, e.g. "code_review" (".yaml" appended if missing)
        schedule_yaml: the Version-2 YAML content
        description: optional one-line description, saved as a header comment
        save_layout: also generate a visual layout (default True)
        team: save under this team's directory instead of the user's
    """
    effective_user = _resolve_effective_user(username)
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


async def _set_oasis_python_workflow(
    username: str = "",
    name: str = "",
    python_code: str = "",
    team: str = "",
) -> str:
    """
    Save a standalone Python workflow so it can be reused later via start_new_oasis(python_file="name.py").

    Python workflows are stored under data/user_files/{user}/oasis/python/
    (or teams/{team}/oasis/python/ when team is set).
    Use list_oasis_workflows(kind="python") to see saved Python workflows.

    :param name: Workflow file name; ".py" is appended if missing
    :param python_code: Full source of the standalone Python workflow
    :param team: Optional team; when set, save into the team's workflow directory
    """
    effective_user = _resolve_effective_user(username)
    safe_name = "".join(c for c in str(name or "") if c.isalnum() or c in "-_ ").strip() or "untitled"
    if not python_code.strip():
        return "❌ python_code 不能为空"
    workflow_dir = _workflow_python_dir(effective_user, team)
    os.makedirs(workflow_dir, exist_ok=True)
    filename = safe_name if safe_name.endswith(".py") else f"{safe_name}.py"
    path = os.path.join(workflow_dir, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(python_code if python_code.endswith("\n") else python_code + "\n")
        lines = ["✅ Python workflow 已保存"]
        lines.append(f"  文件: {filename}")
        lines.append(f"  路径: {path}")
        if team:
            lines.append(f"  Team: {team}")
        lines.append(f"\n💡 使用方式: start_new_oasis(python_file=\"{filename}\", ...)")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 保存失败: {e}"

@mcp.tool()
async def list_oasis_workflows(username: str = "", team: str = "", kind: Literal["yaml", "python", "all"] = "all") -> str:
    """
    List saved OASIS workflows (YAML and/or Python) for the current user.

    Args:
        username: (auto-injected) current user identity; do NOT set manually
        team: Team name. When provided, lists workflows from the team directory.
        kind: "yaml", "python", or "all"
    """
    if kind == "python":
        return await _list_oasis_python_workflows(username, team)
    if kind == "all":
        yaml_part = await list_oasis_workflows(username, team, "yaml")
        python_part = await _list_oasis_python_workflows(username, team)
        return f"{yaml_part}\n\n{python_part}"
    effective_user = _resolve_effective_user(username)
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
async def get_workflow_rules(kind: Literal["python", "yaml"]) -> str:
    """
    Return the canonical authoring rules for an OASIS workflow — the workflowpy
    ctx API and constraints, or the YAML graph format. Call this BEFORE
    writing or editing one.

    :param kind: Which workflow format: "python" or "yaml"
    """
    if kind == "yaml":
        return await _yaml_workflow_rules()
    return await _python_workflow_rules()


async def _python_workflow_rules() -> str:
    """
    Return the canonical authoring rules for ClawCross/OASIS Python workflows
    (workflowpy). Call this BEFORE writing or editing a workflow file so you
    follow the exact bootstrap, ctx API, and orchestration constraints.

    The rules cover:
      • Required structure (`from oasis.workflow import Context, workflow`,
        `@workflow` decorator)
      • Context API: identity, list/get helpers, send_agent / send_agent_once /
        send_persona / call_llm, publish, topics, set_conclusion / set_result
      • SendToAgentResult attribute access
      • Agent vs persona selection
      • Multi-round prompt splicing
      • Hard constraints (no sys.path / PYTHONPATH bootstrap, no direct
        python_workflow_cli import, etc.)
    """
    try:
        from oasis.workflow_rules import get_workflow_writing_rules as _rules
        return _rules()
    except Exception as e:
        return f"❌ 读取 workflow 规则失败: {e}"


_YAML_WORKFLOW_RULES_FALLBACK = """\
# OASIS YAML Workflow (Version 2 — Graph Mode)

A workflow is a directed graph: `plan` lists nodes, `edges` define order.
The schedule MUST contain a top-level `plan` key.

version: 2
repeat: false
plan:
  - id: n1                         # every node needs a unique id
    persona: creative              # a temporary agent with this persona
    instruction: "optional task for this step"
  - id: n2
    agent: Reviewer                # an agent you have: role name, handle, address or ag_ id
  - id: done
    manual: {author: bend, content: "wrap-up text"}   # manual/no-LLM node
edges:
  - [n1, n2]                       # fixed edge; fan-in waits for ALL predecessors
  - [n2, done]

Participants:
  agent: <ref>        an agent you have (WeBot, codex, claude, openclaw, …); in a team, its role name
  persona: <tag>      a temporary agent with a persona from the library, gone when the topic ends
    tools: none|all|[names]   none (default): one model call per turn; else a temporary WeBot session
    instance: N               tells same-persona participants apart

Manual authors: begin (start), bend (end), or any string (speaker name).
Step types: agent / persona | parallel: [...] | all_experts: true | manual | script | human.
Branching: conditional_edges (source/condition/then/else) and selector_edges
(node with selector: true; choices map LLM output → branch; __end__ terminates).
Conditions: last_post_contains:<kw>, last_post_not_contains:<kw>,
post_count_gte:<N>, post_count_lt:<N>, always, !<expr>.
"""


async def _yaml_workflow_rules() -> str:
    """
    Return the canonical authoring spec for OASIS **YAML** workflows
    (Version 2 graph format): node/step types, persona ref formats, edges /
    conditional_edges / selector_edges, and graph rules.

    Call this BEFORE writing or editing a YAML workflow with
    save_oasis_workflow so the schedule validates (it MUST contain a
    top-level `plan`).
    """
    from pathlib import Path as _Path
    candidates = [
        PROJECT_ROOT / "docs" / "create_workflow.md",
        _Path(str(WORKSPACE_DIR)) / "docs" / "create_workflow.md",
    ]
    for p in candidates:
        try:
            if p.is_file():
                return p.read_text(encoding="utf-8")
        except Exception:
            continue
    return _YAML_WORKFLOW_RULES_FALLBACK


async def _list_oasis_python_workflows(username: str = "", team: str = "") -> str:
    """
    List all saved Python workflows for the current user.

    Args:
        username: (auto-injected) current user identity; do NOT set manually
        team: Team name. When provided, lists workflows from the team directory.
    """
    effective_user = _resolve_effective_user(username)
    try:
        items: list[dict] = []
        for scope, team_name, py_dir in _iter_python_workflow_dirs(effective_user, team):
            if not os.path.isdir(py_dir):
                continue
            files = sorted(f for f in os.listdir(py_dir) if f.endswith(".py"))
            for fname in files:
                path = os.path.join(py_dir, fname)
                first_line = ""
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        first_line = f.readline().strip()
                except Exception:
                    pass
                items.append({
                    "file": fname,
                    "preview": first_line[:90],
                    "scope": scope,
                    "team": team_name,
                })

        if not items:
            return "📭 暂无保存的 Python workflow"

        lines = [f"🐍 已保存的 Python Workflows — 共 {len(items)} 个\n"]
        for item in items:
            location = f"[team:{item['team']}]" if item["scope"] == "team" else "[personal]"
            preview = item.get("preview", "")
            lines.append(f"  • {location} {item.get('file')}" + (f"  — {preview}" if preview else ""))
        if team:
            lines.append(f"\n💡 当前只显示 team=\"{team}\" 下的 Python workflows。")
        else:
            lines.append("\n💡 未指定 team，已展示个人目录和全部 team 的 Python workflows。")
        lines.append("💡 使用: start_new_oasis(python_file=\"文件名\", ...)")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 查询失败: {e}"


async def _check_oasis_python_run(run_id: str = "", username: str = "", team: str = "") -> str:
    """
    Check the result of a Python workflow run started via
    start_new_oasis(python_file=...); with no run_id, list recent runs.

    Args:
        run_id: run_id returned by start_new_oasis in Python mode; empty lists recent runs
        username: (auto-injected) current user identity; do NOT set manually
        team: When listing, only show runs of this team
    """
    if not (run_id or "").strip():
        return await _list_oasis_python_runs(username, team)
    data, err = _load_python_run_payload(run_id)
    if err:
        return f"❌ {err}"
    assert data is not None
    effective_user = _resolve_effective_user(username)
    if str(data.get("user_id") or "") != effective_user:
        return f"❌ 无权查看此运行: {run_id}"

    lines = ["🐍 Python Workflow 运行结果"]
    lines.append(f"Run ID: {data.get('run_id')}")
    if "ok" not in data:
        lines.append(f"状态: {'⏳ 运行中' if data.get('_running') else '❓ 未完成'}")
    else:
        lines.append(f"状态: {'✅ 成功' if data.get('ok') else '❌ 失败'}")
    lines.append(f"Question: {data.get('question', '')}")
    lines.append(f"User: {data.get('user_id', '')}")
    if data.get("team"):
        lines.append(f"Team: {data.get('team')}")
    if data.get("topic_id"):
        lines.append(f"Topic ID: {data.get('topic_id')}")
    if data.get("conclusion"):
        lines.extend(["", "🏆 结论", str(data.get("conclusion"))])
    if data.get("error"):
        lines.extend(["", "❌ 错误", str(data.get("error"))])
    result = data.get("result")
    if result is not None:
        try:
            rendered = json.dumps(result, ensure_ascii=False, indent=2)
        except Exception:
            rendered = str(result)
        lines.extend(["", "📦 Result", rendered[:4000]])
    messages = data.get("published_messages") or []
    if messages:
        lines.append("")
        lines.append("--- 最近消息 ---")
        for item in messages[-5:]:
            author = item.get("author", "workflowpy")
            content = str(item.get("content", "") or "")
            preview = content[:160] + ("..." if len(content) > 160 else "")
            lines.append(f"  • {author}: {preview}")
    lines.append("")
    lines.append(f"Log: {data.get('_log_file')}")
    lines.append(f"Result File: {data.get('_result_file')}")
    if data.get("_meta_file"):
        lines.append(f"Meta File: {data.get('_meta_file')}")
    return "\n".join(lines)


async def _list_oasis_python_runs(username: str = "", team: str = "") -> str:
    """
    List recent standalone Python workflow runs started via MCP.

    Args:
        username: (auto-injected) current user identity; do NOT set manually
        team: Optional team filter
    """
    effective_user = _resolve_effective_user(username)
    runs_dir = _python_runs_dir()
    if not os.path.isdir(runs_dir):
        return "📭 暂无 Python workflow 运行记录"
    items: list[dict[str, Any]] = []
    for fname in sorted(os.listdir(runs_dir), reverse=True):
        if not fname.endswith(".meta.json"):
            continue
        path = os.path.join(runs_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if not isinstance(meta, dict):
                continue
        except Exception:
            continue
        if str(meta.get("user_id") or "") != effective_user:
            continue
        if team and str(meta.get("team") or "") != team:
            continue
        run_id = str(meta.get("run_id") or "")
        result, _ = _load_python_run_payload(run_id)
        status = "running"
        if result and "ok" in result:
            status = "ok" if result.get("ok") else "error"
        elif result and result.get("_running"):
            status = "running"
        elif result:
            status = "pending"
        items.append({
            "run_id": run_id,
            "question": str(meta.get("question") or ""),
            "team": str(meta.get("team") or ""),
            "status": status,
        })
    if not items:
        return "📭 暂无 Python workflow 运行记录"

    lines = [f"🐍 Python Workflow Runs — 共 {len(items)} 个\n"]
    for item in items[:20]:
        team_label = f"[team:{item['team']}]" if item.get("team") else "[personal]"
        lines.append(f"  • {team_label} {item['run_id']} | {item['status']} | {item['question'][:70]}")
    lines.append("\n💡 查看详情: check_oasis_discussion(topic_id=\"<run_id>\")")
    lines.append("💡 取消: cancel_oasis_discussion(topic_id=\"<run_id>\")")
    return "\n".join(lines)


async def _cancel_oasis_python_run(run_id: str, username: str = "") -> str:
    """
    Cancel a standalone Python workflow run by PID when it is still running.

    Args:
        run_id: The run_id returned by start_new_oasis in Python mode
        username: (auto-injected) current user identity; do NOT set manually
    """
    effective_user = _resolve_effective_user(username)
    data, err = _load_python_run_payload(run_id)
    if err:
        return f"❌ {err}"
    assert data is not None
    if str(data.get("user_id") or "") != effective_user:
        return f"❌ 无权取消此运行: {run_id}"
    pid = int(data.get("pid") or 0)
    if pid <= 0:
        return f"❌ 运行缺少 PID: {run_id}"
    if not data.get("_running"):
        cancelled_topic = await _cancel_oasis_topic_for_python_run(data, effective_user)
        if cancelled_topic:
            return f"ℹ️ Python workflow 进程已结束\nRun ID: {run_id}\nOASIS Topic 已同步取消: {cancelled_topic}"
        return f"ℹ️ 运行已结束，无需取消: {run_id}"
    try:
        os.killpg(pid, 15)
    except Exception:
        try:
            os.kill(pid, 15)
        except Exception as e:
            return f"❌ 取消失败: {e}"
    cancelled_topic = await _cancel_oasis_topic_for_python_run(data, effective_user)
    result_file = data.get("_result_file") or data.get("result_file")
    if result_file:
        try:
            payload = {
                "ok": False,
                "run_id": re.sub(r"[^a-zA-Z0-9]", "", str(run_id or "").strip()),
                "question": data.get("question", ""),
                "user_id": data.get("user_id", ""),
                "team": data.get("team", ""),
                "topic_id": data.get("topic_id") or cancelled_topic or None,
                "error": "cancelled",
                "cancelled": True,
                "published_messages": data.get("published_messages") or [],
            }
            with open(str(result_file), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
        except Exception:
            pass
    lines = [f"🛑 Python workflow 已发送终止信号", f"Run ID: {run_id}", f"PID: {pid}"]
    if cancelled_topic:
        lines.append(f"OASIS Topic 已同步取消: {cancelled_topic}")
    return "\n".join(lines)


@mcp.tool()
async def list_oasis_agent_catalog(username: str = "", team: str = "") -> str:
    """
    List the agents a Python workflow (and a YAML ``agent:`` step) can call:
    the team's members with their role names, or all of the user's agents.

    :param team: Optional team whose agents to list; empty lists the user's own
    """
    effective_user = _resolve_effective_user(username)
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
                f"  • {item.get('role') or item.get('name')} — {item.get('address')} ({item.get('platform')}, {item.get('agent_id')})"
            )
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
    Get public network info — tunnel status, public URL, ports — e.g. to share
    the public link with the user. Read-only: it never starts the tunnel or
    downloads cloudflared; the tunnel manager requires an existing installation.

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
        domain = tunnel.get("public_domain", "")
        ports = data.get("ports", {})

        lines = ["📡 系统信息\n"]

        # Tunnel info
        if tunnel.get("running"):
            lines.append("🌐 公网隧道: ✅ 运行中")
            if not domain:
                lines.append("   ⏳ 公网地址尚未就绪")
            lines.append(f"   PID: {tunnel.get('pid')}")
        else:
            lines.append("🌐 公网隧道: ❌ 未运行")
            if not domain:
                lines.append("   💡 可通过 launch/run.sh start-tunnel 启动")
                lines.append("   💡 或在前端 Settings 面板中点击「启动隧道」")
        if domain:
            lines.append(f"🌍 已配置公网入口: {domain}")
            lines.append("   地址来自运行配置；本工具不验证外网可达性。")

        # Ports
        lines.append(f"\n📌 端口:")
        lines.append(f"   前端: {ports.get('frontend', '?')}")
        lines.append(f"   OASIS: {ports.get('oasis', '?')}")

        return "\n".join(lines)
    except httpx.ConnectError:
        return _CONN_ERR
    except Exception as e:
        return f"❌ 查询失败: {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
