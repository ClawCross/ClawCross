import asyncio
import base64
import json
import os
import re
import secrets
import shutil
import threading
import time
import utils.scheduler_service
from typing import Any, Callable, Literal

import httpx
from fastapi import HTTPException

from utils.auth_utils import extract_user_password_session, is_internal_bearer, parse_bearer_parts
from utils.checkpoint_repository import list_thread_ids_by_prefix
from api.group_repository import (
    add_group_member,
    clear_group_members,
    create_group_with_members,
    delete_group as delete_group_records,
    get_group,
    get_group_member_by_global_id,
    get_group_owner,
    group_exists,
    init_group_db as init_group_db_repo,
    insert_group_message,
    list_group_member_targets,
    list_group_members,
    list_group_messages_after,
    list_groups_for_user,
    list_recent_group_messages,
    remove_group_member,
    upsert_http_agent_session,
    update_group_name,
)
from api.group_models import Attachment, GroupCreateRequest, GroupAddMemberRequest, GroupMessageRequest, GroupUpdateRequest
from utils.logging_utils import get_logger
from utils.session_summary import first_human_title
from integrations.acpx_adapter import AcpxError, get_acpx_adapter, load_external_agent_system_prompt
from integrations.acpx_cli_tools import acpx_agent_tags_with_legacy
from integrations.agent_sender import SendToAgentRequest, send_to_agent
from integrations.external_persona import build_external_persona_prompt

logger = get_logger("group_service")

# Subcommands accepted by `acpx <tag> ...` (from `acpx --help` + legacy aliases)
_ACP_TOOL_NAMES: frozenset[str] = acpx_agent_tags_with_legacy()
_DEFAULT_ACP_SESSION_SUFFIX = "clawcrosschat"
_AGENT_MODEL_RE = re.compile(r"^agent:[^:]+(?::(.+))?$")

# ACPX-backed ACP support
_ACP_AVAILABLE = bool(shutil.which("acpx"))

# Project root for team-scoped paths
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

_EXTERNAL_AGENT_GROUP_RULES_PATH = os.path.join(
    _PROJECT_ROOT, "data", "prompts", "external_agent_group_rules.txt",
)
_external_agent_group_rules_cache: str | None = None
_EXTERNAL_AGENT_PRIVATE_RULES_PATH = os.path.join(
    _PROJECT_ROOT, "data", "prompts", "external_agent_private_rules.txt",
)
_external_agent_private_rules_cache: str | None = None
_external_agent_system_prompt_cache: str | None = None


def _external_agent_group_rules_block() -> str:
    """Rules for ext agents (ACP/HTTP): no access to agent.py group_chat_rules; prompt must be inlined."""
    global _external_agent_group_rules_cache
    if _external_agent_group_rules_cache is not None:
        return _external_agent_group_rules_cache
    try:
        with open(_EXTERNAL_AGENT_GROUP_RULES_PATH, encoding="utf-8") as f:
            _external_agent_group_rules_cache = f.read().strip()
    except Exception:
        _external_agent_group_rules_cache = (
            "【外部 Agent】人类问候、@你、直呼你名时必须 groups send 简短回复；"
            "其他消息仅在与职责相关、被点名或面向众人需要专业意见时回复。"
        )
    return _external_agent_group_rules_cache


def _external_agent_private_rules_block() -> str:
    global _external_agent_private_rules_cache
    if _external_agent_private_rules_cache is not None:
        return _external_agent_private_rules_cache
    try:
        with open(_EXTERNAL_AGENT_PRIVATE_RULES_PATH, encoding="utf-8") as f:
            _external_agent_private_rules_cache = f.read().strip()
    except Exception:
        _external_agent_private_rules_cache = (
            "【外部 Agent 私聊须知】当前是用户与你的一对一私聊，直接回答，不要写成群发或广播口吻。"
        )
    return _external_agent_private_rules_cache


def _external_agent_system_prompt() -> str:
    global _external_agent_system_prompt_cache
    if _external_agent_system_prompt_cache is None:
        _external_agent_system_prompt_cache = load_external_agent_system_prompt(_PROJECT_ROOT)
    return _external_agent_system_prompt_cache


def _external_agent_session_prompt(agent_info: dict, *, is_private_chat: bool) -> str:
    parts = [
        _external_agent_system_prompt(),
        build_external_persona_prompt(
            str(agent_info.get("tag", "") or ""),
            user_id=str(agent_info.get("owner_user_id", "") or ""),
            team=str(agent_info.get("team", "") or ""),
        ),
    ]
    if is_private_chat:
        parts.append(_external_agent_private_rules_block())
    else:
        parts.append(_external_agent_group_rules_block())
    return "\n\n".join(p for p in parts if p).strip()


def _external_http_registry_prompt(agent_info: dict) -> str:
    parts = [
        _external_agent_system_prompt(),
        build_external_persona_prompt(
            str(agent_info.get("tag", "") or ""),
            user_id=str(agent_info.get("owner_user_id", "") or ""),
            team=str(agent_info.get("team", "") or ""),
        ),
        _external_agent_group_rules_block(),
        _external_agent_private_rules_block(),
    ]
    return "\n\n".join(p for p in parts if p).strip()


def _canonical_external_platform(platform: str) -> str:
    pl = (platform or "").strip().lower()
    if pl in ("claude-code", "claudecode"):
        return "claude"
    if pl in ("gemini-cli", "geminicli"):
        return "gemini"
    return pl


def _external_platform_from_agent(agent_info: dict) -> str:
    """Resolve transport platform for external agents.

    Prefer explicit ``platform``. Fall back to legacy ``tag`` because mobile
    private-chat flows still persist ext members with tag-only metadata.
    """
    return _canonical_external_platform(
        str(agent_info.get("platform", "") or agent_info.get("tag", "") or "")
    )


# ── Temporary ACP lifecycle trace (群聊): logger [ACP_TRACE] + logs/acp_group_trace.jsonl
_ACP_TRACE_PATH = os.path.join(_PROJECT_ROOT, "logs", "acp_group_trace.jsonl")
_acp_trace_file_lock = threading.Lock()


def _acp_group_trace(event: str, **fields: Any) -> None:
    """Temporarily disabled ACP trace output for group chat."""
    return


def _select_external_transport(platform: str) -> Literal["acp", "http", "drop"]:
    pl = _canonical_external_platform(platform)
    if not pl:
        return "drop"
    if pl == "openclaw":
        return "http"
    if pl in _ACP_TOOL_NAMES:
        return "acp"
    return "drop"


def _resolve_external_session_suffix(model: str) -> str:
    m = _AGENT_MODEL_RE.match((model or "").strip())
    if m and m.group(1):
        return m.group(1)
    return _DEFAULT_ACP_SESSION_SUFFIX


def _external_http_session_key(agent_info: dict) -> str:
    global_name = str(agent_info.get("global_name", "")).strip()
    session_suffix = _resolve_external_session_suffix(str(agent_info.get("model", "")))
    return f"agent:{global_name}:{session_suffix}"


# ── Text MIME helpers (shared with system_service) ──
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_EXACT = {
    "application/json", "application/xml", "application/javascript",
    "application/typescript", "application/x-yaml", "application/yaml",
    "application/toml", "application/x-toml",
    "application/sql", "application/graphql",
    "application/x-sh", "application/x-python",
    "application/csv", "application/x-csv",
    "application/ld+json", "application/manifest+json",
}


def _is_text_mime(mime_type: str) -> bool:
    """判断 MIME 类型是否为文本类。"""
    mime = mime_type.lower().strip()
    if any(mime.startswith(p) for p in _TEXT_MIME_PREFIXES):
        return True
    if mime in _TEXT_MIME_EXACT:
        return True
    if mime.endswith("+json") or mime.endswith("+xml"):
        return True
    return False


def _decode_att_text(att_data: str, max_chars: int = 50000) -> str | None:
    """尝试将附件 base64 数据解码为 UTF-8 文本。失败返回 None。"""
    try:
        raw = base64.b64decode(att_data)
        text = raw.decode("utf-8")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n... (文件过长，已截断，共 {len(raw)} 字节)"
        return text
    except Exception:
        return None


def _compose_acpx_prompt(message: str, attachments: list[Attachment] | None = None) -> str:
    """Compose one plain-text prompt for acpx-backed ACP call."""
    parts: list[str] = [message]
    for att in attachments or []:
        if att.type == "image":
            parts.append(f"[附件: {att.name} ({att.mime_type}), 图片已随多模态附件发送]")
            continue
        if att.type == "audio":
            parts.append(f"[附件: {att.name} ({att.mime_type}), 音频已随多模态附件发送]")
            continue
        if _is_text_mime(att.mime_type):
            decoded = _decode_att_text(att.data)
            if decoded is not None:
                parts.append(f"\n📄 附件「{att.name}」内容:\n```\n{decoded}\n```")
            else:
                parts.append(f"[附件: {att.name} ({att.mime_type}), 解码失败]")
        else:
            parts.append(f"[附件: {att.name} ({att.mime_type}), 二进制文件无法展示]")
    return "\n\n".join(p for p in parts if p)


def _load_team_internal_agents(user_id: str, team: str) -> list[dict]:
    """Load internal agents from team's internal_agents.json.

    Returns list of {"user_id": user_id, "global_id": session, "short_name": name, "member_type": "oasis", "tag": ...}
    """
    if not user_id or not team:
        return []
    path = os.path.join(_PROJECT_ROOT, "data", "user_files", user_id, "teams", team, "internal_agents.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        result = []
        for a in data:
            if not isinstance(a, dict) or "session" not in a:
                continue
            result.append({
                "user_id": user_id,
                "global_id": a.get("session", ""),
                "short_name": a.get("name", ""),
                "member_type": "oasis",
                "tag": a.get("tag", ""),
            })
        return result
    except Exception:
        return []


def _public_external_agents_path(user_id: str) -> str:
    """User-level external_agents.json (not tied to a team)."""
    return os.path.join(_PROJECT_ROOT, "data", "user_files", user_id, "external_agents.json")


def _parse_external_agents_file(path: str, *, owner_user_id: str = "", team: str = "") -> list[dict]:
    """Parse external_agents.json list into member-style dicts (shared shape with team file)."""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        result = []
        for a in data:
            if not isinstance(a, dict) or "name" not in a:
                continue
            ext_config = a.get("config") or a.get("meta") or {}
            nm = a.get("name", "")
            gn = a.get("global_name", "")
            result.append({
                "user_id": "ext",
                "owner_user_id": owner_user_id,
                "global_id": gn,
                "short_name": nm,
                "member_type": "ext",
                "tag": a.get("tag", ""),
                "global_name": gn,
                "name": nm,
                "team": team,
                "platform": _canonical_external_platform(str(a.get("platform", "") or "")),
                "api_url": ext_config.get("api_url", ""),
                "api_key": ext_config.get("api_key", ""),
                "model": ext_config.get("model", ""),
            })
        return result
    except Exception:
        return []


def _load_public_external_agents(user_id: str) -> list[dict]:
    """Load external agents from user-level external_agents.json."""
    if not user_id:
        return []
    return _parse_external_agents_file(_public_external_agents_path(user_id), owner_user_id=user_id)


def _load_team_external_agents(user_id: str, team: str) -> list[dict]:
    """Load external agents from team's external_agents.json.

    Returns list of {"user_id": "ext", "global_id": global_name, "short_name": name, "member_type": "ext", ...}
    """
    if not user_id or not team:
        return []
    path = os.path.join(_PROJECT_ROOT, "data", "user_files", user_id, "teams", team, "external_agents.json")
    return _parse_external_agents_file(path, owner_user_id=user_id, team=team)


def build_external_agents_map_for_owner(owner_uid: str) -> dict[str, dict]:
    """Merge user-level + all teams' external_agents.json by global_id (same as broadcast_to_group)."""
    external_agents_map: dict[str, dict] = {}
    if not owner_uid:
        return external_agents_map
    for ea in _load_public_external_agents(owner_uid):
        gid = str(ea.get("global_id") or ea.get("global_name") or "").strip()
        if gid:
            external_agents_map[gid] = ea
    teams_dir = os.path.join(_PROJECT_ROOT, "data", "user_files", owner_uid, "teams")
    if os.path.isdir(teams_dir):
        for team_dir in os.listdir(teams_dir):
            for ea in _load_team_external_agents(owner_uid, team_dir):
                gid = str(ea.get("global_id", "") or "").strip()
                if gid:
                    external_agents_map[gid] = ea
    return external_agents_map


def _load_team_members(user_id: str, team: str) -> list[dict]:
    """Load all team members (internal + external agents).

    Returns list of member dicts with user_id, session_id, member_type, etc.
    """
    internal = _load_team_internal_agents(user_id, team)
    external = _load_team_external_agents(user_id, team)
    return internal + external


async def init_group_db(group_db_path: str) -> None:
    """初始化群聊数据库表结构。"""
    await init_group_db_repo(group_db_path)


def _group_id_name_segment(display_name: str) -> str:
    """从展示用群名得到 group_id 中段（owner::此段）。

    保留中文及绝大部分可打印字符（不丢语义）；只去掉对 ``uid::segment`` 和路径不安全的字符
    （``:``、``/``、``\\``、控制符）。仅当去完后为空时用 hash 兜底，避免撞车。
    """
    raw = (display_name or "").strip()
    if not raw:
        return "h" + hashlib.sha256(b"").hexdigest()[:20]
    segment = re.sub(r"[:/\\\x00-\x1f]", "_", raw).strip()
    if not segment:
        return "h" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return segment[:120]


_TYPING_TIMEOUT_SEC = 120  # 超时自动清除"正在输入"状态


class GroupService:
    def __init__(
        self,
        *,
        internal_token: str,
        verify_password: Callable[[str, str], bool],
        checkpoint_db_path: str,
        group_db_path: str,
        agent: Any,
    ):
        self.internal_token = internal_token
        self.verify_password = verify_password
        self.checkpoint_db_path = checkpoint_db_path
        self.group_db_path = group_db_path
        self.agent = agent
        self.group_muted: set[str] = set()
        # Typing state: {group_id: {display_name: timestamp}}
        self._typing_agents: dict[str, dict[str, float]] = {}

    # ── Typing indicator helpers ──

    def set_typing(self, group_id: str, display_name: str) -> None:
        """标记某 agent 在某群正在输入。"""
        if group_id not in self._typing_agents:
            self._typing_agents[group_id] = {}
        self._typing_agents[group_id][display_name] = time.time()

    def clear_typing(self, group_id: str, display_name: str) -> None:
        """清除某 agent 的正在输入状态。"""
        bucket = self._typing_agents.get(group_id)
        if bucket:
            bucket.pop(display_name, None)

    def clear_typing_by_sender_display(self, group_id: str, sender_display: str) -> None:
        """根据 sender_display (tag#type#short_name#global_id) 清除输入状态。"""
        bucket = self._typing_agents.get(group_id)
        if not bucket:
            return
        # sender_display 格式: tag#type#short_name#global_id
        # 从中提取 short_name 用于匹配
        parts = sender_display.split("#")
        short_name = parts[2] if len(parts) > 2 else ""
        # 清除所有匹配的 key（精确匹配 display_name 或 short_name）
        to_remove = [k for k in bucket if k == sender_display or k == short_name]
        for k in to_remove:
            bucket.pop(k, None)

    def get_typing_agents(self, group_id: str) -> list[str]:
        """返回某群中正在输入的 agent 列表（自动清理超时条目）。"""
        bucket = self._typing_agents.get(group_id)
        if not bucket:
            return []
        now = time.time()
        expired = [k for k, ts in bucket.items() if now - ts > _TYPING_TIMEOUT_SEC]
        for k in expired:
            bucket.pop(k, None)
        return list(bucket.keys())

    async def get_typing_status(self, group_id: str, authorization: str | None) -> dict:
        """返回群中正在输入的 agent 列表。

        同时检查内部 agent 的 thread lock 状态：
        如果内部 agent 的 thread lock 仍被占用（is_thread_busy），保持其 typing 状态。
        """
        self.parse_group_auth(authorization)  # 鉴权
        typing_list = self.get_typing_agents(group_id)

        # 检查内部 agent 的 thread lock 状态
        members = await list_group_member_targets(self.group_db_path, group_id)
        for _uid, global_id, is_agent, member_type, short_name, tag in members:
            if not is_agent:
                continue
            if member_type == "oasis":
                # 内部 agent: 通过 thread lock 检查是否仍在处理
                thread_id = f"{_uid}#{global_id}"
                if self.agent.is_thread_busy(thread_id):
                    if short_name not in typing_list:
                        typing_list.append(short_name)
                else:
                    # lock 已释放但 typing 列表还有，清除
                    if short_name in typing_list:
                        self.clear_typing(group_id, short_name)
                        typing_list = [n for n in typing_list if n != short_name]

        return {"typing": typing_list}

    def parse_group_auth(self, authorization: str | None):
        """从 Bearer token 解析用户认证，返回 (user_id, password, session_id)。"""
        parts = parse_bearer_parts(authorization)
        if not parts:
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        if len(parts) < 2:
            raise HTTPException(status_code=401, detail="Invalid token format")

        if is_internal_bearer(parts, self.internal_token):
            uid = parts[1] if len(parts) >= 2 and parts[1] else "system"
            sid = parts[2] if len(parts) > 2 else "default"
            return uid, "", sid

        parsed = extract_user_password_session(parts, default_session="default")
        if not parsed:
            raise HTTPException(status_code=401, detail="Invalid token format")
        uid, pw, sid = parsed
        if not self.verify_password(uid, pw):
            raise HTTPException(status_code=401, detail="认证失败")
        return uid, pw, sid

    async def get_agent_title(self, user_id: str, session_id: str) -> str:
        """从 checkpoint 提取 agent 的 session title（第一条非系统触发 HumanMessage 前50字）。"""
        tid = f"{user_id}#{session_id}"
        try:
            config = {"configurable": {"thread_id": tid}}
            snapshot = await self.agent.agent_app.aget_state(config)
            msgs = snapshot.values.get("messages", []) if snapshot and snapshot.values else []
            title = first_human_title(
                msgs,
                skip_prefixes=("[系统触发]", "[外部学术会议邀请]", "[群聊"),
                title_len=50,
                list_fallback="",
                default=session_id,
            )
            return title
        except Exception:
            pass
        return session_id

    async def _send_to_acp_agent(
        self,
        agent_info: dict,
        message: str,
        attachments: list[Attachment] | None = None,
        metadata: dict | None = None,
        *,
        _retry_dead: bool = False,
    ) -> str | None:
        """Send message to external agent via acpx-backed ACP session."""
        if not _ACP_AVAILABLE:
            logger.warning("acpx not available for %s", agent_info.get("name"))
            return None

        platform = _external_platform_from_agent(agent_info)
        global_name = agent_info.get("global_name", "")
        if not global_name:
            logger.warning("No global_name for external agent %s", agent_info.get("name"))
            return None
        if not platform:
            logger.warning("Missing ACP tool platform for %s", global_name)
            return None
        if platform not in _ACP_TOOL_NAMES:
            logger.warning("Unsupported ACP platform '%s' for %s", platform, global_name)
            return None

        session_suffix = _resolve_external_session_suffix(str(agent_info.get("model", "")))
        acp_session = f"agent:{global_name}:{session_suffix}"
        t0 = time.time()
        prompt_text = _compose_acpx_prompt(message, attachments)
        acpx_session = ""
        _acp_group_trace(
            "acp_ephemeral_start",
            phase="acpx_prompt",
            agent_global_name=global_name,
            platform=platform,
            cli_session_arg=acp_session,
            attachment_count=len(attachments or []),
        )
        try:
            adapter = get_acpx_adapter(cwd=_PROJECT_ROOT)
            acpx_session = adapter.to_acpx_session_name(tool=platform, session_key=acp_session)
            acpx_attachments = None
            if attachments:
                acpx_attachments = [
                    {
                        "type": att.type,
                        "mime_type": att.mime_type,
                        "data": att.data,
                        "name": att.name,
                    }
                    for att in attachments
                ]

            result = await send_to_agent(
                SendToAgentRequest(
                    prompt=prompt_text,
                    connect_type="acp",
                    platform=platform,
                    session=acp_session,
                    options={
                        "cwd": _PROJECT_ROOT,
                        "timeout_sec": 180,
                        "reset_session": bool(metadata and metadata.get("resetSession")),
                        "system_prompt": _external_agent_session_prompt(
                            agent_info,
                            is_private_chat=bool(metadata and metadata.get("is_private_chat")),
                        ),
                        "attachments": acpx_attachments,
                    },
                )
            )
            if not result.ok:
                raise AcpxError(result.error or "unknown acpx send error")
            reply = result.content or ""
            _acp_group_trace(
                "acp_prompt_complete",
                agent_global_name=global_name,
                cli_session_arg=acp_session,
                reply_chars=len(reply or ""),
                attachment_count=len(attachments or []),
                elapsed_ms=round((time.time() - t0) * 1000),
                cached=True,
                backend="acpx",
                acpx_session=acpx_session,
            )
            return reply or None
        except AcpxError as e:
            _acp_group_trace(
                "acp_error",
                agent_global_name=global_name,
                cli_session_arg=acp_session,
                error_type=type(e).__name__,
                error=str(e),
                elapsed_ms=round((time.time() - t0) * 1000),
                backend="acpx",
                acpx_session=acpx_session,
            )
            logger.warning("acpx send failed for %s: %s", global_name, e)
            return None

    async def _send_to_http_agent(
        self,
        agent_info: dict,
        message: str,
        attachments: list[Attachment] | None = None,
        metadata: dict | None = None,
    ) -> str | None:
        """Fallback: send message to external agent via HTTP API.
        
        Message already contains clawcross_type instruction from caller.
        Supports multimodal content via OpenAI image_url / input_audio format.
        """
        api_url = agent_info.get("api_url", "")
        api_key = agent_info.get("api_key", "")
        platform = _external_platform_from_agent(agent_info)
        global_name = str(agent_info.get("global_name", "")).strip()
        if platform == "openclaw":
            # OpenClaw endpoint is device-dependent: prefer runtime env over saved config.
            api_url = os.getenv("OPENCLAW_API_URL", "") or api_url
            api_key = os.getenv("OPENCLAW_GATEWAY_TOKEN", "") or api_key
        model = agent_info.get("model", "gpt-3.5-turbo")
        # Keep OpenClaw HTTP payload aligned with /proxy_openclaw_chat:
        # use agent:<global_name> model for gateway session routing.
        if platform == "openclaw" and global_name:
            model_str = str(model or "").strip()
            if not model_str.startswith("agent:"):
                model = f"agent:{global_name}"

        if not api_url:
            logger.warning("No api_url for external agent %s", agent_info.get("name"))
            return None

        # Normalize URL
        api_url = api_url.rstrip("/")
        if not api_url.endswith("/v1/chat/completions"):
            if not api_url.endswith("/v1"):
                api_url += "/v1"
            api_url += "/chat/completions"

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        is_private_chat = bool(metadata and metadata.get("is_private_chat"))
        session_prompt = _external_http_registry_prompt(agent_info)
        session_key = _external_http_session_key(agent_info) if global_name else ""
        if self.group_db_path and global_name and session_key and session_prompt:
            should_inject = await upsert_http_agent_session(
                self.group_db_path,
                session_key=session_key,
                global_name=global_name,
                prompt_text=session_prompt,
                transport="http",
                now_ts=time.time(),
            )
            if should_inject:
                message = f"{session_prompt}\n\n{message}".strip()

        if platform == "openclaw" and global_name:
            headers["x-openclaw-session-key"] = session_key

        # Build message content (multimodal if attachments present)
        if attachments:
            content_parts: list[dict] = [{"type": "text", "text": message}]
            for att in attachments:
                if att.type == "image":
                    data_uri = f"data:{att.mime_type};base64,{att.data}"
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": data_uri},
                    })
                elif att.type == "audio":
                    content_parts.append({
                        "type": "input_audio",
                        "input_audio": {
                            "data": att.data,
                            "format": att.mime_type.split("/")[-1],
                        },
                    })
                else:
                    # Generic file: try to decode text content, fallback to description
                    if _is_text_mime(att.mime_type):
                        decoded = _decode_att_text(att.data)
                        if decoded is not None:
                            content_parts.append({
                                "type": "text",
                                "text": f"\n📄 附件「{att.name}」内容:\n```\n{decoded}\n```",
                            })
                        else:
                            content_parts.append({
                                "type": "text",
                                "text": f"[附件: {att.name} ({att.mime_type}), 解码失败]",
                            })
                    else:
                        content_parts.append({
                            "type": "text",
                            "text": f"[附件: {att.name} ({att.mime_type}), 二进制文件无法展示]",
                        })
            msg_content: str | list = content_parts
        else:
            msg_content = message

        body = {
            "model": model,
            "messages": [{"role": "user", "content": msg_content}],
            "stream": False,
        }

        result = await send_to_agent(
            SendToAgentRequest(
                prompt=body["messages"],
                connect_type="http",
                platform=platform,
                session=session_key or None,
                options={
                    "api_url": api_url,
                    "api_key": api_key,
                    "headers": headers,
                    "body": body,
                    "timeout": 60,
                },
            )
        )
        if not result.ok:
            logger.warning("HTTP send failed for %s: %s", agent_info.get("name"), result.error)
            return None
        return result.content

    async def _handle_external_agent_reply(
        self,
        group_id: str,
        agent_info: dict,
        message: str,
        agent_name: str,
        attachments: list[Attachment] | None = None,
        metadata: dict | None = None,
    ):
        """Send message to external agent and handle reply.

        Both private and group chats use push-only delivery for external agents.
        ACP/HTTP synchronous replies are not auto-posted into the group; the
        agent must use CLI `groups send` to publish visible messages.
        """
        # 标记正在输入
        self.set_typing(group_id, agent_name)
        # Select transport explicitly by tag.
        platform = _external_platform_from_agent(agent_info)
        transport = _select_external_transport(platform)
        if transport == "drop":
            logger.warning(
                "Drop external agent %s due to unknown platform '%s'",
                agent_name,
                agent_info.get("platform", "") or agent_info.get("tag", ""),
            )
            return
        if transport == "acp":
            reply = await self._send_to_acp_agent(
                agent_info,
                message,
                attachments=attachments,
                metadata=metadata,
            )
            if not reply:
                logger.info(
                    "ACP failed/no reply for %s, fallback to HTTP",
                    agent_name,
                )
                reply = await self._send_to_http_agent(
                    agent_info,
                    message,
                    attachments=attachments,
                    metadata=metadata,
                )
        else:
            reply = await self._send_to_http_agent(
                agent_info,
                message,
                attachments=attachments,
                metadata=metadata,
            )
            if not reply and platform == "openclaw":
                logger.info(
                    "HTTP failed/no reply for openclaw %s, fallback to ACP",
                    agent_name,
                )
                reply = await self._send_to_acp_agent(
                    agent_info,
                    message,
                    attachments=attachments,
                    metadata=metadata,
                )
        if not reply:
            logger.info("External agent %s did not reply", agent_name)
            # ACP/HTTP 已返回（无回复），清除输入状态
            self.clear_typing(group_id, agent_name)
            return

        members = await list_group_member_targets(self.group_db_path, group_id)
        is_private = len(members) <= 2
        if is_private:
            logger.info(
                "Private chat: agent %s returned sync reply (ignored; waiting for push): %s",
                agent_name,
                reply[:200] if reply else "",
            )
        else:
            logger.info("Group chat: agent %s replied (not auto-posting): %s", agent_name, reply[:200] if reply else "")
        # ACP/HTTP 已返回，清除输入状态
        self.clear_typing(group_id, agent_name)

    async def broadcast_to_group(
        self,
        group_id: str,
        sender: str,
        content: str,
        exclude_sender_display: str = "",
        mentions: list[str] | None = None,
        user_id: str = "",
        attachments: list[Attachment] | None = None,
    ):
        """向群内 agent 成员广播消息（异步 fire-and-forget）。

        members 直接从数据库读取，包含 global_id / short_name / tag / member_type。
        exclude_sender_display: 用 tag#type#short_name 格式排除发送者自己，
        避免 global_id 在不同 agent 平台间可能冲突的问题。
        """
        if group_id in self.group_muted:
            logger.info("群 %s 已静音，跳过广播", group_id)
            return
        members = await list_group_member_targets(self.group_db_path, group_id)
        member_count = len(members)
        is_private_chat = member_count <= 2  # owner + 1 agent = 私聊
        owner_uid = user_id or await get_group_owner(self.group_db_path, group_id) or ""
        human_user_hint = (
            f"当前群主 owner=\"{owner_uid}\"。当前人类用户是「{owner_uid}」。"
        )

        # Build external agent config map by global_id (need api_url etc. for ACP/HTTP)
        external_agents_map: dict[str, dict] = (
            build_external_agents_map_for_owner(user_id) if user_id else {}
        )

        for user_id_member, global_id, is_agent, member_type, short_name, tag in members:
            if group_id in self.group_muted:
                logger.info("群 %s 广播中途被静音，停止", group_id)
                return
            if not is_agent:
                continue
            # 用 tag#type#short_name#global_id 排除发送者自己
            member_display = f"{tag}#{member_type}#{short_name}#{global_id}" if tag else f"#{member_type}#{short_name}#{global_id}"
            if exclude_sender_display and member_display == exclude_sender_display:
                continue

            if mentions and global_id not in mentions:
                continue

            # Build message - use prompt instructions to guide agent behavior
            agent_identity = f"你是「{short_name}」"
            sender_display = f"{tag}#{member_type}#{short_name}#{global_id}" if tag else f"#{member_type}#{short_name}#{global_id}"
            group_cli_hint = (f"cd {_PROJECT_ROOT} && "
                              f"uv run scripts/cli.py groups send --group-id {group_id} --sender '{sender_display}' --message '你的回复内容'")
            private_cli_hint = (f"cd {_PROJECT_ROOT} && "
                                f"uv run scripts/cli.py groups private-send --group-id {group_id} --sender '{sender_display}' --message '你的回复内容'")

            _ext_rules = _external_agent_group_rules_block()
            if is_private_chat:
                # 私聊：不需要群聊标记，直接告知是私信
                msg_prefix = f"[私聊] {sender} 说:\n"
                msg_suffix = (f"\n\n{agent_identity}。\n"
                              f"{human_user_hint}\n\n"
                              "如需让用户看到你的回复，请使用 send private cli（底层等价于群消息发送）：\n"
                              f"{private_cli_hint}\n"
                              "[end padding]\n[end padding]\n[end padding]")
            elif mentions and global_id in mentions:
                msg_prefix = f"[群聊 {group_id} 成员数:{member_count}] {sender} @你 说:\n"
                msg_suffix = (f"\n\n⚠️ 这是专门 @你 的消息，你必须回复！{agent_identity}。\n"
                              f"{human_user_hint}\n\n"
                              "请先 cd 到项目目录，然后使用 CLI 工具发送消息到群里：\n"
                              f"{group_cli_hint}\n"
                              "[end padding]\n[end padding]\n[end padding]")
            else:
                msg_prefix = f"[群聊 {group_id} 成员数:{member_count}] {sender} 说:\n"
                msg_suffix = (
                    f"\n\n{agent_identity}。\n"
                    f"{human_user_hint}\n\n"
                    "如需回复，请先 cd 到项目目录，然后使用 CLI 工具发送消息到群里：\n"
                    f"{group_cli_hint}\n"
                    "[end padding]\n[end padding]\n[end padding]"
                )

            msg_text = msg_prefix + content + msg_suffix

            # Append attachment descriptions to text message
            if attachments:
                attach_desc = "\n".join(
                    f"  📎 {att.name} ({att.type}/{att.mime_type})" for att in attachments
                )
                msg_text = msg_prefix + content + f"\n\n[随消息附件]\n{attach_desc}" + msg_suffix

            # Handle different member types
            if member_type == "ext":
                # External agent: use ACP or HTTP
                agent_info = external_agents_map.get(global_id, {})
                if not agent_info:
                    tag_key = _canonical_external_platform(str(tag or ""))
                    if tag_key != "openclaw":
                        logger.info(
                            "Skip untracked external ACP agent %s (%s); not found in tracked external agents map",
                            short_name,
                            global_id,
                        )
                        continue
                    # Allow untracked public openclaw agents as a special case.
                    agent_info = {"global_name": global_id, "name": short_name, "tag": tag, "platform": "openclaw"}
                asyncio.create_task(
                    self._handle_external_agent_reply(
                        group_id, agent_info, msg_text, short_name,
                        attachments=attachments,
                        metadata={"is_private_chat": is_private_chat},
                    )
                )
            else:
                # Internal oasis agent: use HTTP trigger
                trigger_url = f"http://127.0.0.1:{os.getenv('PORT_AGENT', '51200')}/system_trigger"

                group_trigger_suffix = ("\n\n如果需要回复，请使用 send_to_group 工具发送消息到群里：\n"
                                        f"  当前群主 owner=\"{owner_uid}\"；当前人类用户是「{owner_uid}」\n"
                                        f"  send_to_group(group_id=\"{group_id}\", content=\"你的回复内容\")\n"
                                        "注意：username 和 source_session 会自动注入，不要手动设置。\n"
                                        "[end padding]\n[end padding]\n[end padding]")
                private_trigger_suffix = ("\n\n如果需要回复，请使用 send_private_cli 工具发送私聊消息：\n"
                                          f"  当前群主 owner=\"{owner_uid}\"；当前人类用户是「{owner_uid}」\n"
                                          f"  send_private_cli(group_id=\"{group_id}\", content=\"你的回复内容\")\n"
                                          "注意：username 和 source_session 会自动注入，不要手动设置。\n"
                                          "[end padding]\n[end padding]\n[end padding]")

                attach_hint = ""
                if attachments:
                    attach_desc = "\n".join(
                        f"  📎 {att.name} ({att.type}/{att.mime_type})" for att in attachments
                    )
                    attach_hint = f"\n\n[随消息附件]\n{attach_desc}"

                if is_private_chat:
                    trigger_msg = (f"[私聊] {sender} 说:\n{content}{attach_hint}\n\n"
                                   f"(你当前的身份/角色是「{short_name}」。)"
                                   f"{private_trigger_suffix}")
                elif mentions and global_id in mentions:
                    trigger_msg = (f"[群聊 {group_id} 成员数:{member_count}] {sender} @你 说:\n{content}{attach_hint}\n\n"
                                   f"(⚠️ 这是专门 @你 的消息，你必须回复！"
                                   f"你在群聊中的身份/角色是「{short_name}」，回复时请体现你的专业角色视角。)"
                                   f"{group_trigger_suffix}")
                else:
                    trigger_msg = (f"[群聊 {group_id} 成员数:{member_count}] {sender} 说:\n{content}{attach_hint}\n\n"
                                   f"(你在群聊中的身份/角色是「{short_name}」，回复时请体现你的专业角色视角。)"
                                   f"{group_trigger_suffix}")

                # 标记内部 agent 正在输入（thread lock 会在处理完成后自动释放，
                # get_typing_status 会检查 lock 状态来判断是否仍在输入）
                self.set_typing(group_id, short_name)
                try:
                    trigger_body: dict = {
                        "user_id": user_id_member,
                        "session_id": global_id,
                        "text": trigger_msg,
                    }
                    if attachments:
                        trigger_body["attachments"] = [
                            a.model_dump() for a in attachments
                        ]
                    async with httpx.AsyncClient(timeout=30) as client:
                        await client.post(
                            trigger_url,
                            headers={"X-Internal-Token": self.internal_token},
                            json=trigger_body,
                        )
                except Exception as e:
                    logger.warning("广播到 %s (global_id=%s) 失败: %s", short_name, global_id, e)
                    self.clear_typing(group_id, short_name)

    async def create_group(self, req: GroupCreateRequest, authorization: str | None):
        uid, _, _ = self.parse_group_auth(authorization)

        # Private chat (custom_name starts with "private_"): create empty group,
        # the caller will add the single target agent via POST /members afterwards.
        # Regular group chat: load all team members from config using team_name.
        is_private = req.custom_name and req.custom_name.startswith("private_")
        if is_private:
            members = []
        elif req.team_name:
            members = _load_team_members(uid, req.team_name)
        else:
            members = []

        # group_id = owner::segment（segment 保留中文语义，仅剔除 : / \ 与控制符）
        name_safe = _group_id_name_segment(req.name)
        group_id = f"{uid}::{name_safe}"

        if await group_exists(self.group_db_path, group_id):
            return {"group_id": group_id, "name": req.name, "owner": uid, "exists": True}
        now = time.time()
        await create_group_with_members(
            self.group_db_path,
            group_id=group_id,
            name=req.name,
            owner=uid,
            created_at=now,
            members=members,
        )
        return {"group_id": group_id, "name": req.name, "owner": uid, "member_count": len(members)}

    async def list_groups(self, authorization: str | None):
        uid, _, _ = self.parse_group_auth(authorization)
        return await list_groups_for_user(self.group_db_path, uid)

    async def get_group(self, group_id: str, authorization: str | None):
        self.parse_group_auth(authorization)
        group = await get_group(self.group_db_path, group_id)
        if not group:
            raise HTTPException(status_code=404, detail="群聊不存在")

        members = await list_group_members(self.group_db_path, group_id)
        # title 直接用数据库里的 short_name，不需要再查 json
        for member in members:
            if member.get("is_agent"):
                member["title"] = member.get("short_name") or member.get("global_id") or "未命名"
            else:
                member["title"] = member.get("user_id") or "群主"

        messages = await list_recent_group_messages(self.group_db_path, group_id, limit=100)
        return {**group, "members": members, "messages": messages}

    async def get_group_messages(self, group_id: str, after_id: int, authorization: str | None):
        self.parse_group_auth(authorization)
        messages = await list_group_messages_after(self.group_db_path, group_id, after_id, limit=200)
        return {"messages": messages}

    async def post_group_message(
        self,
        group_id: str,
        req: GroupMessageRequest,
        authorization: str | None,
        x_internal_token: str | None,
    ):
        sender = ""
        sender_display = req.sender_display or ""

        if x_internal_token and x_internal_token == self.internal_token:
            sender = req.sender or "agent"
            uid = "agent"

            # 自动补全 sender_display：
            # MCP tool 传入的 sender 格式为 "username#session_id"，sender_display 为 "#session_id"
            # CLI 传入的 sender 已经是完整的 "tag#type#short_name#global_id" 格式
            # 判断：sender_display 段数 < 3 说明不完整，需要根据 global_id 查成员信息补全
            sd_parts = sender_display.split("#") if sender_display else []
            if len(sd_parts) < 3 and sender and "#" in sender:
                _parts = sender.split("#", 1)
                source_global_id = _parts[1] if len(_parts) > 1 else ""
                if source_global_id:
                    member_info = await get_group_member_by_global_id(
                        self.group_db_path, group_id, source_global_id
                    )
                    if member_info:
                        tag = member_info.get("tag") or ""
                        mtype = member_info.get("member_type") or "oasis"
                        sname = member_info.get("short_name") or ""
                        gid = member_info.get("global_id") or ""
                        sender_display = f"{tag}#{mtype}#{sname}#{gid}" if tag else f"#{mtype}#{sname}#{gid}"
                        sender = sender_display
        else:
            uid, _, sid = self.parse_group_auth(authorization)
            sender = req.sender or uid
            # 人类发消息 sender_display 为空（前端用这个判断是否 agent）

        now = time.time()
        if not await group_exists(self.group_db_path, group_id):
            raise HTTPException(status_code=404, detail="群聊不存在")

        # Resolve real owner user_id for loading external agent configs
        broadcast_uid = uid
        if uid == "agent":
            owner = await get_group_owner(self.group_db_path, group_id)
            broadcast_uid = owner or ""

        # Serialize attachments for DB storage
        attachments_json = "[]"
        if req.attachments:
            attachments_json = json.dumps([a.model_dump() for a in req.attachments])

        msg_id = await insert_group_message(
            self.group_db_path,
            group_id=group_id,
            sender=sender,
            sender_display=sender_display,
            content=req.content,
            attachments=attachments_json,
            timestamp=now,
        )

        # Agent 发消息后清除其"正在输入"状态
        if sender_display:
            self.clear_typing_by_sender_display(group_id, sender_display)

        # ── Auto-resolve @mentions from message content ──
        # Instead of regex-parsing @name tokens (which breaks on spaces /
        # special chars), we fetch the member list first and then check
        # whether "@<short_name>" appears anywhere in the content.
        # This works for all channels (frontend, CLI, MCP send_to_group).
        resolved_mentions = list(req.mentions) if req.mentions else []
        if "@" in req.content:
            members = await list_group_members(self.group_db_path, group_id)
            content_lower = req.content.lower()
            # Build list sorted by name length desc so longer names match first
            # (e.g. "@Code Reviewer" before "@Code")
            # Include ALL members (agents + humans) so that @human also
            # populates mentions, preventing broadcast to unrelated agents.
            name_gid_pairs: list[tuple[str, str]] = []
            for m in members:
                sname = (m.get("short_name") or "").strip()
                gid = m.get("global_id") or ""
                if sname and gid:
                    name_gid_pairs.append((sname, gid))
            name_gid_pairs.sort(key=lambda x: len(x[0]), reverse=True)
            for sname, gid in name_gid_pairs:
                if f"@{sname.lower()}" in content_lower and gid not in resolved_mentions:
                    resolved_mentions.append(gid)
        final_mentions = resolved_mentions if resolved_mentions else None

        # 用 sender_display (tag#type#short_name) 排除发送者自己
        # 不用 global_id 排除，因为不同 agent 平台的 global_id 可能冲突
        exclude_display = sender_display or ""
        asyncio.create_task(
            self.broadcast_to_group(
                group_id,
                sender_display or sender,
                req.content,
                exclude_sender_display=exclude_display,
                mentions=final_mentions,
                user_id=broadcast_uid,
                attachments=req.attachments,
            )
        )

        return {"status": "sent", "sender": sender, "sender_display": sender_display, "timestamp": now, "id": msg_id}

    async def update_group(self, group_id: str, req: GroupUpdateRequest, authorization: str | None):
        uid, _, _ = self.parse_group_auth(authorization)
        owner = await get_group_owner(self.group_db_path, group_id)
        if not owner:
            raise HTTPException(status_code=404, detail="群聊不存在")
        if owner != uid:
            raise HTTPException(status_code=403, detail="只有群主可以修改群设置")

        if req.name:
            await update_group_name(self.group_db_path, group_id, req.name)

        return {"status": "updated"}

    async def delete_group(self, group_id: str, authorization: str | None):
        uid, _, _ = self.parse_group_auth(authorization)
        owner = await get_group_owner(self.group_db_path, group_id)
        if not owner:
            raise HTTPException(status_code=404, detail="群聊不存在")
        if owner != uid:
            raise HTTPException(status_code=403, detail="只有群主可以删除群")
        await delete_group_records(self.group_db_path, group_id)
        return {"status": "deleted"}

    async def sync_group_members(self, group_id: str, authorization: str | None, team_name: str = ""):
        """Sync group members from team configuration.

        This will:
        1. Clear all existing non-owner members
        2. Reload members from team config (internal_agents.json + external_agents.json)
        3. Add the reloaded members to the group

        team_name must be provided by the caller (no longer stored in DB).
        """
        uid, _, _ = self.parse_group_auth(authorization)
        owner = await get_group_owner(self.group_db_path, group_id)
        if not owner:
            raise HTTPException(status_code=404, detail="群聊不存在")
        if owner != uid:
            raise HTTPException(status_code=403, detail="只有群主可以同步群成员")

        if not team_name:
            raise HTTPException(status_code=400, detail="需要提供 team_name")

        # Load current members from team config
        new_members = _load_team_members(uid, team_name)

        # Clear existing non-owner members
        await clear_group_members(self.group_db_path, group_id=group_id, keep_owners=True)

        # Add new members
        now = time.time()
        added_count = 0
        for m in new_members:
            m_global = m.get("global_id", "")
            m_short = m.get("short_name", "")
            m_uid = m.get("user_id", "")
            m_type = m.get("member_type", "oasis")
            m_tag = m.get("tag", "")
            if m_global:
                await add_group_member(
                    self.group_db_path,
                    group_id=group_id,
                    user_id=m_uid,
                    short_name=m_short,
                    global_id=m_global,
                    member_type=m_type,
                    tag=m_tag,
                    joined_at=now,
                )
                added_count += 1

        return {
            "status": "synced",
            "group_id": group_id,
            "added_members": added_count,
        }

    async def mute_group(self, group_id: str, authorization: str | None):
        self.parse_group_auth(authorization)
        self.group_muted.add(group_id)
        return {"status": "muted", "group_id": group_id}

    async def unmute_group(self, group_id: str, authorization: str | None):
        self.parse_group_auth(authorization)
        self.group_muted.discard(group_id)
        return {"status": "unmuted", "group_id": group_id}

    async def group_mute_status(self, group_id: str, authorization: str | None):
        self.parse_group_auth(authorization)
        return {"muted": group_id in self.group_muted}

    async def list_available_sessions(self, group_id: str, authorization: str | None):
        uid, _, _ = self.parse_group_auth(authorization)
        prefix = f"{uid}#"
        sessions = []
        try:
            rows = await list_thread_ids_by_prefix(self.checkpoint_db_path, prefix)

            for thread_id in rows:
                sid = thread_id[len(prefix):]
                config = {"configurable": {"thread_id": thread_id}}
                snapshot = await self.agent.agent_app.aget_state(config)
                msgs = snapshot.values.get("messages", []) if snapshot and snapshot.values else []
                first_human = first_human_title(
                    msgs,
                    skip_prefixes=("[系统触发]", "[外部学术会议邀请]"),
                    title_len=80,
                    list_fallback="(图片消息)",
                    default="",
                )

                sessions.append({
                    "session_id": sid,
                    "title": first_human or f"Session {sid}",
                })
        except Exception as e:
            return {"sessions": [], "error": str(e)}

        return {"sessions": sessions}

    async def add_single_member(self, group_id: str, req: GroupAddMemberRequest, authorization: str | None):
        """向群聊中添加单个 agent 成员。"""
        uid, _, _ = self.parse_group_auth(authorization)
        owner = await get_group_owner(self.group_db_path, group_id)
        if not owner:
            raise HTTPException(status_code=404, detail="群聊不存在")
        if owner != uid:
            raise HTTPException(status_code=403, detail="只有群主可以添加成员")

        # 判断 user_id：内部 agent 的 user_id 是群主 uid，外部 agent 的 user_id 是 "ext"
        m_uid = "ext" if req.member_type == "ext" else uid

        await add_group_member(
            self.group_db_path,
            group_id=group_id,
            user_id=m_uid,
            short_name=req.short_name,
            global_id=req.global_id,
            member_type=req.member_type,
            tag=req.tag,
            joined_at=time.time(),
        )
        return {"status": "added", "global_id": req.global_id, "short_name": req.short_name}

    async def remove_single_member(self, group_id: str, global_id: str, authorization: str | None):
        """从群聊中移除单个 agent 成员（通过 global_id）。"""
        uid, _, _ = self.parse_group_auth(authorization)
        owner = await get_group_owner(self.group_db_path, group_id)
        if not owner:
            raise HTTPException(status_code=404, detail="群聊不存在")
        if owner != uid:
            raise HTTPException(status_code=403, detail="只有群主可以移除成员")

        await remove_group_member(
            self.group_db_path,
            group_id=group_id,
            global_id=global_id,
        )
        return {"status": "removed", "global_id": global_id}
