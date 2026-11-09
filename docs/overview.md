# Overview

This page holds the higher-level product information that does not need to live in `README.md` or `SKILL.md`.

## What Clawcross Is

Clawcross is an OpenAI-compatible AI agent system with:

- a local `/v1/chat/completions` endpoint
- a WeBot delegated-agent runtime with profile-based subagents
- a built-in multi-persona orchestration engine called OASIS
- a ClawCross Studio sidebar for OASIS Town, swarm graphs, and ReportAgent
- living GraphRAG memory per topic, backed by local SQLite and optional Zep mirroring
- an AI-assisted ClawCross Creator that can turn task descriptions or SOP pages into Teams
- lightweight built-in agents for files, commands, and messaging
- ACP exchange (acpx) for communicating with external AI agents (OpenClaw, Codex, Claude, Gemini, Aider) via the Agent Client Protocol
- optional integrations such as OpenClaw, TinyFish internet search agent, Telegram, QQ, and Cloudflare Tunnel

## Core Concepts

### Team

A Team combines:

- agents
- personas (expert persona prompts — special prompts that define identity, not separate agents)
- workflows

This lets Clawcross move beyond single-agent chat into structured collaboration.

### OASIS

OASIS is the workflow engine behind coordinated persona-driven discussions and execution flows. It supports:

- sequential steps
- parallel branches
- conditional routing
- loops
- DAG-style dependency graphs
- Town Genesis swarm blueprints and graph-backed prediction topics
- ReportAgent explanations based on graph evidence instead of raw post replay

## Main Capabilities

- OpenAI-compatible chat API
- ClawCross Creator for task-to-team drafting and workflow preview
- OASIS Town in the ClawCross Studio sidebar
- GraphRAG long-term memory and report queries
- multi-session and multi-user isolation
- profile-bound delegated subagents for research / planning / coding / review / verification
- ACP exchange (acpx) for external agent communication in group chat and OASIS workflows
- Web UI on the local machine
- scheduled tasks
- internet search via TinyFish
- optional bot integration
- optional public exposure through Cloudflare Tunnel

## Typical Usage Modes

### Local assistant

Run Clawcross on your own machine and use the local Web UI or the OpenAI-compatible API.

### Multi-persona orchestration

Create teams of personas and define workflows that debate, vote, summarize, or execute staged tasks.

### AI-assisted team drafting

Use ClawCross Creator to discover public SOP pages, extract role definitions with TinyFish, and generate a draft Team plus DAG workflow before importing it into Clawcross.

### Integration hub

Use Clawcross as a bridge layer for bots, external tools, or other OpenAI-compatible clients.

## Where To Go Next

- Documentation map: [index.md](./index.md)
- Codebase map: [repo-index.md](./repo-index.md)
- Installation, Windows / WSL setup, startup expectations, access notes, and audio setup: `SKILL.md`
- ClawCross Creator and workflow-to-team flow: [team-creator.md](./team-creator.md)
- OASIS runtime model and orchestration semantics: [oasis-reference.md](./oasis-reference.md)
- Runtime architecture and auth model: [runtime-reference.md](./runtime-reference.md)
- WeBot delegated-agent runtime: [webot-agent-runtime.md](./webot-agent-runtime.md)
- CLI usage: [cli.md](./cli.md)
- Ports and service map: [ports.md](./ports.md)
