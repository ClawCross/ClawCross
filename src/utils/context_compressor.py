"""
Multi-level Context Compression Pipeline – Claude Code style.

Levels (from lightest to heaviest):
1. Snip    – trim oversized individual messages (tool results, user inputs)
2. Micro   – compress old tool results to one-line summaries
3. Collapse – merge consecutive assistant+tool pairs into summaries
4. Auto    – full LLM-based summarization of conversation history
5. Evict   – drop oldest messages entirely when budget exhausted

Each level is tried in order; the pipeline stops as soon as the context
fits within the token budget.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage


@dataclass(frozen=True)
class CompressionStats:
    """Statistics from a compression run."""
    level_applied: str
    original_messages: int
    final_messages: int
    original_tokens: int
    final_tokens: int
    messages_removed: int = 0
    messages_compressed: int = 0


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


def _stringify(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


# ---------------------------------------------------------------------------
# Level 1: Snip – trim oversized individual messages
# ---------------------------------------------------------------------------

def _snip_message(msg: BaseMessage, char_limit: int = 3000) -> BaseMessage:
    """Trim an individual message if it exceeds char_limit."""
    content = _stringify(msg.content)
    if len(content) <= char_limit:
        return msg

    head = max(100, char_limit // 2)
    tail = max(80, char_limit - head - 60)
    trimmed = (
        content[:head]
        + f"\n... [snipped, original {len(content)} chars] ...\n"
        + content[-tail:]
    )

    if isinstance(msg, ToolMessage):
        return ToolMessage(
            content=trimmed,
            tool_call_id=getattr(msg, "tool_call_id", ""),
            name=getattr(msg, "name", ""),
        )
    if isinstance(msg, AIMessage):
        return AIMessage(
            content=trimmed,
            additional_kwargs=dict(getattr(msg, "additional_kwargs", {}) or {}),
            response_metadata=dict(getattr(msg, "response_metadata", {}) or {}),
            tool_calls=list(getattr(msg, "tool_calls", []) or []),
            invalid_tool_calls=list(getattr(msg, "invalid_tool_calls", []) or []),
        )
    if isinstance(msg, HumanMessage):
        return HumanMessage(content=trimmed)
    return msg


def level_snip(
    messages: list[BaseMessage],
    *,
    token_budget: int,
    char_limit: int = 3000,
    preserve_recent: int = 4,
) -> list[BaseMessage]:
    """Level 1: Snip oversized messages, preserving the most recent ones."""
    if _total_tokens(messages) <= token_budget:
        return messages

    result = []
    cutoff = max(0, len(messages) - preserve_recent)
    for i, msg in enumerate(messages):
        if i < cutoff and not isinstance(msg, SystemMessage):
            result.append(_snip_message(msg, char_limit))
        else:
            result.append(msg)
    return result


# ---------------------------------------------------------------------------
# Level 2: Micro – compress old tool results to one-liners
# ---------------------------------------------------------------------------

def _micro_compress_tool(msg: ToolMessage) -> ToolMessage:
    """Compress a tool result to a brief summary."""
    content = _stringify(msg.content)
    tool_name = getattr(msg, "name", "") or "tool"

    if len(content) <= 200:
        return msg

    # Extract first meaningful line
    lines = content.strip().split("\n")
    first_line = ""
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("[") and not stripped.startswith("#"):
            first_line = stripped[:120]
            break

    summary = f"[{tool_name} result: {first_line or content[:80]}... ({len(content)} chars)]"
    return ToolMessage(
        content=summary,
        tool_call_id=getattr(msg, "tool_call_id", ""),
        name=tool_name,
    )


def level_micro(
    messages: list[BaseMessage],
    *,
    token_budget: int,
    preserve_recent: int = 6,
) -> list[BaseMessage]:
    """Level 2: Compress old tool results to one-line summaries."""
    if _total_tokens(messages) <= token_budget:
        return messages

    result = []
    cutoff = max(0, len(messages) - preserve_recent)
    for i, msg in enumerate(messages):
        if i < cutoff and isinstance(msg, ToolMessage):
            result.append(_micro_compress_tool(msg))
        else:
            result.append(msg)
    return result


# ---------------------------------------------------------------------------
# Level 3: Collapse – merge consecutive assistant+tool pairs
# ---------------------------------------------------------------------------

def level_collapse(
    messages: list[BaseMessage],
    *,
    token_budget: int,
    preserve_recent: int = 8,
) -> list[BaseMessage]:
    """Level 3: Collapse old assistant+tool message pairs into summaries."""
    if _total_tokens(messages) <= token_budget:
        return messages

    cutoff = max(0, len(messages) - preserve_recent)
    old_part = messages[:cutoff]
    recent_part = messages[cutoff:]

    if not old_part:
        return messages

    collapsed: list[BaseMessage] = []
    summary_lines = [
        "以下为早期对话的压缩摘要（assistant+tool 交互已合并）：",
    ]

    i = 0
    while i < len(old_part):
        msg = old_part[i]
        if isinstance(msg, SystemMessage):
            collapsed.append(msg)
            i += 1
            continue

        if isinstance(msg, HumanMessage):
            text = _stringify(msg.content)[:200]
            summary_lines.append(f"- user: {text}")
            i += 1
            continue

        if isinstance(msg, AIMessage):
            text = _stringify(msg.content)[:150]
            # Check if followed by tool messages
            tool_summaries = []
            j = i + 1
            while j < len(old_part) and isinstance(old_part[j], ToolMessage):
                tool_name = getattr(old_part[j], "name", "") or "tool"
                tool_text = _stringify(old_part[j].content)[:80]
                tool_summaries.append(f"{tool_name}→{tool_text}")
                j += 1

            if tool_summaries:
                summary_lines.append(
                    f"- assistant: {text} [tools: {'; '.join(tool_summaries)}]"
                )
            else:
                summary_lines.append(f"- assistant: {text}")
            i = j
            continue

        # ToolMessage without preceding AI (orphan)
        if isinstance(msg, ToolMessage):
            tool_name = getattr(msg, "name", "") or "tool"
            summary_lines.append(f"- {tool_name}: {_stringify(msg.content)[:100]}")
            i += 1
            continue

        i += 1

    summary = HumanMessage(content="\n".join(summary_lines))
    return [summary] + collapsed + recent_part


# ---------------------------------------------------------------------------
# Level 4: Auto – LLM-based summarization (synchronous stub, async in agent)
# ---------------------------------------------------------------------------

def level_auto_summary_prompt(messages: list[BaseMessage], preserve_recent: int = 8) -> str:
    """
    Generate a prompt for LLM-based summarization of older messages.

    The agent should call the LLM with this prompt to get a compressed summary,
    then replace the older messages with the result.
    """
    cutoff = max(0, len(messages) - preserve_recent)
    old_part = messages[:cutoff]

    if not old_part:
        return ""

    lines = ["请将以下早期对话历史压缩为一份简洁的摘要，保留关键决策、操作结果和未完成事项：\n"]
    for msg in old_part:
        role = "system"
        if isinstance(msg, HumanMessage):
            role = "user"
        elif isinstance(msg, AIMessage):
            role = "assistant"
        elif isinstance(msg, ToolMessage):
            role = f"tool:{getattr(msg, 'name', '')}"
        text = _stringify(msg.content)[:300]
        lines.append(f"[{role}] {text}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Level 5: Evict – drop oldest messages entirely
# ---------------------------------------------------------------------------

def level_evict(
    messages: list[BaseMessage],
    *,
    token_budget: int,
    preserve_recent: int = 6,
) -> list[BaseMessage]:
    """Level 5: Drop oldest messages until within budget."""
    if _total_tokens(messages) <= token_budget:
        return messages

    # Always keep system messages, existing summaries, and the latest user
    # request. Dropping the current ask is worse than exceeding the budget.
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    non_system = [m for m in messages if not isinstance(m, SystemMessage)]
    recent_count = min(preserve_recent, len(non_system))
    recent = non_system[-recent_count:] if recent_count > 0 else []
    protected_ids = {id(m) for m in system_msgs}

    latest_human: HumanMessage | None = None
    for msg in reversed(non_system):
        if isinstance(msg, HumanMessage):
            latest_human = msg
            protected_ids.add(id(msg))
            break

    summary_msgs: list[BaseMessage] = []
    for msg in non_system:
        if isinstance(msg, HumanMessage) and isinstance(msg.content, str):
            if "压缩摘要" in msg.content or "compressed summary" in msg.content.lower():
                protected_ids.add(id(msg))
                summary_msgs.append(msg)

    result = system_msgs + summary_msgs + recent
    if latest_human is not None and all(id(m) != id(latest_human) for m in result):
        result.append(latest_human)

    deduped: list[BaseMessage] = []
    seen_ids: set[int] = set()
    for msg in result:
        if id(msg) in seen_ids:
            continue
        deduped.append(msg)
        seen_ids.add(id(msg))
    result = deduped

    # If still over budget, trim unprotected recent messages too.
    while _total_tokens(result) > token_budget and len(result) > 1:
        removed = False
        for i, msg in enumerate(result):
            if id(msg) not in protected_ids:
                result.pop(i)
                removed = True
                break
        if not removed:
            break

    return result


# ---------------------------------------------------------------------------
# Pipeline: run all levels in order
# ---------------------------------------------------------------------------

def compress_context(
    messages: list[BaseMessage],
    *,
    token_budget: int = 12000,
    preserve_recent: int = 8,
) -> tuple[list[BaseMessage], CompressionStats]:
    """
    Run the full 5-level compression pipeline.

    Returns the compressed messages and compression statistics.
    """
    original_count = len(messages)
    original_tokens = _total_tokens(messages)

    if original_tokens <= token_budget:
        return messages, CompressionStats(
            level_applied="none",
            original_messages=original_count,
            final_messages=original_count,
            original_tokens=original_tokens,
            final_tokens=original_tokens,
        )

    # Level 1: Snip
    result = level_snip(messages, token_budget=token_budget, preserve_recent=preserve_recent)
    if _total_tokens(result) <= token_budget:
        return result, CompressionStats(
            level_applied="snip",
            original_messages=original_count,
            final_messages=len(result),
            original_tokens=original_tokens,
            final_tokens=_total_tokens(result),
            messages_compressed=sum(1 for a, b in zip(messages, result) if a is not b),
        )

    # Level 2: Micro
    result = level_micro(result, token_budget=token_budget, preserve_recent=preserve_recent)
    if _total_tokens(result) <= token_budget:
        return result, CompressionStats(
            level_applied="micro",
            original_messages=original_count,
            final_messages=len(result),
            original_tokens=original_tokens,
            final_tokens=_total_tokens(result),
        )

    # Level 3: Collapse
    result = level_collapse(result, token_budget=token_budget, preserve_recent=preserve_recent)
    if _total_tokens(result) <= token_budget:
        return result, CompressionStats(
            level_applied="collapse",
            original_messages=original_count,
            final_messages=len(result),
            original_tokens=original_tokens,
            final_tokens=_total_tokens(result),
        )

    # Level 4: Auto summary would be handled async in the agent
    # (We provide the prompt but don't actually call LLM here)

    # Level 5: Evict
    result = level_evict(result, token_budget=token_budget, preserve_recent=max(4, preserve_recent // 2))
    return result, CompressionStats(
        level_applied="evict",
        original_messages=original_count,
        final_messages=len(result),
        original_tokens=original_tokens,
        final_tokens=_total_tokens(result),
        messages_removed=original_count - len(result),
    )
