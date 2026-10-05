"""
WeBot runtime routes for subagent inspection and policy management.
"""

from typing import Any, Callable

from fastapi import APIRouter, Header

from webot.models import (
    WeBotApprovalResolutionRequest,
    WeBotClaudeKeepaliveUpdateRequest,
    WeBotClaudeKickoffRequest,
    WeBotClaudeProbeRequest,
    WeBotDreamRequest,
    WeBotKairosUpdateRequest,
    WeBotLspRequest,
    WeBotPlanUpdateRequest,
    WeBotRunInterruptRequest,
    WeBotSessionInboxListRequest,
    WeBotSessionModeUpdateRequest,
    WeBotSessionRuntimeRequest,
    WeBotSubagentHistoryRequest,
    WeBotSubagentRefRequest,
    WeBotTodoUpdateRequest,
    WeBotToolPolicyUpdateRequest,
    WeBotRuntimeSettingsUpdateRequest,
    WeBotVerificationCreateRequest,
    WeBotWorkflowPresetApplyRequest,
)
from webot.api.service import WeBotService


def create_webot_router(
    *,
    agent: Any,
    system: Any,
    verify_auth_or_token: Callable[[str, str, str | None], None],
    extract_text: Callable[[Any], str],
) -> APIRouter:
    router = APIRouter()
    service = WeBotService(
        agent=agent,
        system=system,
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

    @router.get("/webot/runtime-settings")
    async def get_runtime_settings(user_id: str, session_id: str = "", password: str = "", x_internal_token: str | None = Header(None)):
        return await service.get_runtime_settings(user_id, session_id, password, x_internal_token)

    @router.post("/webot/runtime-settings")
    async def update_runtime_settings(req: WeBotRuntimeSettingsUpdateRequest, x_internal_token: str | None = Header(None)):
        return await service.update_runtime_settings(req, x_internal_token)

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

    @router.post("/webot/tool-approvals/resolve")
    async def resolve_tool_approval(
        req: WeBotApprovalResolutionRequest,
        x_internal_token: str | None = Header(None),
    ):
        return await service.resolve_tool_approval(req, x_internal_token)

    return router
