"""WeBot's run of one chat completion (the request itself is ``agents.openai``'s)."""

from dataclasses import dataclass
from typing import Any


@dataclass
class OpenAIExecutionContext:
    """One chat completion's turn: the session, its state input and how to answer."""
    user_id: str
    session_id: str
    thread_id: str
    config: dict
    user_input: dict
    model_name: str
    external_tool_names: set[str]
    thread_lock: Any
    max_tokens: int | None = None
