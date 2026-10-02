"""Validated, atomic user/session settings for context and approval review."""

from __future__ import annotations

import json
import os
import tempfile
from functools import wraps
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from common.runtime_paths import USER_FILES_DIR


class ContextSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    auto_compact: bool = True
    context_window_tokens: int = Field(default=1_000_000, ge=4096, le=4_000_000)
    history_tokens: int = Field(default=0, ge=0, le=4_000_000)
    trigger_tokens: int = Field(default=0, ge=0, le=4_000_000)
    target_tokens: int = Field(default=0, ge=0, le=4_000_000)
    preserve_recent_turns: int = Field(default=4, ge=1, le=100)
    summary_tokens: int = Field(default=2000, ge=128, le=32000)
    summarizer_input_tokens: int = Field(default=8000, ge=1024, le=128000)
    summarizer_model: str = Field(default="", max_length=200)
    preserve_instructions: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def validate_budgets(self):
        from webot.context_compressor import _approx_tokens
        if self.trigger_tokens and self.target_tokens >= self.trigger_tokens:
            raise ValueError("target_tokens must be smaller than trigger_tokens")
        if self.history_tokens and self.trigger_tokens > self.history_tokens:
            raise ValueError("trigger_tokens must not exceed history_tokens")
        if self.target_tokens and self.summary_tokens >= self.target_tokens:
            raise ValueError("summary_tokens must be smaller than target_tokens")
        if self.summarizer_input_tokens <= self.summary_tokens + _approx_tokens(self.preserve_instructions) + 512:
            raise ValueError("summarizer_input_tokens must leave room for the previous summary and instructions")
        return self


class ApprovalSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["chat", "readonly", "bypass", "auto"] = "auto"
    approvals_reviewer: Literal["user", "auto_review"] = "user"
    reviewer_model: str = Field(default="", max_length=200)
    reviewer_policy: str = Field(default="", max_length=4000)
    reviewer_timeout_seconds: int = Field(default=30, ge=5, le=120)
    reviewer_max_tokens: int = Field(default=4096, ge=1024, le=16384)
    command_sandbox: Literal["off", "srt", "auto", "landlock"] = "off"

    @model_validator(mode="before")
    @classmethod
    def migrate_container_setting(cls, value):
        if isinstance(value, dict) and value.get("command_sandbox") == "container":
            return {**value, "command_sandbox": "srt"}
        return value


def resolve_context_window(settings: ContextSettings, model: str | None = None) -> int:
    """The user's configured capacity is authoritative; model names are not a cap."""
    return settings.context_window_tokens


def resolve_context_history_budget(settings: ContextSettings, *, is_subagent: bool = False,
                                   model: str | None = None, prefix_tokens: int = 0,
                                   output_reserve: int = 2048) -> int:
    from webot.context_limits import resolve_history_token_budget
    window = resolve_context_window(settings, model)
    available = window - prefix_tokens - output_reserve
    if available < 512:
        raise ValueError("System prompt and tools leave insufficient history space in the configured context window")
    history = settings.history_tokens or resolve_history_token_budget(
        is_subagent=is_subagent, model=model, context_window=window,
    )
    return min(history, available)


def context_usage_with_window(usage: dict, window: int) -> dict:
    """Refresh the denominator without replacing measured tokens or breakdown."""
    tokens = int(usage.get("tokens", 0) or 0)
    percent = min(100, round(tokens / window * 100)) if window else 0
    return {**usage, "budget": window, "percent": max(1, percent) if tokens else 0,
            "remaining": max(0, window - tokens)}


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    context: ContextSettings = Field(default_factory=ContextSettings)
    approval: ApprovalSettings = Field(default_factory=ApprovalSettings)


def settings_path(user_id: str) -> Path:
    if not user_id or user_id in {".", ".."} or Path(user_id).name != user_id or "\\" in user_id:
        raise ValueError("Invalid user ID")
    root = USER_FILES_DIR.resolve()
    path = (root / user_id / "webot_runtime_settings.json").resolve()
    if not user_id or not path.is_relative_to(root) or path.parent == root:
        raise ValueError("Invalid user ID")
    return path


def _load(user_id: str) -> dict:
    path = settings_path(user_id)
    if not path.exists():
        return {"user": {}, "sessions": {}}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("user", {}), dict) or not isinstance(raw.get("sessions", {}), dict):
        raise ValueError("Invalid runtime settings file")
    if set(raw) - {"user", "sessions"} or any(not isinstance(value, dict) for value in raw.get("sessions", {}).values()):
        raise ValueError("Invalid runtime settings file")
    return raw


def _merge(base: dict, override: dict) -> dict:
    if any(key not in {"context", "approval"} or not isinstance(value, dict) for key, value in base.items()):
        raise ValueError("Invalid runtime settings section")
    result = {k: dict(v) for k, v in base.items()}
    for section, values in override.items():
        if section not in {"context", "approval"} or not isinstance(values, dict):
            raise ValueError("Invalid runtime settings section")
        result.setdefault(section, {}).update(values)
    return result


def _env_defaults() -> dict:
    context = {}
    for key, env in (
        ("summary_tokens", "WEBOT_COMPRESSION_SUMMARY_TOKENS"),
    ):
        try:
            value = int(os.getenv(env, "0"))
            if value > 0:
                context[key] = value
        except ValueError:
            pass
    context["auto_compact"] = os.getenv("WEBOT_COMPRESSION_DISABLED", "0").lower() in {"", "0", "false", "off", "no"}
    context["summarizer_model"] = os.getenv("WEBOT_SUMMARIZER_MODEL", "")
    return {"context": context, "approval": {}}


def get_runtime_settings(user_id: str, session_id: str = "") -> RuntimeSettings:
    data = _load(user_id)
    raw = _merge(_env_defaults(), data.get("user", {}))
    if session_id:
        raw = _merge(raw, data.get("sessions", {}).get(session_id, {}))
    return RuntimeSettings.model_validate(raw)


def runtime_settings_payload(user_id: str, session_id: str = "") -> dict:
    data = _load(user_id)
    return {
        "settings": get_runtime_settings(user_id, session_id).model_dump(),
        "user_overrides": data.get("user", {}),
        "session_overrides": data.get("sessions", {}).get(session_id, {}) if session_id else {},
    }


def _serialize_settings(fn):
    @wraps(fn)
    def locked(user_id: str, **kwargs):
        path = settings_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(".lock").open("a") as lock:
            if os.name == "nt":
                import msvcrt
                lock.write(" ")
                lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return fn(user_id, **kwargs)
            finally:
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)
    return locked


@_serialize_settings
def save_runtime_settings(user_id: str, *, settings: dict, session_id: str = "", reset: bool = False) -> dict:
    # A user update also validates every existing session against the new defaults.
    data = _load(user_id)
    data.setdefault("user", {})
    data.setdefault("sessions", {})
    if session_id:
        if reset:
            data["sessions"].pop(session_id, None)
        else:
            existing = data["sessions"].get(session_id, {})
            data["sessions"][session_id] = _merge(existing, settings)
    else:
        data["user"] = {} if reset else _merge(data["user"], settings)
    defaults = _merge(_env_defaults(), data["user"])
    RuntimeSettings.model_validate(defaults)
    for override in data["sessions"].values():
        RuntimeSettings.model_validate(_merge(defaults, override))
    path = settings_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".runtime-settings-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return runtime_settings_payload(user_id, session_id)
