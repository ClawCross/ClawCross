"""Agent tool-table semantics: None means all, [] means none, lists select tools."""

MANAGEMENT_TOOLS = frozenset({'manage_team', 'manage_group', 'manage_agent_alarms', 'send_to_session'})
REMOVED_TOOLS = frozenset({'call_llm_api'})
