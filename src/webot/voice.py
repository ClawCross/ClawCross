"""
WeBot voice-mode capability helpers.
"""

from __future__ import annotations

import os
from typing import Any

from services.llm_factory import get_provider_audio_defaults, infer_provider
from webot.runtime_store import get_voice_state as load_voice_state


def get_voice_state(user_id: str, session_id: str = "") -> dict[str, Any]:
    api_key = os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "")
    provider = infer_provider(
        model=os.getenv("LLM_MODEL", ""),
        base_url=base_url,
        provider=os.getenv("LLM_PROVIDER", ""),
        api_key=api_key,
    )
    defaults = get_provider_audio_defaults(provider)
    stored = load_voice_state(user_id, session_id or "default")
    tts_model = stored.tts_model or os.getenv("TTS_MODEL", "").strip() or defaults.get("tts_model", "")
    tts_voice = stored.tts_voice or os.getenv("TTS_VOICE", "").strip() or defaults.get("tts_voice", "")
    stt_model = stored.stt_model or os.getenv("STT_MODEL", "").strip() or defaults.get("stt_model", "")
    enabled = bool(stored.enabled)
    return {
        "enabled": enabled,
        "status": "enabled" if enabled else "disabled",
        "provider": provider or "openai",
        "recording_supported": bool(stored.recording_supported),
        "browser_capture": True,
        "audio_upload_supported": True,
        "tts_available": bool(tts_model),
        "auto_read_aloud": bool(stored.auto_read_aloud),
        "tts_model": tts_model,
        "tts_voice": tts_voice,
        "stt_model": stt_model,
        "last_transcript": stored.last_transcript,
        "metadata": dict(stored.metadata),
        "max_pending_recordings": 2,
        "max_audio_bytes": 25 * 1024 * 1024,
        "tts_proxy_path": "/proxy_tts",
        "session_id": session_id,
        "user_id": user_id,
    }
