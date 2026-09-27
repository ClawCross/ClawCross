"""HTTP surface for teams: a team is addressable like an agent, through its lead.

    GET  /v1/teams                    the caller's teams with members and lead
    GET  /v1/teams/{team}             one team
    POST /v1/teams/{team}/messages    ask (or deliver to) the team's lead, in team context
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException

from agents.gateway import AgentGateway, agent_card
from agents.messages import AgentMessage
from agents.routes import AgentMessageRequest, authenticate
from teams.view import TeamHasNoLead, TeamNotFound, TeamView


def team_card(view: TeamView, owner: str, team: str) -> dict[str, Any]:
    members = view.members(owner, team)
    lead = next((m for m in members if m.is_lead and m.agent is not None), None)
    return {
        "team": team,
        "address": f"{owner}/{team}",
        "lead": lead.agent.address if lead else None,
        "members": [
            {
                "role_name": m.role_name,
                "kind": m.kind,
                "tag": m.tag,
                "is_lead": m.is_lead,
                "agent": agent_card(m.agent) if m.agent else None,
            }
            for m in members
        ],
    }


def create_teams_router(
    *,
    internal_token: str,
    verify_password: Callable[[str, str], bool],
    gateway: AgentGateway | None = None,
) -> APIRouter:
    router = APIRouter()
    state: dict[str, Any] = {}

    def get_gateway() -> AgentGateway:
        if "gateway" not in state:
            state["gateway"] = gateway or AgentGateway(internal_token=internal_token)
        return state["gateway"]

    def get_view() -> TeamView:
        return TeamView(get_gateway().registry)

    def user_of(authorization: str | None) -> str:
        return authenticate(authorization, internal_token=internal_token, verify_password=verify_password)

    def require_team(user: str, team: str) -> None:
        if not get_view().exists(user, team):
            raise HTTPException(status_code=404, detail=f"no team {team!r}")

    @router.get("/v1/teams")
    async def list_teams(authorization: str | None = Header(None)):
        user = user_of(authorization)
        view = get_view()
        return {"object": "list", "data": [team_card(view, user, team) for team in view.teams(user)]}

    @router.post("/v1/teams/{team}/messages")
    async def message_team(team: str, body: AgentMessageRequest, authorization: str | None = Header(None)):
        user = user_of(authorization)
        require_team(user, team)
        try:
            lead = get_view().lead(user, team)
        except (TeamNotFound, TeamHasNoLead) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        msg = AgentMessage(text=body.text, attachments=body.attachments, sender=f"u:{user}", instructions=body.instructions)
        context = {**body.context, **TeamView.context(team)}
        if body.deliver:
            receipt = await get_gateway().deliver(user, lead.agent, msg, context=context, mode=body.mode)
            return {"team": team, "agent": agent_card(lead.agent), "accepted": receipt.accepted, "error": receipt.error}
        reply = await get_gateway().ask(
            user, lead.agent, msg,
            context=context,
            mode=body.mode,
            tools=body.tools,
            response_format=body.response_format,
            timeout=body.timeout,
        )
        return {"team": team, "agent": agent_card(lead.agent), "ok": reply.ok, "content": reply.content, "error": reply.error}

    @router.get("/v1/teams/{team}")
    async def describe_team(team: str, authorization: str | None = Header(None)):
        user = user_of(authorization)
        require_team(user, team)
        return team_card(get_view(), user, team)

    return router
