"""
Streaming Tool Executor – Claude Code style edge-producing tool execution.

Features:
- Streams tool results as they become available (yield partial results)
- Supports concurrent execution of read-only tools with serial writes
- Token budget tracking per tool result with auto-truncation
- Partial result accumulator for long-running tools

Ported from Claude Code's streaming tool execution pattern.
"""

from __future__ import annotations

import asyncio
import time
import utils.scheduler_service
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable, Awaitable

from langchain_core.messages import ToolMessage


class ToolAccessMode(str, Enum):
    """Tool concurrency classification."""
    READ_ONLY = "read_only"
    WRITE = "write"
    UNKNOWN = "unknown"


# Default classification of known tools
_TOOL_ACCESS_MODES: dict[str, ToolAccessMode] = {
    # Read-only tools
    "read_file": ToolAccessMode.READ_ONLY,
    "list_files": ToolAccessMode.READ_ONLY,
    "search_files": ToolAccessMode.READ_ONLY,
    "grep_search": ToolAccessMode.READ_ONLY,
    "list_sessions": ToolAccessMode.READ_ONLY,
    "get_current_session": ToolAccessMode.READ_ONLY,
    "get_current_time": ToolAccessMode.READ_ONLY,
    "list_alarms": ToolAccessMode.READ_ONLY,
    "get_telegram_status": ToolAccessMode.READ_ONLY,
    "list_oasis_topics": ToolAccessMode.READ_ONLY,
    "list_oasis_sessions": ToolAccessMode.READ_ONLY,
    "list_oasis_experts": ToolAccessMode.READ_ONLY,
    "list_oasis_workflows": ToolAccessMode.READ_ONLY,
    "list_subagents": ToolAccessMode.READ_ONLY,
    "get_subagent_history": ToolAccessMode.READ_ONLY,
    "list_webot_agent_profiles": ToolAccessMode.READ_ONLY,
    "session_inbox": ToolAccessMode.READ_ONLY,
    "claude_session_inbox": ToolAccessMode.READ_ONLY,
    "get_session_mode": ToolAccessMode.READ_ONLY,
    "ultraplan_status": ToolAccessMode.READ_ONLY,
    "ultrareview_status": ToolAccessMode.READ_ONLY,
    "list_verifications": ToolAccessMode.READ_ONLY,
    "list_tool_approvals": ToolAccessMode.READ_ONLY,
    "call_llm_api": ToolAccessMode.READ_ONLY,
    "skill_view": ToolAccessMode.READ_ONLY,
    "skill_list": ToolAccessMode.READ_ONLY,
    "skill_evolution_report": ToolAccessMode.READ_ONLY,
    "search_sessions": ToolAccessMode.READ_ONLY,
    "get_insights": ToolAccessMode.READ_ONLY,
    "get_trajectory_stats": ToolAccessMode.READ_ONLY,
    # Write tools
    "write_file": ToolAccessMode.WRITE,
    "append_file": ToolAccessMode.WRITE,
    "delete_file": ToolAccessMode.WRITE,
    "run_command": ToolAccessMode.WRITE,
    "run_python_code": ToolAccessMode.WRITE,
    "start_background_command": ToolAccessMode.WRITE,
    "get_background_command_status": ToolAccessMode.READ_ONLY,
    "read_background_command_output": ToolAccessMode.READ_ONLY,
    "cancel_background_command": ToolAccessMode.WRITE,
    "start_new_oasis": ToolAccessMode.WRITE,
    "cancel_oasis_discussion": ToolAccessMode.WRITE,
    "set_oasis_workflow": ToolAccessMode.WRITE,
    "add_oasis_expert": ToolAccessMode.WRITE,
    "update_oasis_expert": ToolAccessMode.WRITE,
    "delete_oasis_expert": ToolAccessMode.WRITE,
    "spawn_subagent": ToolAccessMode.WRITE,
    "cancel_subagent": ToolAccessMode.WRITE,
    "delete_subagent": ToolAccessMode.WRITE,
    "add_alarm": ToolAccessMode.WRITE,
    "delete_alarm": ToolAccessMode.WRITE,
    "set_telegram_chat_id": ToolAccessMode.WRITE,
    "send_telegram_message": ToolAccessMode.WRITE,
    "remove_telegram_config": ToolAccessMode.WRITE,
    "send_internal_message": ToolAccessMode.WRITE,
    "send_to_group": ToolAccessMode.WRITE,
    "send_private_cli": ToolAccessMode.WRITE,
    "enter_plan_mode": ToolAccessMode.WRITE,
    "exit_plan_mode": ToolAccessMode.WRITE,
    "skill_manage": ToolAccessMode.WRITE,
    "skill_evolution_apply": ToolAccessMode.WRITE,
    "manage_personality": ToolAccessMode.WRITE,
}


def classify_tool_access(tool_name: str) -> ToolAccessMode:
    """Classify a tool's access mode for concurrency control."""
    return _TOOL_ACCESS_MODES.get(tool_name, ToolAccessMode.UNKNOWN)


def register_tool_access_mode(tool_name: str, mode: ToolAccessMode) -> None:
    """Register or override a tool's access classification at runtime."""
    _TOOL_ACCESS_MODES[tool_name] = mode


@dataclass
class ToolExecutionResult:
    """Result of a single tool execution with metadata."""
    tool_call_id: str
    tool_name: str
    content: str
    success: bool = True
    duration_ms: float = 0.0
    truncated: bool = False
    original_length: int = 0


@dataclass
class StreamingToolExecutor:
    """
    Executes tool calls with streaming support and concurrency control.

    - Read-only tools run concurrently
    - Write tools run serially (with a write lock)
    - Results are yielded as they complete
    - Token budget enforcement with auto-truncation
    """

    max_concurrent_reads: int = 8
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    read_semaphore: asyncio.Semaphore = field(init=False)
    result_char_budget: int = 6000
    per_tool_timeout: float = 120.0  # Per-tool timeout in seconds
    on_progress: Callable[[str, str, float], None] | None = None  # (tool_name, status, pct)
    _active_count: int = field(default=0, init=False)

    def __post_init__(self):
        self.read_semaphore = asyncio.Semaphore(self.max_concurrent_reads)

    async def execute_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        tool_executor: Callable[[dict[str, Any]], Awaitable[str]],
    ) -> AsyncIterator[ToolExecutionResult]:
        """
        Execute multiple tool calls with concurrency control.

        Read-only tools execute concurrently; write tools execute serially.
        Results are yielded as each tool completes.
        """
        if not tool_calls:
            return

        # Partition into read-only and write groups
        read_calls = []
        write_calls = []

        for tc in tool_calls:
            mode = classify_tool_access(tc.get("name", ""))
            if mode == ToolAccessMode.READ_ONLY:
                read_calls.append(tc)
            else:
                write_calls.append(tc)

        result_queue: asyncio.Queue[ToolExecutionResult | None] = asyncio.Queue()
        pending_count = len(tool_calls)

        async def _execute_single(tc: dict, is_write: bool = False) -> None:
            nonlocal pending_count
            start_time = time.monotonic()
            tool_name = tc.get("name", "unknown")
            tool_call_id = tc.get("id", "unknown")

            try:
                if is_write:
                    async with self.write_lock:
                        raw_result = await asyncio.wait_for(
                            tool_executor(tc), timeout=self.per_tool_timeout
                        )
                else:
                    async with self.read_semaphore:
                        raw_result = await asyncio.wait_for(
                            tool_executor(tc), timeout=self.per_tool_timeout
                        )

                if self.on_progress:
                    try:
                        self.on_progress(tool_name, "completed", 1.0)
                    except Exception:
                        pass

                duration_ms = (time.monotonic() - start_time) * 1000
                content = str(raw_result) if raw_result is not None else ""
                original_length = len(content)
                truncated = False

                if len(content) > self.result_char_budget:
                    # Truncate with head+tail strategy
                    head = max(120, self.result_char_budget // 2)
                    tail = max(80, self.result_char_budget - head - 60)
                    content = (
                        content[:head]
                        + f"\n\n... [truncated, original {original_length} chars] ...\n\n"
                        + content[-tail:]
                    )
                    truncated = True

                result = ToolExecutionResult(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    content=content,
                    success=True,
                    duration_ms=duration_ms,
                    truncated=truncated,
                    original_length=original_length,
                )
            except Exception as exc:
                duration_ms = (time.monotonic() - start_time) * 1000
                result = ToolExecutionResult(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    content=f"Error executing {tool_name}: {exc}",
                    success=False,
                    duration_ms=duration_ms,
                )

            await result_queue.put(result)
            pending_count -= 1
            if pending_count <= 0:
                await result_queue.put(None)  # Signal completion

        # Launch all reads concurrently
        tasks = []
        for tc in read_calls:
            tasks.append(asyncio.create_task(_execute_single(tc, is_write=False)))

        # Launch writes serially (they share the write_lock)
        for tc in write_calls:
            tasks.append(asyncio.create_task(_execute_single(tc, is_write=True)))

        # Yield results as they arrive
        while True:
            result = await result_queue.get()
            if result is None:
                break
            yield result

        # Ensure all tasks complete
        for task in tasks:
            if not task.done():
                await task

    def to_tool_messages(self, results: list[ToolExecutionResult]) -> list[ToolMessage]:
        """Convert execution results to LangChain ToolMessages."""
        messages = []
        for result in results:
            messages.append(
                ToolMessage(
                    content=result.content,
                    tool_call_id=result.tool_call_id,
                    name=result.tool_name,
                )
            )
        return messages


# Module-level singleton for shared use
_default_executor: StreamingToolExecutor | None = None


def get_streaming_executor() -> StreamingToolExecutor:
    """Get or create the module-level streaming tool executor."""
    global _default_executor
    if _default_executor is None:
        _default_executor = StreamingToolExecutor()
    return _default_executor
