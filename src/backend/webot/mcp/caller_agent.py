"""Headers for a tool calling the Agent service on behalf of its user.

A tool runs inside a WeBot session (``username`` / ``source_session`` are
injected); the session is the calling agent, its id the agent id.
"""

from __future__ import annotations

import os


def internal_headers(username: str) -> dict[str, str]:
    token = os.getenv("INTERNAL_TOKEN", "")
    return {"Authorization": f"Bearer {token}:{username}", "X-Internal-Token": token}
