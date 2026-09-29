"""
OpenAI 兼容 API 路由模块

提供 OpenAI Chat Completions API 的 FastAPI 路由：
- /v1/chat/completions：聊天补全接口
- /v1/models：可用模型列表
"""

from fastapi import APIRouter, Header

from webot.api.openai_models import ChatCompletionRequest
from webot.api.openai_service import OpenAIChatService


def create_openai_router(*, service: OpenAIChatService) -> APIRouter:
    """构建 OpenAI 兼容路由。"""
    router = APIRouter()

    @router.post("/v1/chat/completions")
    async def openai_chat_completions(
        req: ChatCompletionRequest,
        authorization: str | None = Header(None),
    ):
        return await service.handle_chat_completions(req, authorization)

    @router.get("/v1/models")
    async def list_models(authorization: str | None = Header(None)):
        """返回可用模型列表（OpenAI 兼容）；认证后包含调用者名下的全部 agent。"""
        return service.list_models(authorization)

    return router
