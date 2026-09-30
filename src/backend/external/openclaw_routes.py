"""Managing the OpenClaw agents of the local OpenClaw install: ``/sessions/openclaw/*``.

List, create, configure (skills / tools), bind channels, snapshot, restore and
remove them, and edit their workspace files. Served by the Agent service, through
the agent layer's router. OpenClaw agents belong to the machine, not to a user: only
this machine's ClawCross services (``X-Internal-Token``) manage them, and file access
stays inside an OpenClaw agent's workspace.
"""

import asyncio
import json
import os
import re
import secrets
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from common.env_settings import read_env_settings
from common.logging_utils import get_logger
from common.runtime_paths import ENV_FILE, LOGS_DIR
from external import openclaw_config as oc

logger = get_logger("external.openclaw_routes")

_CORE_FILES = [
    "IDENTITY.md",
    "TOOLS.md",
    "AGENTS.md",
    "custom_instructions.md",
    ".claude/settings.local.json",
    ".claude/CLAUDE.md",
]

_TOOL_GROUPS = {
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

_TOOL_PROFILES = {
    "safe": {"description": "Read-only (no writes)", "groups": ["code"]},
    "default": {"description": "Standard development", "groups": ["code", "terminal"]},
    "full": {"description": "Unrestricted (all tools)", "groups": list(_TOOL_GROUPS.keys())},
}


def _no_cli() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "openclaw CLI not available"}, status_code=500)


def _find_agent(config: dict | None, name: str) -> tuple[int | None, dict | None]:
    """(index in agents.list, detail) of the agent whose id or name is ``name``."""
    config = config or {}
    for i, a in enumerate(config.get("list", [])):
        if a.get("id") == name or a.get("name") == name:
            return i, oc.agent_detail(a, config.get("defaults", {}))
    return None, None


def _agent_workspace(workspace: str) -> str | None:
    """The real path of ``workspace`` if it is an OpenClaw agent's (or the default) workspace."""
    config = oc.agents_config() or {}
    defaults = config.get("defaults", {})
    known = [oc.agent_detail(a, defaults)["workspace"] for a in config.get("list", [])]
    known.append(defaults.get("workspace", ""))
    path = os.path.realpath(os.path.expanduser(workspace))
    return path if any(ws and os.path.realpath(os.path.expanduser(ws)) == path for ws in known) else None


def _file_in(ws_path: str, filename: str) -> str | None:
    """The real path of ``filename`` under the workspace ``ws_path``; None if it leads out of it."""
    path = os.path.realpath(os.path.join(ws_path, filename))
    return path if path != ws_path and os.path.commonpath([ws_path, path]) == ws_path else None


def _new_workspace(name: str, custom_ws: str) -> str:
    if custom_ws:
        return os.path.expanduser(custom_ws)
    default_ws = oc.default_workspace()
    if default_ws:
        return os.path.join(os.path.dirname(default_ws.rstrip("/")), f"workspace-{name}")
    return os.path.expanduser(f"~/workspace-{name}")


def _all_skills(workspace: str | None) -> list[dict]:
    """Workspace skills, then managed, then bundled; a name appears once."""
    skills = []
    if workspace:
        skills_dir = os.path.join(workspace, "skills")
        if os.path.isdir(skills_dir):
            for item in os.listdir(skills_dir):
                item_path = os.path.join(skills_dir, item)
                if os.path.isdir(item_path):
                    skills.append({"name": item, "eligible": True, "source": "workspace", "path": item_path})

    managed_dir = oc.managed_skills_dir
    if managed_dir and os.path.isdir(managed_dir):
        existing = {s["name"] for s in skills}
        for item in os.listdir(managed_dir):
            item_path = os.path.join(managed_dir, item)
            if item not in existing and os.path.isdir(item_path):
                skills.append({"name": item, "eligible": True, "source": "managed", "path": item_path})

    existing = {s["name"] for s in skills}
    for bs in oc.bundled_skills:
        skill_name = bs.get("name", "")
        if skill_name and skill_name not in existing:
            skills.append({
                "name": skill_name, "eligible": bs.get("eligible", False),
                "source": "bundled", "description": bs.get("description", ""),
                "emoji": bs.get("emoji", ""), "disabled": bs.get("disabled", False),
                "missing": bs.get("missing", {}),
            })
    skills.sort(key=lambda x: x["name"])
    return skills


def _append_restore_record(**record) -> str | None:
    """One JSON line per restore in OPENCLAW_RESTORE_TIMING_LOG (default logs/restore_timing.jsonl)."""
    path = (os.environ.get("OPENCLAW_RESTORE_TIMING_LOG") or "").strip()
    if not path:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        path = str(LOGS_DIR / "restore_timing.jsonl")
    line = {"ts": datetime.now(timezone.utc).isoformat(), "event": "agent_restore", **record}
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return path
    except Exception:
        return None


def _repair_tools_profiles(tag: str) -> bool:
    """OpenClaw's CLI rejects the whole openclaw.json over one bad tools.profile: fix them first."""
    root = oc.load_root_config()
    if not root:
        return True
    fixed = oc.sanitize_root_tools_profiles(root)
    if fixed == 0:
        return True
    if not oc.save_root_config(root):
        logger.warning("[%s] could not save sanitized tools.profile", tag)
        return False
    logger.info("[%s] sanitized tools.profile on %s agent(s)", tag, fixed)
    return True


def _config_set(binary: str, path: str, value, errors: list | None = None, label: str = "") -> None:
    try:
        subprocess.run(
            [binary, "config", "set", path, json.dumps(value), "--json"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        if errors is not None:
            errors.append(f"{label} failed: {e}")


@asynccontextmanager
async def _preload_skills(_app):
    # 预热技能缓存（一个 6~13s 的 Node CLI）：读取方都能应对空缓存，放到线程里后台做，不挡端口。
    preload = asyncio.create_task(asyncio.to_thread(oc.preload_skills))  # held so it isn't GC'd
    yield
    preload.cancel()


def _not_a_workspace() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "Not an OpenClaw agent workspace"}, status_code=403)


def _outside_workspace() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "File is outside the workspace"}, status_code=400)


def create_openclaw_router(*, internal_token: str) -> APIRouter:
    def local_service(x_internal_token: str | None = Header(None)) -> None:
        if not internal_token or not secrets.compare_digest(x_internal_token or "", internal_token):
            raise HTTPException(status_code=401, detail="认证失败")

    router = APIRouter(lifespan=_preload_skills, dependencies=[Depends(local_service)])

    @router.get("/sessions/openclaw")
    async def list_agents(filter: str = Query("")):
        """列出 OpenClaw agents。"""
        config = oc.agents_config()
        if config is None:
            return {"agents": [], "available": False,
                    "message": "openclaw CLI not available or command failed"}

        defaults = config.get("defaults", {})
        agents = [oc.agent_detail(entry, defaults) for entry in config.get("list", [])]
        if filter:
            agents = [a for a in agents if filter.lower() in a.get("id", "").lower()]
        agents.sort(key=lambda a: (not a.get("is_default", False), a.get("id", "")))

        result = [{
            "name": a["id"],
            "model": a["model"].get("primary", ""),
            "workspace": a["workspace"],
            "is_default": a["is_default"],
            "tools": a["tools"],
            "skills": a["skills"],
            "skills_all": a["skills_all"],
        } for a in agents]

        raw_url = os.getenv("OPENCLAW_API_URL", "") or read_env_settings(
            str(ENV_FILE), ["OPENCLAW_API_URL"]).get("OPENCLAW_API_URL", "")
        return {
            "agents": result,
            "available": True,
            "openclaw_api_url": raw_url.replace("/v1/chat/completions", "").rstrip("/"),
        }

    @router.get("/sessions/openclaw/default-workspace")
    async def get_default_workspace():
        """返回默认 workspace 路径。"""
        if not oc.openclaw_bin():
            return _no_cli()
        default_ws = oc.default_workspace()
        if not default_ws:
            return {"ok": True, "parent_dir": "", "default_workspace": ""}
        return {"ok": True, "parent_dir": os.path.dirname(default_ws.rstrip("/")), "default_workspace": default_ws}

    @router.post("/sessions/openclaw/add")
    async def add_agent(req: Request):
        """创建新 OpenClaw agent。"""
        binary = oc.openclaw_bin()
        if not binary:
            return _no_cli()

        body = await req.json()
        name = (body.get("name") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "Agent name is required"}, status_code=400)
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            return JSONResponse(
                {"ok": False, "error": "Agent name can only contain letters, numbers, underscores and hyphens"},
                status_code=400,
            )
        if any(a.get("id") == name for a in (oc.agents_config() or {}).get("list", [])):
            return JSONResponse({"ok": False, "error": f"Agent '{name}' already exists"}, status_code=409)

        new_workspace = _new_workspace(name, (body.get("workspace") or "").strip())
        try:
            result = subprocess.run(
                [binary, "agents", "add", name, "--workspace", new_workspace, "--non-interactive"],
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
    async def list_workspace_files(workspace: str = Query(...)):
        """列出 workspace 中的文件：先核心文件（标注存在状态），再其余文件。"""
        ws_path = _agent_workspace(workspace)
        if ws_path is None:
            return _not_a_workspace()
        if not os.path.isdir(ws_path):
            return JSONResponse({"ok": False, "error": "Workspace not found"}, status_code=404)
        files = []
        for core_name in _CORE_FILES:
            core_path = os.path.join(ws_path, core_name)
            try:
                files.append({"name": core_name, "exists": True, "size": os.path.getsize(core_path)}
                             if os.path.isfile(core_path) else {"name": core_name, "exists": False, "size": 0})
            except Exception:
                files.append({"name": core_name, "exists": False, "size": 0})

        for item in sorted(os.listdir(ws_path)):
            if item in _CORE_FILES:
                continue
            item_path = os.path.join(ws_path, item)
            if os.path.isfile(item_path):
                try:
                    files.append({"name": item, "exists": True, "size": os.path.getsize(item_path)})
                except Exception:
                    pass
            elif os.path.isdir(item_path):
                files.append({"name": item + "/", "is_dir": True, "exists": True})
        return {"ok": True, "files": files}

    @router.get("/sessions/openclaw/workspace-file")
    async def read_workspace_file(workspace: str = Query(...), filename: str = Query(...)):
        """读取 workspace 中的文件内容。"""
        ws_path = _agent_workspace(workspace)
        if ws_path is None:
            return _not_a_workspace()
        file_path = _file_in(ws_path, filename)
        if file_path is None:
            return _outside_workspace()
        if not os.path.isfile(file_path):
            return JSONResponse({"ok": False, "error": "File not found"}, status_code=404)
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return {"ok": True, "filename": filename, "content": content}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @router.post("/sessions/openclaw/workspace-file")
    async def write_workspace_file(req: Request):
        """写入 workspace 中的文件内容。"""
        body = await req.json()
        workspace = (body.get("workspace") or "").strip()
        filename = (body.get("filename") or "").strip()
        content = body.get("content", "")
        if not workspace or not filename:
            return JSONResponse({"ok": False, "error": "workspace and filename are required"}, status_code=400)

        ws_path = _agent_workspace(workspace)
        if ws_path is None:
            return _not_a_workspace()
        file_path = _file_in(ws_path, filename)
        if file_path is None:
            return _outside_workspace()
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"ok": True, "message": f"File '{filename}' saved"}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @router.get("/sessions/openclaw/agent-detail")
    async def get_agent_detail(name: str = Query(...)):
        """返回单个 agent 的详细配置和可用技能。"""
        _, detail = _find_agent(oc.agents_config(), name)
        if detail is None:
            return JSONResponse({"ok": False, "error": f"Agent '{name}' not found"}, status_code=404)
        all_skills = _all_skills(detail.get("workspace") or oc.workspace_path())
        user_skills = [s for s in all_skills if s.get("source") != "bundled"]
        return {"ok": True, "agent": detail, "skills": all_skills, "user_skills": user_skills}

    @router.get("/sessions/openclaw/skills")
    async def list_skills(name: str = Query("", description="Agent name to filter effective skills")):
        """返回可用的 OpenClaw 技能列表；给了 agent 时只留它配置的技能。"""
        try:
            detail = _find_agent(oc.agents_config(), name)[1] if name else None
            skills = _all_skills((detail and detail.get("workspace")) or oc.workspace_path())
            if detail is not None and not detail["skills_all"]:
                allowed = set(detail["skills"])
                skills = [s for s in skills if s["name"] in allowed]
            return {"ok": True, "skills": skills}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @router.get("/sessions/openclaw/skills-info")
    async def list_skills_info():
        """返回缓存的完整技能信息。"""
        return {"ok": True, "skills": oc.skills_info}

    @router.get("/sessions/openclaw/tool-groups")
    async def list_tool_groups():
        """返回可用的工具组和配置文件（静态元数据）。groups 的值为工具名数组。"""
        return {
            "ok": True,
            "groups": {k: v["tools"] for k, v in _TOOL_GROUPS.items()},
            "profiles": dict(_TOOL_PROFILES),
        }

    @router.post("/sessions/openclaw/update-config")
    async def update_agent_config(req: Request):
        """更新 agent 的 skills/tools 配置。"""
        binary = oc.openclaw_bin()
        if not binary:
            return _no_cli()

        body = await req.json()
        agent_name = (body.get("agent_name") or "").strip()
        if not agent_name:
            return JSONResponse({"ok": False, "error": "agent_name is required"}, status_code=400)
        config = oc.agents_config()
        if config is None:
            return JSONResponse({"ok": False, "error": "Cannot read openclaw config"}, status_code=500)
        agent_idx = _find_agent(config, agent_name)[0]
        if agent_idx is None:
            return JSONResponse({"ok": False, "error": f"Agent '{agent_name}' not found"}, status_code=404)

        errors = []
        if "skills" in body:
            skills_val = body["skills"]
            try:
                if skills_val is None:
                    r = subprocess.run(
                        [binary, "config", "unset", f"agents.list[{agent_idx}].skills"],
                        capture_output=True, text=True, timeout=10,
                    )
                    if r.returncode != 0:
                        subprocess.run(
                            [binary, "config", "set", f"agents.list[{agent_idx}].skills", "--json", "null"],
                            capture_output=True, text=True, timeout=10,
                        )
                else:
                    subprocess.run(
                        [binary, "config", "set", f"agents.list[{agent_idx}].skills", json.dumps(skills_val)],
                        capture_output=True, text=True, timeout=10,
                    )
            except Exception as e:
                errors.append(f"skills: {e}")

        tools = body.get("tools")
        if isinstance(tools, dict):
            for key in ("profile", "alsoAllow", "deny"):
                if key in tools:
                    try:
                        subprocess.run(
                            [binary, "config", "set",
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
    async def list_channels():
        """返回所有频道及其账号。"""
        data = oc.channels()
        if data is None:
            return JSONResponse({"ok": False, "error": "Cannot read openclaw channels"}, status_code=500)

        channels = []
        for channel_name, accounts in data.get("chat", {}).items():
            if isinstance(accounts, str):
                accounts = [accounts]
            if isinstance(accounts, list):
                for acc in accounts:
                    channels.append({
                        "channel": channel_name, "account": acc,
                        "bind_key": f"{channel_name}:{acc}" if acc != "default" else channel_name,
                    })
        return {"ok": True, "channels": channels, "raw": data}

    @router.get("/sessions/openclaw/agent-bindings")
    async def get_agent_bindings(agent: str = Query(...)):
        """获取 agent 的频道绑定。"""
        binary = oc.openclaw_bin()
        if not binary:
            return _no_cli()

        def bind_key(ch, acc):
            return f"{ch}:{acc}" if acc != "default" else ch

        try:
            result = subprocess.run(
                [binary, "agents", "list", "--bindings", "--json"],
                capture_output=True, text=True, timeout=45,
            )
            data = oc.parse_first_json(result.stdout) if result.returncode == 0 else None
            if data is not None:
                agents_list = data if isinstance(data, list) else data.get("agents", data.get("list", []))
                for a in agents_list:
                    if a.get("id", a.get("name", "")) != agent:
                        continue
                    bindings = a.get("bindings", a.get("channels", []))
                    if isinstance(bindings, list):
                        return {"ok": True, "bindings": bindings}
                    if isinstance(bindings, dict):
                        flat = []
                        for ch, accs in bindings.items():
                            flat.extend(bind_key(ch, acc) for acc in (accs if isinstance(accs, list) else [accs]))
                        return {"ok": True, "bindings": flat}
                    detail_bindings = a.get("bindingDetails", a.get("routes", []))
                    if isinstance(detail_bindings, list):
                        flat = []
                        for item in detail_bindings:
                            if not isinstance(item, str):
                                continue
                            if " accountId=" in item:
                                flat.append(bind_key(*item.split(" accountId=", 1)))
                            elif " " in item:
                                flat.append(bind_key(*item.split(" ", 1)))
                            elif item:
                                flat.append(item)
                        return {"ok": True, "bindings": flat}
        except Exception as e:
            logger.warning("openclaw agent bindings parse error: %s", e)
        return {"ok": True, "bindings": []}

    @router.post("/sessions/openclaw/agent-bind")
    async def agent_bind(req: Request):
        """绑定或解绑频道到 agent。"""
        binary = oc.openclaw_bin()
        if not binary:
            return _no_cli()

        body = await req.json()
        agent_name = (body.get("agent") or "").strip()
        channel = (body.get("channel") or "").strip()
        if not agent_name or not channel:
            return JSONResponse({"ok": False, "error": "agent and channel are required"}, status_code=400)

        cmd_action = "bind" if (body.get("action") or "bind").strip() == "bind" else "unbind"
        try:
            result = subprocess.run(
                [binary, "agents", cmd_action, "--agent", agent_name, "--bind", channel],
                capture_output=True, text=True, timeout=45,
            )
            if result.returncode != 0:
                err = result.stderr.strip() or result.stdout.strip()
                return JSONResponse({"ok": False, "error": err[:500]}, status_code=500)
            return {"ok": True, "message": f"Agent '{agent_name}' {cmd_action} '{channel}' success"}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    # ------------------------------------------------------------------
    # Snapshot / restore
    # ------------------------------------------------------------------

    @router.get("/sessions/openclaw/agent-snapshot")
    async def export_agent_snapshot(name: str = Query(...)):
        """导出 agent 完整快照（配置 + workspace 文件）。"""
        _, detail = _find_agent(oc.agents_config(), name)
        if not detail:
            return JSONResponse({"ok": False, "error": f"Agent '{name}' not found"}, status_code=404)

        workspace_files = {}
        ws = detail.get("workspace", "")
        ws_path = os.path.expanduser(ws) if ws else ""
        if ws_path and os.path.isdir(ws_path):
            for fname in _CORE_FILES:
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
                "skills": detail.get("skills", []),
                "skills_all": detail.get("skills_all", True),
                "tools": detail.get("tools", {}),
                "model": detail.get("model", {}),
            },
            "workspace_files": workspace_files,
        }

    @router.post("/sessions/openclaw/agent-restore")
    async def restore_agent_snapshot(req: Request):
        """从快照恢复 agent（创建 + 配置 + 写入 workspace 文件）。"""
        binary = oc.openclaw_bin()
        if not binary:
            return _no_cli()

        body = await req.json()
        agent_name = (body.get("agent_name") or "").strip()
        if not agent_name:
            return JSONResponse({"ok": False, "error": "agent_name is required"}, status_code=400)
        display_name = (body.get("display_name") or "").strip() or agent_name
        snapshot_config = body.get("config", {})
        snapshot_files = body.get("workspace_files", {})
        custom_ws = (body.get("workspace") or "").strip()

        errors = []
        _repair_tools_profiles("openclaw-restore")

        timing_ms: dict[str, float] = {}
        t_wall = t_start = time.perf_counter()

        def _seg(label: str) -> None:
            nonlocal t_wall
            t1 = time.perf_counter()
            timing_ms[label] = round((t1 - t_wall) * 1000, 2)
            t_wall = t1

        # Step 1: 检查 agent 是否存在，不存在则创建
        existing = oc.agents_config() or {}
        defaults = existing.get("defaults", {})
        found = next((a for a in existing.get("list", []) if a.get("id", "") == agent_name), None)
        _seg("step1_list_agents_fetch_config")
        workspace = ""
        if found is None:
            new_workspace = _new_workspace(agent_name, custom_ws)
            try:
                result = subprocess.run(
                    [binary, "agents", "add", agent_name, "--workspace", new_workspace, "--non-interactive"],
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
            workspace = oc.agent_detail(found, defaults)["workspace"]
            _seg("step1b_existing_workspace_lookup")

        # Step 2: 更新 skills/tools（优先直接读写 openclaw.json，避免多次 config get/set CLI）
        if snapshot_config:
            root_cfg = oc.load_root_config()
            use_file = root_cfg is not None and isinstance(root_cfg.get("agents"), dict)
            config = root_cfg["agents"] if use_file else oc.agents_config()
            _seg("step2a_fetch_config")

            agent_list = config.setdefault("list", []) if config else []
            agent_idx = next((i for i, a in enumerate(agent_list)
                              if a.get("id") == agent_name or a.get("name") == agent_name), None)
            skills_val = snapshot_config.get("skills")
            skills_all = snapshot_config.get("skills_all", False)
            tools_cfg = snapshot_config.get("tools", {})

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
                if skills_all:
                    entry.pop("skills", None)
                elif skills_val is not None:
                    entry["skills"] = skills_val
                _seg("step2c_config_set_skills")

                if tools_cfg:
                    entry["tools"] = oc.sanitize_tools(tools_cfg)
                _seg("step2d_config_set_tools")

                if not oc.save_root_config(root_cfg):
                    errors.append("Save openclaw.json failed (skills/tools not persisted)")
            else:
                if agent_idx is None:
                    agent_idx = len(agent_list)
                    init_entry = {"id": agent_name, "name": display_name}
                    if workspace:
                        init_entry["workspace"] = workspace
                    _config_set(binary, f"agents.list[{agent_idx}]", init_entry, errors, "Create config entry")
                _seg("step2b_config_set_list_entry")

                if skills_all:
                    try:
                        subprocess.run(
                            [binary, "config", "set", f"agents.list[{agent_idx}].skills", "--delete", "--json"],
                            capture_output=True, text=True, timeout=10,
                        )
                    except Exception:
                        pass
                elif skills_val is not None:
                    _config_set(binary, f"agents.list[{agent_idx}].skills", skills_val, errors, "Set skills")
                _seg("step2c_config_set_skills")

                if tools_cfg:
                    _config_set(binary, f"agents.list[{agent_idx}].tools", oc.sanitize_tools(tools_cfg), errors, "Set tools")
                _seg("step2d_config_set_tools")
                if display_name != agent_name:
                    _config_set(binary, f"agents.list[{agent_idx}].name", display_name)
        else:
            _seg("step2_skip_no_snapshot_config")

        # Step 3: 写入 workspace 文件
        n_ws_files = 0
        if workspace and snapshot_files:
            ws_path = os.path.realpath(os.path.expanduser(workspace))
            for fname, content in snapshot_files.items():
                fpath = _file_in(ws_path, fname)
                if fpath is None:
                    errors.append(f"Write {fname} failed: outside the workspace")
                    continue
                try:
                    os.makedirs(os.path.dirname(fpath), exist_ok=True)
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(content)
                    n_ws_files += 1
                except Exception as e:
                    errors.append(f"Write {fname} failed: {e}")
            _seg("step3_write_workspace_files")
        else:
            _seg("step3_skip_no_files_or_workspace")

        timing_ms["total_ms"] = round((time.perf_counter() - t_start) * 1000, 2)
        ok_restore = not errors
        logger.info("[openclaw-restore] agent=%s ok=%s total_ms=%.1f timing_ms=%s errors=%s",
                    agent_name, ok_restore, timing_ms["total_ms"], timing_ms, errors)
        timing_path = _append_restore_record(
            agent_name=agent_name,
            ok=ok_restore,
            restore_timing_ms=timing_ms,
            restore_workspace_files_written=n_ws_files,
            errors=errors,
        )

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

    @router.delete("/sessions/openclaw/remove")
    async def remove_agent(name: str = Query(...)):
        """删除 OpenClaw agent。"""
        binary = oc.openclaw_bin()
        if not binary:
            return _no_cli()
        agent_name = (name or "").strip()
        if not agent_name:
            return JSONResponse({"ok": False, "error": "Agent name is required"}, status_code=400)
        if agent_name.lower() == "main":
            return JSONResponse({"ok": False, "error": "The main agent cannot be deleted"}, status_code=400)

        try:
            if not _repair_tools_profiles("openclaw-remove"):
                return JSONResponse(
                    {"ok": False, "error": "Failed to repair openclaw.json (tools.profile); cannot run agents delete"},
                    status_code=500,
                )
            result = subprocess.run(
                [binary, "agents", "delete", agent_name, "--force", "--json"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                err = result.stderr.strip() or result.stdout.strip()
                return JSONResponse({"ok": False, "error": err[:500]}, status_code=500)
            return {"ok": True, "message": f"Agent '{agent_name}' removed"}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    return router
