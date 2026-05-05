"""
环境配置读写模块

提供 .env 文件的读写接口，支持：
- 白名单密钥读取（只返回允许的配置项）
- 敏感字段自动脱敏
- 配置合并更新
"""

from typing import Iterable


SETTINGS_WHITELIST = [
    "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_PROVIDER", "LLM_VISION_SUPPORT",
    "TTS_MODEL", "TTS_VOICE",
    "PORT_AGENT", "PORT_SCHEDULER", "PORT_OASIS", "PORT_FRONTEND",
    "OASIS_BASE_URL",
    # Chatbot 多渠道（NoneBot 桥接 + WeClaw 微信桥）
    "NONEBOT_ADAPTERS", "NONEBOT_HOST", "NONEBOT_PORT",
    "WHITELIST_FILE",
    "WECLAW_ENABLED", "WECLAW_BIN", "WECLAW_USERNAME", "WECLAW_CONFIG",
    "TELEGRAM_BOTS", "QQ_BOTS", "QQ_IS_SANDBOX",
    "PUBLIC_DOMAIN",
    "OPENAI_STANDARD_MODE",
    "ALLOWED_COMMANDS", "EXEC_TIMEOUT", "BACKGROUND_EXEC_TIMEOUT", "MAX_EXEC_TIMEOUT", "MAX_OUTPUT_LENGTH",
    "TINYFISH_API_KEY", "TINYFISH_BASE_URL",
    "TINYFISH_MONITOR_DB_PATH", "TINYFISH_MONITOR_TARGETS_PATH",
    "TINYFISH_MONITOR_ENABLED", "TINYFISH_MONITOR_CRON",
]

MASK_FIELDS = {"LLM_API_KEY", "TELEGRAM_BOTS", "QQ_BOTS", "TINYFISH_API_KEY"}
FULL_MASK_PATTERNS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def _iter_env_items(env_path: str):
    """遍历 .env 文件中的有效键值对（跳过注释和空行）。

    :param env_path: .env 文件路径
    :yield: (key, value) 元组
    """
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                if key:
                    yield key, val.strip()
    except FileNotFoundError:
        return


def read_env_settings(env_path: str, allowed_keys: Iterable[str] | None = None) -> dict:
    """读取 .env 中的白名单配置项。

    :param env_path: .env 文件路径
    :param allowed_keys: 允许读取的密钥列表，None 时使用 SETTINGS_WHITELIST
    :return: 配置字典
    """
    allowed = set(allowed_keys if allowed_keys is not None else SETTINGS_WHITELIST)
    settings = {}
    for key, val in _iter_env_items(env_path):
        if key in allowed:
            settings[key] = val
    return settings


def read_env_all(env_path: str) -> dict:
    """读取 .env 中的所有非注释键值对。

    :param env_path: .env 文件路径
    :return: 配置字典
    """
    settings = {}
    for key, val in _iter_env_items(env_path):
        settings[key] = val
    return settings


def mask_sensitive(settings: dict, masked_fields: set[str] | None = None) -> dict:
    """对指定敏感字段进行脱敏处理。

    :param settings: 原始配置字典
    :param masked_fields: 要脱敏的字段集合，None 时使用 MASK_FIELDS
    :return: 脱敏后的配置字典
    """
    fields = masked_fields if masked_fields is not None else MASK_FIELDS
    masked = {}
    for key, val in settings.items():
        if key in fields and val and len(val) > 8:
            masked[key] = val[:4] + "****" + val[-4:]
        else:
            masked[key] = val
    return masked


def mask_all_sensitive(settings: dict, patterns: tuple[str, ...] | None = None) -> dict:
    """对名称包含敏感模式的字段进行脱敏。

    :param settings: 原始配置字典
    :param patterns: 敏感模式元组，None 时使用 FULL_MASK_PATTERNS
    :return: 脱敏后的配置字典
    """
    mask_patterns = patterns if patterns is not None else FULL_MASK_PATTERNS
    masked = {}
    for key, val in settings.items():
        if any(p in key.upper() for p in mask_patterns) and val and len(val) > 8:
            masked[key] = val[:4] + "****" + val[-4:]
        else:
            masked[key] = val
    return masked


def write_env_settings(env_path: str, updates: dict):
    """将更新合并到 .env 文件（更新已有键，追加新键）。

    :param env_path: .env 文件路径
    :param updates: 要更新的配置字典
    """
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def filter_whitelisted_updates(
    incoming: dict,
    allowed_keys: Iterable[str] | None = None,
) -> dict:
    """过滤只保留白名单密钥，并跳过已被脱敏的占位符值。

    :param incoming: 传入的更新字典
    :param allowed_keys: 允许的密钥集合，None 时使用 SETTINGS_WHITELIST
    :return: 过滤后的配置字典
    """
    allowed = set(allowed_keys if allowed_keys is not None else SETTINGS_WHITELIST)
    filtered = {}
    for key, value in incoming.items():
        if key not in allowed:
            continue
        if "****" in str(value):
            continue
        filtered[key] = str(value)
    return filtered


def filter_updates_skip_mask(incoming: dict) -> dict:
    """过滤掉已被脱敏的占位符值，保留其他所有键。

    :param incoming: 传入的更新字典
    :return: 过滤后的配置字典
    """
    updates = {}
    for key, value in incoming.items():
        if "****" in str(value):
            continue
        updates[key] = str(value)
    return updates
