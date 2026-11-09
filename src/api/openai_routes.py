"""
OpenAI 兼容 API 路由模块

提供 OpenAI Chat Completions API 的 FastAPI 路由：
- /v1/chat/completions：聊天补全接口
- /v1/models：可用模型列表
"""

from typing import Any, Callable

from fastapi import APIRouter, Header
from langchain_core.messages import HumanMessage

from api.openai_models import ChatCompletionRequest
from api.openai_service import OpenAIChatService


def create_openai_router(
    *,
    internal_token: str,
    verify_password: Callable[[str, str], bool],
    agent: Any,
    extract_text: Callable[[Any], str],
    build_human_message: Callable[[str, list[str] | None, list[dict] | None, list[dict] | None], HumanMessage],
) -> APIRouter:
    """构建 OpenAI 兼容路由。"""
    router = APIRouter()
    service = OpenAIChatService(
        internal_token=internal_token,
        verify_password=verify_password,
        agent=agent,
        extract_text=extract_text,
        build_human_message=build_human_message,
    )

    @router.post("/v1/chat/completions")
    async def openai_chat_completions(
        req: ChatCompletionRequest,
        authorization: str | None = Header(None),
    ):
        return await service.handle_chat_completions(req, authorization)

    @router.get("/v1/models")
    async def list_models():
        """返回可用模型列表（OpenAI 兼容）"""
        return service.list_models()

    return router
