"""FastAPI routes for cross-computer agent harness state."""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Header, HTTPException

from api.harness_models import HarnessEventRequest, HarnessOpenCliRunRequest
from harness.opencli_bridge import get_opencli_status, run_opencli_command
from harness.store import apply_harness_event, get_harness_state


def create_harness_router(
    *,
    verify_auth_or_token: Callable[[str, str, str | None], None],
) -> APIRouter:
    router = APIRouter()

    @router.get("/harness/state")
    async def read_harness_state(
        user_id: str,
        password: str = "",
        x_internal_token: str | None = Header(None),
    ):
        verify_auth_or_token(user_id, password, x_internal_token)
        return get_harness_state(user_id)

    @router.post("/harness/event")
    async def write_harness_event(
        req: HarnessEventRequest,
        x_internal_token: str | None = Header(None),
    ):
        verify_auth_or_token(req.user_id, req.password, x_internal_token)
        try:
            return apply_harness_event(req.user_id, req.model_dump(exclude_none=True, exclude_defaults=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/harness/opencli/status")
    async def read_opencli_status(
        user_id: str,
        password: str = "",
        query: str = "",
        x_internal_token: str | None = Header(None),
    ):
        verify_auth_or_token(user_id, password, x_internal_token)
        return get_opencli_status(query=query)

    @router.post("/harness/opencli/run")
    async def run_opencli(
        req: HarnessOpenCliRunRequest,
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
