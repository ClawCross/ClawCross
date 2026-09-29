from __future__ import annotations

import os as _os

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


def build_external_persona_prompt(persona: str = "", *, name: str = "", user_id: str = "", team: str = "") -> str:
    """An external agent's identity for first-prompt injection: its own persona text.

    Persona framing and skill listing mirror the internal session agent
    (webot.profiles.frame_session_identity / webot.skills.build_user_skills_listing),
    so internal and external agents share one source of truth. Skill and workflow
    blocks are injected whether or not the agent has a persona, matching internal
    agents which inject skills unconditionally.
    """
    _log(f"CALL name={name!r} uid={user_id!r} team={team!r} persona={len(persona or '')} chars")
    try:
        from webot.profiles import frame_session_identity
    except Exception:
        from src.webot.profiles import frame_session_identity
    persona_block = frame_session_identity(name, "", str(persona or "").strip())

    # --- User profile + team-scoped skill / workflow injection (unconditional, matches internal) ---
    profile_block = ""
    workflow_prompt = ""
    skills_listing = ""
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
            from webot.skills import build_user_profile_block, build_user_skills_listing
        except Exception:
            from src.webot.skills import build_user_profile_block, build_user_skills_listing
        profile_block = build_user_profile_block(user_id or "")
        skills_listing = build_user_skills_listing(user_id or "", team=team or "", tool_mode="cli")
    except Exception as e:
        _log(f"  -> skills prompt import/build error: {e}")

    parts = [persona_block, profile_block, skills_listing, workflow_prompt]
    result = "\n\n".join(p for p in parts if p).strip()
    _log(f"  -> return {len(result)} chars")
    return result
