"""
WeBot memory/Kairos/auto-dream helpers.

This is a browser-native adaptation of Claude Code's memory system:
- per-project memory directories
- MEMORY.md index
- daily append-only logs
- relevant-memory recall
- simple Kairos/auto-dream state and consolidation
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any

from webot.profiles import slugify
from webot.runtime_store import save_memory_state
from webot.workspace import resolve_session_workspace


PROJECT_ROOT = Path(__file__).resolve().parents[2]
from utils.runtime_paths import USER_FILES_DIR
_MAX_INDEX_LINES = 200
_MAX_INDEX_BYTES = 25_000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _user_root(user_id: str) -> Path:
    root = USER_FILES_DIR / (user_id or "anonymous")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _project_slug(user_id: str, session_id: str) -> str:
    workspace = resolve_session_workspace(user_id, session_id)
    source = str(workspace.cwd or workspace.root or session_id or "default")
    return slugify(source.replace("/", "-"), "default-project") or "default-project"


def _sync_runtime_store(
    user_id: str,
    session_id: str,
    *,
    dream_status: str = "idle",
    active_run_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    local_state = _load_state(user_id, session_id)
    payload = {
        "enabled": True,
        "project_slug": _project_slug(user_id, session_id),
        "memory_dir": str(get_memory_dir(user_id, session_id)),
        "index_path": str(_index_path(user_id, session_id)),
        "daily_log_path": str(_daily_log_path(user_id, session_id)),
        "kairos_enabled": bool(local_state.get("kairos_enabled", False)),
        "last_dream_at": str(local_state.get("last_dream_at") or ""),
        "log_entries_since_dream": int(local_state.get("log_entries_since_dream") or 0),
    }
    save_memory_state(
        user_id,
        session_id,
        project_slug=payload["project_slug"],
        memory_dir=payload["memory_dir"],
        index_path=payload["index_path"],
        kairos_enabled=payload["kairos_enabled"],
        dream_status=dream_status,
        active_run_id=active_run_id,
        last_dream_at=payload["last_dream_at"],
        daily_log_path=payload["daily_log_path"],
        metadata=metadata or {},
    )
    return payload


def get_memory_dir(user_id: str, session_id: str) -> Path:
    root = _user_root(user_id) / "projects" / _project_slug(user_id, session_id) / "memory"
    (root / "entries").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    return root


def _state_path(user_id: str, session_id: str) -> Path:
    return get_memory_dir(user_id, session_id) / "state.json"


def _index_path(user_id: str, session_id: str) -> Path:
    return get_memory_dir(user_id, session_id) / "MEMORY.md"


def _daily_log_path(user_id: str, session_id: str, at: datetime | None = None) -> Path:
    current = at or _utc_now()
    log_root = get_memory_dir(user_id, session_id) / "logs" / current.strftime("%Y") / current.strftime("%m")
    log_root.mkdir(parents=True, exist_ok=True)
    return log_root / f"{current.strftime('%Y-%m-%d')}.md"


def _tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9_]{3,}", (text or "").lower())}


def _load_state(user_id: str, session_id: str) -> dict[str, Any]:
    path = _state_path(user_id, session_id)
    if not path.is_file():
        return {"kairos_enabled": False, "last_dream_at": "", "log_entries_since_dream": 0, "updated_at": ""}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "kairos_enabled": bool(raw.get("kairos_enabled", False)),
        "last_dream_at": str(raw.get("last_dream_at") or ""),
        "log_entries_since_dream": int(raw.get("log_entries_since_dream") or 0),
        "updated_at": str(raw.get("updated_at") or ""),
    }


def _save_state(user_id: str, session_id: str, state: dict[str, Any]) -> dict[str, Any]:
    payload = dict(state)
    payload["updated_at"] = _utc_now().isoformat()
    _state_path(user_id, session_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def set_kairos_mode(user_id: str, session_id: str, enabled: bool, reason: str = "") -> dict[str, Any]:
    state = _load_state(user_id, session_id)
    state["kairos_enabled"] = bool(enabled)
    if reason:
        state["reason"] = reason[:240]
    _save_state(user_id, session_id, state)
    return ensure_memory_state(user_id, session_id, kairos_enabled=enabled)


def append_memory_entry(
    user_id: str,
    session_id: str,
    *,
    name: str,
    content: str,
    mem_type: str = "project",
    description: str = "",
) -> Path:
    # --- Memory injection detection (new: ported from Hermes Agent) ---
    from webot.memory_guard import scan_memory_content
    scan = scan_memory_content(content)
    if not scan.safe:
        import logging
        logging.getLogger("memory").warning(
            "Memory injection blocked for user=%s: %s", user_id, "; ".join(scan.violations)
        )
        raise ValueError(f"Memory content blocked by security scan: {'; '.join(scan.violations)}")

    memory_dir = get_memory_dir(user_id, session_id)
    filename = f"{slugify(name, 'memory')}.md"
    path = memory_dir / "entries" / filename
    frontmatter = [
        "---",
        f"name: {name.strip() or 'memory'}",
        f"description: {(description or name).strip()[:180]}",
        f"type: {(mem_type or 'project').strip()[:32]}",
        "---",
        "",
        content.strip(),
        "",
    ]
    path.write_text("\n".join(frontmatter), encoding="utf-8")
    refresh_memory_index(user_id, session_id)
    return path


def list_memory_entries(user_id: str, session_id: str) -> list[dict[str, Any]]:
    entry_dir = get_memory_dir(user_id, session_id) / "entries"
    rows: list[dict[str, Any]] = []
    for path in sorted(entry_dir.glob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True):
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        meta: dict[str, str] = {"name": path.stem, "description": "", "type": "project"}
        if lines[:1] == ["---"]:
            for line in lines[1:12]:
                if line.strip() == "---":
                    break
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip()] = value.strip()
        rows.append(
            {
                "name": meta.get("name", path.stem),
                "description": meta.get("description", ""),
                "type": meta.get("type", "project"),
                "path": str(path),
                "snippet": text[:280].replace("\n", " "),
            }
        )
    return rows


def refresh_memory_index(user_id: str, session_id: str) -> Path:
    entries = list_memory_entries(user_id, session_id)
    lines = ["# MEMORY", "", "Known memory entries for this project:", ""]
    for item in entries[:80]:
        lines.append(f"- `{item['name']}` [{item['type']}] - {item['description'] or item['snippet'][:120]}")
    text = "\n".join(lines[:_MAX_INDEX_LINES])
    encoded = text.encode("utf-8")
    if len(encoded) > _MAX_INDEX_BYTES:
        text = encoded[:_MAX_INDEX_BYTES].decode("utf-8", errors="ignore")
    path = _index_path(user_id, session_id)
    path.write_text(text, encoding="utf-8")
    return path


def append_daily_log(user_id: str, session_id: str, title: str, body: str) -> Path:
    path = _daily_log_path(user_id, session_id)
    timestamp = _utc_now().strftime("%H:%M:%S")
    block = f"\n## {timestamp} {title.strip()[:120]}\n\n{body.strip()[:2000]}\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(block)
    state = _load_state(user_id, session_id)
    state["log_entries_since_dream"] = int(state.get("log_entries_since_dream") or 0) + 1
    _save_state(user_id, session_id, state)
    _sync_runtime_store(user_id, session_id, metadata={"last_log_title": title[:120]})
    return path


def recall_relevant_memories(user_id: str, session_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
    query_tokens = _tokenize(query)
    ranked: list[tuple[int, dict[str, Any]]] = []
    for item in list_memory_entries(user_id, session_id):
        haystack = " ".join([item["name"], item["description"], item["snippet"]])
        score = len(query_tokens & _tokenize(haystack))
        if score > 0 or not query_tokens:
            ranked.append((score, item))
    ranked.sort(key=lambda pair: (pair[0], pair[1]["name"]), reverse=True)
    return [item for _, item in ranked[: max(1, limit)]]


def get_memory_state(user_id: str, session_id: str, query: str = "") -> dict[str, Any]:
    state = _load_state(user_id, session_id)
    entries = list_memory_entries(user_id, session_id)
    relevant = recall_relevant_memories(user_id, session_id, query, limit=5)
    return {
        "enabled": True,
        "project_slug": _project_slug(user_id, session_id),
        "memory_dir": str(get_memory_dir(user_id, session_id)),
        "index_path": str(_index_path(user_id, session_id)),
        "entry_count": len(entries),
        "relevant_entries": relevant,
        "daily_log_path": str(_daily_log_path(user_id, session_id)),
        "kairos_enabled": bool(state.get("kairos_enabled", False)),
        "last_dream_at": state.get("last_dream_at", ""),
        "log_entries_since_dream": int(state.get("log_entries_since_dream") or 0),
        "can_dream": should_run_auto_dream(user_id, session_id),
    }


def ensure_memory_state(
    user_id: str,
    session_id: str,
    *,
    project_slug: str = "",
    kairos_enabled: bool | None = None,
) -> dict[str, Any]:
    del project_slug
    memory_dir = get_memory_dir(user_id, session_id)
    memory_dir.mkdir(parents=True, exist_ok=True)
    state = _load_state(user_id, session_id)
    if kairos_enabled is not None:
        state["kairos_enabled"] = bool(kairos_enabled)
        _save_state(user_id, session_id, state)
    refresh_memory_index(user_id, session_id)
    payload = get_memory_state(user_id, session_id)
    _sync_runtime_store(
        user_id,
        session_id,
        dream_status="idle",
        metadata={"source": "ensure_memory_state"},
    )
    return payload


def should_run_auto_dream(user_id: str, session_id: str, *, min_hours: int = 24, min_entries: int = 5) -> bool:
    state = _load_state(user_id, session_id)
    last_dream_text = str(state.get("last_dream_at") or "")
    last_dream_at = None
    if last_dream_text:
        try:
            last_dream_at = datetime.fromisoformat(last_dream_text)
        except ValueError:
            last_dream_at = None
    enough_time = last_dream_at is None or last_dream_at <= (_utc_now() - timedelta(hours=min_hours))
    enough_entries = int(state.get("log_entries_since_dream") or 0) >= min_entries
    return enough_time and enough_entries


def run_auto_dream(
    user_id: str,
    session_id: str,
    *,
    query: str = "",
    force: bool = False,
    plan: dict[str, Any] | None = None,
    todos: list[dict[str, Any]] | None = None,
    verifications: list[dict[str, Any]] | None = None,
    inbox: list[dict[str, Any]] | None = None,
    recent_runs: list[dict[str, Any]] | None = None,
    recent_artifacts: list[dict[str, Any]] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    if not force and not should_run_auto_dream(user_id, session_id):
        return {
            "ran": False,
            "reason": "dream gates not satisfied",
            "state": ensure_memory_state(user_id, session_id),
        }
    memory_state = get_memory_state(user_id, session_id, query=query)
    log_path = Path(memory_state["daily_log_path"])
    recent_log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    relevant = memory_state["relevant_entries"]
    summary_lines = [
        "# Auto Dream",
        "",
        f"Generated at: {_utc_now().isoformat()}",
        "",
        "## Recent signal",
        recent_log[-2500:] if recent_log else "(no daily log yet)",
        "",
        "## Relevant memories",
    ]
    if relevant:
        for item in relevant:
            summary_lines.append(f"- {item['name']} [{item['type']}] - {item['description'] or item['snippet'][:160]}")
    else:
        summary_lines.append("- (none)")
    if plan:
        summary_lines.extend(["", "## Active plan", json.dumps(plan, ensure_ascii=False, indent=2)[:2400]])
    if todos:
        summary_lines.extend(["", "## Todos", json.dumps(todos[:20], ensure_ascii=False, indent=2)[:2400]])
    if verifications:
        summary_lines.extend(["", "## Verifications", json.dumps(verifications[:20], ensure_ascii=False, indent=2)[:2400]])
    if inbox:
        summary_lines.extend(["", "## Inbox", json.dumps(inbox[:20], ensure_ascii=False, indent=2)[:2400]])
    if recent_runs:
        summary_lines.extend(["", "## Recent runs", json.dumps(recent_runs[:10], ensure_ascii=False, indent=2)[:2400]])
    if recent_artifacts:
        summary_lines.extend(["", "## Recent artifacts", json.dumps(recent_artifacts[:10], ensure_ascii=False, indent=2)[:2400]])
    if reason:
        summary_lines.extend(["", f"## Trigger\n{reason[:400]}"])
    path = append_memory_entry(
        user_id,
        session_id,
        name="auto_dream_summary",
        content="\n".join(summary_lines),
        mem_type="project",
        description="Auto-dream consolidated summary",
    )
    state = _load_state(user_id, session_id)
    state["last_dream_at"] = _utc_now().isoformat()
    state["log_entries_since_dream"] = 0
    _save_state(user_id, session_id, state)
    append_daily_log(
        user_id,
        session_id,
        "Auto Dream",
        f"Consolidated memory state into {path.name}. Trigger: {(reason or 'auto')[:240]}",
    )
    synced_state = ensure_memory_state(user_id, session_id)
    _sync_runtime_store(
        user_id,
        session_id,
        dream_status="idle",
        metadata={"last_dream_summary": str(path)},
    )
    return {
        "ran": True,
        "summary_path": str(path),
        "state": synced_state,
    }
