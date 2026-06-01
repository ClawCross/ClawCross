"""Token estimation for LangChain message lists.

Heuristic-only. CJK characters count ~1 token each (matches real
tokenizers like cl100k_base / Claude on Chinese, Japanese, Korean);
non-CJK characters fall back to the OpenAI English rule of ~4 chars/token.
The real compression entrypoint lives in ``webot/compression.py``; this
module just provides the token counter shared between the runtime
compression pass and the static frontend view.
"""

from __future__ import annotations

import json

from langchain_core.messages import BaseMessage


# Codepoint ranges where one character ≈ one token under common BPE tokenizers.
# Kept short and inclusive; precision matters more than exhaustiveness.
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x3000, 0x303F),  # CJK Symbols and Punctuation
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xAC00, 0xD7AF),  # Hangul Syllables
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0xFF00, 0xFFEF),  # Halfwidth and Fullwidth Forms
)


def _is_cjk(cp: int) -> bool:
    for lo, hi in _CJK_RANGES:
        if lo <= cp <= hi:
            return True
        if cp < lo:
            return False
    return False


def _approx_tokens(text: str) -> int:
    """Mixed-script token estimate.

    CJK characters count 1 token each; remaining characters use the
    ~4 chars/token English rule. Empty / whitespace-only text returns 1
    so that a non-empty message never falls below the floor used by the
    rest of the compression pipeline.
    """
    s = (text or "").strip()
    if not s:
        return 1
    cjk = 0
    for ch in s:
        if _is_cjk(ord(ch)):
            cjk += 1
    other = len(s) - cjk
    return max(1, cjk + (other // 4))


def _tool_calls_tokens(msg: BaseMessage) -> int:
    """Estimate tokens contributed by an AIMessage's tool_calls block.

    LangChain stores tool calls on a separate attribute (not in ``content``),
    so the content-only path under-counts assistant turns that drive tools.
    Count name + JSON-serialised args for each call.
    """
    calls = getattr(msg, "tool_calls", None)
    if not calls:
        return 0
    total = 0
    for tc in calls:
        if not isinstance(tc, dict):
            continue
        total += _approx_tokens(str(tc.get("name", "")))
        args = tc.get("args")
        if args is None:
            continue
        if isinstance(args, str):
            total += _approx_tokens(args)
        else:
            try:
                total += _approx_tokens(json.dumps(args, ensure_ascii=False))
            except (TypeError, ValueError):
                total += _approx_tokens(str(args))
    return total


def _msg_tokens(msg: BaseMessage) -> int:
    """Estimate tokens in a message (content + tool_calls)."""
    content = msg.content
    if isinstance(content, str):
        total = _approx_tokens(content)
    elif isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, str):
                total += _approx_tokens(part)
            elif isinstance(part, dict):
                total += _approx_tokens(part.get("text", ""))
    else:
        total = _approx_tokens(str(content))
    return total + _tool_calls_tokens(msg)


def _total_tokens(messages: list[BaseMessage]) -> int:
    return sum(_msg_tokens(m) for m in messages)


def estimate_messages_tokens(messages: list[BaseMessage]) -> int:
    """Public wrapper around the same heuristic compress_context uses."""
    return _total_tokens(messages)
