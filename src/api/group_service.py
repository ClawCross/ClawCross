import asyncio
import hashlib
import json
import os
import re
import shlex
import time
from typing import Any, Callable

from fastapi import HTTPException

# Permission modes live with the agent layer; CLI / PC web / mobile group chat
# all send the same names.
from agents.messages import normalize_run_mode as _normalize_run_mode
from comms.delivery import WakeRequest, mentions_everyone, render_digest, select_wake_targets, StormGuard
from comms.principals import human_principal, member_agent, member_principal
from api.external_agent_registry import build_external_agents_map_for_owner
from utils.auth_utils import extract_user_password_session, is_internal_bearer, parse_bearer_parts
from utils.checkpoint_repository import list_thread_ids_by_prefix
from utils.runtime_paths import USER_FILES_DIR
from api.group_repository import (
    add_group_member,
    advance_member_read_cursor,
    clear_group_members,
    create_group_with_members,
    delete_group as delete_group_records,
    get_group,
    get_group_member_by_global_id,
    get_group_mute_state,
    get_group_owner,
    get_member_read_cursor,
    get_group_primary_agent,
    group_exists,
    init_group_db as init_group_db_repo,
    insert_group_message,
    list_group_mute_states,
    list_group_member_targets,
    list_group_members,
    list_group_messages_after,
    list_group_messages_between,
    list_groups_for_user,
    list_recent_group_messages,
    remove_group_member,
    set_group_mute_state,
    set_group_primary_agent,
    set_group_team,
    update_group_name,
)
from api.group_models import (
    Attachment,
    GroupAddMemberRequest,
    GroupCreateRequest,
    GroupMessageRequest,
    GroupMuteAllRequest,
    GroupMuteMemberRequest,
    GroupSetPrimaryRequest,
    GroupUpdateRequest,
)
from utils.logging_utils import get_logger
from utils.session_summary import first_human_title

logger = get_logger("group_service")

# Messages a woken member may have missed, shown as a digest (most recent).
_DIGEST_LIMIT = 15

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


def _canonical_external_platform(platform: str) -> str:
    pl = (platform or "").strip().lower()
    if pl in ("claude-code", "claudecode"):
        return "claude"
    if pl in ("gemini-cli", "geminicli"):
        return "gemini"
    return pl


def _team_view():
    """The team view over this module's user-files tree (tests repoint USER_FILES_DIR)."""
    from teams.view import get_team_view

    return get_team_view(USER_FILES_DIR)


def _load_team_internal_agents(user_id: str, team: str) -> list[dict]:
    """Internal members of a team, as group members.

    Returns list of {"user_id": user_id, "global_id": session, "short_name": name, "member_type": "oasis", "tag": ...}.
    Roles written without a session get one from the team view, so they join too.
    """
    if not user_id or not team:
        return []
    return [
        {
            "user_id": user_id,
            "global_id": entry.get("session", ""),
            "short_name": entry.get("name", ""),
            "member_type": "oasis",
            "tag": entry.get("tag", ""),
            "is_primary": bool(entry.get("is_primary")),
        }
        for entry in _team_view().entries(user_id, team, "internal")
        if entry.get("session")
    ]


def _external_member(entry: dict, *, owner_user_id: str = "", team: str = "") -> dict:
    """An external_agents.json entry in group-member shape."""
    ext_config = entry.get("config") or entry.get("meta") or {}
    if not isinstance(ext_config, dict):
        ext_config = {}
    name = entry.get("name", "")
    global_name = entry.get("global_name", "")
    return {
        "user_id": "ext",
        "owner_user_id": owner_user_id,
        "global_id": global_name,
        "short_name": name,
        "member_type": "ext",
        "tag": entry.get("tag", ""),
        "global_name": global_name,
        "name": name,
        "team": team,
        "platform": _canonical_external_platform(str(entry.get("platform", "") or "")),
        "api_url": ext_config.get("api_url", ""),
        "api_key": ext_config.get("api_key", ""),
        "model": ext_config.get("model", ""),
        "meta": ext_config,
        "is_primary": bool(entry.get("is_primary")),
    }


def _load_public_external_agents(user_id: str) -> list[dict]:
    """External agents in the user's own (non-team) scope."""
    if not user_id:
        return []
    return [
        _external_member(entry, owner_user_id=user_id)
        for entry in _team_view().entries(user_id, "", "external")
    ]


def _load_team_external_agents(user_id: str, team: str) -> list[dict]:
    """External members of a team, as group members."""
    if not user_id or not team:
        return []
    return [
        _external_member(entry, owner_user_id=user_id, team=team)
        for entry in _team_view().entries(user_id, team, "external")
    ]


def _load_team_members(user_id: str, team: str) -> list[dict]:
    """All team members (internal + external) in group-member shape."""
    return _load_team_internal_agents(user_id, team) + _load_team_external_agents(user_id, team)


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


_ASCII_WORD_CHAR = re.compile(r"[A-Za-z0-9_\-]")


def resolve_text_mentions(content: str, members: list[tuple[str, str]]) -> list[str]:
    """Return the global ids of members written as ``@name`` in *content*.

    *members* is ``(short_name, global_id)`` pairs. Longer names are matched
    first and claim their text, so ``@Code Reviewer`` does not also mention a
    member called ``Code``. A name ending in an ASCII word character must be
    followed by a non-word character, so ``@Codex`` does not mention ``Code``;
    names ending in CJK text still match without a separator, as before. An
    ``@`` right after an ASCII word character (``a@Code.io``) is not a mention.
    """
    lowered = (content or "").lower()
    claimed = [False] * len(lowered)
    found: list[str] = []
    for name, gid in sorted(members, key=lambda item: len(item[0]), reverse=True):
        needle = "@" + name.lower()
        start = 0
        while True:
            idx = lowered.find(needle, start)
            if idx < 0:
                break
            start = idx + 1
            end = idx + len(needle)
            if any(claimed[idx:end]):
                continue
            if idx > 0 and _ASCII_WORD_CHAR.match(lowered[idx - 1]):
                continue
            if (
                _ASCII_WORD_CHAR.match(needle[-1])
                and end < len(lowered)
                and _ASCII_WORD_CHAR.match(lowered[end])
            ):
                continue
            claimed[idx:end] = [True] * (end - idx)
            if gid not in found:
                found.append(gid)
    return found


def _cli_hint(action: str, *, owner: str, group_id: str, sender_display: str, agent: str = "") -> str:
    """Shell command an external agent runs to post into a group or private chat.

    A registered agent names itself by address (``--agent``); an undeclared one
    falls back to its ``tag#type#short_name#global_id`` sender display.
    """
    who = f"--agent {shlex.quote(agent)}" if agent else f"--sender {shlex.quote(sender_display)}"
    return (
        f"cd {shlex.quote(_PROJECT_ROOT)} && uv run scripts/cli.py -u {shlex.quote(owner)} "
        f"groups {action} --group-id {shlex.quote(group_id)} "
        f"{who} --message '你的回复内容'"
    )


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
        gateway: Any = None,
    ):
        self.internal_token = internal_token
        self.verify_password = verify_password
        self.checkpoint_db_path = checkpoint_db_path
        self.group_db_path = group_db_path
        self.agent = agent
        self._gateway = gateway  # L1 AgentGateway; built on first use
        self._storm_guard = StormGuard()
        self._team_sync_state: dict[str, tuple] = {}
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
        uid, _, _ = self.parse_group_auth(authorization)
        await self._require_group_access(group_id, uid)
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

    async def _require_group_access(self, group_id: str, uid: str, *, owner_only: bool = False) -> str:
        """Return the group owner if *uid* may act on the group, else raise.

        The owner always may; other human members may unless *owner_only*.
        ``system`` is an internal caller with no user context (no Flask session),
        which only a local process holding the internal token can present.
        """
        owner = await get_group_owner(self.group_db_path, group_id)
        if not owner:
            raise HTTPException(status_code=404, detail="群聊不存在")
        if uid in (owner, "system"):
            return owner
        if not owner_only:
            member = await get_group_member_by_global_id(self.group_db_path, group_id, uid)
            if member and not bool(member.get("is_agent")):
                return owner
        raise HTTPException(status_code=403, detail="无权访问该群聊")

    async def _sender_display_for_agent(self, group_id: str, owner: str, ref: str) -> str:
        """The ``tag#type#short_name#global_id`` of the member agent *ref* names (CLI --agent)."""
        from agents.registry import AgentNotFound, AmbiguousAgentRef

        try:
            record = self._registry().resolve(owner, ref)
        except (AgentNotFound, AmbiguousAgentRef) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        global_id = str(record.binding.get("session") or record.binding.get("global_name") or "")
        member = await get_group_member_by_global_id(self.group_db_path, group_id, global_id)
        if not member or not bool(member.get("is_agent")):
            raise HTTPException(status_code=403, detail=f"{record.address} 不是本群成员")
        tag, mtype = member.get("tag") or "", member.get("member_type") or "oasis"
        return f"{tag}#{mtype}#{member.get('short_name') or ''}#{global_id}"

    async def _agent_member_for_sender(self, group_id: str, sender_display: str) -> dict | None:
        """Return the agent member a full ``tag#type#short_name#global_id`` names."""
        parts = (sender_display or "").split("#")
        if len(parts) < 4 or not parts[-1].strip():
            return None
        member = await get_group_member_by_global_id(self.group_db_path, group_id, parts[-1].strip())
        return member if member and bool(member.get("is_agent")) else None

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

    # ── delivery (through the L1 gateway) ────────────────────────────────

    def _registry(self):
        from agents.registry import get_registry

        return get_registry(USER_FILES_DIR)

    def _agent_gateway(self):
        if self._gateway is None:
            from agents.gateway import AgentGateway

            self._gateway = AgentGateway(self._registry(), internal_token=self.internal_token)
        return self._gateway

    async def _is_do_not_disturb(self, group_id: str) -> bool:
        """免打扰: messages are kept but no agent is woken."""
        return await get_group_mute_state(
            self.group_db_path, group_id=group_id, target_type="dnd", target_id="*",
        )

    async def _digest_for(self, group_id: str, global_id: str, message_id: int) -> str:
        """What this member missed since it was last woken (or last spoke)."""
        if not message_id:
            return ""
        cursor = await get_member_read_cursor(self.group_db_path, group_id, global_id)
        missed = await list_group_messages_between(
            self.group_db_path, group_id, after_id=cursor, before_id=message_id, limit=_DIGEST_LIMIT,
        )
        own_suffix = f"#{global_id}"
        return render_digest([m for m in missed if not str(m.get("sender_display") or "").endswith(own_suffix)])

    async def _deliver_to_agent(
        self,
        group_id: str,
        owner: str,
        record,
        short_name: str,
        text: str,
        *,
        instructions: str = "",
        attachments: list[Attachment] | None = None,
        mode: str | None = None,
    ) -> None:
        """Wake one agent member. Typing shows until an external send ends; a WeBot
        member's typing follows its thread lock (see get_typing_status)."""
        from agents.messages import AgentMessage

        self.set_typing(group_id, short_name)
        msg = AgentMessage(
            text=text,
            attachments=[a.model_dump() for a in attachments or []],
            instructions=instructions,
        )

        def settled(reply) -> None:
            self.clear_typing(group_id, short_name)
            if reply.ok and reply.content:
                # The direct reply is not posted: agents speak through groups send.
                logger.info("agent %s replied directly (not auto-posting): %s", short_name, reply.content[:200])

        try:
            receipt = await self._agent_gateway().deliver(
                owner,
                record,
                msg,
                context={"conversation_id": group_id},
                mode=mode,
                coalesce_key=f"group:{group_id}:agent:{record.binding.get('session') or record.handle}",
                on_complete=settled,
            )
        except Exception:
            logger.exception("delivery to %s in %s crashed", short_name, group_id)
            self.clear_typing(group_id, short_name)
            return
        if not receipt.accepted:
            logger.warning("delivery to %s in %s failed: %s", short_name, group_id, receipt.error)
            self.clear_typing(group_id, short_name)

    async def broadcast_to_group(
        self,
        group_id: str,
        sender: str,
        content: str,
        exclude_sender_display: str = "",
        mentions: list[str] | None = None,
        user_id: str = "",
        attachments: list[Attachment] | None = None,
        run_mode: str | None = None,
        message_id: int = 0,
        mention_all: bool = False,
    ):
        """Wake the agent members a message is for (fire-and-forget).

        Every member can read the message; ``comms.delivery.select_wake_targets``
        decides who is woken: a human wakes whom they @ (else the primary agent,
        else everyone); an agent wakes only whom it @ (a non-primary agent only
        the primary); ``@所有人`` from a human or the primary wakes everyone.
        Woken members get a digest of what they missed since last time.
        exclude_sender_display (``tag#type#short_name#global_id``) names the sender.
        run_mode: permission override for each woken agent (chat / readonly / bypass / auto).
        """
        normalized_mode = _normalize_run_mode(run_mode)
        if await self._is_do_not_disturb(group_id):
            logger.info("群 %s 免打扰，跳过唤醒", group_id)
            return
        # The group owner, not the caller: a CLI reply arrives as whatever -u the
        # agent typed, which would load the wrong user's agents.
        owner_uid = await get_group_owner(self.group_db_path, group_id) or user_id or ""
        await self._sync_team_group(group_id, owner_uid)
        members = await list_group_member_targets(self.group_db_path, group_id)
        member_count = len(members)
        group = await get_group(self.group_db_path, group_id) or {}
        is_private_chat = group.get("kind") == "direct" or member_count <= 2  # owner + 1 agent = 私聊
        human_user_hint = (
            f"当前群主 owner=\"{owner_uid}\"。当前人类用户是「{owner_uid}」。"
        )

        primary_agent_gid = await get_group_primary_agent(self.group_db_path, group_id)
        agent_gids = [gid for _u, gid, is_agent, *_rest in members if is_agent]
        if primary_agent_gid and primary_agent_gid not in agent_gids:
            # A primary that left the group would swallow every message.
            primary_agent_gid = None
        sender_gid = ""
        ex_parts = exclude_sender_display.split("#") if exclude_sender_display else []
        if len(ex_parts) >= 4 and ex_parts[-1].strip() in agent_gids:
            sender_gid = ex_parts[-1].strip()

        targets = select_wake_targets(WakeRequest(
            agent_ids=agent_gids,
            sender_id=sender_gid,
            mentions=list(mentions or []),
            mention_all=mention_all,
            primary_id=primary_agent_gid,
            direct=is_private_chat,
        ))
        if not sender_gid:
            self._storm_guard.human_spoke(group_id)
        elif targets and not self._storm_guard.allow(group_id, len(targets)):
            logger.warning(
                "群 %s agent 间唤醒过于频繁，已暂停唤醒，等待人类发言（本次目标 %s）", group_id, targets,
            )
            return

        primary_short_name = ""
        sub_agent_short_names: list[str] = []
        if primary_agent_gid:
            for _uid, _gid, _is_agent, _mtype, _sname, _tag in members:
                if not _is_agent:
                    continue
                if _gid == primary_agent_gid:
                    primary_short_name = _sname or _gid
                else:
                    sub_agent_short_names.append(_sname or _gid)

        registry = self._registry()
        attach_hint = ""
        if attachments:
            attach_desc = "\n".join(f"  📎 {att.name} ({att.type}/{att.mime_type})" for att in attachments)
            attach_hint = f"\n\n[随消息附件]\n{attach_desc}"
        mention_hint = (
            "你的发言会进入群聊记录，但只会唤醒你 @ 的成员"
            + ("（sub-agent 的发言只送达主 agent）" if primary_agent_gid else "")
            + "：需要谁回应就在回复内容里直接写 @对方名称，不 @ 就不会有人被唤醒；"
            "@所有人 只有群主和主 agent 可用。不要写内部 global_id、session_id 或 tag#type#... 标识。"
        )

        for user_id_member, global_id, is_agent, member_type, short_name, tag in members:
            if not is_agent or global_id not in targets:
                continue
            member = {
                "user_id": user_id_member, "global_id": global_id, "is_agent": is_agent,
                "member_type": member_type, "short_name": short_name, "tag": tag,
            }
            record = member_agent(registry, owner_uid, member)
            if record is None:
                logger.info("Skip untracked external agent %s (%s); not declared in any external_agents.json",
                            short_name, global_id)
                continue
            mentioned = bool(mentions) and global_id in (mentions or [])
            digest = await self._digest_for(group_id, global_id, message_id)

            role_hint = ""
            if primary_agent_gid and not is_private_chat:
                if global_id == primary_agent_gid:
                    sub_list = "、".join(f"「{n}」" for n in sub_agent_short_names) or "（暂无）"
                    role_hint = (
                        f"\n你是本群【主 agent】，其他 agent 都是你的 sub-agent：{sub_list}。"
                        "sub-agent 的所有发言（含 @）都只会送达你，由你统筹后再回复用户与群聊。\n"
                    )
                else:
                    role_hint = (
                        f"\n你是本群【sub-agent】，主 agent 是「{primary_short_name or primary_agent_gid}」。"
                        "你的所有发言（含 @ 任何人）都只会送达主 agent，不会扩散给群里其他成员；"
                        "请把汇报/请示当成主要交互模式。\n"
                    )

            if member_type == "ext":
                sender_display = f"{tag}#{member_type}#{short_name}#{global_id}" if tag else f"#{member_type}#{short_name}#{global_id}"
                agent_ref = record.address if record.agent_id else ""
                group_cli_hint = _cli_hint("send", owner=owner_uid, group_id=group_id,
                                           sender_display=sender_display, agent=agent_ref)
                private_cli_hint = _cli_hint("private-send", owner=owner_uid, group_id=group_id,
                                             sender_display=sender_display, agent=agent_ref)
                agent_identity = f"你是「{short_name}」"
                if is_private_chat:
                    msg_prefix = f"[私聊] {sender} 说:\n"
                    msg_suffix = (f"\n\n{agent_identity}。\n"
                                  f"{human_user_hint}\n\n"
                                  "如需让用户看到你的回复，请使用 send private cli（底层等价于群消息发送）：\n"
                                  f"{private_cli_hint}\n"
                                  "[end padding]\n[end padding]\n[end padding]")
                elif mentioned:
                    msg_prefix = f"[群聊 {group_id} 成员数:{member_count}] {sender} @你 说:\n"
                    msg_suffix = (f"\n\n⚠️ 这是专门 @你 的消息，你必须回复！{agent_identity}。\n"
                                  f"{role_hint}"
                                  f"{human_user_hint}\n\n"
                                  f"{mention_hint}\n"
                                  "请先 cd 到项目目录，然后使用 CLI 工具发送消息到群里：\n"
                                  f"{group_cli_hint}\n"
                                  "[end padding]\n[end padding]\n[end padding]")
                else:
                    msg_prefix = f"[群聊 {group_id} 成员数:{member_count}] {sender} 说:\n"
                    msg_suffix = (f"\n\n{agent_identity}。\n"
                                  f"{role_hint}"
                                  f"{human_user_hint}\n\n"
                                  f"{mention_hint}\n"
                                  "如需回复，请先 cd 到项目目录，然后使用 CLI 工具发送消息到群里：\n"
                                  f"{group_cli_hint}\n"
                                  "[end padding]\n[end padding]\n[end padding]")
                text = digest + msg_prefix + content + attach_hint + msg_suffix
                # ACP sessions get the rules for this kind of chat; an HTTP session is
                # shared by private and group chats, so it carries both, unchanged.
                if record.driver in ("openclaw", "http"):
                    instructions = "\n\n".join([_external_agent_group_rules_block(), _external_agent_private_rules_block()])
                elif is_private_chat:
                    instructions = _external_agent_private_rules_block()
                else:
                    instructions = _external_agent_group_rules_block()
            else:
                group_trigger_suffix = ("\n\n如果需要回复，请使用 send_to_group 工具发送消息到群里：\n"
                                        f"  当前群主 owner=\"{owner_uid}\"；当前人类用户是「{owner_uid}」\n"
                                        f"  send_to_group(group_id=\"{group_id}\", content=\"你的回复内容\")\n"
                                        f"  {mention_hint}\n"
                                        "注意：username 和 source_session 会自动注入，不要手动设置。\n"
                                        "[end padding]\n[end padding]\n[end padding]")
                private_trigger_suffix = ("\n\n如果需要回复，请使用 send_to_group 工具发送私聊消息：\n"
                                          f"  当前群主 owner=\"{owner_uid}\"；当前人类用户是「{owner_uid}」\n"
                                          f"  send_to_group(group_id=\"{group_id}\", content=\"你的回复内容\")\n"
                                          "注意：username 和 source_session 会自动注入，不要手动设置。\n"
                                          "[end padding]\n[end padding]\n[end padding]")
                if is_private_chat:
                    text = (f"[私聊] {sender} 说:\n{content}{attach_hint}\n\n"
                            f"(你当前的身份/角色是「{short_name}」。)"
                            f"{private_trigger_suffix}")
                elif mentioned:
                    text = (f"[群聊 {group_id} 成员数:{member_count}] {sender} @你 说:\n{content}{attach_hint}\n\n"
                            f"(⚠️ 这是专门 @你 的消息，你必须回复！"
                            f"你在群聊中的身份/角色是「{short_name}」，回复时请体现你的专业角色视角。)"
                            f"{role_hint}{group_trigger_suffix}")
                else:
                    text = (f"[群聊 {group_id} 成员数:{member_count}] {sender} 说:\n{content}{attach_hint}\n\n"
                            f"(你在群聊中的身份/角色是「{short_name}」，回复时请体现你的专业角色视角。)"
                            f"{role_hint}{group_trigger_suffix}")
                text = digest + text
                instructions = ""

            await self._deliver_to_agent(
                group_id, owner_uid, record, short_name, text,
                instructions=instructions, attachments=attachments, mode=normalized_mode,
            )
            if message_id:
                await advance_member_read_cursor(self.group_db_path, group_id, global_id, message_id)

    # ── team groups ───────────────────────────────────────────────────────

    def _team_fingerprint(self, owner: str, team: str) -> tuple:
        base = os.path.join(str(USER_FILES_DIR), owner, "teams", team)
        stamp = []
        for name in ("internal_agents.json", "external_agents.json"):
            try:
                st = os.stat(os.path.join(base, name))
                stamp.append((name, st.st_mtime_ns, st.st_size))
            except OSError:
                stamp.append((name, None, None))
        return tuple(stamp)

    async def _sync_team_group(self, group_id: str, owner: str) -> None:
        """Keep a team group's agent members and primary in step with the team."""
        group = await get_group(self.group_db_path, group_id)
        team = str((group or {}).get("team") or "")
        if not team or not owner:
            return
        fingerprint = self._team_fingerprint(owner, team)
        if self._team_sync_state.get(group_id) == fingerprint:
            return
        desired = {m["global_id"]: m for m in _load_team_members(owner, team) if m.get("global_id")}
        current = {m["global_id"]: m for m in await list_group_members(self.group_db_path, group_id) if m.get("is_agent")}
        now = time.time()
        for gid, m in desired.items():
            if gid not in current:
                await add_group_member(
                    self.group_db_path, group_id=group_id, user_id=m.get("user_id", ""),
                    short_name=m.get("short_name", ""), global_id=gid,
                    member_type=m.get("member_type", "oasis"), tag=m.get("tag", ""), joined_at=now,
                )
        for gid in current:
            if gid not in desired:
                await remove_group_member(self.group_db_path, group_id=group_id, global_id=gid)
        lead = next((gid for gid, m in desired.items() if m.get("is_primary")), None)
        primary = await get_group_primary_agent(self.group_db_path, group_id)
        if lead and primary != lead:
            await set_group_primary_agent(self.group_db_path, group_id=group_id, global_id=lead)
        elif primary and primary not in desired:
            await set_group_primary_agent(self.group_db_path, group_id=group_id, global_id=None)
        self._team_sync_state[group_id] = fingerprint

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
        # Auto-pick primary agent: first member with is_primary=true from team config.
        primary_gid = next(
            (m.get("global_id") for m in members if m.get("is_primary") and m.get("global_id")),
            None,
        )
        if primary_gid:
            await set_group_primary_agent(
                self.group_db_path,
                group_id=group_id,
                global_id=primary_gid,
            )
        if is_private:
            await set_group_team(self.group_db_path, group_id=group_id, team="", kind="direct")
        elif req.team_name:
            # A team group mirrors its team: members and primary follow the manifest.
            await set_group_team(self.group_db_path, group_id=group_id, team=req.team_name)
            self._team_sync_state[group_id] = self._team_fingerprint(uid, req.team_name)
        return {
            "group_id": group_id,
            "name": req.name,
            "owner": uid,
            "member_count": len(members),
            "primary_agent_global_id": primary_gid,
        }

    async def list_groups(self, authorization: str | None):
        uid, _, _ = self.parse_group_auth(authorization)
        return await list_groups_for_user(self.group_db_path, uid)

    async def get_group(self, group_id: str, authorization: str | None):
        uid, _, _ = self.parse_group_auth(authorization)
        owner = await self._require_group_access(group_id, uid)
        await self._sync_team_group(group_id, owner)
        group = await get_group(self.group_db_path, group_id)
        if not group:
            raise HTTPException(status_code=404, detail="群聊不存在")

        members = await list_group_members(self.group_db_path, group_id)
        mute_states = await list_group_mute_states(self.group_db_path, group_id=group_id)
        muted_member_ids = {
            str(item.get("target_id") or "")
            for item in mute_states
            if str(item.get("target_type") or "") == "member" and bool(item.get("muted"))
        }
        mute_all_agents = any(
            str(item.get("target_type") or "") == "all_agents"
            and str(item.get("target_id") or "") == "*"
            and bool(item.get("muted"))
            for item in mute_states
        )
        external_agents_map = build_external_agents_map_for_owner(str(group.get("owner") or ""))
        # title 直接用数据库里的 short_name，不需要再查 json
        for member in members:
            member["muted"] = bool(member.get("is_agent")) and str(member.get("global_id") or "") in muted_member_ids
            if member.get("is_agent"):
                member["title"] = member.get("short_name") or member.get("global_id") or "未命名"
                if (member.get("member_type") or "").strip() == "ext":
                    ext_info = external_agents_map.get(str(member.get("global_id") or "").strip()) or {}
                    meta = ext_info.get("meta") if isinstance(ext_info.get("meta"), dict) else {}
                    member["platform"] = ext_info.get("platform", "") or member.get("tag", "")
                    member["model"] = ext_info.get("model", "") or meta.get("model", "")
                    member["meta"] = meta
            else:
                member["title"] = member.get("user_id") or "群主"

        messages = await list_recent_group_messages(self.group_db_path, group_id, limit=100)
        return {**group, "members": members, "messages": messages, "mute_all_agents": mute_all_agents}

    async def get_group_messages(self, group_id: str, after_id: int, authorization: str | None):
        uid, _, _ = self.parse_group_auth(authorization)
        await self._require_group_access(group_id, uid)
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
        owner = await get_group_owner(self.group_db_path, group_id)
        if not owner:
            raise HTTPException(status_code=404, detail="群聊不存在")

        if x_internal_token and x_internal_token == self.internal_token:
            sender = req.sender or "agent"
            # MCP send_to_group sends "username#session_id": an agent may only
            # post into groups owned by its own user.
            if sender.split("#", 1)[0] != owner:
                raise HTTPException(status_code=403, detail="无权向该群发消息")

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
            uid, _, _ = self.parse_group_auth(authorization)
            sender = req.sender or uid
            # 人类发消息 sender_display 为空（前端用这个判断是否 agent）。
            # The CLI posts for an agent member (--agent, or its full
            # sender_display); the browser proxy strips these fields, so only
            # local callers can.
            if req.agent:
                sender_display = await self._sender_display_for_agent(group_id, owner, req.agent)
                sender = sender_display
            elif not await self._agent_member_for_sender(group_id, sender_display):
                await self._require_group_access(group_id, uid)

        now = time.time()

        if sender_display:
            sender_global_id = ""
            sender_parts = sender_display.split("#")
            if len(sender_parts) >= 4:
                sender_global_id = sender_parts[-1].strip()
            elif sender and "#" in sender:
                sender_global_id = sender.split("#", 1)[1].strip()
            if sender_global_id:
                member_info = await get_group_member_by_global_id(self.group_db_path, group_id, sender_global_id)
                if member_info and bool(member_info.get("is_agent")):
                    if await get_group_mute_state(
                        self.group_db_path,
                        group_id=group_id,
                        target_type="all_agents",
                        target_id="*",
                    ):
                        return {
                            "status": "muted",
                            "muted": True,
                            "sender": sender,
                            "sender_display": sender_display,
                            "message": "当前群已开启全员禁言，该成员已被禁言，暂不发言",
                        }
                    if await get_group_mute_state(
                        self.group_db_path,
                        group_id=group_id,
                        target_type="member",
                        target_id=sender_global_id,
                    ):
                        return {
                            "status": "muted",
                            "muted": True,
                            "sender": sender,
                            "sender_display": sender_display,
                            "message": "该成员已被禁言，暂不发言",
                        }

        # ── Auto-resolve @mentions from message content ──
        # Match "@<short_name>" against the member list rather than regex-parsing
        # @tokens, which breaks on names with spaces. Works for all channels
        # (frontend, CLI, MCP send_to_group). Humans are included so that @human
        # also narrows the broadcast instead of waking every agent.
        resolved_mentions = list(req.mentions) if req.mentions else []
        # A team group follows its team; resolve names against today's members,
        # not ones a role change has since replaced.
        await self._sync_team_group(group_id, owner)
        if "@" in req.content:
            members = await list_group_members(self.group_db_path, group_id)
            name_gid_pairs = [
                ((m.get("short_name") or "").strip(), m.get("global_id") or "")
                for m in members
            ]
            for gid in resolve_text_mentions(
                req.content, [(name, gid) for name, gid in name_gid_pairs if name and gid]
            ):
                if gid not in resolved_mentions:
                    resolved_mentions.append(gid)
        final_mentions = resolved_mentions if resolved_mentions else None

        sender_member = await self._agent_member_for_sender(group_id, sender_display)
        if sender_member is not None:
            sender_id = member_principal(self._registry(), owner, sender_member)
        elif x_internal_token and x_internal_token == self.internal_token:
            session = sender.split("#", 1)[1] if "#" in sender else ""
            record = self._registry().webot_session(owner, session) if session else None
            sender_id = record.agent_id if record else ""
        else:
            sender_id = human_principal(sender)

        # Serialize attachments for DB storage
        attachments_json = "[]"
        if req.attachments:
            attachments_json = json.dumps([a.model_dump() for a in req.attachments])

        msg_id, created = await insert_group_message(
            self.group_db_path,
            group_id=group_id,
            sender=sender,
            sender_display=sender_display,
            content=req.content,
            attachments=attachments_json,
            timestamp=now,
            sender_id=sender_id,
            mentions=json.dumps(final_mentions or [], ensure_ascii=False),
            reply_to=req.reply_to,
            client_msg_id=(req.client_msg_id or "").strip(),
        )
        if not created:
            return {"status": "duplicate", "sender": sender, "sender_display": sender_display, "id": msg_id}

        # Agent 发消息后清除其"正在输入"状态；它说话时已看过此前的一切
        if sender_display:
            self.clear_typing_by_sender_display(group_id, sender_display)
        if sender_member is not None:
            await advance_member_read_cursor(self.group_db_path, group_id, sender_member["global_id"], msg_id)

        # 用 sender_display (tag#type#short_name#global_id) 标识发送者自己
        asyncio.create_task(
            self.broadcast_to_group(
                group_id,
                sender_display or sender,
                req.content,
                exclude_sender_display=sender_display or "",
                mentions=final_mentions,
                user_id=owner,
                attachments=req.attachments,
                run_mode=req.run_mode,
                message_id=msg_id,
                mention_all=mentions_everyone(req.content),
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

        # Keep the primary only if it is still a member; otherwise take the
        # team's current primary, or clear it.
        member_gids = {m.get("global_id") for m in new_members if m.get("global_id")}
        primary_gid = await get_group_primary_agent(self.group_db_path, group_id)
        if primary_gid not in member_gids:
            primary_gid = next(
                (m.get("global_id") for m in new_members if m.get("is_primary") and m.get("global_id")),
                None,
            )
            await set_group_primary_agent(self.group_db_path, group_id=group_id, global_id=primary_gid)

        await set_group_team(self.group_db_path, group_id=group_id, team=team_name)
        self._team_sync_state[group_id] = self._team_fingerprint(uid, team_name)
        return {
            "status": "synced",
            "group_id": group_id,
            "added_members": added_count,
            "primary_agent_global_id": primary_gid,
        }

    async def mute_group(self, group_id: str, authorization: str | None):
        """免打扰: keep every message, wake no agent. Survives restarts (it used to be in memory)."""
        uid, _, _ = self.parse_group_auth(authorization)
        await self._require_group_access(group_id, uid, owner_only=True)
        await set_group_mute_state(
            self.group_db_path, group_id=group_id, target_type="dnd", target_id="*",
            muted=True, updated_at=time.time(),
        )
        return {"status": "muted", "group_id": group_id}

    async def unmute_group(self, group_id: str, authorization: str | None):
        uid, _, _ = self.parse_group_auth(authorization)
        await self._require_group_access(group_id, uid, owner_only=True)
        await set_group_mute_state(
            self.group_db_path, group_id=group_id, target_type="dnd", target_id="*",
            muted=False, updated_at=time.time(),
        )
        return {"status": "unmuted", "group_id": group_id}

    async def group_mute_status(self, group_id: str, authorization: str | None):
        uid, _, _ = self.parse_group_auth(authorization)
        await self._require_group_access(group_id, uid)
        return {"muted": await self._is_do_not_disturb(group_id)}

    async def mute_group_member(self, group_id: str, req: GroupMuteMemberRequest, authorization: str | None):
        uid, _, _ = self.parse_group_auth(authorization)
        owner = await get_group_owner(self.group_db_path, group_id)
        if not owner:
            raise HTTPException(status_code=404, detail="群聊不存在")
        if owner != uid:
            raise HTTPException(status_code=403, detail="只有群主可以管理成员禁言")

        member = await get_group_member_by_global_id(self.group_db_path, group_id, req.global_id)
        if not member:
            raise HTTPException(status_code=404, detail="成员不存在")
        if not bool(member.get("is_agent")):
            raise HTTPException(status_code=400, detail="群主成员不能被禁言")

        await set_group_mute_state(
            self.group_db_path,
            group_id=group_id,
            target_type="member",
            target_id=req.global_id,
            muted=bool(req.muted),
            updated_at=time.time(),
        )
        return {"status": "ok", "group_id": group_id, "global_id": req.global_id, "muted": bool(req.muted)}

    async def mute_all_group_agents(self, group_id: str, req: GroupMuteAllRequest, authorization: str | None):
        uid, _, _ = self.parse_group_auth(authorization)
        owner = await get_group_owner(self.group_db_path, group_id)
        if not owner:
            raise HTTPException(status_code=404, detail="群聊不存在")
        if owner != uid:
            raise HTTPException(status_code=403, detail="只有群主可以设置全员禁言")

        await set_group_mute_state(
            self.group_db_path,
            group_id=group_id,
            target_type="all_agents",
            target_id="*",
            muted=bool(req.muted),
            updated_at=time.time(),
        )
        return {"status": "ok", "group_id": group_id, "muted": bool(req.muted)}

    async def set_primary_agent(self, group_id: str, req: GroupSetPrimaryRequest, authorization: str | None):
        """设置群主 agent。主 agent 是可选的——未设置时所有 agent 正常广播。

        设置后，非主 agent 发的消息只投递给主 agent 与显式被 @ 的成员。
        非主 agent 仍可发消息（消息入库，人类前端拉取可见）。
        """
        uid, _, _ = self.parse_group_auth(authorization)
        owner = await get_group_owner(self.group_db_path, group_id)
        if not owner:
            raise HTTPException(status_code=404, detail="群聊不存在")
        if owner != uid:
            raise HTTPException(status_code=403, detail="只有群主可以设置主 agent")

        target_gid = (req.global_id or "").strip()
        if not target_gid:
            raise HTTPException(status_code=400, detail="需要提供 global_id")

        member = await get_group_member_by_global_id(self.group_db_path, group_id, target_gid)
        if not member or not bool(member.get("is_agent")):
            raise HTTPException(status_code=400, detail="目标不是该群的 agent 成员")

        await set_group_primary_agent(
            self.group_db_path,
            group_id=group_id,
            global_id=target_gid,
        )
        return {"status": "ok", "group_id": group_id, "primary_agent_global_id": target_gid}

    async def clear_primary_agent(self, group_id: str, authorization: str | None):
        """清除主 agent，恢复全员正常广播。"""
        uid, _, _ = self.parse_group_auth(authorization)
        owner = await get_group_owner(self.group_db_path, group_id)
        if not owner:
            raise HTTPException(status_code=404, detail="群聊不存在")
        if owner != uid:
            raise HTTPException(status_code=403, detail="只有群主可以清除主 agent")

        await set_group_primary_agent(
            self.group_db_path,
            group_id=group_id,
            global_id=None,
        )
        return {"status": "ok", "group_id": group_id, "primary_agent_global_id": None}

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
        if await get_group_primary_agent(self.group_db_path, group_id) == global_id:
            await set_group_primary_agent(self.group_db_path, group_id=group_id, global_id=None)
        return {"status": "removed", "global_id": global_id}
