"""
会话管理路由模块

提供会话相关的 API 路由：
- POST /sessions：获取会话列表
- POST /sessions_status：批量获取会话状态
- POST /session_history：获取会话消息历史
- POST /delete_session：删除会话
- POST /session_status：获取单个会话状态
"""

from typing import Any, Callable

from fastapi import APIRouter, Header

from api.session_models import (
    CompactSessionRequest,
    DeleteSessionRequest,
    SessionHistoryRequest,
    SessionListRequest,
    SessionStatusRequest,
)
from api.session_service import SessionService


def create_session_router(
    *,
    db_path: str,
    agent: Any,
    verify_auth_or_token: Callable[[str, str, str | None], None],
    extract_text: Callable[[Any], str],
) -> APIRouter:
    """构建会话相关路由。"""
    router = APIRouter()
    service = SessionService(
        db_path=db_path,
        agent=agent,
        verify_auth_or_token=verify_auth_or_token,
        extract_text=extract_text,
    )

    @router.post("/sessions")
    async def list_sessions(req: SessionListRequest, x_internal_token: str | None = Header(None)):
        return await service.list_sessions(req, x_internal_token)

    @router.post("/sessions_status")
    async def sessions_status(req: SessionListRequest, x_internal_token: str | None = Header(None)):
        return await service.sessions_status(req, x_internal_token)

    @router.post("/session_history")
    async def get_session_history(req: SessionHistoryRequest, x_internal_token: str | None = Header(None)):
        return await service.get_session_history(req, x_internal_token)

    @router.post("/delete_session")
    async def delete_session(req: DeleteSessionRequest, x_internal_token: str | None = Header(None)):
        return await service.delete_session(req, x_internal_token)

    @router.post("/session_status")
    async def session_status(req: SessionStatusRequest, x_internal_token: str | None = Header(None)):
        return await service.session_status(req, x_internal_token)

    @router.post("/compact_session")
    async def compact_session(req: CompactSessionRequest, x_internal_token: str | None = Header(None)):
        return await service.compact_session(req, x_internal_token)

    return router
