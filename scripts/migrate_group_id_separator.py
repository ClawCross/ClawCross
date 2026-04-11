#!/usr/bin/env python3
"""
数据库迁移脚本：将 group_id 中的 # 分隔符替换为 ::

用法: python scripts/migrate_group_id_separator.py [--db-path data/group_chat.db] [--dry-run]

该脚本用于迁移历史数据中的 group_id 格式，将旧的分隔符 # 替换为新的 ::
主要影响 groups、group_members、group_messages 三张表的 group_id 字段
"""
import argparse
import asyncio
import os
import shutil
import sys

import aiosqlite


async def migrate(db_path: str, dry_run: bool = False) -> None:
    """执行 group_id 分隔符迁移"""
    if not os.path.exists(db_path):
        print(f"数据库不存在: {db_path}")
        return

    # 备份数据库（仅在非 dry_run 模式下执行）
    if not dry_run:
        backup_path = db_path + ".bak_before_separator_migration"
        if not os.path.exists(backup_path):
            shutil.copy2(db_path, backup_path)
            print(f"✅ 已备份到 {backup_path}")
        else:
            print(f"⚠️  备份文件已存在: {backup_path}，跳过备份")

    async with aiosqlite.connect(db_path) as db:
        # 查询所有包含旧分隔符 # 的 group_id
        cursor = await db.execute("SELECT group_id FROM groups WHERE group_id LIKE '%#%'")
        rows = await cursor.fetchall()

        if not rows:
            print("没有需要迁移的 group_id（全部已经使用 :: 分隔符）")
            return

        print(f"发现 {len(rows)} 个需要迁移的 group_id：")
        for (old_id,) in rows:
            # 只替换第一个 # → ::（保持 group_id 结构兼容）
            new_id = old_id.replace("#", "::", 1)
            print(f"  {old_id!r}  →  {new_id!r}")

        if dry_run:
            print("\n[DRY RUN] 不执行实际修改")
            return

        # 逐个迁移（需同时更新 groups/group_members/group_messages 三张表）
        for (old_id,) in rows:
            new_id = old_id.replace("#", "::", 1)
            # 按外键约束顺序：先更新子表再更新主表
            await db.execute(
                "UPDATE group_messages SET group_id = ? WHERE group_id = ?",
                (new_id, old_id),
            )
            await db.execute(
                "UPDATE group_members SET group_id = ? WHERE group_id = ?",
                (new_id, old_id),
            )
            await db.execute(
                "UPDATE groups SET group_id = ? WHERE group_id = ?",
                (new_id, old_id),
            )

        await db.commit()
        print(f"\n✅ 成功迁移 {len(rows)} 个 group_id")


def main():
    """命令行入口函数"""
    parser = argparse.ArgumentParser(description="将 group_id 分隔符从 # 迁移为 ::")
    parser.add_argument(
        "--db-path",
        default=os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "group_chat.db"),
        help="数据库文件路径 (默认: data/group_chat.db)",
    )
    parser.add_argument("--dry-run", action="store_true", help="只显示会做什么，不实际修改")
    args = parser.parse_args()

    asyncio.run(migrate(args.db_path, args.dry_run))


if __name__ == "__main__":
    main()
