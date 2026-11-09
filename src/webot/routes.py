"""
WeBot runtime routes for subagent inspection and policy management.
"""

from typing import Any, Callable

from fastapi import APIRouter, Header
from starlette.websockets import WebSocket, WebSocketDisconnect

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
from webot.bridge import bridge_hub, get_bridge_record_for_user
from webot.service import WeBotService


def create_webot_router(
    *,
    agent: Any,
    verify_auth_or_token: Callable[[str, str, str | None], None],
    extract_text: Callable[[Any], str],
) -> APIRouter:
    router = APIRouter()
    service = WeBotService(
        agent=agent,
        verify_auth_or_token=verify_auth_or_token,
        extract_text=extract_text,
    )

    @router.get("/webot/subagents")
    async def list_subagents(
        user_id: str,
        password: str = "",
        x_internal_token: str | None = Header(None),
    ):
        return await service.list_subagents(user_id, password, x_internal_token)

    @router.post("/webot/subagents/history")
    async def get_subagent_history(
        req: WeBotSubagentHistoryRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.get_subagent_history(req, x_internal_token)

    @router.post("/webot/subagents/cancel")
    async def cancel_subagent(
        req: WeBotSubagentRefRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.cancel_subagent(req, x_internal_token)

    @router.get("/webot/tool-policy")
    async def get_tool_policy(
        user_id: str,
        password: str = "",
        x_internal_token: str | None = Header(None),
    ):
        return await service.get_tool_policy(user_id, password, x_internal_token)

    @router.post("/webot/tool-policy")
    async def update_tool_policy(
        req: WeBotToolPolicyUpdateRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.update_tool_policy(req, x_internal_token)

    @router.get("/webot/tool-approvals")
    async def get_tool_approvals(
        user_id: str,
        status: str = "pending",
        session_id: str = "",
        limit: int = 20,
        password: str = "",
        x_internal_token: str | None = Header(None),
    ):
        return await service.list_tool_approvals(
            user_id,
            password,
            status,
            session_id,
            limit,
            x_internal_token,
        )

    @router.get("/webot/session-runtime")
    async def get_session_runtime(
        user_id: str,
        session_id: str,
        password: str = "",
        x_internal_token: str | None = Header(None),
    ):
        return await service.get_session_runtime(user_id, session_id, password, x_internal_token)

    @router.post("/webot/session-mode")
    async def update_session_mode(
        req: WeBotSessionModeUpdateRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.update_session_mode(req, x_internal_token)

    @router.post("/webot/lsp")
    async def run_lsp(
        req: WeBotLspRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.run_lsp(req, x_internal_token)

    @router.get("/webot/workflow-presets")
    async def list_session_workflow_presets(
        user_id: str,
        password: str = "",
        x_internal_token: str | None = Header(None),
    ):
        return await service.list_session_workflow_presets(user_id, password, x_internal_token)

    @router.post("/webot/workflow-presets/apply")
    async def apply_session_workflow_preset(
        req: WeBotWorkflowPresetApplyRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.apply_session_workflow_preset(req, x_internal_token)

    @router.get("/webot/session-inbox")
    async def get_session_inbox(
        user_id: str,
        session_id: str,
        target_ref: str = "",
        status: str = "queued",
        limit: int = 20,
        password: str = "",
        x_internal_token: str | None = Header(None),
    ):
        req = WeBotSessionInboxListRequest(
            user_id=user_id,
            password=password,
            session_id=session_id,
            target_ref=target_ref,
            status=status,
            limit=limit,
        )
        return await service.get_session_inbox(req, x_internal_token)

    @router.post("/webot/session-inbox/send")
    async def send_session_inbox(
        req: WeBotSessionInboxSendRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.send_session_inbox(req, x_internal_token)

    @router.post("/webot/session-inbox/deliver")
    async def deliver_session_inbox(
        req: WeBotSessionInboxDeliverRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.deliver_session_inbox(req, x_internal_token)

    @router.post("/webot/runs/interrupt")
    async def interrupt_run(
        req: WeBotRunInterruptRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.interrupt_run(req, x_internal_token)

    @router.post("/webot/session-plan")
    async def update_session_plan(
        req: WeBotPlanUpdateRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.update_session_plan(req, x_internal_token)

    @router.delete("/webot/session-plan")
    async def clear_session_plan(
        req: WeBotSessionRuntimeRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.clear_session_plan(req, x_internal_token)

    @router.post("/webot/session-todos")
    async def update_session_todos(
        req: WeBotTodoUpdateRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.update_session_todos(req, x_internal_token)

    @router.delete("/webot/session-todos")
    async def clear_session_todos(
        req: WeBotSessionRuntimeRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.clear_session_todos(req, x_internal_token)

    @router.get("/webot/session-goals")
    async def list_session_goals(
        user_id: str,
        session_id: str = "",
        status: str = "",
        limit: int = 20,
        password: str = "",
        x_internal_token: str | None = Header(None),
    ):
        return await service.list_session_goals(
            user_id,
            session_id,
            password,
            status,
            limit,
            x_internal_token,
        )

    @router.post("/webot/session-goals")
    async def update_session_goal(
        req: WeBotGoalUpdateRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.update_session_goal(req, x_internal_token)

    @router.post("/webot/session-goals/heartbeat")
    async def record_goal_heartbeat(
        req: WeBotGoalHeartbeatRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.record_goal_heartbeat(req, x_internal_token)

    @router.get("/webot/claude-code/status")
    async def get_claude_code_status(
        user_id: str,
        session_id: str = "default",
        password: str = "",
        x_internal_token: str | None = Header(None),
    ):
        return await service.get_claude_code_status(user_id, session_id, password, x_internal_token)

    @router.post("/webot/claude-code/keepalive")
    async def update_claude_keepalive(
        req: WeBotClaudeKeepaliveUpdateRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.update_claude_keepalive(req, x_internal_token)

    @router.post("/webot/claude-code/probe")
    async def probe_claude_code(
        req: WeBotClaudeProbeRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.probe_claude_code(req, x_internal_token)

    @router.post("/webot/claude-code/kickoff")
    async def run_claude_keepalive_once(
        req: WeBotClaudeKickoffRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.run_claude_keepalive_once(req, x_internal_token)

    @router.post("/webot/verifications")
    async def record_verification(
        req: WeBotVerificationCreateRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.record_verification(req, x_internal_token)

    @router.post("/webot/voice")
    async def update_voice_state(
        req: WeBotVoiceStateUpdateRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.update_voice_state(req, x_internal_token)

    @router.post("/webot/bridge/attach")
    async def create_bridge_attach(
        req: WeBotBridgeAttachRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.create_bridge_attach(req, x_internal_token)

    @router.post("/webot/bridge/detach")
    async def detach_bridge(
        req: WeBotBridgeDetachRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.detach_bridge(req, x_internal_token)

    @router.post("/webot/kairos")
    async def update_kairos_state(
        req: WeBotKairosUpdateRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.update_kairos_state(req, x_internal_token)

    @router.post("/webot/dream")
    async def run_dream(
        req: WeBotDreamRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.run_dream(req, x_internal_token)

    @router.post("/webot/buddy")
    async def buddy_action(
        req: WeBotBuddyActionRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.buddy_action(req, x_internal_token)

    @router.post("/webot/tool-approvals/resolve")
    async def resolve_tool_approval(
        req: WeBotApprovalResolutionRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.resolve_tool_approval(req, x_internal_token)

    @router.websocket("/webot/ws/{user_id}/{bridge_id}")
    async def webot_bridge_socket(websocket: WebSocket, user_id: str, bridge_id: str):
        record = get_bridge_record_for_user(user_id, bridge_id)
        if record is None:
            await websocket.close(code=4404)
            return
        await bridge_hub.connect(record, websocket)
        try:
            snapshot = service._serialize_session_runtime(user_id, record.session_id)
            await websocket.send_json(
                {
                    "type": "connected",
                    "bridge_id": record.bridge_id,
                    "session_id": record.session_id,
                    "role": record.role,
                }
            )
            await websocket.send_json(
                {
                    "type": "runtime_snapshot",
                    "bridge_id": record.bridge_id,
                    "session_id": record.session_id,
                    "changed_session_id": record.session_id,
                    "runtime": snapshot,
                }
            )
            while True:
                message = await websocket.receive_json()
                msg_type = str(message.get("type") or "").strip().lower()
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong", "bridge_id": record.bridge_id})
                elif msg_type == "refresh":
                    await websocket.send_json(
                        {
                            "type": "runtime_snapshot",
                            "bridge_id": record.bridge_id,
                            "session_id": record.session_id,
                            "changed_session_id": record.session_id,
                            "runtime": service._serialize_session_runtime(user_id, record.session_id),
                        }
                    )
                else:
                    await websocket.send_json({"type": "ack", "bridge_id": record.bridge_id, "message": message})
        except WebSocketDisconnect:
            pass
        finally:
            await bridge_hub.disconnect(record, websocket)

    return router
