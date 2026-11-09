"""
Session Search — cross-session recall via keyword search.

Ported from Hermes Agent's session_search concept:
- Search historical sessions for recalled context
- Prevents user from repeating themselves across sessions
- Keyword-based search with relevance ranking
- Optional LLM summarization of matches
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from utils.checkpoint_paths import iter_checkpoint_db_paths
from utils.runtime_paths import DATA_DIR


def session_search(
    *,
    query: str = "",
    user_id: str,
    current_session_id: str = "",
    limit: int = 5,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Search across historical sessions for relevant context.

    Args:
        query: Search keywords. If empty, returns recent sessions.
        user_id: User to search for
        current_session_id: Current session to exclude from results
        limit: Max results to return
        db_path: Path to checkpoint DB

    Returns:
        Dict with matches: list of session summaries
    """
    if not db_path:
        db_path = str(DATA_DIR / "agent_checkpoints")

    # Also search trajectory data for richer context
    trajectory_matches = _search_trajectories(
        query=query,
        user_id=user_id,
        current_session_id=current_session_id,
        limit=limit,
    )

    # Search checkpoint DB for session metadata
    checkpoint_matches = _search_checkpoints(
        query=query,
        user_id=user_id,
        current_session_id=current_session_id,
        limit=limit,
        db_path=str(db_path),
    )

    # Merge and deduplicate
    seen_sessions: set[str] = set()
    merged: list[dict[str, Any]] = []

    for match in trajectory_matches + checkpoint_matches:
        sid = match.get("session_id", "")
        if sid and sid not in seen_sessions:
            seen_sessions.add(sid)
            merged.append(match)

    # Sort by relevance score descending
    merged.sort(key=lambda m: m.get("relevance_score", 0), reverse=True)
    merged = merged[:limit]

    return {
        "query": query,
        "match_count": len(merged),
        "matches": merged,
    }


def _search_trajectories(
    *,
    query: str,
    user_id: str,
    current_session_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Search trajectory JSONL files for matching sessions."""
    from webot.trajectory import list_trajectories

    entries = list_trajectories(limit=500, user_id=user_id)
    if not entries:
        return []

    # Filter out current session
    entries = [e for e in entries if e.get("session_id") != current_session_id]

    if not query:
        # Return recent sessions without search
        return [
            {
                "session_id": e.get("session_id", ""),
                "timestamp": e.get("timestamp", ""),
                "model": e.get("model", ""),
                "message_count": e.get("message_count", 0),
                "tool_calls": e.get("tool_calls_count", 0),
                "completed": e.get("completed", False),
                "preview": _extract_preview(e),
                "relevance_score": 0,
                "source": "trajectory",
            }
            for e in entries[:limit]
        ]

    # Keyword search
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in entries:
        text = _extract_searchable_text(entry)
        text_tokens = _tokenize(text)
        score = len(query_tokens & text_tokens)
        if score > 0:
            scored.append((score, {
                "session_id": entry.get("session_id", ""),
                "timestamp": entry.get("timestamp", ""),
                "model": entry.get("model", ""),
                "message_count": entry.get("message_count", 0),
                "tool_calls": entry.get("tool_calls_count", 0),
                "completed": entry.get("completed", False),
                "preview": _extract_preview(entry, query),
                "relevance_score": score,
                "source": "trajectory",
            }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]


def _search_checkpoints(
    *,
    query: str,
    user_id: str,
    current_session_id: str,
    limit: int,
    db_path: str,
) -> list[dict[str, Any]]:
    """Search checkpoint DB for session metadata."""
    import sqlite3

    seen_threads: list[tuple[str, str]] = []
    seen_ids: set[str] = set()

    for path in iter_checkpoint_db_paths(db_path):
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
        except Exception:
            continue

        try:
            cursor = conn.execute(
                "SELECT DISTINCT thread_id FROM checkpoints ORDER BY rowid DESC LIMIT 200"
            )
            threads = [row["thread_id"] for row in cursor.fetchall()]
        except Exception:
            conn.close()
            continue
        finally:
            conn.close()

        for tid in threads:
            if not tid.startswith(f"{user_id}#"):
                continue
            sid = tid[len(user_id) + 1:]
            if sid == current_session_id or tid in seen_ids:
                continue
            seen_ids.add(tid)
            seen_threads.append((tid, sid))

    if not query:
        return [
            {
                "session_id": sid,
                "timestamp": "",
                "preview": f"Session: {sid}",
                "relevance_score": 0,
                "source": "checkpoint",
            }
            for _, sid in seen_threads[:limit]
        ]

    # For keyword search, we'd need message content in checkpoints
    # This is limited since checkpoints store binary state
    return []


def _tokenize(text: str) -> set[str]:
    """Extract searchable tokens from text."""
    return {token for token in re.findall(r"[a-z0-9_]{3,}", (text or "").lower())}


def _extract_searchable_text(entry: dict[str, Any]) -> str:
    """Extract all searchable text from a trajectory entry."""
    parts: list[str] = []
    for msg in entry.get("conversations", []):
        parts.append(msg.get("value", ""))
    parts.append(entry.get("model", ""))
    parts.append(entry.get("session_id", ""))
    return " ".join(parts)


def _extract_preview(entry: dict[str, Any], query: str = "") -> str:
    """Extract a preview snippet from a trajectory entry."""
    convos = entry.get("conversations", [])
    # Find first human message
    for msg in convos:
        if msg.get("from") == "human":
            text = msg.get("value", "")[:200]
            return text

    if convos:
        return convos[0].get("value", "")[:200]
    return ""
