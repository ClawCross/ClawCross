"""
OASIS Forum - 数据模型定义

本模块定义了 OASIS 讨论论坛的 API 请求/响应数据结构。
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class DiscussionStatus(str, Enum):
    """讨论状态枚举"""
    PENDING = "pending"      # 待开始
    DISCUSSING = "discussing"  # 进行中
    CONCLUDED = "concluded"    # 已结束
    CANCELLED = "cancelled"    # 已取消
    ERROR = "error"            # 错误


class CreateTopicRequest(BaseModel):
    """创建新讨论主题的请求体

    专家池由 schedule_yaml 或 schedule_file 构建（至少需要其中之一）。
    若两者同时提供，schedule_file 优先。
      "tag#temp#N" → ExpertAgent（临时专家）
      "tag#oasis#id" → SessionExpert（OASIS 管理会话）
      "title#sid" → SessionExpert（常规会话）
    Tag 用于查找专家的名称和人设。

    简单的全并行场景示例：
      version: 1
      repeat: true
      plan:
        - all_experts: true
    """
    question: str                    # 讨论主题
    user_id: str = "anonymous"       # 用户ID
    max_rounds: int = Field(default=5, ge=1, le=20)  # 最大轮数
    schedule_yaml: Optional[str] = None   # YAML 格式的调度配置
    schedule_file: Optional[str] = None    # 调度配置文件路径
    python_file: Optional[str] = None      # Python workflow 文件路径
    bot_enabled_tools: Optional[list[str]] = None  # 启用的工具列表
    bot_timeout: Optional[float] = None    # 超时时间（秒）
    early_stop: bool = False          # 是否启用提前终止
    discussion: Optional[bool] = None  # None=使用YAML设置; True=论坛讨论模式; False=执行模式
    callback_url: Optional[str] = None  # 讨论结束时回调的URL
    callback_session_id: Optional[str] = None  # 回调会话ID
    team: Optional[str] = None         # 团队名称（用于分组代理存储）
    autogen_swarm: bool = False        # 是否自动生成 swarm / GraphRAG 蓝图
    swarm_mode: Optional[str] = "prediction"  # swarm blueprint 模式


class ManualPostRequest(BaseModel):
    """向正在运行的讨论注入人工帖子的请求体"""
    user_id: str = "anonymous"            # 用户ID
    author: Optional[str] = None          # 作者名称
    content: str = Field(min_length=1, max_length=8000)  # 帖子内容
    reply_to: Optional[int] = None        # 回复目标帖子ID


class AgentCallbackRequest(BaseModel):
    """外部代理在运行中的主题提交的结构化回调"""
    user_id: str = "anonymous"                    # 用户ID
    author: str = Field(min_length=1, max_length=200)  # 代理作者
    round_num: int = Field(ge=0)              # 当前轮次
    result: dict[str, Any]                    # 回调结果数据


class ReportAskRequest(BaseModel):
    """基于 topic GraphRAG 记忆向 ReportAgent 提问。"""
    user_id: str = "anonymous"
    question: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=8, ge=3, le=20)


class AgentCallbackRequest(BaseModel):
    """Structured callback posted by an external agent during a running topic."""
    user_id: str = "anonymous"
    author: str = Field(min_length=1, max_length=200)
    round_num: int = Field(ge=0)
    result: dict[str, Any]


class HumanReplyRequest(BaseModel):
    """Plain-text reply that satisfies a waiting human workflow node."""
    user_id: str = "anonymous"
    node_id: str = Field(min_length=1)
    round_num: int = Field(ge=0)
    content: str = Field(min_length=1, max_length=8000)
    author: Optional[str] = None


class HumanWaitInfo(BaseModel):
    """Current waiting human node state, if any."""
    node_id: str
    prompt: str
    author: str
    round_num: int
    reply_to: Optional[int] = None


class PostInfo(BaseModel):
    """讨论线程中的单个帖子"""
    id: int                              # 帖子ID
    author: str                          # 作者
    content: str                         # 内容
    reply_to: Optional[int] = None       # 回复目标帖子ID
    upvotes: int = 0                      # 点赞数
    downvotes: int = 0                    # 点踩数
    timestamp: float                      # 时间戳
    elapsed: float = 0.0                  # 距离讨论开始的时间（秒）


class TimelineEventInfo(BaseModel):
    """时间线事件"""
    elapsed: float   # 距离讨论开始的时间（秒）
    event: str       # 事件类型
    agent: str = ""  # 关联的专家名称
    detail: str = "" # 详细信息


class TopicDetail(BaseModel):
    """Full detail of a discussion topic."""
    topic_id: str
    question: str
    user_id: str = "anonymous"
    status: DiscussionStatus
    current_round: int
    max_rounds: int
    posts: list[PostInfo]
    timeline: list[TimelineEventInfo] = []
    discussion: bool = True
    conclusion: Optional[str] = None
    pending_human: Optional[HumanWaitInfo] = None
    swarm_mode: Optional[str] = None       # swarm blueprint mode
    swarm: Optional[dict[str, Any]] = None  # 自动生成的 swarm / GraphRAG 蓝图


class TopicSummary(BaseModel):
    """讨论主题摘要（用于列表展示）"""
    topic_id: str              # 主题ID
    question: str              # 讨论问题
    user_id: str = "anonymous"  # 用户ID
    status: DiscussionStatus    # 当前状态
    post_count: int            # 帖子数量
    current_round: int         # 当前轮次
    max_rounds: int            # 最大轮数
    created_at: float          # 创建时间戳
    swarm_mode: Optional[str] = None
    has_swarm: bool = False
