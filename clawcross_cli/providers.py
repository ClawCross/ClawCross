"""
LLM provider registry and model catalog for ClawCross.

Defines supported providers, their config keys, default base URLs,
and curated model lists used by the interactive /model and /provider
commands (both CLI and chatbot).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProviderInfo:
    slug: str
    label: str
    default_base_url: str
    models: list[str] = field(default_factory=list)
    description: str = ""
    api_mode: str = "chat"


PROVIDERS: dict[str, ProviderInfo] = {
    "openai": ProviderInfo(
        slug="openai",
        label="OpenAI",
        default_base_url="https://api.openai.com/v1",
        models=[
            "gpt-5.2",
            "gpt-5.1",
            "gpt-5",
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4o",
            "gpt-4o-mini",
            "o4-mini",
            "o3-mini",
        ],
        description="OpenAI official API",
    ),
    "anthropic": ProviderInfo(
        slug="anthropic",
        label="Anthropic",
        default_base_url="https://api.anthropic.com",
        api_mode="anthropic_messages",
        models=[
            "claude-sonnet-4-5-20250929",
            "claude-opus-4-5-20251101",
            "claude-haiku-4-5-20250501",
            "claude-sonnet-4-20250514",
            "claude-opus-4-1-20250805",
            "claude-opus-4-20250514",
            "claude-3-5-sonnet-20241022",
        ],
        description="Anthropic Claude API",
    ),
    "google": ProviderInfo(
        slug="google",
        label="Google Gemini",
        default_base_url="https://generativelanguage.googleapis.com/v1beta",
        api_mode="gemini",
        models=[
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
        ],
        description="Google Gemini API",
    ),
    "deepseek": ProviderInfo(
        slug="deepseek",
        label="DeepSeek",
        default_base_url="https://api.deepseek.com/v1",
        models=[
            "deepseek-chat",
            "deepseek-reasoner",
        ],
        description="DeepSeek official API",
    ),
    "ollama": ProviderInfo(
        slug="ollama",
        label="Ollama",
        default_base_url="http://localhost:11434/v1",
        models=[],
        description="Local Ollama — models discovered at runtime",
    ),
    "antigravity": ProviderInfo(
        slug="antigravity",
        label="AntiGravity",
        default_base_url="https://api.antigravity.dev/v1",
        models=[],
        description="AntiGravity managed API",
    ),
    "minimax": ProviderInfo(
        slug="minimax",
        label="MiniMax",
        default_base_url="https://api.minimaxi.com/v1",
        models=[
            "abab7",
            "abab6.5s",
        ],
        description="MiniMax official API",
    ),
}

PROVIDER_ALIASES: dict[str, str] = {
    "gpt": "openai",
    "chatgpt": "openai",
    "claude": "anthropic",
    "gemini": "google",
    "gemma": "google",
}

ENV_MODEL_KEY = "LLM_MODEL"
ENV_PROVIDER_KEY = "LLM_PROVIDER"
ENV_BASE_URL_KEY = "LLM_BASE_URL"
ENV_API_KEY = "LLM_API_KEY"


def resolve_provider(name: str) -> ProviderInfo | None:
    key = name.lower().strip()
    if key in PROVIDERS:
        return PROVIDERS[key]
    if key in PROVIDER_ALIASES:
        return PROVIDERS[PROVIDER_ALIASES[key]]
    return None


def list_providers() -> list[ProviderInfo]:
    return list(PROVIDERS.values())


def get_provider_slugs() -> list[str]:
    return list(PROVIDERS.keys())
