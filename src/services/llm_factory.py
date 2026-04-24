"""
LLM 模型工厂模块

创建和管理共享的 LangChain 聊天模型实例：
- 支持多种 provider（OpenAI、Anthropic、Google、DeepSeek）
- 自动推断 provider（根据模型名、API URL、API Key 前缀）
- 标准化 OpenAI 兼容的 base URL
- 提供 TTS/STT 默认配置
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from langchain_core.language_models.chat_models import BaseChatModel


def extract_text(content) -> str:
    """将 provider 特定的消息内容转换为纯文本。

    :param content: 消息内容（可能是字符串、列表或字典）
    :return: 纯文本字符串
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)

    return str(content)


_MODEL_PROVIDER_PATTERNS: dict[str, str] = {
    "gemini": "google",
    "claude": "anthropic",
    "deepseek": "deepseek",
    "gpt": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    "qwen": "openai",
    "minimax": "minimax",
    "glm": "openai",
    "moonshot": "openai",
    "yi-": "openai",
    "baichuan": "openai",
    "doubao": "openai",
    "hunyuan": "openai",
    "ernie": "openai",
    "mistral": "openai",
    "llama": "openai",
    "groq": "openai",
}

_PROVIDER_ALIASES: dict[str, str] = {
    "gemini": "google",
    "google": "google",
    "openai": "openai",
    "anthropic": "anthropic",
    "deepseek": "deepseek",
    "ollama": "ollama",
    "antigravity": "openai",  # Antigravity reverse-proxy → OpenAI-compatible
    "minimax": "minimax",    # MiniMax chat → Anthropic-compatible API (separate branch in create_chat_model)
}

_BASE_URL_PROVIDER_PATTERNS: tuple[tuple[str, str], ...] = (
    ("api.openai.com", "openai"),
    ("openai.azure.com", "openai"),
    ("generativelanguage.googleapis.com", "google"),
    ("ai.google.dev", "google"),
    ("googleapis.com", "google"),
    ("127.0.0.1:8045", "openai"),  # Antigravity-Manager default endpoint
    ("localhost:8045", "openai"),   # Antigravity-Manager default endpoint
    ("127.0.0.1:11434", "ollama"),  # Ollama OpenAI-compatible endpoint
    ("localhost:11434", "ollama"),  # Ollama OpenAI-compatible endpoint
    ("api.minimaxi.com", "minimax"),  # MiniMax API endpoint
)

_AUDIO_DEFAULTS: dict[str, dict[str, str]] = {
    "openai": {
        "tts_model": "gpt-4o-mini-tts",
        "tts_voice": "alloy",
        "stt_model": "whisper-1",
    },
    "google": {
        "tts_model": "gemini-2.5-flash-preview-tts",
        "tts_voice": "charon",
        "stt_model": "",
    },
    "ollama": {
        "tts_model": "",
        "tts_voice": "",
        "stt_model": "",
    },
    # MiniMax chat uses Anthropic-compatible API; reuse OpenAI-style names as generic audio defaults.
    "minimax": {
        "tts_model": "gpt-4o-mini-tts",
        "tts_voice": "alloy",
        "stt_model": "whisper-1",
    },
}

_NO_TEMPERATURE_PREFIXES = ("gpt-5", "o1", "o3", "o4")
_RESPONSES_API_PREFIXES = ("gpt-5", "o1", "o3", "o4")
_OPENAI_ENDPOINT_SUFFIXES = (
    "/v1/chat/completions",
    "/chat/completions",
    "/v1/responses",
    "/responses",
    "/v1/models",
    "/models",
)


def _model_has_prefix(model: str, prefix: str) -> bool:
    model_lower = model.lower()
    if not model_lower.startswith(prefix):
        return False

    return len(model_lower) == len(prefix) or model_lower[len(prefix)] in "-_."


def _model_supports_temperature(model: str) -> bool:
    return not any(_model_has_prefix(model, prefix) for prefix in _NO_TEMPERATURE_PREFIXES)


def _normalize_openai_base_url(base_url: str) -> str:
    cleaned = (base_url or "https://api.deepseek.com").strip().rstrip("/")
    cleaned_lower = cleaned.lower()

    for suffix in _OPENAI_ENDPOINT_SUFFIXES:
        if cleaned_lower.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            cleaned_lower = cleaned.lower()
            break

    if not cleaned_lower.endswith("/v1"):
        cleaned = cleaned.rstrip("/") + "/v1"

    return cleaned


def _normalize_minimax_base_url(base_url: str) -> str:
    """
    MiniMax anthropic endpoint normalization.

    规则：
    - baseUrl 末尾已是 /anthropic：保持不变
    - baseUrl 只提供 host（路径为空或 /）：补上 /anthropic
    - 其他路径：原样保留（让用户自己提供完整端点）
    """
    u = (base_url or "").strip().rstrip("/")
    if not u:
        return u

    # 允许 base_url 只写 host:port
    if "://" not in u:
        u = "https://" + u

    parsed = urlparse(u)
    scheme = parsed.scheme
    netloc = parsed.netloc
    path = (parsed.path or "").rstrip("/")

    if not path or path == "/":
        return f"{scheme}://{netloc}/anthropic"

    if path.endswith("/anthropic"):
        return f"{scheme}://{netloc}{path}"

    # 其他路径：保持原路径，避免破坏用户提供的兼容端点
    return f"{scheme}://{netloc}{path}"


def _is_native_openai_host(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host = (parsed.netloc or parsed.path).lower()
    return host == "api.openai.com" or host.endswith(".openai.com")


def _should_use_responses_api(model: str, base_url: str) -> bool:
    return _is_native_openai_host(base_url) and any(
        _model_has_prefix(model, prefix) for prefix in _RESPONSES_API_PREFIXES
    )


def _normalize_provider_name(provider: str) -> str:
    return _PROVIDER_ALIASES.get((provider or "").strip().lower(), "")


def infer_provider(
    *,
    model: str = "",
    base_url: str = "",
    provider: str = "",
    api_key: str = "",
) -> str:
    """根据显式 provider、base_url、模型名自动推断 provider。

    :param model: 模型名称
    :param base_url: API base URL
    :param provider: 显式指定的 provider
    :param api_key: API key（保留参数用于兼容调用方；不参与 provider 推断）
    :return: provider 名称（openai/anthropic/google/deepseek/ollama/minimax）
    """
    normalized_provider = _normalize_provider_name(provider)
    if normalized_provider:
        return normalized_provider

    base_url_lower = (base_url or "").strip().lower()
    for pattern, detected_provider in _BASE_URL_PROVIDER_PATTERNS:
        if pattern in base_url_lower:
            return detected_provider

    model_lower = (model or "").strip().lower()
    for pattern, detected_provider in _MODEL_PROVIDER_PATTERNS.items():
        if pattern in model_lower:
            return detected_provider

    return "openai"


def get_provider_audio_defaults(provider: str) -> dict[str, str]:
    """获取 provider 对应的音频（TTS/STT）默认模型配置。

    :param provider: provider 名称
    :return: 包含 tts_model、tts_voice、stt_model 的字典
    """
    normalized_provider = _normalize_provider_name(provider)
    if not normalized_provider:
        return {"tts_model": "", "tts_voice": "", "stt_model": ""}
    return dict(_AUDIO_DEFAULTS.get(normalized_provider, {"tts_model": "", "tts_voice": "", "stt_model": ""}))


def create_chat_model(
    *,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    timeout: int = 60,
    max_retries: int = 2,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    provider: str | None = None,
) -> BaseChatModel:
    """从环境变量创建聊天模型实例。

    当 *model*、*api_key*、*base_url* 或 *provider* 显式传入时，
    优先使用传入值而非全局 LLM_* 环境变量，支持 per-expert/per-team-member 的模型覆盖。

    必需的环境变量（作为回退）：
      - LLM_API_KEY
      - LLM_MODEL
      - LLM_BASE_URL（部分 provider 有默认值）

    :param temperature: 生成温度
    :param max_tokens: 最大 token 数
    :param timeout: 超时时间（秒）
    :param max_retries: 最大重试次数
    :param model: 模型名称（优先于 env）
    :param api_key: API key（优先于 env）
    :param base_url: API base URL（优先于 env）
    :param provider: provider 名称（优先于 env）
    :return: LangChain 聊天模型实例
    """
    explicit_api_key = (api_key or "").strip()
    explicit_base_url = (base_url or "").strip()
    explicit_model = (model or "").strip()
    explicit_provider = (provider or "").strip().lower()

    env_api_key = (os.getenv("LLM_API_KEY") or "").strip()
    env_base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").strip()
    env_model = (os.getenv("LLM_MODEL") or "").strip()
    env_provider = os.getenv("LLM_PROVIDER", "").strip().lower()

    api_key = explicit_api_key or env_api_key
    base_url = explicit_base_url or env_base_url
    model = explicit_model or env_model
    provider = explicit_provider or env_provider
    if not model:
        raise ValueError(
            "LLM_MODEL is not configured. Set it in config/.env, or run "
            "selfskill/scripts/configure.py --auto-model and then configure LLM_MODEL <model>."
        )

    supports_temp = _model_supports_temperature(model)

    provider = infer_provider(
        model=model,
        base_url=base_url,
        provider=provider,
        api_key=api_key,
    )

    if provider == "ollama":
        env_base_url_is_ollama = infer_provider(
            model=model,
            base_url=env_base_url,
            provider=env_provider,
            api_key=env_api_key,
        ) == "ollama"
        base_url = explicit_base_url or (env_base_url if env_base_url_is_ollama else "http://127.0.0.1:11434")
        api_key = explicit_api_key or (env_api_key if env_base_url_is_ollama or env_provider == "ollama" else "") or "ollama"
    elif not api_key:
        raise ValueError("LLM_API_KEY is not configured.")

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        kwargs = {
            "model": model,
            "google_api_key": api_key,
            "max_output_tokens": max_tokens,
            "timeout": timeout,
            "max_retries": max_retries,
        }
        if supports_temp:
            kwargs["temperature"] = temperature
        if base_url:
            kwargs["base_url"] = base_url
        return ChatGoogleGenerativeAI(**kwargs)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs = {
            "model": model,
            "api_key": api_key,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "max_retries": max_retries,
        }
        if supports_temp:
            kwargs["temperature"] = temperature
        if base_url:
            kwargs["base_url"] = base_url
        return ChatAnthropic(**kwargs)

    if provider == "deepseek":
        from langchain_deepseek import ChatDeepSeek

        kwargs = {
            "model": model,
            "api_key": api_key,
            "base_url": base_url,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "max_retries": max_retries,
            "use_responses_api": False,
        }
        if supports_temp:
            kwargs["temperature"] = temperature
        return ChatDeepSeek(**kwargs)

    if provider == "minimax":
        from langchain_anthropic import ChatAnthropic

        anthropic_base = _normalize_minimax_base_url(base_url)
        kwargs = {
            "model": model,
            "api_key": api_key,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "max_retries": max_retries,
        }
        if supports_temp:
            kwargs["temperature"] = temperature
        if anthropic_base:
            kwargs["base_url"] = anthropic_base
        return ChatAnthropic(**kwargs)

    from langchain_openai import ChatOpenAI

    openai_base = _normalize_openai_base_url(base_url)
    kwargs = {
        "model": model,
        "base_url": openai_base,
        "api_key": api_key,
        "max_tokens": max_tokens,
        "timeout": timeout,
        "max_retries": max_retries,
    }
    if supports_temp:
        kwargs["temperature"] = temperature
    if _should_use_responses_api(model, openai_base):
        kwargs["use_responses_api"] = True

    return ChatOpenAI(**kwargs)
