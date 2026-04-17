import asyncio
import json
import os
import re
import shutil
from typing import Any, Callable

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from integrations.acpx_adapter import AcpxError, get_acpx_adapter
from integrations.acpx_cli_tools import acpx_agent_tags_with_legacy
from utils.auth_utils import extract_user_password_session, is_internal_bearer, parse_bearer_parts
from api.group_repository import (
    delete_http_agent_sessions_by_global_name,
    delete_http_agent_session_by_key,
    get_group,
    get_group_member_by_global_id,
    list_http_agent_sessions,
)
from api.group_service import _load_public_external_agents, build_external_agents_map_for_owner
from services.llm_factory import get_provider_audio_defaults, infer_provider
from utils.logging_utils import get_logger
from api.ops_models import ACPControlRequest, ACPStatusRequest, CancelRequest, LoginRequest, TTSRequest

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
    path = os.path.join(_PROJECT_ROOT, "data", "user_files", user_id, "teams", team, "external_agents.json")
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
    team_base = os.path.join(_PROJECT_ROOT, "data", "user_files", user_id, "teams")
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
                agent_info = {"global_name": agent_key, "name": short_n, "tag": tag_m, "platform": ""}
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

        if not shutil.which("acpx"):
            raise HTTPException(status_code=500, detail="acpx 未安装或不在 PATH")

        try:
            adapter = get_acpx_adapter(cwd=_PROJECT_ROOT)
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
                        session_key=session_key, slash="/new", timeout_sec=180
                    )
                else:
                    await adapter.ops_non_openclaw_reset_session(
                        tool=acpx_tool, session_key=session_key
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
                        session_key=session_key, slash="/stop", timeout_sec=25
                    )
                else:
                    await adapter.ops_non_openclaw_cancel(tool=acpx_tool, session_key=session_key)
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
                team_base = os.path.join(_PROJECT_ROOT, "data", "user_files", req.user_id, "teams")
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


    async def close_acp_session(self, platform: str, session_name: str) -> dict:
        """Close an acpx session via 'acpx <platform> sessions close <name>'."""
        acpx_bin = shutil.which("acpx")
        if not acpx_bin:
            return {"status": "error", "reason": "acpx not found"}
        try:
            proc = await asyncio.create_subprocess_exec(
                acpx_bin, platform, "sessions", "close", session_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            # exit 0 = just closed, exit 1 = already closed (both are fine)
            if proc.returncode in (0, 1):
                return {"status": "success"}
            return {"status": "error", "reason": stderr.decode()[:200]}
        except Exception as e:
            return {"status": "error", "reason": str(e)}
