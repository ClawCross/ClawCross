"""
认证工具模块

提供 Bearer token 解析和用户认证辅助函数：
- 解析 Authorization header
- 验证内部服务令牌
- 提取用户/密码/会话信息
"""

from typing import List, Optional, Tuple


def parse_bearer_parts(authorization: Optional[str]) -> Optional[List[str]]:
    """解析 Bearer token 为冒号分隔的各部分。

    :param authorization: Authorization header 值（格式: "Bearer user:password:session"）
    :return: 分割后的列表，解析失败返回 None
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    return token.split(":")


def is_internal_bearer(parts: Optional[List[str]], internal_token: str) -> bool:
    """判断解析后的 bearer parts 是否匹配内部服务令牌。

    :param parts: parse_bearer_parts 返回的列表
    :param internal_token: 内部服务令牌
    :return: 是否匹配
    """
    return bool(parts) and parts[0] == internal_token


def extract_user_password_session(
    parts: Optional[List[str]],
    *,
    default_session: str = "default",
) -> Optional[Tuple[str, str, str]]:
    """从解析后的 bearer parts 中提取用户身份信息。

    :param parts: parse_bearer_parts 返回的列表
    :param default_session: 会话 ID 未提供时的默认值
    :return: (user_id, password, session_id) 元组，解析失败返回 None
    """
    if not parts or len(parts) < 2:
        return None
    user_id = parts[0]
    password = parts[1]
    session_id = parts[2] if len(parts) > 2 and parts[2] else default_session
    return user_id, password, session_id
