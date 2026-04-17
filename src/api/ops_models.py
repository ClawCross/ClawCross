"""
Ops 操作服务的数据模型模块

定义登录、TTS、ACP 外部 agent 控制相关的请求模型：
- LoginRequest：登录请求
- CancelRequest：取消任务请求
- TTSRequest：文本转语音请求
- ACPControlRequest：ACP 外部 agent 控制请求
- ACPStatusRequest：ACP agent 状态查询请求
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel


class LoginRequest(BaseModel):
    """登录请求"""
    user_id: str
    password: str


class CancelRequest(BaseModel):
    """取消任务请求"""
    user_id: str
    password: str = ""  # Optional when using X-Internal-Token
    session_id: str = "default"


class TTSRequest(BaseModel):
    """文本转语音请求"""
    user_id: str
    password: str = ""  # Optional when using X-Internal-Token
    text: str
    voice: Optional[str] = None


class ACPControlRequest(BaseModel):
    """对外部 ACP agent 执行控制命令。

    Session routing 通过命令行 --session 参数传入（bridge 当前版本不处理 _meta.sessionKey）。
    - reset_session: 通过命令行 --reset-session 标志强制生成新 session ID（同 key 下刷新状态）
    - delete: 关闭该 agent 对应的 ACP session（用于真实删除前的清理）
    """
    user_id: str
    password: str = ""
    team: str                              # 群组/团队名
    group_id: str = ""                     # 群聊 id；若提供则与 broadcast_to_group 相同方式解析 ext 成员
    agent_name: str                        # 外部 agent 的 name (= session_id in members)
    action: Literal["new", "stop", "delete"]  # delete=关闭 ACP session
    reset_session: bool = False            # 重置会话（通过 --reset-session 命令行标志实现）


class ACPStatusRequest(BaseModel):
    """查询外部 agent 的 session 列表 / 运行状态。"""
    user_id: str
    password: str = ""
    team: str
    agent_name: str = ""                   # 为空则查所有外部 agent

class SessionsListRequest(BaseModel):
    """列出所有 acpx sessions 和 http_agent_sessions。"""
    user_id: str
    password: str = ""

class SessionsDeleteRequest(BaseModel):
    """删除指定的 http_agent_session 记录。"""
    user_id: str
    password: str = ""
    session_key: str

class SessionsCloseRequest(BaseModel):
    """关闭指定的 acpx session。"""
    user_id: str
    password: str = ""
    platform: str
    session_name: str
