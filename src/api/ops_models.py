"""
Ops 操作服务的数据模型模块

定义登录、TTS、ACP 外部 agent 控制相关的请求模型：
- LoginRequest：登录请求
- TTSRequest：文本转语音请求
"""

from typing import Optional

from pydantic import BaseModel


class LoginRequest(BaseModel):
    """登录请求"""
    user_id: str
    password: str


class TTSRequest(BaseModel):
    """文本转语音请求"""
    user_id: str
    password: str = ""  # Optional when using X-Internal-Token
    text: str
    voice: Optional[str] = None


class SessionsListRequest(BaseModel):
    """列出所有 acpx sessions。"""
    user_id: str
    password: str = ""


class SessionsCloseRequest(BaseModel):
    """关闭指定的 acpx session。"""
    user_id: str
    password: str = ""
    platform: str
    session_name: str
    # acpx binds each session to the cwd it was created in; ``sessions close``
    # only acts on the current cwd. Pass the session's own cwd (from the list
    # row) or close silently no-ops with exit 0.
    cwd: str = ""


class UpdateCheckRequest(BaseModel):
    user_id: str
    password: str = ""
    refresh_remote: bool = True


class UpdateStartRequest(BaseModel):
    user_id: str
    password: str = ""
    branch: str = ""


class UpdateStatusRequest(BaseModel):
    user_id: str
    password: str = ""
