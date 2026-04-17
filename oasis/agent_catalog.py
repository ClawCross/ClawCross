from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from typing import Any

_SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from integrations.acpx_cli_tools import acpx_agent_tags_with_legacy
from oasis.experts import get_all_experts

_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
_ACP_PLATFORMS = {str(tag or "").strip().lower() for tag in acpx_agent_tags_with_legacy()}


def _canonical_external_platform(platform_name: str) -> str:
    pl = (platform_name or "").strip().lower()
    if pl in ("claude-code", "claudecode"):
        return "claude"
    if pl in ("gemini-cli", "geminicli"):
        return "gemini"
    return pl


def _load_json_list(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _team_base(user_id: str, team: str = "") -> str:
    if team:
        return os.path.join(_PROJECT_ROOT, "data", "user_files", user_id, "teams", team)
    return os.path.join(_PROJECT_ROOT, "data", "user_files", user_id)


def _load_internal_agents(user_id: str, team: str = "") -> list[dict]:
    return _load_json_list(os.path.join(_team_base(user_id, team), "internal_agents.json"))


def _load_external_agents(user_id: str, team: str = "") -> list[dict]:
    return _load_json_list(os.path.join(_team_base(user_id, team), "external_agents.json"))


def _expert_by_tag(user_id: str, team: str = "") -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in get_all_experts(user_id, team=team):
        tag = str(item.get("tag", "") or "").strip()
        if tag and tag not in result:
            result[tag] = item
    return result


def build_persona_catalog(user_id: str, team: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for expert in get_all_experts(user_id, team=team):
        tag = str(expert.get("tag", "") or "").strip()
        if not tag:
            continue
        options = {
            "temperature": float(expert.get("temperature", 0.7)),
            "max_tokens": 1024,
        }
        for key in ("model", "api_key", "base_url", "provider"):
            if expert.get(key):
                options[key] = expert.get(key)
        items.append({
            "id": f"persona:{tag}",
            "name": str(expert.get("name", "") or tag),
            "tag": tag,
            "persona": str(expert.get("persona", "") or ""),
            "source": str(expert.get("source", "") or ""),
            "options": options,
            "raw": deepcopy(expert),
        })
    return items


def build_agent_catalog(user_id: str, team: str = "") -> list[dict[str, Any]]:
    experts_by_tag = _expert_by_tag(user_id, team)
    port = os.getenv("PORT_AGENT", "51200")
    internal_token = os.getenv("INTERNAL_TOKEN", "")
    internal_api_url = f"http://127.0.0.1:{port}/v1/chat/completions"

    items: list[dict[str, Any]] = []

    for agent in _load_internal_agents(user_id, team):
        name = str(agent.get("name", "") or "").strip()
        session_id = str(agent.get("session", "") or "").strip()
        if not name or not session_id:
            continue
        tag = str(agent.get("tag", "") or "").strip()
        expert = experts_by_tag.get(tag, {})
        options: dict[str, Any] = {
            "api_url": internal_api_url,
            "headers": {"Authorization": f"Bearer {internal_token}:{user_id}"},
            "body": {"model": "webot", "stream": False},
        }
        llm_override: dict[str, Any] = {}
        for key in ("model", "api_key", "base_url", "provider"):
            if expert.get(key):
                llm_override[key] = expert.get(key)
        if llm_override:
            options["body"]["llm_override"] = llm_override
        items.append({
            "id": f"internal:{name}",
            "kind": "internal",
            "name": name,
            "tag": tag,
            "platform": "internal",
            "connect_type": "http",
            "session": session_id,
            "source": "team" if team else "user",
            "options": options,
            "raw": deepcopy(agent),
        })

    for agent in _load_external_agents(user_id, team):
        name = str(agent.get("name", "") or "").strip()
        if not name:
            continue
        tag = str(agent.get("tag", "") or "").strip()
        platform = _canonical_external_platform(str(agent.get("platform", "") or tag))
        expert = experts_by_tag.get(tag, {})
        model = str(agent.get("model", "") or "").strip()
        global_name = str(agent.get("global_name", "") or "").strip()
        session_suffix = "clawcrosschat"
        if model.startswith("agent:"):
            parts = model.split(":")
            if len(parts) >= 3 and parts[2].strip():
                session_suffix = parts[2].strip()
        connect_type = "http"
        if platform != "openclaw" and platform in _ACP_PLATFORMS:
            connect_type = "acp"
        session_key = f"agent:{global_name}:{session_suffix}" if global_name else None
        options = {
            "headers": deepcopy(agent.get("headers") or {}),
        }
        if connect_type == "http":
            api_url = str(agent.get("api_url", "") or "").strip()
            api_key = str(agent.get("api_key", "") or "").strip()
            if platform == "openclaw":
                api_url = os.getenv("OPENCLAW_API_URL", "") or api_url
                api_key = os.getenv("OPENCLAW_GATEWAY_TOKEN", "") or api_key
            options["api_url"] = api_url
            if api_key:
                options["api_key"] = api_key
            req_model = model or "gpt-3.5-turbo"
            if platform == "openclaw" and global_name and not req_model.startswith("agent:"):
                req_model = f"agent:{global_name}"
            options["body"] = {"model": req_model, "stream": False}
        else:
            options["cwd"] = _PROJECT_ROOT
            options["timeout_sec"] = 180
        items.append({
            "id": f"external:{name}",
            "kind": "external",
            "name": name,
            "tag": tag,
            "platform": platform,
            "connect_type": connect_type,
            "session": session_key,
            "source": "team" if team else "user",
            "options": options,
            "raw": deepcopy(agent),
        })

    return items
