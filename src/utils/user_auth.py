"""
用户认证模块

提供用户密码验证功能：
- 从 users.json 加载用户信息
- 验证用户名和密码（SHA-256 哈希比对）
"""

import hashlib
import json
import os

from utils.logging_utils import get_logger

logger = get_logger("user_auth")


def load_users(users_path: str) -> dict:
    """从 users.json 加载用户数据。

    :param users_path: users.json 文件路径
    :return: 用户名到密码哈希的映射字典
    """
    if not os.path.exists(users_path):
        logger.warning("未找到用户配置文件 %s，请先运行 python tools/gen_password.py 创建用户", users_path)
        return {}
    with open(users_path, "r", encoding="utf-8") as f:
        return json.load(f)


def verify_password(users_path: str, username: str, password: str) -> bool:
    """验证用户名和密码（SHA-256 哈希比对）。

    :param users_path: users.json 文件路径
    :param username: 用户名
    :param password: 明文密码
    :return: 验证是否通过
    """
    users = load_users(users_path)
    if username not in users:
        return False
    pw_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return pw_hash == users[username]
