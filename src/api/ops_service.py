import asyncio
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict
from typing import Any, Callable

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from integrations.acpx_adapter import AcpxError, acpx_options_from_agent, get_acpx_adapter
from integrations.acpx_cli_tools import acpx_agent_tags_with_legacy
from integrations.agent_session import inspect_http_agent_session
from utils.auth_utils import extract_user_password_session, is_internal_bearer, parse_bearer_parts
from api.update_manager import current_update_snapshot, start_update_process
from api.group_repository import (
    delete_http_agent_sessions_by_global_name,
    delete_http_agent_session_by_key,
    get_group,
    get_group_member_by_global_id,
    list_http_agent_sessions,
)
from utils.checkpoint_repository import delete_thread_records, list_thread_ids_by_prefix
from api.group_service import _load_public_external_agents, build_external_agents_map_for_owner
from services.llm_factory import get_provider_audio_defaults, infer_provider
from utils.logging_utils import get_logger
from utils.runtime_paths import USER_FILES_DIR, WORKSPACE_DIR
from api.ops_models import ACPControlRequest, ACPStatusRequest, AgentControlRequest, CancelRequest, LoginRequest, TTSRequest, UpdateCheckRequest, UpdateStartRequest, UpdateStatusRequest

logger = get_logger("ops_service")

# Project root for team-scoped paths
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

# Known ACP-compatible tool binaries (must match CLI status platforms list)
_ACP_KNOWN_TOOLS: frozenset = frozenset({
    "openclaw", "codex", "claude", "gemini", "aider",
    "claude-code", "gemini-cli",
})

# Session suffix rule aligned with group_service (model `agent:…:suffix` → suffix; else default)
_DEFAULT_ACP_SESSION_SUFFIX = "clawcrosschat"
_AGENT_MODEL_RE = re.compile(r"^agent:[^:]+(?::(.+))?$")
_ACPX_AGENT_TAGS: frozenset[str] = acpx_agent_tags_with_legacy()


def _canonical_external_platform(platform: str) -> str:
    pl = (platform or "").strip().lower()
    if pl in ("claude-code", "claudecode"):
        return "claude"
    if pl in ("gemini-cli", "geminicli"):
        return "gemini"
    return pl


def _resolve_external_session_suffix(model: str) -> str:
    m = _AGENT_MODEL_RE.match((model or "").strip())
    if m and m.group(1):
        return m.group(1)
    return _DEFAULT_ACP_SESSION_SUFFIX


def _load_team_external_agents(user_id: str, team: str) -> list[dict]:
    """Load external agents from team's external_agents.json."""
    team = (team or "").strip()
    if not user_id or not team:
        return []
    path = os.path.join(str(USER_FILES_DIR), user_id, "teams", team, "external_agents.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        result = []
        for agent in data:
            if not isinstance(agent, dict) or "name" not in agent:
                continue
            ext_config = agent.get("config") or agent.get("meta") or {}
            if not isinstance(ext_config, dict):
                ext_config = {}
            result.append({
                "user_id": "ext",
                "session_id": agent.get("name", ""),
                "member_type": "ext",
                "name": agent.get("name", ""),
                "tag": agent.get("tag", ""),
                "platform": _canonical_external_platform(str(agent.get("platform", "") or "")),
                "global_name": agent.get("global_name", ""),
                "api_url": ext_config.get("api_url", ""),
                "api_key": ext_config.get("api_key", ""),
                "model": ext_config.get("model", ""),
                "meta": ext_config,
            })
        return result
    except Exception:
        return []


def _find_external_agent(agents: list[dict], agent_key: str) -> dict | None:
    """Match by global_name first (stable id), then by short name (may collide)."""
    agent_key = (agent_key or "").strip()
    if not agent_key:
        return None
    for agent in agents:
        if (agent.get("global_name") or "").strip() == agent_key:
            return agent
    for agent in agents:
        if (agent.get("name") or "").strip() == agent_key:
            return agent
    return None


def _find_external_agent_across_teams(user_id: str, agent_key: str) -> dict | None:
    """Search all team folders when the primary team hint misses the agent."""
    if not user_id or not agent_key:
        return None
    team_base = os.path.join(str(USER_FILES_DIR), user_id, "teams")
    if not os.path.isdir(team_base):
        return None
    for entry in sorted(os.listdir(team_base)):
        path = os.path.join(team_base, entry)
        if not os.path.isdir(path):
            continue
        found = _find_external_agent(_load_team_external_agents(user_id, entry), agent_key)
        if found:
            return found
    return None


def _resolve_external_agent_record(user_id: str, team_hint: str, agent_key: str) -> dict | None:
    """Team external_agents.json first (if team given), then user-level external_agents.json, then any team."""
    agent_key = (agent_key or "").strip()
    if not user_id or not agent_key:
        return None
    th = (team_hint or "").strip()
    if th:
        found = _find_external_agent(_load_team_external_agents(user_id, th), agent_key)
        if found:
            return found
    found = _find_external_agent(_load_public_external_agents(user_id), agent_key)
    if found:
        return found
    return _find_external_agent_across_teams(user_id, agent_key)


class OpsService:
    """操作服务类，提供工具列表、登录、TTS、ACP 外部 agent 控制等功能。"""

    def __init__(
        self,
        *,
        internal_token: str,
        agent: Any,
        verify_password: Callable[[str, str], bool],
        verify_auth_or_token: Callable[[str, str, str | None], None],
        group_db_path: str | None = None,
    ):
        self.internal_token = internal_token
        self.agent = agent
        self.verify_password = verify_password
        self.verify_auth_or_token = verify_auth_or_token
        self.group_db_path = group_db_path

    async def get_tools_list(self, x_internal_token: str | None, authorization: str | None):
        """获取可用工具列表。

        :param x_internal_token: 内部令牌（可选）
        :param authorization: Bearer 授权头（可选）
        :return: 包含工具列表的字典
        :raises HTTPException: 认证失败时抛出 403 异常
        """
        if x_internal_token and x_internal_token == self.internal_token:
            return {"status": "success", "tools": self.agent.get_tools_info()}
        parts = parse_bearer_parts(authorization)
        if parts:
            if is_internal_bearer(parts, self.internal_token):
                return {"status": "success", "tools": self.agent.get_tools_info()}
            parsed = extract_user_password_session(parts, default_session="default")
            if parsed and self.verify_password(parsed[0], parsed[1]):
                return {"status": "success", "tools": self.agent.get_tools_info()}
        raise HTTPException(status_code=403, detail="认证失败")

    async def login(self, req: LoginRequest):
        """用户登录验证。

        :param req: 登录请求，包含 user_id 和 password
        :return: 登录成功状态
        :raises HTTPException: 密码错误时抛出 401 异常
        """
        if self.verify_password(req.user_id, req.password):
            logger.info("login success user=%s", req.user_id)
            return {"status": "success", "message": "登录成功"}
        logger.warning("login failed user=%s", req.user_id)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    async def cancel_agent(self, req: CancelRequest, x_internal_token: str | None):
        """取消指定用户的运行中任务。

        :param req: 取消请求，包含 user_id、session_id、password
        :param x_internal_token: 内部令牌（可选）
        :return: 取消操作结果
        """
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        task_key = f"{req.user_id}#{req.session_id}"
        logger.info("cancel user=%s session=%s", req.user_id, req.session_id)
        actually_cancelled = await self.agent.cancel_task(task_key)
        if actually_cancelled:
            return {"status": "success", "message": "已终止", "cancelled": True}
        return {"status": "success", "message": "当前没有运行中的任务", "cancelled": False}

    # ------------------------------------------------------------------
    # Unified agent catalog and control plane
    # ------------------------------------------------------------------

    @staticmethod
    def _read_agent_file(path: str) -> list[dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                value = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    @staticmethod
    def _agent_files(user_id: str, filename: str) -> list[tuple[str, str]]:
        """Return ``(path, team)`` pairs without creating a second registry."""
        user_root = os.path.join(str(USER_FILES_DIR), user_id)
        result: list[tuple[str, str]] = []
        public_path = os.path.join(user_root, filename)
        if os.path.isfile(public_path):
            result.append((public_path, ""))
        teams_root = os.path.join(user_root, "teams")
        if os.path.isdir(teams_root):
            for team in sorted(os.listdir(teams_root)):
                path = os.path.join(teams_root, team, filename)
                if os.path.isfile(path):
                    result.append((path, team))
        return result

    def _internal_agent_catalog(
        self,
        user_id: str,
        subagent_sessions: set[str],
        persisted_sessions: set[str],
    ) -> list[dict[str, Any]]:
        prefix = f"{user_id}#"
        thread_status = self.agent.get_all_thread_status(prefix)
        active_keys = set(self.agent.list_active_task_keys(prefix))
        by_identity: dict[str, dict[str, Any]] = {}

        for path, team in self._agent_files(user_id, "internal_agents.json"):
            for config in self._read_agent_file(path):
                identity = str(config.get("session") or config.get("session_id") or "").strip()
                if not identity:
                    continue
                row = by_identity.setdefault(identity, {
                    "identity": identity,
                    "kind": "internal",
                    "transport": "internal",
                    "platform": "internal",
                    "name": str(config.get("name") or identity),
                    "tag": str(config.get("tag") or ""),
                    "teams": [],
                    "status": "idle",
                    "status_source": "runtime",
                    "connection_status": "local",
                    "session_initialized": None,
                    "identity_injection_policy": "stable_system_prompt",
                    "running_known": True,
                    "can_cancel": False,
                    "supported_actions": ["status", "cancel", "stop", "reset", "delete"],
                })
                if team and team not in row["teams"]:
                    row["teams"].append(team)

        # Every persisted Internal session belongs in the flat Grid, even when it
        # is idle and has no named entry in internal_agents.json. Runtime-only
        # sessions remain visible too; WeBot subagents get their own rows.
        active_runtime_ids = {
            key[len(prefix):]
            for key in active_keys
            if key.startswith(prefix)
        }
        for identity in (active_runtime_ids | persisted_sessions) - subagent_sessions:
            by_identity.setdefault(identity, {
                "identity": identity,
                "kind": "internal",
                "transport": "internal",
                "platform": "internal",
                "name": identity,
                "tag": "",
                "teams": [],
                "status": "idle",
                "status_source": "runtime",
                "connection_status": "local",
                "session_initialized": None,
                "identity_injection_policy": "stable_system_prompt",
                "running_known": True,
                "can_cancel": False,
                "supported_actions": ["status", "cancel", "stop", "reset", "delete"],
            })

        for identity, row in by_identity.items():
            task_key = f"{prefix}{identity}"
            state = thread_status.get(task_key, {})
            busy = bool(state.get("busy")) or task_key in active_keys
            row["status"] = "running" if busy else "idle"
            row["can_cancel"] = busy
            row["runtime"] = {
                "busy": busy,
                "source": state.get("source", ""),
                "pending_system": state.get("pending_system", 0),
            }
        return list(by_identity.values())

    def _external_agent_catalog(self, user_id: str) -> list[dict[str, Any]]:
        by_identity: dict[str, dict[str, Any]] = {}
        for path, team in self._agent_files(user_id, "external_agents.json"):
            for config in self._read_agent_file(path):
                identity = str(config.get("global_name") or "").strip()
                if not identity:
                    continue
                meta = config.get("config") or config.get("meta") or {}
                if not isinstance(meta, dict):
                    meta = {}
                platform = _canonical_external_platform(
                    str(config.get("platform") or config.get("tag") or "")
                )
                controllable = platform == "openclaw" or platform in _ACP_KNOWN_TOOLS or platform in _ACPX_AGENT_TAGS
                transport = "http" if platform == "openclaw" or not controllable else "acp"
                session_key = f"agent:{identity}:{_resolve_external_session_suffix(str(meta.get('model') or ''))}"
                row = by_identity.setdefault(identity, {
                    "identity": identity,
                    "kind": "external",
                    "transport": transport,
                    "platform": platform,
                    "name": str(config.get("name") or identity),
                    "tag": str(config.get("tag") or ""),
                    "teams": [],
                    "status": "unknown",
                    "status_source": "configuration",
                    "connection_status": "unknown",
                    "session_initialized": None,
                    "identity_injection_policy": "first_session_or_prompt_change" if transport == "http" else "first_acpx_session",
                    "running_known": False,
                    "can_cancel": controllable,
                    "supported_actions": ["status", "reset", "delete"] + (["cancel", "stop", "new"] if controllable else []),
                    "session_key": session_key,
                    "session_count": 0,
                })
                if team and team not in row["teams"]:
                    row["teams"].append(team)
        return list(by_identity.values())

    async def list_agents(
        self,
        user_id: str,
        *,
        team: str = "",
        refresh_external: bool = True,
    ) -> list[dict[str, Any]]:
        """Build a flat runtime view from Team files and live registries."""
        from webot.subagents import list_subagents_for_user

        subagent_records = list_subagents_for_user(user_id)
        subagent_sessions = {record.session_id for record in subagent_records}
        persisted_sessions: set[str] = set()
        checkpoint_db_path = str(getattr(self.agent, "_db_path", "") or "")
        if checkpoint_db_path:
            prefix = f"{user_id}#"
            try:
                persisted_sessions = {
                    thread_id[len(prefix):]
                    for thread_id in await list_thread_ids_by_prefix(checkpoint_db_path, prefix)
                    if thread_id.startswith(prefix) and len(thread_id) > len(prefix)
                }
            except Exception as exc:
                logger.warning("failed to list persisted Internal sessions: %s", exc)
        rows = self._internal_agent_catalog(
            user_id,
            subagent_sessions,
            persisted_sessions,
        )
        external_rows = self._external_agent_catalog(user_id)

        if refresh_external and external_rows:
            # HTTP's registry only proves that a conversation was initialized.
            # It has no portable remote-running or cancellation protocol.
            if self.group_db_path:
                async def refresh_http(row: dict[str, Any]) -> None:
                    if row["transport"] != "http":
                        return
                    try:
                        state = await inspect_http_agent_session(
                            group_db_path=self.group_db_path or "",
                            session_key=row["session_key"],
                        )
                    except Exception as exc:
                        row["status_detail"] = str(exc)
                        return
                    row["session_initialized"] = state.initialized
                    row["connection_status"] = "online" if state.initialized else "idle"
                    row["status"] = "idle"
                    row["status_source"] = state.source
                    row["session_count"] = 1 if state.initialized else 0

                await asyncio.gather(*(refresh_http(row) for row in external_rows))

            acp_rows: dict[str, list[dict[str, Any]]] = {}
            for row in external_rows:
                if row["transport"] == "acp":
                    acp_rows.setdefault(row["platform"], []).append(row)

            if acp_rows and shutil.which("acpx"):
                try:
                    adapter = get_acpx_adapter(cwd=str(WORKSPACE_DIR / "acpx"))
                except AcpxError:
                    adapter = None
                if adapter is not None:
                    async def refresh_platform(platform: str, platform_rows: list[dict[str, Any]]) -> None:
                        try:
                            sessions = await adapter.list_sessions(tool=platform)
                        except AcpxError as exc:
                            for row in platform_rows:
                                row["status_detail"] = str(exc)
                            return
                        for row in platform_rows:
                            matched = [
                                session for session in sessions
                                if session.get("name") == row["session_key"] and not session.get("closed")
                            ]
                            row["connection_status"] = "online" if matched else "idle"
                            row["status"] = "idle"
                            row["status_source"] = "acpx_session_registry"
                            row["session_initialized"] = bool(matched)
                            row["session_count"] = len(matched)
                            row["sessions"] = matched

                    await asyncio.gather(*(
                        refresh_platform(platform, platform_rows)
                        for platform, platform_rows in acp_rows.items()
                    ))

            # OpenClaw transport is HTTP, while its lifecycle control is exposed
            # by the OpenClaw CLI/ACP bridge. Preserve the existing status probe.
            async def refresh_openclaw(row: dict[str, Any]) -> None:
                if row["platform"] != "openclaw" or row.get("session_initialized") is True:
                    return
                config = _resolve_external_agent_record(
                    user_id,
                    (row.get("teams") or [""])[0],
                    row["identity"],
                )
                if not config:
                    return
                state = await self._acp_status_single(config)
                raw_status = state.get("status", "unknown")
                row["connection_status"] = raw_status
                row["session_initialized"] = raw_status == "online"
                row["session_count"] = int(state.get("session_count") or 0)
                row["sessions"] = state.get("sessions", [])
                row["status"] = "idle" if raw_status in {"online", "idle"} else "unknown"
                row["status_source"] = "openclaw_session_presence"
                if state.get("reason"):
                    row["status_detail"] = state["reason"]

            await asyncio.gather(*(refresh_openclaw(row) for row in external_rows))
        rows.extend(external_rows)

        internal_teams = {
            row["identity"]: row["teams"]
            for row in rows
            if row["kind"] == "internal"
        }
        active_keys = set(self.agent.list_active_task_keys(f"{user_id}#"))
        for record in subagent_records:
            stored = asdict(record)
            runtime_key = f"{user_id}#{record.session_id}"
            active = runtime_key in active_keys
            stored_status = str(record.status or "idle")
            status = "running" if active or stored_status in {"queued", "running", "cancelling"} else stored_status
            rows.append({
                "identity": record.agent_id,
                "kind": "subagent",
                "transport": "internal",
                "platform": record.agent_type,
                "name": record.name,
                "tag": record.agent_type,
                "teams": list(internal_teams.get(record.parent_session, [])),
                "status": status,
                "status_source": "webot_registry",
                "connection_status": "local",
                "session_initialized": True,
                "identity_injection_policy": "internal_session",
                "running_known": active or stored_status not in {"queued", "running", "cancelling"},
                "can_cancel": status in {"queued", "running", "cancelling"},
                "supported_actions": ["status", "cancel", "stop", "reset", "delete"],
                "session_id": record.session_id,
                "parent_session": record.parent_session,
                "updated_at": record.updated_at,
                "runtime": stored,
            })

        team = team.strip()
        if team:
            rows = [row for row in rows if team in row.get("teams", [])]
        rows.sort(key=lambda row: (row["kind"], str(row["name"]).casefold(), row["identity"]))
        return rows

    async def agent_control(self, req: AgentControlRequest, x_internal_token: str | None):
        """One compatible entry point for catalog, status, and lifecycle control."""
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        rows = await self.list_agents(
            req.user_id,
            team=req.team,
            refresh_external=req.refresh_external,
        )
        if req.action == "list":
            if req.kind:
                rows = [row for row in rows if row["kind"] == req.kind]
            return {"status": "success", "count": len(rows), "agents": rows}

        identity = req.identity.strip()
        if not identity:
            raise HTTPException(status_code=400, detail="identity 不能为空")
        matches = [
            row for row in rows
            if row["identity"] == identity and (not req.kind or row["kind"] == req.kind)
        ]
        if not matches:
            raise HTTPException(status_code=404, detail=f"未找到 Agent: {identity}")
        if len(matches) > 1:
            raise HTTPException(status_code=409, detail="identity 有歧义，请同时指定 kind")
        target = matches[0]
        if req.action == "status":
            return {"status": "success", "agent": target}

        action = "cancel" if req.action == "stop" else req.action
        if target["kind"] == "internal":
            if action == "reset":
                await self._delete_internal_agent_runtime(req.user_id, identity)
                return {
                    "status": "success",
                    "supported": True,
                    "action": req.action,
                    "message": f"Agent {identity} 的会话已重置",
                    "agent": target,
                }
            if action == "delete":
                await self._delete_internal_agent_runtime(req.user_id, identity)
                deleted_sources = self._delete_agent_configs(req.user_id, "internal", identity)
                return {
                    "status": "success",
                    "supported": True,
                    "action": req.action,
                    "deleted_sources": deleted_sources,
                    "message": f"Agent {identity} 已删除",
                    "agent": target,
                }
            if action != "cancel":
                return {"status": "unsupported", "supported": False, "action": req.action, "agent": target}
            result = await self.agent.cancel_task(f"{req.user_id}#{identity}")
            return {"status": "success", "supported": True, "action": req.action, "cancelled": result, "agent": target}

        if target["kind"] == "subagent":
            if action == "reset":
                session_id = str(target.get("session_id") or "").strip()
                if not session_id:
                    raise HTTPException(status_code=500, detail="Subagent 缺少 session_id")
                await self._delete_internal_agent_runtime(req.user_id, session_id)
                return {
                    "status": "success",
                    "supported": True,
                    "action": req.action,
                    "message": f"Subagent {identity} 的会话已重置",
                    "agent": target,
                }
            if action == "delete":
                session_id = str(target.get("session_id") or "").strip()
                if not session_id:
                    raise HTTPException(status_code=500, detail="Subagent 缺少 session_id")
                await self._delete_internal_agent_runtime(req.user_id, session_id, delete_subagent=True)
                return {
                    "status": "success",
                    "supported": True,
                    "action": req.action,
                    "message": f"Subagent {identity} 的运行会话和追踪记录已删除",
                    "agent": target,
                }
            if action != "cancel":
                return {"status": "unsupported", "supported": False, "action": req.action, "agent": target}
            from webot.models import WeBotSubagentRefRequest
            from webot.service import WeBotService
            service = WeBotService(
                agent=self.agent,
                verify_auth_or_token=self.verify_auth_or_token,
                extract_text=lambda value: str(value or ""),
            )
            result = await service.cancel_subagent(
                WeBotSubagentRefRequest(user_id=req.user_id, password=req.password, agent_ref=identity),
                x_internal_token,
            )
            return {
                **result,
                "supported": True,
                "action": req.action,
                "agent": target,
            }

        if req.action == "reset" and target.get("transport") == "http" and target.get("platform") != "openclaw":
            deleted = 0
            if self.group_db_path:
                deleted = await delete_http_agent_sessions_by_global_name(
                    self.group_db_path,
                    identity,
                )
            return {
                "status": "success",
                "supported": True,
                "action": req.action,
                "deleted_sessions": deleted,
                "message": f"Agent {identity} 的本地会话已重置",
                "agent": target,
            }

        if req.action == "delete" and target.get("transport") == "http":
            if target.get("platform") == "openclaw":
                await self._delete_openclaw_agent(identity)
            elif self.group_db_path:
                await delete_http_agent_sessions_by_global_name(self.group_db_path, identity)
            deleted_sources = self._delete_agent_configs(req.user_id, "external", identity)
            return {
                "status": "success",
                "supported": True,
                "action": req.action,
                "deleted_sources": deleted_sources,
                "message": f"Agent {identity} 已删除",
                "agent": target,
            }

        if req.action not in target["supported_actions"]:
            return {
                "status": "unsupported",
                "supported": False,
                "action": req.action,
                "reason": f"{target['transport']} transport does not expose this control",
                "agent": target,
            }
        acp_action = "stop" if action == "cancel" else "new" if action == "reset" else action
        result = await self.acp_control(
            ACPControlRequest(
                user_id=req.user_id,
                password=req.password,
                team=req.team or (target.get("teams") or [""])[0],
                agent_name=identity,
                action=acp_action,
            ),
            x_internal_token,
        )
        if req.action == "delete" and result.get("status") == "success":
            result = {
                **result,
                "deleted_sources": self._delete_agent_configs(req.user_id, "external", identity),
                "message": f"Agent {identity} 已删除",
            }
        return {**result, "supported": True, "agent": target}

    @classmethod
    def _delete_agent_configs(cls, user_id: str, kind: str, identity: str) -> list[str]:
        """Remove one Agent definition from every backing config file."""
        filename = "internal_agents.json" if kind == "internal" else "external_agents.json"
        identity_key = "session" if kind == "internal" else "global_name"
        deleted_sources: list[str] = []
        for path, team in cls._agent_files(user_id, filename):
            rows = cls._read_agent_file(path)
            remaining = [
                row for row in rows
                if str(row.get(identity_key) or "").strip() != identity
            ]
            if len(remaining) == len(rows):
                continue
            directory = os.path.dirname(path)
            fd, tmp_path = tempfile.mkstemp(prefix=".agent-delete-", suffix=".json", dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(remaining, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_path, path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            deleted_sources.append(team or "public")
        return deleted_sources

    @staticmethod
    async def _delete_openclaw_agent(identity: str) -> None:
        if identity.strip().lower() == "main":
            raise HTTPException(status_code=400, detail="不能删除 OpenClaw main Agent")
        oasis_port = int(os.getenv("PORT_OASIS", "51202"))
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"http://127.0.0.1:{oasis_port}/sessions/openclaw/remove",
                    params={"name": identity},
                )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"OpenClaw 删除失败: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("error") or response.json().get("detail")
            except Exception:
                detail = response.text
            raise HTTPException(status_code=502, detail=f"OpenClaw 删除失败: {detail or response.status_code}")

    async def _delete_internal_agent_runtime(
        self,
        user_id: str,
        session_id: str,
        *,
        delete_subagent: bool = False,
    ) -> None:
        """Delete runtime state only; Agent/Team configuration is intentionally untouched."""
        from webot.subagents import delete_subagent_by_session

        thread_id = f"{user_id}#{session_id}"
        await self.agent.cancel_task(thread_id)
        close_checkpoint = getattr(self.agent, "close_thread_checkpoint", None)
        if callable(close_checkpoint):
            await close_checkpoint(thread_id)
        checkpoint_db_path = str(getattr(self.agent, "_db_path", "") or "")
        if checkpoint_db_path:
            await delete_thread_records(checkpoint_db_path, thread_id)
        if delete_subagent:
            delete_subagent_by_session(user_id, session_id)

    async def text_to_speech(self, req: TTSRequest, x_internal_token: str | None):
        """文本转语音（TTS）服务。

        :param req: TTS 请求，包含 text、voice 等
        :param x_internal_token: 内部令牌（可选）
        :return: 音频流响应
        :raises HTTPException: 未配置 API 或文本为空时抛出异常
        """
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)

        tts_text = req.text.strip()
        if not tts_text:
            raise HTTPException(status_code=400, detail="文本不能为空")
        if len(tts_text) > 4000:
            tts_text = tts_text[:4000]

        api_key = os.getenv("LLM_API_KEY", "")
        base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
        provider = infer_provider(
            model=os.getenv("LLM_MODEL", ""),
            base_url=base_url,
            provider=os.getenv("LLM_PROVIDER", ""),
            api_key=api_key,
        )
        audio_defaults = get_provider_audio_defaults(provider)
        tts_model = os.getenv("TTS_MODEL", "").strip() or audio_defaults["tts_model"]
        tts_voice = req.voice or os.getenv("TTS_VOICE", "").strip() or audio_defaults["tts_voice"]

        if not api_key or not base_url:
            raise HTTPException(status_code=500, detail="TTS API 未配置")
        if not tts_model:
            raise HTTPException(
                status_code=500,
                detail="TTS_MODEL 未配置，且当前 LLM provider 没有可自动推断的音频默认值",
            )

        tts_url = f"{base_url}/audio/speech"

        async def audio_stream():
            payload = {
                "model": tts_model,
                "input": tts_text,
                "response_format": "mp3",
            }
            if tts_voice:
                payload["voice"] = tts_voice

            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST",
                    tts_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                ) as resp:
                    if resp.status_code != 200:
                        error_body = await resp.aread()
                        raise HTTPException(
                            status_code=resp.status_code,
                            detail=f"TTS API 错误: {error_body.decode('utf-8', errors='replace')[:200]}",
                        )
                    async for chunk in resp.aiter_bytes(chunk_size=4096):
                        yield chunk

        return StreamingResponse(
            audio_stream(),
            media_type="audio/mpeg",
            headers={"Content-Disposition": "inline; filename=tts_output.mp3"},
        )

    # ------------------------------------------------------------------
    # ACP external agent management
    # ------------------------------------------------------------------

    async def acp_control(self, req: ACPControlRequest, x_internal_token: str | None):
        """对外部 agent 执行 new / stop：经 acpx（与群聊 session 后缀规则一致）。"""
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)

        team_hint = (req.team or "").strip()
        agent_key = (req.agent_name or "").strip()
        group_id = (req.group_id or "").strip()

        agent_info: dict | None = None
        if self.group_db_path and group_id:
            g = await get_group(self.group_db_path, group_id)
            if not g:
                raise HTTPException(status_code=404, detail="群聊不存在")
            if g.get("owner") != req.user_id:
                raise HTTPException(status_code=403, detail="只有群主可控制群内外部 agent")
            member = await get_group_member_by_global_id(self.group_db_path, group_id, agent_key)
            if not member:
                raise HTTPException(
                    status_code=404,
                    detail=f"群内未找到成员（global_id={agent_key}）",
                )
            if (member.get("member_type") or "").strip() != "ext":
                raise HTTPException(status_code=400, detail="该成员不是外部 agent")
            ext_map = build_external_agents_map_for_owner(req.user_id)
            agent_info = ext_map.get(agent_key, {})
            if not agent_info:
                short_n = str(member.get("short_name") or "").strip()
                tag_m = str(member.get("tag") or "").strip()
                platform_guess = _canonical_external_platform(tag_m)
                agent_info = {"global_name": agent_key, "name": short_n, "tag": tag_m, "platform": platform_guess if platform_guess else ""}
        else:
            agent_info = _resolve_external_agent_record(req.user_id, team_hint, agent_key)
            if not agent_info:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"外部 agent '{agent_key}' 未找到（team={team_hint or '(空)'}；"
                        "已查用户级 external_agents.json 与各 team 目录；"
                        "若在群内仅限 JSON 的成员，请在请求中提供 group_id）"
                    ),
                )

        global_name = agent_info.get("global_name", "")
        if not global_name:
            raise HTTPException(status_code=400, detail="该 agent 未配置 global_name")

        platform = _canonical_external_platform(str(agent_info.get("platform", "") or ""))
        suffix = _resolve_external_session_suffix(str(agent_info.get("model", "")))
        session_key = f"agent:{global_name}:{suffix}"
        acpx_policy = acpx_options_from_agent(
            agent_info,
            overrides={
                "timeout_sec": req.timeout_sec,
                "ttl_sec": req.ttl_sec,
                "approve_all": req.approve_all,
                "non_interactive_permissions": req.non_interactive_permissions,
            },
            default_timeout_sec=180,
        )

        if not shutil.which("acpx"):
            raise HTTPException(status_code=500, detail="acpx 未安装或不在 PATH")

        try:
            adapter = get_acpx_adapter(cwd=str(WORKSPACE_DIR / "acpx"))
        except AcpxError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

        use_openclaw_exec = platform == "openclaw"
        acpx_tool = platform

        logger.info(
            "acp_control action=%s agent=%s session_key=%s openclaw_exec=%s acpx_tool=%s",
            req.action,
            agent_key,
            session_key,
            use_openclaw_exec,
            acpx_tool,
        )

        if not platform:
            raise HTTPException(status_code=400, detail="external agent missing platform")
        if not use_openclaw_exec and acpx_tool not in _ACP_KNOWN_TOOLS and acpx_tool not in _ACPX_AGENT_TAGS:
            raise HTTPException(status_code=400, detail=f"unsupported external platform: {platform}")

        try:
            if req.action == "new":
                if use_openclaw_exec:
                    await adapter.ops_openclaw_exec_slash(
                        session_key=session_key,
                        slash="/new",
                        timeout_sec=acpx_policy["timeout_sec"],
                        ttl_sec=acpx_policy["ttl_sec"],
                        approve_all=acpx_policy["approve_all"],
                        non_interactive_permissions=acpx_policy["non_interactive_permissions"],
                    )
                else:
                    await adapter.ops_non_openclaw_reset_session(
                        tool=acpx_tool,
                        session_key=session_key,
                        timeout_sec=acpx_policy["timeout_sec"],
                        ttl_sec=acpx_policy["ttl_sec"],
                        approve_all=acpx_policy["approve_all"],
                        non_interactive_permissions=acpx_policy["non_interactive_permissions"],
                    )
                cleared_http_sessions = 0
                if self.group_db_path:
                    cleared_http_sessions = await delete_http_agent_sessions_by_global_name(
                        self.group_db_path,
                        global_name,
                    )
                return {
                    "status": "success",
                    "action": req.action,
                    "acp_session": session_key,
                    "cleared_http_sessions": cleared_http_sessions,
                    "message": f"已为 {agent_key} 请求新会话（acpx）",
                }

            if req.action == "stop":
                if use_openclaw_exec:
                    await adapter.ops_openclaw_exec_slash(
                        session_key=session_key,
                        slash="/stop",
                        timeout_sec=min(acpx_policy["timeout_sec"], 60),
                        ttl_sec=acpx_policy["ttl_sec"],
                        approve_all=acpx_policy["approve_all"],
                        non_interactive_permissions=acpx_policy["non_interactive_permissions"],
                    )
                else:
                    await adapter.ops_non_openclaw_cancel(
                        tool=acpx_tool,
                        session_key=session_key,
                        timeout_sec=min(acpx_policy["timeout_sec"], 60),
                        ttl_sec=acpx_policy["ttl_sec"],
                        approve_all=acpx_policy["approve_all"],
                        non_interactive_permissions=acpx_policy["non_interactive_permissions"],
                    )
                return {
                    "status": "success",
                    "action": req.action,
                    "acp_session": session_key,
                    "message": f"已请求停止 {agent_key}（acpx）",
                }

            if req.action == "delete":
                if use_openclaw_exec:
                    raise HTTPException(status_code=400, detail="openclaw delete should use native remove endpoint")
                acpx_session = adapter.to_acpx_session_name(tool=acpx_tool, session_key=session_key)
                await adapter.close_session(
                    tool=acpx_tool,
                    session_key=session_key,
                    acpx_session=acpx_session,
                    timeout_sec=min(acpx_policy["timeout_sec"], 60),
                    ttl_sec=acpx_policy["ttl_sec"],
                    approve_all=acpx_policy["approve_all"],
                    non_interactive_permissions=acpx_policy["non_interactive_permissions"],
                )
                cleared_http_sessions = 0
                if self.group_db_path:
                    cleared_http_sessions = await delete_http_agent_sessions_by_global_name(
                        self.group_db_path,
                        global_name,
                    )
                return {
                    "status": "success",
                    "action": req.action,
                    "acp_session": session_key,
                    "cleared_http_sessions": cleared_http_sessions,
                    "message": f"已关闭 {agent_key} 的 ACP 会话",
                }

            raise HTTPException(status_code=400, detail=f"未知 action: {req.action}")

        except AcpxError as e:
            msg = str(e)
            logger.warning("acp_control failed: %s", msg)
            if "timeout" in msg.lower():
                raise HTTPException(status_code=504, detail=msg) from e
            raise HTTPException(status_code=500, detail=msg) from e

    async def acp_status(self, req: ACPStatusRequest, x_internal_token: str | None):
        """查询外部 agent 的 session 状态列表。

        优先走 CLI `openclaw sessions --all-agents --json` 获取全局状态，
        如 CLI 不支持则逐个通过 ACP 协议 list_sessions。

        :param req: 状态查询请求，包含 user_id、team、agent_name（可选）
        :param x_internal_token: 内部令牌（可选）
        :return: agent 状态列表
        """
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)

        team_hint = (req.team or "").strip()
        if req.agent_name:
            agent_key = req.agent_name.strip()
            one = _resolve_external_agent_record(req.user_id, team_hint, agent_key)
            agents = [one] if one else []
        else:
            agents = []
            if team_hint:
                agents.extend(_load_team_external_agents(req.user_id, team_hint))
            else:
                agents.extend(_load_public_external_agents(req.user_id))
                team_base = os.path.join(str(USER_FILES_DIR), req.user_id, "teams")
                if os.path.isdir(team_base):
                    seen: set[str] = set()
                    for entry in sorted(os.listdir(team_base)):
                        path = os.path.join(team_base, entry)
                        if not os.path.isdir(path):
                            continue
                        for ea in _load_team_external_agents(req.user_id, entry):
                            gn = (ea.get("global_name") or "").strip()
                            key = gn or (ea.get("name") or "").strip()
                            if key and key not in seen:
                                seen.add(key)
                                agents.append(ea)

        if not agents:
            return {"status": "success", "agents": []}

        results = []

        # 方案 A: 尝试 CLI 快速获取
        cli_data = await self._acp_status_via_cli(agents)
        if cli_data is not None:
            return {"status": "success", "agents": cli_data}

        # 方案 B: 逐个走 ACP 协议
        for agent_info in agents:
            status_info = await self._acp_status_single(agent_info)
            results.append(status_info)

        return {"status": "success", "agents": results}

    async def update_check(self, req: UpdateCheckRequest, x_internal_token: str | None):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        try:
            snapshot = current_update_snapshot(fetch_remote=bool(req.refresh_remote))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"status": "success", "update": snapshot}

    async def update_start(self, req: UpdateStartRequest, x_internal_token: str | None):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        try:
            status = start_update_process(req.user_id, branch=req.branch)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"status": "success", "update": status}

    async def update_status(self, req: UpdateStatusRequest, x_internal_token: str | None):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        try:
            snapshot = current_update_snapshot(fetch_remote=False)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"status": "success", "update": snapshot}

    async def _acp_status_via_cli(self, agents: list[dict]) -> list[dict] | None:
        """尝试通过 CLI 获取所有 agent sessions（一次调用，高效）。

        :param agents: 外部 agent 配置列表
        :return: agent 状态列表，获取失败时返回 None
        """
        # 取第一个 agent 的 platform 来决定 binary
        first_platform = _canonical_external_platform(str(agents[0].get("platform", "") or "")) if agents else ""
        acp_tool = first_platform if first_platform else "openclaw"
        acp_bin = shutil.which(acp_tool)
        if not acp_bin:
            return None

        try:
            proc = await asyncio.create_subprocess_exec(
                acp_bin, "sessions", "--all-agents", "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
            if proc.returncode != 0:
                return None

            raw = json.loads(stdout.decode("utf-8", errors="replace"))
            # CLI may return {"sessions": [...]} or a bare list
            if isinstance(raw, dict):
                all_sessions = raw.get("sessions", [])
            elif isinstance(raw, list):
                all_sessions = raw
            else:
                return None

            # Build global_name -> agent_info mapping
            gname_map = {a["global_name"]: a for a in agents if a.get("global_name")}

            results = []
            for agent_info in agents:
                gn = agent_info.get("global_name", "")
                agent_sessions = [s for s in all_sessions if s.get("agent") == gn]
                results.append({
                    "name": agent_info["name"],
                    "global_name": gn,
                    "tag": agent_info.get("tag", ""),
                    "platform": agent_info.get("platform", ""),
                    "status": "online" if agent_sessions else "idle",
                    "session_count": len(agent_sessions),
                    "sessions": agent_sessions,
                })
            return results

        except (asyncio.TimeoutError, json.JSONDecodeError, Exception) as e:
            logger.debug("CLI status fallback: %s", e)
            return None

    async def _acp_status_single(self, agent_info: dict) -> dict:
        """通过 CLI 查询单个外部 agent 的 session 列表。

        bridge 不支持 session/list 方法，改用 `openclaw sessions --json` 过滤
        对应 agent 的 session key 前缀来判断状态。

        :param agent_info: 外部 agent 配置信息
        :return: 该 agent 的详细状态信息
        """
        name = agent_info.get("name", "")
        global_name = agent_info.get("global_name", "")
        platform = _canonical_external_platform(str(agent_info.get("platform", "") or ""))

        base_result = {
            "name": name,
            "global_name": global_name,
            "tag": agent_info.get("tag", ""),
            "platform": agent_info.get("platform", ""),
        }

        if not global_name:
            return {**base_result, "status": "unavailable", "reason": "no global_name"}

        acp_tool = platform if platform else "openclaw"
        acp_bin = shutil.which(acp_tool)
        if not acp_bin:
            return {**base_result, "status": "unavailable", "reason": f"binary '{acp_tool}' not found"}

        # 用 CLI sessions --json 列出所有 session，过滤 agent:<global_name>: 前缀
        session_prefix = f"agent:{global_name}:"
        try:
            proc = await asyncio.create_subprocess_exec(
                acp_bin, "sessions", "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
            if proc.returncode != 0:
                return {**base_result, "status": "unavailable", "reason": "CLI sessions failed"}

            raw = json.loads(stdout.decode("utf-8", errors="replace"))
            # CLI returns {"sessions": [...], ...} or bare list
            if isinstance(raw, dict):
                all_sessions = raw.get("sessions", [])
            elif isinstance(raw, list):
                all_sessions = raw
            else:
                return {**base_result, "status": "unavailable", "reason": "unexpected CLI output"}

            # 过滤属于该 agent 的 session（key 以 agent:<global_name>: 开头）
            agent_sessions = [
                s for s in all_sessions
                if str(s.get("key", s.get("session_key", ""))).startswith(session_prefix)
            ]

            return {
                **base_result,
                "status": "online" if agent_sessions else "idle",
                "session_count": len(agent_sessions),
                "sessions": [
                    {
                        "session_id": s.get("sessionId", s.get("session_id", s.get("id", ""))),
                        "key": s.get("key", s.get("session_key", "")),
                        "age": s.get("ageMs", s.get("age", "")),
                    }
                    for s in agent_sessions
                ],
            }

        except asyncio.TimeoutError:
            return {**base_result, "status": "timeout", "reason": "CLI sessions timeout"}
        except (json.JSONDecodeError, Exception) as e:
            return {**base_result, "status": "error", "reason": str(e)}




    async def list_all_sessions(self, user_id: str) -> dict:
        """Return acpx sessions (via acpx sessions list) + http_agent_sessions (from DB)."""
        acpx_sessions: list[dict] = []
        platforms = ["openclaw", "claude", "gemini", "codex", "aider"]
        acpx_bin = shutil.which("acpx")
        if not acpx_bin:
            platforms = []  # fallback: try direct binary names
        # ``acpx <plat> sessions list`` is a global registry view (not scoped to
        # cwd), so it lists every session regardless of where we run it. Each row
        # carries the session's own cwd (column 3 below) — that cwd is what
        # close_acp_session must reuse to actually close it.
        for plat_name in platforms:
            bin_path = acpx_bin if acpx_bin else shutil.which(plat_name)
            if not bin_path:
                continue
            try:
                args = [acpx_bin, plat_name, "sessions", "list"] if acpx_bin else [bin_path, "sessions"]
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
                if proc.returncode != 0:
                    continue
                lines = stdout.decode("utf-8", errors="replace").strip().splitlines()
                for line in lines:
                    if not line.strip():
                        continue
                    parts = line.split("	")
                    if len(parts) < 2:
                        continue
                    session_id = parts[0].replace(" [closed]", "").strip()
                    name = parts[1].strip() if len(parts) > 1 else ""
                    cwd = parts[2].strip() if len(parts) > 2 else ""
                    last_used = parts[3].strip() if len(parts) > 3 else ""
                    closed = "[closed]" in parts[0]
                    acpx_sessions.append({
                        "platform": plat_name,
                        "session_id": session_id,
                        "name": name,
                        "cwd": cwd,
                        "last_used_at": last_used,
                        "closed": closed,
                    })
            except (asyncio.TimeoutError, Exception):
                continue

        http_records: list[dict] = []
        if self.group_db_path:
            try:
                http_records = await list_http_agent_sessions(self.group_db_path)
            except Exception:
                pass

        return {
            "status": "success",
            "acpx_sessions": acpx_sessions,
            "http_agent_sessions": http_records,
        }


    async def delete_http_agent_session(self, user_id: str, session_key: str) -> dict:
        """Delete a single http_agent_sessions record by session_key."""
        if not self.group_db_path:
            return {"status": "error", "reason": "no db"}
        try:
            deleted = await delete_http_agent_session_by_key(self.group_db_path, session_key)
            return {"status": "success", "deleted": deleted}
        except Exception as e:
            return {"status": "error", "reason": str(e)}


    async def close_acp_session(self, platform: str, session_name: str, cwd: str = "") -> dict:
        """Close an acpx session via 'acpx --cwd <session_cwd> <platform> sessions close <name>'.

        ``acpx`` binds every session to the cwd it was created in, and
        ``sessions close`` only acts on the current cwd (``acpx --help``:
        "Close session for current cwd"). The list rows carry each session's
        own cwd (column 3) — close must reuse *that* exact cwd, not a fixed
        store path. Closing from any other cwd prints
        ``No named session "<name>" for cwd <dir>`` and still exits 0, so the
        old fixed-``WORKSPACE_DIR/acpx`` path silently no-oped for every
        session created elsewhere while the UI reported success.
        """
        acpx_bin = shutil.which("acpx")
        if not acpx_bin:
            return {"status": "error", "reason": "acpx not found"}
        acpx_cwd = (cwd or "").strip() or None
        if acpx_cwd is None:
            # No session cwd supplied: fall back to the canonical store so newly
            # created sessions (which use WORKSPACE_DIR/acpx) still close.
            try:
                from utils.runtime_paths import WORKSPACE_DIR  # local import to avoid cycles
                acpx_cwd = os.path.join(str(WORKSPACE_DIR), "acpx")
                os.makedirs(acpx_cwd, exist_ok=True)
            except Exception:
                acpx_cwd = None
        cmd = [acpx_bin]
        if acpx_cwd:
            cmd.extend(["--cwd", acpx_cwd])
        cmd.extend([platform, "sessions", "close", session_name])
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=acpx_cwd or None,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            err_text = (stderr.decode("utf-8", errors="replace") or "").strip()
            # acpx exits 0 even when it finds nothing to close ("No named session
            # ... for cwd ..."), so returncode alone can't confirm success. Treat
            # that message as a real failure instead of a false "closed".
            if "No named session" in err_text:
                return {"status": "error", "reason": err_text[:200]}
            # exit 0 = just closed, exit 1 = already closed (both fine for idempotency)
            if proc.returncode in (0, 1):
                return {"status": "success", "stderr": err_text} if err_text else {"status": "success"}
            return {"status": "error", "reason": err_text[:200] or f"exit={proc.returncode}"}
        except Exception as e:
            return {"status": "error", "reason": str(e)}
