"""Runtimes for agents that live outside ClawCross.

* ``acp``      — Codex, Claude Code, Gemini and other ACP tools, through the acpx CLI;
* ``openclaw`` — an OpenClaw agent, over its OpenAI-compatible gateway;
* ``http``     — any OpenAI-compatible endpoint;
* ``llm``      — a single model call (a temporary persona, nothing kept).

Each is a ``agents.runtime.Runtime``. Inside the runtime every agent's session is
named after its id (``session.runtime_session``).

``openclaw_routes`` (over ``openclaw_config``) manages the OpenClaw agents themselves
— the ones an ``openclaw`` agent's ``global_name`` names.
"""
