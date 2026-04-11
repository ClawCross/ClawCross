"""
OpenClaw Agent 管理路由

从 oasis/server.py 提取的 /sessions/openclaw/* 端点集合。
包含 agent 列表、创建、配置、快照、恢复、技能管理、频道绑定等功能。
"""

import json
import os
import shutil
import subprocess
import time

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from src.utils.logging_utils import get_logger
from src.integrations.restore_timing_log import append_agent_restore_record

from oasis.openclaw_cli import (
    build_agent_detail,
    fetch_openclaw_channels,
    fetch_openclaw_full_config,
    get_openclaw_default_workspace,
    get_openclaw_workspace_path,
    load_openclaw_root_config,
    sanitize_openclaw_root_agents_tools_profiles,
    sanitize_tools_dict,
    save_openclaw_root_config,
)

router = APIRouter()

# --- 全局状态（由 init_openclaw_routes 注入） ---
_openclaw_bin: str | None = None
_get_env = None
_openclaw_skills_cache: dict = {}
_openclaw_managed_skills_dir: str = ""
_openclaw_bundled_skills: list = []

_logger_restore = get_logger("oasis.openclaw_restore")

# --- 静态常量 ---
_OPENCLAW_CORE_FILES = [
    "IDENTITY.md",
    "TOOLS.md",
    "AGENTS.md",
    "custom_instructions.md",
    ".claude/settings.local.json",
    ".claude/CLAUDE.md",
]

_OPENCLAW_TOOL_GROUPS = {
    "code": {
        "description": "Code editing (Read/Write/Edit)",
        "tools": ["read", "write", "edit", "apply_patch", "nodes"],
    },
    "terminal": {
        "description": "Terminal / shell commands",
        "tools": ["exec", "bash", "process"],
    },
    "browser": {
        "description": "Web browser access",
        "tools": ["browser", "canvas", "web_search", "web_fetch"],
    },
    "mcp": {
        "description": "MCP server tools",
        "tools": ["sessions_list", "session_status"],
    },
}

_OPENCLAW_TOOL_PROFILES = {
    "safe": {"description": "Read-only (no writes)", "groups": ["code"]},
    "default": {"description": "Standard development", "groups": ["code", "terminal"]},
    "full": {"description": "Unrestricted (all tools)", "groups": list(_OPENCLAW_TOOL_GROUPS.keys())},
}


def init_openclaw_routes(
    *,
    openclaw_bin: str | None,
    get_env_fn,
    skills_cache: dict,
    managed_skills_dir: str,
    bundled_skills: list,
) -> APIRouter:
    """初始化 OpenClaw 路由模块的全局状态并返回 router。"""
    global _openclaw_bin, _get_env, _openclaw_skills_cache
    global _openclaw_managed_skills_dir, _openclaw_bundled_skills
    _openclaw_bin = openclaw_bin
    _get_env = get_env_fn
    _openclaw_skills_cache = skills_cache
    _openclaw_managed_skills_dir = managed_skills_dir
    _openclaw_bundled_skills = bundled_skills
    return router


# ============================================================
# 辅助函数
# ============================================================

def _fetch_config() -> dict | None:
    return fetch_openclaw_full_config(_openclaw_bin)


def _build_detail(agent_cfg: dict, defaults: dict) -> dict:
    return build_agent_detail(agent_cfg, defaults)


def _get_default_workspace() -> str | None:
    return get_openclaw_default_workspace(_openclaw_bin)


def _get_workspace_path():
    return get_openclaw_workspace_path(_openclaw_bin)


def _get_agents_from_config() -> list[dict] | None:
    full_config = _fetch_config()
    if full_config is None:
        return None
    defaults = full_config.get("defaults", {})
    agents = []
    for entry in full_config.get("list", []):
        detail = _build_detail(entry, defaults)
        model_val = detail.get("model", "")
        if isinstance(model_val, dict):
            model_val = model_val.get("primary", "")
        agents.append({
            "name": detail.get("id", ""),
            "model": model_val,
            "workspace": detail.get("workspace", ""),
            "is_default": detail.get("is_default", False),
        })
    return agents


# ============================================================
# 路由
# ============================================================

@router.get("/sessions/openclaw")
async def list_openclaw_agents(filter: str = Query("")):
    """列出 OpenClaw agents。"""
    full_config = _fetch_config()
    if full_config is None:
        return {"agents": [], "available": False,
                "message": "openclaw CLI not available or command failed"}

    defaults = full_config.get("defaults", {})
    agents = []
    for entry in full_config.get("list", []):
        detail = _build_detail(entry, defaults)
        agents.append(detail)

    if filter:
        agents = [a for a in agents if filter.lower() in a.get("id", "").lower()]

    agents.sort(key=lambda a: (not a.get("is_default", False), a.get("id", "")))

    result = []
    for a in agents:
        model_val = a.get("model", "")
        if isinstance(model_val, dict):
            model_val = model_val.get("primary", "")
        result.append({
            "name": a.get("id", ""),
            "model": model_val,
            "workspace": a.get("workspace", ""),
            "is_default": a.get("is_default", False),
            "tools": a.get("tools", {}),
            "skills": a.get("skills", []),
            "skills_all": a.get("skills_all", True),
        })

    raw_url = _get_env("OPENCLAW_API_URL", "")
    base_url = raw_url.replace("/v1/chat/completions", "").rstrip("/")

    return {
        "agents": result,
        "available": True,
        "openclaw_api_url": base_url,
    }


@router.get("/sessions/openclaw/default-workspace")
async def get_openclaw_default_workspace_route():
    """返回默认 workspace 路径。"""
    if not _openclaw_bin:
        return JSONResponse({"ok": False, "error": "openclaw CLI not available"}, status_code=500)
    default_ws = _get_default_workspace()
    if not default_ws:
        return {"ok": True, "parent_dir": "", "default_workspace": ""}
    parent_dir = os.path.dirname(default_ws.rstrip("/"))
    return {"ok": True, "parent_dir": parent_dir, "default_workspace": default_ws}


@router.post("/sessions/openclaw/add")
async def add_openclaw_agent(req: Request):
    """创建新 OpenClaw agent。"""
    if not _openclaw_bin:
        return JSONResponse({"ok": False, "error": "openclaw CLI not available"}, status_code=500)

    body = await req.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "Agent name is required"}, status_code=400)

    import re
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return JSONResponse(
            {"ok": False, "error": "Agent name can only contain letters, numbers, underscores and hyphens"},
            status_code=400,
        )

    existing = _get_agents_from_config()
    if existing and any(a.get("name") == name for a in existing):
        return JSONResponse({"ok": False, "error": f"Agent '{name}' already exists"}, status_code=409)

    custom_ws = (body.get("workspace") or "").strip()
    if custom_ws:
        new_workspace = os.path.expanduser(custom_ws)
    else:
        default_ws = _get_default_workspace()
        if default_ws:
            parent_dir = os.path.dirname(default_ws.rstrip("/"))
            new_workspace = os.path.join(parent_dir, f"workspace-{name}")
        else:
            new_workspace = os.path.expanduser(f"~/workspace-{name}")

    try:
        result = subprocess.run(
            [_openclaw_bin, "agents", "add", name, "--workspace", new_workspace, "--non-interactive"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            err_msg = (result.stderr or result.stdout or "Unknown error").strip()[:500]
            return JSONResponse({"ok": False, "error": err_msg}, status_code=500)

        return {"ok": True, "name": name, "workspace": new_workspace,
                "message": f"Agent '{name}' created successfully"}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/sessions/openclaw/workspace-files")
async def list_openclaw_workspace_files(workspace: str = Query(...)):
    """列出 workspace 中的文件。"""
    ws_path = os.path.expanduser(workspace)
    if not os.path.isdir(ws_path):
        return JSONResponse({"ok": False, "error": "Workspace not found"}, status_code=404)
    # 先以核心文件列表为基础，标注存在状态；再追加 workspace 中其余文件
    seen = set()
    files = []
    for core_name in _OPENCLAW_CORE_FILES:
        core_path = os.path.join(ws_path, core_name)
        seen.add(core_name)
        if os.path.isfile(core_path):
            try:
                size = os.path.getsize(core_path)
                files.append({"name": core_name, "exists": True, "size": size})
            except Exception:
                files.append({"name": core_name, "exists": False, "size": 0})
        else:
            files.append({"name": core_name, "exists": False, "size": 0})

    for item in sorted(os.listdir(ws_path)):
        if item in seen:
            continue
        item_path = os.path.join(ws_path, item)
        if os.path.isfile(item_path):
            try:
                size = os.path.getsize(item_path)
                files.append({"name": item, "exists": True, "size": size})
            except Exception:
                pass
        elif os.path.isdir(item_path):
            files.append({"name": item + "/", "is_dir": True, "exists": True})
    return {"ok": True, "files": files}


@router.get("/sessions/openclaw/workspace-file")
async def read_openclaw_workspace_file(workspace: str = Query(...), filename: str = Query(...)):
    """读取 workspace 中的文件内容。"""
    ws_path = os.path.expanduser(workspace)
    file_path = os.path.join(ws_path, filename)
    if not os.path.isfile(file_path):
        return JSONResponse({"ok": False, "error": "File not found"}, status_code=404)
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"ok": True, "filename": filename, "content": content}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/sessions/openclaw/workspace-file")
async def write_openclaw_workspace_file(req: Request):
    """写入 workspace 中的文件内容。"""
    body = await req.json()
    workspace = (body.get("workspace") or "").strip()
    filename = (body.get("filename") or "").strip()
    content = body.get("content", "")

    if not workspace or not filename:
        return JSONResponse({"ok": False, "error": "workspace and filename are required"}, status_code=400)

    ws_path = os.path.expanduser(workspace)
    file_path = os.path.join(ws_path, os.path.basename(filename))

    try:
        os.makedirs(os.path.dirname(file_path) if "/" in filename else ws_path, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"ok": True, "message": f"File '{filename}' saved"}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/sessions/openclaw/agent-detail")
async def get_openclaw_agent_detail(name: str = Query(...)):
    """返回单个 agent 的详细配置和可用技能。"""
    config = _fetch_config()
    defaults = config.get("defaults", {}) if config else {}
    agent_list = config.get("list", []) if config else []

    detail = None
    for a in agent_list:
        if a.get("id") == name or a.get("name") == name:
            detail = _build_detail(a, defaults)
            break

    if detail is None:
        config_agents = _get_agents_from_config()
        if config_agents:
            for a in config_agents:
                if a.get("name") == name:
                    detail = {
                        "id": a.get("name", name),
                        "name": a.get("name", name),
                        "workspace": a.get("workspace", defaults.get("workspace", "")),
                        "agentDir": "",
                        "is_default": a.get("is_default", False),
                        "model": {},
                        "tools": {"profile": "", "alsoAllow": [], "deny": []},
                        "skills": [],
                        "skills_all": True,
                    }
                    break

    if detail is None:
        return JSONResponse({"ok": False, "error": f"Agent '{name}' not found"}, status_code=404)

    # 收集所有可用技能（3 来源）
    all_skills = []
    workspace_path = detail.get("workspace") or _get_workspace_path()

    if workspace_path:
        skills_dir = os.path.join(workspace_path, "skills")
        if os.path.isdir(skills_dir):
            for item in os.listdir(skills_dir):
                item_path = os.path.join(skills_dir, item)
                if os.path.isdir(item_path):
                    all_skills.append({"name": item, "eligible": True, "source": "workspace", "path": item_path})

    if _openclaw_managed_skills_dir and os.path.isdir(_openclaw_managed_skills_dir):
        existing = {s["name"] for s in all_skills}
        for item in os.listdir(_openclaw_managed_skills_dir):
            if item not in existing:
                item_path = os.path.join(_openclaw_managed_skills_dir, item)
                if os.path.isdir(item_path):
                    all_skills.append({"name": item, "eligible": True, "source": "managed", "path": item_path})

    existing = {s["name"] for s in all_skills}
    for bs in _openclaw_bundled_skills:
        skill_name = bs.get("name", "")
        if skill_name and skill_name not in existing:
            all_skills.append({
                "name": skill_name, "eligible": bs.get("eligible", False),
                "source": "bundled", "description": bs.get("description", ""),
                "emoji": bs.get("emoji", ""), "disabled": bs.get("disabled", False),
                "missing": bs.get("missing", {}),
            })

    all_skills.sort(key=lambda x: x["name"])
    user_skills = [s for s in all_skills if s.get("source") != "bundled"]

    return {"ok": True, "agent": detail, "skills": all_skills, "user_skills": user_skills}


@router.get("/sessions/openclaw/skills")
async def list_openclaw_skills(name: str = Query("", description="Agent name to filter effective skills")):
    """返回可用的 OpenClaw 技能列表。"""
    try:
        skills = []
        agent_workspace = None
        agent_skills_cfg: list | None = None

        if name:
            config = _fetch_config()
            defaults = config.get("defaults", {}) if config else {}
            agent_list = config.get("list", []) if config else []

            for a in agent_list:
                if a.get("id") == name or a.get("name") == name:
                    detail = _build_detail(a, defaults)
                    agent_workspace = detail.get("workspace") or None
                    raw_skills = detail.get("skills")
                    if raw_skills is not None and isinstance(raw_skills, list):
                        agent_skills_cfg = raw_skills
                    break

        workspace = agent_workspace or _get_workspace_path()

        # Workspace skills
        if workspace:
            skills_dir = os.path.join(workspace, "skills")
            if os.path.isdir(skills_dir):
                for item in os.listdir(skills_dir):
                    item_path = os.path.join(skills_dir, item)
                    if os.path.isdir(item_path):
                        skills.append({"name": item, "eligible": True, "source": "workspace", "path": item_path})

        # Managed skills
        if _openclaw_managed_skills_dir and os.path.isdir(_openclaw_managed_skills_dir):
            existing = {s["name"] for s in skills}
            for item in os.listdir(_openclaw_managed_skills_dir):
                if item not in existing:
                    item_path = os.path.join(_openclaw_managed_skills_dir, item)
                    if os.path.isdir(item_path):
                        skills.append({"name": item, "eligible": True, "source": "managed", "path": item_path})

        # Bundled skills
        existing = {s["name"] for s in skills}
        for bs in _openclaw_bundled_skills:
            skill_name = bs.get("name", "")
            if skill_name and skill_name not in existing:
                skills.append({
                    "name": skill_name, "eligible": bs.get("eligible", False),
                    "source": "bundled", "description": bs.get("description", ""),
                    "emoji": bs.get("emoji", ""), "disabled": bs.get("disabled", False),
                    "missing": bs.get("missing", {}),
                })

        # Filter by agent skills config
        if agent_skills_cfg is not None:
            allowed = set(agent_skills_cfg)
            skills = [s for s in skills if s["name"] in allowed]

        skills.sort(key=lambda x: x["name"])
        return {"ok": True, "skills": skills}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/sessions/openclaw/skills-info")
async def list_openclaw_skills_info():
    """返回缓存的完整技能信息。"""
    return {"ok": True, "skills": _openclaw_skills_cache}


@router.get("/sessions/openclaw/tool-groups")
async def list_openclaw_tool_groups():
    """返回可用的工具组和配置文件（静态元数据）。

    groups 的值为工具名数组，前端通过 for (const tn of tools) 遍历。
    """
    return {
        "ok": True,
        "groups": {k: v["tools"] for k, v in _OPENCLAW_TOOL_GROUPS.items()},
        "profiles": {k: v for k, v in _OPENCLAW_TOOL_PROFILES.items()},
    }


@router.post("/sessions/openclaw/update-config")
async def update_openclaw_agent_config(req: Request):
    """更新 agent 的 skills/tools 配置。"""
    if not _openclaw_bin:
        return JSONResponse({"ok": False, "error": "openclaw CLI not available"}, status_code=500)

    body = await req.json()
    agent_name = (body.get("agent_name") or "").strip()
    if not agent_name:
        return JSONResponse({"ok": False, "error": "agent_name is required"}, status_code=400)

    config = _fetch_config()
    if config is None:
        return JSONResponse({"ok": False, "error": "Cannot read openclaw config"}, status_code=500)

    agent_list = config.get("list", [])
    agent_idx = None
    for i, a in enumerate(agent_list):
        if a.get("id") == agent_name or a.get("name") == agent_name:
            agent_idx = i
            break

    if agent_idx is None:
        config_agents = _get_agents_from_config()
        cli_match = None
        if config_agents:
            for a in config_agents:
                if a.get("name") == agent_name:
                    cli_match = a
                    break
        if cli_match is None:
            return JSONResponse({"ok": False, "error": f"Agent '{agent_name}' not found"}, status_code=404)

        agent_idx = len(agent_list)
        init_entry = {
            "id": cli_match.get("id", cli_match.get("name", agent_name)),
            "name": cli_match.get("name", agent_name),
        }
        ws = cli_match.get("workspace", "")
        if ws:
            init_entry["workspace"] = ws
        try:
            result = subprocess.run(
                [_openclaw_bin, "config", "set",
                 f"agents.list[{agent_idx}]", json.dumps(init_entry), "--json"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return JSONResponse(
                    {"ok": False, "error": f"Failed to create config entry: {result.stderr or result.stdout}"},
                    status_code=500,
                )
        except Exception as e:
            return JSONResponse(
                {"ok": False, "error": f"Failed to create config entry: {e}"},
                status_code=500,
            )

    errors = []

    if "skills" in body:
        skills_val = body["skills"]
        if skills_val is None:
            try:
                r = subprocess.run(
                    [_openclaw_bin, "config", "unset", f"agents.list[{agent_idx}].skills"],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode != 0:
                    subprocess.run(
                        [_openclaw_bin, "config", "set", f"agents.list[{agent_idx}].skills", "--json", "null"],
                        capture_output=True, text=True, timeout=10,
                    )
            except Exception as e:
                errors.append(f"skills: {e}")
        else:
            skills_json = json.dumps(skills_val)
            try:
                subprocess.run(
                    [_openclaw_bin, "config", "set", f"agents.list[{agent_idx}].skills", skills_json],
                    capture_output=True, text=True, timeout=10,
                )
            except Exception as e:
                errors.append(f"skills: {e}")

    if "tools" in body:
        tools = body["tools"]
        if isinstance(tools, dict):
            for key in ("profile", "alsoAllow", "deny"):
                if key in tools:
                    try:
                        subprocess.run(
                            [_openclaw_bin, "config", "set",
                             f"agents.list[{agent_idx}].tools.{key}", json.dumps(tools[key])],
                            capture_output=True, text=True, timeout=10,
                        )
                    except Exception as e:
                        errors.append(f"tools.{key}: {e}")

    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=500)
    return {"ok": True, "message": f"Agent '{agent_name}' config updated"}


# ------------------------------------------------------------------
# Channels + Agent binding
# ------------------------------------------------------------------

@router.get("/sessions/openclaw/channels")
async def list_openclaw_channels():
    """返回所有频道及其账号。"""
    data = fetch_openclaw_channels(_openclaw_bin)
    if data is None:
        return JSONResponse({"ok": False, "error": "Cannot read openclaw channels"}, status_code=500)

    chat = data.get("chat", {})
    channels = []
    for channel_name, accounts in chat.items():
        if isinstance(accounts, list):
            for acc in accounts:
                channels.append({
                    "channel": channel_name, "account": acc,
                    "bind_key": f"{channel_name}:{acc}" if acc != "default" else channel_name,
                })
        elif isinstance(accounts, str):
            channels.append({
                "channel": channel_name, "account": accounts,
                "bind_key": f"{channel_name}:{accounts}" if accounts != "default" else channel_name,
            })

    return {"ok": True, "channels": channels, "raw": data}


@router.get("/sessions/openclaw/agent-bindings")
async def get_openclaw_agent_bindings(agent: str = Query(...)):
    """获取 agent 的频道绑定。"""
    if not _openclaw_bin:
        return JSONResponse({"ok": False, "error": "openclaw CLI not available"}, status_code=500)
    try:
        result = subprocess.run(
            [_openclaw_bin, "agents", "list", "--bindings", "--json"],
            capture_output=True, text=True, timeout=45,
        )
        if result.returncode == 0:
            raw = result.stdout
            idx = raw.find('{')
            arr_idx = raw.find('[')
            if idx < 0 or (arr_idx >= 0 and arr_idx < idx):
                idx = arr_idx
            if idx >= 0:
                decoder = json.JSONDecoder()
                data, _ = decoder.raw_decode(raw[idx:])
                agents_list = data if isinstance(data, list) else data.get("agents", data.get("list", []))
                for a in agents_list:
                    aid = a.get("id", a.get("name", ""))
                    if aid == agent:
                        bindings = a.get("bindings", a.get("channels", []))
                        if isinstance(bindings, list):
                            return {"ok": True, "bindings": bindings}
                        elif isinstance(bindings, dict):
                            flat = []
                            for ch, accs in bindings.items():
                                if isinstance(accs, list):
                                    for acc in accs:
                                        flat.append(f"{ch}:{acc}" if acc != "default" else ch)
                                else:
                                    flat.append(f"{ch}:{accs}" if accs != "default" else ch)
                            return {"ok": True, "bindings": flat}
                        detail_bindings = a.get("bindingDetails", a.get("routes", []))
                        if isinstance(detail_bindings, list):
                            flat = []
                            for item in detail_bindings:
                                if not isinstance(item, str):
                                    continue
                                if " accountId=" in item:
                                    ch, acc = item.split(" accountId=", 1)
                                    flat.append(f"{ch}:{acc}" if acc != "default" else ch)
                                elif " " in item:
                                    ch, acc = item.split(" ", 1)
                                    flat.append(f"{ch}:{acc}" if acc != "default" else ch)
                                elif item:
                                    flat.append(item)
                            return {"ok": True, "bindings": flat}
    except Exception as e:
        print(f"  [OASIS] ⚠️ agent bindings parse error: {e}")
    return {"ok": True, "bindings": []}


@router.post("/sessions/openclaw/agent-bind")
async def openclaw_agent_bind(req: Request):
    """绑定或解绑频道到 agent。"""
    if not _openclaw_bin:
        return JSONResponse({"ok": False, "error": "openclaw CLI not available"}, status_code=500)

    body = await req.json()
    agent_name = (body.get("agent") or "").strip()
    channel = (body.get("channel") or "").strip()
    action = (body.get("action") or "bind").strip()

    if not agent_name or not channel:
        return JSONResponse({"ok": False, "error": "agent and channel are required"}, status_code=400)

    cmd_action = "bind" if action == "bind" else "unbind"
    try:
        result = subprocess.run(
            [_openclaw_bin, "agents", cmd_action, "--agent", agent_name, "--bind", channel],
            capture_output=True, text=True, timeout=45,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            return JSONResponse({"ok": False, "error": err[:500]}, status_code=500)
        return {"ok": True, "message": f"Agent '{agent_name}' {cmd_action} '{channel}' success"}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ------------------------------------------------------------------
# OpenClaw agent snapshot
# ------------------------------------------------------------------

@router.get("/sessions/openclaw/agent-snapshot")
async def export_openclaw_agent_snapshot(name: str = Query(...)):
    """导出 agent 完整快照（配置 + workspace 文件）。"""
    config = _fetch_config()
    defaults = config.get("defaults", {}) if config else {}
    agent_list = config.get("list", []) if config else []

    agent_detail = None
    for a in agent_list:
        if a.get("id") == name or a.get("name") == name:
            agent_detail = _build_detail(a, defaults)
            break

    if not agent_detail:
        config_agents = _get_agents_from_config()
        if config_agents:
            for a in config_agents:
                if a.get("name") == name:
                    agent_detail = {
                        "name": a.get("name", name),
                        "workspace": a.get("workspace", defaults.get("workspace", "")),
                        "tools": {"profile": "", "alsoAllow": [], "deny": []},
                        "skills": [],
                        "skills_all": True,
                        "model": {},
                    }
                    break

    if not agent_detail:
        return JSONResponse({"ok": False, "error": f"Agent '{name}' not found"}, status_code=404)

    workspace_files = {}
    ws = agent_detail.get("workspace", "")
    if ws:
        ws_path = os.path.expanduser(ws)
        if os.path.isdir(ws_path):
            for fname in _OPENCLAW_CORE_FILES:
                fpath = os.path.join(ws_path, fname)
                if os.path.isfile(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            workspace_files[fname] = f.read()
                    except Exception:
                        pass

    return {
        "ok": True,
        "agent_name": name,
        "config": {
            "skills": agent_detail.get("skills", []),
            "skills_all": agent_detail.get("skills_all", True),
            "tools": agent_detail.get("tools", {}),
            "model": agent_detail.get("model", {}),
        },
        "workspace_files": workspace_files,
    }


@router.post("/sessions/openclaw/agent-restore")
async def restore_openclaw_agent_snapshot(req: Request):
    """从快照恢复 agent（创建 + 配置 + 写入 workspace 文件）。"""
    if not _openclaw_bin:
        return JSONResponse({"ok": False, "error": "openclaw CLI not available"}, status_code=500)

    body = await req.json()
    agent_name = (body.get("agent_name") or "").strip()
    if not agent_name:
        return JSONResponse({"ok": False, "error": "agent_name is required"}, status_code=400)
    display_name = (body.get("display_name") or "").strip() or agent_name

    snapshot_config = body.get("config", {})
    snapshot_files = body.get("workspace_files", {})
    custom_ws = (body.get("workspace") or "").strip()

    errors = []

    # 若盘上已有非法 tools.profile，CLI 会拒绝 agents add；先就地修复再恢复
    _root_pre = load_openclaw_root_config()
    if _root_pre:
        _nfix = sanitize_openclaw_root_agents_tools_profiles(_root_pre)
        if _nfix > 0:
            if save_openclaw_root_config(_root_pre):
                _logger_restore.info(
                    "[openclaw-restore] sanitized tools.profile on %s agent(s) before restore",
                    _nfix,
                )
            else:
                _logger_restore.warning(
                    "[openclaw-restore] could not save sanitized tools.profile; CLI may still reject config",
                )
    timing_ms: dict[str, float] = {}
    t_wall = time.perf_counter()
    t_start = t_wall

    def _seg(label: str) -> None:
        nonlocal t_wall
        t1 = time.perf_counter()
        timing_ms[label] = round((t1 - t_wall) * 1000, 2)
        t_wall = t1

    # Step 1: 检查 agent 是否存在，不存在则创建
    existing = _get_agents_from_config() or []
    _seg("step1_list_agents_fetch_config")
    agent_exists = any(a.get("name") == agent_name for a in existing)
    workspace = ""

    if not agent_exists:
        if custom_ws:
            new_workspace = os.path.expanduser(custom_ws)
        else:
            default_ws = _get_default_workspace()
            if default_ws:
                parent_dir = os.path.dirname(default_ws.rstrip("/"))
                new_workspace = os.path.join(parent_dir, f"workspace-{agent_name}")
            else:
                new_workspace = os.path.expanduser(f"~/workspace-{agent_name}")

        try:
            result = subprocess.run(
                [_openclaw_bin, "agents", "add", agent_name, "--workspace", new_workspace, "--non-interactive"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                err_msg = (result.stderr or result.stdout or "Unknown error").strip()[:500]
                errors.append(f"Create agent failed: {err_msg}")
            else:
                workspace = new_workspace
        except Exception as e:
            errors.append(f"Create agent failed: {e}")
        _seg("step1b_openclaw_agents_add")
    else:
        for a in existing:
            if a.get("name") == agent_name:
                workspace = a.get("workspace", "")
                break
        _seg("step1b_existing_workspace_lookup")

    # Step 2: 更新 skills/tools（优先直接读写 openclaw.json，避免多次 config get/set CLI）
    if snapshot_config:
        root_cfg = load_openclaw_root_config()
        use_file = root_cfg is not None and isinstance(root_cfg.get("agents"), dict)
        if use_file:
            config = root_cfg["agents"]
        else:
            config = _fetch_config()
        _seg("step2a_fetch_config")

        agent_list = config.setdefault("list", []) if config else []
        agent_idx = None
        for i, a in enumerate(agent_list):
            if a.get("id") == agent_name or a.get("name") == agent_name:
                agent_idx = i
                break

        if use_file:
            if agent_idx is None:
                agent_idx = len(agent_list)
                init_entry = {"id": agent_name, "name": display_name}
                if workspace:
                    init_entry["workspace"] = workspace
                agent_list.append(init_entry)
            _seg("step2b_config_set_list_entry")

            entry = agent_list[agent_idx]
            entry["id"] = agent_name
            entry["name"] = display_name
            skills_val = snapshot_config.get("skills")
            skills_all = snapshot_config.get("skills_all", False)
            if skills_all:
                entry.pop("skills", None)
            elif skills_val is not None:
                entry["skills"] = skills_val
            _seg("step2c_config_set_skills")

            tools_cfg = snapshot_config.get("tools", {})
            if tools_cfg:
                entry["tools"] = sanitize_tools_dict(tools_cfg)
            _seg("step2d_config_set_tools")

            if not save_openclaw_root_config(root_cfg):
                errors.append("Save openclaw.json failed (skills/tools not persisted)")
        else:
            if agent_idx is None:
                agent_idx = len(agent_list)
                init_entry = {"id": agent_name, "name": display_name}
                if workspace:
                    init_entry["workspace"] = workspace
                try:
                    subprocess.run(
                        [_openclaw_bin, "config", "set",
                         f"agents.list[{agent_idx}]", json.dumps(init_entry), "--json"],
                        capture_output=True, text=True, timeout=10,
                    )
                except Exception as e:
                    errors.append(f"Create config entry failed: {e}")
            _seg("step2b_config_set_list_entry")

            skills_val = snapshot_config.get("skills")
            skills_all = snapshot_config.get("skills_all", False)
            if skills_all:
                try:
                    subprocess.run(
                        [_openclaw_bin, "config", "set",
                         f"agents.list[{agent_idx}].skills", "--delete", "--json"],
                        capture_output=True, text=True, timeout=10,
                    )
                except Exception:
                    pass
            elif skills_val is not None:
                try:
                    subprocess.run(
                        [_openclaw_bin, "config", "set",
                         f"agents.list[{agent_idx}].skills", json.dumps(skills_val), "--json"],
                        capture_output=True, text=True, timeout=10,
                    )
                except Exception as e:
                    errors.append(f"Set skills failed: {e}")
            _seg("step2c_config_set_skills")

            tools_cfg = snapshot_config.get("tools", {})
            if tools_cfg:
                tools_cfg = sanitize_tools_dict(tools_cfg)
                try:
                    subprocess.run(
                        [_openclaw_bin, "config", "set",
                         f"agents.list[{agent_idx}].tools", json.dumps(tools_cfg), "--json"],
                        capture_output=True, text=True, timeout=10,
                    )
                except Exception as e:
                    errors.append(f"Set tools failed: {e}")
            _seg("step2d_config_set_tools")
            if display_name != agent_name:
                try:
                    subprocess.run(
                        [_openclaw_bin, "config", "set",
                         f"agents.list[{agent_idx}].name", json.dumps(display_name), "--json"],
                        capture_output=True, text=True, timeout=10,
                    )
                except Exception:
                    pass
    else:
        _seg("step2_skip_no_snapshot_config")

    # Step 3: 写入 workspace 文件
    n_ws_files = 0
    if workspace and snapshot_files:
        ws_path = os.path.expanduser(workspace)
        os.makedirs(ws_path, exist_ok=True)
        for fname, content in snapshot_files.items():
            safe_name = os.path.basename(fname)
            fpath = os.path.join(ws_path, safe_name)
            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(content)
                n_ws_files += 1
            except Exception as e:
                errors.append(f"Write {safe_name} failed: {e}")
        _seg("step3_write_workspace_files")
    else:
        _seg("step3_skip_no_files_or_workspace")

    timing_ms["total_ms"] = round((time.perf_counter() - t_start) * 1000, 2)

    _logger_restore.info(
        "[openclaw-restore] agent=%s ok=%s total_ms=%.1f timing_ms=%s errors=%s",
        agent_name,
        len(errors) == 0,
        timing_ms["total_ms"],
        timing_ms,
        errors,
    )

    ok_restore = len(errors) == 0
    timing_path = append_agent_restore_record(
        agent_name=agent_name,
        ok=ok_restore,
        restore_timing_ms=timing_ms,
        restore_workspace_files_written=n_ws_files,
        errors=errors,
    )
    if timing_path:
        _logger_restore.info("[openclaw-restore] timing file=%s", timing_path)

    return {
        "ok": ok_restore,
        "agent_name": agent_name,
        "display_name": display_name,
        "workspace": workspace,
        "errors": errors,
        "restore_timing_ms": timing_ms,
        "restore_workspace_files_written": n_ws_files,
        "restore_timing_log": timing_path,
        "message": f"Agent '{agent_name}' restored" + (f" with {len(errors)} error(s)" if errors else " successfully"),
    }


@router.get("/sessions/openclaw/remove")
async def remove_openclaw_agent(name: str = Query(...)):
    """删除 OpenClaw agent。"""
    if not _openclaw_bin:
        return JSONResponse({"ok": False, "error": "openclaw CLI not available"}, status_code=500)
    agent_name = (name or "").strip()
    if not agent_name:
        return JSONResponse({"ok": False, "error": "Agent name is required"}, status_code=400)
    if agent_name.lower() == "main":
        return JSONResponse({"ok": False, "error": "The main agent cannot be deleted"}, status_code=400)

    try:
        root = load_openclaw_root_config()
        if root:
            nfix = sanitize_openclaw_root_agents_tools_profiles(root)
            if nfix > 0:
                if not save_openclaw_root_config(root):
                    return JSONResponse(
                        {"ok": False, "error": "Failed to repair openclaw.json (tools.profile); cannot run agents delete"},
                        status_code=500,
                    )
                _logger_restore.info(
                    "[openclaw-remove] sanitized tools.profile on %s agent(s) before delete",
                    nfix,
                )

        result = subprocess.run(
            [_openclaw_bin, "agents", "delete", agent_name, "--force", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            return JSONResponse({"ok": False, "error": err[:500]}, status_code=500)
        return {"ok": True, "message": f"Agent '{agent_name}' removed"}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
