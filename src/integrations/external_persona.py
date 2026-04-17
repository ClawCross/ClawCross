from __future__ import annotations

import os as _os
import sys as _sys

# 确保 oasis 模块可以被导入
_oasis_path = _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__)))
if _oasis_path not in _sys.path:
    _sys.path.insert(0, _oasis_path)

_DEBUG_FILE = _os.environ.get("CLAWCROSS_PERSONA_DEBUG", "")

def _log(*args):
    if not _DEBUG_FILE:
        return
    try:
        with open(_DEBUG_FILE, "a") as f:
            import time as _time
            f.write(f"[{_time.strftime('%H:%M:%S')}] " + " ".join(str(a) for a in args) + "\n")
    except Exception:
        pass


def build_external_persona_prompt(tag: str = "", *, user_id: str = "", team: str = "") -> str:
    """Resolve an external-agent persona by tag and format it for first-prompt injection."""
    persona_tag = str(tag or "").strip()
    _log(f"CALL tag={tag!r} uid={user_id!r} team={team!r}")
    if not persona_tag:
        _log(f"  -> empty tag, return empty")
        return ""
    try:
        from oasis.experts import get_all_experts
    except Exception as e:
        _log(f"  -> import error: {e}, return empty")
        return ""

    try:
        experts = get_all_experts(user_id or None, team=team or "")
    except Exception as e:
        _log(f"  -> get_all_experts error: {e}, return empty")
        return ""

    _log(f"  -> got {len(experts)} experts")
    matched = None
    for expert in experts:
        if not isinstance(expert, dict):
            continue
        if str(expert.get("tag", "")).strip() == persona_tag:
            matched = expert
            _log(f"  -> matched: {matched.get('name')} source={matched.get('source')}")
            break
    if not matched:
        _log(f"  -> no match for tag={persona_tag!r}, return empty")
        return ""

    persona = str(matched.get("persona", "") or "").strip()
    if not persona:
        _log(f"  -> empty persona, return empty")
        return ""

    expert_name = str(matched.get("name", "") or persona_tag).strip()
    result = (
        "【外部 Agent 人设】\n"
        f"当前 persona tag: {persona_tag}\n"
        f"当前角色名: {expert_name}\n"
        "以下是你需要遵循的人设与行为描述：\n\n"
        f"{persona}"
    ).strip()
    _log(f"  -> return {len(result)} chars")
    return result
