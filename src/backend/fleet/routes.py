"""FastAPI routes for cross-computer agent fleet state."""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Header, HTTPException

from fleet.models import FleetEventRequest, FleetOpenCliRunRequest
from fleet.opencli_bridge import get_opencli_status, run_opencli_command
from fleet.store import apply_fleet_event, get_fleet_state


def create_fleet_router(
    *,
    verify_auth_or_token: Callable[[str, str, str | None], None],
) -> APIRouter:
    router = APIRouter()

    @router.get("/fleet/state")
    async def read_fleet_state(
        user_id: str,
        password: str = "",
        x_internal_token: str | None = Header(None),
    ):
        verify_auth_or_token(user_id, password, x_internal_token)
        return get_fleet_state(user_id)

    @router.post("/fleet/event")
    async def write_fleet_event(
        req: FleetEventRequest,
        x_internal_token: str | None = Header(None),
    ):
        verify_auth_or_token(req.user_id, req.password, x_internal_token)
        # Use model_fields_set (only fields the caller actually sent) instead of
        # exclude_defaults=True — otherwise a real value that happens to equal
        # the field's default (e.g. project_id="default") would be silently
        # dropped before reaching the store.
        dumped = req.model_dump(exclude_none=True)
        sent = req.model_fields_set | {"user_id"}
        event = {k: v for k, v in dumped.items() if k in sent}
        try:
            return apply_fleet_event(req.user_id, event)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/fleet/opencli/status")
    async def read_opencli_status(
        user_id: str,
        password: str = "",
        query: str = "",
        x_internal_token: str | None = Header(None),
    ):
        verify_auth_or_token(user_id, password, x_internal_token)
        return get_opencli_status(query=query)

    @router.post("/fleet/opencli/run")
    async def run_opencli(
        req: FleetOpenCliRunRequest,
        x_internal_token: str | None = Header(None),
    ):
        verify_auth_or_token(req.user_id, req.password, x_internal_token)
        try:
            return run_opencli_command(
                req.args,
                timeout_seconds=req.timeout_seconds,
                max_output_chars=req.max_output_chars,
                profile=req.profile,
                allow_mutating=req.allow_mutating,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
