from __future__ import annotations

import httpx
import json
import os
from typing import Any, Callable

from fastapi import HTTPException

from services.llm_factory import get_provider_audio_defaults, infer_provider
from webot.bridge import bridge_hub, get_bridge_runtime_payload, issue_bridge_session, serialize_bridge_record
from webot.buddy import apply_buddy_action, serialize_buddy_state
from webot.memory import ensure_memory_state, run_auto_dream
from webot.models import (
    WeBotApprovalResolutionRequest,
    WeBotBridgeAttachRequest,
    WeBotBridgeDetachRequest,
    WeBotBuddyActionRequest,
    WeBotClaudeKeepaliveUpdateRequest,
    WeBotClaudeKickoffRequest,
    WeBotClaudeProbeRequest,
    WeBotDreamRequest,
    WeBotGoalHeartbeatRequest,
    WeBotGoalUpdateRequest,
    WeBotKairosUpdateRequest,
    WeBotLspRequest,
    WeBotPlanUpdateRequest,
    WeBotRunInterruptRequest,
    WeBotSessionInboxDeliverRequest,
    WeBotSessionInboxListRequest,
    WeBotSessionInboxSendRequest,
    WeBotSessionModeUpdateRequest,
    WeBotSessionRuntimeRequest,
    WeBotSubagentHistoryRequest,
    WeBotSubagentRefRequest,
    WeBotTodoUpdateRequest,
    WeBotToolPolicyUpdateRequest,
    WeBotVerificationCreateRequest,
    WeBotVoiceStateUpdateRequest,
    WeBotWorkflowPresetApplyRequest,
)
from webot.claude_code import detect_claude_code_cached, probe_claude_acp, run_claude_cli_prompt
from webot.lsp import probe_diagnostics
from webot.permission_context import resolve_permission_request
from webot.policy import get_tool_policy, save_tool_policy_config, serialize_tool_policy
from webot.profiles import slugify
from webot.runtime_store import (
    add_verification_record,
    count_inbox_messages,
    create_inbox_message,
    create_runtime_artifact,
    delete_session_plan,
    delete_session_todos,
    get_bridge_session,
    get_claude_keepalive_state,
    get_latest_active_run_for_session,
    get_latest_run_for_agent,
    get_memory_state,
    get_session_mode,
    get_session_plan,
    get_session_todos,
    get_voice_state,
    list_inbox_messages,
    list_run_events,
    list_runs_for_session,
    list_runtime_artifacts,
    list_tool_approvals,
    list_verification_records,
    list_bridge_sessions,
    mark_inbox_delivered,
    list_session_goals,
    request_run_interrupt,
    record_claude_keepalive_result,
    record_goal_heartbeat,
    save_memory_state,
    save_claude_keepalive_state,
    save_session_mode,
    save_session_plan,
    save_session_todos,
    save_voice_state,
    update_run_status,
    upsert_session_goal,
    upsert_bridge_session,
)
from webot.subagents import (
    SubagentRecord,
    get_subagent,
    get_subagent_by_name,
    get_subagent_by_session,
    list_subagents_for_parent_session,
    list_subagents_for_user,
    update_subagent_status,
)
from webot.workflow_presets import (
    build_run_recovery_hint,
    get_workflow_preset,
    list_workflow_presets,
)
from webot.workspace import describe_session_workspace


def _safe_json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


class WeBotService:
    """Runtime APIs for WeBot subagent registry, runtime visibility, and tool policy."""

    def __init__(
        self,
        *,
        agent: Any,
        verify_auth_or_token: Callable[[str, str, str | None], None],
        extract_text: Callable[[Any], str],
    ):
        self.agent = agent
        self.verify_auth_or_token = verify_auth_or_token
        self.extract_text = extract_text

    @staticmethod
    def _resolve_subagent_ref(user_id: str, agent_ref: str) -> SubagentRecord | None:
        ref = (agent_ref or "").strip()
        if not ref:
            return None

        record = get_subagent(ref, user_id)
        if record is not None:
            return record

        record = get_subagent_by_session(ref, user_id)
        if record is not None:
            return record

        record = get_subagent_by_name(ref, user_id)
        if record is not None:
            return record

        normalized = slugify(ref, "")
        if normalized and normalized != ref:
            return get_subagent_by_name(normalized, user_id)
        return None

    def _runtime_status_for(self, user_id: str, record: SubagentRecord) -> str:
        thread_id = f"{user_id}#{record.session_id}"
        status_map = self.agent.get_all_thread_status(thread_id)
        active_keys = set(self.agent.list_active_task_keys(thread_id))
        if thread_id in active_keys:
            return "running"
        info = status_map.get(thread_id)
        if info and info.get("busy"):
            return "running"
        latest_active = get_latest_active_run_for_session(user_id, record.session_id)
        if latest_active is not None:
            return latest_active.status
        if record.status in {"queued", "running", "cancelling"}:
            return "stale"
        return record.status

    @staticmethod
    def _serialize_inbox(item) -> dict[str, Any]:
        return {
            "message_id": item.message_id,
            "source_session": item.source_session,
            "source_agent_id": item.source_agent_id,
            "source_label": item.source_label,
            "target_session": item.target_session,
            "target_agent_id": item.target_agent_id,
            "body": item.body,
            "status": item.status,
            "metadata": _safe_json_loads(item.metadata_json),
            "created_at": item.created_at,
            "delivered_at": item.delivered_at,
            "updated_at": item.updated_at,
        }

    @staticmethod
    def _serialize_artifact(item) -> dict[str, Any]:
        return {
            "artifact_id": item.artifact_id,
            "run_id": item.run_id,
            "artifact_kind": item.artifact_kind,
            "title": item.title,
            "path": item.path,
            "preview": item.preview,
            "metadata": _safe_json_loads(item.metadata_json),
            "created_at": item.created_at,
        }

    @staticmethod
    def _serialize_goal(item) -> dict[str, Any]:
        budget = {
            "tokens": {"limit": item.budget_tokens, "spent": item.spent_tokens},
            "usd": {"limit": item.budget_usd, "spent": item.spent_usd},
        }
        return {
            "goal_id": item.goal_id,
            "session_id": item.session_id,
            "title": item.title,
            "description": item.description,
            "status": item.status,
            "priority": item.priority,
            "parent_goal_id": item.parent_goal_id,
            "owner_session": item.owner_session,
            "metrics": dict(item.metrics),
            "budget": budget,
            "budget_tokens": item.budget_tokens,
            "spent_tokens": item.spent_tokens,
            "budget_usd": item.budget_usd,
            "spent_usd": item.spent_usd,
            "heartbeat_status": item.heartbeat_status,
            "heartbeat_at": item.heartbeat_at,
            "last_report": item.last_report,
            "metadata": dict(item.metadata),
            "updated_at": item.updated_at,
            "created_at": item.created_at,
        }

    @staticmethod
    def _serialize_claude_keepalive(item) -> dict[str, Any]:
        return {
            "enabled": item.enabled,
            "tool": item.tool,
            "prompt": item.prompt,
            "model": item.model,
            "timezone": item.timezone,
            "start_time": item.start_time,
            "sleep_time": item.sleep_time,
            "weekdays": item.weekdays,
            "use_caffeinate": item.use_caffeinate,
            "force_sleep_at_quiet_hours": item.force_sleep_at_quiet_hours,
            "monitor_command": item.monitor_command,
            "timeout_seconds": item.timeout_seconds,
            "next_run_at": item.next_run_at,
            "last_run_at": item.last_run_at,
            "last_status": item.last_status,
            "last_error": item.last_error,
            "last_result": item.last_result,
            "reset_at": item.reset_at,
            "metadata": dict(item.metadata),
            "updated_at": item.updated_at,
            "created_at": item.created_at,
        }

    def _serialize_run(self, user_id: str, item, *, include_events: bool = True) -> dict[str, Any]:
        events = list_run_events(user_id, item.run_id, limit=8) if include_events else []
        payload = {
            "run_id": item.run_id,
            "agent_id": item.agent_id,
            "session_id": item.session_id,
            "parent_session": item.parent_session,
            "parent_run_id": item.parent_run_id,
            "agent_type": item.agent_type,
            "run_kind": item.run_kind,
            "mode": item.mode,
            "title": item.title,
            "status": item.status,
            "timeout_seconds": item.timeout_seconds,
            "max_turns": item.max_turns,
            "wait_mode": item.wait_mode,
            "attempt_count": item.attempt_count,
            "worker_id": item.worker_id,
            "lease_expires_at": item.lease_expires_at,
            "heartbeat_at": item.heartbeat_at,
            "interrupt_requested": item.interrupt_requested,
            "last_error": item.last_error,
            "last_result": item.last_result,
            "metadata": _safe_json_loads(item.metadata_json),
            "recovery": build_run_recovery_hint(
                status=item.status,
                last_error=item.last_error,
                last_result=item.last_result,
                interrupt_requested=item.interrupt_requested,
                events=events,
            ),
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }
        if include_events:
            payload["events"] = events
        return payload

    def _serialize_subagent_card(self, user_id: str, record: SubagentRecord) -> dict[str, Any]:
        latest_run = get_latest_run_for_agent(user_id, record.agent_id)
        return {
            "agent_id": record.agent_id,
            "name": record.name,
            "session_id": record.session_id,
            "agent_type": record.agent_type,
            "description": record.description,
            "parent_session": record.parent_session,
            "status": self._runtime_status_for(user_id, record),
            "stored_status": record.status,
            "updated_at": record.updated_at,
            "created_at": record.created_at,
            "last_result": record.last_result,
            "workspace": describe_session_workspace(user_id, record.session_id, explicit_cwd=record.cwd),
            "workspace_mode": record.workspace_mode,
            "workspace_root": record.workspace_root,
            "cwd": record.cwd,
            "remote": record.remote,
            "session_mode": get_session_mode(user_id, record.session_id),
            "queued_inbox_count": count_inbox_messages(user_id, record.session_id, status="queued"),
            "latest_run": None if latest_run is None else self._serialize_run(user_id, latest_run, include_events=False),
        }

    @staticmethod
    def _agent_base_url() -> str:
        return f"http://127.0.0.1:{os.getenv('PORT_AGENT', '51200')}"

    @staticmethod
    def _internal_headers() -> dict[str, str]:
        token = (os.getenv("INTERNAL_TOKEN", "") or "").strip()
        if not token:
            raise HTTPException(status_code=500, detail="INTERNAL_TOKEN 未配置，无法执行 WeBot 控制面操作。")
        return {
            "X-Internal-Token": token,
            "Content-Type": "application/json",
        }

    def _source_label(self, user_id: str, source_session: str) -> tuple[str, str]:
        record = get_subagent_by_session(source_session, user_id) if source_session else None
        if record is not None:
            return record.agent_id, record.name or record.agent_id
        return "", source_session or user_id

    def _resolve_target_sessions(
        self,
        user_id: str,
        target_ref: str,
        source_session: str,
    ) -> list[dict[str, str]]:
        normalized_ref = (target_ref or "").strip()
        if normalized_ref == "*":
            targets = [
                {
                    "target_session": record.session_id,
                    "target_agent_id": record.agent_id,
                }
                for record in list_subagents_for_user(user_id)
                if record.session_id != source_session
            ]
            if source_session != "default":
                targets.append({"target_session": "default", "target_agent_id": ""})
            return targets

        target_record = self._resolve_subagent_ref(user_id, normalized_ref)
        if target_record is not None:
            return [{"target_session": target_record.session_id, "target_agent_id": target_record.agent_id}]
        return [{"target_session": normalized_ref or source_session or "default", "target_agent_id": ""}]

    async def _peek_session_busy(self, user_id: str, session_id: str) -> bool:
        thread_id = f"{user_id}#{session_id}"
        return bool(self.agent.is_thread_busy(thread_id))

    async def _push_system_message(self, *, user_id: str, session_id: str, text: str, timeout: int = 30) -> None:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self._agent_base_url()}/system_trigger",
                headers=self._internal_headers(),
                json={"user_id": user_id, "session_id": session_id, "text": text},
            )
            response.raise_for_status()

    async def _deliver_inbox_messages(
        self,
        *,
        user_id: str,
        target_session: str,
        target_agent_id: str = "",
        limit: int = 20,
        force: bool = False,
    ) -> tuple[int, str]:
        queued_items = list_inbox_messages(user_id, target_session, status="queued", limit=max(1, min(limit, 50)))
        if not queued_items:
            return 0, "empty"
        if not force and await self._peek_session_busy(user_id, target_session):
            return 0, "busy"

        ordered = list(reversed(queued_items))
        lines = [
            "[Session Inbox Delivery]",
            "You have queued cross-session messages. Fold them into the current task if relevant.",
            "",
        ]
        for item in ordered:
            sender = item.source_label or item.source_session or item.source_agent_id or "unknown"
            lines.append(f"- From {sender}: {item.body}")

        await self._push_system_message(
            user_id=user_id,
            session_id=target_session,
            text="\n".join(lines),
        )
        delivered_count = mark_inbox_delivered(user_id, [item.message_id for item in ordered])
        create_runtime_artifact(
            user_id=user_id,
            session_id=target_session,
            kind="session_inbox_delivery",
            title="session_inbox",
            summary=f"Delivered {delivered_count} queued inbox message(s).",
            metadata={
                "target_session": target_session,
                "target_agent_id": target_agent_id,
                "message_ids": [item.message_id for item in ordered],
                "force": force,
            },
        )
        return delivered_count, "delivered"

    @staticmethod
    def _voice_defaults() -> dict[str, str]:
        api_key = os.getenv("LLM_API_KEY", "")
        base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
        provider = infer_provider(
            model=os.getenv("LLM_MODEL", ""),
            base_url=base_url,
            provider=os.getenv("LLM_PROVIDER", ""),
            api_key=api_key,
        )
        return get_provider_audio_defaults(provider)

    def _serialize_voice_payload(self, user_id: str, session_id: str) -> dict[str, Any]:
        defaults = self._voice_defaults()
        state = get_voice_state(user_id, session_id)
        return {
            "enabled": state.enabled,
            "auto_read_aloud": state.auto_read_aloud,
            "recording_supported": state.recording_supported,
            "tts_model": state.tts_model or defaults.get("tts_model", ""),
            "tts_voice": state.tts_voice or defaults.get("tts_voice", ""),
            "stt_model": state.stt_model or defaults.get("stt_model", ""),
            "last_transcript": state.last_transcript,
            "status": "enabled" if state.enabled else "disabled",
            "tts_available": bool(state.tts_model or defaults.get("tts_model", "")),
            "metadata": dict(state.metadata),
            "updated_at": state.updated_at,
        }

    def _serialize_memory_payload(self, user_id: str, session_id: str) -> dict[str, Any]:
        state = ensure_memory_state(user_id, session_id)
        summary_parts = [f"{int(state.get('entry_count', 0))} entries"]
        summary_parts.append("kairos on" if state.get("kairos_enabled") else "kairos off")
        if state.get("last_dream_at"):
            summary_parts.append(f"last dream {str(state.get('last_dream_at'))[:16]}")
        state["summary"] = " · ".join(summary_parts)
        return state

    @staticmethod
    def _active_workflow_payload(plan: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(plan, dict):
            return None
        metadata = plan.get("metadata") or {}
        if not isinstance(metadata, dict):
            return None
        workflow = metadata.get("workflow") or {}
        if not isinstance(workflow, dict) or not workflow.get("preset_id"):
            return None
        return workflow

    def _serialize_session_runtime(self, user_id: str, session_id: str) -> dict[str, Any]:
        record = get_subagent_by_session(session_id, user_id)
        runtime_runs = list_runs_for_session(user_id, session_id, limit=10)
        runtime_mode = get_session_mode(user_id, session_id)
        plan = get_session_plan(user_id, session_id)
        inbox_items = list_inbox_messages(user_id, session_id, limit=20)
        artifacts = list_runtime_artifacts(user_id, session_id, limit=20)
        goals = list_session_goals(user_id, session_id, limit=20)
        active_goals = [goal for goal in goals if goal.status == "active"]
        keepalive = get_claude_keepalive_state(user_id, session_id)
        approvals = [
            {
                "approval_id": approval.approval_id,
                "tool_name": approval.tool_name,
                "status": approval.status,
                "request_reason": approval.request_reason,
                "created_at": approval.created_at,
            }
            for approval in list_tool_approvals(user_id, session_id, limit=20)
        ]
        memory = self._serialize_memory_payload(user_id, session_id)
        bridge = get_bridge_runtime_payload(user_id, session_id)
        voice = self._serialize_voice_payload(user_id, session_id)
        buddy = serialize_buddy_state(user_id)
        return {
            "status": "success",
            "session_id": session_id,
            "session_role": "subagent" if record is not None else "main",
            "workspace": describe_session_workspace(
                user_id,
                session_id,
                explicit_cwd=record.cwd if record is not None else "",
            ),
            "mode": runtime_mode,
            "plan": plan,
            "goals": {
                "items": [self._serialize_goal(item) for item in goals],
                "active_goal": self._serialize_goal(active_goals[0]) if active_goals else None,
                "active_count": len(active_goals),
            },
            "workflow_presets": list_workflow_presets(),
            "active_workflow": self._active_workflow_payload(plan),
            "todos": get_session_todos(user_id, session_id),
            "verifications": list_verification_records(user_id, session_id, limit=20),
            "approvals": approvals,
            "inbox": [self._serialize_inbox(item) for item in inbox_items],
            "artifacts": [self._serialize_artifact(item) for item in artifacts],
            "runs": [self._serialize_run(user_id, item) for item in runtime_runs],
            "active_run": None if not runtime_runs else self._serialize_run(user_id, runtime_runs[0]),
            "relationships": {
                "parent_session": record.parent_session if record is not None else "",
                "children": [
                    self._serialize_subagent_card(user_id, child)
                    for child in list_subagents_for_parent_session(user_id, session_id, limit=20)
                ],
            },
            "subagent": None
            if record is None
            else {
                "agent_id": record.agent_id,
                "name": record.name,
                "agent_type": record.agent_type,
                "description": record.description,
                "workspace_mode": record.workspace_mode,
                "workspace_root": record.workspace_root,
                "cwd": record.cwd,
                "remote": record.remote,
                "status": self._runtime_status_for(user_id, record),
                "stored_status": record.status,
                "updated_at": record.updated_at,
                "created_at": record.created_at,
            },
            "memory": memory,
            "bridge": bridge,
            "voice": voice,
            "buddy": buddy,
            "claude_code": {
                "status": detect_claude_code_cached(ttl_seconds=60),
                "keepalive": self._serialize_claude_keepalive(keepalive),
            },
        }

    async def _publish_runtime_snapshot(
        self,
        user_id: str,
        session_id: str,
        *,
        reason: str,
        event_type: str = "runtime_update",
        changed_session_id: str = "",
    ) -> None:
        if not session_id:
            return
        targets = {session_id}
        record = get_subagent_by_session(session_id, user_id)
        if record is not None and record.parent_session:
            targets.add(record.parent_session)
        effective_changed_session = changed_session_id or session_id
        for target_session in targets:
            runtime = self._serialize_session_runtime(user_id, target_session)
            await bridge_hub.publish_to_session(
                user_id,
                target_session,
                {
                    "type": event_type,
                    "reason": reason,
                    "session_id": target_session,
                    "changed_session_id": effective_changed_session,
                    "runtime": runtime,
                },
            )

    async def _publish_runtime_snapshots(
        self,
        user_id: str,
        session_ids: list[str],
        *,
        reason: str,
        event_type: str = "runtime_update",
        changed_session_id: str = "",
    ) -> None:
        seen: set[str] = set()
        for session_id in session_ids:
            if not session_id or session_id in seen:
                continue
            seen.add(session_id)
            await self._publish_runtime_snapshot(
                user_id,
                session_id,
                reason=reason,
                event_type=event_type,
                changed_session_id=changed_session_id or session_id,
            )

    async def list_subagents(self, user_id: str, password: str, x_internal_token: str | None):
        self.verify_auth_or_token(user_id, password, x_internal_token)
        records = list_subagents_for_user(user_id)
        return {
            "status": "success",
            "subagents": [self._serialize_subagent_card(user_id, record) for record in records],
        }

    async def get_subagent_history(
        self,
        req: WeBotSubagentHistoryRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        record = self._resolve_subagent_ref(req.user_id, req.agent_ref)
        if record is None:
            raise HTTPException(status_code=404, detail=f"未找到子 Agent: {req.agent_ref}")

        thread_id = f"{req.user_id}#{record.session_id}"
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self.agent.agent_app.aget_state(config)
        messages = snapshot.values.get("messages", []) if snapshot and snapshot.values else []
        limit = max(1, min(req.limit, 50))
        result = []
        for msg in messages[-limit:]:
            msg_type = type(msg).__name__
            if msg_type == "HumanMessage":
                result.append({"role": "user", "content": msg.content})
            elif msg_type == "AIMessage":
                content = self.extract_text(msg.content)
                entry = {"role": "assistant", "content": content}
                if getattr(msg, "tool_calls", None):
                    entry["tool_calls"] = [
                        {
                            "name": tool_call.get("name", ""),
                            "args": tool_call.get("args", {}),
                        }
                        for tool_call in msg.tool_calls
                    ]
                result.append(entry)
            elif msg_type == "ToolMessage":
                result.append(
                    {
                        "role": "tool",
                        "content": self.extract_text(msg.content),
                        "tool_name": getattr(msg, "name", ""),
                    }
                )

        return {
            "status": "success",
            "subagent": self._serialize_subagent_card(req.user_id, record),
            "messages": result,
        }

    async def cancel_subagent(
        self,
        req: WeBotSubagentRefRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        record = self._resolve_subagent_ref(req.user_id, req.agent_ref)
        if record is None:
            raise HTTPException(status_code=404, detail=f"未找到子 Agent: {req.agent_ref}")

        latest_run = get_latest_run_for_agent(req.user_id, record.agent_id)
        if latest_run is not None and latest_run.status in {"queued", "running", "cancelling"}:
            request_run_interrupt(latest_run.run_id, req.user_id)

        task_key = f"{req.user_id}#{record.session_id}"
        cancelled = await self.agent.cancel_task(task_key)
        if cancelled or record.status not in {"completed", "failed", "cancelled"}:
            record = update_subagent_status(
                record.agent_id,
                req.user_id,
                status="cancelled",
                last_result="子 Agent 已通过运行时控制面板取消。",
            ) or record
            if latest_run is not None:
                update_run_status(
                    latest_run.run_id,
                    req.user_id,
                    status="cancelled",
                    last_result="子 Agent 已通过运行时控制面板取消。",
                    last_error="cancelled",
                    interrupt_requested=False,
                    clear_worker=True,
                )
        await self._publish_runtime_snapshot(
            req.user_id,
            record.session_id,
            reason="cancel_subagent",
        )

        return {
            "status": "success",
            "cancelled": cancelled,
            "subagent": {
                "agent_id": record.agent_id,
                "name": record.name,
                "session_id": record.session_id,
                "agent_type": record.agent_type,
                "status": self._runtime_status_for(req.user_id, record),
                "stored_status": record.status,
                "updated_at": record.updated_at,
                "last_result": record.last_result,
            },
        }

    async def get_tool_policy(self, user_id: str, password: str, x_internal_token: str | None):
        self.verify_auth_or_token(user_id, password, x_internal_token)
        policy = get_tool_policy(user_id)
        return {"status": "success", "policy": serialize_tool_policy(policy)}

    async def list_tool_approvals(
        self,
        user_id: str,
        password: str,
        status: str,
        session_id: str,
        limit: int,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(user_id, password, x_internal_token)
        approvals = [
            {
                "approval_id": approval.approval_id,
                "session_id": approval.session_id,
                "tool_name": approval.tool_name,
                "status": approval.status,
                "request_reason": approval.request_reason,
                "created_at": approval.created_at,
            }
            for approval in list_tool_approvals(
                user_id,
                session_id or None,
                status=(status or "").strip().lower() or None,
                limit=max(1, min(limit or 20, 100)),
            )
        ]
        return {"status": "success", "approvals": approvals}

    async def update_tool_policy(
        self,
        req: WeBotToolPolicyUpdateRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        path = save_tool_policy_config(req.user_id, req.policy or {})
        policy = get_tool_policy(req.user_id)
        return {
            "status": "success",
            "definition_path": str(path),
            "policy": serialize_tool_policy(policy),
        }

    async def get_session_runtime(
        self,
        user_id: str,
        session_id: str,
        password: str,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(user_id, password, x_internal_token)
        return self._serialize_session_runtime(user_id, session_id)

    async def update_session_mode(
        self,
        req: WeBotSessionModeUpdateRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        save_session_mode(
            req.user_id,
            req.session_id,
            mode=req.mode,
            reason=req.reason,
        )
        mode = get_session_mode(req.user_id, req.session_id)
        await self._publish_runtime_snapshot(
            req.user_id,
            req.session_id,
            reason="session_mode",
        )
        return {
            "status": "success",
            "session_id": req.session_id,
            "mode": mode,
        }

    async def run_lsp(
        self,
        req: WeBotLspRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        normalized_op = (req.op or "diagnostics").strip().lower()
        if normalized_op != "diagnostics":
            if normalized_op == "rename" and not (req.new_name or "").strip():
                raise HTTPException(status_code=400, detail="lsp rename requires new_name")
            return {
                "status": "stub",
                "op": normalized_op,
                "file": req.file,
                "message": "Only diagnostics is implemented in this ClawCross LSP bridge.",
            }
        payload = probe_diagnostics(
            username=req.user_id,
            session_id=req.session_id,
            file=req.file,
            timeout_seconds=req.timeout_seconds,
            max_diagnostics=req.max_diagnostics,
        )
        return {
            "status": "success" if payload.get("ok") else "error",
            **payload,
        }

    async def list_session_workflow_presets(
        self,
        user_id: str,
        password: str,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(user_id, password, x_internal_token)
        return {
            "status": "success",
            "presets": list_workflow_presets(),
        }

    async def apply_session_workflow_preset(
        self,
        req: WeBotWorkflowPresetApplyRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        preset = get_workflow_preset(req.preset_id)
        if preset is None:
            raise HTTPException(status_code=404, detail=f"未知 workflow preset: {req.preset_id}")
        save_session_mode(
            req.user_id,
            req.session_id,
            mode=preset.mode,
            reason=preset.reason,
        )
        save_session_plan(
            req.user_id,
            req.session_id,
            title=preset.title,
            status="active",
            items=list(preset.items),
            metadata=preset.plan_metadata(),
        )
        create_runtime_artifact(
            user_id=req.user_id,
            session_id=req.session_id,
            run_id="",
            kind="workflow_preset",
            title=f"Applied workflow preset: {preset.name}",
            path=f"workflow://{preset.preset_id}",
            summary=preset.description,
            metadata={
                "preset_id": preset.preset_id,
                "mode": preset.mode,
                "source": preset.source,
            },
        )
        await self._publish_runtime_snapshot(
            req.user_id,
            req.session_id,
            reason="workflow_preset_apply",
        )
        return {
            "status": "success",
            "session_id": req.session_id,
            "preset": preset.to_payload(),
            "mode": get_session_mode(req.user_id, req.session_id),
            "plan": get_session_plan(req.user_id, req.session_id),
        }

    async def get_session_inbox(
        self,
        req: WeBotSessionInboxListRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        source_session = req.session_id or "default"
        targets = self._resolve_target_sessions(req.user_id, req.target_ref or source_session, source_session)
        if req.target_ref == "*":
            targets = self._resolve_target_sessions(req.user_id, "*", source_session)
            targets.append({"target_session": source_session, "target_agent_id": ""})
        deduped: dict[str, str] = {}
        for target in targets:
            deduped[target["target_session"]] = target.get("target_agent_id", "")
        if not deduped:
            deduped[source_session] = ""

        items: list[dict[str, Any]] = []
        for session_key in deduped.keys():
            rows = list_inbox_messages(
                req.user_id,
                session_key,
                status=(req.status or "").strip().lower() or None,
                limit=max(1, min(req.limit, 50)),
            )
            items.extend(self._serialize_inbox(row) for row in rows)
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return {
            "status": "success",
            "target_sessions": list(deduped.keys()),
            "items": items[: max(1, min(req.limit, 50))],
        }

    async def send_session_inbox(
        self,
        req: WeBotSessionInboxSendRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        source_session = req.session_id or "default"
        source_agent_id, source_label = self._source_label(req.user_id, source_session)
        targets = self._resolve_target_sessions(req.user_id, req.target_ref, source_session)
        if not targets:
            return {"status": "success", "created": 0, "delivered": 0, "targets": []}

        created = 0
        delivered = 0
        results: list[dict[str, Any]] = []
        for target in targets:
            target_session = target["target_session"]
            target_agent_id = target.get("target_agent_id", "")
            inbox_record = create_inbox_message(
                req.user_id,
                target_session=target_session,
                body=req.body,
                source_session=source_session,
                source_agent_id=source_agent_id,
                source_label=source_label,
                target_agent_id=target_agent_id,
                metadata={"target_ref": req.target_ref},
            )
            created += 1
            delivered_count, state = await self._deliver_inbox_messages(
                user_id=req.user_id,
                target_session=target_session,
                target_agent_id=target_agent_id,
                limit=20,
                force=False,
            )
            delivered += delivered_count
            results.append(
                {
                    "target_session": target_session,
                    "target_agent_id": target_agent_id,
                    "delivery_state": state,
                    "delivered_count": delivered_count,
                    "message": self._serialize_inbox(inbox_record),
                }
            )
        await self._publish_runtime_snapshots(
            req.user_id,
            [source_session] + [item["target_session"] for item in results],
            reason="session_inbox_send",
            changed_session_id=source_session,
        )
        return {
            "status": "success",
            "source_session": source_session,
            "created": created,
            "delivered": delivered,
            "targets": results,
        }

    async def deliver_session_inbox(
        self,
        req: WeBotSessionInboxDeliverRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        source_session = req.session_id or "default"
        if req.target_ref == "*":
            targets = self._resolve_target_sessions(req.user_id, "*", source_session)
            targets.append({"target_session": source_session, "target_agent_id": ""})
        else:
            targets = self._resolve_target_sessions(req.user_id, req.target_ref or source_session, source_session)
        deduped: dict[str, str] = {}
        for target in targets:
            deduped[target["target_session"]] = target.get("target_agent_id", "")
        if not deduped:
            deduped[source_session] = ""

        delivered_total = 0
        target_results: list[dict[str, Any]] = []
        for target_session, target_agent_id in deduped.items():
            delivered_count, state = await self._deliver_inbox_messages(
                user_id=req.user_id,
                target_session=target_session,
                target_agent_id=target_agent_id,
                limit=req.limit,
                force=req.force,
            )
            delivered_total += delivered_count
            target_results.append(
                {
                    "target_session": target_session,
                    "target_agent_id": target_agent_id,
                    "delivery_state": state,
                    "delivered_count": delivered_count,
                }
            )
        await self._publish_runtime_snapshots(
            req.user_id,
            [source_session] + [item["target_session"] for item in target_results],
            reason="session_inbox_deliver",
            changed_session_id=source_session,
        )
        return {
            "status": "success",
            "source_session": source_session,
            "delivered_total": delivered_total,
            "targets": target_results,
        }

    async def interrupt_run(
        self,
        req: WeBotRunInterruptRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        target_run_id = req.run_id.strip()
        if not target_run_id and req.agent_ref.strip():
            record = self._resolve_subagent_ref(req.user_id, req.agent_ref)
            if record is not None:
                latest_run = get_latest_run_for_agent(req.user_id, record.agent_id)
                if latest_run is not None:
                    target_run_id = latest_run.run_id
        if not target_run_id:
            raise HTTPException(status_code=404, detail="未找到需要中断的运行")
        run = request_run_interrupt(target_run_id, req.user_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"未找到运行: {target_run_id}")
        await self._publish_runtime_snapshot(
            req.user_id,
            run.session_id,
            reason="run_interrupt",
        )
        return {
            "status": "success",
            "run": self._serialize_run(req.user_id, run),
        }

    async def update_session_plan(
        self,
        req: WeBotPlanUpdateRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        save_session_plan(
            req.user_id,
            req.session_id,
            title=req.title,
            status=req.status,
            items=req.items,
            metadata=req.metadata or {},
        )
        plan = get_session_plan(req.user_id, req.session_id)
        await self._publish_runtime_snapshot(
            req.user_id,
            req.session_id,
            reason="session_plan_update",
        )
        return {
            "status": "success",
            "session_id": req.session_id,
            "plan": plan,
        }

    async def clear_session_plan(
        self,
        req: WeBotSessionRuntimeRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        deleted = delete_session_plan(req.user_id, req.session_id)
        await self._publish_runtime_snapshot(
            req.user_id,
            req.session_id,
            reason="session_plan_clear",
        )
        return {"status": "success", "deleted": deleted}

    async def update_session_todos(
        self,
        req: WeBotTodoUpdateRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        save_session_todos(req.user_id, req.session_id, items=req.items)
        todos = get_session_todos(req.user_id, req.session_id)
        await self._publish_runtime_snapshot(
            req.user_id,
            req.session_id,
            reason="session_todos_update",
        )
        return {
            "status": "success",
            "session_id": req.session_id,
            "todos": todos,
        }

    async def clear_session_todos(
        self,
        req: WeBotSessionRuntimeRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        deleted = delete_session_todos(req.user_id, req.session_id)
        await self._publish_runtime_snapshot(
            req.user_id,
            req.session_id,
            reason="session_todos_clear",
        )
        return {"status": "success", "deleted": deleted}

    async def list_session_goals(
        self,
        user_id: str,
        session_id: str,
        password: str,
        status: str,
        limit: int,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(user_id, password, x_internal_token)
        goals = list_session_goals(
            user_id,
            session_id or None,
            status=(status or "").strip().lower() or None,
            limit=max(1, min(limit or 20, 100)),
        )
        return {
            "status": "success",
            "session_id": session_id or "",
            "goals": [self._serialize_goal(item) for item in goals],
        }

    async def update_session_goal(
        self,
        req: WeBotGoalUpdateRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        record = upsert_session_goal(
            req.user_id,
            req.session_id,
            goal_id=req.goal_id,
            title=req.title,
            description=req.description,
            status=req.status,
            priority=req.priority,
            parent_goal_id=req.parent_goal_id,
            owner_session=req.owner_session,
            metrics=req.metrics or {},
            budget_tokens=req.budget_tokens,
            spent_tokens=req.spent_tokens,
            budget_usd=req.budget_usd,
            spent_usd=req.spent_usd,
            metadata=req.metadata or {},
        )
        await self._publish_runtime_snapshot(
            req.user_id,
            record.session_id,
            reason="session_goal_update",
        )
        return {
            "status": "success",
            "goal": self._serialize_goal(record),
        }

    async def record_goal_heartbeat(
        self,
        req: WeBotGoalHeartbeatRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        record = record_goal_heartbeat(
            req.user_id,
            req.goal_id,
            session_id=req.session_id,
            heartbeat_status=req.heartbeat_status,
            report=req.report,
            spent_tokens_delta=req.spent_tokens_delta,
            spent_usd_delta=req.spent_usd_delta,
            metadata=req.metadata or {},
        )
        if record is None:
            raise HTTPException(status_code=404, detail=f"未找到目标: {req.goal_id}")
        await self._publish_runtime_snapshot(
            req.user_id,
            record.session_id,
            reason="session_goal_heartbeat",
        )
        return {
            "status": "success",
            "goal": self._serialize_goal(record),
        }

    async def get_claude_code_status(
        self,
        user_id: str,
        session_id: str,
        password: str,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(user_id, password, x_internal_token)
        keepalive = get_claude_keepalive_state(user_id, session_id or "default")
        return {
            "status": "success",
            "session_id": session_id or "default",
            "claude_code": {
                "status": detect_claude_code_cached(ttl_seconds=10),
                "keepalive": self._serialize_claude_keepalive(keepalive),
            },
        }

    async def update_claude_keepalive(
        self,
        req: WeBotClaudeKeepaliveUpdateRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        record = save_claude_keepalive_state(
            req.user_id,
            req.session_id,
            enabled=req.enabled,
            prompt=req.prompt,
            model=req.model,
            timezone_name=req.timezone,
            start_time=req.start_time,
            sleep_time=req.sleep_time,
            weekdays=req.weekdays,
            use_caffeinate=req.use_caffeinate,
            force_sleep_at_quiet_hours=req.force_sleep_at_quiet_hours,
            monitor_command=req.monitor_command,
            timeout_seconds=req.timeout_seconds,
            metadata=req.metadata or {},
        )
        await self._publish_runtime_snapshot(
            req.user_id,
            record.session_id,
            reason="claude_keepalive_update",
        )
        return {
            "status": "success",
            "keepalive": self._serialize_claude_keepalive(record),
        }

    async def probe_claude_code(
        self,
        req: WeBotClaudeProbeRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        result = probe_claude_acp(
            prompt=req.prompt,
            session_name=req.session_name,
            timeout=req.timeout_seconds,
        )
        record_claude_keepalive_result(
            req.user_id,
            req.session_id,
            status="success" if result.get("ok") else "failed",
            result=str(result.get("stdout_tail") or "")[-2000:],
            error=str(result.get("error") or result.get("stderr_tail") or "")[-1000:],
            metadata={"probe": True, "session_name": result.get("session_name", "")},
        )
        await self._publish_runtime_snapshot(
            req.user_id,
            req.session_id,
            reason="claude_code_probe",
        )
        return {
            "status": "success" if result.get("ok") else "failed",
            "probe": result,
        }

    async def run_claude_keepalive_once(
        self,
        req: WeBotClaudeKickoffRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        state = get_claude_keepalive_state(req.user_id, req.session_id)
        prompt = req.prompt or state.prompt or "ping"
        if req.use_acp:
            result = probe_claude_acp(
                prompt=prompt,
                session_name=req.session_name,
                timeout=req.timeout_seconds or state.timeout_seconds,
            )
        else:
            result = run_claude_cli_prompt(
                prompt=prompt,
                model=req.model or state.model,
                timeout=req.timeout_seconds or state.timeout_seconds,
            )
        record = record_claude_keepalive_result(
            req.user_id,
            req.session_id,
            status="success" if result.get("ok") else "failed",
            result=str(result.get("stdout_tail") or "")[-2000:],
            error=str(result.get("error") or result.get("stderr_tail") or "")[-1000:],
            metadata={"kickoff": True, "use_acp": req.use_acp},
        )
        await self._publish_runtime_snapshot(
            req.user_id,
            record.session_id,
            reason="claude_keepalive_kickoff",
        )
        return {
            "status": "success" if result.get("ok") else "failed",
            "result": result,
            "keepalive": self._serialize_claude_keepalive(record),
        }

    async def record_verification(
        self,
        req: WeBotVerificationCreateRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        verification_id = f"verify-{req.session_id}-{len(list_verification_records(req.user_id, req.session_id, limit=1000)) + 1}"
        add_verification_record(
            req.user_id,
            req.session_id,
            verification_id=verification_id,
            title=req.title,
            status=req.status,
            details=req.details,
        )
        verifications = list_verification_records(req.user_id, req.session_id, limit=20)
        await self._publish_runtime_snapshot(
            req.user_id,
            req.session_id,
            reason="verification_record",
        )
        return {
            "status": "success",
            "verification_id": verification_id,
            "verifications": verifications,
        }

    async def update_voice_state(
        self,
        req: WeBotVoiceStateUpdateRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        defaults = self._voice_defaults()
        state = save_voice_state(
            req.user_id,
            req.session_id,
            enabled=req.enabled,
            auto_read_aloud=req.auto_read_aloud,
            recording_supported=True,
            tts_model=req.tts_model or defaults.get("tts_model", ""),
            tts_voice=req.tts_voice or defaults.get("tts_voice", ""),
            stt_model=req.stt_model or defaults.get("stt_model", ""),
            last_transcript=req.last_transcript,
        )
        payload = self._serialize_voice_payload(req.user_id, req.session_id)
        await self._publish_runtime_snapshot(
            req.user_id,
            req.session_id,
            reason="voice_update",
        )
        return {"status": "success", "voice": payload}

    async def create_bridge_attach(
        self,
        req: WeBotBridgeAttachRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        bridge = issue_bridge_session(
            user_id=req.user_id,
            session_id=req.session_id,
            role=req.role,
            label=req.label,
        )
        await self._publish_runtime_snapshot(
            req.user_id,
            req.session_id,
            reason="bridge_attach",
        )
        return {"status": "success", "bridge": bridge}

    async def detach_bridge(
        self,
        req: WeBotBridgeDetachRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        record = get_bridge_session(req.bridge_id, req.user_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"未找到 bridge: {req.bridge_id}")
        updated = upsert_bridge_session(
            bridge_id=record.bridge_id,
            user_id=record.user_id,
            session_id=record.session_id,
            role=record.role,
            label=record.label,
            attach_code=record.attach_code,
            websocket_path=record.websocket_path,
            status="detached",
            connection_count=0,
            metadata=record.metadata,
            last_error="",
            last_attached_at=record.last_attached_at,
        )
        await self._publish_runtime_snapshot(
            req.user_id,
            record.session_id,
            reason="bridge_detach",
        )
        return {"status": "success", "bridge": serialize_bridge_record(updated)}

    async def update_kairos_state(
        self,
        req: WeBotKairosUpdateRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        current = get_memory_state(req.user_id, req.session_id)
        layout = ensure_memory_state(req.user_id, req.session_id)
        record = save_memory_state(
            req.user_id,
            req.session_id,
            project_slug=layout["project_slug"],
            memory_dir=layout["memory_dir"],
            index_path=layout["index_path"],
            kairos_enabled=req.enabled,
            dream_status=current.dream_status,
            active_run_id=current.active_run_id,
            last_dream_at=current.last_dream_at,
            daily_log_path=layout["daily_log_path"],
            metadata={"last_kairos_reason": req.reason},
        )
        memory = ensure_memory_state(req.user_id, req.session_id, kairos_enabled=req.enabled)
        await self._publish_runtime_snapshot(
            req.user_id,
            req.session_id,
            reason="kairos_update",
        )
        return {"status": "success", "memory": memory}

    async def run_dream(
        self,
        req: WeBotDreamRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        runtime = self._serialize_session_runtime(req.user_id, req.session_id)
        memory = run_auto_dream(
            req.user_id,
            req.session_id,
            plan=runtime.get("plan"),
            todos=runtime.get("todos"),
            verifications=runtime.get("verifications"),
            inbox=runtime.get("inbox"),
            recent_runs=runtime.get("runs"),
            recent_artifacts=runtime.get("artifacts"),
            reason=req.reason or "manual",
        )
        await self._publish_runtime_snapshot(
            req.user_id,
            req.session_id,
            reason="dream_run",
        )
        return {"status": "success", "memory": memory}

    async def buddy_action(
        self,
        req: WeBotBuddyActionRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        normalized_action = (req.action or "").strip().lower() or "pet"
        apply_buddy_action(req.user_id, normalized_action)
        buddy = serialize_buddy_state(req.user_id)
        await self._publish_runtime_snapshot(
            req.user_id,
            req.session_id or "default",
            reason="buddy_action",
        )
        return {"status": "success", "buddy": buddy}

    async def resolve_tool_approval(
        self,
        req: WeBotApprovalResolutionRequest,
        x_internal_token: str | None,
    ):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        normalized_action = "approved" if req.action.lower() in {"approve", "approved", "allow"} else "denied"
        approval = resolve_permission_request(
            user_id=req.user_id,
            approval_id=req.approval_id,
            action=normalized_action,
            reason=req.reason,
            remember=req.remember,
        )
        if approval is None:
            raise HTTPException(status_code=404, detail=f"未找到 tool approval: {req.approval_id}")
        target_session = req.session_id or approval.session_id
        if target_session:
            await self._publish_runtime_snapshot(
                req.user_id,
                target_session,
                reason="approval_resolution",
            )
        return {
            "status": "success",
            "approval": {
                "approval_id": approval.approval_id,
                "tool_name": approval.tool_name,
                "status": approval.status,
                "remember": req.remember,
            },
        }
