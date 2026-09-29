"""
会话管理相关的数据模型模块

定义会话列表、历史、删除、状态等请求模型：
- SessionListRequest：会话列表请求
- SessionHistoryRequest：会话历史请求
- DeleteSessionRequest：删除会话请求
- SessionStatusRequest：会话状态请求
"""

from pydantic import BaseModel


class SessionListRequest(BaseModel):
    """会话列表请求"""
    user_id: str
    password: str = ""  # Optional when using X-Internal-Token


class SessionHistoryRequest(BaseModel):
    """会话历史请求"""
    user_id: str
    password: str = ""  # Optional when using X-Internal-Token
    session_id: str


class DeleteSessionRequest(BaseModel):
    """删除会话请求"""
    user_id: str
    password: str = ""  # Optional when using X-Internal-Token
    session_id: str = ""  # 为空则删除该用户所有会话


class SessionStatusRequest(BaseModel):
    """会话状态请求"""
    user_id: str
    password: str = ""  # Optional when using X-Internal-Token
    session_id: str = "default"
    peek: bool = False


class CompactSessionRequest(BaseModel):
    """手动压缩会话请求"""
    user_id: str
    password: str = ""  # Optional when using X-Internal-Token
    session_id: str
