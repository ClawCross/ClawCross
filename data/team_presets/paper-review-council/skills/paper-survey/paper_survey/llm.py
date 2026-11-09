"""Shared LLM wrapper for prompt injection and usage accounting."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional

import paper_survey.config as config

logger = logging.getLogger(__name__)

_thread_local = threading.local()
_stats_lock = threading.Lock()
_stats: Dict[str, object] = {
    "calls": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "by_tag": {},
}


def _get_client():
    client = getattr(_thread_local, "client", None)
    if client is None:
        from openai import OpenAI

        client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
        _thread_local.client = client
    return client


def _merge_usage(tag: str, usage) -> None:
    if usage is None:
        with _stats_lock:
            _stats["calls"] += 1
            by_tag = _stats["by_tag"]
            if tag not in by_tag:
                by_tag[tag] = {
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                }
            by_tag[tag]["calls"] += 1
        return

    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", prompt_tokens + completion_tokens) or 0)

    with _stats_lock:
        _stats["calls"] += 1
        _stats["prompt_tokens"] += prompt_tokens
        _stats["completion_tokens"] += completion_tokens
        _stats["total_tokens"] += total_tokens

        by_tag = _stats["by_tag"]
        if tag not in by_tag:
            by_tag[tag] = {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }
        by_tag[tag]["calls"] += 1
        by_tag[tag]["prompt_tokens"] += prompt_tokens
        by_tag[tag]["completion_tokens"] += completion_tokens
        by_tag[tag]["total_tokens"] += total_tokens


def get_llm_stats() -> Dict[str, object]:
    with _stats_lock:
        by_tag = {
            key: dict(value)
            for key, value in _stats["by_tag"].items()
        }
        return {
            "calls": _stats["calls"],
            "prompt_tokens": _stats["prompt_tokens"],
            "completion_tokens": _stats["completion_tokens"],
            "total_tokens": _stats["total_tokens"],
            "by_tag": by_tag,
        }


def reset_llm_stats() -> None:
    with _stats_lock:
        _stats["calls"] = 0
        _stats["prompt_tokens"] = 0
        _stats["completion_tokens"] = 0
        _stats["total_tokens"] = 0
        _stats["by_tag"] = {}


def _find_clawcross_root() -> Path | None:
    """Find the ClawCross project root from a nested team skill path."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "oasis" / "agent_center.py").is_file() and (parent / "src").is_dir():
            return parent
    return None


def _load_clawcross_env(root: Path) -> None:
    env_path = root / "config" / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=env_path, override=False)
    except Exception:
        # Minimal fallback when python-dotenv is unavailable.
        import os

        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip('"').strip("'")


def _messages_to_prompt(messages: List[Dict[str, str]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role") or "user").strip() or "user"
        content = str(message.get("content") or "")
        if not content:
            continue
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts).strip()


async def _send_to_clawcross_persona_async(
    *,
    prompt: str,
    tag: str,
    temperature: float,
    metadata: Optional[Dict[str, str]],
) -> str:
    root = _find_clawcross_root()
    if root is None:
        raise RuntimeError("Cannot locate ClawCross project root for persona backend")
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    _load_clawcross_env(root)

    from oasis.agent_center import send_team_persona

    options = {"temperature": temperature}
    if metadata:
        options["metadata"] = metadata
    result = await send_team_persona(
        str(config.CLAWCROSS_USER_ID or "default"),
        str(config.CLAWCROSS_TEAM or ""),
        str(config.CLAWCROSS_PERSONA_TAG or "paper_reporter"),
        prompt,
        options=options,
    )
    if not getattr(result, "ok", False):
        raise RuntimeError(getattr(result, "error", "") or "send_persona failed")
    content = getattr(result, "content", "") or ""
    if not content:
        raise RuntimeError("send_persona returned empty content")
    logger.debug("send_to_llm tag=%s used ClawCross persona backend", tag)
    return content


def _run_async_blocking(coro, *, timeout: float | None = None):
    """Run a coroutine from sync code with a hard caller-side timeout."""
    box: dict[str, object] = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        raise TimeoutError(f"ClawCross persona backend timed out after {timeout} seconds")
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box.get("value")


def _send_to_clawcross_persona(
    *,
    messages: List[Dict[str, str]],
    tag: str,
    temperature: float,
    metadata: Optional[Dict[str, str]],
) -> str:
    prompt = _messages_to_prompt(messages)
    if not prompt:
        raise ValueError("persona prompt is empty")
    return _run_async_blocking(
        _send_to_clawcross_persona_async(
            prompt=prompt,
            tag=tag,
            temperature=temperature,
            metadata=metadata,
        ),
        timeout=float(config.CLAWCROSS_PERSONA_TIMEOUT or 120),
    )


def send_to_llm(
    *,
    messages: List[Dict[str, str]],
    tag: str,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    metadata: Optional[Dict[str, str]] = None,
    prepend_system_prompt: str = "",
    append_user_suffix: str = "",
) -> str:
    """Central wrapper for all LLM calls in the packaged code path."""
    if not messages:
        raise ValueError("messages must not be empty")

    final_messages = [dict(message) for message in messages]

    if prepend_system_prompt:
        if final_messages[0].get("role") == "system":
            final_messages[0]["content"] = prepend_system_prompt + "\n\n" + final_messages[0]["content"]
        else:
            final_messages.insert(0, {"role": "system", "content": prepend_system_prompt})

    if append_user_suffix:
        for index in range(len(final_messages) - 1, -1, -1):
            if final_messages[index].get("role") == "user":
                final_messages[index]["content"] = final_messages[index]["content"] + "\n\n" + append_user_suffix
                break

    if config.CLAWCROSS_PERSONA_ENABLED:
        try:
            content = _send_to_clawcross_persona(
                messages=final_messages,
                tag=tag,
                temperature=temperature,
                metadata=metadata,
            )
            _merge_usage(f"clawcross_persona:{tag}", None)
            return content
        except Exception as exc:
            logger.warning("ClawCross persona backend failed for tag=%s: %s", tag, exc)
            if not config.CLAWCROSS_FALLBACK_TO_OPENAI or not config.LLM_API_KEY:
                raise

    client = _get_client()
    request_kwargs = {
        "model": config.LLM_MODEL,
        "messages": final_messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        request_kwargs["max_tokens"] = max_tokens
    if metadata:
        logger.debug("send_to_llm tag=%s metadata=%s", tag, metadata)

    response = client.chat.completions.create(**request_kwargs)
    _merge_usage(tag, getattr(response, "usage", None))
    content = response.choices[0].message.content or ""
    return content
