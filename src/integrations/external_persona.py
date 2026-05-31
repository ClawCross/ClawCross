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
    """Resolve an external-agent persona by tag and format it for first-prompt injection.

    Persona framing and skill listing mirror the internal session agent
    (webot.profiles.frame_session_identity / webot.skills.build_user_skills_listing),
    so internal and external agents share one source of truth. Skill and workflow
    blocks are injected regardless of whether a persona tag matched, matching
    internal agents which inject skills unconditionally.
    """
    persona_tag = str(tag or "").strip()
    _log(f"CALL tag={tag!r} uid={user_id!r} team={team!r}")

    # --- Resolve persona by tag (may be absent; skills are injected either way) ---
    persona = ""
    expert_name = persona_tag
    if persona_tag:
        experts: list = []
        try:
            from oasis.experts import get_all_experts
            experts = get_all_experts(user_id or None, team=team or "")
        except Exception as e:
            _log(f"  -> get_all_experts error: {e}")
        _log(f"  -> got {len(experts)} experts")
        for expert in experts:
            if not isinstance(expert, dict):
                continue
            if str(expert.get("tag", "")).strip() == persona_tag:
                persona = str(expert.get("persona", "") or "").strip()
                expert_name = str(expert.get("name", "") or "").strip() or persona_tag
                _log(f"  -> matched: {expert.get('name')} source={expert.get('source')}")
                break
        if not persona:
            _log(f"  -> no persona for tag={persona_tag!r}")

    # --- Shared identity framing (same as internal session agents) ---
    try:
        from webot.profiles import frame_session_identity
    except Exception:
        from src.webot.profiles import frame_session_identity
    persona_block = frame_session_identity(expert_name, persona_tag, persona)

    # --- User profile + team-scoped skill / workflow injection (unconditional, matches internal) ---
    profile_block = ""
    workflow_prompt = ""
    skills_listing = ""
    skills_prompt = ""
    try:
        try:
            from webot.workflow_prompt import build_team_workflow_prompt
        except Exception:
            from src.webot.workflow_prompt import build_team_workflow_prompt
        workflow_prompt = build_team_workflow_prompt(user_id or "", team=team or "")
    except Exception as e:
        _log(f"  -> workflow prompt import/build error: {e}")
    try:
        try:
            from webot.skills import build_skills_prompt, build_user_profile_block, build_user_skills_listing
        except Exception:
            from src.webot.skills import build_skills_prompt, build_user_profile_block, build_user_skills_listing
        profile_block = build_user_profile_block(user_id or "")
        skills_listing = build_user_skills_listing(user_id or "", team=team or "", tool_mode="cli")
        skills_prompt = build_skills_prompt(user_id or "", team=team or "", tool_mode="cli")
    except Exception as e:
        _log(f"  -> skills prompt import/build error: {e}")

    parts = [persona_block, profile_block, skills_listing, skills_prompt, workflow_prompt]
    result = "\n\n".join(p for p in parts if p).strip()
    _log(f"  -> return {len(result)} chars")
    return result
