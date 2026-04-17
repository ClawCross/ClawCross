from __future__ import annotations

import os
from typing import Any

import httpx


def _oasis_base_url() -> str:
    return os.getenv("OASIS_BASE_URL", "http://127.0.0.1:51202")


async def publish_to_topic(
    *,
    topic_id: str,
    user_id: str,
    author: str,
    content: str,
    reply_to: int | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "user_id": user_id,
        "author": author,
        "content": content,
    }
    if reply_to is not None:
        payload["reply_to"] = reply_to

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{_oasis_base_url().rstrip('/')}/topics/{topic_id}/posts",
            json=payload,
        )
    resp.raise_for_status()
    return resp.json()

