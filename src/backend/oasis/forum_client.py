from __future__ import annotations

import os
from typing import Any

import httpx


def _oasis_base_url() -> str:
    return os.getenv("OASIS_BASE_URL", "http://127.0.0.1:51202")


async def create_empty_topic(
    *,
    question: str,
    user_id: str,
    max_rounds: int = 1,
    team: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "question": question,
        "user_id": user_id,
        "max_rounds": max_rounds,
        "allow_empty": True,
    }
    if team:
        payload["team"] = team

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{_oasis_base_url().rstrip('/')}/topics",
            json=payload,
        )
    resp.raise_for_status()
    return resp.json()


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


async def conclude_topic(
    *,
    topic_id: str,
    user_id: str,
    conclusion: str,
    author: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "user_id": user_id,
        "conclusion": conclusion,
    }
    if author:
        payload["author"] = author

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{_oasis_base_url().rstrip('/')}/topics/{topic_id}/conclude",
            json=payload,
        )
    resp.raise_for_status()
    return resp.json()


async def vote_topic_post(
    *,
    topic_id: str,
    user_id: str,
    post_id: int,
    direction: str,
    voter: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "user_id": user_id,
        "post_id": post_id,
        "direction": direction,
    }
    if voter:
        payload["voter"] = voter

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{_oasis_base_url().rstrip('/')}/topics/{topic_id}/vote",
            json=payload,
        )
    resp.raise_for_status()
    return resp.json()
