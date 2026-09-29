"""HTTP surface for teams.

    GET    /v1/teams                              teams with members and lead
    POST   /v1/teams                              create {team}
    GET    /v1/teams/{team}                       one team
    PATCH  /v1/teams/{team}                       rename {name}
    DELETE /v1/teams/{team}                       delete (agents stay)
    POST   /v1/teams/{team}/members               add {agent, role?, is_lead?}
    PATCH  /v1/teams/{team}/members/{agent}       {role?, is_lead?}
    DELETE /v1/teams/{team}/members/{agent}       remove from the team (the agent stays)
    POST   /v1/teams/{team}/import                import the manifest files in the team folder

A member is reached like any agent, by its id or as ``<team>.<name>``.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from agents.routes import agent_card, authenticate
from teams.manifest import import_folder
from teams.store import Member, TeamNotFound, TeamStore, valid_team_name


class TeamCreate(BaseModel):
    team: str


class TeamRename(BaseModel):
    name: str


class MemberAdd(BaseModel):
    agent: str
    role: str = ""
    is_lead: bool = False
    tag: str = ""            # the team persona it wears (its text is the agent's own copy)


class MemberPatch(BaseModel):
    role: str | None = None
    is_lead: bool | None = None
    tag: str | None = None


def member_card(m: Member) -> dict[str, Any]:
    """``tag``: the team persona the member was made with, if any."""
    return {"agent": agent_card(m.agent), "role": m.role, "is_lead": m.is_lead, "tag": m.extra.get("tag", "")}


def team_card(teams: TeamStore, owner: str, team: str) -> dict[str, Any]:
    members = teams.members(owner, team)
    lead = next((m for m in members if m.is_lead), None)
    return {
        "team": team,
        "lead": lead.agent.agent_id if lead else None,
        "members": [member_card(m) for m in members],
    }


def create_teams_router(
    *,
    internal_token: str,
    verify_password: Callable[[str, str], bool],
    teams: TeamStore,
) -> APIRouter:
    router = APIRouter()

    def user_of(authorization: str | None) -> str:
        return authenticate(authorization, internal_token=internal_token, verify_password=verify_password)

    def require(user: str, team: str) -> None:
        try:
            teams.require(user, team)
        except (TeamNotFound, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    def agent_id(user: str, ref: str) -> str:
        agent = teams.agents.get(user, ref) or teams.address(user, ref)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"no agent {ref!r}")
        return agent.agent_id

    @router.get("/v1/teams")
    async def list_teams(authorization: str | None = Header(None)):
        user = user_of(authorization)
        return {"object": "list", "data": [team_card(teams, user, team) for team in teams.teams(user)]}

    @router.post("/v1/teams")
    async def create_team(body: TeamCreate, authorization: str | None = Header(None)):
        user = user_of(authorization)
        if not valid_team_name(body.team):
            raise HTTPException(status_code=400, detail="invalid team name")
        if teams.exists(user, body.team):
            raise HTTPException(status_code=409, detail="team already exists")
        teams.create(user, body.team.strip())
        return team_card(teams, user, body.team.strip())

    @router.post("/v1/teams/{team}/members")
    async def add_member(team: str, body: MemberAdd, authorization: str | None = Header(None)):
        user = user_of(authorization)
        require(user, team)
        member = teams.add(user, team, agent_id(user, body.agent), role=body.role, is_lead=body.is_lead,
                           extra={"tag": body.tag} if body.tag else None)
        return member_card(member)

    @router.patch("/v1/teams/{team}/members/{ref:path}")
    async def update_member(team: str, ref: str, body: MemberPatch, authorization: str | None = Header(None)):
        user = user_of(authorization)
        require(user, team)
        try:
            member = teams.update(user, team, agent_id(user, ref), role=body.role, is_lead=body.is_lead, tag=body.tag)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return member_card(member)

    @router.delete("/v1/teams/{team}/members/{ref:path}")
    async def remove_member(team: str, ref: str, authorization: str | None = Header(None)):
        user = user_of(authorization)
        require(user, team)
        teams.remove(user, team, agent_id(user, ref))
        return team_card(teams, user, team)

    @router.post("/v1/teams/{team}/import")
    async def import_team(team: str, authorization: str | None = Header(None)):
        user = user_of(authorization)
        require(user, team)
        try:
            import_folder(teams, user, team)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return team_card(teams, user, team)

    @router.get("/v1/teams/{team}")
    async def describe_team(team: str, authorization: str | None = Header(None)):
        user = user_of(authorization)
        require(user, team)
        return team_card(teams, user, team)

    @router.patch("/v1/teams/{team}")
    async def rename_team(team: str, body: TeamRename, authorization: str | None = Header(None)):
        user = user_of(authorization)
        require(user, team)
        if not valid_team_name(body.name):
            raise HTTPException(status_code=400, detail="invalid team name")
        try:
            teams.rename(user, team, body.name.strip())
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return team_card(teams, user, body.name.strip())

    @router.delete("/v1/teams/{team}")
    async def delete_team(team: str, authorization: str | None = Header(None)):
        user = user_of(authorization)
        require(user, team)
        teams.delete(user, team)
        return {"deleted": team}

    return router
