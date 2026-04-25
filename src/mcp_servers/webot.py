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
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)


import asyncio
import contextlib
import json
import os
from pathlib import Path
import uuid

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from webot.bridge import get_bridge_runtime_payload, issue_bridge_session
from webot.buddy import apply_buddy_action, serialize_buddy_state
from webot.memory import ensure_memory_state, run_auto_dream, set_kairos_mode
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
    create_inbox_message,
    create_run_record,
    delete_session_plan,
    delete_session_todos,
    get_latest_run_for_agent,
    get_run,
    get_session_mode as load_session_mode,
    get_session_plan,
    get_session_todos,
    heartbeat_run,
    list_inbox_messages,
    list_recoverable_runs,
    list_run_events,
    mark_inbox_delivered,
    list_tool_approvals as list_tool_approval_records,
    list_runs_for_session,
    list_verification_records,
    record_run_event,
    record_runtime_artifact,
    release_run_worker,
    request_run_interrupt,
    save_session_mode,
    save_session_plan,
    save_session_todos,
    save_voice_state,
    update_run_status,
    upsert_run,
    add_verification_record,
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
from webot.voice import get_voice_state as get_voice_runtime_state
from webot.workflow_presets import get_workflow_preset, list_workflow_presets
from webot.workspace import describe_session_workspace

root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(dotenv_path=os.path.join(root_dir, "config", ".env"))

mcp = FastMCP("WeBotAgents")

_AGENT_PORT = os.getenv("PORT_AGENT", "51200")
_INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "")
_AGENT_URL = f"http://127.0.0.1:{_AGENT_PORT}/v1/chat/completions"
_SYSTEM_TRIGGER_URL = f"http://127.0.0.1:{_AGENT_PORT}/system_trigger"
_SESSION_HISTORY_URL = f"http://127.0.0.1:{_AGENT_PORT}/session_history"
_CANCEL_URL = f"http://127.0.0.1:{_AGENT_PORT}/cancel"
_DELETE_SESSION_URL = f"http://127.0.0.1:{_AGENT_PORT}/delete_session"
_SESSION_STATUS_URL = f"http://127.0.0.1:{_AGENT_PORT}/session_status"

_BACKGROUND_TASKS: dict[str, asyncio.Task] = {}
_WORKER_ID = f"webot-mcp:{os.getpid()}:{uuid.uuid4().hex[:8]}"
_RUNTIME_ROOT = Path(root_dir) / "data" / "user_files"
_DEFAULT_ULTRAREVIEW_ANGLES = [
    "security",
    "logic",
    "performance",
    "types",
    "concurrency",
    "error handling",
    "dependencies",
    "testing",
    "api contracts",
    "state management",
    "edge cases",
    "data integrity",
    "observability",
    "refactor safety",
    "filesystem safety",
    "prompt/runtime policy",
    "tool misuse",
    "UX regression",
    "maintainability",
    "deployment/runtime",
]

def _trim(text: str, limit: int = 1200) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... (已截断，原始长度 {len(text)} 字符)"

def _new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"

def _safe_json_loads(value: str | None) -> dict:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}

def _extract_result_field(text: str, field_name: str) -> str:
    prefix = f"{field_name}:"
    for line in (text or "").splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""

def _artifact_dir(user_id: str, session_id: str, bucket: str) -> Path:
    root = _RUNTIME_ROOT / (user_id or "anonymous") / bucket / (session_id or "default")
    root.mkdir(parents=True, exist_ok=True)
    return root

def _write_runtime_text_artifact(
    *,
    user_id: str,
    session_id: str,
    bucket: str,
    name: str,
    content: str,
) -> Path:
    safe_name = slugify(name, "artifact")
    path = _artifact_dir(user_id, session_id, bucket) / f"{safe_name}-{uuid.uuid4().hex[:8]}.md"
    path.write_text(content, encoding="utf-8")
    return path

def _ensure_internal_token() -> str:
    if not _INTERNAL_TOKEN:
        raise RuntimeError("系统未配置 INTERNAL_TOKEN，无法启用 WeBot 子 Agent 调度。")
    return _INTERNAL_TOKEN

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
    token = _ensure_internal_token()
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            _SESSION_STATUS_URL,
            headers={"X-Internal-Token": token, "Content-Type": "application/json"},
            json={"user_id": username, "session_id": session_id, "peek": True},
        )
        if response.status_code != 200:
            return True
        data = response.json()
    return bool(data.get("busy"))

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

async def _deliver_inbox_messages(
    *,
    username: str,
    target_session: str,
    target_agent_id: str = "",
    limit: int = 20,
    force: bool = False,
) -> tuple[int, str]:
    queued_items = list_inbox_messages(username, target_session, status="queued", limit=max(1, limit))
    if not queued_items:
        return 0, ""

    if target_agent_id:
        latest_run = get_latest_run_for_agent(username, target_agent_id)
        if not force and latest_run is not None and latest_run.status in {"queued", "running", "cancelling"}:
            return 0, "busy"
    else:
        if not force and await _peek_session_busy(username, target_session):
            return 0, "busy"

    ordered = list(reversed(queued_items))
    lines = ["[WeBot Session Inbox]"]
    for item in ordered:
        sender = item.source_label or item.source_session or item.source_agent_id or "unknown"
        lines.append(f"from: {sender}\n{item.body}")
    payload_text = "\n\n---\n\n".join(lines)
    await _push_system_message(username=username, session_id=target_session, text=payload_text)
    mark_inbox_delivered(username, [item.message_id for item in ordered])
    artifact_path = _write_runtime_text_artifact(
        user_id=username,
        session_id=target_session,
        bucket="webot_inbox_deliveries",
        name="session-inbox",
        content=payload_text,
    )
    record_runtime_artifact(
        username,
        target_session,
        artifact_kind="session_inbox_delivery",
        title="session_inbox",
        path=str(artifact_path),
        preview=_trim(payload_text, 240),
        metadata={"count": len(ordered)},
    )
    return len(ordered), payload_text

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
        if _active_background_task(run.agent_id) is not None:
            continue
        record = get_subagent(run.agent_id, run.user_id)
        if record is None:
            record_run_event(
                run.user_id,
                run.run_id,
                run.session_id,
                event_type="recover_failed",
                status="failed",
                message="关联子 Agent 已不存在，无法恢复后台运行。",
            )
            update_run_status(
                run.run_id,
                run.user_id,
                status="failed",
                last_error="关联子 Agent 已不存在，无法恢复后台运行。",
                last_result="关联子 Agent 已不存在，无法恢复后台运行。",
            )
            continue
        record_run_event(
            run.user_id,
            run.run_id,
            run.session_id,
            event_type="recovered",
            status=run.status,
            attempt=run.attempt_count,
            message=f"后台运行由 worker {_WORKER_ID} 恢复。",
        )
        _schedule_background_run(
            run_id=run.run_id,
            username=run.user_id,
            agent_id=run.agent_id,
            session_id=run.session_id,
            agent_type=run.agent_type,
            agent_name=record.name,
            content=run.input_text,
            parent_session=run.parent_session,
            timeout=run.timeout_seconds,
            max_turns=run.max_turns,
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
    token = _ensure_internal_token()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            _CANCEL_URL,
            headers={"X-Internal-Token": token, "Content-Type": "application/json"},
            json={"user_id": username, "session_id": session_id},
        )
        if response.status_code != 200:
            raise RuntimeError(f"取消子 Agent 失败 (HTTP {response.status_code}): {response.text[:500]}")
        data = response.json()
    return bool(data.get("cancelled"))


async def _delete_internal_session(
    *,
    username: str,
    session_id: str,
) -> dict:
    token = _ensure_internal_token()
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            _DELETE_SESSION_URL,
            headers={"X-Internal-Token": token, "Content-Type": "application/json"},
            json={"user_id": username, "session_id": session_id},
        )
        if response.status_code != 200:
            raise RuntimeError(f"删除子 Agent session 失败 (HTTP {response.status_code}): {response.text[:500]}")
        return response.json()


@mcp.tool()
async def list_webot_agent_profiles(username: str = "") -> str:
    """
    列出 WeBot 内置子 Agent 类型及其工具能力边界。

    这一步适合在委派任务前先调用，用于选择合适的 agent_type。
    username 由系统自动注入，无需手动传递。
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
    lines.append("\n推荐流程：先选 profile，再用 spawn_subagent 创建或继续一个子 Agent。")
    return "\n".join(lines)

@mcp.tool()
async def list_webot_workflow_presets(username: str = "") -> str:
    presets = list_workflow_presets()
    if not presets:
        return "📭 当前没有可用的 WeBot workflow preset。"
    lines = ["🧭 WeBot workflow presets"]
    for preset in presets:
        lines.append(
            f"- {preset.get('preset_id', '')} · {preset.get('name', '')}\n"
            f"  mode={preset.get('mode', 'execute')} · source={preset.get('source', '')}\n"
            f"  {preset.get('description', '')}"
        )
    lines.append("\n可用 apply_webot_workflow_preset 将 preset 写入当前会话计划和 mode。")
    return "\n".join(lines)

@mcp.tool()
async def apply_webot_workflow_preset(
    username: str,
    preset_id: str,
    source_session: str = "",
) -> str:
    session_id = source_session or "default"
    preset = get_workflow_preset(preset_id)
    if preset is None:
        return f"❌ 未找到 workflow preset: {preset_id}"
    save_session_mode(username, session_id, mode=preset.mode, reason=preset.reason)
    save_session_plan(
        username,
        session_id,
        title=preset.title,
        status="active",
        items=list(preset.items),
        metadata=preset.plan_metadata(),
    )
    return (
        f"✅ 已应用 WeBot workflow preset\n"
        f"session_id: {session_id}\n"
        f"preset_id: {preset.preset_id}\n"
        f"name: {preset.name}\n"
        f"mode: {preset.mode}\n"
        f"items: {len(preset.items)}"
    )

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
    创建或继续一个 WeBot 子 Agent 来处理独立任务。

    适用场景：
    - 调研、规划、实现、审查、验证等可分工的子任务
    - 需要保持独立上下文，而不想把全部中间过程塞回主会话

    Args:
        username: 当前用户（系统自动注入）
        task: 委派给子 Agent 的任务
        agent_type: 子 Agent 类型，如 general/research/planner/coder/reviewer/verifier
        name: 可选名称；若名称已存在，则继续该子 Agent 的既有会话
        description: 对这次子任务的简短描述
        wait: True=同步等待结果；False=后台执行并异步通知父会话
        parent_session: 当前父会话 ID（系统自动注入）
        timeout: 最长等待秒数
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

    if _active_background_task(agent_id) is not None:
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
    update_subagent_status(agent_id, username, status="queued")
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
    列出当前用户已创建的 WeBot 子 Agent 会话。

    username 由系统自动注入，无需手动传递。
    """
    await _recover_background_runs(username)
    records = list_subagents_for_user(username)
    if not records:
        return "📭 当前还没有任何 WeBot 子 Agent。"

    lines = [f"📋 用户 {username} 的子 Agent 列表:\n"]
    for record in records:
        task = _active_background_task(record.agent_id)
        runtime_status = "running" if task else record.status
        latest_run = get_latest_run_for_agent(username, record.agent_id)
        queued_inbox = len(list_inbox_messages(username, record.session_id, status="queued", limit=5))
        session_mode = load_session_mode(username, record.session_id).get("mode")
        if runtime_status in {"queued", "running"} and task is None:
            runtime_status = "stale"
        lines.append(
            f"- {record.name} ({record.agent_id})\n"
            f"  type: {record.agent_type}\n"
            f"  session_id: {record.session_id}\n"
            f"  mode: {session_mode}\n"
            f"  status: {runtime_status}\n"
            f"  workspace: {describe_session_workspace(username, record.session_id, explicit_cwd=record.cwd)}\n"
            f"  latest_run: {latest_run.run_id if latest_run else '(none)'} / {latest_run.status if latest_run else '(none)'}\n"
            f"  inbox: {queued_inbox} queued\n"
            f"  updated_at: {record.updated_at}\n"
            f"  last_result: {_trim(record.last_result, 240) or '(暂无)'}"
        )
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

    agent_ref 可以是 agent_id、session_id，也可以是创建时使用的 name。
    source_session 由系统自动注入，用于后台回调通知。
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

    if _active_background_task(record.agent_id) is not None:
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
    update_subagent_status(record.agent_id, username, status="queued")
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
    """
    await _recover_background_runs(username)
    record = _resolve_subagent_ref(username, agent_ref)
    if record is None:
        return f"❌ 未找到子 Agent: {agent_ref}"

    token = _ensure_internal_token()
    payload = {"user_id": username, "session_id": record.session_id}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            _SESSION_HISTORY_URL,
            headers={"X-Internal-Token": token, "Content-Type": "application/json"},
            json=payload,
        )
        if response.status_code != 200:
            return f"❌ 读取子 Agent 历史失败 (HTTP {response.status_code}): {response.text[:500]}"
        data = response.json()

    messages = data.get("messages") or []
    messages = messages[-max(1, min(limit, 50)) :]
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

    agent_ref 可以是 agent_id、session_id，或创建时指定的 name。
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

    background_task = _active_background_task(record.agent_id)
    had_background_task = background_task is not None
    if background_task is not None:
        background_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await background_task

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

    if not had_background_task and (source_session or updated.parent_session):
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

    agent_ref 可以是 agent_id、session_id，或创建时指定的 name。
    """
    await _recover_background_runs(username)
    record = _resolve_subagent_ref(username, agent_ref)
    if record is None:
        return f"❌ 未找到子 Agent: {agent_ref}"

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

    background_task = _active_background_task(record.agent_id)
    had_background_task = background_task is not None
    if background_task is not None:
        background_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await background_task

    with contextlib.suppress(Exception):
        await _cancel_internal_subagent(username=username, session_id=record.session_id)

    try:
        delete_resp = await _delete_internal_session(username=username, session_id=record.session_id)
    except Exception as exc:
        return f"❌ 删除子 Agent 失败: {exc}"

    # The /delete_session endpoint removes the registry row for subagent sessions.
    # Keep this idempotent cleanup for MCP-only/runtime edge cases.
    registry_deleted = delete_subagent_by_session(username, record.session_id)
    plan_deleted = delete_session_plan(username, record.session_id)
    todos_deleted = delete_session_todos(username, record.session_id)

    if latest_run is not None:
        update_run_status(
            latest_run.run_id,
            username,
            status="cancelled",
            last_result="子 Agent session 已删除。",
            last_error="deleted",
            interrupt_requested=False,
            clear_worker=True,
        )
        record_run_event(
            username,
            latest_run.run_id,
            record.session_id,
            event_type="deleted",
            status="cancelled",
            message=f"子 Agent 已删除: {record.name}",
        )

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
        f"had_background_task: {'yes' if had_background_task else 'no'}\n"
        f"registry_deleted_extra: {registry_deleted}\n"
        f"plan_deleted: {plan_deleted}\n"
        f"todos_deleted: {todos_deleted}\n"
        f"delete_session: {delete_resp.get('message') or delete_resp.get('status') or 'ok'}"
    )


@mcp.tool()
async def write_session_plan(
    username: str,
    title: str,
    items: list[dict] | None = None,
    source_session: str = "",
    status: str = "active",
) -> str:
    session_id = source_session or "default"
    save_session_plan(
        username,
        session_id,
        title=title,
        status=status,
        items=items or [],
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
    session_id = source_session or "default"
    plan = get_session_plan(username, session_id)
    if plan is None:
        return f"📭 当前会话 {session_id} 还没有计划。"
    lines = [
        f"🗺️ 当前计划\nsession_id: {session_id}\ntitle: {plan.get('title', '')}\nstatus: {plan.get('status', 'active')}"
    ]
    for item in plan.get("items", []):
        lines.append(f"- [{item.get('status', 'pending')}] {item.get('step', '')}")
    return "\n".join(lines)

@mcp.tool()
async def clear_session_plan(username: str, source_session: str = "") -> str:
    session_id = source_session or "default"
    deleted = delete_session_plan(username, session_id)
    if deleted:
        return f"🧹 已清除会话计划: {session_id}"
    return f"📭 会话 {session_id} 没有可清除的计划。"

@mcp.tool()
async def write_session_todos(
    username: str,
    items: list[dict] | None = None,
    source_session: str = "",
) -> str:
    session_id = source_session or "default"
    save_session_todos(username, session_id, items=items or [])
    todos = get_session_todos(username, session_id) or {"items": []}
    return f"✅ Todo 已更新\nsession_id: {session_id}\nitems: {len(todos.get('items', []))}"

@mcp.tool()
async def read_session_todos(username: str, source_session: str = "") -> str:
    session_id = source_session or "default"
    todos = get_session_todos(username, session_id)
    if todos is None:
        return f"📭 当前会话 {session_id} 还没有 todo 列表。"
    lines = [f"📌 当前 Todo\nsession_id: {session_id}"]
    for item in todos.get("items", []):
        lines.append(f"- [{item.get('status', 'pending')}] {item.get('step', '')}")
    return "\n".join(lines)

@mcp.tool()
async def clear_session_todos(username: str, source_session: str = "") -> str:
    session_id = source_session or "default"
    deleted = delete_session_todos(username, session_id)
    if deleted:
        return f"🧹 已清除会话 Todo: {session_id}"
    return f"📭 会话 {session_id} 没有可清除的 Todo。"

@mcp.tool()
async def record_verification(
    username: str,
    title: str,
    status: str = "passed",
    details: str = "",
    source_session: str = "",
) -> str:
    session_id = source_session or "default"
    verification_id = f"verify-{uuid.uuid4().hex[:10]}"
    add_verification_record(
        username,
        session_id,
        verification_id=verification_id,
        title=title,
        status=status,
        details=details,
    )
    return (
        f"✅ 已记录验证结果\n"
        f"verification_id: {verification_id}\n"
        f"session_id: {session_id}\n"
        f"status: {status}"
    )

@mcp.tool()
async def list_verifications(username: str, source_session: str = "", limit: int = 10) -> str:
    session_id = source_session or "default"
    items = list_verification_records(username, session_id, limit=max(1, min(limit, 20)))
    if not items:
        return f"📭 当前会话 {session_id} 暂无验证记录。"
    lines = [f"🧪 验证记录\nsession_id: {session_id}"]
    for item in items:
        lines.append(
            f"- {item.get('verification_id', '')} [{item.get('status', '')}] {item.get('title', '')}\n"
            f"  {_trim(item.get('details', ''), 200)}"
        )
    return "\n".join(lines)

@mcp.tool()
async def run_verification(
    username: str,
    task: str,
    context: str = "",
    source_session: str = "",
    timeout: int = 300,
) -> str:
    verification_task = task.strip() or "验证当前实现"
    prompt = verification_task
    if context.strip():
        prompt += f"\n\n需要重点验证的上下文：\n{context.strip()}"
    result = await spawn_subagent(
        username=username,
        task=prompt,
        agent_type="verifier",
        wait=True,
        parent_session=source_session,
        timeout=timeout,
    )
    status = "failed" if result.startswith("❌") else "completed"
    add_verification_record(
        username,
        source_session or "default",
        verification_id=f"verify-{uuid.uuid4().hex[:10]}",
        title=verification_task[:120],
        status=status,
        details=result,
    )
    return result

@mcp.tool()
async def list_tool_approvals(
    username: str,
    source_session: str = "",
    status: str = "pending",
    limit: int = 20,
) -> str:
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
async def enter_plan_mode(
    username: str,
    reason: str = "",
    mode: str = "plan",
    source_session: str = "",
) -> str:
    session_id = source_session or "default"
    normalized_mode = "review" if (mode or "").strip().lower() == "review" else "plan"
    save_session_mode(username, session_id, mode=normalized_mode, reason=reason)
    return (
        f"✅ 已进入 {normalized_mode} 模式\n"
        f"session_id: {session_id}\n"
        f"reason: {reason or '(none)'}"
    )

@mcp.tool()
async def exit_plan_mode(
    username: str,
    reason: str = "",
    source_session: str = "",
) -> str:
    session_id = source_session or "default"
    save_session_mode(username, session_id, mode="execute", reason=reason)
    return (
        f"✅ 已恢复 execute 模式\n"
        f"session_id: {session_id}\n"
        f"reason: {reason or '(none)'}"
    )

@mcp.tool()
async def get_session_mode(
    username: str,
    source_session: str = "",
) -> str:
    session_id = source_session or "default"
    mode_info = load_session_mode(username, session_id)
    return (
        f"🧭 当前会话模式\n"
        f"session_id: {session_id}\n"
        f"mode: {mode_info.get('mode', 'execute')}\n"
        f"reason: {mode_info.get('reason', '') or '(none)'}"
    )

@mcp.tool()
async def session_send_to(
    username: str,
    target_ref: str,
    content: str,
    source_session: str = "",
) -> str:
    source_session_id = source_session or "default"
    source_agent_id, source_label = _source_label(username, source_session_id)
    targets = _resolve_target_sessions(username, target_ref, source_session_id)
    if not targets:
        return "📭 没有可投递的目标会话。"

    created = 0
    delivered = 0
    lines = [
        "📮 Session Inbox 已写入",
        f"source_session: {source_session_id}",
    ]
    for target in targets:
        target_session = target["target_session"]
        target_agent_id = target.get("target_agent_id", "")
        inbox_record = create_inbox_message(
            username,
            target_session=target_session,
            body=content,
            source_session=source_session_id,
            source_agent_id=source_agent_id,
            source_label=source_label,
            target_agent_id=target_agent_id,
            metadata={"target_ref": target_ref},
        )
        created += 1
        delivered_count, state = await _deliver_inbox_messages(
            username=username,
            target_session=target_session,
            target_agent_id=target_agent_id,
            limit=20,
            force=False,
        )
        if delivered_count:
            delivered += delivered_count
            lines.append(f"- {target_session}: delivered_now ({delivered_count})")
        else:
            lines.append(f"- {target_session}: queued ({state or inbox_record.status})")

    lines.append(f"targets: {len(targets)}")
    lines.append(f"messages_created: {created}")
    lines.append(f"messages_delivered_now: {delivered}")
    return "\n".join(lines)

@mcp.tool()
async def session_inbox(
    username: str,
    target_ref: str = "",
    status: str = "queued",
    source_session: str = "",
    limit: int = 20,
) -> str:
    source_session_id = source_session or "default"
    targets = _resolve_target_sessions(username, target_ref or source_session_id, source_session_id)
    if target_ref == "*":
        targets = _resolve_target_sessions(username, "*", source_session_id)
    deduped: dict[str, str] = {}
    for target in targets:
        deduped[target["target_session"]] = target.get("target_agent_id", "")
    if not deduped:
        deduped[source_session_id] = ""

    lines = ["📬 Session Inbox"]
    total = 0
    for session_key in deduped.keys():
        items = list_inbox_messages(
            username,
            session_key,
            status=(status or "").strip().lower() or None,
            limit=max(1, min(limit, 50)),
        )
        if not items:
            continue
        lines.append(f"- session: {session_key}")
        for item in reversed(items):
            sender = item.source_label or item.source_session or item.source_agent_id or "unknown"
            lines.append(
                f"  [{item.status}] {item.message_id} from={sender}\n"
                f"  {_trim(item.body, 240)}"
            )
            total += 1
    if total == 0:
        return "📭 当前没有匹配的 Inbox 消息。"
    lines.append(f"total: {total}")
    return "\n".join(lines)

@mcp.tool()
async def session_deliver_inbox(
    username: str,
    target_ref: str = "",
    source_session: str = "",
    limit: int = 20,
    force: bool = False,
) -> str:
    source_session_id = source_session or "default"
    if target_ref == "*":
        targets = _resolve_target_sessions(username, "*", source_session_id)
        targets.append({"target_session": source_session_id, "target_agent_id": ""})
    else:
        targets = _resolve_target_sessions(username, target_ref or source_session_id, source_session_id)
    deduped: dict[str, str] = {}
    for target in targets:
        deduped[target["target_session"]] = target.get("target_agent_id", "")
    if not deduped:
        return "📭 没有可投递的目标会话。"

    lines = ["🚚 Session Inbox Delivery"]
    delivered_total = 0
    for target_session, target_agent_id in deduped.items():
        delivered_count, state = await _deliver_inbox_messages(
            username=username,
            target_session=target_session,
            target_agent_id=target_agent_id,
            limit=max(1, min(limit, 50)),
            force=force,
        )
        if delivered_count:
            delivered_total += delivered_count
            lines.append(f"- {target_session}: delivered {delivered_count}")
        else:
            lines.append(f"- {target_session}: skipped ({state or 'empty'})")
    lines.append(f"delivered_total: {delivered_total}")
    return "\n".join(lines)

@mcp.tool()
async def claude_session_send_to(
    username: str,
    target_ref: str,
    content: str,
    source_session: str = "",
) -> str:
    return await session_send_to(
        username=username,
        target_ref=target_ref,
        content=content,
        source_session=source_session,
    )

@mcp.tool()
async def claude_session_inbox(
    username: str,
    target_ref: str = "",
    status: str = "queued",
    source_session: str = "",
    limit: int = 20,
) -> str:
    return await session_inbox(
        username=username,
        target_ref=target_ref,
        status=status,
        source_session=source_session,
        limit=limit,
    )

@mcp.tool()
async def claude_session_deliver_inbox(
    username: str,
    target_ref: str = "",
    source_session: str = "",
    limit: int = 20,
    force: bool = False,
) -> str:
    return await session_deliver_inbox(
        username=username,
        target_ref=target_ref,
        source_session=source_session,
        limit=limit,
        force=force,
    )

@mcp.tool()
async def bridge_attach(
    username: str,
    source_session: str = "",
    role: str = "viewer",
    label: str = "",
) -> str:
    session_id = source_session or "default"
    bridge = issue_bridge_session(
        user_id=username,
        session_id=session_id,
        role=role or "viewer",
        label=label,
    )
    return (
        "🌉 Bridge attach 已创建\n"
        f"session_id: {session_id}\n"
        f"bridge_id: {bridge.get('bridge_id', '')}\n"
        f"attach_code: {bridge.get('attach_code', '')}\n"
        f"websocket_path: {bridge.get('websocket_path', '')}"
    )

@mcp.tool()
async def bridge_status(
    username: str,
    source_session: str = "",
) -> str:
    session_id = source_session or "default"
    payload = get_bridge_runtime_payload(username, session_id)
    primary = payload.get("primary") or {}
    return (
        "🌉 Bridge 状态\n"
        f"session_id: {session_id}\n"
        f"status: {payload.get('status', 'detached')}\n"
        f"connection_count: {payload.get('connection_count', 0)}\n"
        f"attach_code: {primary.get('attach_code', '') or '(none)'}"
    )

@mcp.tool()
async def voice_mode(
    username: str,
    enabled: bool = True,
    source_session: str = "",
    auto_read_aloud: bool = False,
) -> str:
    session_id = source_session or "default"
    current = get_voice_runtime_state(username, session_id)
    save_voice_state(
        username,
        session_id,
        enabled=enabled,
        auto_read_aloud=auto_read_aloud,
        recording_supported=True,
        tts_model=str(current.get("tts_model") or ""),
        tts_voice=str(current.get("tts_voice") or ""),
        stt_model=str(current.get("stt_model") or ""),
        last_transcript=str(current.get("last_transcript") or ""),
        metadata={"source": "mcp_tool"},
    )
    updated = get_voice_runtime_state(username, session_id)
    return (
        "🎙️ Voice mode 已更新\n"
        f"session_id: {session_id}\n"
        f"enabled: {updated.get('enabled', False)}\n"
        f"tts: {updated.get('tts_model', '')}:{updated.get('tts_voice', '')}\n"
        f"stt: {updated.get('stt_model', '')}"
    )

@mcp.tool()
async def buddy_status(
    username: str,
) -> str:
    buddy = serialize_buddy_state(username)
    return (
        "🪶 Buddy 状态\n"
        f"name: {buddy.get('name', '')}\n"
        f"species: {buddy.get('species', '')}\n"
        f"rarity: {buddy.get('rarity', '')}\n"
        f"reaction: {buddy.get('reaction', '') or '(none)'}"
    )

@mcp.tool()
async def buddy_action(
    username: str,
    action: str = "pet",
    note: str = "",
) -> str:
    buddy = apply_buddy_action(username, action, note=note)
    return (
        "🪶 Buddy 已响应\n"
        f"name: {buddy.get('name', '')}\n"
        f"action: {(action or 'pet').strip().lower() or 'pet'}\n"
        f"reaction: {buddy.get('reaction', '')}"
    )

@mcp.tool()
async def kairos_mode(
    username: str,
    enabled: bool = True,
    source_session: str = "",
    reason: str = "",
) -> str:
    session_id = source_session or "default"
    set_kairos_mode(username, session_id, enabled, reason=reason)
    memory = ensure_memory_state(username, session_id, kairos_enabled=enabled)
    return (
        "🧠 Kairos 已更新\n"
        f"session_id: {session_id}\n"
        f"enabled: {memory.get('kairos_enabled', False)}\n"
        f"entries: {memory.get('entry_count', 0)}\n"
        f"reason: {reason or '(none)'}"
    )

@mcp.tool()
async def dream_now(
    username: str,
    source_session: str = "",
    force: bool = True,
    reason: str = "manual",
) -> str:
    session_id = source_session or "default"
    result = run_auto_dream(
        username,
        session_id,
        force=force,
        reason=reason,
    )
    state = result.get("state") or {}
    return (
        "💭 Dream 执行结果\n"
        f"session_id: {session_id}\n"
        f"ran: {result.get('ran', False)}\n"
        f"summary_path: {result.get('summary_path', '') or '(none)'}\n"
        f"last_dream_at: {state.get('last_dream_at', '') or '(none)'}"
    )

@mcp.tool()
async def ultraplan_start(
    username: str,
    task: str,
    source_session: str = "",
    name: str = "",
    timeout: int = 1800,
    workspace_mode: str = "worktree",
    workspace_root: str = "",
    cwd: str = "",
    remote: str = "",
) -> str:
    plan_name = slugify(name, "") or f"ultraplan-{uuid.uuid4().hex[:6]}"
    plan_prompt = (
        "你是一个专门负责大范围项目规划的 Planner 子 Agent。\n"
        "请先全面调研，再输出详细实施计划、风险、验证策略和阶段划分。\n"
        "除非绝对必要，不要直接修改文件。\n\n"
        f"任务：\n{task.strip()}"
    )
    result = await spawn_subagent(
        username=username,
        task=plan_prompt,
        agent_type="planner",
        name=plan_name,
        description=f"ULTRAPLAN: {task[:80]}",
        wait=False,
        parent_session=source_session,
        timeout=max(300, timeout),
        max_turns=24,
        workspace_mode=workspace_mode,
        workspace_root=workspace_root,
        cwd=cwd,
        remote=remote,
    )
    run_id = _extract_result_field(result, "run_id")
    session_id = _extract_result_field(result, "session_id")
    if session_id:
        save_session_mode(username, session_id, mode="plan", reason=f"Ultraplan: {task[:120]}")
    if run_id:
        update_run_status(
            run_id,
            username,
            run_kind="ultraplan",
            mode="plan",
            metadata={
                "task": task,
                "source_session": source_session or "default",
                "workspace_mode": workspace_mode,
                "workspace_root": workspace_root,
                "cwd": cwd,
                "remote": remote,
            },
        )
        record_run_event(
            username,
            run_id,
            session_id or "default",
            event_type="ultraplan_started",
            status="queued",
            message=f"ULTRAPLAN 已启动: {plan_name}",
        )
    return (
        "⚡ ULTRAPLAN 已启动\n"
        f"{result}\n\n"
        "可稍后用 ultraplan_status 查询进度。"
    )

@mcp.tool()
async def ultraplan_status(
    username: str,
    run_id: str = "",
    agent_ref: str = "",
    source_session: str = "",
) -> str:
    target_run = None
    if run_id:
        target_run = get_run(run_id, username)
    elif agent_ref:
        record = _resolve_subagent_ref(username, agent_ref)
        if record is not None:
            target_run = get_latest_run_for_agent(username, record.agent_id)
    if target_run is None:
        return "❌ 未找到对应的 ULTRAPLAN 运行。请提供 run_id 或 agent_ref。"

    metadata = _safe_json_loads(target_run.metadata_json)
    if target_run.status == "completed" and target_run.last_result and not metadata.get("artifact_path"):
        artifact_path = _write_runtime_text_artifact(
            user_id=username,
            session_id=target_run.session_id,
            bucket="webot_ultraplan",
            name="ultraplan-result",
            content=target_run.last_result,
        )
        record_runtime_artifact(
            username,
            target_run.session_id,
            run_id=target_run.run_id,
            artifact_kind="ultraplan_result",
            title="ultraplan_result",
            path=str(artifact_path),
            preview=_trim(target_run.last_result, 240),
        )
        metadata["artifact_path"] = str(artifact_path)
        update_run_status(target_run.run_id, username, metadata=metadata)
        target_run = get_run(target_run.run_id, username) or target_run
    events = list_run_events(username, target_run.run_id, limit=10)
    lines = [
        "🛰️ ULTRAPLAN 状态",
        f"run_id: {target_run.run_id}",
        f"session_id: {target_run.session_id}",
        f"status: {target_run.status}",
        f"mode: {target_run.mode}",
        f"attempt_count: {target_run.attempt_count}",
    ]
    if metadata.get("artifact_path"):
        lines.append(f"artifact: {metadata['artifact_path']}")
    if target_run.last_result:
        lines.append(f"result:\n{_trim(target_run.last_result, 2000)}")
    if events:
        lines.append("events:")
        for event in events:
            lines.append(
                f"- [{event.get('status') or event.get('event_type')}] {event.get('event_type')} :: {event.get('message')}"
            )
    return "\n".join(lines)

@mcp.tool()
async def ultrareview_start(
    username: str,
    target: str,
    agent_count: int = 8,
    source_session: str = "",
    timeout: int = 600,
    workspace_mode: str = "worktree",
    workspace_root: str = "",
    cwd: str = "",
    remote: str = "",
    angles: list[str] | None = None,
) -> str:
    review_source_session = source_session or "default"
    requested_angles = [str(angle).strip() for angle in (angles or []) if str(angle).strip()]
    selected_angles = requested_angles or list(_DEFAULT_ULTRAREVIEW_ANGLES)
    selected_angles = selected_angles[: max(5, min(agent_count, 20))]

    coordinator_run_id = _new_run_id()
    coordinator_run = create_run_record(
        run_id=coordinator_run_id,
        user_id=username,
        agent_id=f"ultrareview-{coordinator_run_id[-6:]}",
        session_id=review_source_session,
        parent_session=review_source_session,
        agent_type="reviewer",
        title=f"ULTRAREVIEW: {target[:80]}",
        input_text=target,
        status="running",
        timeout_seconds=max(120, timeout),
        max_turns=None,
        wait_mode=False,
        run_kind="ultrareview",
        mode="review",
        metadata={
            "target": target,
            "angles": selected_angles,
            "child_runs": [],
            "source_session": review_source_session,
        },
    )
    upsert_run(coordinator_run)
    record_run_event(
        username,
        coordinator_run_id,
        review_source_session,
        event_type="ultrareview_started",
        status="running",
        message=f"ULTRAREVIEW 已启动，角度数={len(selected_angles)}",
    )

    child_runs: list[dict[str, str]] = []
    lines = [
        "🧪 ULTRAREVIEW 已启动",
        f"run_id: {coordinator_run_id}",
        f"target: {target}",
        f"reviewers: {len(selected_angles)}",
    ]
    for angle in selected_angles:
        reviewer_name = slugify(f"review-{coordinator_run_id[-4:]}-{angle}", "") or f"review-{uuid.uuid4().hex[:6]}"
        reviewer_prompt = (
            f"你是一个专门从 `{angle}` 角度审查代码/实现的 Reviewer 子 Agent。\n"
            "请优先给出 findings，而不是泛泛总结。\n"
            "重点关注 bug、风险、回归、缺失测试和不合理假设。\n"
            "除非用户明确要求，不要修改文件。\n\n"
            f"审查目标：\n{target}"
        )
        spawn_result = await spawn_subagent(
            username=username,
            task=reviewer_prompt,
            agent_type="reviewer",
            name=reviewer_name,
            description=f"ULTRAREVIEW::{angle}",
            wait=False,
            parent_session=review_source_session,
            timeout=max(120, timeout),
            max_turns=18,
            workspace_mode=workspace_mode,
            workspace_root=workspace_root,
            cwd=cwd,
            remote=remote,
        )
        child_run_id = _extract_result_field(spawn_result, "run_id")
        child_session_id = _extract_result_field(spawn_result, "session_id")
        child_agent_id = _extract_result_field(spawn_result, "agent_id")
        if child_session_id:
            save_session_mode(username, child_session_id, mode="review", reason=f"Ultrareview::{angle}")
        if child_run_id:
            update_run_status(
                child_run_id,
                username,
                run_kind="ultrareview_reviewer",
                mode="review",
                parent_run_id=coordinator_run_id,
                metadata={
                    "angle": angle,
                    "target": target,
                    "coordinator_run_id": coordinator_run_id,
                    "source_session": review_source_session,
                },
            )
        child_runs.append(
            {
                "run_id": child_run_id,
                "session_id": child_session_id,
                "agent_id": child_agent_id,
                "angle": angle,
            }
        )
        lines.append(f"- {angle}: {child_run_id or '(missing run_id)'}")

    update_run_status(
        coordinator_run_id,
        username,
        metadata={
            "target": target,
            "angles": selected_angles,
            "child_runs": child_runs,
            "source_session": review_source_session,
        },
        status="running" if child_runs else "failed",
        last_error="" if child_runs else "未能启动任何 reviewer 子 Agent。",
    )
    if not child_runs:
        record_run_event(
            username,
            coordinator_run_id,
            review_source_session,
            event_type="ultrareview_failed",
            status="failed",
            message="未能启动任何 reviewer 子 Agent。",
        )
    return "\n".join(lines)

@mcp.tool()
async def ultrareview_status(
    username: str,
    run_id: str,
    source_session: str = "",
) -> str:
    coordinator = get_run(run_id, username)
    if coordinator is None:
        return f"❌ 未找到 ULTRAREVIEW 运行: {run_id}"

    metadata = _safe_json_loads(coordinator.metadata_json)
    child_runs = metadata.get("child_runs") or []
    if not isinstance(child_runs, list):
        child_runs = []

    active = 0
    failed = 0
    completed = 0
    findings: list[str] = []
    lines = [
        "🔎 ULTRAREVIEW 状态",
        f"run_id: {coordinator.run_id}",
        f"target: {metadata.get('target', coordinator.input_text)}",
        f"coordinator_status: {coordinator.status}",
    ]
    for child in child_runs:
        if not isinstance(child, dict):
            continue
        child_run_id = str(child.get("run_id") or "").strip()
        angle = str(child.get("angle") or "review").strip()
        if not child_run_id:
            lines.append(f"- {angle}: missing child run id")
            failed += 1
            continue
        child_run = get_run(child_run_id, username)
        if child_run is None:
            lines.append(f"- {angle}: missing")
            failed += 1
            continue
        lines.append(f"- {angle}: {child_run.status} ({child_run.run_id})")
        if child_run.status in {"queued", "running", "cancelling"}:
            active += 1
        elif child_run.status == "completed":
            completed += 1
            if child_run.last_result:
                findings.append(f"## {angle}\n\n{child_run.last_result}")
        else:
            failed += 1
            if child_run.last_result:
                findings.append(f"## {angle}\n\n{child_run.last_result}")

    overall_status = "running" if active else ("failed" if completed == 0 and failed else "completed")
    summary_text = (
        f"completed={completed}, failed={failed}, active={active}\n\n"
        + ("\n\n---\n\n".join(findings) if findings else "暂无 reviewer 结果。")
    )
    if overall_status != coordinator.status:
        metadata["final_status"] = overall_status
        update_run_status(
            coordinator.run_id,
            username,
            status=overall_status,
            last_result=summary_text if overall_status != "running" else coordinator.last_result,
            metadata=metadata,
        )
        coordinator = get_run(coordinator.run_id, username) or coordinator
    if overall_status != "running" and summary_text and not metadata.get("artifact_path"):
        artifact_path = _write_runtime_text_artifact(
            user_id=username,
            session_id=source_session or coordinator.session_id,
            bucket="webot_ultrareview",
            name="ultrareview-summary",
            content=summary_text,
        )
        record_runtime_artifact(
            username,
            source_session or coordinator.session_id,
            run_id=coordinator.run_id,
            artifact_kind="ultrareview_summary",
            title="ultrareview_summary",
            path=str(artifact_path),
            preview=_trim(summary_text, 240),
        )
        metadata["artifact_path"] = str(artifact_path)
        update_run_status(coordinator.run_id, username, metadata=metadata)
        lines.append(f"artifact: {artifact_path}")
    lines.append(f"completed: {completed}")
    lines.append(f"failed: {failed}")
    lines.append(f"active: {active}")
    if summary_text:
        lines.append(f"summary:\n{_trim(summary_text, 2200)}")
    return "\n".join(lines)

if __name__ == "__main__":
    mcp.run()
