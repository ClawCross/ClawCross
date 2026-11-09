"""
OpenAI API 数据模型模块

定义 OpenAI 兼容 API 的请求/响应数据结构：
- ChatMessage / ChatMessageContent：聊天消息格式
- ChatCompletionRequest：聊天补全请求
- OpenAIExecutionContext：执行上下文（dataclass）
"""

from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel


class ChatMessageContent(BaseModel):
    """OpenAI 消息内容部分（text / image_url / input_audio / file）"""
    type: str
    text: Optional[str] = None
    image_url: Optional[dict] = None
    input_audio: Optional[dict] = None
    file: Optional[dict] = None


class ChatMessage(BaseModel):
    """OpenAI 格式的消息"""
    role: str  # "system" | "user" | "assistant" | "tool"
    content: Optional[Any] = None  # str 或 list[ChatMessageContent]
    name: Optional[str] = None
    tool_calls: Optional[list[dict]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    """OpenAI /v1/chat/completions 请求格式"""
    model: Optional[str] = None
    messages: list[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    tools: Optional[list[dict]] = None
    tool_choice: Optional[Any] = None
    user: Optional[str] = None
    session_id: Optional[str] = "default"
    password: Optional[str] = None
    enabled_tools: Optional[list[str]] = None
    # Per-request LLM model override (used by OASIS SessionExpert)
    llm_override: Optional[dict] = None
    max_turns: Optional[int] = None
    # Per-request session mode override: "execute" | "plan" | "bypass".
    # Wins over the stored session mode for this turn without writing the store.
    session_mode: Optional[str] = None


@dataclass
class OpenAIExecutionContext:
    """聊天补全执行上下文（dataclass）"""
    user_id: str
    session_id: str
    thread_id: str
    config: dict
    user_input: dict
    model_name: str
    external_tool_names: set[str]
    thread_lock: Any
    max_tokens: int | None = None
