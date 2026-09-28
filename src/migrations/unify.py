"""Upgrades of ``clawcross.db`` and the files it replaces, run in order at start.

Version 1 (2026-09) — agents, teams and group chat move into ``clawcross.db``.
Before: agents were entries in every team folder's ``internal_agents.json`` /
``external_agents.json`` (and the user root's), and group chat lived in
``group_chat.db`` keyed by those entries' session / global_name. After: one
agent record each, team membership rows, and conversations whose members and
senders are agent ids; the imported manifest files are removed.

Version 2 — what an agent's runtime already knows lives on the agent record,
and what version 1 left behind goes: ``group_chat.db``, the ``.migrated``
backups and old file locks, the ``agent_runtime_sessions`` and ``migrations``
tables. An external runtime is told its identity again once.

``PRAGMA user_version`` holds the version a database has reached.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
from pathlib import Path

from agents.store import ACPX, HTTP, OPENCLAW, WEBOT, Agent, AgentExists, AgentStore
from comms.store import DIRECT, GROUP, ConversationStore, human
from teams.manifest import EXTERNAL_FILE, INTERNAL_FILE, agent_for_external_entry, agent_for_internal_entry, import_entries, read_folder
from teams.store import TeamStore

logger = logging.getLogger(__name__)

VERSION = 2


def _version(store: AgentStore) -> int:
    conn = store._connect()
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0 and conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'migrations'").fetchone():
            # Version 1 was first recorded in a table of its own.
            if conn.execute("SELECT 1 FROM migrations WHERE name = '2026-09-unify-agents'").fetchone():
                version = 1
        return version
    finally:
        conn.close()


def _set_version(store: AgentStore, version: int) -> None:
    conn = store._connect()
    try:
        conn.execute(f"PRAGMA user_version = {int(version)}")
    finally:
        conn.close()


def _remove_imported(folder: Path) -> None:
    """The manifest files of *folder* have been imported: remove them and their old lock."""
    for name in (INTERNAL_FILE, EXTERNAL_FILE):
        (folder / name).unlink(missing_ok=True)
        (folder / f".{name}.lock").unlink(missing_ok=True)


def migrate_manifests(teams: TeamStore) -> None:
    root = teams.user_files_dir
    if not root.is_dir():
        return
    for owner_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        owner = owner_dir.name
        # Teams in sorted order first: an agent listed in several teams takes the
        # first as its home, as the runtime used to decide.
        for team in teams.teams(owner):
            folder = teams.folder(owner, team)
            internal, external = read_folder(folder)
            if internal or external:
                try:
                    import_entries(teams, owner, team, internal, [e for e in external if e.get("global_name")])
                except Exception:
                    logger.exception("migrating team %s/%s failed; its files stay in place", owner, team)
                    continue
            _remove_imported(folder)
        # Agents declared at the user root belong to no team.
        internal, external = read_folder(owner_dir)
        for entry in internal:
            agent_for_internal_entry(teams, owner, "", entry)
        for entry in external:
            if entry.get("global_name"):
                agent_for_external_entry(teams, owner, "", entry)
        _remove_imported(owner_dir)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _member_agent(agents: AgentStore, owner: str, row: dict) -> Agent | None:
    gid = str(row.get("global_id") or "").strip()
    if not gid:
        return None
    if row.get("member_type") == "ext":
        for driver in (ACPX, OPENCLAW, HTTP):
            found = agents.find(owner, driver, {"global_name": gid})
            if found:
                return found
        return None
    found = agents.find(owner, WEBOT, {"session": gid})
    if found:
        return found
    # A chat session added to a group without being named: it becomes an agent.
    try:
        return agents.create(owner, name=str(row.get("short_name") or gid), driver=WEBOT,
                             config={"session": gid, "persona": str(row.get("tag") or ""), "team": ""})
    except AgentExists as exc:
        return exc.agent


def migrate_groups(old_db: Path, conversations: ConversationStore) -> None:
    if not old_db.is_file():
        return
    agents = conversations.agents
    src = sqlite3.connect(f"{old_db.resolve().as_uri()}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "groups" not in tables:
            return
        group_cols = _columns(src, "groups")
        message_cols = _columns(src, "group_messages")
        for g in [dict(r) for r in src.execute("SELECT * FROM groups")]:
            gid, owner = g["group_id"], g["owner"]
            if conversations.get(gid) is not None:
                continue
            members = [dict(r) for r in src.execute("SELECT * FROM group_members WHERE group_id = ?", (gid,))]
            principal_of: dict[str, str] = {owner: human(owner)}
            agent_ids: list[str] = []
            for m in members:
                if not m.get("is_agent"):
                    continue
                agent = _member_agent(agents, owner, m)
                if agent is not None:
                    principal_of[str(m["global_id"])] = agent.agent_id
                    if agent.agent_id not in agent_ids:
                        agent_ids.append(agent.agent_id)
            kind = g.get("kind") if "kind" in group_cols else ""
            kind = DIRECT if kind == DIRECT else GROUP
            team = str(g.get("team") or "") if "team" in group_cols else ""
            # Old names carried a suffix the UI hid: "Coder#<session>" for a private chat,
            # "team#custom" for a named team group.
            head, _, tail = str(g["name"] or "").partition("#")
            if tail and len(agent_ids) == 1 and principal_of.get(tail) == agent_ids[0]:
                kind, title = DIRECT, head
            else:
                title = tail or head
            conversations.create(owner, title or gid, kind, members=agent_ids, meta={"team": team} if team else {},
                                 conv_id=gid, created_at=g["created_at"])
            primary = principal_of.get(str(g.get("primary_agent_global_id") or ""))
            if primary:
                conversations.update(gid, primary_agent=primary)

            for msg in [dict(r) for r in src.execute("SELECT * FROM group_messages WHERE group_id = ? ORDER BY id", (gid,))]:
                display = str(msg.get("sender_display") or "")
                gid_from_display = display.rsplit("#", 1)[-1] if display.count("#") >= 3 else ""
                session_from_sender = str(msg["sender"]).split("#", 1)[1] if "#" in str(msg["sender"]) else ""
                sender = (principal_of.get(gid_from_display) or principal_of.get(session_from_sender)
                          or principal_of.get(str(msg["sender"])))
                if sender is None:
                    parts = display.split("#")
                    sender = (parts[2] if len(parts) >= 3 and parts[2] else display or str(msg["sender"]))
                mentions = json.loads(msg.get("mentions") or "[]") if "mentions" in message_cols else []
                conversations.add_message(
                    gid, sender, msg["content"],
                    mentions=[principal_of[m] for m in mentions if m in principal_of],
                    reply_to=msg.get("reply_to") if "reply_to" in message_cols else None,
                    attachments=json.loads(msg.get("attachments") or "[]"),
                    client_msg_id=(msg.get("client_msg_id") or None) if "client_msg_id" in message_cols else None,
                    created_at=msg["timestamp"],
                )
            last = conversations.last_message(gid)
            for principal in agent_ids:
                if last:
                    conversations.advance_cursor(gid, principal, last.id)

            if "group_mute_state" in tables:
                for row in src.execute("SELECT * FROM group_mute_state WHERE group_id = ? AND muted = 1", (gid,)):
                    if row["target_type"] == "dnd":
                        conversations.update(gid, dnd=1)
                    elif row["target_type"] == "member" and row["target_id"] in principal_of:
                        conversations.set_muted(gid, principal_of[row["target_id"]], True)
                    elif row["target_type"] == "all_agents":
                        for principal in agent_ids:
                            conversations.set_muted(gid, principal, True)
    finally:
        src.close()


def _alarm_agent(teams: TeamStore, info: dict) -> Agent | None:
    owner = str(info.get("user_id") or "")
    agents = teams.agents
    if str(info.get("target_type") or "internal") == "external":
        name, team = str(info.get("target_name") or ""), str(info.get("team") or "")
        if name and teams.exists(owner, team):
            with_name = [m.agent for m in teams.members(owner, team) if m.role == name]
            if len(with_name) == 1:
                return with_name[0]
        ref = str(info.get("target_ref") or "")
        return next((a for d in (ACPX, OPENCLAW, HTTP) if (a := agents.find(owner, d, {"global_name": ref}))), None)
    session = str(info.get("target_ref") or info.get("session_id") or "default")
    found = agents.find(owner, WEBOT, {"session": session})
    if found:
        return found
    return agents.create(owner, name="主助手" if session == "default" else session, driver=WEBOT,
                         config={"session": session, "persona": "", "team": ""})


def migrate_alarms(data_dir: Path, teams: TeamStore) -> None:
    path = Path(data_dir) / "timeset" / "tasks.json"
    try:
        tasks = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for task_id, info in tasks.items():
        if not isinstance(info, dict) or info.get("agent"):
            continue
        agent = _alarm_agent(teams, info)
        if agent is None:
            logger.warning("alarm %s: its target no longer exists; it will be skipped", task_id)
        for key in ("session_id", "target_type", "target_ref", "target_name"):
            info.pop(key, None)
        info["agent"] = agent.agent_id if agent else ""
    path.write_text(json.dumps(tasks, ensure_ascii=False, indent=4), encoding="utf-8")


_EXPERT_LINE = re.compile(r"^(?P<indent>[ \t]*)(?P<dash>-[ \t]+)?expert:[ \t]*(?P<value>.*?)(?P<comment>[ \t]+#.*)?$")


def _yaml_scalar(text: str) -> str:
    plain = text and text == text.strip() and not any(c in text for c in ':#{}[],&*!|>%@`"\'') and not text[0] in "-?"
    return text if plain else json.dumps(text, ensure_ascii=False)


def _participant_for(expert: str) -> list[tuple[str, str]]:
    """The ``agent:`` / ``persona:`` keys for an old ``expert:`` name."""
    parts = expert.split("#")
    if len(parts) >= 3 and parts[1] in ("temp", "tmp"):
        keys = [("persona", parts[0])]
        if parts[2].isdigit() and parts[2] != "1":
            keys.append(("instance", parts[2]))
        return keys
    if len(parts) >= 3 and parts[1] == "oasis":
        if parts[2] == "new":
            return [("persona", parts[0]), ("tools", "all")]
        return [("agent", parts[2])]
    if len(parts) >= 3:  # tag#ext#name, tag#<platform>#name
        return [("agent", parts[2])]
    return [("agent", expert)]


def convert_workflow_yaml(text: str) -> str:
    """Rewrite every ``expert: <old name>`` line of a workflow; comments and layout stay."""
    out = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        match = _EXPERT_LINE.match(body)
        if not match or not match["value"]:
            out.append(line)
            continue
        ending = line[len(body):]
        value = match["value"].strip().strip("\"'")
        keys = _participant_for(value)
        first_key, first_value = keys[0]
        comment = match["comment"] or ""
        out.append(f"{match['indent']}{match['dash'] or ''}{first_key}: {_yaml_scalar(first_value)}{comment}{ending or chr(10)}")
        inner = match["indent"] + " " * len(match["dash"] or "")
        for key, val in keys[1:]:
            out.append(f"{inner}{key}: {val if key == 'instance' else _yaml_scalar(val)}{ending or chr(10)}")
    return "".join(out)


def migrate_workflows(teams: TeamStore) -> None:
    root = teams.user_files_dir
    if not root.is_dir():
        return
    for path in root.glob("*/**/oasis/yaml/*.y*ml"):
        text = path.read_text(encoding="utf-8")
        converted = convert_workflow_yaml(text)
        if converted != text:
            path.write_text(converted, encoding="utf-8")


def remove_old_files(data_dir: Path, teams: TeamStore) -> None:
    """What version 1 left behind: the old group chat database, backups, locks, and the
    history of WeBot and one-call agents that was recorded twice and never read."""
    for suffix in ("", "-wal", "-shm"):
        (Path(data_dir) / f"group_chat.db{suffix}").unlink(missing_ok=True)
    history_dir = Path(data_dir) / "external_agent_history"
    for pattern in ("internal#*", "temp#*"):
        for path in history_dir.glob(pattern):
            path.unlink(missing_ok=True)
    root = teams.user_files_dir
    if not root.is_dir():
        return
    for owner_dir in (p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        teams_dir = owner_dir / "teams"
        folders = [owner_dir] + ([p for p in teams_dir.iterdir() if p.is_dir()] if teams_dir.is_dir() else [])
        for folder in folders:
            shutil.rmtree(folder / ".migrated", ignore_errors=True)
            for name in (INTERNAL_FILE, EXTERNAL_FILE):
                (folder / f".{name}.lock").unlink(missing_ok=True)


def _drop_old_tables(store: AgentStore) -> None:
    conn = store._connect()
    try:
        conn.execute("DROP TABLE IF EXISTS agent_runtime_sessions")
        conn.execute("DROP TABLE IF EXISTS migrations")
    finally:
        conn.close()


def migrate(*, data_dir: Path, teams: TeamStore, conversations: ConversationStore) -> None:
    """Bring the data up to ``VERSION``; a no-op once it is there."""
    agents = teams.agents
    version = _version(agents)
    if version < 1:
        logger.info("migrating agents, teams and group chat into %s", agents.db_path)
        migrate_manifests(teams)
        migrate_groups(Path(data_dir) / "group_chat.db", conversations)
        migrate_alarms(Path(data_dir), teams)
        migrate_workflows(teams)
    if version < 2:
        logger.info("removing what the move into %s left behind", agents.db_path)
        remove_old_files(Path(data_dir), teams)
        _drop_old_tables(agents)
    if version < VERSION:
        _set_version(agents, VERSION)
