"""Saved API model profiles. Selection belongs to an Agent, never the platform."""
from __future__ import annotations

import hashlib
import os
import sys
from threading import RLock

from common.runtime_paths import CONFIG_DIR, PROJECT_ROOT

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from src.cli.commands import models_store

_lock = RLock()


def _user_path(user: str):
    return CONFIG_DIR / 'model-profiles' / (hashlib.sha256(user.encode()).hexdigest() + '.json')


def public_profile(profile, profile_id: str) -> dict:
    from common.model_capabilities import model_capabilities
    return {'id': profile_id, 'name': profile.name, 'provider': profile.provider,
            'model': profile.model, 'base_url': profile.base_url,
            'has_api_key': bool(profile.auth.api_key), 'model_capabilities':model_capabilities(profile.model, profile.provider)}


def platform_default() -> dict:
    from common.llm_factory import infer_provider
    active = models_store.get_active()
    model = os.getenv('LLM_MODEL', '').strip() or (active.model if active else '')
    base_url = os.getenv('LLM_BASE_URL', '').strip() or (active.base_url if active else '') or 'https://api.deepseek.com'
    provider = os.getenv('LLM_PROVIDER', '').strip() or (active.provider if active else '')
    return {'model': model, 'base_url': base_url,
            'provider': infer_provider(model=model, base_url=base_url, provider=provider, api_key='')}


def catalog(user: str) -> dict:
    from common.model_capabilities import model_capabilities
    own = models_store.load(path=_user_path(user))
    platform = models_store.load()
    default = platform_default()
    default['model_capabilities'] = model_capabilities(default['model'], default['provider'])
    return {'default': default, 'profiles': [
        *[public_profile(p, 'user:' + p.name) for p in own.profiles.values()],
        *[public_profile(p, 'platform:' + p.name) for p in platform.profiles.values()],
    ]}


def save_profile(user: str, *, name: str, model: str, provider: str, base_url: str, api_key: str) -> dict:
    from urllib.parse import urlsplit
    name, model, provider, base_url = name.strip(), model.strip(), provider.strip().lower(), base_url.strip()
    if not name or not model:
        raise ValueError('配置名称和模型不能为空')
    if provider not in {'openai', 'deepseek', 'anthropic', 'google', 'minimax', 'ollama'}:
        raise ValueError('请选择支持的 API 提供方')
    if base_url:
        url = urlsplit(base_url)
        if url.scheme not in {'https', 'http'} or not url.hostname or url.username or url.password:
            raise ValueError('API 地址必须是 http/https 地址，不要在地址中填写密钥')
    with _lock:
        path = _user_path(user)
        store = models_store.load(path=path)
        old = store.profiles.get(name)
        key = api_key.strip() or (old.auth.api_key if old else '')
        if not key and provider != 'ollama':
            raise ValueError('请填写此配置的 API 密钥；更新已有配置时可留空保留原密钥')
        defaults = {'openai': 'https://api.openai.com/v1', 'deepseek': 'https://api.deepseek.com',
                    'anthropic': 'https://api.anthropic.com', 'google': 'https://generativelanguage.googleapis.com',
                    'minimax': 'https://api.minimax.io/v1', 'ollama': 'http://127.0.0.1:11434'}
        profile = models_store.Profile(name=name, model=model, provider=provider,
                                      base_url=base_url or defaults[provider],
                                      auth=models_store.ProfileAuth(api_key=key or 'ollama'))
        store.profiles[name] = profile
        models_store.save(store, path=path)
        return public_profile(profile, 'user:' + name)


def profile_override(user: str, profile_id: str) -> dict:
    if not profile_id:
        return {}
    scope, sep, name = profile_id.partition(':')
    if not sep or scope not in {'user', 'platform'}:
        raise ValueError('无效的模型配置')
    store = models_store.load(path=_user_path(user)) if scope == 'user' else models_store.load()
    profile = store.profiles.get(name)
    if not profile:
        raise ValueError('模型配置不存在')
    return {'model': profile.model, 'provider': profile.provider, 'base_url': profile.base_url,
            'api_key': profile.auth.api_key, 'profile_id': profile_id, 'profile_name': profile.name}


def agent_model_override(user: str, agent_id: str) -> dict:
    from agents.store import get_store
    agent = get_store().get(user, agent_id)
    return dict(agent.config.get('llm') or {}) if agent else {}
