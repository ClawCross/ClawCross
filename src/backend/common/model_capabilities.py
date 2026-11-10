"""Local model capabilities: installed LangChain profiles, then a fixed catalog.

No network access. Unknown models stay unknown; names are not capability claims.
"""
from __future__ import annotations

import importlib
import json
from functools import lru_cache
from pathlib import Path

_PACKAGES = {"openai": "langchain_openai", "anthropic": "langchain_anthropic",
             "google": "langchain_google_genai", "deepseek": "langchain_deepseek"}
_ALIASES = {"gemini": "google", "claude": "anthropic"}
# The installed SDK/catalog omits these documented Chat Completions options.
# https://api-docs.deepseek.com/api/create-chat-completion/
_DEEPSEEK_EFFORT_MODELS = frozenset({'deepseek-flash', 'deepseek-v4-pro'})


@lru_cache(maxsize=1)
def _catalog() -> dict:
    data = json.loads(Path(__file__).with_name("model_catalog.json").read_text(encoding="utf-8"))
    return {(m["provider"].lower(), m["id"].lower()): m for m in data["models"]}


@lru_cache(maxsize=8)
def _profiles(provider: str) -> dict:
    package = _PACKAGES.get(provider)
    if not package:
        return {}
    try:
        return importlib.import_module(package + ".data._profiles")._PROFILES
    except (ImportError, AttributeError):
        return {}


def catalog_model(model: str, provider: str = "") -> dict:
    provider = _ALIASES.get(provider.lower(), provider.lower())
    name = model.lower().removeprefix("models/")
    entries = _catalog()
    if (provider, name) in entries:
        return entries[provider, name]
    if "/" in name:
        vendor, short = name.split("/", 1)
        vendor = _ALIASES.get(vendor, vendor)
        if (vendor, short) in entries:
            return entries[vendor, short]
        if (provider, short) in entries:
            return entries[provider, short]
        name = short
    matches = [row for (_, mid), row in entries.items() if mid == name]
    # Prefer the vendor's own entry over resellers of the same model.
    for vendor in _PACKAGES:
        if (vendor, name) in entries:
            return entries[vendor, name]
    return matches[0] if matches else {}


def model_capabilities(model: str, provider: str = "", profile: dict | None = None) -> dict:
    row = catalog_model(model, provider)
    vendor = _ALIASES.get(provider.lower(), provider.lower()) or row.get("provider", "")
    name = model.removeprefix("models/")
    profiles = _profiles(vendor)
    resolved = profile if profile is not None else profiles.get(name, profiles.get(name.split("/", 1)[-1], {}))
    result = {"model": model, "source": "catalog" if row else "unknown"}
    if row:
        result.update(image_inputs="image" in row.get("input", []),
                      reasoning_output=row.get("reasoning", False),
                      max_input_tokens=row.get("contextWindow"), max_output_tokens=row.get("maxTokens"),
                      pricing=row.get("pricing", {}), image=row.get("mediaInput", {}).get("image", {}))
    if resolved:
        result.update(resolved)
        result["source"] = "langchain"
    levels = resolved.get("reasoning_effort_levels") or []
    if not levels and vendor in {"openai", "deepseek"}:
        levels = row.get("compat", {}).get("supportedReasoningEfforts", [])
    if vendor == 'deepseek' and name.split('/', 1)[-1].lower() in _DEEPSEEK_EFFORT_MODELS:
        levels = ['none', 'low', 'high', 'max']
        result['reasoning_effort_default'] = 'high'
        result['reasoning_effort_source'] = 'https://api-docs.deepseek.com/api/create-chat-completion/'
    result["reasoning_effort_levels"] = [v for v in levels if isinstance(v, str)]
    from common.reasoning_levels import level_map
    result['reasoning_level_map'] = level_map(result['reasoning_effort_levels'])
    return result


def reasoning_effort(model: str, provider: str, requested: str, *, level: int = 0) -> str | None:
    """Only send a parameter supported by this model and adapter."""
    caps = model_capabilities(model, provider)
    levels = caps["reasoning_effort_levels"]
    if level:
        from common.reasoning_levels import mapped_effort
        return mapped_effort(levels, level)
    if requested and requested in levels:
        return requested
    return None
