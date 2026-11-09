#!/usr/bin/env python3
"""
用户密码哈希生成工具。
用法：python tools/gen_password.py
会交互式输入用户名和密码，输出追加到 config/users.json。
"""
import hashlib
import json
import os
import getpass

from src.utils.runtime_paths import USERS_FILE, ensure_runtime_dirs

# 用户配置文件路径
CONFIG_FILE_PATH = str(USERS_FILE)


def hash_password(password: str) -> str:
    """使用 SHA-256 对密码进行哈希处理。"""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def main():
    ensure_runtime_dirs()
    # 加载已有用户
    users_database = {}
    if os.path.exists(CONFIG_FILE_PATH):
        with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
            users_database = json.load(f)
        print(f"已加载 {len(users_database)} 个用户: {', '.join(users_database.keys())}")
    else:
        print("未检测到 users.json，将创建新文件。")

    print("-" * 40)
    username = input("请输入用户名: ").strip()
    if not username:
        print("用户名不能为空！")
        return

    password = getpass.getpass("请输入密码: ")
    if not password:
        print("密码不能为空！")
        return

    password_confirm = getpass.getpass("请再次输入密码: ")
    if password != password_confirm:
        print("两次密码不一致！")
        return

    password_hash = hash_password(password)
    users_database[username] = password_hash

    with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(users_database, f, ensure_ascii=False, indent=4)

    print(f"\n用户 '{username}' 已保存到 {CONFIG_FILE_PATH}")
    print(f"   哈希值: {password_hash}")


if __name__ == "__main__":
    main()
