"""
设置更新请求模型
"""

from pydantic import BaseModel


class SettingsUpdateRequest(BaseModel):
    """设置更新请求"""
    user_id: str  # 用户标识
    password: str = ""  # 使用 X-Internal-Token 时可选
    settings: dict  # 要更新的设置项


class ChatbotWhitelistUpdateRequest(BaseModel):
    """Chatbot 白名单更新请求"""
    user_id: str
    password: str = ""
    whitelist: dict
