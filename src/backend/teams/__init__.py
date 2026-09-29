"""L3 · teams: named sets of agents, one of which may lead.

A team owns no agent. Membership (agent id, role, lead) is stored next to the
agents; the team folder holds assets. ``manifest`` reads and writes the team
package format (``internal_agents.json`` / ``external_agents.json``).
"""
