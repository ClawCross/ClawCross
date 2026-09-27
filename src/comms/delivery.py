"""Which members a message wakes, how often agents may wake each other, and
what a woken member missed."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

# "@所有人" / "@all" / "@everyone", not glued to a longer word.
_MENTION_ALL = re.compile(r"@(所有人|all|everyone)(?![A-Za-z0-9_\-])", re.IGNORECASE)


def mentions_everyone(content: str) -> bool:
    return bool(_MENTION_ALL.search(content or ""))


@dataclass(slots=True)
class WakeRequest:
    """Everything the wake rule needs to know about one message."""

    agent_ids: list[str]              # agent members' ids in the conversation (excluding nobody)
    sender_id: str = ""               # the sending member's id; "" for a human
    mentions: list[str] = field(default_factory=list)
    mention_all: bool = False
    primary_id: str | None = None     # the conversation's lead agent, if any
    direct: bool = False              # a one-to-one conversation


def select_wake_targets(req: WakeRequest) -> list[str]:
    """The agent members a message wakes.

    * A human wakes the agents they @; with no @, the lead if there is one,
      else every agent. In a one-to-one chat the other side is always woken.
    * An agent wakes only the agents it @ — a plain agent message is read by
      everyone but wakes nobody, which stops agents from answering each other
      in circles. An agent that is not the lead reaches only the lead.
    * ``@所有人`` wakes every agent, but only from a human or the lead.
    """
    others = [a for a in req.agent_ids if a != req.sender_id]
    sender_is_human = not req.sender_id
    sender_is_lead = bool(req.primary_id) and req.sender_id == req.primary_id

    if req.direct:
        return others if sender_is_human else []
    if req.mention_all and (sender_is_human or sender_is_lead):
        return others
    if sender_is_human:
        if req.mentions:
            return [a for a in others if a in req.mentions]
        if req.primary_id and req.primary_id in others:
            return [req.primary_id]
        return others
    if req.primary_id and not sender_is_lead:
        return [req.primary_id] if req.primary_id in others else []
    return [a for a in others if a in req.mentions]


class StormGuard:
    """Caps agent-caused wakes per conversation between two human messages.

    Agents that keep @-ing each other would otherwise run forever; a human
    message starts a fresh budget.
    """

    def __init__(self, limit: int = 24, window_sec: float = 600.0):
        self.limit = limit
        self.window_sec = window_sec
        self._lock = threading.Lock()
        self._wakes: dict[str, list[float]] = {}

    def human_spoke(self, conversation_id: str) -> None:
        with self._lock:
            self._wakes.pop(conversation_id, None)

    def allow(self, conversation_id: str, count: int) -> bool:
        """Spend *count* agent-caused wakes; False (and nothing spent) when over budget."""
        if count <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._wakes.get(conversation_id, []) if now - t < self.window_sec]
            if len(recent) + count > self.limit:
                self._wakes[conversation_id] = recent
                return False
            recent.extend([now] * count)
            self._wakes[conversation_id] = recent
            return True


def render_digest(messages: list[dict], *, limit_chars: int = 300) -> str:
    """Messages a member was not woken for, as a short block for its next wake."""
    if not messages:
        return ""
    lines = []
    for m in messages:
        display = str(m.get("sender_display") or "")
        parts = display.split("#")
        name = parts[2] if len(parts) >= 3 and parts[2] else (m.get("sender") or "?")
        text = " ".join(str(m.get("content") or "").split())
        if len(text) > limit_chars:
            text = text[:limit_chars] + "…"
        lines.append(f"- {name}: {text}")
    return "[你上次之后的群聊消息（未唤醒你，仅供了解）]\n" + "\n".join(lines) + "\n\n"
