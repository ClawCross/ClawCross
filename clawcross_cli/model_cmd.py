"""
Model and provider selection commands for ClawCross.

Exposes two high-level functions consumed by both the CLI and chatbot:
- select_model   -> interactive model picker across all providers
- select_provider -> interactive provider picker with optional base_url
- apply_model    -> write LLM_MODEL to config/.env
- apply_provider -> write LLM_PROVIDER + LLM_BASE_URL to config/.env

Also provides non-interactive direct-set helpers for scripted / one-shot use.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Callable

from clawcross_cli.providers import (
    ENV_BASE_URL_KEY,
    ENV_MODEL_KEY,
    ENV_PROVIDER_KEY,
    PROVIDERS,
    ProviderInfo,
    list_providers,
    resolve_provider,
)


# ---------------------------------------------------------------------------
# .env I/O (mirrors scripts/clawcross.py helpers but self-contained)
# ---------------------------------------------------------------------------

def _find_env_file() -> Path:
    """Locate config/.env using standard ClawCross home resolution."""
    home = Path(os.environ.get("CLAWCROSS_HOME", Path.home() / ".clawcross"))
    return home / "config" / ".env"


def _read_env() -> dict[str, str]:
    path = _find_env_file()
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
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        if k:
            values[k] = v
    return values


def _write_env(updates: dict[str, str]) -> None:
    path = _find_env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_env()
    existing.update(updates)
    lines = [f"{k}={_quote(v)}" for k, v in existing.items()]
    path.write_text("\n".join(lines) + "\n", "utf-8")


def _quote(v: str) -> str:
    v = str(v)
    if not v or any(c.isspace() for c in v) or any(c in v for c in "#'\""):
        import json

        return json.dumps(v, ensure_ascii=False)
    return v


# ---------------------------------------------------------------------------
# Non-interactive setters
# ---------------------------------------------------------------------------

def set_model(model: str) -> None:
    """Write LLM_MODEL to .env (no provider change)."""
    model = model.strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-.:/]+", model):
        raise ValueError(f"invalid model name: {model!r}")
    _write_env({ENV_MODEL_KEY: model})


def set_provider(provider_slug: str, base_url: str | None = None) -> None:
    """Write LLM_PROVIDER (and optionally LLM_BASE_URL) to .env."""
    info = resolve_provider(provider_slug)
    if info is None:
        valid = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"unknown provider: {provider_slug!r}. Valid: {valid}")
    updates: dict[str, str] = {ENV_PROVIDER_KEY: info.slug}
    if base_url is not None:
        updates[ENV_BASE_URL_KEY] = base_url
    else:
        updates[ENV_BASE_URL_KEY] = info.default_base_url
    _write_env(updates)


# ---------------------------------------------------------------------------
# Current state queries
# ---------------------------------------------------------------------------

def current_model() -> str:
    """Return the currently configured model string."""
    return _read_env().get(ENV_MODEL_KEY, "")


def current_provider() -> str:
    """Return the currently configured provider slug."""
    return _read_env().get(ENV_PROVIDER_KEY, "openai")


def current_base_url() -> str:
    """Return the currently configured base URL."""
    return _read_env().get(ENV_BASE_URL_KEY, "")


# ---------------------------------------------------------------------------
# Interactive selection (console-based)
# ---------------------------------------------------------------------------

def _pick_from(items: list[str], prompt: str, prompt_fn: Callable[[], str] | None = None) -> str | None:
    """Simple numbered picker. Returns selected item or None if cancelled."""
    if not items:
        return None
    for i, name in enumerate(items, 1):
        print(f"  {i:>2}) {name}")
    sel = (prompt_fn and prompt_fn()) or input(prompt).strip()
    if not sel:
        return None
    try:
        idx = int(sel) - 1
        if 0 <= idx < len(items):
            return items[idx]
    except ValueError:
        if sel in items:
            return sel
    return None


def select_provider(prompt_text: str = "Select provider number (or name): ") -> ProviderInfo | None:
    """Interactive provider picker. Prints a menu and returns the selection."""
    providers = list_providers()
    if not providers:
        print("No providers configured.", file=sys.stderr)
        return None
    print("\nProviders:")
    labels = [f"{p.slug:<14} {p.label}  — {p.description}" for p in providers]
    pick = _pick_from(labels, "\n" + prompt_text)
    if pick is None:
        return None
    slug = pick.split()[0]
    return resolve_provider(slug)


def select_model(
    provider_slug: str | None = None,
    prompt_text: str = "Select model number (or name): ",
) -> str | None:
    """Interactive model picker for a given provider (or all providers if None)."""
    if provider_slug is not None:
        info = resolve_provider(provider_slug)
        if info is None:
            print(f"Unknown provider: {provider_slug}", file=sys.stderr)
            return None
        providers = [info]
    else:
        providers = list_providers()

    if not any(p.models for p in providers):
        print("No models available for the selected provider(s).", file=sys.stderr)
        return None

    labels: list[str] = []
    model_map: dict[str, tuple[str, str]] = {}  # label -> (provider_slug, model_name)
    for provider in providers:
        for m in provider.models:
            l = f"{m:<36} [{provider.slug}]"
            labels.append(l)
            model_map[l] = (provider.slug, m)

    print("\nModels:")
    pick = _pick_from(labels, "\n" + prompt_text)
    if pick is None:
        return None
    _provider, model = model_map[pick]
    return model


def apply_model_interactive(model: str | None = None) -> str:
    """Full flow: select and persist a model.

    If *model* is given, set it directly. Otherwise enter interactive picker.
    Returns the selected model name.
    """
    if model is not None:
        m = model.strip()
        set_model(m)
        print(f"LLM_MODEL={m}")
        return m

    chosen = select_model()
    if chosen is None:
        print("Model selection cancelled.", file=sys.stderr)
        return current_model()

    set_model(chosen)
    print(f"LLM_MODEL={chosen}")
    return chosen


def apply_provider_interactive(provider_slug: str | None = None, base_url: str | None = None) -> str:
    """Full flow: select and persist a provider.

    If *provider_slug* is given, set it directly. Otherwise enter interactive
    picker. Returns the selected provider slug.
    """
    if provider_slug is not None:
        set_provider(provider_slug, base_url)
        info = resolve_provider(provider_slug)
        if info is not None:
            print(f"LLM_PROVIDER={info.slug}")
            print(f"LLM_BASE_URL={base_url or info.default_base_url}")
        return provider_slug

    chosen = select_provider()
    if chosen is None:
        print("Provider selection cancelled.", file=sys.stderr)
        return current_provider()

    print(f"\nDefault base URL: {chosen.default_base_url}")
    custom = input("Base URL (enter to use default): ").strip()
    url = custom if custom else chosen.default_base_url

    set_provider(chosen.slug, url)
    print(f"LLM_PROVIDER={chosen.slug}")
    print(f"LLM_BASE_URL={url}")
    return chosen.slug
