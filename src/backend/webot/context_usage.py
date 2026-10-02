"""Per-component accounting of the context sent to the model.

Providers report a single ``input_tokens`` total per call. Each component
(system prompt, tool schemas, runtime state, history, ...) is measured locally
with tiktoken and scaled to the real total. Stable append-only requests also
allow incremental allocation by input/output difference. Component allocation
remains inferred; after compaction the active view is explicitly estimated.
"""

from __future__ import annotations

from functools import lru_cache
import json
from typing import Any

from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from webot.context_compressor import _approx_tokens
from webot.compression import is_summary_message
from webot.context import RUNTIME_DELTA_KEY

CONTEXT_COMPONENTS = (
    "system_prompt",
    "tools",
    "runtime_state",
    "summary",
    "messages",
    "tool_results",
)

_IMAGE_BLOCK_TYPES = {"image", "image_url"}


@lru_cache(maxsize=1)
def _encoding():
    try:
        import tiktoken

        return tiktoken.get_encoding("o200k_base")
    except Exception:
        return None


def count_tokens(text: str) -> int:
    if not text:
        return 0
    encoding = _encoding()
    if encoding is None:
        return _approx_tokens(text)
    return len(encoding.encode(text, disallowed_special=()))


def tool_schemas(tools: list[Any]) -> list[dict]:
    """OpenAI-format schemas for bound tools; unconvertible entries are skipped."""
    schemas = []
    for tool in tools or []:
        try:
            schemas.append(convert_to_openai_tool(tool))
        except Exception:
            continue
    return schemas


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        parts = [content]
    else:
        parts = []
        for block in content or []:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, dict) and block.get("type") not in _IMAGE_BLOCK_TYPES:
                parts.append(json.dumps(block, ensure_ascii=False, default=str))
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        parts.append(json.dumps(
            [{"name": tc.get("name"), "args": tc.get("args")} for tc in tool_calls],
            ensure_ascii=False,
            default=str,
        ))
    return "\n".join(parts)


def estimate_context_components(
    *,
    system_prompt: str,
    tools: list[dict],
    runtime_state: str,
    messages: list[BaseMessage],
) -> dict[str, int]:
    components = dict.fromkeys(CONTEXT_COMPONENTS, 0)
    components["system_prompt"] = count_tokens(system_prompt)
    components["tools"] = count_tokens(json.dumps(tools, ensure_ascii=False)) if tools else 0
    components["runtime_state"] = count_tokens(runtime_state)
    for message in messages:
        delta = message.additional_kwargs.get(RUNTIME_DELTA_KEY)
        if isinstance(delta, str) and delta:
            components["runtime_state"] += count_tokens(delta)
        if is_summary_message(message):
            key = "summary"
        elif isinstance(message, ToolMessage):
            key = "tool_results"
        else:
            key = "messages"
        components[key] += count_tokens(_message_text(message))
    return components


def scale_components(components: dict[str, int], total: int) -> dict[str, int]:
    """Scale estimates to sum exactly to ``total`` (largest-remainder rounding)."""
    positive = {key: value for key, value in components.items() if value > 0}
    measured = sum(positive.values())
    if total <= 0 or measured <= 0:
        return {}
    shares = {key: value * total / measured for key, value in positive.items()}
    scaled = {key: int(share) for key, share in shares.items()}
    leftover = total - sum(scaled.values())
    for key in sorted(shares, key=lambda k: shares[k] - scaled[k], reverse=True)[:leftover]:
        scaled[key] += 1
    return scaled


def validate_context_capacity(*, system_prompt: str, tools: list[dict],
                              messages: list[BaseMessage], context_window: int,
                              output_reserve: int) -> int:
    """Reject a request that remains too large after safe history compaction."""
    estimate = sum(estimate_context_components(system_prompt=system_prompt, tools=tools,
                   runtime_state="", messages=messages).values())
    if estimate + output_reserve > context_window:
        raise ValueError(
            f"本轮输入约 {estimate} tokens，另需预留 {output_reserve} 输出 tokens，"
            f"超过配置的上下文窗口 {context_window}。请缩短输入、拆分附件或调整上下文设置；"
            "原始会话记录已保留。"
        )
    return estimate


def compaction_key(record) -> str:
    """Identify the committed view; API usage for another view is stale."""
    if not record:
        return ""
    import hashlib
    return hashlib.sha256(json.dumps([record.compacted_until, record.summary, record.updated_at], ensure_ascii=False).encode()).hexdigest()


def message_fingerprint(message) -> str:
    import hashlib
    payload = [message.type, message.content, getattr(message, "tool_calls", None),
               getattr(message, "tool_call_id", None), message.additional_kwargs]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def request_accounting(system_prompt, tools, messages, response, model):
    import hashlib
    prefix = hashlib.sha256(json.dumps([model, system_prompt, tools], ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    return {"prefix": prefix, "messages": [message_fingerprint(m) for m in messages],
            "response": message_fingerprint(response),
            "reasoning_output": bool(response.additional_kwargs.get("reasoning_content") or
                ((getattr(response, "usage_metadata", None) or {}).get("output_token_details") or {}).get("reasoning"))}


def difference_components(previous, current, messages, input_tokens):
    """Attribute a stable append-only request increment; otherwise keep estimates.

    Output tokens are not guaranteed to equal their serialized input cost, so
    even this API difference remains an inferred component allocation.
    """
    old = previous.get("request_accounting") or {}
    old_messages = old.get("messages") or []
    if old.get("reasoning_output") or not old_messages or old.get("prefix") != current.get("prefix"):
        return None
    boundary = len(old_messages)
    if current["messages"][:boundary] != old_messages or len(messages) <= boundary + 1:
        return None
    if current["messages"][boundary] != old.get("response"):
        return None
    appended = messages[boundary + 1:]
    # A runtime delta or multiple kinds of messages cannot be separated exactly
    # from a single input total. Fall back to local allocation.
    if any(m.additional_kwargs.get(RUNTIME_DELTA_KEY) for m in appended):
        return None
    kinds = {"tool_results" if isinstance(m, ToolMessage) else "messages" if m.type == "human" else "other" for m in appended}
    if len(kinds) != 1 or "other" in kinds:
        return None
    delta = input_tokens - int(previous.get("input_tokens", 0)) - int(previous.get("output_tokens", 0))
    if delta < 0:
        return None
    parts = dict(previous.get("breakdown") or {})
    output = parts.pop("output", 0)
    parts["messages"] = parts.get("messages", 0) + output
    key = next(iter(kinds))
    parts[key] = parts.get(key, 0) + delta
    if sum(parts.values()) != input_tokens:
        return None
    return parts


def compacted_components(record, messages):
    """Re-estimate the active view, retaining measured static-prefix allocation."""
    from webot.compression import compression_view_from_record
    parts = estimate_context_components(system_prompt="", tools=[], runtime_state="",
        messages=compression_view_from_record(record, messages))
    return parts
