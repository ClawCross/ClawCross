"""
群聊管理路由模块

提供群聊相关的 API 路由：
- POST/GET /groups：创建/获取群聊
- GET /groups/{group_id}：获取群聊详情
- GET/POST /groups/{group_id}/messages：获取/发送群聊消息
- PUT /groups/{group_id}：更新群聊
- DELETE /groups/{group_id}：删除群聊
- 群成员管理、禁言、成员同步等
"""

from typing import Any, Callable

from fastapi import APIRouter, Header

from api.group_models import (
    GroupCreateRequest,
    GroupMessageRequest,
    GroupUpdateRequest,
    GroupAddMemberRequest,
    GroupMuteMemberRequest,
    GroupMuteAllRequest,
)
from api.group_service import GroupService, init_group_db


def create_group_router(
    *,
    internal_token: str,
    verify_password: Callable[[str, str], bool],
    checkpoint_db_path: str,
    group_db_path: str,
    agent: Any,
) -> APIRouter:
    """构建群聊路由，保持与原 mainagent 路径兼容。"""
    router = APIRouter()
    service = GroupService(
        internal_token=internal_token,
        verify_password=verify_password,
        checkpoint_db_path=checkpoint_db_path,
        group_db_path=group_db_path,
        agent=agent,
    )

    @router.post("/groups")
    async def create_group(req: GroupCreateRequest, authorization: str | None = Header(None)):
        return await service.create_group(req, authorization)

    @router.get("/groups")
    async def list_groups(authorization: str | None = Header(None)):
        return await service.list_groups(authorization)

    @router.get("/groups/{group_id}")
    async def get_group(group_id: str, authorization: str | None = Header(None)):
        return await service.get_group(group_id, authorization)

    @router.get("/groups/{group_id}/messages")
    async def get_group_messages(group_id: str, after_id: int = 0, authorization: str | None = Header(None)):
        return await service.get_group_messages(group_id, after_id, authorization)

    @router.post("/groups/{group_id}/messages")
    async def post_group_message(
        group_id: str,
        req: GroupMessageRequest,
        authorization: str | None = Header(None),
        x_internal_token: str | None = Header(None),
    ):
        return await service.post_group_message(group_id, req, authorization, x_internal_token)

    @router.put("/groups/{group_id}")
    async def update_group(group_id: str, req: GroupUpdateRequest, authorization: str | None = Header(None)):
        return await service.update_group(group_id, req, authorization)

    @router.delete("/groups/{group_id}")
    async def delete_group(group_id: str, authorization: str | None = Header(None)):
        return await service.delete_group(group_id, authorization)

    @router.post("/groups/{group_id}/mute")
    async def mute_group(group_id: str, authorization: str | None = Header(None)):
        return await service.mute_group(group_id, authorization)

    @router.post("/groups/{group_id}/unmute")
    async def unmute_group(group_id: str, authorization: str | None = Header(None)):
        return await service.unmute_group(group_id, authorization)

    @router.get("/groups/{group_id}/mute_status")
    async def group_mute_status(group_id: str, authorization: str | None = Header(None)):
        return await service.group_mute_status(group_id, authorization)

    @router.post("/groups/{group_id}/members/mute")
    async def mute_group_member(group_id: str, req: GroupMuteMemberRequest, authorization: str | None = Header(None)):
        return await service.mute_group_member(group_id, req, authorization)

    @router.post("/groups/{group_id}/mute_all")
    async def mute_all_group_agents(group_id: str, req: GroupMuteAllRequest, authorization: str | None = Header(None)):
        return await service.mute_all_group_agents(group_id, req, authorization)

    @router.get("/groups/{group_id}/typing")
    async def group_typing_status(group_id: str, authorization: str | None = Header(None)):
        return await service.get_typing_status(group_id, authorization)

    @router.get("/groups/{group_id}/sessions")
    async def list_available_sessions(group_id: str, authorization: str | None = Header(None)):
        return await service.list_available_sessions(group_id, authorization)

    @router.post("/groups/{group_id}/sync_members")
    async def sync_group_members(group_id: str, team_name: str = "", authorization: str | None = Header(None)):
        return await service.sync_group_members(group_id, authorization, team_name=team_name)

    @router.post("/groups/{group_id}/members")
    async def add_member(group_id: str, req: GroupAddMemberRequest, authorization: str | None = Header(None)):
        return await service.add_single_member(group_id, req, authorization)

    @router.delete("/groups/{group_id}/members/{global_id}")
    async def remove_member(group_id: str, global_id: str, authorization: str | None = Header(None)):
        return await service.remove_single_member(group_id, global_id, authorization)

    return router
