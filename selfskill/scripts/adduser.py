#!/usr/bin/env python3
"""
非交互式用户创建工具。供外部 agent 调用。

用法:
    python selfskill/scripts/adduser.py <username> <password>

如果用户已存在则更新密码，否则新增。
"""
import hashlib
import json
import os
import sys

# 项目根目录路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 用户配置文件路径
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "users.json")


def hash_password(password: str) -> str:
    """对密码进行 SHA256 哈希处理。"""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def main():
    """主函数：创建或更新用户。"""
    # 检查命令行参数数量
    if len(sys.argv) != 3:
        print("用法: python skill/scripts/adduser.py <username> <password>", file=sys.stderr)
        sys.exit(1)

    username, password = sys.argv[1], sys.argv[2]

    # 验证用户名和密码非空
    if not username or not password:
        print("用户名和密码不能为空", file=sys.stderr)
        sys.exit(1)

    # 读取现有用户数据
    users = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            users = json.load(f)

    # 判断是新建还是更新操作
    operation = "updated" if username in users else "created"
    users[username] = hash_password(password)

    # 确保目录存在并写入配置文件
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=4)

    print(f"✅ User '{username}' {operation}")


if __name__ == "__main__":
    main()
