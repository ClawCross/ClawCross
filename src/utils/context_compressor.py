"""Token estimation for LangChain message lists.

Heuristic-only (~4 chars/token). The real compression entrypoint lives in
``webot/compression.py``; this module just provides the token counter
shared between the runtime compression pass and the static frontend view.
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage


def _approx_tokens(text: str) -> int:
    """Quick char-based token estimate (~4 chars/token)."""
    return max(1, len((text or "").strip()) // 4)


def _msg_tokens(msg: BaseMessage) -> int:
    """Estimate tokens in a message."""
    content = msg.content
    if isinstance(content, str):
        return _approx_tokens(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, str):
                total += _approx_tokens(part)
            elif isinstance(part, dict):
                total += _approx_tokens(part.get("text", ""))
        return total
    return _approx_tokens(str(content))


def _total_tokens(messages: list[BaseMessage]) -> int:
    return sum(_msg_tokens(m) for m in messages)


def estimate_messages_tokens(messages: list[BaseMessage]) -> int:
    """Public wrapper around the same heuristic compress_context uses."""
    return _total_tokens(messages)
