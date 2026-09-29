"""
Ops 操作服务路由模块

提供基础运维和认证相关的 API 路由：
- GET /tools：获取可用工具列表
- POST /login：用户登录
- POST /tts：文本转语音
- POST /acp_control：ACP 外部 agent 控制
- POST /acp_status：查询 ACP agent 状态
- POST /agent_control：统一列出、查询和控制各类 agent
"""

from typing import Any, Callable

from fastapi import APIRouter, Header

from ops.models import LoginRequest, SessionsCloseRequest, SessionsListRequest, TTSRequest, UpdateCheckRequest, UpdateStartRequest, UpdateStatusRequest
from ops.service import OpsService


def create_ops_router(
    *,
    internal_token: str,
    agent: Any,
    verify_password: Callable[[str, str], bool],
    verify_auth_or_token: Callable[[str, str, str | None], None],
) -> APIRouter:
    """构建基础运维/认证相关路由。"""
    router = APIRouter()
    service = OpsService(
        internal_token=internal_token,
        agent=agent,
        verify_password=verify_password,
        verify_auth_or_token=verify_auth_or_token,
    )

    @router.get("/tools")
    async def get_tools_list(
        x_internal_token: str | None = Header(None),
        authorization: str | None = Header(None),
    ):
        return await service.get_tools_list(x_internal_token, authorization)

    @router.post("/login")
    async def login(req: LoginRequest):
        return await service.login(req)

    @router.post("/tts")
    async def text_to_speech(req: TTSRequest, x_internal_token: str | None = Header(None)):
        return await service.text_to_speech(req, x_internal_token)

    @router.post("/sessions_list")
    async def sessions_list(req: SessionsListRequest, x_internal_token: str | None = Header(None)):
        return await service.list_all_sessions(req.user_id)

    @router.post("/sessions_close")
    async def sessions_close(req: SessionsCloseRequest, x_internal_token: str | None = Header(None)):
        return await service.close_acp_session(req.platform, req.session_name, req.cwd)

    @router.post("/update_check")
    async def update_check(req: UpdateCheckRequest, x_internal_token: str | None = Header(None)):
        return await service.update_check(req, x_internal_token)

    @router.post("/update_start")
    async def update_start(req: UpdateStartRequest, x_internal_token: str | None = Header(None)):
        return await service.update_start(req, x_internal_token)

    @router.post("/update_status")
    async def update_status(req: UpdateStatusRequest, x_internal_token: str | None = Header(None)):
        return await service.update_status(req, x_internal_token)

    return router
