"""Runtimes for agents that live outside ClawCross.

* ``acp``      — Codex, Claude Code, Gemini, OpenClaw and other ACP tools, through the acpx CLI;
* ``http``     — any OpenAI-compatible endpoint;
* ``llm``      — a single model call (a temporary persona, nothing kept).

Each is a ``agents.runtime.Runtime``. Inside the runtime every agent's session is
named after its id (``session.runtime_session``).
"""
