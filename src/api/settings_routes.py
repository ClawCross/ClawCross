"""
设置管理路由模块

提供系统配置相关的 API 路由：
- GET/POST /settings：获取/更新白名单配置
- GET/POST /settings/full：获取/更新完整配置（含敏感信息）
- POST /restart：发送重启信号
"""

from typing import Callable

from fastapi import APIRouter, Header

from api.settings_models import ChatbotWhitelistUpdateRequest, SettingsUpdateRequest
from api.settings_service import SettingsService


def create_settings_router(
    *,
    env_path: str,
    verify_auth_or_token: Callable[[str, str, str | None], None],
) -> APIRouter:
    """构建 settings 相关路由。"""
    router = APIRouter()
    service = SettingsService(
        env_path=env_path,
        verify_auth_or_token=verify_auth_or_token,
    )

    @router.get("/settings")
    async def get_settings(user_id: str, password: str = "", x_internal_token: str | None = Header(None)):
        return await service.get_settings(user_id, password, x_internal_token)

    @router.post("/settings")
    async def update_settings(req: SettingsUpdateRequest, x_internal_token: str | None = Header(None)):
        return await service.update_settings(req, x_internal_token)

    @router.get("/settings/full")
    async def get_settings_full(user_id: str, password: str = "", x_internal_token: str | None = Header(None)):
        return await service.get_settings_full(user_id, password, x_internal_token)

    @router.post("/settings/full")
    async def update_settings_full(req: SettingsUpdateRequest, x_internal_token: str | None = Header(None)):
        return await service.update_settings_full(req, x_internal_token)

    @router.post("/restart")
    async def restart_services(req: SettingsUpdateRequest, x_internal_token: str | None = Header(None)):
        return await service.restart_services(req, x_internal_token)

    @router.get("/chatbot/whitelist")
    async def get_chatbot_whitelist(user_id: str, password: str = "", x_internal_token: str | None = Header(None)):
        return await service.get_chatbot_whitelist(user_id, password, x_internal_token)

    @router.post("/chatbot/whitelist")
    async def update_chatbot_whitelist(req: ChatbotWhitelistUpdateRequest, x_internal_token: str | None = Header(None)):
        return await service.update_chatbot_whitelist(req, x_internal_token)

    return router
