"""The agent registry: one record per agent, whatever runtime it lives in.

Each agent gets an ``ag_…`` id that never changes, a handle that is unique per
owner (canonical address ``owner/handle``), and a binding that says how to reach
it. The binding may change — a WeBot session reset, an external agent renamed —
without the id changing.

Team folders are still where agents are declared (``internal_agents.json``,
``external_agents.json``). ``sync_owner`` imports them keyed by binding, so an
agent keeps its id across edits and across the teams it appears in. The rules
that pick an agent's home team and config mirror the readers this replaces:

* WeBot agents: the first team in sorted order that lists the session, else the
  user root (``TeamAgent._find_internal_session_meta``).
* External agents: the last file read wins, user root first, then teams in
  sorted order (``api.external_agent_registry``).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from integrations.acpx_cli_tools import acpx_agent_tags_with_legacy

AGENT_ID_PREFIX = "ag_"
_ID_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # base32 without look-alikes
_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")

DRIVER_WEBOT = "webot"
DRIVER_ACPX = "acpx"
DRIVER_OPENCLAW = "openclaw"
DRIVER_HTTP = "http"
# A persona-only participant that lives for one task and is never stored:
# a single LLM call (no tools), or a temporary WeBot session (with tools).
DRIVER_EPHEMERAL = "ephemeral"

# Session ids of temporary WeBot sessions start with this; only these may be
# discarded wholesale by their creator.
EPHEMERAL_SESSION_PREFIX = "tmp__"


class AgentNotFound(LookupError):
    pass


class AmbiguousAgentRef(LookupError):
    def __init__(self, ref: str, candidates: list[str]):
        super().__init__(f"agent reference {ref!r} is ambiguous: {', '.join(candidates)}")
        self.candidates = candidates


def canonical_platform(platform: str) -> str:
    """Canonical external platform name (claude-code → claude, gemini-cli → gemini)."""
    pl = (platform or "").strip().lower()
    if pl in ("claude-code", "claudecode"):
        return "claude"
    if pl in ("gemini-cli", "geminicli"):
        return "gemini"
    return pl


def external_driver(platform: str) -> str:
    """Which driver reaches an external agent on *platform*."""
    pl = canonical_platform(platform)
    if pl == "openclaw":
        return DRIVER_OPENCLAW
    if pl and pl in {canonical_platform(t) for t in acpx_agent_tags_with_legacy()}:
        return DRIVER_ACPX
    return DRIVER_HTTP


def new_agent_id() -> str:
    return AGENT_ID_PREFIX + "".join(secrets.choice(_ID_ALPHABET) for _ in range(10))


def _base36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = digits[r] + out
        if n == 0:
            return out


def new_webot_session_id() -> str:
    """A fresh WeBot session id, in the format the web UI and imports have always used:
    base36 milliseconds + 4 random base36 digits."""
    return _base36(int(time.time() * 1000)) + _base36(secrets.randbelow(36 ** 4)).zfill(4)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", (text or "").strip().lower()).strip("-_")
    return slug[:32].rstrip("-_")


@contextlib.contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Serialize manifest write-backs between the ClawCross processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        try:
            import fcntl
        except ImportError:  # Windows
            try:
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            except (ImportError, OSError):
                pass
            yield
            return
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_json_entries(path: Path) -> list[Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _write_entries(path: Path, entries: list[Any]) -> None:
    """Replace the manifest atomically, formatted exactly like ``_ia_save``."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def stamp_missing_sessions(path: str | os.PathLike) -> bool:
    """Give every named entry of an internal_agents.json a WeBot session.

    An entry written without ``session`` (by hand, or team-builder via
    ``write_file``) declares a new agent; its session is stamped and written
    back in the ``_ia_save`` format (session appended last). Returns True when
    the file changed.
    """
    path = Path(path)
    if not path.is_file():
        return False

    def missing(entries: list) -> bool:
        return any(
            isinstance(e, dict) and "name" in e and not str(e.get("session") or "").strip()
            for e in entries
        )

    if not missing(read_json_entries(path)):
        return False
    with _file_lock(path.with_name(f".{path.name}.lock")):
        entries = read_json_entries(path)  # re-read: another process may have stamped it
        if not missing(entries):
            return False
        for entry in entries:
            if isinstance(entry, dict) and "name" in entry and not str(entry.get("session") or "").strip():
                entry.pop("session", None)
                entry["session"] = new_webot_session_id()
        _write_entries(path, entries)
    return True


@dataclass(slots=True)
class AgentRecord:
    agent_id: str
    owner: str
    handle: str
    display_name: str
    driver: str
    persona_tag: str = ""
    binding_key: str = ""
    binding: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    default_context: dict[str, Any] = field(default_factory=dict)
    status: str = "active"

    @property
    def address(self) -> str:
        return f"{self.owner}/{self.handle}"

    @property
    def teams(self) -> list[str]:
        return list(self.settings.get("teams") or [])

    def team_name(self, team: str) -> str:
        """The name this agent goes by inside *team* (its entry's ``name``)."""
        return str((self.settings.get("team_names") or {}).get(team) or "")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    handle TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    driver TEXT NOT NULL,
    persona_tag TEXT NOT NULL DEFAULT '',
    settings_json TEXT NOT NULL DEFAULT '{}',
    default_context_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (owner, handle)
);
CREATE TABLE IF NOT EXISTS agent_bindings (
    binding_key TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_bindings_agent ON agent_bindings(agent_id);
CREATE TABLE IF NOT EXISTS agent_aliases (
    owner TEXT NOT NULL,
    ref TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    PRIMARY KEY (owner, ref)
);
"""


def webot_binding_key(owner: str, session: str) -> str:
    return f"{owner}|webot|{session}"


def external_binding_key(owner: str, global_name: str) -> str:
    return f"{owner}|external|{global_name}"


def _read_json_list(path: str) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


class AgentRegistry:
    """Registry backed by SQLite; safe to share between the ClawCross processes."""

    def __init__(self, db_path: str | os.PathLike, user_files_dir: str | os.PathLike):
        self.db_path = str(db_path)
        self.user_files_dir = str(user_files_dir)
        self._fingerprints: dict[str, tuple] = {}
        self._lock = threading.Lock()
        self._schema_ready = False

    # ── storage ──────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        if not self._schema_ready:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            self._schema_ready = True
        return conn

    @contextmanager
    def _session(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> AgentRecord:
        return AgentRecord(
            agent_id=row["agent_id"],
            owner=row["owner"],
            handle=row["handle"],
            display_name=row["display_name"],
            driver=row["driver"],
            persona_tag=row["persona_tag"],
            binding_key=row["binding_key"] or "",
            binding=json.loads(row["config_json"] or "{}"),
            settings=json.loads(row["settings_json"] or "{}"),
            default_context=json.loads(row["default_context_json"] or "{}"),
            status=row["status"],
        )

    _SELECT = """
        SELECT a.*, b.binding_key, b.config_json
        FROM agents a LEFT JOIN agent_bindings b ON b.agent_id = a.agent_id
    """

    def _query(self, where: str, params: tuple) -> list[AgentRecord]:
        with self._session() as conn:
            rows = conn.execute(f"{self._SELECT} WHERE {where}", params).fetchall()
        return [self._row_to_record(row) for row in rows]

    # ── import from team folders ─────────────────────────────────────────

    def _owner_files(self, owner: str) -> list[tuple[str, str, str]]:
        """``(kind, team, path)`` for every agent file of *owner*; team "" is the user root."""
        root = os.path.join(self.user_files_dir, owner)
        teams_root = os.path.join(root, "teams")
        teams = sorted(
            name for name in (os.listdir(teams_root) if os.path.isdir(teams_root) else [])
            if os.path.isdir(os.path.join(teams_root, name))
        )
        files: list[tuple[str, str, str]] = []
        for kind in ("internal_agents.json", "external_agents.json"):
            candidates = [(team, os.path.join(teams_root, team, kind)) for team in teams]
            candidates.append(("", os.path.join(root, kind)))
            for team, path in candidates:
                if os.path.isfile(path):
                    files.append((kind, team, path))
        return files

    @staticmethod
    def _fingerprint(files: list[tuple[str, str, str]]) -> tuple:
        result = []
        for _kind, _team, path in files:
            try:
                st = os.stat(path)
            except OSError:
                continue
            result.append((path, st.st_mtime_ns, st.st_size))
        return tuple(result)

    def _desired_agents(self, owner: str, files: list[tuple[str, str, str]]) -> dict[str, dict]:
        """Binding key → what the team folders currently say about that agent."""
        desired: dict[str, dict] = {}

        # WeBot: teams in sorted order, then the user root; the first entry is home.
        for kind, team, path in files:
            if kind != "internal_agents.json":
                continue
            for item in _read_json_list(path):
                session = str(item.get("session") or item.get("session_id") or "").strip()
                if not session:
                    continue
                key = webot_binding_key(owner, session)
                name = str(item.get("name") or "").strip()
                entry = desired.get(key)
                if entry is None:
                    entry = desired[key] = {
                        "driver": DRIVER_WEBOT,
                        "display_name": name,
                        "persona_tag": str(item.get("tag") or "").strip(),
                        "home_team": team,
                        "binding": {"session": session},
                        "teams": [],
                        "team_names": {},
                        "refs": {session},
                        "tools": item.get("tools"),
                    }
                entry["teams"].append(team)
                entry["team_names"].setdefault(team, name)
                if name:
                    entry["refs"].add(f"internal:{name}")

        # External: user root first, then teams in sorted order; the last entry wins.
        external = [f for f in files if f[0] == "external_agents.json"]
        external.sort(key=lambda f: (f[1] != "", f[1]))
        for _kind, team, path in external:
            for item in _read_json_list(path):
                global_name = str(item.get("global_name") or "").strip()
                if not global_name or "name" not in item:
                    continue
                key = external_binding_key(owner, global_name)
                config = item.get("config") or item.get("meta") or {}
                config = config if isinstance(config, dict) else {}
                name = str(item.get("name") or "").strip()
                platform = canonical_platform(str(item.get("platform") or item.get("tag") or ""))
                previous = desired.get(key)
                entry = desired[key] = {
                    "driver": external_driver(platform),
                    "display_name": name,
                    "persona_tag": str(item.get("tag") or "").strip(),
                    "home_team": team,
                    "binding": {
                        "global_name": global_name,
                        "platform": platform,
                        "api_url": config.get("api_url", ""),
                        "api_key": config.get("api_key", ""),
                        "model": config.get("model", ""),
                        "meta": config,
                        "name": name,
                        "tag": str(item.get("tag") or ""),
                        "team": team,
                    },
                    "teams": (previous or {}).get("teams", []) + [team],
                    "team_names": {**(previous or {}).get("team_names", {}), team: name},
                    "refs": (previous or {}).get("refs", set()) | {global_name, f"external:{name}"},
                    "tools": None,
                }
        return desired

    def sync_owner(self, owner: str) -> None:
        """Bring *owner*'s records in line with the team folders (cheap when unchanged)."""
        owner = (owner or "").strip()
        if not owner:
            return
        files = self._owner_files(owner)
        fingerprint = self._fingerprint(files)
        with self._lock:
            if self._fingerprints.get(owner) == fingerprint:
                return
        # Roles written without a session declare new agents: give them one.
        stamped = [stamp_missing_sessions(path) for kind, _team, path in files if kind == "internal_agents.json"]
        if any(stamped):
            files = self._owner_files(owner)
            fingerprint = self._fingerprint(files)
        desired = self._desired_agents(owner, files)
        now = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            seen_ids: set[str] = set()
            for key, entry in desired.items():
                seen_ids.add(self._upsert(conn, owner, key, entry, now))
            # Agents no longer declared anywhere keep their id and history.
            rows = conn.execute(
                "SELECT agent_id FROM agents WHERE owner = ? AND status = 'active'", (owner,)
            ).fetchall()
            for row in rows:
                if row["agent_id"] not in seen_ids:
                    conn.execute(
                        "UPDATE agents SET status = 'detached', updated_at = ? WHERE agent_id = ?",
                        (now, row["agent_id"]),
                    )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        with self._lock:
            self._fingerprints[owner] = fingerprint

    def _unique_handle(self, conn: sqlite3.Connection, owner: str, entry: dict, agent_id: str) -> str:
        binding = entry["binding"]
        candidates = [
            entry["display_name"],
            entry["persona_tag"],
            binding.get("global_name", ""),
            binding.get("session", ""),
        ]
        base = next((s for s in (_slug(c) for c in candidates) if _HANDLE_RE.match(s)), "agent")
        handle, n = base, 1
        while True:
            row = conn.execute(
                "SELECT agent_id FROM agents WHERE owner = ? AND handle = ?", (owner, handle)
            ).fetchone()
            if row is None or row["agent_id"] == agent_id:
                return handle
            n += 1
            suffix = f"-{n}"
            handle = base[: 32 - len(suffix)] + suffix

    def _upsert(self, conn: sqlite3.Connection, owner: str, key: str, entry: dict, now: float) -> str:
        settings = {
            "teams": entry["teams"],
            "team_names": entry["team_names"],
        }
        if entry.get("tools") is not None:
            settings["tools"] = entry["tools"]
        default_context = {"team": entry["home_team"]} if entry["home_team"] else {}
        row = conn.execute("SELECT agent_id FROM agent_bindings WHERE binding_key = ?", (key,)).fetchone()
        if row is None:
            agent_id = new_agent_id()
            handle = self._unique_handle(conn, owner, entry, agent_id)
            conn.execute(
                "INSERT INTO agents (agent_id, owner, handle, display_name, driver, persona_tag,"
                " settings_json, default_context_json, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (agent_id, owner, handle, entry["display_name"], entry["driver"], entry["persona_tag"],
                 json.dumps(settings, ensure_ascii=False), json.dumps(default_context, ensure_ascii=False),
                 now, now),
            )
            conn.execute(
                "INSERT INTO agent_bindings (binding_key, agent_id, config_json, updated_at) VALUES (?, ?, ?, ?)",
                (key, agent_id, json.dumps(entry["binding"], ensure_ascii=False), now),
            )
        else:
            agent_id = row["agent_id"]
            conn.execute(
                "UPDATE agents SET display_name = ?, driver = ?, persona_tag = ?, settings_json = ?,"
                " default_context_json = ?, status = 'active', updated_at = ? WHERE agent_id = ?",
                (entry["display_name"], entry["driver"], entry["persona_tag"],
                 json.dumps(settings, ensure_ascii=False), json.dumps(default_context, ensure_ascii=False),
                 now, agent_id),
            )
            conn.execute(
                "UPDATE agent_bindings SET config_json = ?, updated_at = ? WHERE binding_key = ?",
                (json.dumps(entry["binding"], ensure_ascii=False), now, key),
            )
        for ref in entry["refs"]:
            conn.execute(
                "INSERT OR REPLACE INTO agent_aliases (owner, ref, agent_id) VALUES (?, ?, ?)",
                (owner, ref, agent_id),
            )
        return agent_id

    # ── lookup ───────────────────────────────────────────────────────────

    def get(self, agent_id: str) -> AgentRecord | None:
        records = self._query("a.agent_id = ?", (agent_id,))
        return records[0] if records else None

    def list(self, owner: str, *, include_inactive: bool = False) -> list[AgentRecord]:
        self.sync_owner(owner)
        where = "a.owner = ?" + ("" if include_inactive else " AND a.status = 'active'")
        return sorted(self._query(where, (owner,)), key=lambda r: r.handle)

    def by_binding(self, key: str) -> AgentRecord | None:
        records = self._query("b.binding_key = ?", (key,))
        return records[0] if records else None

    def webot_session(self, owner: str, session: str) -> AgentRecord | None:
        self.sync_owner(owner)
        record = self.by_binding(webot_binding_key(owner, session))
        return record if record and record.status == "active" else None

    def external(self, owner: str, global_name: str) -> AgentRecord | None:
        self.sync_owner(owner)
        record = self.by_binding(external_binding_key(owner, global_name))
        return record if record and record.status == "active" else None

    def internal_session_meta(self, owner: str, session: str) -> dict | None:
        """``{"team", "name", "tag"}`` for a declared WeBot session, as the runtime expects."""
        record = self.webot_session(owner, session)
        if record is None:
            return None
        return {
            "team": str(record.default_context.get("team") or ""),
            "name": record.display_name,
            "tag": record.persona_tag,
        }

    def resolve(self, owner: str, ref: str, *, team: str | None = None) -> AgentRecord:
        """Find *owner*'s agent by id, address, handle, legacy reference or team name.

        ``ref`` may be ``ag_…``, ``alice/coder``, ``alice/dev-team/<name>``,
        ``dev-team/<name>``, ``coder`` / ``@coder``, a WeBot session or external
        global_name, ``internal:<name>`` / ``external:<name>``, or an agent's name
        (inside *team* when given). Raises AgentNotFound or AmbiguousAgentRef.
        """
        self.sync_owner(owner)
        text = (ref or "").strip().lstrip("@")
        if not text:
            raise AgentNotFound("empty agent reference")

        if text.startswith(AGENT_ID_PREFIX):
            record = self.get(text)
            if record and record.owner == owner:
                return record
            raise AgentNotFound(f"no agent {ref!r} for {owner}")

        if "/" in text:
            parts = [p for p in text.split("/") if p]
            if parts and parts[0] == owner:
                parts = parts[1:]
            elif len(parts) == 3:
                raise AgentNotFound(f"{ref!r} belongs to another user")
            if len(parts) == 1:
                return self.resolve(owner, parts[0], team=team)
            if len(parts) == 2:
                return self._by_name(owner, parts[1], team=parts[0], ref=ref)
            raise AgentNotFound(f"cannot parse agent address {ref!r}")

        if team is not None:
            # Inside a team its own names come first: "Reviewer" in ops is ops'
            # reviewer even when another team's reviewer owns the handle.
            try:
                return self._by_name(owner, text, team=team, ref=ref)
            except AgentNotFound:
                pass

        records = self._query("a.owner = ? AND a.handle = ?", (owner, text.lower()))
        if records:
            return records[0]
        with self._session() as conn:
            row = conn.execute(
                "SELECT agent_id FROM agent_aliases WHERE owner = ? AND ref = ?", (owner, text)
            ).fetchone()
        if row:
            record = self.get(row["agent_id"])
            if record:
                return record
        return self._by_name(owner, text, team=team, ref=ref)

    def _by_name(self, owner: str, name: str, *, team: str | None, ref: str) -> AgentRecord:
        wanted = name.strip().lower()
        matches = []
        for record in self.list(owner):
            if team is not None:
                if team not in record.teams:
                    continue
                names = {record.team_name(team)}
            else:
                names = {record.display_name, *record.settings.get("team_names", {}).values()}
            if wanted in {n.strip().lower() for n in names if n}:
                matches.append(record)
        if not matches:
            raise AgentNotFound(f"no agent {ref!r} for {owner}")
        if len(matches) > 1:
            raise AmbiguousAgentRef(ref, [m.address for m in matches])
        return matches[0]


_DEFAULT: dict[tuple[str, str], AgentRegistry] = {}
_DEFAULT_LOCK = threading.Lock()


def get_registry(
    user_files_dir: str | os.PathLike | None = None,
    db_path: str | os.PathLike | None = None,
) -> AgentRegistry:
    """The shared registry for a user-files tree; its DB sits next to it by default."""
    if user_files_dir is None:
        from utils.runtime_paths import USER_FILES_DIR
        user_files_dir = USER_FILES_DIR
    user_files_dir = str(user_files_dir)
    if db_path is None:
        db_path = str(Path(user_files_dir).parent / "group_chat.db")
    key = (user_files_dir, str(db_path))
    with _DEFAULT_LOCK:
        registry = _DEFAULT.get(key)
        if registry is None:
            registry = _DEFAULT[key] = AgentRegistry(db_path, user_files_dir)
        return registry
