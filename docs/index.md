# Clawcross Documentation Index

This repository uses a **progressive-disclosure** documentation layout:

1. `AGENTS.md` is the entrypoint for AI agents (behavior rules, task routing, progressive disclosure).
2. `SKILL.md` is the complete install, configure, and debug guide.
3. This file routes the current task to the right deep-dive doc.
4. `repo-index.md` is the codebase map when you need to inspect or edit files.

If you are an agent, do **not** load everything by default. Start with `AGENTS.md` and read only the docs needed for the current task.

## Entry Points

| File | Use It For |
|---|---|
| [`../AGENTS.md`](../AGENTS.md) | Agent behavior rules, task routing, progressive disclosure protocol |
| [`../SKILL.md`](../SKILL.md) | Complete installation, configuration, debug, and troubleshooting guide |
| [`../README.md`](../README.md) | Product overview, positioning, big-picture capabilities |
| [`repo-index.md`](./repo-index.md) | Codebase indexing and file lookup before editing code |

## Read By Task

| Task | Read First | Then Read |
|---|---|---|
| Install / configure / start Clawcross | [`../SKILL.md`](../SKILL.md) | [`ports.md`](./ports.md) if ports matter |
| Understand the platform | [`overview.md`](./overview.md) | [`../README.md`](../README.md) |
| Understand WeBot agent runtime, subagents, delegated tool boundaries, runtime policy hooks, or the subagent panel | [`webot-agent-runtime.md`](./webot-agent-runtime.md) | [`runtime-reference.md`](./runtime-reference.md), [`repo-index.md`](./repo-index.md) |
| Compare WeBot with Claude Code and inspect the runtime feature matrix that drives parity work | [`webot-claude-gap-analysis.md`](./webot-claude-gap-analysis.md) | [`webot-agent-runtime.md`](./webot-agent-runtime.md), [`repo-index.md`](./repo-index.md) |
| Build a Team from a task description or discovered SOP pages | [`team-creator.md`](./team-creator.md) | [`build_team.md`](./build_team.md), [`example_team.md`](./example_team.md) |
| Understand OASIS runtime behavior, Town Mode, GraphRAG memory, or ReportAgent | [`oasis-reference.md`](./oasis-reference.md) | [`runtime-reference.md`](./runtime-reference.md), [`create_workflow.md`](./create_workflow.md), [`build_team.md`](./build_team.md) |
| Build Python-script OASIS workflows, call team agents from Python, or post into OASIS topics from code | [`workflowpy.md`](./workflowpy.md) | [`oasis-reference.md`](./oasis-reference.md), [`create_workflow.md`](./create_workflow.md), [`repo-index.md`](./repo-index.md) |
| Understand runtime architecture / auth / services | [`runtime-reference.md`](./runtime-reference.md) | [`ports.md`](./ports.md), [`repo-index.md`](./repo-index.md) |
| Inspect or extend MCP web search tools | [`mcp-search.md`](./mcp-search.md) | [`runtime-reference.md`](./runtime-reference.md), [`repo-index.md`](./repo-index.md) |
| Find CLI syntax or examples | [`cli.md`](./cli.md) | `uv run scripts/cli.py <command> --help` |
| Build a Team | [`build_team.md`](./build_team.md) | [`example_team.md`](./example_team.md) |
| Convert a workflow canvas into a Team | [`team-creator.md`](./team-creator.md) | [`build_team.md`](./build_team.md), [`create_workflow.md`](./create_workflow.md) |
| Create / debug workflow YAML | [`create_workflow.md`](./create_workflow.md) | [`example_team.md`](./example_team.md) |
| Configure OpenClaw or external agents | [`openclaw-commands.md`](./openclaw-commands.md) | [`build_team.md`](./build_team.md) |
| Configure or debug ACP / acpx (external agent communication) | [`runtime-reference.md`](./runtime-reference.md) | [`build_team.md`](./build_team.md), [`oasis-reference.md`](./oasis-reference.md), [`repo-index.md`](./repo-index.md) |
| Configure TinyFish internet search agent | [`tinyfish-monitor.md`](./tinyfish-monitor.md) | [`runtime-reference.md`](./runtime-reference.md), [`repo-index.md`](./repo-index.md) |
| Inspect ports, proxies, or service boundaries | [`ports.md`](./ports.md) | [`repo-index.md`](./repo-index.md) |

## Document Groups

### Product / Orientation

- [`overview.md`](./overview.md): brief explanation of what Clawcross is and how people use it
- [`../README.md`](../README.md): user-facing overview, highlights, and public-facing narrative

### Operator / Builder Guides

- [`cli.md`](./cli.md): command catalog
- [`team-creator.md`](./team-creator.md): ClawCross Creator flow, jobs, bilingual UI, workflow-to-team bridge
- [`build_team.md`](./build_team.md): Team creation, internal agents, OpenClaw members, personas
- [`create_workflow.md`](./create_workflow.md): workflow YAML grammar and examples
- [`workflowpy.md`](./workflowpy.md): Python-script workflow mode, agent center, and forum posting helpers
- [`mcp-search.md`](./mcp-search.md): MCP web search tools, structured JSON search, page fetch, filters, and safety limits
- [`oasis-reference.md`](./oasis-reference.md): OASIS runtime model, Town Mode, swarm / GraphRAG behavior, ReportAgent
- [`webot-agent-runtime.md`](./webot-agent-runtime.md): WeBot delegated subagent runtime, profiles, tool boundaries, and the runtime DTO wiring
- [`webot-claude-gap-analysis.md`](./webot-claude-gap-analysis.md): feature matrix vs Claude Code and the outstanding parity checklist
- [`example_team.md`](./example_team.md): concrete file layout for a Team
- [`openclaw-commands.md`](./openclaw-commands.md): OpenClaw command and config reference
- [`tinyfish-monitor.md`](./tinyfish-monitor.md): TinyFish internet search agent, live crawl, and data persistence
- [`ports.md`](./ports.md): ports, proxy routes, exposure rules

### Maintainer / Developer Docs

- [`runtime-reference.md`](./runtime-reference.md): runtime architecture, API surface, and the WeBot control-plane map
- [`repo-index.md`](./repo-index.md): where code and data live

## Suggested Agent Reading Paths

### Install and run locally

1. Read [`../SKILL.md`](../SKILL.md)
2. If you need the service map, read [`ports.md`](./ports.md)
3. If setup scripts must be edited, read [`repo-index.md`](./repo-index.md)

### Build a Team and run workflows

1. Read [`build_team.md`](./build_team.md)
2. Read [`create_workflow.md`](./create_workflow.md)
3. Read [`oasis-reference.md`](./oasis-reference.md)
4. Read [`example_team.md`](./example_team.md)
5. If behavior looks wrong, inspect the indexed runtime files in [`repo-index.md`](./repo-index.md)

### Use ClawCross Creator or Generate Team from Workflow

1. Read [`team-creator.md`](./team-creator.md)
2. If the output Team shape matters, read [`build_team.md`](./build_team.md)
3. If the source is a workflow graph, read [`create_workflow.md`](./create_workflow.md)
4. If UI behavior looks wrong, inspect the indexed frontend files in [`repo-index.md`](./repo-index.md)

### Diagnose OpenClaw issues

1. Read [`openclaw-commands.md`](./openclaw-commands.md)
2. Read the OpenClaw section in [`build_team.md`](./build_team.md)
3. Inspect `oasis/openclaw_routes.py`, `oasis/openclaw_cli.py`, and related scripts via [`repo-index.md`](./repo-index.md)

### Operate TinyFish monitoring

1. Read [`tinyfish-monitor.md`](./tinyfish-monitor.md)
2. Read [`runtime-reference.md`](./runtime-reference.md) if you need the service or storage map
3. Inspect the indexed TinyFish files in [`repo-index.md`](./repo-index.md) before editing code

### Modify the codebase

1. Read [`repo-index.md`](./repo-index.md)
2. Read the topic doc that matches the area you are changing
3. Open only the files indexed for that subsystem

### Extend WeBot Agent Capabilities

1. Read [`webot-agent-runtime.md`](./webot-agent-runtime.md)
2. Read [`runtime-reference.md`](./runtime-reference.md)
3. Read [`repo-index.md`](./repo-index.md)
4. Open the indexed WeBot runtime and MCP files only as needed

## Current Structure Rationale

The important split is:

- `AGENTS.md`: agent behavior rules, task routing, progressive disclosure protocol
- `SKILL.md`: complete install, configure, debug, and troubleshooting guide
- `README.md`: product story for human users
- `docs/*.md`: task-specific reference
- `docs/repo-index.md`: code and data index

This keeps `AGENTS.md` short enough for agents while making the full repository discoverable via `SKILL.md` and topic docs.
