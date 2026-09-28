"""L1 · the unified agent layer.

Every agent this machine can reach — WeBot sessions, acpx-driven agents
(codex, claude code, gemini, …), OpenClaw and other OpenAI-compatible HTTP
agents — is one record in the store with a stable ``ag_…`` id, and is driven
through one single-agent interface (``gateway.AgentGateway``).

This layer knows nothing about teams, group chats or OASIS: those compose
agents on top of it. Callers above it pass team context in, opaquely.
"""
