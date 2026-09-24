"""Per-component accounting of the context sent to the model.

Providers report a single ``input_tokens`` total per call. Each component
(system prompt, tool schemas, runtime state, history, ...) is measured locally
with tiktoken and the counts are scaled so they add up to that real total —
only the total is exact, the split is proportional.
"""

from __future__ import annotations

from functools import lru_cache
import json
from typing import Any

from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from utils.context_compressor import _approx_tokens
from webot.compression import is_summary_message

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
