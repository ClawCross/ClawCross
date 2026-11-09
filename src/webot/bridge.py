"""
WeBot browser-native bridge / direct-connect helpers.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from starlette.websockets import WebSocket

from webot.runtime_store import get_bridge_session, list_bridge_sessions, upsert_bridge_session


class WeBotBridgeHub:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, record, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.setdefault(record.bridge_id, set()).add(websocket)
            upsert_bridge_session(
                bridge_id=record.bridge_id,
                user_id=record.user_id,
                session_id=record.session_id,
                role=record.role,
                label=record.label,
                attach_code=record.attach_code,
                websocket_path=record.websocket_path,
                status="attached",
                connection_count=len(self._connections[record.bridge_id]),
                metadata=record.metadata,
                last_error="",
                last_attached_at=record.last_attached_at or record.updated_at,
            )

    async def disconnect(self, record, websocket: WebSocket) -> None:
        async with self._lock:
            bucket = self._connections.get(record.bridge_id)
            if bucket and websocket in bucket:
                bucket.remove(websocket)
                if not bucket:
                    self._connections.pop(record.bridge_id, None)
            connection_count = len(self._connections.get(record.bridge_id, set()))
        upsert_bridge_session(
            bridge_id=record.bridge_id,
            user_id=record.user_id,
            session_id=record.session_id,
            role=record.role,
            label=record.label,
            attach_code=record.attach_code,
            websocket_path=record.websocket_path,
            status="attached" if connection_count else "detached",
            connection_count=connection_count,
            metadata=record.metadata,
            last_error="",
            last_attached_at=record.last_attached_at or record.updated_at,
        )

    async def publish(self, bridge_id: str, payload: dict[str, Any]) -> int:
        async with self._lock:
            targets = list(self._connections.get(bridge_id, set()))
        if not targets:
            return 0
        delivered = 0
        for socket in targets:
            try:
                await socket.send_text(json.dumps(payload, ensure_ascii=False))
                delivered += 1
            except Exception:
                continue
        return delivered

    async def publish_to_session(self, user_id: str, session_id: str, payload: dict[str, Any]) -> int:
        delivered = 0
        for record in list_bridge_sessions(user_id, session_id, limit=20):
            delivered += await self.publish(record.bridge_id, payload)
        return delivered


bridge_hub = WeBotBridgeHub()


def issue_bridge_session(
    *,
    user_id: str,
    session_id: str,
    role: str = "viewer",
    label: str = "",
    websocket_prefix: str = "/webot/ws",
) -> dict[str, Any]:
    bridge_id = f"bridge-{uuid.uuid4().hex[:12]}"
    attach_code = uuid.uuid4().hex[:6].upper()
    websocket_path = f"{websocket_prefix}/{user_id}/{bridge_id}"
    record = upsert_bridge_session(
        bridge_id=bridge_id,
        user_id=user_id,
        session_id=session_id,
        role=role,
        label=label or session_id,
        attach_code=attach_code,
        websocket_path=websocket_path,
        status="detached",
        connection_count=0,
        metadata={"session_id": session_id},
    )
    return serialize_bridge_record(record)


def serialize_bridge_record(record) -> dict[str, Any]:
    return {
        "bridge_id": record.bridge_id,
        "session_id": record.session_id,
        "role": record.role,
        "label": record.label,
        "attach_code": record.attach_code,
        "websocket_path": record.websocket_path,
        "status": record.status,
        "connection_count": record.connection_count,
        "last_error": record.last_error,
        "last_attached_at": record.last_attached_at,
        "metadata": dict(record.metadata),
        "updated_at": record.updated_at,
        "created_at": record.created_at,
    }


def get_bridge_runtime_payload(user_id: str, session_id: str) -> dict[str, Any]:
    records = [serialize_bridge_record(record) for record in list_bridge_sessions(user_id, session_id, limit=20)]
    primary = records[0] if records else {}
    roles = sorted({str(item.get("role") or "viewer") for item in records})
    connection_count = sum(int(item.get("connection_count") or 0) for item in records)
    return {
        "sessions": records,
        "attached": any(item["connection_count"] > 0 for item in records),
        "status": "attached" if any(item["connection_count"] > 0 for item in records) else "detached",
        "primary": primary,
        "session_key": str(primary.get("bridge_id") or ""),
        "attach_code": str(primary.get("attach_code") or ""),
        "roles": roles,
        "connected_clients": connection_count,
        "connection_count": connection_count,
        "session_count": len(records),
    }


def get_bridge_record_for_user(user_id: str, bridge_id: str):
    return get_bridge_session(bridge_id, user_id)
