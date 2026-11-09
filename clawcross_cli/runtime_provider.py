"""
Runtime LLM provider resolution for ClawCross.

Single entry point — ``resolve_active_profile()`` — used by
``src/services/llm_factory.py`` to obtain the model, provider, base URL,
and API key for the active LLM. All other callers (LangGraph, services)
keep consuming env vars; only the factory is rewired.

Resolution priority:

1. ``config/.env`` LLM_* keys (single-config mode)
2. ``models.json``'s active profile (multi-profile fallback)

(1) wins as long as ``.env`` has ``LLM_MODEL`` or ``LLM_API_KEY`` set —
since ``model use`` mirrors the active profile into ``.env`` anyway,
``.env`` is the authoritative "currently effective" config and
``models.json`` is just the profile catalog used for switching.

When (2) is resolved, the values are *also* exported into ``os.environ``
so legacy code paths reading ``LLM_API_KEY`` etc. directly keep working
without further changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from clawcross_cli import models_store
from clawcross_cli.providers import (
    ENV_API_KEY,
    ENV_BASE_URL_KEY,
    ENV_MODEL_KEY,
    ENV_PROVIDER_KEY,
    resolve_provider,
)


@dataclass
class RuntimeProfile:
    provider: str
    model: str
    base_url: str
    api_key: str
    api_mode: str
    source: str  # "models.json" | "env" | "empty"


def resolve_active_profile() -> RuntimeProfile:
    """Return the runtime LLM profile to use, in priority order."""
    env = _read_env()
    if env.get(ENV_MODEL_KEY) or env.get(ENV_API_KEY):
        provider_slug = env.get(ENV_PROVIDER_KEY, "")
        info = resolve_provider(provider_slug) if provider_slug else None
        api_mode = info.api_mode if info else "chat"
        return RuntimeProfile(
            provider=provider_slug,
            model=env.get(ENV_MODEL_KEY, ""),
            base_url=env.get(ENV_BASE_URL_KEY, info.default_base_url if info else ""),
            api_key=env.get(ENV_API_KEY, ""),
            api_mode=api_mode,
            source="env",
        )

    profile = models_store.get_active()
    if profile is not None:
        info = resolve_provider(profile.provider)
        base_url = profile.base_url or (info.default_base_url if info else "")
        api_mode = profile.api_mode or (info.api_mode if info else "chat")
        rt = RuntimeProfile(
            provider=profile.provider,
            model=profile.model,
            base_url=base_url,
            api_key=profile.auth.api_key,
            api_mode=api_mode,
            source="models.json",
        )
        _export_to_env(rt)
        return rt

    return RuntimeProfile(
        provider="", model="", base_url="", api_key="", api_mode="chat", source="empty"
    )


def _export_to_env(rt: RuntimeProfile) -> None:
    """Mirror the resolved profile into os.environ for legacy readers."""
    if rt.model:
        os.environ[ENV_MODEL_KEY] = rt.model
    if rt.provider:
        os.environ[ENV_PROVIDER_KEY] = rt.provider
    if rt.base_url:
        os.environ[ENV_BASE_URL_KEY] = rt.base_url
    if rt.api_key:
        os.environ[ENV_API_KEY] = rt.api_key


def _read_env() -> dict[str, str]:
    """Read config/.env directly (does not consult os.environ)."""
    home = Path(os.environ.get("CLAWCROSS_HOME", Path.home() / ".clawcross"))
    path = home / "config" / ".env"
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (
            v.startswith("'") and v.endswith("'")
        ):
            v = v[1:-1]
        if k:
            values[k] = v
    return values
