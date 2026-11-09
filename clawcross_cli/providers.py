"""
LLM provider registry and model catalog for ClawCross.

Defines supported providers, their default base URLs, and a curated model
catalog used by the interactive /model commands (both CLI and
chatbot).  Providers and models are copied from Hermes' PROVIDER_REGISTRY
and _PROVIDER_MODELS (api_key auth only) — keep in sync with
hermes_cli/auth.py and hermes_cli/models.py.
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
    api_mode: str = "chat"  # "chat" | "anthropic_messages" | "gemini"


# ---------------------------------------------------------------------------
# Provider catalog — copied verbatim from Hermes (api_key entries only).
#
# api_mode rules:
#   - inference_base_url contains "/anthropic"  -> "anthropic_messages"
#   - slug == "gemini"                          -> "gemini"
#   - otherwise                                 -> "chat"
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, ProviderInfo] = {
    "openai": ProviderInfo(
        slug="openai",
        label="OpenAI",
        default_base_url="https://api.openai.com/v1",
        api_mode="chat",
        description="OpenAI Chat Completions (api.openai.com)",
        models=[
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5-mini",
            "gpt-5.3-codex",
            "gpt-5.2-codex",
            "gpt-4.1",
            "gpt-4o",
            "gpt-4o-mini",
        ],
    ),
    "lmstudio": ProviderInfo(
        slug="lmstudio",
        label="LM Studio",
        default_base_url="http://127.0.0.1:1234/v1",
        api_mode="chat",
        description="Local LM Studio (OpenAI-compat)",
        models=[],
    ),
    "copilot": ProviderInfo(
        slug="copilot",
        label="GitHub Copilot",
        default_base_url="https://api.githubcopilot.com",
        api_mode="chat",
        description="GitHub Copilot Models",
        models=[
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5-mini",
            "gpt-5.3-codex",
            "gpt-5.2-codex",
            "gpt-4.1",
            "gpt-4o",
            "gpt-4o-mini",
            "claude-sonnet-4.6",
            "claude-sonnet-4",
            "claude-sonnet-4.5",
            "claude-haiku-4.5",
            "gemini-3.1-pro-preview",
            "gemini-3-pro-preview",
            "gemini-3-flash-preview",
            "gemini-2.5-pro",
        ],
    ),
    "gemini": ProviderInfo(
        slug="gemini",
        label="Google AI Studio",
        default_base_url="https://generativelanguage.googleapis.com/v1beta",
        api_mode="gemini",
        description="Google AI Studio (Gemini API)",
        models=[
            "gemini-3.1-pro-preview",
            "gemini-3-pro-preview",
            "gemini-3-flash-preview",
            "gemini-3.1-flash-lite-preview",
        ],
    ),
    "zai": ProviderInfo(
        slug="zai",
        label="Z.AI / GLM",
        default_base_url="https://api.z.ai/api/paas/v4",
        api_mode="chat",
        description="Z.AI / GLM (Zhipu)",
        models=[
            "glm-5.1",
            "glm-5",
            "glm-5v-turbo",
            "glm-5-turbo",
            "glm-4.7",
            "glm-4.5",
            "glm-4.5-flash",
        ],
    ),
    "kimi-coding": ProviderInfo(
        slug="kimi-coding",
        label="Kimi / Moonshot",
        default_base_url="https://api.moonshot.ai/v1",
        api_mode="chat",
        description="Kimi / Moonshot global",
        models=[
            "kimi-k2.6",
            "kimi-k2.5",
            "kimi-for-coding",
            "kimi-k2-thinking",
            "kimi-k2-thinking-turbo",
            "kimi-k2-turbo-preview",
            "kimi-k2-0905-preview",
        ],
    ),
    "kimi-coding-cn": ProviderInfo(
        slug="kimi-coding-cn",
        label="Kimi / Moonshot (China)",
        default_base_url="https://api.moonshot.cn/v1",
        api_mode="chat",
        description="Kimi / Moonshot China endpoint",
        models=[
            "kimi-k2.6",
            "kimi-k2.5",
            "kimi-k2-thinking",
            "kimi-k2-turbo-preview",
            "kimi-k2-0905-preview",
        ],
    ),
    "stepfun": ProviderInfo(
        slug="stepfun",
        label="StepFun",
        default_base_url="https://api.stepfun.com/v1",
        api_mode="chat",
        description="StepFun Step Plan",
        models=[
            "step-3.5-flash",
            "step-3.5-flash-2603",
        ],
    ),
    "arcee": ProviderInfo(
        slug="arcee",
        label="Arcee AI",
        default_base_url="https://api.arcee.ai/api/v1",
        api_mode="chat",
        description="Arcee AI",
        models=[
            "trinity-large-thinking",
            "trinity-large-preview",
            "trinity-mini",
        ],
    ),
    "gmi": ProviderInfo(
        slug="gmi",
        label="GMI Cloud",
        default_base_url="https://api.gmi-serving.com/v1",
        api_mode="chat",
        description="GMI Cloud (multi-model gateway)",
        models=[
            "zai-org/GLM-5.1-FP8",
            "deepseek-ai/DeepSeek-V3.2",
            "moonshotai/Kimi-K2.5",
            "google/gemini-3.1-flash-lite-preview",
            "anthropic/claude-sonnet-4.6",
            "openai/gpt-5.4",
        ],
    ),
    "minimax": ProviderInfo(
        slug="minimax",
        label="MiniMax",
        default_base_url="https://api.minimax.io/anthropic",
        api_mode="anthropic_messages",
        description="MiniMax (Anthropic Messages API)",
        models=[
            "MiniMax-M2.7",
            "MiniMax-M2.5",
            "MiniMax-M2.1",
            "MiniMax-M2",
        ],
    ),
    "anthropic": ProviderInfo(
        slug="anthropic",
        label="Anthropic",
        default_base_url="https://api.anthropic.com",
        api_mode="anthropic_messages",
        description="Anthropic Claude API",
        models=[
            "claude-opus-4-7",
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-opus-4-5-20251101",
            "claude-sonnet-4-5-20250929",
            "claude-opus-4-20250514",
            "claude-sonnet-4-20250514",
            "claude-haiku-4-5-20251001",
        ],
    ),
    "alibaba": ProviderInfo(
        slug="alibaba",
        label="Alibaba Cloud (DashScope)",
        default_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        api_mode="chat",
        description="Alibaba DashScope international",
        models=[
            "qwen3.6-plus",
            "kimi-k2.5",
            "qwen3.5-plus",
            "qwen3-coder-plus",
            "qwen3-coder-next",
            "glm-5",
            "glm-4.7",
            "MiniMax-M2.5",
        ],
    ),
    "alibaba-coding-plan": ProviderInfo(
        slug="alibaba-coding-plan",
        label="Alibaba Cloud (Coding Plan)",
        default_base_url="https://coding-intl.dashscope.aliyuncs.com/v1",
        api_mode="chat",
        description="Alibaba DashScope coding plan",
        models=[
            "qwen3.6-plus",
            "qwen3.5-plus",
            "qwen3-coder-plus",
            "qwen3-coder-next",
            "kimi-k2.5",
            "glm-5",
            "glm-4.7",
            "MiniMax-M2.5",
        ],
    ),
    "minimax-cn": ProviderInfo(
        slug="minimax-cn",
        label="MiniMax (China)",
        default_base_url="https://api.minimaxi.com/anthropic",
        api_mode="anthropic_messages",
        description="MiniMax China (Anthropic Messages)",
        models=[
            "MiniMax-M2.7",
            "MiniMax-M2.5",
            "MiniMax-M2.1",
            "MiniMax-M2",
        ],
    ),
    "deepseek": ProviderInfo(
        slug="deepseek",
        label="DeepSeek",
        default_base_url="https://api.deepseek.com/v1",
        api_mode="chat",
        description="DeepSeek official API",
        models=[
            "deepseek-v4-pro",
            "deepseek-v4-flash",
            "deepseek-chat",
            "deepseek-reasoner",
        ],
    ),
    "xai": ProviderInfo(
        slug="xai",
        label="xAI",
        default_base_url="https://api.x.ai/v1",
        api_mode="chat",
        description="xAI Grok",
        models=[
            "grok-4",
            "grok-4-fast",
            "grok-code-fast-1",
            "grok-3",
            "grok-3-mini",
        ],
    ),
    "nvidia": ProviderInfo(
        slug="nvidia",
        label="NVIDIA NIM",
        default_base_url="https://integrate.api.nvidia.com/v1",
        api_mode="chat",
        description="NVIDIA NIM (build.nvidia.com)",
        models=[
            "nvidia/nemotron-3-super-120b-a12b",
            "nvidia/nemotron-3-nano-30b-a3b",
            "nvidia/llama-3.3-nemotron-super-49b-v1.5",
            "qwen/qwen3.5-397b-a17b",
            "deepseek-ai/deepseek-v3.2",
            "moonshotai/kimi-k2.6",
            "minimaxai/minimax-m2.5",
            "z-ai/glm5",
            "openai/gpt-oss-120b",
        ],
    ),
    "ai-gateway": ProviderInfo(
        slug="ai-gateway",
        label="Vercel AI Gateway",
        default_base_url="https://ai-gateway.vercel.sh/v1",
        api_mode="chat",
        description="Vercel AI Gateway (multi-model)",
        models=[],
    ),
    "opencode-zen": ProviderInfo(
        slug="opencode-zen",
        label="OpenCode Zen",
        default_base_url="https://opencode.ai/zen/v1",
        api_mode="chat",
        description="OpenCode Zen gateway",
        models=[
            "kimi-k2.5",
            "gpt-5.4-pro",
            "gpt-5.4",
            "gpt-5.3-codex",
            "gpt-5.2",
            "gpt-5.2-codex",
            "gpt-5.1",
            "gpt-5.1-codex",
            "gpt-5.1-codex-max",
            "gpt-5.1-codex-mini",
            "gpt-5",
            "gpt-5-codex",
            "gpt-5-nano",
            "claude-opus-4-6",
            "claude-opus-4-5",
            "claude-opus-4-1",
            "claude-sonnet-4-6",
            "claude-sonnet-4-5",
            "claude-sonnet-4",
            "claude-haiku-4-5",
            "claude-3-5-haiku",
            "gemini-3.1-pro",
            "gemini-3-pro",
            "gemini-3-flash",
            "minimax-m2.7",
            "minimax-m2.5",
            "minimax-m2.5-free",
            "minimax-m2.1",
            "glm-5",
            "glm-4.7",
            "glm-4.6",
            "kimi-k2-thinking",
            "kimi-k2",
            "qwen3-coder",
            "big-pickle",
        ],
    ),
    "opencode-go": ProviderInfo(
        slug="opencode-go",
        label="OpenCode Go",
        default_base_url="https://opencode.ai/zen/go/v1",
        api_mode="chat",
        description="OpenCode Go gateway (mixed APIs)",
        models=[
            "kimi-k2.6",
            "kimi-k2.5",
            "glm-5.1",
            "glm-5",
            "mimo-v2.5-pro",
            "mimo-v2.5",
            "mimo-v2-pro",
            "mimo-v2-omni",
            "minimax-m2.7",
            "minimax-m2.5",
            "qwen3.6-plus",
            "qwen3.5-plus",
        ],
    ),
    "kilocode": ProviderInfo(
        slug="kilocode",
        label="Kilo Code",
        default_base_url="https://api.kilo.ai/api/gateway",
        api_mode="chat",
        description="Kilo Code gateway",
        models=[
            "anthropic/claude-opus-4.6",
            "anthropic/claude-sonnet-4.6",
            "openai/gpt-5.4",
            "google/gemini-3-pro-preview",
            "google/gemini-3-flash-preview",
        ],
    ),
    "huggingface": ProviderInfo(
        slug="huggingface",
        label="Hugging Face",
        default_base_url="https://router.huggingface.co/v1",
        api_mode="chat",
        description="Hugging Face Inference Router",
        models=[
            "moonshotai/Kimi-K2.5",
            "Qwen/Qwen3.5-397B-A17B",
            "Qwen/Qwen3.5-35B-A3B",
            "deepseek-ai/DeepSeek-V3.2",
            "MiniMaxAI/MiniMax-M2.5",
            "zai-org/GLM-5",
            "XiaomiMiMo/MiMo-V2-Flash",
            "moonshotai/Kimi-K2-Thinking",
            "moonshotai/Kimi-K2.6",
        ],
    ),
    "xiaomi": ProviderInfo(
        slug="xiaomi",
        label="Xiaomi MiMo",
        default_base_url="https://api.xiaomimimo.com/v1",
        api_mode="chat",
        description="Xiaomi MiMo",
        models=[
            "mimo-v2.5-pro",
            "mimo-v2.5",
            "mimo-v2-pro",
            "mimo-v2-omni",
            "mimo-v2-flash",
        ],
    ),
    "tencent-tokenhub": ProviderInfo(
        slug="tencent-tokenhub",
        label="Tencent TokenHub",
        default_base_url="https://tokenhub.tencentmaas.com/v1",
        api_mode="chat",
        description="Tencent TokenHub gateway",
        models=[
            "hy3-preview",
        ],
    ),
    "ollama-cloud": ProviderInfo(
        slug="ollama-cloud",
        label="Ollama Cloud",
        default_base_url="https://ollama.com/v1",
        api_mode="chat",
        description="Ollama Cloud hosted models",
        models=[],
    ),
    "ollama": ProviderInfo(
        slug="ollama",
        label="Ollama",
        default_base_url="http://localhost:11434/v1",
        api_mode="chat",
        description="Local Ollama — models discovered at runtime",
        models=[],
    ),
}


PROVIDER_ALIASES: dict[str, str] = {
    "gpt": "openai",
    "chatgpt": "openai",
    "claude": "anthropic",
    "gemini": "google",  # legacy alias
    "google": "gemini",
    "gemma": "gemini",
    "kimi": "kimi-coding",
    "glm": "zai",
    "qwen": "alibaba",
    "grok": "xai",
    "minimax": "minimax",
}

# Resolve legacy "google" alias to actual "gemini" entry. The above table
# maps "gemini" -> "google" historically, but the new registry uses "gemini"
# as the canonical slug. Drop the broken self-reference if present.
PROVIDER_ALIASES.pop("gemini", None)
PROVIDER_ALIASES["google"] = "gemini"
PROVIDER_ALIASES["gemma"] = "gemini"


ENV_MODEL_KEY = "LLM_MODEL"
ENV_PROVIDER_KEY = "LLM_PROVIDER"
ENV_BASE_URL_KEY = "LLM_BASE_URL"
ENV_API_KEY = "LLM_API_KEY"


def resolve_provider(name: str) -> ProviderInfo | None:
    key = (name or "").lower().strip()
    if not key:
        return None
    if key in PROVIDERS:
        return PROVIDERS[key]
    if key in PROVIDER_ALIASES:
        return PROVIDERS.get(PROVIDER_ALIASES[key])
    return None


def list_providers() -> list[ProviderInfo]:
    return list(PROVIDERS.values())


def get_provider_slugs() -> list[str]:
    return list(PROVIDERS.keys())
