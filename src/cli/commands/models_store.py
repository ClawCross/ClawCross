"""
Multi-profile model store for ClawCross.

Persists a list of LLM profiles (provider + model + auth) to
``~/.clawcross/config/models.json`` with a single ``active`` pointer.
The active profile is what the runtime resolver hands to llm_factory.

Schema (version 1)::

    {
      "version": 1,
      "active": "<profile name>",
      "profiles": {
        "<name>": {
          "provider": "anthropic",
          "model": "claude-sonnet-4-5-20250929",
          "base_url": "https://api.anthropic.com",
          "api_mode": "anthropic_messages",
          "auth": {
            "type": "api_key",
            "api_key": "sk-ant-..."
          }
        }
      }
    }

Auth ``type`` is reserved as a discriminator for future OAuth / bearer /
external-process support. Only ``api_key`` is implemented in v1.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path


SCHEMA_VERSION = 1


@dataclass
class ProfileAuth:
    type: str = "api_key"
    api_key: str = ""


@dataclass
class Profile:
    name: str
    provider: str
    model: str
    base_url: str = ""
    api_mode: str = "chat"
    auth: ProfileAuth = field(default_factory=ProfileAuth)


@dataclass
class ModelsStore:
    active: str = ""
    profiles: dict[str, Profile] = field(default_factory=dict)
    version: int = SCHEMA_VERSION


def _store_path() -> Path:
    home = Path(os.environ.get("CLAWCROSS_HOME", Path.home() / ".clawcross"))
    return home / "config" / "models.json"


def store_exists() -> bool:
    return _store_path().is_file()


def load() -> ModelsStore:
    """Read models.json; return an empty store if absent or malformed."""
    path = _store_path()
    if not path.is_file():
        return ModelsStore()
    try:
        data = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return ModelsStore()

    profiles: dict[str, Profile] = {}
    for name, raw in (data.get("profiles") or {}).items():
        auth_raw = raw.get("auth") or {}
        profiles[name] = Profile(
            name=name,
            provider=raw.get("provider", ""),
            model=raw.get("model", ""),
            base_url=raw.get("base_url", ""),
            api_mode=raw.get("api_mode", "chat"),
            auth=ProfileAuth(
                type=auth_raw.get("type", "api_key"),
                api_key=auth_raw.get("api_key", ""),
            ),
        )
    return ModelsStore(
        active=data.get("active", ""),
        profiles=profiles,
        version=data.get("version", SCHEMA_VERSION),
    )


def save(store: ModelsStore) -> None:
    """Write models.json atomically with 0600 permissions."""
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": store.version or SCHEMA_VERSION,
        "active": store.active,
        "profiles": {name: _profile_dict(p) for name, p in store.profiles.items()},
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", "utf-8")
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    os.replace(tmp, path)


def _profile_dict(p: Profile) -> dict:
    d = asdict(p)
    d.pop("name", None)
    return d


def list_profiles(store: ModelsStore | None = None) -> list[Profile]:
    s = store or load()
    return list(s.profiles.values())


def get_profile(name: str, store: ModelsStore | None = None) -> Profile | None:
    s = store or load()
    return s.profiles.get(name)


def get_active(store: ModelsStore | None = None) -> Profile | None:
    s = store or load()
    if not s.active:
        return None
    return s.profiles.get(s.active)


def set_active(name: str) -> Profile:
    s = load()
    if name not in s.profiles:
        raise KeyError(f"profile not found: {name!r}")
    s.active = name
    save(s)
    return s.profiles[name]


def upsert_profile(
    name: str,
    provider: str,
    model: str,
    api_key: str = "",
    base_url: str = "",
    api_mode: str = "chat",
    make_active: bool = False,
) -> Profile:
    s = load()
    profile = Profile(
        name=name,
        provider=provider,
        model=model,
        base_url=base_url,
        api_mode=api_mode,
        auth=ProfileAuth(type="api_key", api_key=api_key),
    )
    s.profiles[name] = profile
    if make_active or not s.active:
        s.active = name
    save(s)
    return profile


def remove_profile(name: str) -> bool:
    s = load()
    if name not in s.profiles:
        return False
    del s.profiles[name]
    if s.active == name:
        s.active = next(iter(s.profiles), "")
    save(s)
    return True
