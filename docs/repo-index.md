# Clawcross Repository Index

Use this file when an agent needs to **index the repo before reading code**. It is a task-oriented map of the main files and directories.

## Fast Indexing Checklist

1. Confirm the task area: install, runtime, frontend, workflow, ACP agents, bots, or maintenance.
2. Read only the matching section below.
3. Open the referenced files for the subsystem you are changing.
4. Only expand outward if the first-hop files are insufficient.

## Top-Level Layout

| Path | What Lives Here |
|---|---|
| `SKILL.md` | Agent entrypoint and task router |
| `README.md` | Product overview |
| `docs/` | Task docs, maintainer docs, repo index |
| `launch/` | launch, environment preparation, runtime control, tunnel and runtime migration only |
| `src/cli/` | interactive CLI and API command interface |
| `src/cli/commands/` | CLI subcommands, profiles, pickers and API client |
| `src/backend/ops/setup/` | runtime configuration, user setup and the read-only OpenClaw LLM import |
| `tools/build/` | development and frontend/preset build tools |
| `tools/diagnostics/` | runtime inspection tools |
| `tools/maintenance/` | repository maintenance tools |
| `examples/` | API and OASIS examples, grouped by subsystem |
| `agents/` | standard skill interface metadata for root SKILL.md |
| `src/backend/` | every backend service, one package per module; the Python import root |
| `src/frontend/` | the web frontend: Flask server, proxies to the backend, templates, static assets |
| `config/` | `.env`, TinyFish target files, requirements, users |
| `data/` | runtime DBs, prompts, user files, workflow files |
| `test/` | automated Python, Node, and browser smoke tests |

`src/backend/` modules (imported by their name, e.g. `from agents.store import …`):

| Module | What it is |
|---|---|
| `server.py` | the Agent service (port 51200): every agent entrance and the routers below |
| `agents/` | L1: the table of all agents, the gateway, `/v1/agents`, `/v1/chat/completions`, `/system_trigger` |
| `external/` | the runtimes of external agents (ACP tools including OpenClaw, HTTP, model calls) |
| `webot/` | WeBot: `engine/`, `api/`, `mcp/` (its MCP tool servers), `driver.py` (its runtime) |
| `teams/` | teams: store, manifest (package format), Creator, presets, snapshots |
| `groups/` | independent group server, relay store/API, device client, legacy local group compatibility |
| `oasis/` | OASIS workflows (its own service, port 51202) |
| `scheduler/` | the scheduler service (port 51201), cron parsing, internal alarms, background-job notices |
| `channels/` | chat channel bridges (webhook, NoneBot, WeClaw) |
| `fleet/` | the cross-session fleet control plane |
| `ops/` | the Agent service's own operations: login, tools, TTS, settings, self-update |
| `tinyfish/` | TinyFish internet monitoring |
| `common/` | shared by all: runtime paths, env settings, logging, auth, the LLM factory |

## Install and Configuration

Read these first for setup or environment changes:

| Path | Purpose |
|---|---|
| `launch/run.sh` | Linux / macOS uv and Python bootstrap |
| `launch/run.ps1` | Windows uv and Python bootstrap |
| `launch/runtime_control.py` | shared Python start, stop, status, and tunnel lifecycle |
| `launch/environment.py` | core Python dependencies and explicit optional component installs |
| `config/requirements-channels.txt` | optional QQ, Telegram, and media dependencies |
| `src/backend/ops/setup/configure.py` | `.env` initialization and configuration logic |
| `src/backend/ops/setup/configure_openclaw.py` | read-only import of OpenClaw's LLM settings into `config/.env` |
| `config/.env.example` | config template and inline guidance |
| `config/tinyfish_targets.example.json` | example TinyFish search target schema |
| `src/backend/ops/setup/configure.py` | API key and model configuration |
| `tools/maintenance/evolve_skill.py` | repo-level Markdown skill self-evolution helper with strategy presets and validation artifacts |

If the issue is model detection or provider-specific behavior, inspect:

- `src/backend/common/llm_factory.py`
- `src/backend/ops/service.py`

## Runtime Entry Points

These are the main services Clawcross runs:

| Path | Service |
|---|---|
| `src/backend/server.py` | Agent API bootstrap and router composition |
| `src/backend/groups/server.py` | independent group relay and local compatibility (51203) |
| `src/backend/groups/admin.py` | host-only group administration |
| `src/frontend/server.py` | Flask frontend and proxy gateway |
| `src/backend/scheduler/service.py` | scheduler service |
| `src/backend/teams/creator.py` | ClawCross Creator discovery, extraction, build, jobs, and translation pipeline |
| `src/backend/tinyfish/monitor.py` | shared TinyFish monitor runtime used by frontend, scheduler, and CLI |
| `src/backend/oasis/server.py` | OASIS service |
| `launch/launcher.py` | multi-service startup order |

When the bug is "service does not start" or "route behaves unexpectedly", start from the matching entrypoint plus its route/service files below.

## Backend Module Map (`src/`)

### OpenAI-compatible chat API

- `src/backend/agents/openai.py` (`/v1/chat/completions`, `/v1/models`)
- `src/backend/webot/api/openai_service.py`
- `src/backend/webot/api/openai_models.py`
- `src/backend/webot/api/openai_protocol.py`
- `src/backend/webot/message_builder.py`

### Sessions

A WeBot session is its agent: listed, read, compacted and deleted through `/v1/agents`.

- `src/backend/webot/api/session_service.py` (the session's own operations, for its runtime)
- `src/backend/webot/session_summary.py`
- `src/backend/webot/checkpoint_repository.py`

### Agents and their compositions (see `docs/architecture.md`)

- L1 agents: `src/backend/agents/store.py` (the table of all sessions: session number = agent id), `src/backend/agents/gateway.py` (ask / trigger / inbox and the control plane, handed to the agent's runtime), `src/backend/agents/runtime.py` (what a runtime offers), `src/backend/webot/driver.py` and `src/backend/external/` (the runtimes: WeBot, acp, openclaw, http, llm), `src/backend/agents/routes.py` (`/v1/agents`), `src/backend/webot/api/openai_service.py` (`/v1/chat/completions`), `src/backend/agents/trigger.py` (`/system_trigger`)
- Network group protocol/client: `src/backend/groups/relay_store.py`, `relay_api.py`, `client.py`, `facade.py`; see `docs/group-network.md`.
- L2 group chat: `src/backend/groups/store.py` (conversations.db), `src/backend/groups/conversations.py` (post + wake), `src/backend/groups/delivery.py` (wake rule, storm guard, unread digest), `src/backend/groups/`
- L2 teams: `src/backend/teams/store.py` (members.json in the team folder, `<team>.<name>`), `src/backend/teams/manifest.py` (internal_agents.json / external_agents.json import/export), `src/backend/teams/routes.py` (`/v1/teams`)

### Settings / ops / auth / system

- `src/backend/ops/settings_routes.py`
- `src/backend/ops/settings_service.py`
- `src/backend/ops/settings_models.py`
- `src/backend/ops/routes.py`
- `src/backend/ops/service.py`
- `src/backend/ops/models.py`
- `src/backend/webot/api/system_service.py`
- `src/backend/webot/api/system_models.py`
- `src/backend/common/env_settings.py`
- `src/backend/common/user_auth.py`
- `src/backend/common/auth_utils.py`

### Runtime plumbing

- `src/backend/webot/engine/agent.py`
- `src/backend/webot/engine/agent_runtime_state.py`
- `src/backend/webot/skill_evolution.py`
- `src/backend/webot/skill_memory.py` — path-free Skill正文 entries via file tools in memory mode
- `src/backend/webot/context.py`
- `src/backend/webot/compression.py`
- `src/backend/webot/runtime_settings.py`
- `src/backend/webot/approval_review.py`
- `src/backend/webot/command_sandbox.py` — SRT/Landlock command isolation and scoped escalation policies
- `src/backend/webot/landlock_network.py` — temporary systemd network fence and per-command HTTP/SOCKS proxies
- `src/backend/webot/landlock_launcher.py` — inherited kernel restrictions and basic resource limits
- `src/backend/webot/approval_actions.py`
- `src/backend/webot/permission_context.py`
- `src/backend/webot/policy.py`
- `src/backend/webot/profiles.py`
- `src/backend/webot/api/routes.py`
- `src/backend/webot/runtime.py`
- `src/backend/webot/runtime_store.py`
- `src/backend/webot/api/service.py`
- `src/backend/webot/subagents.py`
- `src/backend/webot/workspace.py`
- `src/backend/common/logging_utils.py`

## Frontend Map

If the task touches the UI, start here:

Tool output windows are managed by `src/frontend/static/js/conversation-ui-panels.js`:
drag the title to move, drag the corner to resize, minimize or close using the title buttons,
and restore them through the Studio `+` → `对话面板` submenu. Minimize retains iframe state;
close removes its iframe and stops scripts. Layout is retained during session switches in
the current page, and cleared on logout. Tool code remains in an isolated sandbox iframe.

| Path | Purpose |
|---|---|
| `src/frontend/static/js/tool-catalog.js` | tool categories and grouped picker shared by desktop/mobile |
| `src/frontend/static/js/runtime-settings.js` | context usage meter and context/approval settings |
| `src/frontend/static/js/main.js` | main desktop frontend logic |
| `src/frontend/static/css/style.css` | main desktop styling, including OASIS Town / swarm / ReportAgent panels |
| `src/frontend/proxies/webot.py` | Flask proxy routes for WeBot runtime panel and tool policy |
| `src/frontend/static/js/creator.js` | ClawCross Creator page logic, i18n, persistence, DAG preview |
| `src/frontend/static/css/creator.css` | ClawCross Creator styles and DAG layout |
| `src/frontend/static/js/orchestration.js` | Studio canvas logic, including `Generate Team` |
| `src/frontend/templates/creator.html` | ClawCross Creator HTML shell |
| `src/frontend/templates/group_chat_mobile.html` | mobile group chat page and mobile settings UI |
| `src/frontend/templates/` | other HTML templates |
| `src/frontend/static/` | CSS, JS, images (served at `/static`) |
| `src/frontend/server.py` | the Flask frontend (port 51209) |
| `src/frontend/visual.py` | visual orchestration helpers (layout ↔ YAML, expert pool) |
| `src/frontend/proxies/groups.py` | frontend proxy routes for groups |
| `src/frontend/proxies/oasis.py` | frontend proxy routes for OASIS |
| `src/frontend/proxies/agents.py` | frontend proxy for the unified Agent catalog/control plane |

## OASIS and Workflow Engine

Read these for workflow execution, topics, and experts:

| Path | Purpose |
|---|---|
| `src/backend/oasis/server.py` | OASIS API bootstrap |
| `src/backend/oasis/engine.py` | discussion / execution engine |
| `src/backend/oasis/scheduler.py` | workflow scheduling logic |
| `src/backend/oasis/participants.py` | a participant = an agent asked by its id over the agent layer's entrances (`agents/client.py`) |
| `src/backend/oasis/agent_center.py` | the agents and personas a workflow can reach (team members, persona library) |
| `src/backend/oasis/experts.py` | persona library (public / agency / custom / team) and reply parsing |
| `src/backend/oasis/forum.py` | forum/topic data handling plus post/event hooks for living graph ingestion |
| `src/backend/oasis/swarm_engine.py` | Town Genesis scaffold and LLM swarm blueprint generation |
| `src/backend/oasis/graph_memory.py` | GraphRAG persistence, local SQLite fallback, optional Zep mirror, ReportAgent retrieval |
| `src/backend/oasis/models.py` | OASIS request/response models |

Pair these with:

- `docs/create_workflow.md`
- `docs/build_team.md`
- `docs/openclaw-commands.md`

## MCP Tools and Integrations

For tool execution or tool exposure:

- `src/backend/webot/mcp/commander.py`
- `src/backend/webot/mcp/filemanager.py`
- `src/backend/webot/mcp/oasis.py`
- `src/backend/webot/mcp/scheduler.py`
- `src/backend/webot/mcp/search.py`
- `src/backend/webot/mcp/session.py`
- `src/backend/webot/mcp/webot.py`
- `src/backend/webot/mcp/llmapi.py`

## ACP Exchange (acpx)

For external AI agent communication via the Agent Client Protocol:

| Path | Purpose |
|---|---|
| `src/backend/external/acpx.py` | Singleton `AcpxAdapter` wrapping the `acpx` CLI; manages sessions and prompt execution |
| `src/backend/external/acp.py` | the only acpx consumer: the runtime of codex, claude-code, gemini … agents |
| `src/backend/external/session.py`, `src/backend/external/history.py` | what the external runtimes share: the session named after the agent id, the identity prompt, the exchange log |
| `src/backend/agents/platforms.py` | which platforms are ACP tools (the `acpx` agent list) |

Known ACP tools (external AI agents): `openclaw`, `codex`, `claude`, `gemini`, `aider`.

`acpx` is optional. Install it explicitly with `bash launch/run.sh install-component acpx` before using ACP agents.

## Bot Integrations

| Path | Purpose |
|---|---|
| `src/backend/channels/main.py` | starts the configured channels |
| `src/backend/channels/adapters/` | webhook, NoneBot and WeClaw bridges |
| `src/backend/channels/channel_catalog.py` | the channel catalog (`config/channels.json`) |
| `src/backend/channels/setup_requests.py` | authenticated setup requests; credentials bypass tool history |

## Team and User Data

The most important runtime data lives here:

```text
data/
├── agent_checkpoints/        # per-thread append-only context SQLite files
├── agents.db                 # the table of all agents (every session, by its number)
├── conversations.db          # group and private chats
├── oasis_graph_memory.db
├── webot_subagents.db
├── team_creator_jobs.db
├── prompts/
├── schedules/
└── user_files/{user_id}/
    ├── user_profile.txt
    ├── skills_manifest.json
    ├── webot_agent_profiles.json
    ├── oasis/yaml/
    └── teams/{team_name}/            # the team's namespace
        ├── members.json
        ├── oasis_experts.json
        ├── oasis/yaml/*.yaml
        ├── oasis/python/*.py
        └── skills/
```

Pair these with:

- `docs/example_team.md`
- `docs/build_team.md`
- `docs/create_workflow.md`

## Tests and Validation

When changing code, check the nearest validation surface:

| Path / Command | Use |
|---|---|
| `test/test_agent_runtime_state.py` | runtime state unit tests |
| `test/test_session_service.py` | session API filtering / deletion tests |
| `test/test_webot_profiles.py` | WeBot agent profile unit tests |
| `test/test_webot_policy.py` | WeBot tool policy and hook unit tests |
| `test/test_webot_runtime.py` | WeBot delegated runtime helper tests |
| `test/test_webot_service.py` | WeBot runtime API service tests |
| `test/test_webot_subagents.py` | WeBot subagent metadata store unit tests |
| `test/test_webot_orchestration.py` | delegated subagent flow integration tests |
| `test/test_openai_protocol.py` | OpenAI protocol unit tests |
| `test/test_integration.py` | cross-service integration tests |
| `test/test_team_creator_jobs.py` | ClawCross Creator job persistence tests |
| `test/test_team_creator_imports.py` | ClawCross Creator colleague/mentor import and quick-create route tests |
| `test/test_skill_evolution.py` | self-evolution strategy, validation-report, and repo-skill update tests |
| `test/test_skill_import_tools.py` | ArXiv / Feishu helper conversion tests |
| `test/test_team_creator_workflow.py` | ClawCross Creator workflow/build tests |
| `test/test_team_creator_zip.py` | ClawCross Creator ZIP export tests |
| `test/test_proxy_login_i18n.py` | frontend i18n and login proxy coverage |
| `test/test_tinyfish_monitor.py` | TinyFish target loading, persistence, and polling tests |
| `test/test_configure_openclaw_sync.py` | OpenClaw LLM import tests |
| `test/test_oasis_swarm_engine.py` | swarm scaffold / blueprint normalization tests |
| `test/test_oasis_graph_memory.py` | GraphRAG persistence, retrieval, and ReportAgent fallback tests |
| `test/browser/creator-smoke.spec.js` | Playwright smoke for `/creator` direct mentor/colleague generation flows |
| `test/browser/studio-smoke.spec.js` | Playwright smoke for `/studio` tabs, settings actions, and WeBot runtime sidebar |
| `test/llm_live_smoke.py` | opt-in real provider LLM smoke test |
| `test/cloudflare_live_smoke.py` | opt-in Cloudflare quick tunnel smoke test |
| `npm run test:node` | frontend pure logic tests |
| `npm run test:browser-smoke` | browser smoke with the Flask test shell |
| `python test/tinyfish_live_smoke.py --site <site_key>` | opt-in real TinyFish smoke test |
| `uv run src/cli/cli.py status` | smoke test services |
| `python -m py_compile <file>` | quick syntax check for touched Python files |
| `node --check src/frontend/static/js/creator.js` | quick ClawCross Creator syntax check |
| `node --check src/frontend/static/js/main.js` | quick JS syntax check |

## Task-to-File Lookup

### "Settings page or `.env` behavior is wrong"

Read:

- `src/frontend/static/js/main.js`
- `src/frontend/templates/group_chat_mobile.html`
- `src/backend/ops/settings_routes.py`
- `src/backend/ops/settings_service.py`
- `src/backend/common/env_settings.py`
- `config/.env.example`

### "Model selection / provider / audio defaults are wrong"

Read:

- `src/backend/common/llm_factory.py`
- `src/backend/ops/service.py`
- `src/backend/ops/setup/configure.py`

### "Workflow YAML or OASIS execution is wrong"

Read:

- `docs/create_workflow.md`
- `src/backend/oasis/scheduler.py`
- `src/backend/oasis/engine.py`
- `src/backend/oasis/server.py`
- `src/backend/oasis/swarm_engine.py`
- `src/backend/oasis/graph_memory.py`
- `src/backend/oasis/participants.py`
- `docs/example_team.md`

### "Town Mode / swarm graph / ReportAgent looks wrong"

Read:

- `src/frontend/templates/index.html`
- `src/frontend/static/js/main.js`
- `src/frontend/static/css/style.css`
- `src/frontend/proxies/oasis.py`
- `src/backend/oasis/server.py`
- `src/backend/oasis/swarm_engine.py`
- `src/backend/oasis/graph_memory.py`

### "ClawCross Creator or workflow-to-team is wrong"

Read:

- `docs/team-creator.md`
- `src/frontend/server.py`
- `src/backend/teams/creator.py`
- `src/frontend/static/js/creator.js`
- `src/frontend/static/css/creator.css`
- `src/frontend/templates/creator.html`
- `src/frontend/static/js/orchestration.js`
- `test/test_team_creator_jobs.py`
- `test/test_team_creator_workflow.py`
- `test/test_team_creator_zip.py`

### "An OpenClaw agent does not answer"

OpenClaw is an ACP agent like Codex. Read:

- `docs/openclaw-commands.md`
- `src/backend/external/acp.py` (the ACP runtime), `src/backend/external/acpx.py` (`openclaw acp --session …`), `src/backend/external/session.py` (`runtime_session`)

### "TinyFish search agent or data extraction is wrong"

Read:

- `docs/tinyfish-monitor.md`
- `src/backend/tinyfish/monitor.py`
- `src/frontend/server.py`
- `src/backend/scheduler/service.py`
- `config/tinyfish_targets.example.json`
- `test/test_tinyfish_monitor.py`

### "Frontend route or login behavior is wrong"

Read:

- `src/frontend/server.py`
- `src/frontend/proxies/groups.py`
- `src/frontend/proxies/oasis.py`
- `src/frontend/proxies/front_session_routes.py`
- `docs/ports.md`

## Documentation Cross-Links

- Start here for docs routing: [`index.md`](./index.md)
- Start here for operator workflow: [`../SKILL.md`](../SKILL.md)
- Use [`../README.md`](../README.md) for product-facing explanation, not code indexing

## 模型能力与前端构建

- `src/backend/common/model_capabilities.py` / `model_catalog.json`：本地能力与固定目录；`docs/model-capabilities.md` 说明优先级。
- `tools/maintenance/update_model_catalog.py`：维护者手动更新，不在启动路径。
- `tools/build/tailwind.config.cjs` / `tailwind.input.css`：静态 CSS 的构建输入；`npm run build:css`。
- `src/frontend/static/js/attachment-utils.js`：共用图片压缩和群消息大小校验。
