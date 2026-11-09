"""
Notification & Lifecycle Management System.

Features:
1. Notification System: push notifications for long-running operations
2. TTL Cleanup: auto-expire old sessions, forks, and cached data
3. Broadcast: send messages to multiple sessions/agents at once
4. Session Resume: restore interrupted sessions

Ported from openclaw-claude-code and oh-my-codex patterns.
"""

from __future__ import annotations

import asyncio
import time
import utils.scheduler_service
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from enum import Enum


# ============================================================================
# 1. Notification System
# ============================================================================

class NotificationLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"


@dataclass
class Notification:
    """A notification for a user/session."""
    notification_id: str
    user_id: str
    session_id: str
    level: str
    title: str
    body: str
    source: str = ""  # Which component generated this
    read: bool = False
    created_at: str = ""
    expires_at: str = ""

    def __post_init__(self):
        if not self.notification_id:
            self.notification_id = f"notif_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "notification_id": self.notification_id,
            "level": self.level,
            "title": self.title,
            "body": self.body[:500],
            "source": self.source,
            "read": self.read,
            "created_at": self.created_at,
        }


_notifications: dict[str, list[Notification]] = {}  # user_id -> notifications


def send_notification(
    *,
    user_id: str,
    session_id: str = "",
    level: str = NotificationLevel.INFO,
    title: str,
    body: str,
    source: str = "",
) -> Notification:
    """Send a notification to a user."""
    notif = Notification(
        notification_id="",
        user_id=user_id,
        session_id=session_id,
        level=level,
        title=title,
        body=body,
        source=source,
    )
    if user_id not in _notifications:
        _notifications[user_id] = []
    _notifications[user_id].append(notif)

    # Keep max 100 notifications per user
    if len(_notifications[user_id]) > 100:
        _notifications[user_id] = _notifications[user_id][-100:]

    return notif


def get_notifications(
    user_id: str,
    *,
    unread_only: bool = False,
    limit: int = 20,
) -> list[Notification]:
    """Get notifications for a user."""
    notifs = _notifications.get(user_id, [])
    if unread_only:
        notifs = [n for n in notifs if not n.read]
    return list(reversed(notifs[-limit:]))


def mark_notification_read(user_id: str, notification_id: str) -> bool:
    """Mark a notification as read."""
    for notif in _notifications.get(user_id, []):
        if notif.notification_id == notification_id:
            notif.read = True
            return True
    return False


def mark_all_read(user_id: str) -> int:
    """Mark all notifications as read for a user."""
    count = 0
    for notif in _notifications.get(user_id, []):
        if not notif.read:
            notif.read = True
            count += 1
    return count


# ============================================================================
# 2. TTL Cleanup
# ============================================================================

@dataclass
class TTLEntry:
    """An entry with a time-to-live."""
    key: str
    category: str
    created_at: float
    ttl_seconds: float
    cleanup_fn: Callable | None = None

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > self.ttl_seconds

    @property
    def remaining_seconds(self) -> float:
        return max(0, self.ttl_seconds - (time.time() - self.created_at))


_ttl_registry: dict[str, TTLEntry] = {}

# Default TTLs by category
_DEFAULT_TTLS: dict[str, float] = {
    "session_fork": 3600 * 24,      # 24 hours
    "ralph_loop": 3600 * 4,         # 4 hours
    "council_session": 3600,         # 1 hour
    "deep_interview": 3600 * 8,     # 8 hours
    "coordinator_run": 3600 * 12,   # 12 hours
    "notification": 3600 * 72,      # 72 hours
    "cost_tracker": 3600 * 24 * 7,  # 7 days
    "hud_state": 3600,              # 1 hour
}


def register_ttl(
    key: str,
    category: str,
    ttl_seconds: float | None = None,
    cleanup_fn: Callable | None = None,
) -> TTLEntry:
    """Register an entry with TTL."""
    if ttl_seconds is None:
        ttl_seconds = _DEFAULT_TTLS.get(category, 3600)

    entry = TTLEntry(
        key=key,
        category=category,
        created_at=time.time(),
        ttl_seconds=ttl_seconds,
        cleanup_fn=cleanup_fn,
    )
    _ttl_registry[key] = entry
    return entry


def run_ttl_cleanup() -> dict[str, int]:
    """Run TTL cleanup, removing expired entries. Returns counts by category."""
    expired: list[TTLEntry] = []
    for key, entry in list(_ttl_registry.items()):
        if entry.expired:
            expired.append(entry)
            del _ttl_registry[key]

    counts: dict[str, int] = {}
    for entry in expired:
        counts[entry.category] = counts.get(entry.category, 0) + 1
        if entry.cleanup_fn:
            try:
                entry.cleanup_fn()
            except Exception:
                pass

    return counts


def get_ttl_stats() -> dict[str, Any]:
    """Get TTL registry statistics."""
    by_category: dict[str, int] = {}
    expired_count = 0
    for entry in _ttl_registry.values():
        by_category[entry.category] = by_category.get(entry.category, 0) + 1
        if entry.expired:
            expired_count += 1

    return {
        "total_entries": len(_ttl_registry),
        "expired_pending": expired_count,
        "by_category": by_category,
    }


# ============================================================================
# 3. Broadcast
# ============================================================================

@dataclass
class BroadcastMessage:
    """A message broadcast to multiple sessions."""
    broadcast_id: str
    sender_user_id: str
    sender_session_id: str
    target_sessions: list[str]
    content: str
    delivered_to: list[str] = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self):
        if not self.broadcast_id:
            self.broadcast_id = f"broadcast_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


_broadcasts: dict[str, BroadcastMessage] = {}


def create_broadcast(
    *,
    sender_user_id: str,
    sender_session_id: str,
    target_sessions: list[str],
    content: str,
) -> BroadcastMessage:
    """Create a broadcast message to multiple sessions."""
    msg = BroadcastMessage(
        broadcast_id="",
        sender_user_id=sender_user_id,
        sender_session_id=sender_session_id,
        target_sessions=target_sessions,
        content=content,
    )
    _broadcasts[msg.broadcast_id] = msg
    return msg


def mark_broadcast_delivered(broadcast_id: str, session_id: str) -> bool:
    """Mark a broadcast as delivered to a session."""
    msg = _broadcasts.get(broadcast_id)
    if msg and session_id not in msg.delivered_to:
        msg.delivered_to.append(session_id)
        return True
    return False


def get_broadcast(broadcast_id: str) -> BroadcastMessage | None:
    return _broadcasts.get(broadcast_id)


# ============================================================================
# 4. Session Resume
# ============================================================================

@dataclass
class SessionCheckpoint:
    """Checkpoint for session resumption."""
    checkpoint_id: str
    user_id: str
    session_id: str
    state_summary: str
    pending_tasks: list[str] = field(default_factory=list)
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self):
        if not self.checkpoint_id:
            self.checkpoint_id = f"ckpt_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


_checkpoints: dict[str, SessionCheckpoint] = {}


def save_session_checkpoint(
    *,
    user_id: str,
    session_id: str,
    state_summary: str,
    pending_tasks: list[str] | None = None,
    context_snapshot: dict[str, Any] | None = None,
) -> SessionCheckpoint:
    """Save a checkpoint for session resumption."""
    checkpoint = SessionCheckpoint(
        checkpoint_id="",
        user_id=user_id,
        session_id=session_id,
        state_summary=state_summary,
        pending_tasks=pending_tasks or [],
        context_snapshot=context_snapshot or {},
    )
    key = f"{user_id}#{session_id}"
    _checkpoints[key] = checkpoint
    return checkpoint


def get_session_checkpoint(user_id: str, session_id: str) -> SessionCheckpoint | None:
    """Get the latest checkpoint for a session."""
    return _checkpoints.get(f"{user_id}#{session_id}")


def build_resume_prompt(checkpoint: SessionCheckpoint) -> str:
    """Build a prompt to resume from a checkpoint."""
    lines = [
        "【会话恢复】",
        f"上次中断时的状态: {checkpoint.state_summary}",
        "",
    ]
    if checkpoint.pending_tasks:
        lines.append("待完成的任务:")
        for task in checkpoint.pending_tasks:
            lines.append(f"  - {task}")
    lines.append("\n请从上次中断的位置继续。")
    return "\n".join(lines)


# ============================================================================
# 5. Model Hot-swap
# ============================================================================

@dataclass
class ModelSwapRequest:
    """Request to swap the active model mid-session."""
    user_id: str
    session_id: str
    target_model: str
    reason: str = ""
    effective_from_turn: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_model": self.target_model,
            "reason": self.reason,
            "effective_from_turn": self.effective_from_turn,
        }


_model_swaps: dict[str, ModelSwapRequest] = {}


def request_model_swap(
    user_id: str,
    session_id: str,
    target_model: str,
    reason: str = "",
) -> ModelSwapRequest:
    """Request a model swap for the current session."""
    key = f"{user_id}#{session_id}"
    request = ModelSwapRequest(
        user_id=user_id,
        session_id=session_id,
        target_model=target_model,
        reason=reason,
    )
    _model_swaps[key] = request
    return request


def get_pending_model_swap(user_id: str, session_id: str) -> ModelSwapRequest | None:
    """Get pending model swap request."""
    return _model_swaps.get(f"{user_id}#{session_id}")


def consume_model_swap(user_id: str, session_id: str) -> ModelSwapRequest | None:
    """Consume (apply) a pending model swap."""
    return _model_swaps.pop(f"{user_id}#{session_id}", None)
