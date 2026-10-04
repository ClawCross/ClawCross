"""
设置更新请求模型
"""

from pydantic import BaseModel


class SettingsUpdateRequest(BaseModel):
    """设置更新请求"""
    user_id: str  # 用户标识
    password: str = ""  # 使用 X-Internal-Token 时可选
    settings: dict  # 要更新的设置项


class ChannelWhitelistUpdateRequest(BaseModel):
    """渠道白名单更新请求"""
    user_id: str
    password: str = ""
    whitelist: dict


class ChannelSetupRequest(BaseModel):
    user_id: str
    password: str = ''
    session_id: str = 'default'
    channel: str = ''
    request_id: str = ''
    values: dict = {}
    cancel: bool = False


class ConfigurationSetupRequest(ChannelSetupRequest):
    topic: str = ''
