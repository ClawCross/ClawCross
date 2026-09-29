"""
系统触发相关的数据模型模块

定义系统触发请求和附件格式：
- SystemTriggerAttachment：系统触发消息附件
- SystemTriggerRequest：系统触发请求
"""

from typing import Optional

from pydantic import BaseModel


class SystemTriggerAttachment(BaseModel):
    """系统触发消息中的附件（与群聊 Attachment 格式一致）"""
    type: str          # "image" | "audio" | "file"
    name: str          # 文件名
    data: str          # base64 编码内容
    mime_type: str     # MIME 类型


class SystemTriggerRequest(BaseModel):
    """系统触发请求"""
    user_id: str
    text: str = "summary"
    session_id: str  # the number of the agent it is for
    attachments: Optional[list[SystemTriggerAttachment]] = None
    coalesce_key: str = ""
    # Per-trigger permission overrides. session_mode: "manual" | "plan" | "bypass".
    # enabled_tools=[] is the explicit "no tools" signal (manual mode); None = default.
    session_mode: Optional[str] = None
    enabled_tools: Optional[list[str]] = None
    # OpenAI-shaped forced reply format for this trigger's turn — same
    # contract as ChatCompletionRequest.response_format (see agents/openai.py).
    response_format: Optional[dict] = None
    llm_override: Optional[dict] = None  # the model for this turn (the agent's own)
    # Hold the request until this trigger's turn has run and return the agent's
    # reply. The turn still queues behind the session's current run like any
    # other trigger — waiting is the caller's choice, never an interruption.
    wait_reply: bool = False
    # Cross-session delivery uses the durable inbox. A source session creates
    # an entry; drain_inbox wakes the same worker for entries already stored.
    inbox_source_session: str = ""
    inbox_source_user: str = ""
    inbox_source_label: str = ""
    inbox_summary: str = ""
    drain_inbox: bool = False
