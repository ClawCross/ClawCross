"""
设置管理路由模块

提供系统配置相关的 API 路由：
- GET/POST /settings：获取/更新白名单配置
- GET/POST /settings/full：获取/更新完整配置（含敏感信息）
- POST /restart：发送重启信号
"""

from typing import Callable

from fastapi import APIRouter, Header, HTTPException

from ops.settings_models import ChannelWhitelistUpdateRequest, SettingsUpdateRequest, ChannelSetupRequest, ConfigurationSetupRequest
from ops.settings_service import SettingsService


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

    @router.get('/configuration/setup')
    async def configuration_setup(user_id: str, session_id: str = '', password: str = '', x_internal_token: str | None = Header(None)):
        verify_auth_or_token(user_id, password, x_internal_token)
        from ops.configuration_requests import describe, list_requests
        return {'topics': describe(user_id, session_id, env_path=env_path),
                'requests': list_requests(user_id, session_id, env_path=env_path, include_finished=True)}

    @router.post('/configuration/setup')
    async def configuration_submit(req: ConfigurationSetupRequest, x_internal_token: str | None = Header(None)):
        verify_auth_or_token(req.user_id, req.password, x_internal_token)
        from ops.configuration_requests import create, submit
        try:
            if req.request_id:
                return submit(req.user_id, req.request_id, req.values, cancel=req.cancel, env_path=env_path)
            return create(req.user_id, req.session_id, req.topic or ('channel:'+req.channel if req.channel else ''), req.values)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @router.get('/channels/setup')
    async def channel_setup(user_id: str, session_id: str = '', password: str = '', x_internal_token: str | None = Header(None)):
        verify_auth_or_token(user_id, password, x_internal_token)
        from channels.setup_requests import describe, list_requests
        return {'channels': describe(), 'requests': list_requests(user_id, session_id, include_finished=True)}

    @router.post('/channels/setup')
    async def channel_setup_submit(req: ChannelSetupRequest, x_internal_token: str | None = Header(None)):
        verify_auth_or_token(req.user_id, req.password, x_internal_token)
        from channels.setup_requests import create, submit
        try:
            if req.request_id:
                return submit(req.user_id, req.request_id, req.values, cancel=req.cancel)
            return create(req.user_id, req.session_id, req.channel, req.values)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

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

    @router.get("/channels/whitelist")
    async def get_channel_whitelist(user_id: str, password: str = "", x_internal_token: str | None = Header(None)):
        return await service.get_channel_whitelist(user_id, password, x_internal_token)

    @router.post("/channels/whitelist")
    async def update_channel_whitelist(req: ChannelWhitelistUpdateRequest, x_internal_token: str | None = Header(None)):
        return await service.update_channel_whitelist(req, x_internal_token)

    return router
