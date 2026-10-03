"""
MCP tools for WeBot subagent orchestration.

This is a first-phase Claude-Code-inspired runtime:
- agent profiles with explicit tool boundaries
- persistent subagent metadata
- sync and background delegated execution
- follow-up messaging into existing subagent sessions
"""

from __future__ import annotations
import sys as _sys
import os as _os
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)


import asyncio
import contextlib
import json
import os
import uuid

import httpx
from dotenv import load_dotenv
from typing import Literal
from pydantic import BaseModel, Field
from webot.mcp_tool_docs import DocumentedFastMCP as FastMCP

from webot.claude_code import detect_claude_code_cached, probe_claude_acp, run_claude_cli_prompt
from webot.profiles import (
    build_subagent_session_id,
    get_agent_profile,
    list_agent_profiles,
    slugify,
)
from webot.permission_context import resolve_permission_request
from webot.policy import get_tool_policy, run_tool_policy_hooks
from webot.runtime import normalize_session_mode
from webot.runtime_store import (
    claim_run_worker,
    clear_run_interrupt,
    create_run_record,
    delete_session_plan,
    delete_session_todos,
    get_claude_keepalive_state,
    get_latest_run_for_agent,
    get_run,
    get_session_mode as load_session_mode,
    get_session_plan,
    get_session_todos,
    heartbeat_run,
    count_inbox_messages,
    get_inbox_message,
    list_inbox_messages,
    list_recoverable_runs,
    list_runs_for_parent_session,
    list_runs_for_session,
    list_tool_approvals as list_tool_approval_records,
    mark_inbox_read,
    record_claude_keepalive_result,
    record_run_event,
    release_run_worker,
    request_run_interrupt,
    save_claude_keepalive_state,
    save_session_mode,
    save_session_plan,
    save_session_todos,
    update_run_status,
    upsert_run,
)
from webot.subagents import (
    create_subagent_record,
    get_subagent,
    get_subagent_by_name,
    get_subagent_by_session,
    list_subagents_for_user,
    delete_subagent_by_session,
    update_subagent_metadata,
    update_subagent_status,
    upsert_subagent,
)
from webot.workspace import describe_session_workspace
from common.runtime_paths import ENV_FILE, PROJECT_ROOT

root_dir = str(PROJECT_ROOT)
load_dotenv(dotenv_path=str(ENV_FILE))

mcp = FastMCP("WeBotAgents")


# Structured argument types. Every tool argument must have a closed schema so a
# decoder can be constrained to it (strict tool calling); a bare ``dict`` has none.
class PlanStep(BaseModel):
    step: str = Field(description="步骤内容")
    status: Literal["pending", "in_progress", "completed"] = Field("pending", description="步骤状态")
    notes: str = Field("", description="备注")



def _steps_to_dicts(items: list[PlanStep] | None) -> list[dict]:
    return [item.model_dump() for item in items or []]


_AGENT_PORT = os.getenv("PORT_AGENT", "51200")
_INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "")
_AGENT_URL = f"http://127.0.0.1:{_AGENT_PORT}/v1/chat/completions"
_SYSTEM_TRIGGER_URL = f"http://127.0.0.1:{_AGENT_PORT}/system_trigger"
_AGENTS_URL = f"http://127.0.0.1:{_AGENT_PORT}/v1/agents"

_BACKGROUND_TASKS: dict[str, asyncio.Task] = {}
_WORKER_ID = f"webot-mcp:{os.getpid()}:{uuid.uuid4().hex[:8]}"
def _trim(text: str, limit: int = 1200) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... (已截断，原始长度 {len(text)} 字符)"

def _new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"

def _ensure_internal_token() -> str:
    if not _INTERNAL_TOKEN:
        raise RuntimeError("系统未配置 INTERNAL_TOKEN，无法启用 WeBot 子 Agent 调度。")
    return _INTERNAL_TOKEN

def _agent_auth(username: str) -> dict[str, str]:
    """``/v1/agents`` as *username*: a session is the agent of its number."""
    return {"Authorization": f"Bearer {_ensure_internal_token()}:{username}"}

def _resolve_subagent_ref(username: str, agent_ref: str):
    ref = (agent_ref or "").strip()
    if not ref:
        return None

    record = get_subagent(ref, username)
    if record is not None:
        return record

    record = get_subagent_by_session(ref, username)
    if record is not None:
        return record

    record = get_subagent_by_name(ref, username)
    if record is not None:
        return record

    normalized = slugify(ref, "")
    if normalized and normalized != ref:
        return get_subagent_by_name(normalized, username)
    return None

async def _push_system_message(
    *,
    username: str,
    session_id: str,
    text: str,
    timeout: int = 30,
) -> None:
    token = _ensure_internal_token()
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            _SYSTEM_TRIGGER_URL,
            headers={"X-Internal-Token": token, "Content-Type": "application/json"},
            json={"user_id": username, "session_id": session_id, "text": text},
        )
        response.raise_for_status()

async def _peek_session_busy(username: str, session_id: str) -> bool:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{_AGENTS_URL}/{session_id}", headers=_agent_auth(username))
    if response.status_code == 404:  # no turn yet
        return False
    if response.status_code != 200:
        return True
    return (response.json().get("status") or {}).get("state") == "running"

def _source_label(username: str, source_session: str) -> tuple[str, str]:
    record = get_subagent_by_session(source_session, username) if source_session else None
    if record is not None:
        return record.agent_id, record.name or record.agent_id
    return "", source_session or username

def _resolve_target_sessions(username: str, target_ref: str, source_session: str) -> list[dict[str, str]]:
    normalized_ref = (target_ref or "").strip()
    if normalized_ref == "*":
        targets = [
            {
                "target_session": record.session_id,
                "target_agent_id": record.agent_id,
            }
            for record in list_subagents_for_user(username)
            if record.session_id != source_session
        ]
        if source_session != "default":
            targets.append({"target_session": "default", "target_agent_id": ""})
        return targets

    target_record = _resolve_subagent_ref(username, normalized_ref)
    if target_record is not None:
        return [{"target_session": target_record.session_id, "target_agent_id": target_record.agent_id}]
    return [{"target_session": normalized_ref or "default", "target_agent_id": ""}]

def _build_agent_payload(
    content: str,
    session_id: str,
    agent_type: str,
    *,
    username: str,
    max_turns: int | None = None,
) -> dict:
    profile = get_agent_profile(agent_type, user_id=username)
    payload = {
        "model": f"webot:{profile.agent_type}",
        "messages": [{"role": "user", "content": content}],
        "stream": False,
        "session_id": session_id,
    }
    if profile.allowed_tools is not None:
        payload["enabled_tools"] = list(profile.allowed_tools)
    if profile.preferred_model:
        payload["llm_override"] = {"model": profile.preferred_model}
    if max_turns is not None and max_turns > 0:
        payload["max_turns"] = max_turns
    return payload

def _active_background_task(agent_id: str) -> asyncio.Task | None:
    task = _BACKGROUND_TASKS.get(agent_id)
    if task is None or task.done():
        return None
    return task

async def _run_heartbeat_loop(
    *,
    run_id: str,
    username: str,
    stop_event: asyncio.Event,
    interval_seconds: int = 15,
) -> None:
    while not stop_event.is_set():
        await asyncio.sleep(max(5, interval_seconds))
        if stop_event.is_set():
            break
        heartbeat_run(
            run_id,
            username,
            worker_id=_WORKER_ID,
            lease_seconds=max(20, interval_seconds * 3),
        )

def _schedule_background_run(
    *,
    run_id: str,
    username: str,
    agent_id: str,
    session_id: str,
    agent_type: str,
    agent_name: str,
    content: str,
    parent_session: str,
    timeout: int,
    max_turns: int | None = None,
) -> None:
    _BACKGROUND_TASKS[agent_id] = asyncio.create_task(
        _run_background_subagent(
            run_id=run_id,
            username=username,
            agent_id=agent_id,
            session_id=session_id,
            agent_type=agent_type,
            agent_name=agent_name,
            content=content,
            parent_session=parent_session,
            timeout=timeout,
            max_turns=max_turns,
        )
    )

async def _recover_background_runs(username: str = "") -> None:
    for run in list_recoverable_runs():
        if username and run.user_id != username:
            continue
        if run.run_kind != "subagent" or run.wait_mode:
            continue
        if _active_background_task(run.agent_id) is not None:
            continue
        record = get_subagent(run.agent_id, run.user_id) or get_subagent_by_session(
            run.session_id,
            run.user_id,
        )
        _schedule_background_run(
            run_id=run.run_id,
            username=run.user_id,
            agent_id=run.agent_id,
            session_id=run.session_id,
            agent_type=run.agent_type,
            agent_name=record.name if record is not None else run.agent_id,
            content=run.input_text,
            parent_session=run.parent_session,
            timeout=run.timeout_seconds,
            max_turns=run.max_turns,
        )
        record_run_event(
            run.user_id,
            run.run_id,
            run.session_id,
            event_type="recovered",
            status=run.status,
            message="后台子 Agent 任务已恢复到本地调度器。",
            worker_id=_WORKER_ID,
        )

async def _call_internal_subagent(
    *,
    username: str,
    session_id: str,
    agent_type: str,
    content: str,
    timeout: int,
    max_turns: int | None = None,
) -> str:
    token = _ensure_internal_token()
    payload = _build_agent_payload(
        content,
        session_id,
        agent_type,
        username=username,
        max_turns=max_turns,
    )
    headers = {
        "Authorization": f"Bearer {token}:{username}:{session_id}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(_AGENT_URL, headers=headers, json=payload)
        if response.status_code != 200:
            raise RuntimeError(f"内部子 Agent 调用失败 (HTTP {response.status_code}): {response.text[:500]}")
        data = response.json()
    choices = data.get("choices") or []
    if not choices:
        return json.dumps(data, ensure_ascii=False)[:1000]
    message = choices[0].get("message", {})
    return str(message.get("content") or "").strip()

async def _notify_parent_session(
    *,
    username: str,
    parent_session: str,
    agent_id: str,
    agent_type: str,
    agent_name: str,
    result: str,
    status: str = "completed",
) -> None:
    if not parent_session:
        return
    if status == "completed":
        header = "[子 Agent 完成]"
    elif status == "cancelled":
        header = "[子 Agent 已取消]"
    else:
        header = "[子 Agent 失败]"
    await _push_system_message(
        username=username,
        session_id=parent_session,
        text=(
            f"{header}\n"
            f"agent_id: {agent_id}\n"
            f"name: {agent_name}\n"
            f"type: {agent_type}\n\n"
            f"{_trim(result, 1600)}"
        ),
    )

async def _run_background_subagent(
    *,
    run_id: str,
    username: str,
    agent_id: str,
    session_id: str,
    agent_type: str,
    agent_name: str,
    content: str,
    parent_session: str,
    timeout: int,
    max_turns: int | None = None,
) -> None:
    result = ""
    heartbeat_stop = asyncio.Event()
    heartbeat_task: asyncio.Task | None = None
    try:
        claimed = claim_run_worker(
            run_id,
            username,
            worker_id=_WORKER_ID,
            lease_seconds=max(30, min(timeout, 90)),
            status="running",
        )
        if claimed is None:
            return
        current_run = update_run_status(
            run_id,
            username,
            attempt_delta=1,
            parent_session=parent_session,
        )
        record_run_event(
            username,
            run_id,
            session_id,
            event_type="started",
            attempt=current_run.attempt_count if current_run is not None else 0,
            status="running",
            message=f"后台子 Agent 开始执行: {agent_name}",
            details={
                "worker_id": _WORKER_ID,
                "agent_type": agent_type,
                "parent_session": parent_session,
            },
        )
        heartbeat_task = asyncio.create_task(
            _run_heartbeat_loop(
                run_id=run_id,
                username=username,
                stop_event=heartbeat_stop,
                interval_seconds=min(20, max(10, timeout // 10 if timeout else 15)),
            )
        )
        update_subagent_status(agent_id, username, status="running")
        latest_state = get_run(run_id, username)
        if latest_state is not None and latest_state.interrupt_requested:
            raise asyncio.CancelledError
        result = await _call_internal_subagent(
            username=username,
            session_id=session_id,
            agent_type=agent_type,
            content=content,
            timeout=timeout,
            max_turns=max_turns,
        )
        release_run_worker(
            run_id,
            username,
            worker_id=_WORKER_ID,
            status="completed",
            last_result=result,
            last_error="",
            clear_interrupt=True,
        )
        record_run_event(
            username,
            run_id,
            session_id,
            event_type="completed",
            status="completed",
            message=f"后台子 Agent 执行完成: {agent_name}",
        )
        update_subagent_status(agent_id, username, status="completed", last_result=result)
    except asyncio.CancelledError:
        cancelled_text = "子 Agent 已取消。"
        release_run_worker(
            run_id,
            username,
            worker_id=_WORKER_ID,
            status="cancelled",
            last_result=cancelled_text,
            last_error="cancelled",
            clear_interrupt=True,
        )
        record_run_event(
            username,
            run_id,
            session_id,
            event_type="cancelled",
            status="cancelled",
            message=f"后台子 Agent 已取消: {agent_name}",
        )
        update_subagent_status(
            agent_id,
            username,
            status="cancelled",
            last_result=cancelled_text,
        )
        with contextlib.suppress(Exception):
            await _notify_parent_session(
                username=username,
                parent_session=parent_session,
                agent_id=agent_id,
                agent_type=agent_type,
                agent_name=agent_name,
                result=cancelled_text,
                status="cancelled",
            )
        raise
    except Exception as exc:
        error_text = f"子 Agent 运行失败: {exc}"
        release_run_worker(
            run_id,
            username,
            worker_id=_WORKER_ID,
            status="failed",
            last_error=error_text,
            last_result=error_text,
            clear_interrupt=True,
        )
        record_run_event(
            username,
            run_id,
            session_id,
            event_type="failed",
            status="failed",
            message=error_text,
        )
        update_subagent_status(
            agent_id,
            username,
            status="failed",
            last_result=error_text,
        )
        await _notify_parent_session(
            username=username,
            parent_session=parent_session,
            agent_id=agent_id,
            agent_type=agent_type,
            agent_name=agent_name,
            result=error_text,
            status="failed",
        )
    else:
        try:
            await _notify_parent_session(
                username=username,
                parent_session=parent_session,
                agent_id=agent_id,
                agent_type=agent_type,
                agent_name=agent_name,
                result=result,
                status="completed",
            )
        except Exception as exc:
            update_subagent_status(
                agent_id,
                username,
                status="completed",
                last_result=f"{result}\n\n[系统提示] 父会话回调通知失败: {exc}",
            )
            record_run_event(
                username,
                run_id,
                session_id,
                event_type="callback_failed",
                status="completed",
                message=f"父会话回调失败: {exc}",
            )
    finally:
        heartbeat_stop.set()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        _BACKGROUND_TASKS.pop(agent_id, None)

async def _cancel_internal_subagent(
    *,
    username: str,
    session_id: str,
) -> bool:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"{_AGENTS_URL}/{session_id}/control", headers=_agent_auth(username),
                                     json={"action": "cancel"})
    if response.status_code == 404:  # no turn yet
        return False
    if response.status_code != 200:
        raise RuntimeError(f"取消子 Agent 失败 (HTTP {response.status_code}): {response.text[:500]}")
    return bool(response.json().get("cancelled"))


async def _delete_internal_session(
    *,
    username: str,
    session_id: str,
) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.delete(f"{_AGENTS_URL}/{session_id}", headers=_agent_auth(username))
    if response.status_code == 404:  # no turn yet: nothing of it in the agent service
        return {}
    if response.status_code == 204:
        return {}
    if response.status_code != 200:
        raise RuntimeError(f"删除子 Agent session 失败 (HTTP {response.status_code}): {response.text[:500]}")
    return response.json()


def _agent_profiles_text(username: str = "") -> str:
    """
    列出 WeBot 内置子 Agent 类型及其工具能力边界。

    这一步适合在委派任务前先调用，用于选择合适的 agent_type。
    """
    lines = ["🤖 WeBot 可用子 Agent Profiles:\n"]
    for profile in list_agent_profiles(username):
        tools = ", ".join(profile.allowed_tools or ())
        lines.append(f"- {profile.agent_type} / {profile.display_name}")
        lines.append(f"  定位: {profile.description}")
        lines.append(f"  默认执行: {'后台' if profile.background_default else '前台'}")
        lines.append(f"  工具: {tools or '全部工具'}")
        lines.append(f"  max_turns: {profile.max_turns or '未限制'}")
        if profile.definition_path:
            lines.append(f"  source: {profile.source} ({profile.definition_path})")
        else:
            lines.append(f"  source: {profile.source}")
    lines.append("\n用 spawn_subagent(agent_type=...) 创建或继续一个子 Agent。")
    return "\n".join(lines)

@mcp.tool()
async def spawn_subagent(
    username: str,
    task: str,
    agent_type: str = "general",
    name: str = "",
    description: str = "",
    wait: bool | None = None,
    parent_session: str = "",
    timeout: int = 300,
    max_turns: int | None = None,
    workspace_mode: str = "isolated",
    workspace_root: str = "",
    cwd: str = "",
    remote: str = "",
) -> str:
    """
    创建或继续一个 WeBot 子 Agent，处理可分工的独立任务（调研、规划、实现、审查、验证），
    让中间过程留在它自己的上下文里。

    Args:
        username: 当前用户（系统自动注入）
        task: 委派给子 Agent 的任务
        agent_type: 子 Agent 类型，如 general/research/planner/coder/reviewer/verifier
        name: 可选名称；若名称已存在，则继续该子 Agent 的既有会话
        description: 对这次子任务的简短描述
        wait: True=同步等待结果；False=后台执行并异步通知父会话
        parent_session: 当前父会话 ID（系统自动注入）
        timeout: 最长等待秒数
        max_turns: 子 Agent 最多执行的轮数；留空用默认值
        workspace_mode: isolated（独立目录，默认）/ shared（用户根目录）/ worktree（workspace_root 仓库的 git worktree）/ remote / custom
        workspace_root: 工作区根目录，相对用户目录解析；worktree 模式下是要派生 worktree 的 git 仓库
        cwd: 子 Agent 的工作目录，相对工作区根目录解析
        remote: remote 模式下的远端标识
    """
    await _recover_background_runs(username)
    safe_name = slugify(name, "")
    requested_profile = get_agent_profile(agent_type, user_id=username)
    existing = get_subagent_by_name(safe_name, username) if safe_name else None
    if existing:
        if existing.agent_type != requested_profile.agent_type:
            return (
                f"❌ 已存在同名子 Agent: {existing.name}\n"
                f"现有类型: {existing.agent_type}\n"
                f"请求类型: {requested_profile.agent_type}\n\n"
                "同名子 Agent 会被视为继续原会话。若要新建不同角色，请换一个 name。"
            )
        agent_id = existing.agent_id
        session_id = existing.session_id
        profile = get_agent_profile(existing.agent_type, user_id=username)
        record = existing
        new_parent_session = parent_session or record.parent_session
        new_description = description or record.description
        updated = update_subagent_metadata(
            agent_id,
            username,
            description=new_description,
            parent_session=new_parent_session,
            workspace_mode=workspace_mode or existing.workspace_mode,
            workspace_root=workspace_root or existing.workspace_root,
            cwd=cwd or existing.cwd,
            remote=remote or existing.remote,
        )
        if updated is not None:
            record = updated
        mode_label = "继续已有"
    else:
        profile = requested_profile
        agent_id = safe_name or uuid.uuid4().hex[:8]
        session_id = build_subagent_session_id(profile.agent_type, agent_id)
        record = create_subagent_record(
            agent_id=agent_id,
            user_id=username,
            session_id=session_id,
            agent_type=profile.agent_type,
            name=safe_name or agent_id,
            description=description or task[:80],
            parent_session=parent_session,
            workspace_mode=workspace_mode or "isolated",
            workspace_root=workspace_root,
            cwd=cwd,
            remote=remote,
            status="idle",
        )
        upsert_subagent(record)
        mode_label = "新建"

    effective_parent_session = record.parent_session or parent_session
    effective_wait = wait if wait is not None else (not profile.background_default)
    inherited_mode = load_session_mode(username, effective_parent_session or session_id).get("mode")
    if profile.agent_type == "planner":
        subagent_mode = "plan"
    elif profile.agent_type in {"reviewer", "verifier"}:
        subagent_mode = "review"
    else:
        subagent_mode = normalize_session_mode(inherited_mode)
    save_session_mode(
        username,
        session_id,
        mode=subagent_mode,
        reason=description or f"{profile.display_name} delegated from {effective_parent_session or 'default'}",
    )

    if record.status in {"queued", "running"} and (
        _active_background_task(agent_id) is not None or await _peek_session_busy(username, session_id)
    ):
        return (
            f"⏳ 子 Agent {record.name} ({agent_id}) 正在后台运行中。\n"
            "请先等待完成，或稍后用 list_subagents / get_subagent_history 查看状态。"
        )

    run_id = _new_run_id()
    run_record = create_run_record(
        run_id=run_id,
        user_id=username,
        agent_id=agent_id,
        session_id=session_id,
        parent_session=effective_parent_session,
        agent_type=profile.agent_type,
        title=description or task[:80],
        input_text=task,
        status="running" if effective_wait else "queued",
        timeout_seconds=timeout,
        max_turns=max_turns,
        wait_mode=effective_wait,
        run_kind="subagent",
        mode=subagent_mode,
        metadata={
            "agent_name": record.name,
            "workspace_mode": record.workspace_mode,
            "workspace_root": record.workspace_root,
            "cwd": record.cwd,
            "remote": record.remote,
            "parent_session": effective_parent_session,
        },
    )
    upsert_run(run_record)
    record_run_event(
        username,
        run_id,
        session_id,
        event_type="queued" if not effective_wait else "prepared",
        status=run_record.status,
        message=f"{mode_label}子 Agent 任务已创建: {record.name}",
        details={"mode": subagent_mode},
    )

    if effective_wait:
        claim_run_worker(
            run_id,
            username,
            worker_id=_WORKER_ID,
            lease_seconds=max(30, min(timeout, 90)),
            status="running",
        )
        current_run = update_run_status(
            run_id,
            username,
            attempt_delta=1,
            parent_session=effective_parent_session,
        )
        record_run_event(
            username,
            run_id,
            session_id,
            event_type="started",
            attempt=current_run.attempt_count if current_run is not None else 0,
            status="running",
            message=f"同步子 Agent 开始执行: {record.name}",
        )
        update_subagent_status(agent_id, username, status="running")
        try:
            result = await _call_internal_subagent(
                username=username,
                session_id=session_id,
                agent_type=profile.agent_type,
                content=task,
                timeout=timeout,
                max_turns=max_turns,
            )
            release_run_worker(
                run_id,
                username,
                worker_id=_WORKER_ID,
                status="completed",
                last_result=result,
                last_error="",
                clear_interrupt=True,
            )
            record_run_event(
                username,
                run_id,
                session_id,
                event_type="completed",
                status="completed",
                message=f"同步子 Agent 完成: {record.name}",
            )
            update_subagent_status(agent_id, username, status="completed", last_result=result)
            return (
                f"✅ {mode_label}子 Agent 完成\n"
                f"run_id: {run_id}\n"
                f"agent_id: {agent_id}\n"
                f"name: {record.name}\n"
                f"type: {profile.agent_type}\n"
                f"session_id: {session_id}\n\n"
                f"{_trim(result, 2000)}"
            )
        except Exception as exc:
            error_text = f"子 Agent 执行失败: {exc}"
            release_run_worker(
                run_id,
                username,
                worker_id=_WORKER_ID,
                status="failed",
                last_error=error_text,
                last_result=error_text,
                clear_interrupt=True,
            )
            record_run_event(
                username,
                run_id,
                session_id,
                event_type="failed",
                status="failed",
                message=error_text,
            )
            update_subagent_status(
                agent_id,
                username,
                status="failed",
                last_result=error_text,
            )
            return (
                f"❌ 子 Agent 执行失败\n"
                f"run_id: {run_id}\n"
                f"agent_id: {agent_id}\n"
                f"type: {profile.agent_type}\n"
                f"error: {exc}"
            )

    _schedule_background_run(
        run_id=run_id,
        username=username,
        agent_id=agent_id,
        session_id=session_id,
        agent_type=profile.agent_type,
        agent_name=record.name,
        content=task,
        parent_session=effective_parent_session,
        timeout=timeout,
        max_turns=max_turns,
    )
    record_run_event(
        username,
        run_id,
        session_id,
        event_type="scheduled",
        status="queued",
        message=f"后台子 Agent 已加入本地调度器: {record.name}",
    )
    return (
        f"🚀 {mode_label}子 Agent 已转后台运行\n"
        f"run_id: {run_id}\n"
        f"agent_id: {agent_id}\n"
        f"name: {record.name}\n"
        f"type: {profile.agent_type}\n"
        f"session_id: {session_id}\n"
        f"parent_session: {effective_parent_session or '(none)'}\n\n"
        f"workspace: {describe_session_workspace(username, session_id, explicit_cwd=record.cwd)}\n\n"
        "可稍后使用 list_subagents 查看状态，或用 send_subagent_message 继续与它协作。"
    )

@mcp.tool()
async def list_subagents(username: str) -> str:
    """
    列出当前用户已创建的 WeBot 子 Agent，以及可用的子 Agent 类型（agent_type）
    和各自的工具边界。委派任务前先用它选 agent_type。
    """
    await _recover_background_runs(username)
    records = list_subagents_for_user(username)
    profiles = _agent_profiles_text(username)
    if not records:
        return f"📭 当前还没有任何 WeBot 子 Agent。\n\n{profiles}"

    lines = [f"📋 用户 {username} 的子 Agent 列表:\n"]
    for record in records:
        runtime_status = record.status
        latest_run = get_latest_run_for_agent(username, record.agent_id)
        unread_inbox = count_inbox_messages(username, record.session_id, status="unread")
        session_mode = load_session_mode(username, record.session_id).get("mode")
        # 后台执行在 agent 主进程中跑：DB 写 "running" 后只能靠 session 是否 busy 判断真实状态。
        if runtime_status == "running":
            with contextlib.suppress(Exception):
                if not await _peek_session_busy(username, record.session_id):
                    runtime_status = "running (session idle — 用 get_subagent_history 查结果)"
        lines.append(
            f"- {record.name} ({record.agent_id})\n"
            f"  type: {record.agent_type}\n"
            f"  session_id: {record.session_id}\n"
            f"  mode: {session_mode}\n"
            f"  status: {runtime_status}\n"
            f"  workspace: {describe_session_workspace(username, record.session_id, explicit_cwd=record.cwd)}\n"
            f"  latest_run: {latest_run.run_id if latest_run else '(none)'} / {latest_run.status if latest_run else '(none)'}\n"
            f"  inbox: {unread_inbox} unread\n"
            f"  updated_at: {record.updated_at}\n"
            f"  last_result: {_trim(record.last_result, 240) or '(暂无)'}"
        )
    lines.append(f"\n{profiles}")
    return "\n".join(lines)

@mcp.tool()
async def send_subagent_message(
    username: str,
    agent_ref: str,
    content: str,
    wait: bool | None = None,
    source_session: str = "",
    timeout: int = 300,
    max_turns: int | None = None,
) -> str:
    """
    向一个已存在的子 Agent 继续发送消息。

    :param agent_ref: 子 Agent 的 agent_id、session_id 或创建时的 name
    :param content: 要发送给子 Agent 的消息
    :param wait: True=同步等待回复；False=后台执行并异步通知；留空沿用子 Agent 的默认
    :param timeout: 同步等待的最长秒数
    :param max_turns: 本次最多执行的轮数；留空用默认值
    """
    await _recover_background_runs(username)
    record = _resolve_subagent_ref(username, agent_ref)
    if record is None:
        return f"❌ 未找到子 Agent: {agent_ref}"

    if source_session and source_session != record.parent_session:
        updated = update_subagent_metadata(
            record.agent_id,
            username,
            parent_session=source_session,
        )
        if updated is not None:
            record = updated

    if record.status in {"queued", "running"} and (
        _active_background_task(record.agent_id) is not None
        or await _peek_session_busy(username, record.session_id)
    ):
        return (
            f"⏳ 子 Agent {record.name} ({record.agent_id}) 仍在后台运行中，"
            "请等待其完成后再继续发送消息。"
        )

    profile = get_agent_profile(record.agent_type, user_id=username)
    effective_wait = wait if wait is not None else (not profile.background_default)
    subagent_mode = load_session_mode(username, record.session_id).get("mode")

    run_id = _new_run_id()
    run_record = create_run_record(
        run_id=run_id,
        user_id=username,
        agent_id=record.agent_id,
        session_id=record.session_id,
        parent_session=source_session or record.parent_session,
        agent_type=record.agent_type,
        title=content[:80],
        input_text=content,
        status="running" if effective_wait else "queued",
        timeout_seconds=timeout,
        max_turns=max_turns,
        wait_mode=effective_wait,
        run_kind="subagent",
        mode=subagent_mode,
        metadata={
            "agent_name": record.name,
            "follow_up": True,
            "parent_session": source_session or record.parent_session,
        },
    )
    upsert_run(run_record)
    record_run_event(
        username,
        run_id,
        record.session_id,
        event_type="queued" if not effective_wait else "prepared",
        status=run_record.status,
        message=f"子 Agent 续聊任务已创建: {record.name}",
    )

    if effective_wait:
        claim_run_worker(
            run_id,
            username,
            worker_id=_WORKER_ID,
            lease_seconds=max(30, min(timeout, 90)),
            status="running",
        )
        current_run = update_run_status(
            run_id,
            username,
            attempt_delta=1,
            parent_session=source_session or record.parent_session,
        )
        record_run_event(
            username,
            run_id,
            record.session_id,
            event_type="started",
            attempt=current_run.attempt_count if current_run is not None else 0,
            status="running",
            message=f"同步续聊开始执行: {record.name}",
        )
        update_subagent_status(record.agent_id, username, status="running")
        try:
            result = await _call_internal_subagent(
                username=username,
                session_id=record.session_id,
                agent_type=record.agent_type,
                content=content,
                timeout=timeout,
                max_turns=max_turns,
            )
            release_run_worker(
                run_id,
                username,
                worker_id=_WORKER_ID,
                status="completed",
                last_result=result,
                last_error="",
                clear_interrupt=True,
            )
            record_run_event(
                username,
                run_id,
                record.session_id,
                event_type="completed",
                status="completed",
                message=f"同步续聊完成: {record.name}",
            )
            update_subagent_status(record.agent_id, username, status="completed", last_result=result)
            return (
                f"✅ 子 Agent 已回复\n"
                f"run_id: {run_id}\n"
                f"agent_id: {record.agent_id}\n"
                f"name: {record.name}\n"
                f"type: {record.agent_type}\n\n"
                f"{_trim(result, 2000)}"
            )
        except Exception as exc:
            error_text = f"子 Agent 续聊失败: {exc}"
            release_run_worker(
                run_id,
                username,
                worker_id=_WORKER_ID,
                status="failed",
                last_error=error_text,
                last_result=error_text,
                clear_interrupt=True,
            )
            record_run_event(
                username,
                run_id,
                record.session_id,
                event_type="failed",
                status="failed",
                message=error_text,
            )
            update_subagent_status(
                record.agent_id,
                username,
                status="failed",
                last_result=error_text,
            )
            return f"❌ 子 Agent 续聊失败: {exc}\nrun_id: {run_id}"

    _schedule_background_run(
        run_id=run_id,
        username=username,
        agent_id=record.agent_id,
        session_id=record.session_id,
        agent_type=record.agent_type,
        agent_name=record.name,
        content=content,
        parent_session=source_session or record.parent_session,
        timeout=timeout,
        max_turns=max_turns,
    )
    record_run_event(
        username,
        run_id,
        record.session_id,
        event_type="scheduled",
        status="queued",
        message=f"后台续聊已加入本地调度器: {record.name}",
    )
    return (
        f"🚀 子 Agent 已收到后台续聊任务\n"
        f"run_id: {run_id}\n"
        f"agent_id: {record.agent_id}\n"
        f"name: {record.name}\n"
        f"type: {record.agent_type}"
    )

@mcp.tool()
async def get_subagent_history(
    username: str,
    agent_ref: str,
    limit: int = 12,
) -> str:
    """
    读取某个子 Agent 最近的对话记录。

    适合查看它已经做了什么，而不是把整个历史都拉进主上下文。

    :param agent_ref: 子 Agent 的 agent_id、session_id 或创建时的 name
    :param limit: 返回最近多少条消息
    """
    await _recover_background_runs(username)
    record = _resolve_subagent_ref(username, agent_ref)
    if record is None:
        return f"❌ 未找到子 Agent: {agent_ref}"

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{_AGENTS_URL}/{record.session_id}/history", headers=_agent_auth(username),
                                    params={"limit": max(1, min(limit, 50))})
    if response.status_code not in (200, 404):  # 404: no turn yet
        return f"❌ 读取子 Agent 历史失败 (HTTP {response.status_code}): {response.text[:500]}"
    messages = (response.json().get("messages") or []) if response.status_code == 200 else []
    if not messages:
        return f"📭 子 Agent {record.name} ({record.agent_id}) 还没有历史消息。"

    lines = [
        f"🧵 子 Agent 历史\n"
        f"agent_id: {record.agent_id}\n"
        f"name: {record.name}\n"
        f"type: {record.agent_type}\n"
        f"session_id: {record.session_id}\n"
    ]
    for msg in messages:
        role = msg.get("role", "unknown")
        content = _trim(str(msg.get("content") or ""), 400)
        if msg.get("tool_calls"):
            tool_names = ", ".join(tc.get("name", "") for tc in msg.get("tool_calls", []))
            lines.append(f"[{role}] {content}\n  tool_calls: {tool_names}")
        else:
            lines.append(f"[{role}] {content}")
    return "\n".join(lines)

@mcp.tool()
async def cancel_subagent(
    username: str,
    agent_ref: str,
    source_session: str = "",
) -> str:
    """
    取消一个正在运行中的 WeBot 子 Agent。

    :param agent_ref: 子 Agent 的 agent_id、session_id 或创建时的 name
    """
    await _recover_background_runs(username)
    record = _resolve_subagent_ref(username, agent_ref)
    if record is None:
        return f"❌ 未找到子 Agent: {agent_ref}"

    latest_run = get_latest_run_for_agent(username, record.agent_id)
    if latest_run is not None and latest_run.status in {"queued", "running"}:
        request_run_interrupt(latest_run.run_id, username)
        record_run_event(
            username,
            latest_run.run_id,
            record.session_id,
            event_type="cancel_requested",
            status="cancelling",
            message=f"收到取消请求: {record.name}",
        )
    task = _active_background_task(record.agent_id)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    cancelled = False
    try:
        cancelled = await _cancel_internal_subagent(
            username=username,
            session_id=record.session_id,
        )
    except Exception as exc:
        update_subagent_status(
            record.agent_id,
            username,
            status="failed",
            last_result=f"取消子 Agent 失败: {exc}",
        )
        return f"❌ 取消子 Agent 失败: {exc}"

    updated = update_subagent_status(
        record.agent_id,
        username,
        status="cancelled",
        last_result="子 Agent 已取消。",
    ) or record
    if latest_run is not None:
        update_run_status(
            latest_run.run_id,
            username,
            status="cancelled",
            last_result="子 Agent 已取消。",
            last_error="cancelled",
            interrupt_requested=False,
            clear_worker=True,
        )
        record_run_event(
            username,
            latest_run.run_id,
            record.session_id,
            event_type="cancelled",
            status="cancelled",
            message=f"子 Agent 已取消: {updated.name}",
        )

    # 后台执行在 agent 主进程跑，没有 in-process task 自己 notify，
    # 这里如果有父 session 就主动通知一次。
    if source_session or updated.parent_session:
        with contextlib.suppress(Exception):
            await _notify_parent_session(
                username=username,
                parent_session=source_session or updated.parent_session,
                agent_id=updated.agent_id,
                agent_type=updated.agent_type,
                agent_name=updated.name,
                result="子 Agent 已取消。",
                status="cancelled",
            )

    with contextlib.suppress(Exception):
        run_tool_policy_hooks(
            get_tool_policy(username),
            event="subagent_stop",
            user_id=username,
            session_id=source_session or updated.parent_session or updated.session_id,
            tool_name="__session__",
            args={
                "agent_id": updated.agent_id,
                "agent_type": updated.agent_type,
                "session_id": updated.session_id,
            },
            result={"status": "cancelled"},
        )

    return (
        f"🛑 子 Agent 已取消\n"
        f"run_id: {latest_run.run_id if latest_run else '(none)'}\n"
        f"agent_id: {updated.agent_id}\n"
        f"name: {updated.name}\n"
        f"type: {updated.agent_type}\n"
        f"session_id: {updated.session_id}\n"
        f"cancelled_runtime: {'yes' if cancelled else 'no'}"
    )


@mcp.tool()
async def delete_subagent(
    username: str,
    agent_ref: str,
    source_session: str = "",
) -> str:
    """
    删除一个 WeBot 子 Agent。

    子 Agent 与其 session 一一对应；删除会取消当前运行、删除该 session
    的 checkpoint/history，并移除 webot_subagents registry 记录。

    :param agent_ref: 子 Agent 的 agent_id、session_id 或创建时的 name
    """
    await _recover_background_runs(username)
    record = _resolve_subagent_ref(username, agent_ref)
    if record is None:
        return f"🗑️ 子 Agent 已删除或不存在: {agent_ref}"
    if source_session and source_session == record.session_id:
        return "❌ 不能通过子 Agent 工具删除当前正在执行该工具的会话。"

    latest_run = get_latest_run_for_agent(username, record.agent_id)
    if latest_run is not None and latest_run.status in {"queued", "running", "cancelling"}:
        request_run_interrupt(latest_run.run_id, username)
        record_run_event(
            username,
            latest_run.run_id,
            record.session_id,
            event_type="delete_requested",
            status="cancelling",
            message=f"收到删除请求: {record.name}",
        )

    with contextlib.suppress(Exception):
        await _cancel_internal_subagent(username=username, session_id=record.session_id)

    worker = _BACKGROUND_TASKS.pop(record.agent_id, None)
    if worker is not None and worker is not asyncio.current_task():
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    # Record cancellation before deleting the runtime DB. Writing child plans
    # or run events afterwards recreates its database and can fail after the
    # Agent service has already reported a successful deletion.
    if latest_run is not None:
        update_run_status(latest_run.run_id, username, status="cancelled",
                          last_result="子 Agent 正在删除。", last_error="delete_requested",
                          interrupt_requested=False, clear_worker=True)

    try:
        delete_resp = await _delete_internal_session(username=username, session_id=record.session_id)
    except Exception as exc:
        return f"❌ 删除子 Agent 失败: {exc}"

    # Deleting the agent removes the registry row for subagent sessions.
    # Keep this idempotent cleanup for MCP-only/runtime edge cases.
    registry_deleted = delete_subagent_by_session(username, record.session_id)
    from webot.runtime_store import delete_agent_runtime_db
    delete_agent_runtime_db(username, record.session_id)

    parent_session = source_session or record.parent_session
    if parent_session:
        with contextlib.suppress(Exception):
            await _notify_parent_session(
                username=username,
                parent_session=parent_session,
                agent_id=record.agent_id,
                agent_type=record.agent_type,
                agent_name=record.name,
                result="子 Agent session 已删除。",
                status="cancelled",
            )

    with contextlib.suppress(Exception):
        run_tool_policy_hooks(
            get_tool_policy(username),
            event="subagent_stop",
            user_id=username,
            session_id=parent_session or record.session_id,
            tool_name="__session__",
            args={
                "agent_id": record.agent_id,
                "agent_type": record.agent_type,
                "session_id": record.session_id,
            },
            result={"status": "deleted"},
        )

    return (
        f"🗑️ 子 Agent 已删除\n"
        f"agent_id: {record.agent_id}\n"
        f"name: {record.name}\n"
        f"type: {record.agent_type}\n"
        f"session_id: {record.session_id}\n"
        f"registry_deleted_extra: {registry_deleted}\n"
        f"runtime_state: removed with session\n"
        f"delete_session: {delete_resp.get('message') or delete_resp.get('status') or 'ok'}"
    )


@mcp.tool()
async def write_session_plan(
    username: str,
    items: list[PlanStep] | None = None,
    target: Literal["plan", "todos"] = "plan",
    title: str = "",
    source_session: str = "",
    status: Literal["active", "completed", "archived"] = "active",
) -> str:
    """写入或覆盖当前会话的 plan（标题、步骤、状态）或 todo 列表。

    :param items: 步骤列表，整体覆盖原有内容
    :param target: 写 plan 还是 todos
    :param title: plan 标题；留空沿用原标题（todos 忽略）
    :param status: plan 状态：active / completed / archived（todos 忽略）
    """
    session_id = source_session or "default"
    if target == "todos":
        save_session_todos(username, session_id, items=_steps_to_dicts(items))
        todos = get_session_todos(username, session_id) or {"items": []}
        return f"✅ Todo 已更新\nsession_id: {session_id}\nitems: {len(todos.get('items', []))}"
    existing = get_session_plan(username, session_id) or {}
    save_session_plan(
        username,
        session_id,
        title=title or existing.get("title", ""),
        status=status,
        items=_steps_to_dicts(items),
    )
    plan = get_session_plan(username, session_id) or {"items": []}
    return (
        f"✅ 会话计划已更新\n"
        f"session_id: {session_id}\n"
        f"title: {plan.get('title', '')}\n"
        f"status: {plan.get('status', 'active')}\n"
        f"items: {len(plan.get('items', []))}"
    )


@mcp.tool()
async def read_session_plan(username: str, source_session: str = "") -> str:
    """读取当前会话完整的 plan 和 todo 列表（运行时上下文只带前几条，需要全部时才用）。"""
    session_id = source_session or "default"
    plan = get_session_plan(username, session_id)
    todos = get_session_todos(username, session_id)
    if plan is None and todos is None:
        return f"📭 当前会话 {session_id} 还没有计划和 todo。"
    lines = [f"session_id: {session_id}"]
    if plan is not None:
        lines.append(f"🗺️ 当前计划\ntitle: {plan.get('title', '')}\nstatus: {plan.get('status', 'active')}")
        for item in plan.get("items", []):
            lines.append(f"- [{item.get('status', 'pending')}] {item.get('step', '')}")
    if todos is not None:
        lines.append("📌 当前 Todo")
        for item in todos.get("items", []):
            lines.append(f"- [{item.get('status', 'pending')}] {item.get('step', '')}")
    return "\n".join(lines)


@mcp.tool()
async def clear_session_plan(
    username: str,
    source_session: str = "",
    target: Literal["plan", "todos", "all"] = "plan",
) -> str:
    """删除当前会话的 plan、todo 列表，或两者。

    :param target: 删除哪一个：plan / todos / all
    """
    session_id = source_session or "default"
    cleared = []
    if target in ("plan", "all") and delete_session_plan(username, session_id):
        cleared.append("计划")
    if target in ("todos", "all") and delete_session_todos(username, session_id):
        cleared.append("Todo")
    if cleared:
        return f"🧹 已清除会话{'和'.join(cleared)}: {session_id}"
    return f"📭 会话 {session_id} 没有可清除的内容。"


@mcp.tool()
async def claude_code_status(
    username: str,
    source_session: str = "",
) -> str:
    """查看本机 Claude Code 是否可用，以及当前会话的保活配置状态。"""
    session_id = source_session or "default"
    status = detect_claude_code_cached(ttl_seconds=10)
    keepalive = get_claude_keepalive_state(username, session_id)
    errors = status.get("errors") or []
    return (
        "🧭 Claude Code 本地状态\n"
        f"available: {status.get('available', False)}\n"
        f"claude: {status.get('claude_version') or status.get('claude_path') or '(missing)'}\n"
        f"acpx: {status.get('acpx_path') or '(missing)'} · claude_supported={status.get('acpx_claude_supported', False)}\n"
        f"keepalive: {'enabled' if keepalive.enabled else 'disabled'} · {keepalive.start_time}-{keepalive.sleep_time} · {keepalive.weekdays}\n"
        f"last: {keepalive.last_status} · {keepalive.last_run_at or '(never)'}\n"
        f"errors: {_trim('; '.join(str(item) for item in errors), 500) or '(none)'}"
    )

@mcp.tool()
async def probe_claude_code(
    username: str,
    prompt: str = "",
    source_session: str = "",
    use_acp: bool = True,
    timeout: int = 90,
) -> str:
    """立刻向本机 Claude Code 发一条消息：确认通道能用，或手动触发一次保活。

    :param prompt: 发送的消息；留空用保活配置里的提示词
    :param use_acp: True 走 ACP 通道；False 直接调用 claude CLI
    :param timeout: 等待回复的最长秒数
    """
    session_id = source_session or "default"
    state = get_claude_keepalive_state(username, session_id)
    effective_prompt = prompt or state.prompt or "ping"
    result = (
        probe_claude_acp(
            prompt=effective_prompt,
            session_name=f"clawcross-{username}-{session_id}".replace("#", "-")[:80],
            timeout=timeout,
        )
        if use_acp
        else run_claude_cli_prompt(prompt=effective_prompt, model=state.model, timeout=timeout)
    )
    record_claude_keepalive_result(
        username,
        session_id,
        status="success" if result.get("ok") else "failed",
        result=str(result.get("stdout_tail") or "")[-2000:],
        error=str(result.get("error") or result.get("stderr_tail") or "")[-1000:],
        metadata={"probe": True, "source": "mcp", "use_acp": use_acp},
    )
    channel = "ACP" if use_acp else "CLI"
    if result.get("ok"):
        return f"✅ Claude Code {channel} 调用成功\nsession_id: {session_id}"
    return f"❌ Claude Code {channel} 调用失败\n{_trim(str(result.get('error') or result.get('stderr_tail') or ''), 1200)}"


@mcp.tool()
async def configure_claude_keepalive(
    username: str,
    enabled: bool = True,
    prompt: str = "ping",
    source_session: str = "",
    timezone_name: str = "",
    start_time: str = "06:00",
    sleep_time: str = "23:00",
    weekdays: str = "MTWRFSU",
    timeout: int = 90,
) -> str:
    """配置当前会话对 Claude Code 的定时保活：开关、提示词、时区、起止时刻、生效星期。

    :param enabled: 是否开启定时保活
    :param prompt: 保活时发送的消息
    :param timezone_name: IANA 时区名，如 Asia/Shanghai；留空用系统时区
    :param start_time: 每天开始保活的时刻，HH:MM
    :param sleep_time: 每天停止保活的时刻，HH:MM
    :param weekdays: 生效星期，每个字母代表一天：M T W R F S U（R=周四，U=周日）
    :param timeout: 每次保活等待回复的最长秒数
    """
    session_id = source_session or "default"
    record = save_claude_keepalive_state(
        username,
        session_id,
        enabled=enabled,
        prompt=prompt,
        timezone_name=timezone_name,
        start_time=start_time,
        sleep_time=sleep_time,
        weekdays=weekdays,
        timeout_seconds=timeout,
        metadata={"source": "mcp"},
    )
    return (
        "✅ Claude keepalive 配置已保存\n"
        f"session_id: {record.session_id}\n"
        f"enabled: {record.enabled}\n"
        f"window: {record.start_time}-{record.sleep_time} {record.timezone or '(local)'}\n"
        "说明：ClawCross 只保存和执行安全的一次性 kickoff，不会自动安装系统唤醒/睡眠任务。"
    )

@mcp.tool()
async def list_tool_approvals(
    username: str,
    source_session: str = "",
    status: str = "pending",
    limit: int = 20,
) -> str:
    """列出当前会话的工具审批记录，默认只看还在等待批准的。

    :param status: 按状态过滤：pending / approved / denied；留空列出全部
    :param limit: 最多返回多少条（1-50）
    """
    session_id = source_session or None
    approvals = list_tool_approval_records(
        username,
        session_id,
        status=(status or "").strip().lower() or None,
        limit=max(1, min(limit, 50)),
    )
    if not approvals:
        return "📭 当前没有匹配的 tool approval 请求。"
    lines = ["🪪 Tool Approval 列表"]
    for approval in approvals:
        lines.append(
            f"- {approval.approval_id}\n"
            f"  session_id: {approval.session_id}\n"
            f"  tool: {approval.tool_name}\n"
            f"  status: {approval.status}\n"
            f"  reason: {_trim(approval.request_reason, 160)}"
        )
    return "\n".join(lines)

@mcp.tool()
async def set_session_mode(
    username: str,
    mode: str = "auto",
    reason: str = "",
    source_session: str = "",
) -> str:
    """
    切换当前会话工具模式：chat 交流无工具；readonly 只读；manual 全工具、按策略人工审核；bypass 全工具跳过确认；
    auto 由独立审核模型代审操作。

    :param mode: chat / readonly / manual / auto / bypass (legacy plan / review / agent / execute / yolo accepted)
    :param reason: 切换原因
    """
    session_id = source_session or "default"
    normalized_mode = normalize_session_mode(mode)
    save_session_mode(username, session_id, mode=normalized_mode, reason=reason)
    return (
        f"✅ 已切换到 {normalized_mode} 模式\n"
        f"session_id: {session_id}\n"
        f"reason: {reason or '(none)'}"
    )

@mcp.tool()
async def send_to_session(
    username: str,
    target: str,
    content: str,
    wait: bool = False,
    target_user: str = "",
    source_session: str = "",
    timeout: int = 180,
    summary: str = "",
) -> str:
    """给另一个会话发消息。wait=false 时消息进入对方的持久化收件箱，发出即返回，对方空闲时收到摘要通知、
    按需阅读正文；wait=true 时对方在当前这一轮结束后直接处理正文，并把回复返回给你。

    :param target: 目标会话：子 Agent 的 agent_id / session_id / name，或会话 id；wait=false 时 "*" 表示所有子 Agent 与主会话
    :param content: 消息内容
    :param wait: 是否等待对方回复
    :param target_user: 目标会话属于其他用户时填该用户名；留空为自己
    :param timeout: wait=true 时最多等待的秒数；超时后消息照常处理，回复留在对方会话里
    :param summary: 可选的一句话摘要，用于对方空闲时的收件箱通知；留空时从正文提取短预览
    """
    source_session_id = source_session or "default"
    other_user = (target_user or "").strip()
    to_user = other_user or username
    ref = (target or "").strip()
    if other_user and other_user != username:
        if ref in {"", "*"}:
            return "❌ 发给其他用户时，target 必须是具体的会话 id。"
        targets = [ref]
    else:
        targets = [item["target_session"] for item in _resolve_target_sessions(username, ref, source_session_id)]
    if not targets:
        return "📭 没有可投递的目标会话。"
    if wait and len(targets) > 1:
        return "❌ wait=true 只能发给一个会话。"
    if wait and to_user == username and targets[0] == source_session_id:
        return "❌ 不能等待自己当前会话的回复（会互相等待）；请改用 wait=false。"

    _, source_label = _source_label(username, source_session_id)
    header = f"[来自 {username}#{source_label} 的消息]"
    text = f"{header}\n{content}"
    if wait:
        text += "\n（对方正在等你的回复：直接用文字回答即可。）"

    token = _ensure_internal_token()
    lines = []
    async with httpx.AsyncClient(timeout=max(1, timeout) if wait else 30) as client:
        for session_id in targets:
            try:
                response = await client.post(
                    _SYSTEM_TRIGGER_URL,
                    headers={"X-Internal-Token": token, "Content-Type": "application/json"},
                    json={
                        "user_id": to_user, "session_id": session_id, "text": text,
                        "wait_reply": wait,
                        "inbox_source_user": username,
                        "inbox_source_session": source_session_id,
                        "inbox_source_label": source_label,
                        "inbox_summary": summary,
                    },
                )
            except httpx.TimeoutException:
                return (
                    f"⏰ 等待 {to_user}#{session_id} 回复超时（{timeout}s）。"
                    "对方仍会在当前这一轮结束后处理这条消息，回复留在它的会话里。"
                )
            except httpx.HTTPError as exc:
                lines.append(f"❌ {to_user}#{session_id}: 投递失败: {exc}")
                continue
            if response.status_code != 200:
                lines.append(f"❌ {to_user}#{session_id}: 投递失败 (HTTP {response.status_code}): {response.text[:300]}")
                continue
            if wait:
                if response.json().get("status") != "completed":
                    return f"⏳ {to_user}#{session_id} 的回复还没有拿到，当前处理未完成。"
                reply = str(response.json().get("reply") or "").strip()
                return f"✅ {to_user}#{session_id} 回复:\n\n{reply or '(对方没有给出文字回复)'}"
            lines.append(f"✅ 已入 {to_user}#{session_id} 的收件箱，空闲后处理")
    return "\n".join(lines)


@mcp.tool()
async def read_session_inbox(
    username: str,
    message_ids: list[str] | None = None,
    include_read: bool = False,
    source_session: str = "",
) -> str:
    """阅读当前会话的收件箱正文，并自动将本次读到的消息标记已读。

    :param message_ids: 指定消息 ID；留空时阅读全部未读消息
    :param include_read: 留空读取全部时是否也包含已读消息
    """
    session_id = source_session or "default"
    if message_ids:
        ids = list(dict.fromkeys(message_ids))
        records = [get_inbox_message(username, session_id, mid) for mid in ids]
        missing = [mid for mid, record in zip(ids, records) if record is None]
        records = [record for record in records if record is not None]
    else:
        records = list_inbox_messages(
            username, session_id, status=None if include_read else "unread",
            limit=None, oldest_first=True,
        )
        missing = []
    if not records:
        return "📭 当前会话没有匹配的收件箱消息。" + (f" 未找到：{', '.join(missing)}" if missing else "")
    bodies = []
    for record in records:
        source_user = record.metadata.get("source_user") or username
        sender = record.source_label or record.source_session
        bodies.append(
            f"\n[{record.message_id}] 来自 {source_user}#{sender} · "
            f"{record.created_at}\n{record.content}"
        )
    changed = mark_inbox_read(username, session_id, [record.message_id for record in records])
    lines = [f"📨 收件箱：{len(records)} 条消息（本次自动标记 {changed} 条为已读）", *bodies]
    if missing:
        lines.append(f"\n未找到：{', '.join(missing)}")
    return "\n".join(lines)


@mcp.tool()
async def mark_session_inbox_read(
    username: str,
    message_ids: list[str] | None = None,
    source_session: str = "",
) -> str:
    """直接将当前会话的指定消息标记已读；不传 ID 时标记全部未读。

    :param message_ids: 要标记已读的消息 ID；留空表示全部未读
    """
    session_id = source_session or "default"
    ids = list(dict.fromkeys(message_ids or []))
    missing = [mid for mid in ids if get_inbox_message(username, session_id, mid) is None]
    changed = mark_inbox_read(username, session_id, ids or None)
    remaining = count_inbox_messages(username, session_id, status="unread")
    result = f"✅ 已标记 {changed} 条为已读；当前还剩 {remaining} 条未读。"
    return result + (f" 未找到：{', '.join(missing)}" if missing else "")


if __name__ == "__main__":
    mcp.run()
