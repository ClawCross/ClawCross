"""
Persistent GraphRAG memory for OASIS.

This module turns the lightweight swarm blueprint into a living graph:

- SQLite keeps a durable local graph and long-term memory for every topic
- Zep can be enabled as an external GraphRAG mirror/search backend
- posts, callbacks, timeline events, and conclusions incrementally evolve the graph
- ReportAgent retrieves evidence from the graph instead of prompting over raw posts only
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite
from langchain_core.messages import HumanMessage, SystemMessage

from oasis.swarm_engine import build_pending_swarm


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
_DEFAULT_DB_PATH = os.path.join(_DATA_DIR, "oasis_graph_memory.db")

_ALLOWED_NODE_TYPES = {"objective", "agent", "entity", "memory", "signal", "scenario"}
_GENERIC_TOKENS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "over", "about", "after", "before",
    "than", "what", "when", "where", "which", "while", "would", "should", "could", "their", "there",
    "have", "has", "had", "been", "will", "your", "input", "graph", "graphs", "graphrag", "memory",
    "memories", "predict", "prediction", "predictions", "scenario", "signal", "agent", "agents",
    "topic", "town", "mode", "world", "swarm", "engine", "用户", "现在", "一个", "这个", "那个", "进行",
    "讨论", "图谱", "记忆", "预测", "场景", "节点", "信号", "问题", "因为", "所以", "需要", "可以", "以及",
    "如果", "然后", "正在", "当前", "结果", "内容", "作者", "帖子", "事件", "系统", "总结",
}
_REPORT_SYSTEM_PROMPT = """你是 OASIS 的 ReportAgent。

你只能基于给定的 GraphRAG 检索证据回答，不要伪造外部事实。
任务是解释“为什么当前 swarm 会这样预测”，而不是给空泛建议。

输出必须是 JSON 对象，字段如下：
{
  "answer": "2-4 句的核心结论",
  "because": ["3-5 条因果/证据要点"],
  "watchouts": ["0-3 条可能翻转结论的监测点"],
  "confidence": "low|medium|high"
}
"""


def _compact_text(value: Any, default: str = "", limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return default
    return text[:limit].strip()


def _slugify(value: Any, prefix: str = "item") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or prefix


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _json_loads(raw: str, default: Any) -> Any:
    try:
        return json.loads(raw) if raw else default
    except Exception:
        return default


def _normalize_node_type(raw_type: Any) -> str:
    value = str(raw_type or "").strip().lower()
    aliases = {
        "goal": "objective",
        "task": "objective",
        "actor": "agent",
        "persona": "agent",
        "fact": "entity",
        "concept": "entity",
        "retrieval": "memory",
        "graphrag": "memory",
        "indicator": "signal",
        "forecast": "scenario",
        "branch": "scenario",
    }
    value = aliases.get(value, value)
    return value if value in _ALLOWED_NODE_TYPES else "entity"


def _coerce_weight(value: Any, default: float = 0.55) -> float:
    try:
        weight = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, weight))


def _extract_terms(text: str, limit: int = 8) -> list[str]:
    candidates = re.findall(r"[\u4e00-\u9fff]{2,10}|[A-Za-z][A-Za-z0-9_\-]{2,}", text or "")
    result: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        token = item.strip(" .,:;!?()[]{}\"'").lower()
        if len(token) < 2 or token in _GENERIC_TOKENS or token in seen:
            continue
        seen.add(token)
        result.append(item.strip())
        if len(result) >= limit:
            break
    return result


def _merge_aliases(*alias_groups: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for group in alias_groups:
        for item in group or []:
            text = _compact_text(item, "", 80)
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
    return result


def _text_score(query: str, texts: list[str], timestamp: float = 0.0, activity: float = 0.0) -> float:
    query_norm = _compact_text(query, "", 240).lower()
    if not query_norm:
        return 0.0

    haystack = " ".join(_compact_text(t, "", 1000).lower() for t in texts if t).strip()
    if not haystack:
        return 0.0

    score = 0.0
    if query_norm in haystack:
        score += 14.0

    for token in _extract_terms(query, limit=10):
        token_norm = token.lower()
        if token_norm in haystack:
            score += 4.0 if len(token_norm) > 3 else 2.0

    if timestamp > 0:
        age_hours = max(0.0, (time.time() - timestamp) / 3600.0)
        score += max(0.0, 2.2 - min(age_hours, 24.0) * 0.08)

    score += min(max(activity, 0.0), 6.0) * 0.35
    return round(score, 4)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


class ZepGraphProvider:
    """Thin wrapper around the optional Zep Cloud SDK."""

    def __init__(self):
        self.api_key = os.getenv("ZEP_API_KEY", "").strip()
        self.base_url = os.getenv("ZEP_API_BASE_URL", "").strip()
        self.graph_prefix = _slugify(os.getenv("OASIS_ZEP_GRAPH_PREFIX", "clawcross-oasis"), "clawcross-oasis")
        self._client = None

    def available(self) -> bool:
        if not self.api_key:
            return False
        try:
            import zep_cloud  # noqa: F401
            return True
        except Exception:
            return False

    def _get_client(self):
        if self._client is not None:
            return self._client
        from zep_cloud.client import Zep

        kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        try:
            self._client = Zep(**kwargs)
        except TypeError:
            kwargs.pop("base_url", None)
            self._client = Zep(**kwargs)
        return self._client

    def graph_id_for_topic(self, topic_id: str, user_id: str) -> str:
        return f"{self.graph_prefix}-{_slugify(user_id or 'anonymous', 'anonymous')}-{topic_id}"

    def ensure_graph(self, *, topic_id: str, user_id: str, question: str) -> str:
        graph_id = self.graph_id_for_topic(topic_id, user_id)
        client = self._get_client()
        try:
            client.graph.create(
                graph_id=graph_id,
                name=f"OASIS {topic_id}",
                description=_compact_text(question, "OASIS discussion graph", 220),
            )
        except Exception as exc:
            msg = str(exc).lower()
            if "exist" not in msg and "already" not in msg and "conflict" not in msg:
                raise
        return graph_id

    def add_text(self, *, graph_id: str, text: str) -> None:
        if not text.strip():
            return
        client = self._get_client()
        client.graph.add(graph_id=graph_id, type="text", data=text)

    def search(self, *, graph_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        client = self._get_client()
        items: list[dict[str, Any]] = []

        for scope in ("edges", "nodes"):
            try:
                try:
                    raw = client.graph.search(
                        graph_id=graph_id,
                        query=query,
                        limit=limit,
                        scope=scope,
                        reranker="cross_encoder",
                    )
                except Exception:
                    raw = client.graph.search(graph_id=graph_id, query=query, limit=limit, scope=scope)
            except Exception:
                continue

            if hasattr(raw, "edges") and raw.edges:
                for edge in raw.edges:
                    items.append(
                        {
                            "provider": "zep",
                            "kind": "edge",
                            "id": getattr(edge, "uuid_", None) or getattr(edge, "uuid", ""),
                            "title": getattr(edge, "name", "") or "relationship",
                            "snippet": _compact_text(getattr(edge, "fact", ""), "", 280),
                            "score": 12.0,
                            "source_node_id": getattr(edge, "source_node_uuid", ""),
                            "target_node_id": getattr(edge, "target_node_uuid", ""),
                        }
                    )

            if hasattr(raw, "nodes") and raw.nodes:
                for node in raw.nodes:
                    items.append(
                        {
                            "provider": "zep",
                            "kind": "node",
                            "id": getattr(node, "uuid_", None) or getattr(node, "uuid", ""),
                            "title": getattr(node, "name", "") or "entity",
                            "snippet": _compact_text(getattr(node, "summary", ""), "", 280),
                            "score": 10.0,
                        }
                    )
        return items


class LocalGraphStore:
    def __init__(self, db_path: str = _DEFAULT_DB_PATH):
        self.db_path = db_path
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def initialize(self):
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS topic_graphs (
                        topic_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        question TEXT NOT NULL,
                        swarm_mode TEXT NOT NULL DEFAULT 'prediction',
                        provider TEXT NOT NULL DEFAULT 'local',
                        external_graph_id TEXT NOT NULL DEFAULT '',
                        blueprint_json TEXT NOT NULL DEFAULT '{}',
                        summary TEXT NOT NULL DEFAULT '',
                        objective TEXT NOT NULL DEFAULT '',
                        graph_status TEXT NOT NULL DEFAULT 'pending',
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        last_ingested_at REAL NOT NULL DEFAULT 0
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS graph_nodes (
                        topic_id TEXT NOT NULL,
                        node_id TEXT NOT NULL,
                        label TEXT NOT NULL,
                        node_type TEXT NOT NULL,
                        summary TEXT NOT NULL DEFAULT '',
                        aliases_json TEXT NOT NULL DEFAULT '[]',
                        meta_json TEXT NOT NULL DEFAULT '{}',
                        activity REAL NOT NULL DEFAULT 0,
                        source TEXT NOT NULL DEFAULT 'blueprint',
                        first_seen_at REAL NOT NULL,
                        last_seen_at REAL NOT NULL,
                        PRIMARY KEY (topic_id, node_id)
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS graph_edges (
                        topic_id TEXT NOT NULL,
                        edge_id TEXT NOT NULL,
                        source_node_id TEXT NOT NULL,
                        target_node_id TEXT NOT NULL,
                        label TEXT NOT NULL DEFAULT '',
                        weight REAL NOT NULL DEFAULT 0.55,
                        summary TEXT NOT NULL DEFAULT '',
                        meta_json TEXT NOT NULL DEFAULT '{}',
                        activity REAL NOT NULL DEFAULT 0,
                        source TEXT NOT NULL DEFAULT 'blueprint',
                        first_seen_at REAL NOT NULL,
                        last_seen_at REAL NOT NULL,
                        PRIMARY KEY (topic_id, edge_id)
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS graph_memories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        topic_id TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        author TEXT NOT NULL DEFAULT '',
                        content TEXT NOT NULL,
                        summary TEXT NOT NULL DEFAULT '',
                        round_num INTEGER NOT NULL DEFAULT 0,
                        timestamp REAL NOT NULL,
                        meta_json TEXT NOT NULL DEFAULT '{}',
                        UNIQUE (topic_id, source_type, source_id)
                    )
                    """
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_graph_nodes_topic_last_seen ON graph_nodes(topic_id, last_seen_at DESC)"
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_graph_edges_topic_last_seen ON graph_edges(topic_id, last_seen_at DESC)"
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_graph_memories_topic_time ON graph_memories(topic_id, timestamp DESC)"
                )
                await db.commit()
            self._initialized = True

    @asynccontextmanager
    async def _connection(self):
        await self.initialize()
        db = await aiosqlite.connect(self.db_path)
        db.row_factory = aiosqlite.Row
        try:
            yield db
        finally:
            await db.close()

    async def topic_exists(self, topic_id: str) -> bool:
        async with self._connection() as db:
            cur = await db.execute("SELECT topic_id FROM topic_graphs WHERE topic_id = ?", (topic_id,))
            return (await cur.fetchone()) is not None

    async def get_topic_meta(self, topic_id: str) -> dict[str, Any] | None:
        async with self._connection() as db:
            cur = await db.execute("SELECT * FROM topic_graphs WHERE topic_id = ?", (topic_id,))
            row = await cur.fetchone()
        return dict(row) if row else None

    async def upsert_topic(
        self,
        *,
        topic_id: str,
        user_id: str,
        question: str,
        swarm_mode: str,
        provider: str,
        external_graph_id: str,
        blueprint: dict[str, Any],
    ):
        now = time.time()
        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO topic_graphs (
                    topic_id, user_id, question, swarm_mode, provider, external_graph_id,
                    blueprint_json, summary, objective, graph_status, created_at, updated_at, last_ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    question = excluded.question,
                    swarm_mode = excluded.swarm_mode,
                    provider = excluded.provider,
                    external_graph_id = excluded.external_graph_id,
                    blueprint_json = excluded.blueprint_json,
                    summary = excluded.summary,
                    objective = excluded.objective,
                    graph_status = excluded.graph_status,
                    updated_at = excluded.updated_at
                """,
                (
                    topic_id,
                    user_id,
                    question,
                    swarm_mode or "prediction",
                    provider,
                    external_graph_id or "",
                    _json_dumps(blueprint),
                    _compact_text(blueprint.get("summary"), "", 280),
                    _compact_text(blueprint.get("objective") or question, "", 280),
                    _compact_text(blueprint.get("status"), "pending", 40),
                    now,
                    now,
                    now,
                ),
            )
            await db.commit()

    async def touch_topic_ingest(self, topic_id: str):
        now = time.time()
        async with self._connection() as db:
            await db.execute(
                "UPDATE topic_graphs SET updated_at = ?, last_ingested_at = ? WHERE topic_id = ?",
                (now, now, topic_id),
            )
            await db.commit()

    async def get_node(self, topic_id: str, node_id: str) -> dict[str, Any] | None:
        async with self._connection() as db:
            cur = await db.execute(
                "SELECT * FROM graph_nodes WHERE topic_id = ? AND node_id = ?",
                (topic_id, node_id),
            )
            row = await cur.fetchone()
        return dict(row) if row else None

    async def find_node_by_name(self, topic_id: str, name: str, *, node_type: str | None = None) -> dict[str, Any] | None:
        target = _compact_text(name, "", 120).lower()
        if not target:
            return None
        async with self._connection() as db:
            if node_type:
                cur = await db.execute(
                    "SELECT * FROM graph_nodes WHERE topic_id = ? AND node_type = ?",
                    (topic_id, node_type),
                )
            else:
                cur = await db.execute("SELECT * FROM graph_nodes WHERE topic_id = ?", (topic_id,))
            rows = await cur.fetchall()

        for row in rows:
            data = dict(row)
            label = str(data.get("label") or "").strip().lower()
            aliases = _json_loads(data.get("aliases_json", "[]"), [])
            meta = _json_loads(data.get("meta_json", "{}"), {})
            meta_keys = [
                str(meta.get("tag") or "").strip().lower(),
                str(meta.get("author") or "").strip().lower(),
                str(meta.get("name") or "").strip().lower(),
            ]
            if target == label or target in [str(a).strip().lower() for a in aliases] or target in meta_keys:
                return data
        return None

    async def upsert_node(
        self,
        *,
        topic_id: str,
        node_id: str,
        label: str,
        node_type: str,
        summary: str = "",
        aliases: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        activity_delta: float = 0.0,
        source: str = "memory",
    ):
        now = time.time()
        existing = await self.get_node(topic_id, node_id)
        existing_aliases = _json_loads((existing or {}).get("aliases_json", "[]"), [])
        existing_meta = _json_loads((existing or {}).get("meta_json", "{}"), {})
        merged_meta = {**existing_meta, **(meta or {})}
        merged_aliases = _merge_aliases(existing_aliases, aliases or [])
        next_activity = float((existing or {}).get("activity") or 0.0) + max(activity_delta, 0.0)

        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO graph_nodes (
                    topic_id, node_id, label, node_type, summary, aliases_json, meta_json,
                    activity, source, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_id, node_id) DO UPDATE SET
                    label = excluded.label,
                    node_type = excluded.node_type,
                    summary = CASE
                        WHEN excluded.summary <> '' THEN excluded.summary
                        ELSE graph_nodes.summary
                    END,
                    aliases_json = excluded.aliases_json,
                    meta_json = excluded.meta_json,
                    activity = excluded.activity,
                    source = excluded.source,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    topic_id,
                    node_id,
                    _compact_text(label, node_id, 120),
                    _normalize_node_type(node_type),
                    _compact_text(summary, "", 280),
                    _json_dumps(merged_aliases),
                    _json_dumps(merged_meta),
                    next_activity,
                    source,
                    float((existing or {}).get("first_seen_at") or now),
                    now,
                ),
            )
            await db.commit()

    async def upsert_edge(
        self,
        *,
        topic_id: str,
        source_node_id: str,
        target_node_id: str,
        label: str,
        weight: float = 0.55,
        summary: str = "",
        meta: dict[str, Any] | None = None,
        activity_delta: float = 0.0,
        source: str = "memory",
        edge_id: str | None = None,
    ):
        now = time.time()
        edge_id = edge_id or f"{source_node_id}->{target_node_id}:{_slugify(label or 'links', 'links')}"
        async with self._connection() as db:
            cur = await db.execute(
                "SELECT * FROM graph_edges WHERE topic_id = ? AND edge_id = ?",
                (topic_id, edge_id),
            )
            existing = await cur.fetchone()
            existing_data = dict(existing) if existing else {}
            merged_meta = {
                **_json_loads(existing_data.get("meta_json", "{}"), {}),
                **(meta or {}),
            }
            next_activity = float(existing_data.get("activity") or 0.0) + max(activity_delta, 0.0)
            await db.execute(
                """
                INSERT INTO graph_edges (
                    topic_id, edge_id, source_node_id, target_node_id, label, weight, summary,
                    meta_json, activity, source, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_id, edge_id) DO UPDATE SET
                    source_node_id = excluded.source_node_id,
                    target_node_id = excluded.target_node_id,
                    label = excluded.label,
                    weight = excluded.weight,
                    summary = CASE
                        WHEN excluded.summary <> '' THEN excluded.summary
                        ELSE graph_edges.summary
                    END,
                    meta_json = excluded.meta_json,
                    activity = excluded.activity,
                    source = excluded.source,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    topic_id,
                    edge_id,
                    source_node_id,
                    target_node_id,
                    _compact_text(label, "links", 120),
                    _coerce_weight(weight),
                    _compact_text(summary, "", 280),
                    _json_dumps(merged_meta),
                    next_activity,
                    source,
                    float(existing_data.get("first_seen_at") or now),
                    now,
                ),
            )
            await db.commit()

    async def insert_memory(
        self,
        *,
        topic_id: str,
        source_type: str,
        source_id: str,
        author: str = "",
        content: str,
        summary: str = "",
        round_num: int = 0,
        timestamp: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> bool:
        async with self._connection() as db:
            cur = await db.execute(
                """
                INSERT OR IGNORE INTO graph_memories (
                    topic_id, source_type, source_id, author, content, summary, round_num, timestamp, meta_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topic_id,
                    source_type,
                    source_id,
                    _compact_text(author, "", 120),
                    _compact_text(content, "", 6000),
                    _compact_text(summary, "", 280),
                    int(round_num or 0),
                    float(timestamp or time.time()),
                    _json_dumps(meta or {}),
                ),
            )
            await db.commit()
            inserted = cur.rowcount > 0
        if inserted:
            await self.touch_topic_ingest(topic_id)
        return inserted

    async def list_nodes(self, topic_id: str) -> list[dict[str, Any]]:
        async with self._connection() as db:
            cur = await db.execute(
                "SELECT * FROM graph_nodes WHERE topic_id = ? ORDER BY activity DESC, last_seen_at DESC",
                (topic_id,),
            )
            rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def list_edges(self, topic_id: str) -> list[dict[str, Any]]:
        async with self._connection() as db:
            cur = await db.execute(
                "SELECT * FROM graph_edges WHERE topic_id = ? ORDER BY activity DESC, last_seen_at DESC",
                (topic_id,),
            )
            rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def list_memories(self, topic_id: str, limit: int = 120) -> list[dict[str, Any]]:
        async with self._connection() as db:
            cur = await db.execute(
                """
                SELECT * FROM graph_memories
                WHERE topic_id = ?
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (topic_id, limit),
            )
            rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def count_memories(self, topic_id: str) -> int:
        async with self._connection() as db:
            cur = await db.execute("SELECT COUNT(*) FROM graph_memories WHERE topic_id = ?", (topic_id,))
            row = await cur.fetchone()
        return int(row[0] if row else 0)

    async def build_graph_snapshot(self, topic_id: str, *, memory_limit: int = 12) -> dict[str, Any]:
        nodes = await self.list_nodes(topic_id)
        edges = await self.list_edges(topic_id)

        retained_nodes: list[dict[str, Any]] = []
        memory_nodes = [n for n in nodes if n.get("node_type") == "memory"][:memory_limit]
        retained_nodes.extend([n for n in nodes if n.get("node_type") != "memory"])
        retained_nodes.extend(memory_nodes)

        seen: set[str] = set()
        deduped_nodes: list[dict[str, Any]] = []
        for node in retained_nodes:
            node_id = str(node.get("node_id") or "")
            if not node_id or node_id in seen:
                continue
            seen.add(node_id)
            meta = _json_loads(node.get("meta_json", "{}"), {})
            deduped_nodes.append(
                {
                    "id": node_id,
                    "label": node.get("label") or node_id,
                    "type": _normalize_node_type(node.get("node_type")),
                    "summary": node.get("summary") or "",
                    "aliases": _json_loads(node.get("aliases_json", "[]"), []),
                    "meta": meta if isinstance(meta, dict) else {},
                }
            )

        node_ids = {n["id"] for n in deduped_nodes}
        deduped_edges: list[dict[str, Any]] = []
        for edge in edges:
            src = str(edge.get("source_node_id") or "")
            dst = str(edge.get("target_node_id") or "")
            if src not in node_ids or dst not in node_ids:
                continue
            deduped_edges.append(
                {
                    "id": edge.get("edge_id") or f"{src}->{dst}",
                    "source": src,
                    "target": dst,
                    "label": edge.get("label") or "links",
                    "kind": edge.get("label") or "links",
                    "weight": _coerce_weight(edge.get("weight"), 0.55),
                    "summary": edge.get("summary") or "",
                }
            )

        return {
            "nodes": deduped_nodes,
            "edges": deduped_edges[:80],
        }

    async def search(self, topic_id: str, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        nodes = await self.list_nodes(topic_id)
        edges = await self.list_edges(topic_id)
        memories = await self.list_memories(topic_id, limit=180)

        items: list[dict[str, Any]] = []
        for node in nodes:
            aliases = _json_loads(node.get("aliases_json", "[]"), [])
            meta = _json_loads(node.get("meta_json", "{}"), {})
            score = _text_score(
                query,
                [node.get("label"), node.get("summary"), " ".join(aliases), _json_dumps(meta)],
                float(node.get("last_seen_at") or 0),
                float(node.get("activity") or 0),
            )
            if score <= 0:
                continue
            items.append(
                {
                    "provider": "local",
                    "kind": "node",
                    "id": node.get("node_id"),
                    "title": node.get("label") or node.get("node_id"),
                    "snippet": _compact_text(node.get("summary"), "", 240),
                    "score": score,
                    "node_id": node.get("node_id"),
                }
            )

        for edge in edges:
            meta = _json_loads(edge.get("meta_json", "{}"), {})
            score = _text_score(
                query,
                [edge.get("label"), edge.get("summary"), _json_dumps(meta)],
                float(edge.get("last_seen_at") or 0),
                float(edge.get("activity") or 0),
            )
            if score <= 0:
                continue
            items.append(
                {
                    "provider": "local",
                    "kind": "edge",
                    "id": edge.get("edge_id"),
                    "title": edge.get("label") or "relationship",
                    "snippet": _compact_text(edge.get("summary"), "", 240),
                    "score": score,
                    "source_node_id": edge.get("source_node_id"),
                    "target_node_id": edge.get("target_node_id"),
                }
            )

        for memory in memories:
            meta = _json_loads(memory.get("meta_json", "{}"), {})
            score = _text_score(
                query,
                [memory.get("content"), memory.get("summary"), memory.get("author"), _json_dumps(meta)],
                float(memory.get("timestamp") or 0),
                1.0,
            )
            if score <= 0:
                continue
            items.append(
                {
                    "provider": "local",
                    "kind": "memory",
                    "id": f"memory:{memory.get('id')}",
                    "title": memory.get("author") or memory.get("source_type") or "memory",
                    "snippet": _compact_text(memory.get("content"), "", 240),
                    "score": score,
                    "related_node_ids": list(meta.get("related_node_ids") or []),
                }
            )

        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in sorted(items, key=lambda x: x.get("score", 0), reverse=True):
            key = f"{item.get('provider')}:{item.get('kind')}:{item.get('id')}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= limit:
                break
        return deduped


class GraphMemoryService:
    def __init__(self):
        db_path = os.getenv("OASIS_GRAPHRAG_DB_PATH", _DEFAULT_DB_PATH).strip() or _DEFAULT_DB_PATH
        self.store = LocalGraphStore(db_path)
        self.zep = ZepGraphProvider()
        self._sync_tasks: set[asyncio.Task] = set()

    async def initialize(self):
        await self.store.initialize()

    def _desired_provider(self) -> str:
        return os.getenv("OASIS_GRAPHRAG_PROVIDER", "auto").strip().lower() or "auto"

    def _resolved_provider(self) -> str:
        desired = self._desired_provider()
        if desired == "local":
            return "local"
        if self.zep.available():
            return "zep"
        return "local"

    async def _ensure_external_graph(self, forum) -> str:
        if self._resolved_provider() != "zep":
            return ""
        return await asyncio.to_thread(
            self.zep.ensure_graph,
            topic_id=forum.topic_id,
            user_id=forum.user_id,
            question=forum.question,
        )

    def _enqueue_external_sync(self, *, graph_id: str, text: str):
        if not graph_id or self._resolved_provider() != "zep" or not text.strip():
            return

        async def _run():
            try:
                await asyncio.to_thread(self.zep.add_text, graph_id=graph_id, text=text)
            except Exception:
                return

        task = asyncio.create_task(_run())
        self._sync_tasks.add(task)
        task.add_done_callback(lambda t: self._sync_tasks.discard(t))

    async def ensure_topic_initialized(self, forum, *, seed_if_missing: bool = True) -> dict[str, Any]:
        await self.initialize()
        existing = await self.store.get_topic_meta(forum.topic_id)
        if existing:
            return existing

        swarm = forum.swarm
        if not swarm and seed_if_missing:
            swarm = build_pending_swarm(
                forum.question,
                user_id=forum.user_id,
                team=forum.team,
                schedule_yaml=forum.schedule_yaml,
                mode=forum.swarm_mode or "prediction",
            )
            forum.swarm = swarm

        if swarm:
            await self.sync_blueprint(forum, swarm)
            existing = await self.store.get_topic_meta(forum.topic_id)
            if existing:
                return existing

        await self.store.upsert_topic(
            topic_id=forum.topic_id,
            user_id=forum.user_id,
            question=forum.question,
            swarm_mode=forum.swarm_mode or "prediction",
            provider=self._resolved_provider(),
            external_graph_id="",
            blueprint=swarm or {
                "status": "pending",
                "summary": "",
                "objective": forum.question,
                "graph": {"nodes": [], "edges": []},
            },
        )
        return (await self.store.get_topic_meta(forum.topic_id)) or {}

    async def sync_blueprint(self, forum, swarm: dict[str, Any]) -> dict[str, Any]:
        await self.initialize()
        graph_id = await self._ensure_external_graph(forum)
        provider = "zep" if graph_id else "local"
        await self.store.upsert_topic(
            topic_id=forum.topic_id,
            user_id=forum.user_id,
            question=forum.question,
            swarm_mode=forum.swarm_mode or swarm.get("mode") or "prediction",
            provider=provider,
            external_graph_id=graph_id,
            blueprint=swarm,
        )

        graph = swarm.get("graph") if isinstance(swarm.get("graph"), dict) else {}
        for raw_node in graph.get("nodes") or []:
            node_id = _compact_text(raw_node.get("id"), "", 80)
            if not node_id:
                continue
            await self.store.upsert_node(
                topic_id=forum.topic_id,
                node_id=node_id,
                label=_compact_text(raw_node.get("label"), node_id, 120),
                node_type=_normalize_node_type(raw_node.get("type")),
                summary=_compact_text(raw_node.get("summary"), "", 280),
                aliases=list(raw_node.get("aliases") or []),
                meta=dict(raw_node.get("meta") or {}),
                activity_delta=0.3,
                source="blueprint",
            )

        for raw_edge in graph.get("edges") or []:
            source_id = _compact_text(raw_edge.get("source"), "", 80)
            target_id = _compact_text(raw_edge.get("target"), "", 80)
            if not source_id or not target_id:
                continue
            await self.store.upsert_edge(
                topic_id=forum.topic_id,
                edge_id=_compact_text(raw_edge.get("id"), "", 120) or None,
                source_node_id=source_id,
                target_node_id=target_id,
                label=_compact_text(raw_edge.get("label") or raw_edge.get("kind"), "links", 120),
                weight=_coerce_weight(raw_edge.get("weight"), 0.55),
                summary=_compact_text(raw_edge.get("summary"), "", 280),
                meta={},
                activity_delta=0.2,
                source="blueprint",
            )

        blueprint_episode = self._build_blueprint_episode(forum, swarm)
        await self.store.insert_memory(
            topic_id=forum.topic_id,
            source_type="blueprint",
            source_id=f"blueprint:{int(float(swarm.get('generated_at') or time.time()) * 1000)}",
            author="Town Genesis",
            content=blueprint_episode,
            summary=_compact_text(swarm.get("summary"), "", 220),
            round_num=0,
            timestamp=float(swarm.get("generated_at") or time.time()),
            meta={"related_node_ids": [n.get("id") for n in graph.get("nodes") or []][:10]},
        )
        self._enqueue_external_sync(graph_id=graph_id, text=blueprint_episode)
        return await self.build_swarm_payload(forum)

    def _build_blueprint_episode(self, forum, swarm: dict[str, Any]) -> str:
        signals = ", ".join((swarm.get("signals") or [])[:4])
        scenarios = "; ".join(
            f"{_compact_text(item.get('label'), '', 48)}: {_compact_text(item.get('summary'), '', 120)}"
            for item in (swarm.get("scenarios") or [])[:3]
            if isinstance(item, dict)
        )
        graph = swarm.get("graph") if isinstance(swarm.get("graph"), dict) else {}
        node_lines = [
            f"- {_compact_text(node.get('label') or node.get('id'), '', 80)} [{_normalize_node_type(node.get('type'))}]: {_compact_text(node.get('summary'), '', 120)}"
            for node in (graph.get("nodes") or [])[:10]
            if isinstance(node, dict)
        ]
        return "\n".join(
            [
                f"OASIS topic: {forum.question}",
                f"Swarm mode: {forum.swarm_mode or swarm.get('mode') or 'prediction'}",
                f"Summary: {_compact_text(swarm.get('summary'), '', 240)}",
                f"Prediction: {_compact_text(swarm.get('prediction'), '', 320)}",
                f"Signals: {signals or 'n/a'}",
                f"Scenarios: {scenarios or 'n/a'}",
                "Blueprint nodes:",
                *node_lines,
            ]
        )

    async def _resolve_author_node(self, topic_id: str, author: str) -> str:
        existing = await self.store.find_node_by_name(topic_id, author, node_type="agent")
        if existing:
            return str(existing.get("node_id"))
        node_id = f"agent:{_slugify(author, 'agent')}"
        await self.store.upsert_node(
            topic_id=topic_id,
            node_id=node_id,
            label=author,
            node_type="agent",
            summary=f"{author} is an active contributor in this OASIS topic.",
            aliases=[author],
            meta={"author": author},
            activity_delta=1.0,
            source="discussion",
        )
        return node_id

    async def _resolve_objective_node(self, forum) -> str:
        existing = await self.store.get_node(forum.topic_id, "objective-core")
        if existing:
            return "objective-core"
        await self.store.upsert_node(
            topic_id=forum.topic_id,
            node_id="objective-core",
            label="Prediction Brief",
            node_type="objective",
            summary=_compact_text(forum.question, "", 220),
            aliases=["objective", "brief"],
            meta={"mode": forum.swarm_mode or "prediction"},
            activity_delta=0.2,
            source="seed",
        )
        return "objective-core"

    async def _resolve_entity_nodes(self, topic_id: str, content: str, limit: int = 3) -> list[str]:
        result: list[str] = []
        for term in _extract_terms(content, limit=limit):
            existing = await self.store.find_node_by_name(topic_id, term)
            if existing:
                result.append(str(existing.get("node_id")))
                continue
            node_id = f"entity:{_slugify(term, 'entity')}"
            await self.store.upsert_node(
                topic_id=topic_id,
                node_id=node_id,
                label=term,
                node_type="entity",
                summary=f"{term} emerged as a discussion entity from new evidence.",
                aliases=[term],
                meta={"seed": "discussion"},
                activity_delta=0.8,
                source="discussion",
            )
            result.append(node_id)
        return result[:limit]

    async def ingest_post(self, forum, post):
        await self.ensure_topic_initialized(forum)
        author_node_id = await self._resolve_author_node(forum.topic_id, post.author)
        objective_node_id = await self._resolve_objective_node(forum)
        entity_ids = await self._resolve_entity_nodes(forum.topic_id, post.content, limit=3)
        memory_node_id = f"memory:post:{post.id}"

        await self.store.upsert_node(
            topic_id=forum.topic_id,
            node_id=memory_node_id,
            label=f"{post.author} · #{post.id}",
            node_type="memory",
            summary=_compact_text(post.content, "", 240),
            aliases=[],
            meta={
                "author": post.author,
                "round": post.round_num,
                "reply_to": post.reply_to,
                "source_type": "post",
            },
            activity_delta=1.2,
            source="post",
        )
        await self.store.upsert_edge(
            topic_id=forum.topic_id,
            source_node_id=author_node_id,
            target_node_id=memory_node_id,
            label="posted" if not post.reply_to else "replied",
            weight=0.82,
            summary=f"{post.author} added a discussion memory.",
            meta={"post_id": post.id},
            activity_delta=1.0,
            source="post",
        )
        await self.store.upsert_edge(
            topic_id=forum.topic_id,
            source_node_id=memory_node_id,
            target_node_id=objective_node_id,
            label="evidence",
            weight=0.68,
            summary="This post contributes evidence to the prediction brief.",
            meta={"post_id": post.id},
            activity_delta=0.8,
            source="post",
        )
        for entity_id in entity_ids:
            await self.store.upsert_edge(
                topic_id=forum.topic_id,
                source_node_id=memory_node_id,
                target_node_id=entity_id,
                label="mentions",
                weight=0.6,
                summary="The memory explicitly mentions this entity.",
                meta={"post_id": post.id},
                activity_delta=0.6,
                source="post",
            )
            await self.store.upsert_edge(
                topic_id=forum.topic_id,
                source_node_id=author_node_id,
                target_node_id=entity_id,
                label="tracks",
                weight=0.52,
                summary=f"{post.author} is tracking this entity in discussion.",
                meta={"post_id": post.id},
                activity_delta=0.5,
                source="post",
            )

        meta = {"related_node_ids": [author_node_id, memory_node_id, objective_node_id, *entity_ids]}
        inserted = await self.store.insert_memory(
            topic_id=forum.topic_id,
            source_type="post",
            source_id=str(post.id),
            author=post.author,
            content=post.content,
            summary=_compact_text(post.content, "", 220),
            round_num=post.round_num,
            timestamp=float(post.timestamp or time.time()),
            meta=meta,
        )
        if inserted:
            topic_meta = await self.store.get_topic_meta(forum.topic_id)
            graph_id = (topic_meta or {}).get("external_graph_id", "")
            self._enqueue_external_sync(
                graph_id=graph_id,
                text=(
                    f"OASIS topic '{forum.question}'. Round {post.round_num}. "
                    f"{post.author} said: {post.content}"
                ),
            )

    async def ingest_timeline_event(self, forum, event):
        await self.ensure_topic_initialized(forum)
        objective_node_id = await self._resolve_objective_node(forum)
        related_ids = [objective_node_id]
        summary = event.detail or event.event
        author = event.agent or "system"

        if event.agent:
            agent_node_id = await self._resolve_author_node(forum.topic_id, event.agent)
            related_ids.append(agent_node_id)
            await self.store.upsert_edge(
                topic_id=forum.topic_id,
                source_node_id=agent_node_id,
                target_node_id=objective_node_id,
                label=event.event,
                weight=0.45,
                summary=_compact_text(event.detail or f"{event.agent} triggered {event.event}", "", 220),
                meta={"elapsed": event.elapsed},
                activity_delta=0.4,
                source="timeline",
            )

        memory_inserted = await self.store.insert_memory(
            topic_id=forum.topic_id,
            source_type="timeline",
            source_id=str(getattr(event, "seq", 0) or f"{event.elapsed}:{event.event}:{event.agent}:{event.detail}"),
            author=author,
            content=f"{event.event} {event.agent or ''} {event.detail or ''}".strip(),
            summary=_compact_text(summary, "", 220),
            round_num=int(getattr(forum, "current_round", 0) or 0),
            timestamp=time.time(),
            meta={"related_node_ids": related_ids, "elapsed": event.elapsed, "event": event.event},
        )
        if memory_inserted and event.event in {"condition", "selector", "conclude", "graph_end", "manual_post", "agent_callback"}:
            topic_meta = await self.store.get_topic_meta(forum.topic_id)
            graph_id = (topic_meta or {}).get("external_graph_id", "")
            self._enqueue_external_sync(
                graph_id=graph_id,
                text=(
                    f"OASIS topic '{forum.question}'. Timeline event {event.event} by {author}. "
                    f"Detail: {event.detail or 'n/a'}"
                ),
            )

    async def ingest_conclusion(self, forum):
        if not forum.conclusion:
            return
        await self.ensure_topic_initialized(forum)
        objective_node_id = await self._resolve_objective_node(forum)
        final_node_id = "scenario:final-outlook"
        await self.store.upsert_node(
            topic_id=forum.topic_id,
            node_id=final_node_id,
            label="Final Outlook",
            node_type="scenario",
            summary=_compact_text(forum.conclusion, "", 260),
            aliases=["conclusion", "outlook"],
            meta={"status": forum.status, "full_text": forum.conclusion},
            activity_delta=1.6,
            source="conclusion",
        )
        await self.store.upsert_edge(
            topic_id=forum.topic_id,
            source_node_id=objective_node_id,
            target_node_id=final_node_id,
            label="resolved_as",
            weight=0.86,
            summary="The discussion concluded with this outlook.",
            meta={},
            activity_delta=1.1,
            source="conclusion",
        )
        inserted = await self.store.insert_memory(
            topic_id=forum.topic_id,
            source_type="conclusion",
            source_id="final",
            author="ReportAgent",
            content=forum.conclusion,
            summary=_compact_text(forum.conclusion, "", 220),
            round_num=int(getattr(forum, "current_round", 0) or 0),
            timestamp=time.time(),
            meta={"related_node_ids": [objective_node_id, final_node_id]},
        )
        if inserted:
            topic_meta = await self.store.get_topic_meta(forum.topic_id)
            graph_id = (topic_meta or {}).get("external_graph_id", "")
            self._enqueue_external_sync(
                graph_id=graph_id,
                text=f"OASIS topic '{forum.question}' concluded with: {forum.conclusion}",
            )

    async def build_swarm_payload(self, forum) -> dict[str, Any]:
        await self.ensure_topic_initialized(forum)
        meta = await self.store.get_topic_meta(forum.topic_id)
        base = _json_loads((meta or {}).get("blueprint_json", "{}"), {}) if meta else {}
        if not isinstance(base, dict) or not base:
            base = forum.swarm or build_pending_swarm(
                forum.question,
                user_id=forum.user_id,
                team=forum.team,
                schedule_yaml=forum.schedule_yaml,
                mode=forum.swarm_mode or "prediction",
            )

        snapshot = await self.store.build_graph_snapshot(forum.topic_id)
        if forum.conclusion:
            for node in snapshot.get("nodes") or []:
                if str(node.get("id") or "") != "scenario:final-outlook":
                    continue
                node_meta = dict(node.get("meta") or {})
                node_meta.setdefault("status", forum.status)
                node_meta["full_text"] = forum.conclusion
                node["meta"] = node_meta
                node["full_text"] = forum.conclusion
                break
        memory_count = await self.store.count_memories(forum.topic_id)
        graphrag = dict(base.get("graphrag") or {})
        graphrag.update(
            {
                "provider": (meta or {}).get("provider") or self._resolved_provider(),
                "external_graph_id": (meta or {}).get("external_graph_id") or "",
                "memory_count": memory_count,
                "last_ingested_at": (meta or {}).get("last_ingested_at") or 0,
                "collections": list(dict.fromkeys((graphrag.get("collections") or []) + ["local-memory"])),
                "report_agent": True,
            }
        )

        payload = dict(base)
        payload["mode"] = forum.swarm_mode or payload.get("mode") or "prediction"
        payload["objective"] = payload.get("objective") or forum.question
        payload["graph"] = snapshot
        payload["graphrag"] = graphrag
        payload["status"] = payload.get("status") or "ready"
        payload["generated_at"] = payload.get("generated_at") or (meta or {}).get("updated_at") or time.time()
        if not payload.get("summary"):
            payload["summary"] = "Living GraphRAG memory for the topic is active."
        if not payload.get("prediction") and forum.conclusion:
            payload["prediction"] = forum.conclusion
        return payload

    async def _build_subgraph(self, topic_id: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        full = await self.store.build_graph_snapshot(topic_id, memory_limit=8)
        if not evidence:
            return full

        wanted: set[str] = set()
        for item in evidence:
            if item.get("kind") == "node" and item.get("node_id"):
                wanted.add(str(item["node_id"]))
            if item.get("kind") == "edge":
                if item.get("source_node_id"):
                    wanted.add(str(item["source_node_id"]))
                if item.get("target_node_id"):
                    wanted.add(str(item["target_node_id"]))
            for node_id in item.get("related_node_ids") or []:
                wanted.add(str(node_id))

        if not wanted:
            return full

        nodes = [node for node in full.get("nodes", []) if node.get("id") in wanted]
        edges = [
            edge
            for edge in full.get("edges", [])
            if edge.get("source") in wanted and edge.get("target") in wanted
        ]
        if not nodes:
            return full
        return {"nodes": nodes[:18], "edges": edges[:26]}

    async def retrieve(self, forum, query: str, *, limit: int = 8) -> dict[str, Any]:
        await self.ensure_topic_initialized(forum)
        local_items = await self.store.search(forum.topic_id, query, limit=max(limit, 8))
        merged = list(local_items)

        meta = await self.store.get_topic_meta(forum.topic_id)
        graph_id = (meta or {}).get("external_graph_id", "")
        if graph_id and self._resolved_provider() == "zep":
            try:
                zep_items = await asyncio.to_thread(self.zep.search, graph_id=graph_id, query=query, limit=limit)
                merged.extend(zep_items)
            except Exception:
                pass

        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in sorted(merged, key=lambda x: x.get("score", 0), reverse=True):
            key = f"{item.get('provider')}:{item.get('kind')}:{item.get('title')}:{item.get('snippet')}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= limit:
                break

        return {
            "provider": (meta or {}).get("provider") or self._resolved_provider(),
            "evidence": deduped,
            "graph": await self._build_subgraph(forum.topic_id, deduped),
        }

    async def ask_report(self, forum, question: str, *, limit: int = 8) -> dict[str, Any]:
        retrieval = await self.retrieve(forum, question, limit=limit)
        evidence = retrieval["evidence"]
        evidence_lines = []
        for idx, item in enumerate(evidence, start=1):
            evidence_lines.append(
                f"{idx}. [{item.get('provider')}/{item.get('kind')}] "
                f"{_compact_text(item.get('title'), '', 80)} :: {_compact_text(item.get('snippet'), '', 240)}"
            )

        answer: dict[str, Any] | None = None
        try:
            from oasis.swarm_engine import create_chat_model, extract_text

            model = create_chat_model()
            prompt = "\n".join(
                [
                    f"Topic: {forum.question}",
                    f"Current swarm summary: {_compact_text((forum.swarm or {}).get('summary'), '', 240)}",
                    f"Question: {question}",
                    f"Conclusion so far: {_compact_text(forum.conclusion, '', 280)}",
                    "Evidence:",
                    *(evidence_lines or ["(no evidence found)"]),
                ]
            )
            raw = model.invoke(
                [
                    SystemMessage(content=_REPORT_SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ]
            )
            answer = _parse_json_object(extract_text(raw))
        except Exception:
            answer = None

        if not isinstance(answer, dict):
            answer = self._fallback_report(question, evidence, forum)

        await self.store.insert_memory(
            topic_id=forum.topic_id,
            source_type="report_query",
            source_id=f"{_slugify(question, 'query')}-{int(time.time() * 1000)}",
            author="ReportAgent",
            content=question,
            summary=_compact_text(answer.get("answer"), "", 220),
            round_num=int(getattr(forum, "current_round", 0) or 0),
            timestamp=time.time(),
            meta={"related_node_ids": [item.get("node_id") for item in evidence if item.get("node_id")]},
        )

        return {
            "topic_id": forum.topic_id,
            "provider": retrieval["provider"],
            "answer": _compact_text(answer.get("answer"), "", 1200),
            "because": [str(item) for item in answer.get("because") or []][:5],
            "watchouts": [str(item) for item in answer.get("watchouts") or []][:3],
            "confidence": _compact_text(answer.get("confidence"), "medium", 12).lower() or "medium",
            "evidence": evidence,
            "graph": retrieval["graph"],
        }

    def _fallback_report(self, question: str, evidence: list[dict[str, Any]], forum) -> dict[str, Any]:
        top = evidence[:4]
        because = []
        for item in top:
            prefix = f"{item.get('title')}: " if item.get("title") else ""
            because.append(_compact_text(prefix + (item.get("snippet") or ""), "", 200))
        if not because:
            because.append("当前图谱里的可检索证据还很少，结论更多来自初始 blueprint，而不是后续讨论演化。")

        answer = (
            f"当前预测主要由 {len(top) or 1} 组图谱证据支撑。"
            f" 对“{_compact_text(question, '', 80)}”的解释是："
            f"{_compact_text(because[0], '', 220)}"
        )
        watchouts = [
            "如果后续出现与当前高分证据相反的新帖子或 callback，图谱关系权重会被改写。",
            "若关键 agent 长时间没有新增观察，当前预测会更偏向早期 scaffold。",
        ]
        if forum.conclusion:
            watchouts = watchouts[:1]
        return {
            "answer": answer,
            "because": because[:4],
            "watchouts": watchouts[:2],
            "confidence": "medium" if evidence else "low",
        }


_GRAPH_MEMORY_SERVICE: GraphMemoryService | None = None


async def get_graph_memory_service() -> GraphMemoryService:
    global _GRAPH_MEMORY_SERVICE
    if _GRAPH_MEMORY_SERVICE is None:
        _GRAPH_MEMORY_SERVICE = GraphMemoryService()
        await _GRAPH_MEMORY_SERVICE.initialize()
    return _GRAPH_MEMORY_SERVICE
