"""
群聊相关的数据模型模块

定义群聊消息、成员管理、创建群聊等请求模型：
- Attachment：群聊消息附件
- GroupCreateRequest：创建群聊请求
- GroupUpdateRequest：更新群聊请求
- GroupMessageRequest：发送群聊消息请求
- GroupAddMemberRequest：添加群聊成员请求
"""

from typing import Optional

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    """群聊消息附件（图片/音频/文件）"""
    type: str          # "image" | "audio" | "file"
    name: str          # 文件名，如 "photo.png"
    data: str          # base64 编码内容（不含 data:...;base64, 前缀）
    mime_type: str     # "image/png", "application/pdf", "audio/mp3" 等


class GroupCreateRequest(BaseModel):
    """创建群聊请求"""
    name: str
    team_name: Optional[str] = None  # 用于从 team 快速加载成员，不存库
    custom_name: Optional[str] = None  # 自定义群聊标识


class GroupUpdateRequest(BaseModel):
    """更新群聊请求"""
    name: Optional[str] = None


class GroupMessageRequest(BaseModel):
    """发送群聊消息请求"""
    content: str
    sender: Optional[str] = None       # 人类发消息时可省略（自动取 owner）
    sender_display: Optional[str] = ""  # agent 发言显示名: tag#type#short_name
    mentions: Optional[list[str]] = None  # 被 @ 的 agent global_id 列表
    attachments: Optional[list[Attachment]] = None  # 附件列表（图片/音频/文件）


class GroupAddMemberRequest(BaseModel):
    """添加群聊成员请求"""
    short_name: str                     # agent 短名 (如 testtool, xl1)
    global_id: str                      # 唯一标识 (内部=session, 外部=global_name)
    member_type: str = "oasis"          # "oasis" (内部) 或 "ext" (外部)
    tag: str = ""                       # agent 的 tag


class GroupMuteMemberRequest(BaseModel):
    """群成员禁言/解禁"""
    global_id: str
    muted: bool = True


class GroupMuteAllRequest(BaseModel):
    """全员禁言/解禁（仅 agent）"""
    muted: bool = True
