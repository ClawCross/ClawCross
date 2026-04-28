"""
群聊数据持久化模块

提供群聊相关的 SQLite 数据库操作：
- 群组管理（创建、删除、查询）
- 群成员管理（添加、移除、查询）
- 群消息管理（插入、查询）
"""

import aiosqlite


async def init_group_db(group_db_path: str) -> None:
    """初始化群聊数据库表结构。

    groups: group_id = owner::name_safe (确定性 URL 安全 ID)
    group_members: global_id 是唯一标识（内部=session, 外部=global_name）
    group_messages: sender_display = tag#type#short_name
    """
    async with aiosqlite.connect(group_db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout = 5000")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                group_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                owner TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS group_members (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                short_name TEXT NOT NULL DEFAULT '',
                global_id TEXT NOT NULL DEFAULT '',
                is_agent INTEGER NOT NULL DEFAULT 1,
                member_type TEXT NOT NULL DEFAULT 'oasis',
                tag TEXT NOT NULL DEFAULT '',
                joined_at REAL NOT NULL,
                PRIMARY KEY (group_id, global_id),
                FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS group_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                sender TEXT NOT NULL,
                sender_display TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                attachments TEXT NOT NULL DEFAULT '[]',
                timestamp REAL NOT NULL,
                FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS http_agent_sessions (
                session_key TEXT NOT NULL,
                global_name TEXT NOT NULL DEFAULT '',
                prompt_text TEXT NOT NULL DEFAULT '',
                transport TEXT NOT NULL DEFAULT 'http',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_used_at REAL NOT NULL,
                PRIMARY KEY (session_key)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS group_mute_state (
                group_id TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                muted INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL,
                PRIMARY KEY (group_id, target_type, target_id),
                FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_http_agent_sessions_global_name
            ON http_agent_sessions(global_name)
        """)
        # ── Migrations for old databases ──
        # groups: remove team_name/custom_name (kept as no-op, SQLite can't DROP COLUMN easily)
        # group_members: add new columns
        for col, default in [("short_name", "''"), ("global_id", "''"), ("tag", "''"), ("member_type", "'oasis'")]:
            try:
                await db.execute(f"ALTER TABLE group_members ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}")
            except Exception:
                pass
        # group_messages: rename sender_session -> sender_display
        try:
            await db.execute("ALTER TABLE group_messages ADD COLUMN sender_display TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        # attachments migration
        try:
            await db.execute("ALTER TABLE group_messages ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")
        except Exception:
            pass
        cursor = await db.execute("PRAGMA table_info(http_agent_sessions)")
        http_cols = [row[1] for row in await cursor.fetchall()]
        needs_http_migration = bool(http_cols) and (
            "prompt_profile" in http_cols or "prompt_text" not in http_cols
        )
        if needs_http_migration:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS http_agent_sessions_new (
                    session_key TEXT NOT NULL PRIMARY KEY,
                    global_name TEXT NOT NULL DEFAULT '',
                    prompt_text TEXT NOT NULL DEFAULT '',
                    transport TEXT NOT NULL DEFAULT 'http',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_used_at REAL NOT NULL
                )
            """)
            src_prompt_col = "prompt_text" if "prompt_text" in http_cols else "''"
            src_updated_col = "updated_at" if "updated_at" in http_cols else "created_at"
            src_last_used_col = "last_used_at" if "last_used_at" in http_cols else "created_at"
            src_transport_col = "transport" if "transport" in http_cols else "'http'"
            rows_cur = await db.execute(
                f"""
                SELECT session_key, global_name, {src_prompt_col} AS prompt_text,
                       {src_transport_col} AS transport, created_at,
                       {src_updated_col} AS updated_at, {src_last_used_col} AS last_used_at
                FROM http_agent_sessions
                ORDER BY COALESCE({src_updated_col}, created_at) DESC, rowid DESC
                """
            )
            rows = await rows_cur.fetchall()
            for row in rows:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO http_agent_sessions_new (
                        session_key, global_name, prompt_text, transport,
                        created_at, updated_at, last_used_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(row),
                )
            await db.execute("DROP TABLE http_agent_sessions")
            await db.execute("ALTER TABLE http_agent_sessions_new RENAME TO http_agent_sessions")
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_http_agent_sessions_global_name
                ON http_agent_sessions(global_name)
            """)
        else:
            for col, default in [
                ("global_name", "''"),
                ("prompt_text", "''"),
                ("transport", "'http'"),
            ]:
                try:
                    await db.execute(f"ALTER TABLE http_agent_sessions ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}")
                except Exception:
                    pass
            for col in ("updated_at", "last_used_at"):
                try:
                    await db.execute(f"ALTER TABLE http_agent_sessions ADD COLUMN {col} REAL NOT NULL DEFAULT 0")
                except Exception:
                    pass
        await db.execute("DELETE FROM http_agent_sessions WHERE session_key LIKE 'agent:test-http-registry:%'")
        await db.commit()


async def list_group_member_targets(group_db_path: str, group_id: str) -> list[tuple]:
    """查询群成员（广播用途）。返回 (user_id, global_id, is_agent, member_type, short_name, tag)"""
    async with aiosqlite.connect(group_db_path) as db:
        cursor = await db.execute(
            "SELECT user_id, global_id, is_agent, member_type, short_name, tag FROM group_members WHERE group_id = ?",
            (group_id,),
        )
        return await cursor.fetchall()


async def create_group_with_members(
    group_db_path: str,
    *,
    group_id: str,
    name: str,
    owner: str,
    created_at: float,
    members: list[dict],
) -> None:
    async with aiosqlite.connect(group_db_path) as db:
        await db.execute(
            "INSERT INTO groups (group_id, name, owner, created_at) VALUES (?, ?, ?, ?)",
            (group_id, name, owner, created_at),
        )
        # owner 不是 agent
        await db.execute(
            "INSERT INTO group_members (group_id, user_id, short_name, global_id, is_agent, member_type, tag, joined_at) VALUES (?, ?, ?, ?, 0, 'owner', '', ?)",
            (group_id, owner, owner, owner, created_at),
        )
        for m in members:
            m_uid = m.get("user_id", "")
            m_global = m.get("global_id", "")
            m_short = m.get("short_name", "")
            m_type = m.get("member_type", "oasis")
            m_tag = m.get("tag", "")
            if m_global:
                await db.execute(
                    "INSERT OR IGNORE INTO group_members (group_id, user_id, short_name, global_id, is_agent, member_type, tag, joined_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
                    (group_id, m_uid, m_short, m_global, m_type, m_tag, created_at),
                )
        await db.commit()


async def list_groups_for_user(group_db_path: str, user_id: str) -> list[dict]:
    async with aiosqlite.connect(group_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT g.group_id, g.name, g.owner, g.created_at,
                   (SELECT COUNT(*) FROM group_members WHERE group_id = g.group_id) as member_count,
                   (SELECT COUNT(*) FROM group_messages WHERE group_id = g.group_id) as message_count,
                   (SELECT MAX(timestamp) FROM group_messages WHERE group_id = g.group_id) as last_message_time
            FROM groups g
            WHERE g.owner = ? OR g.group_id IN (
                SELECT group_id FROM group_members WHERE user_id = ?
            )
            ORDER BY g.created_at DESC
            """,
            (user_id, user_id),
        )
        rows = await cursor.fetchall()
        results = [dict(r) for r in rows]

        # Fetch up to 4 member short_names per group for avatar grid display.
        # Agents first (私聊列表头像需要对方 agent 名；群主行 is_agent=0 先入库会占首位)
        for d in results:
            db.row_factory = None
            cursor2 = await db.execute(
                "SELECT short_name FROM group_members WHERE group_id = ? "
                "ORDER BY is_agent DESC, joined_at ASC LIMIT 4",
                (d["group_id"],),
            )
            rows2 = await cursor2.fetchall()
            d["member_names"] = [row[0] or "?" for row in rows2]
            db.row_factory = aiosqlite.Row

    return results


async def get_group(group_db_path: str, group_id: str) -> dict | None:
    async with aiosqlite.connect(group_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM groups WHERE group_id = ?", (group_id,))
        row = await cursor.fetchone()
    return dict(row) if row else None


async def list_group_members(group_db_path: str, group_id: str) -> list[dict]:
    async with aiosqlite.connect(group_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT user_id, short_name, global_id, is_agent, member_type, tag, joined_at FROM group_members WHERE group_id = ?",
            (group_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_group_member_by_global_id(group_db_path: str, group_id: str, global_id: str) -> dict | None:
    """根据 global_id 查询群内某个成员的信息。"""
    async with aiosqlite.connect(group_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT user_id, short_name, global_id, is_agent, member_type, tag FROM group_members WHERE group_id = ? AND global_id = ?",
            (group_id, global_id),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def list_recent_group_messages(group_db_path: str, group_id: str, limit: int = 100) -> list[dict]:
    async with aiosqlite.connect(group_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, sender, sender_display, content, attachments, timestamp FROM group_messages WHERE group_id = ? ORDER BY id DESC LIMIT ?",
            (group_id, limit),
        )
        rows = await cursor.fetchall()
    messages = [dict(r) for r in rows]
    messages.reverse()
    return messages


async def list_group_messages_after(
    group_db_path: str,
    group_id: str,
    after_id: int,
    limit: int = 200,
) -> list[dict]:
    async with aiosqlite.connect(group_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, sender, sender_display, content, attachments, timestamp FROM group_messages WHERE group_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
            (group_id, after_id, limit),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def group_exists(group_db_path: str, group_id: str) -> bool:
    async with aiosqlite.connect(group_db_path) as db:
        cursor = await db.execute("SELECT group_id FROM groups WHERE group_id = ?", (group_id,))
        return (await cursor.fetchone()) is not None


async def find_group_by_owner_and_name(group_db_path: str, owner: str, name: str) -> dict | None:
    """按 owner + name 查找已有群聊，用于创建群时的幂等判断。"""
    async with aiosqlite.connect(group_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT group_id, name, owner FROM groups WHERE owner = ? AND name = ?",
            (owner, name),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def insert_group_message(
    group_db_path: str,
    *,
    group_id: str,
    sender: str,
    sender_display: str,
    content: str,
    attachments: str = "[]",
    timestamp: float,
) -> int:
    async with aiosqlite.connect(group_db_path) as db:
        cursor = await db.execute(
            "INSERT INTO group_messages (group_id, sender, sender_display, content, attachments, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (group_id, sender, sender_display, content, attachments, timestamp),
        )
        msg_id = cursor.lastrowid
        await db.commit()
    return msg_id


async def get_group_owner(group_db_path: str, group_id: str) -> str | None:
    async with aiosqlite.connect(group_db_path) as db:
        cursor = await db.execute("SELECT owner FROM groups WHERE group_id = ?", (group_id,))
        row = await cursor.fetchone()
    return row[0] if row else None


async def update_group_name(group_db_path: str, group_id: str, name: str) -> None:
    async with aiosqlite.connect(group_db_path) as db:
        await db.execute("UPDATE groups SET name = ? WHERE group_id = ?", (name, group_id))
        await db.commit()


async def add_group_member(
    group_db_path: str,
    *,
    group_id: str,
    user_id: str,
    short_name: str,
    global_id: str,
    member_type: str = "oasis",
    tag: str = "",
    joined_at: float,
) -> None:
    # owner 不是 agent，不应被广播触发
    is_agent = 0 if member_type == "owner" else 1
    async with aiosqlite.connect(group_db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO group_members (group_id, user_id, short_name, global_id, is_agent, member_type, tag, joined_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (group_id, user_id, short_name, global_id, is_agent, member_type, tag, joined_at),
        )
        await db.commit()


async def remove_group_member(
    group_db_path: str,
    *,
    group_id: str,
    global_id: str,
) -> None:
    async with aiosqlite.connect(group_db_path) as db:
        await db.execute(
            "DELETE FROM group_members WHERE group_id = ? AND global_id = ?",
            (group_id, global_id),
        )
        await db.commit()


async def delete_group(group_db_path: str, group_id: str) -> None:
    async with aiosqlite.connect(group_db_path) as db:
        await db.execute("DELETE FROM group_messages WHERE group_id = ?", (group_id,))
        await db.execute("DELETE FROM group_members WHERE group_id = ?", (group_id,))
        await db.execute("DELETE FROM groups WHERE group_id = ?", (group_id,))
        await db.commit()


async def clear_group_members(
    group_db_path: str,
    *,
    group_id: str,
    keep_owners: bool = True,
) -> None:
    """Clear all members from a group, optionally keeping owners.

    Args:
        group_db_path: Path to the SQLite database
        group_id: The group ID
        keep_owners: If True, keep members with member_type='owner'
    """
    async with aiosqlite.connect(group_db_path) as db:
        if keep_owners:
            await db.execute(
                "DELETE FROM group_members WHERE group_id = ? AND member_type != 'owner'",
                (group_id,),
            )
        else:
            await db.execute(
                "DELETE FROM group_members WHERE group_id = ?",
                (group_id,),
            )
        await db.commit()


async def set_group_mute_state(
    group_db_path: str,
    *,
    group_id: str,
    target_type: str,
    target_id: str,
    muted: bool,
    updated_at: float,
) -> None:
    async with aiosqlite.connect(group_db_path) as db:
        await db.execute(
            """
            INSERT INTO group_mute_state (group_id, target_type, target_id, muted, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(group_id, target_type, target_id)
            DO UPDATE SET muted = excluded.muted, updated_at = excluded.updated_at
            """,
            (group_id, target_type, target_id, 1 if muted else 0, updated_at),
        )
        await db.commit()


async def get_group_mute_state(
    group_db_path: str,
    *,
    group_id: str,
    target_type: str,
    target_id: str,
) -> bool:
    async with aiosqlite.connect(group_db_path) as db:
        cursor = await db.execute(
            "SELECT muted FROM group_mute_state WHERE group_id = ? AND target_type = ? AND target_id = ?",
            (group_id, target_type, target_id),
        )
        row = await cursor.fetchone()
    return bool(row[0]) if row else False


async def list_group_mute_states(group_db_path: str, *, group_id: str) -> list[dict]:
    async with aiosqlite.connect(group_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT group_id, target_type, target_id, muted, updated_at FROM group_mute_state WHERE group_id = ?",
            (group_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def upsert_http_agent_session(
    group_db_path: str,
    *,
    session_key: str,
    global_name: str,
    prompt_text: str,
    transport: str,
    now_ts: float,
) -> bool:
    async with aiosqlite.connect(group_db_path) as db:
        await db.execute("PRAGMA busy_timeout = 5000")
        cursor = await db.execute(
            """
            SELECT prompt_text
            FROM http_agent_sessions
            WHERE session_key = ?
            """,
            (session_key,),
        )
        row = await cursor.fetchone()
        should_inject = row is None or (row[0] or "") != prompt_text
        if not should_inject:
            return False
        if row is None:
            await db.execute(
                """
                INSERT INTO http_agent_sessions (
                    session_key, global_name, prompt_text, transport,
                    created_at, updated_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_key, global_name, prompt_text, transport, now_ts, now_ts, now_ts),
            )
        else:
            await db.execute(
                """
                UPDATE http_agent_sessions
                SET global_name = ?, prompt_text = ?, transport = ?, updated_at = ?, last_used_at = ?
                WHERE session_key = ?
                """,
                (global_name, prompt_text, transport, now_ts, now_ts, session_key),
            )
        await db.commit()
    return should_inject


async def delete_http_agent_sessions_by_global_name(group_db_path: str, global_name: str) -> int:
    async with aiosqlite.connect(group_db_path) as db:
        await db.execute("PRAGMA busy_timeout = 5000")
        cursor = await db.execute(
            "DELETE FROM http_agent_sessions WHERE global_name = ?",
            (global_name,),
        )
        await db.commit()
    return cursor.rowcount or 0


async def list_http_agent_sessions(group_db_path: str) -> list[dict]:
    """Return all http_agent_sessions records, oldest first."""
    async with aiosqlite.connect(group_db_path) as db:
        await db.execute("PRAGMA busy_timeout = 5000")
        cursor = await db.execute(
            """
            SELECT session_key, global_name, prompt_text, transport,
                   created_at, updated_at, last_used_at
            FROM http_agent_sessions
            ORDER BY last_used_at DESC
            """,
        )
        rows = await cursor.fetchall()
    return [
        {
            "session_key": r[0],
            "global_name": r[1],
            "prompt_text": r[2],
            "transport": r[3],
            "created_at": r[4],
            "updated_at": r[5],
            "last_used_at": r[6],
        }
        for r in rows
    ]


async def delete_http_agent_session_by_key(group_db_path: str, session_key: str) -> int:
    """Delete a single http_agent_sessions record by session_key."""
    async with aiosqlite.connect(group_db_path) as db:
        await db.execute("PRAGMA busy_timeout = 5000")
        cursor = await db.execute(
            "DELETE FROM http_agent_sessions WHERE session_key = ?",
            (session_key,),
        )
        await db.commit()
    return cursor.rowcount or 0
