"""Resolve trusted delegation records without accepting model-supplied parents."""

from webot.profiles import parse_subagent_session_id
from webot.subagents import get_subagent_by_session


def parent_sessions(user_id: str, session_id: str) -> list[str]:
    """Nearest parent first; malformed/cyclic delegation fails closed."""
    parents = []
    seen = {session_id}
    current = session_id
    while parse_subagent_session_id(current):
        record = get_subagent_by_session(current, user_id)
        if record is None or not record.parent_session:
            break
        current = record.parent_session
        if current in seen or len(parents) >= 32:
            raise ValueError("Invalid subagent permission inheritance chain")
        seen.add(current)
        parents.append(current)
    return parents


def intersect_domains(parent: list[str], child: list[str]) -> list[str]:
    """Exact hosts allow any port; a host:port entry narrows that permission."""
    result = []
    for first in parent:
        first_host, _, first_port = first.partition(":")
        for second in child:
            second_host, _, second_port = second.partition(":")
            if first_host != second_host or (first_port and second_port and first_port != second_port):
                continue
            entry = first_host + (":" + (first_port or second_port) if first_port or second_port else "")
            if entry not in result:
                result.append(entry)
    return result
