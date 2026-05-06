from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SendToAgentRequest:
    prompt: Any
    connect_type: str
    platform: str
    session: str | None = None
    options: dict[str, Any] | None = None


@dataclass(slots=True)
class SendToAgentResult:
    ok: bool
    content: str | None = None
    raw_response: Any | None = None
    error: str | None = None
    meta: dict[str, Any] | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


@dataclass(slots=True)
class PreparedAgentStream:
    connect_type: str
    platform: str
    session: str | None
    timeout_sec: int | None
    cmd: list[str] | None = None
    temp_path: str | None = None
    adapter: Any | None = None
    api_url: str | None = None
    headers: dict[str, Any] | None = None
    body: dict[str, Any] | None = None
    history_request_id: str | None = None
    history_options: dict[str, Any] | None = None

    def cleanup(self) -> None:
        if self.temp_path:
            with contextlib.suppress(Exception):
                os.unlink(self.temp_path)


@dataclass(slots=True)
class ResetAgentRequest:
    connect_type: str
    platform: str
    session: str | None = None
    options: dict[str, Any] | None = None


@dataclass(slots=True)
class ResetAgentResult:
    ok: bool
    error: str | None = None
    meta: dict[str, Any] | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)
