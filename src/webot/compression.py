"""Single-pass context compression for ClawCross agent turns.

Replaces the previous 5-layer + 5-level pipeline with one entry point.

Design goals:
- KV-cache friendly: between compressions, prompt prefix = [summary] +
  messages[compacted_until:] stays byte-stable; new turns only append.
- Real summarization: when triggered, call an LLM to fold the segment
  into a capped-length summary. Mechanical fallback if LLM unavailable.
- No segment duplication on disk: LangGraph state already keeps every
  original message, so audit replay reads from there using compacted_until.
- Low frequency: trigger only when accumulated tokens cross a high
  threshold (default 75% of history budget) AND enough new messages
  have accrued since last compression (min_new_messages防抖).
- Newest messages never compressed: preserve_recent tail stays raw.
- Idempotent read path: static_compression_view reproduces the same
  view without writing, so endpoints (session_history) can show the
  badge without side effects.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from utils.checkpoint_repository import (
    ContextCompactionRecord,
    get_context_compaction,
    save_context_compaction,
)
from utils.context_compressor import _msg_tokens, estimate_messages_tokens

# Lazy imports for things that pull heavy modules
# - webot.context._store_runtime_text / _runtime_artifacts_enabled
# - webot.runtime_store.create_runtime_artifact
# - services.llm_factory.create_chat_model

_SUMMARY_HEADER = "以下为早期对话的持久压缩摘要："
_DEFAULT_TRIGGER_RATIO = 0.90
_DEFAULT_TARGET_RATIO = 0.55
_DEFAULT_PRESERVE_RECENT = 8
_DEFAULT_MIN_NEW_MESSAGES = 6
_DEFAULT_SUMMARY_RATIO = 0.20  # summary 字符上限 = budget tokens × 4 × 此比例
_DEFAULT_MAX_SUMMARY_CHARS_ABS = 0  # 0 = 不设绝对上限；>0 时取 min(动态, 绝对)
_DEFAULT_NEW_INPUT_ITEM_LIMIT = 10000  # 当轮新 HumanMessage 超过则落盘+excerpt

_TRIGGER_RATIO_ENV = "WEBOT_COMPRESSION_TRIGGER_RATIO"
_TARGET_RATIO_ENV = "WEBOT_COMPRESSION_TARGET_RATIO"
_PRESERVE_RECENT_ENV = "WEBOT_COMPRESSION_PRESERVE_RECENT"
_MIN_NEW_ENV = "WEBOT_COMPRESSION_MIN_NEW_MESSAGES"
_SUMMARY_RATIO_ENV = "WEBOT_COMPRESSION_SUMMARY_RATIO"
_MAX_SUMMARY_CHARS_ENV = "WEBOT_COMPRESSION_MAX_SUMMARY_CHARS"  # 仅作为绝对上限叠加
_NEW_INPUT_LIMIT_ENV = "WEBOT_NEW_INPUT_ITEM_LIMIT"
_SUMMARIZER_MODEL_ENV = "WEBOT_SUMMARIZER_MODEL"
_DISABLE_ENV = "WEBOT_COMPRESSION_DISABLED"


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _trigger_ratio() -> float:
    return min(0.95, max(0.10, _env_float(_TRIGGER_RATIO_ENV, _DEFAULT_TRIGGER_RATIO)))


def _target_ratio() -> float:
    return min(_trigger_ratio() - 0.05, max(0.05, _env_float(_TARGET_RATIO_ENV, _DEFAULT_TARGET_RATIO)))


def _preserve_recent_default() -> int:
    return max(2, _env_int(_PRESERVE_RECENT_ENV, _DEFAULT_PRESERVE_RECENT))


def _min_new_messages() -> int:
    return max(1, _env_int(_MIN_NEW_ENV, _DEFAULT_MIN_NEW_MESSAGES))


def _summary_ratio() -> float:
    return min(0.50, max(0.01, _env_float(_SUMMARY_RATIO_ENV, _DEFAULT_SUMMARY_RATIO)))


def _max_summary_chars(history_token_budget: int) -> int:
    """Dynamic cap: budget_tokens × 4 chars/token × ratio (default 0.20).

    Allows a hard absolute ceiling via WEBOT_COMPRESSION_MAX_SUMMARY_CHARS
    when set to a positive integer; 0 (default) means ratio-only.
    """
    dynamic = max(500, int(history_token_budget * 4 * _summary_ratio()))
    absolute = _env_int(_MAX_SUMMARY_CHARS_ENV, _DEFAULT_MAX_SUMMARY_CHARS_ABS)
    if absolute > 0:
        return min(dynamic, absolute)
    return dynamic


def _new_input_item_limit() -> int:
    return max(0, _env_int(_NEW_INPUT_LIMIT_ENV, _DEFAULT_NEW_INPUT_ITEM_LIMIT))


def _compression_enabled() -> bool:
    raw = os.getenv(_DISABLE_ENV, "0").strip().lower()
    return raw in {"", "0", "false", "off", "no"}


# ---------------------------------------------------------------------------
# Summary message construction / parsing
# ---------------------------------------------------------------------------

def _summary_to_message(summary: str) -> HumanMessage:
    body = summary.strip()
    if not body.startswith(_SUMMARY_HEADER):
        body = f"{_SUMMARY_HEADER}\n{body}"
    return HumanMessage(content=body)


# ---------------------------------------------------------------------------
# Boundary selection
# ---------------------------------------------------------------------------

def _message_has_tool_calls(msg: BaseMessage) -> bool:
    if not isinstance(msg, AIMessage):
        return False
    if getattr(msg, "tool_calls", None):
        return True
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        return any(isinstance(part, dict) and part.get("type") == "tool_use" for part in content)
    return False


def _find_safe_boundary(messages: list[BaseMessage], desired: int) -> int:
    """Adjust ``desired`` so messages[boundary:] does not start orphaned.

    LangChain requires AIMessage(tool_calls) and matching ToolMessages to
    stay paired. If the desired tail starts with a ToolMessage we shift
    the boundary back to include the preceding AIMessage + its tool block.
    """
    if not messages:
        return 0
    b = min(max(0, desired), len(messages))
    if b == 0 or b == len(messages):
        return b
    if isinstance(messages[b], ToolMessage):
        while b > 0 and isinstance(messages[b], ToolMessage):
            b -= 1
        if b > 0 and _message_has_tool_calls(messages[b - 1]):
            b -= 1
    return max(0, b)


def _pick_boundary(
    messages: list[BaseMessage],
    *,
    current_until: int,
    preserve_recent: int,
    target_tokens: int,
) -> int:
    """Pick the largest safe boundary whose tail (messages[b:]) fits target_tokens.

    Walks from messages[-preserve_recent] downward; first b whose tail tokens
    <= target_tokens wins. Falls back to the last safe boundary otherwise so
    we still fold something rather than nothing.

    Uses a suffix-sum of per-message tokens so the tail-cost lookup is O(1),
    bringing the overall pass to O(N) instead of O(N²).
    """
    if not messages:
        return current_until
    n = len(messages)
    max_b = max(0, n - preserve_recent)
    if max_b <= current_until:
        return current_until
    # suffix[i] = sum of tokens of messages[i:]; suffix[n] = 0
    suffix = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix[i] = suffix[i + 1] + _msg_tokens(messages[i])
    best_fallback = current_until
    for desired in range(max_b, current_until, -1):
        safe = _find_safe_boundary(messages, desired)
        if safe <= current_until:
            continue
        best_fallback = max(best_fallback, safe)
        if suffix[safe] <= target_tokens:
            return safe
    return best_fallback


# ---------------------------------------------------------------------------
# Summarizer
# ---------------------------------------------------------------------------

SummarizerFn = Callable[[str, list[BaseMessage], int], str]
"""Summarizer signature: (previous_summary, segment, target_chars) -> summary text.

``target_chars`` is a soft cap fed into the LLM prompt as a length hint;
apply_compression also enforces it as a hard truncate after the call returns.
"""


def _truncate_to_cap(text: str, cap_chars: int) -> str:
    if cap_chars <= 0 or len(text) <= cap_chars:
        return text
    keep = cap_chars - 30
    return text[:keep] + "\n...[summary truncated]"


def _stringify(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(part.get("text") or part.get("content") or "")
        return "\n".join(p for p in parts if p)
    return str(content)


def _role_label(msg: BaseMessage) -> str:
    if isinstance(msg, HumanMessage):
        return "user"
    if isinstance(msg, ToolMessage):
        return f"tool:{getattr(msg, 'name', '') or 'unknown'}"
    if isinstance(msg, SystemMessage):
        return "system"
    return "assistant"


def _render_segment_for_prompt(segment: list[BaseMessage], *, per_msg_cap: int = 1200) -> str:
    """Render the segment as a readable transcript for the summarizer LLM."""
    lines: list[str] = []
    for i, msg in enumerate(segment):
        role = _role_label(msg)
        body = _stringify(msg.content).strip()
        if len(body) > per_msg_cap:
            body = body[:per_msg_cap] + f"\n...[truncated, {len(body)} chars total]"
        lines.append(f"[{i + 1}] {role}: {body}")
    return "\n\n".join(lines)


def _mechanical_summarizer(previous_summary: str, segment: list[BaseMessage], target_chars: int) -> str:
    """Fallback summarizer used when LLM call is unavailable / fails.

    Rolling digest: previous summary squashed to one line, each new message
    excerpted to ~280 chars with role prefix. Keeps the format machine-
    parseable but loses semantic understanding. Output is truncated to
    target_chars at the end if it overruns.
    """
    _ = target_chars  # truncation handled by caller via _truncate_to_cap
    lines = [_SUMMARY_HEADER]
    prev = previous_summary.strip()
    if prev:
        if prev.startswith(_SUMMARY_HEADER):
            prev = "\n".join(prev.splitlines()[1:]).strip()
        if prev:
            prev_line = prev.replace("\n", " ")
            if len(prev_line) > 1200:
                prev_line = prev_line[:1200] + "..."
            lines.append(f"- previous_summary: {prev_line}")
    for msg in segment:
        role = _role_label(msg)
        text = _stringify(msg.content).replace("\n", " ")
        if len(text) > 280:
            text = text[:277] + "..."
        lines.append(f"- {role}: {text}")
    return "\n".join(lines)


def make_llm_summarizer(
    *,
    model: Optional[str] = None,
    max_output_tokens: int = 2000,
) -> SummarizerFn:
    """Build a summarizer that calls an LLM to compress the segment.

    Resolves the model from ``WEBOT_SUMMARIZER_MODEL`` env when ``model``
    is None, falling back to the project's default chat model. Returns a
    function whose signature matches the mechanical fallback so it can
    be swapped in transparently. Any exception during the LLM call falls
    through to the mechanical summarizer.
    """
    target_model = (model or os.getenv(_SUMMARIZER_MODEL_ENV, "")).strip() or None

    def _summarize(previous_summary: str, segment: list[BaseMessage], target_chars: int) -> str:
        try:
            from services.llm_factory import create_chat_model
        except Exception:
            return _mechanical_summarizer(previous_summary, segment, target_chars)
        try:
            llm = create_chat_model(
                model=target_model,
                temperature=0.2,
                max_tokens=max_output_tokens,
                timeout=60,
            )
        except Exception:
            return _mechanical_summarizer(previous_summary, segment, target_chars)

        prev_block = previous_summary.strip()
        if prev_block.startswith(_SUMMARY_HEADER):
            prev_block = "\n".join(prev_block.splitlines()[1:]).strip()
        transcript = _render_segment_for_prompt(segment)
        instruction = (
            "你是对话压缩助手。请把下面这段早期对话压缩为一段中文摘要，"
            "保留：核心任务/目标、用户的明确决定与偏好、已完成的步骤、"
            "工具调用得到的关键结果、未解决的问题。"
            "丢弃：寒暄、过程性试错的中间步骤、相同内容的重复表达。"
            f"摘要总长度严格不超过 {target_chars} 字符，使用中文，"
            "条目化（每行 '- 主题: 内容'），不要包含原文长引用，不要重复同一信息。"
        )
        if prev_block:
            instruction += (
                "\n\n下面给出『先前摘要』和『新对话段』。你需要把两者综合成"
                "一份新的统一摘要（不是拼接，要去重、合并、更新最新结论）。"
            )
        user_payload = ""
        if prev_block:
            user_payload += f"【先前摘要】\n{prev_block}\n\n"
        user_payload += f"【新对话段】\n{transcript}"
        try:
            response = llm.invoke([
                SystemMessage(content=instruction),
                HumanMessage(content=user_payload),
            ])
        except Exception:
            return _mechanical_summarizer(previous_summary, segment, target_chars)
        text = _stringify(getattr(response, "content", "")).strip()
        if not text:
            return _mechanical_summarizer(previous_summary, segment, target_chars)
        if not text.startswith(_SUMMARY_HEADER):
            text = f"{_SUMMARY_HEADER}\n{text}"
        return text  # apply_compression enforces the hard cap

    return _summarize


# ---------------------------------------------------------------------------
# Record loading / view construction
# ---------------------------------------------------------------------------

def _valid_record(
    record: Optional[ContextCompactionRecord],
    messages: list[BaseMessage],
) -> Optional[ContextCompactionRecord]:
    if record is None:
        return None
    if not record.summary.strip():
        return None
    if record.compacted_until <= 0:
        return None
    if record.compacted_until > len(messages):
        return None
    return record


def _build_view(
    record: Optional[ContextCompactionRecord],
    messages: list[BaseMessage],
) -> list[BaseMessage]:
    if record is None:
        return list(messages)
    return [_summary_to_message(record.summary)] + messages[record.compacted_until:]


# ---------------------------------------------------------------------------
# Public: static read-only view
# ---------------------------------------------------------------------------

def static_compression_view(
    *,
    user_id: str,
    session_id: str,
    messages: list[BaseMessage],
    checkpoint_store_path: Optional[str] = None,
) -> list[BaseMessage]:
    """Read-only reproduction of apply_compression's input view.

    No writes, no LLM calls — safe to invoke from idempotent endpoints
    (session_history, session_status). Returns ``messages`` unchanged
    when compression is disabled or no record exists.
    """
    if not _compression_enabled() or not user_id or not session_id or not messages:
        return list(messages)
    thread_id = f"{user_id}#{session_id}"
    record = _valid_record(get_context_compaction(checkpoint_store_path, thread_id), messages)
    return _build_view(record, messages)


# ---------------------------------------------------------------------------
# Public: new-input trimming (current turn only)
# ---------------------------------------------------------------------------

def trim_new_input_if_oversized(
    messages: list[BaseMessage],
    *,
    user_id: str,
    session_id: str,
) -> list[BaseMessage]:
    """If the *last* HumanMessage in ``messages`` is over the per-input
    character cap, replace it with a `saved_to` pointer + excerpt and
    persist the original text to disk. Only the last message is touched;
    historical HumanMessages are never modified here.
    """
    limit = _new_input_item_limit()
    if limit <= 0 or not messages:
        return messages
    last = messages[-1]
    if not isinstance(last, HumanMessage) or not isinstance(last.content, str):
        return messages
    raw = last.content
    if len(raw) <= limit:
        return messages
    try:
        from webot.context import _runtime_artifacts_enabled, _store_runtime_text
        from webot.runtime_store import create_runtime_artifact
    except Exception:
        return messages
    if not _runtime_artifacts_enabled():
        return messages
    excerpt = raw[:700]
    try:
        path = _store_runtime_text(
            user_id=user_id,
            session_id=session_id,
            bucket="webot_user_inputs",
            prefix="user-input",
            content=raw,
        )
    except Exception:
        return messages
    try:
        create_runtime_artifact(
            user_id=user_id,
            session_id=session_id,
            kind="user_input",
            title="oversized_user_input",
            path=str(path),
            summary=raw[:220],
            metadata={"original_chars": len(raw)},
        )
    except Exception:
        pass
    body = (
        "[User input budgeted]\n"
        f"saved_to={path}\n"
        f"original_chars={len(raw)}\n\n"
        f"{excerpt}"
    )
    return messages[:-1] + [HumanMessage(content=body)]


# ---------------------------------------------------------------------------
# Public: main compression entry point
# ---------------------------------------------------------------------------

@dataclass
class CompressionResult:
    view: list[BaseMessage]
    triggered: bool
    summary: str
    compacted_until: int
    reason: str
    view_tokens: int


def apply_compression(
    *,
    user_id: str,
    session_id: str,
    messages: list[BaseMessage],
    history_token_budget: int,
    checkpoint_store_path: Optional[str] = None,
    preserve_recent: Optional[int] = None,
    summarizer: Optional[SummarizerFn] = None,
) -> CompressionResult:
    """Single-pass compression: load summary, maybe extend it, return view.

    Returned ``view`` is the message list to send downstream. When triggered,
    side effects are:
      1. Call ``summarizer(previous_summary, segment, target_chars)`` to get
         the merged summary text.
      2. Truncate to the dynamic char cap if the LLM returned too much.
      3. Persist (summary, compacted_until) to sqlite via save_context_compaction.

    The full LangGraph state still holds every original message, so the
    folded segment is not duplicated to disk — call sites that need to audit
    "what went into this compression" can replay ``messages[current_until:boundary]``
    from langgraph using the stored ``compacted_until``.

    When not triggered, returns the current view unchanged and writes nothing.
    """
    preserve_recent_val = preserve_recent if preserve_recent is not None else _preserve_recent_default()
    if not _compression_enabled() or not user_id or not session_id or not messages:
        view = list(messages)
        return CompressionResult(
            view=view,
            triggered=False,
            summary="",
            compacted_until=0,
            reason="disabled" if not _compression_enabled() else "empty",
            view_tokens=estimate_messages_tokens(view),
        )

    thread_id = f"{user_id}#{session_id}"
    record = _valid_record(get_context_compaction(checkpoint_store_path, thread_id), messages)
    previous_summary = record.summary if record else ""
    current_until = record.compacted_until if record else 0
    view = _build_view(record, messages)
    view_tokens = estimate_messages_tokens(view)

    if history_token_budget <= 0:
        return CompressionResult(
            view=view,
            triggered=False,
            summary=previous_summary,
            compacted_until=current_until,
            reason="no_budget",
            view_tokens=view_tokens,
        )

    trigger_tokens = max(1, int(history_token_budget * _trigger_ratio()))
    target_tokens = max(1, int(history_token_budget * _target_ratio()))

    if view_tokens <= trigger_tokens:
        return CompressionResult(
            view=view,
            triggered=False,
            summary=previous_summary,
            compacted_until=current_until,
            reason="below_trigger",
            view_tokens=view_tokens,
        )

    boundary = _pick_boundary(
        messages,
        current_until=current_until,
        preserve_recent=preserve_recent_val,
        target_tokens=target_tokens,
    )
    new_count = boundary - current_until
    if boundary <= current_until or new_count < _min_new_messages():
        return CompressionResult(
            view=view,
            triggered=False,
            summary=previous_summary,
            compacted_until=current_until,
            reason="min_new_messages",
            view_tokens=view_tokens,
        )

    segment = messages[current_until:boundary]
    target_chars = _max_summary_chars(history_token_budget)
    summarize = summarizer or _mechanical_summarizer
    try:
        new_summary = summarize(previous_summary, segment, target_chars)
    except Exception:
        new_summary = _mechanical_summarizer(previous_summary, segment, target_chars)
    new_summary = _truncate_to_cap(new_summary, target_chars)
    try:
        save_context_compaction(
            checkpoint_store_path,
            thread_id,
            summary=new_summary,
            compacted_until=boundary,
            source_message_count=len(messages),
            summary_token_estimate=estimate_messages_tokens([_summary_to_message(new_summary)]),
            metadata={
                "trigger_tokens": trigger_tokens,
                "target_tokens": target_tokens,
                "preserve_recent": preserve_recent_val,
                "new_message_count": new_count,
            },
        )
    except Exception:
        pass
    new_view = [_summary_to_message(new_summary)] + messages[boundary:]
    return CompressionResult(
        view=new_view,
        triggered=True,
        summary=new_summary,
        compacted_until=boundary,
        reason="compressed",
        view_tokens=estimate_messages_tokens(new_view),
    )
