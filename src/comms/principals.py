"""Who a conversation member or message sender is, as a principal id."""

from __future__ import annotations

from agents.registry import DRIVER_WEBOT, AgentRecord, AgentRegistry

HUMAN_PREFIX = "u:"


def human_principal(user_id: str) -> str:
    return f"{HUMAN_PREFIX}{user_id}"


def is_human_principal(principal_id: str) -> bool:
    return (principal_id or "").startswith(HUMAN_PREFIX)


def member_agent(registry: AgentRegistry, owner: str, member: dict) -> AgentRecord | None:
    """The agent a conversation member row stands for.

    External members must be declared in the owner's agent files. A WeBot
    session that was added to a group without being declared anywhere still
    receives messages: it gets a transient record (no ``agent_id``).
    """
    if not bool(member.get("is_agent", True)) or (member.get("member_type") or "") == "owner":
        return None
    global_id = str(member.get("global_id") or "").strip()
    if not global_id:
        return None
    if (member.get("member_type") or "") == "ext":
        return registry.external(owner, global_id)
    record = registry.webot_session(owner, global_id)
    if record is not None:
        return record
    return AgentRecord(
        agent_id="",
        owner=owner,
        handle=global_id,
        display_name=str(member.get("short_name") or global_id),
        driver=DRIVER_WEBOT,
        binding={"session": global_id},
    )


def member_principal(registry: AgentRegistry, owner: str, member: dict) -> str:
    """``u:<user>`` for a human member, ``ag_…`` for a registered agent, "" otherwise."""
    if not bool(member.get("is_agent", True)) or (member.get("member_type") or "") == "owner":
        return human_principal(str(member.get("user_id") or member.get("global_id") or ""))
    record = member_agent(registry, owner, member)
    return record.agent_id if record else ""
