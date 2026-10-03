#!/usr/bin/env python3
"""Import the LLM settings of a local OpenClaw install into ClawCross.

Read-only towards OpenClaw: it reads ``openclaw.json`` and the main agent's
``models.json`` and writes ClawCross's ``config/.env``. ClawCross never writes
OpenClaw's configuration.

    python src/backend/ops/setup/configure_openclaw.py --import-clawcross-llm-from-openclaw
"""

import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = str(Path(__file__).resolve().parents[4])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from src.backend.common.runtime_paths import ENV_FILE, ensure_runtime_dirs
ensure_runtime_dirs()
ENV_PATH = str(ENV_FILE)
OPENCLAW_HOME = os.path.expanduser(os.getenv("OPENCLAW_HOME", "~/.openclaw"))
OPENCLAW_CONFIG_PATH = os.path.join(OPENCLAW_HOME, "openclaw.json")
OPENCLAW_AGENT_MODELS_PATH = os.path.join(OPENCLAW_HOME, "agents", "main", "agent", "models.json")

sys.path.insert(0, SCRIPT_DIR)
from configure import read_env, set_env_with_validation  # noqa: E402


def mask_secret(value):
    """掩码显示敏感信息。"""
    if not value:
        return "****"
    if len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-4:]


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_openclaw_config():
    """``openclaw.json``, or {} when there is none."""
    return _load_json(OPENCLAW_CONFIG_PATH)


def load_openclaw_agent_models():
    """The main agent's ``models.json``, or {} when there is none."""
    return _load_json(OPENCLAW_AGENT_MODELS_PATH)


def get_config_value(*path):
    """A nested value of ``openclaw.json``."""
    current = load_openclaw_config()
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def detect_llm_config_from_openclaw():
    """从 OpenClaw 配置中探测 LLM 相关参数（API Key / Base URL / Model / Provider）。

    读取 openclaw.json 中的 models.providers 和 agents.defaults.model，
    返回 dict 形如:
        {"LLM_API_KEY": "...", "LLM_BASE_URL": "...", "LLM_MODEL": "...", "LLM_PROVIDER": "..."}
    仅包含成功探测到的字段。
    """
    result: dict[str, str] = {}

    def _strip_openclaw_base_url_suffix(base_url: str) -> str:
        """OpenClaw baseUrl 常带 /v1；Clawcross LLM_BASE_URL 不带末尾 /v1。"""
        u = (base_url or "").strip()
        if not u:
            return u
        stripped = u.rstrip("/")
        if stripped.endswith("/v1"):
            stripped = stripped[:-3]
        return stripped

    # Step 1: always use openclaw.json defaults.model.primary to decide provider/model.
    config = load_openclaw_config()
    if not config:
        return result

    default_model = get_config_value("agents", "defaults", "model", "primary")
    provider_id = None
    model_id = None
    if isinstance(default_model, str) and "/" in default_model:
        provider_id, model_id = default_model.split("/", 1)
    provider_id = (provider_id or "").strip() or None
    model_id = (model_id or "").strip() or None

    # Also load legacy providers map for fallback.
    providers_legacy = get_config_value("models", "providers") or {}

    # Step 2: read agent models.json to fill apiKey/baseUrl (preferred).
    models_doc = load_openclaw_agent_models()
    providers_doc = models_doc.get("providers") if isinstance(models_doc, dict) else None

    provider_cfg_doc = None
    if isinstance(providers_doc, dict) and provider_id and provider_id in providers_doc:
        provider_cfg_doc = providers_doc.get(provider_id)

    # If defaults didn't provide provider/model, fall back to first provider in models.json.
    if provider_cfg_doc is None and isinstance(providers_doc, dict) and providers_doc:
        for pid, cfg in providers_doc.items():
            if not isinstance(cfg, dict):
                continue
            models_list = cfg.get("models")
            if isinstance(models_list, list) and models_list:
                provider_id = str(pid).strip()
                provider_cfg_doc = cfg
                if not model_id:
                    first = models_list[0]
                    if isinstance(first, dict) and first.get("id"):
                        model_id = str(first.get("id") or "").strip() or None
                break
        # last resort: pick the first provider cfg
        if provider_cfg_doc is None:
            for pid, cfg in providers_doc.items():
                if isinstance(cfg, dict):
                    provider_id = str(pid).strip()
                    provider_cfg_doc = cfg
                    break

    # Fill from models.json provider cfg
    if isinstance(provider_cfg_doc, dict) and provider_cfg_doc:
        api_key = str(provider_cfg_doc.get("apiKey") or "").strip()
        base_url = str(provider_cfg_doc.get("baseUrl") or "").strip()

        # Ensure model_id is valid if we can match it.
        models_list = provider_cfg_doc.get("models")
        if isinstance(models_list, list) and models_list:
            if model_id:
                matched = False
                for m in models_list:
                    if isinstance(m, dict) and str(m.get("id") or "").strip() == model_id:
                        matched = True
                        break
                if not matched:
                    # Sometimes caller might pass "name"; try to map it back to id.
                    for m in models_list:
                        if isinstance(m, dict) and str(m.get("name") or "").strip() == model_id:
                            mid = str(m.get("id") or "").strip()
                            if mid:
                                model_id = mid
                            break
            else:
                first = models_list[0]
                if isinstance(first, dict) and first.get("id"):
                    model_id = str(first.get("id") or "").strip() or None

        if base_url:
            result["LLM_BASE_URL"] = _strip_openclaw_base_url_suffix(base_url)
        if api_key:
            result["LLM_API_KEY"] = api_key
        if model_id:
            result["LLM_MODEL"] = model_id
        if provider_id:
            result["LLM_PROVIDER"] = provider_id

    # Step 3: fallback to openclaw.json providers map if apiKey/baseUrl missing.
    if provider_id:
        provider_cfg_legacy = providers_legacy.get(provider_id, {}) if isinstance(providers_legacy, dict) else {}
        if isinstance(provider_cfg_legacy, dict):
            provider_name = str(provider_id).strip().lower()

            if "LLM_API_KEY" not in result or not result.get("LLM_API_KEY"):
                api_key = str(provider_cfg_legacy.get("apiKey") or "").strip()
                # OpenClaw openai provider prefers env.OPENAI_API_KEY.
                if provider_name == "openai":
                    api_key = ((config.get("env") or {}).get("OPENAI_API_KEY") or "").strip() or api_key
                elif not api_key and not provider_name:
                    api_key = ((config.get("env") or {}).get("OPENAI_API_KEY") or "").strip()
                if api_key:
                    result["LLM_API_KEY"] = api_key

            if "LLM_BASE_URL" not in result or not result.get("LLM_BASE_URL"):
                base_url = str(provider_cfg_legacy.get("baseUrl") or "").strip()
                if base_url:
                    result["LLM_BASE_URL"] = _strip_openclaw_base_url_suffix(base_url)

            # No agent models.json (or empty): still emit model/provider from
            # agents.defaults.model.primary + openclaw.json models.providers.
            if model_id and not result.get("LLM_MODEL"):
                mid = model_id
                models_list = provider_cfg_legacy.get("models")
                if isinstance(models_list, list) and models_list:
                    matched = False
                    for m in models_list:
                        if isinstance(m, dict) and str(m.get("id") or "").strip() == mid:
                            matched = True
                            break
                    if not matched:
                        for m in models_list:
                            if isinstance(m, dict) and str(m.get("name") or "").strip() == mid:
                                resolved = str(m.get("id") or "").strip()
                                if resolved:
                                    mid = resolved
                                break
                result["LLM_MODEL"] = mid

            if not result.get("LLM_PROVIDER"):
                result["LLM_PROVIDER"] = provider_id

    return result


# 模板 / 初始化向导留的默认 BASE_URL，不应阻止从 OpenClaw 导入真实 provider 的 baseUrl
_BOOTSTRAP_LLM_BASE_URLS = frozenset(
    {
        "",
        "https://api.deepseek.com",
        "http://api.deepseek.com",
    }
)


def _is_bootstrap_llm_base_url(url: str) -> bool:
    u = (url or "").strip().lower().rstrip("/")
    if not u:
        return True
    if u in {x.rstrip("/") for x in _BOOTSTRAP_LLM_BASE_URLS if x}:
        return True
    return False


def _llm_base_url_conflicts_with_openclaw(kvs: dict, detected: dict) -> bool:
    """Clawcross 里 BASE_URL 与 OpenClaw 探测结果明显不一致（如 DeepSeek 模板 + MiniMax 模型）。"""
    ex_url = (kvs.get("LLM_BASE_URL") or "").strip().lower()
    det_url = (detected.get("LLM_BASE_URL") or "").strip().lower()
    if not det_url or not ex_url:
        return False
    if ex_url.rstrip("/") == det_url.rstrip("/"):
        return False
    if _is_bootstrap_llm_base_url(ex_url):
        return True
    det_p = (detected.get("LLM_PROVIDER") or "").strip().lower()
    if det_p == "minimax" and "deepseek" in ex_url:
        return True
    if det_p == "deepseek" and "minimaxi" in ex_url:
        return True
    return False


def sync_llm_config_from_openclaw():
    """从 OpenClaw 同步 LLM 配置到 Clawcross .env。

    仅同步未设置或仍为占位符的字段，不覆盖用户已显式设置的值。
    模板默认的 LLM_BASE_URL（如 api.deepseek.com）视为可覆盖，以便导入 OpenClaw 真实 baseUrl。
    返回成功同步的字段数量。
    """
    detected = detect_llm_config_from_openclaw()
    if not detected:
        return 0

    _, kvs = read_env()
    kvs = dict(kvs)
    synced = 0
    placeholder_values = {"your_api_key_here", ""}
    base_url_conflict = _llm_base_url_conflicts_with_openclaw(kvs, detected)

    print("\n🔍 从 OpenClaw 同步 LLM 配置...")
    for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_PROVIDER"):
        new_val = detected.get(key)
        if not new_val:
            continue
        existing = (kvs.get(key) or "").strip()
        if existing and existing not in placeholder_values:
            if key == "LLM_BASE_URL" and (
                _is_bootstrap_llm_base_url(existing) or base_url_conflict
            ):
                pass
            else:
                display = mask_secret(existing) if key in {"LLM_API_KEY"} else existing
                print(f"   ⏭️  {key} 已设置 ({display})，保留不变")
                continue
        set_env_with_validation(key, new_val)
        kvs[key] = new_val
        synced += 1

    if synced > 0:
        print(f"   ✅ 从 OpenClaw 同步了 {synced} 项 LLM 配置")
    else:
        print("   ℹ️ LLM 配置已是最新，无需同步")

    return synced


def main():
    if sys.argv[1:] != ["--import-clawcross-llm-from-openclaw"]:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    try:
        print("🔄 从 OpenClaw 导入 LLM 配置到 Clawcross（写入 config/.env）...")
        synced = sync_llm_config_from_openclaw()
        print(f"✅ 导入完成：已同步 {synced} 项配置")
    except Exception as e:
        print(f"❌ 导入失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
