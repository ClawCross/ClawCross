# Runtime Reference

This reference captures the architecture, service responsibilities, and runtime data layout for Clawcross’s Claude-Code-inspired agent runtime. It emphasizes WeBot, session state, MCP tooling, and the canonical runtime DTO that now powers the browser UI, CLI/BFF proxies, bridge clients, voice state, and memory/Kairos surfaces.

## Architecture Map

```
Browser / Studio UI
    -> `src/front.py` (Flask UI + session auth + runtime proxies)
    -> `frontend/js/main.js` + runtime panel wiring (current-session card, bridge socket consumer, voice controls)
FastAPI services
    -> `src/mainagent.py` (OpenAI-compatible chat endpoints, session history, cancel)
    -> `src/webot/api/routes.py` (runtime + policy APIs via `WeBotService`)
    -> `src/webot/api/service.py` (serializes runtime DTOs, policy/plan/todo persistence)
    -> `src/webot/tools/webot.py` (MCP tools: subagents, session messages, inbox, plans and todos)
    -> `src/api/ops_service.py` (voice/TTS + direct connect hooks for audio uploads)
    -> `src/webot/bridge.py` (bridge session issuance, websocket hub, runtime snapshot publish)
    -> `src/webot/memory.py` / `src/webot/voice.py` (memory and voice services)
Persistence
    -> `data/webot_agents/<user>#<agent>.db` (runs, inbox, approvals, permits, artifacts, session state, memory, voice)
    -> `data/webot_runtime.db` (legacy rows plus active bridge records)
    -> `data/webot_subagents.db` (subagent metadata)
    -> `data/user_files/{user_id}/` (profiles, policies, runtime artifacts, memory dirs, logs)
Side systems
    -> `oasis/` (Town Mode, workflows, swarm engine)
    -> `src/integrations/acpx_adapter.py` (ACP exchange with external AI agents via acpx CLI)
    -> WeBot dream pipeline (`webot_memory.py`) as the current browser-native autoDream layer
```

## Service Responsibilities

| Service | Ownership |
|---|---|
| `src/front.py` | Flask UI shell, authentication, WeBot runtime proxy routes (`/proxy_webot_*`), voice/TTS proxies, bridge-ready URLs. |
| `src/mainagent.py` | OpenAI-compatible chat API, session history, cancel, provider routing. |
| `src/webot/api/service.py` | Serializes DTO (mode, plan, todos, approvals, inbox, artifacts, runs, relationships, bridge/voice/memory), enforces auth, counts inbox queue, exposes policy endpoints. |
| `src/webot/tools/webot.py` | Durable spawn/send/cancel workflows, background run leasing, inbox delivery, plan/todo updates, runtime artifact logging. |
| `src/webot/runtime_store.py` | SQLite tables for runs, attempts, inbox messages, artifacts, session modes, verifications, tool approvals, memory state, bridge sessions, voice state; helpers for leases/heartbeats/interruption/events. |
| `src/webot/runtime.py` | Mode normalization, blocked tool lists, turn-limit messaging, surgical heuristics for plan/execute/review. |
| `src/webot/policy.py` | Normalizes tool policies, events (`session_start`, `permission_request`, `stop`, etc.), hook definitions, serialization, router for `save_tool_policy_config`. |
| `src/webot/engine/agent.py` | Enforces tool filtering, injects runtime context, proxies MCP tooling into session handler, budgets history with `webot_context`. |
| `src/api/ops_service.py` | Text-to-speech / audio proxy for voice mode; writes audio metadata into runtime payload via `front.py`. |
| `src/webot/profiles.py` | Profile definitions (`general`, `research`, `planner`, `coder`, `reviewer`, `verifier`), helper `slugify`, built-in tool sets, user extension loading. |
| `src/webot/context.py` | Budgeting helpers (tool results, user inputs) that log artifacts, perform compaction, build runtime summaries. |
| `src/webot/workspace.py` | Worktree/remote/shared workspace resolution used when rendering runtime panel workspace text. |
| `src/routes/front_webot_routes.py` | Additional Flask proxies for runtime mode updates, plan/todo/verification APIs, supporting UI actions and bridge wiring. |
| `src/webot/bridge.py` | Bridge attach/detach records, websocket route support, connected-client registry, publish helpers for runtime updates. |
| `src/webot/memory.py` | Per-project memory directories, `MEMORY.md`, relevant entry recall, daily logs, dream gating, Kairos flags. |
| `src/webot/voice.py` | Voice defaults + persisted per-session voice state derived from current LLM/audio provider. |

## Runtime DTO

Every runtime request (`/webot/session-runtime` → `WeBotService.get_session_runtime`) returns:

- `mode`: current `execute/agent/plan/review/yolo` mode plus reason/status.
- `plan`, `todos`, `verifications`, `approvals`: persisted states from `webot_runtime_store`.
- `inbox`: messages from `webot_session_inbox` with `summary`, delivery status, and `read_at`. `send_to_session` and the inbox API share the queue; idle delivery sends summaries, and `read_session_inbox` retrieves full bodies on demand. The dynamic context includes new/unread counts and up to three new summaries only on the first inference of a turn with queued messages. A delivered digest already carries that notice, and previously notified unread messages are not repeated in later dynamic blocks.
- `artifacts`: runtime artifacts stored when budgets trigger (`webot_context`, `_deliver_inbox_messages`).
- `runs`: `list_runs_for_session` results with `run_kind`, `mode`, `events`.
- `active_run`: latest `queued`/`running` run (main session or child).
- `relationships`: `parent_session` plus `children` aggregated via `list_subagents_for_parent_session`.
- `memory`: per-project memory metadata, daily logs, Kairos flag, dream timestamps, relevant entries, dream eligibility.
- `bridge`: bridge session records, attach code, websocket path, connection count, live status, role list.
- `voice`: enabled flag, provider defaults, STT/TTS models, read-aloud setting, last transcript.

Internal modules use this DTO to keep the runtime panel, Flask proxies, MCP tools, prompt context injection, and bridge websocket payloads in sync.

## Data Layout

```
data/
├── webot_agents/     (one DB per Agent: runs / attempts / inbox / approvals / permits / artifacts / session_mode)
├── webot_runtime.db  (legacy rows / active bridge records)
├── webot_subagents.db (agent metadata: id/session/parent/status)
├── user_files/
│   └── {user_id}/
│       ├── webot_tool_policy.json
│       ├── webot_agent_profiles.json
│       ├── webot_inbox_deliveries/
│       ├── webot_tool_events.jsonl
│       ├── webot_compactions/
│       ├── projects/{project_slug}/memory/
│       │   ├── MEMORY.md
│       │   └── logs/YYYY/MM/YYYY-MM-DD.md
│       └── ... (artifacts, bridge metadata)
```

## API Surface

- `/webot/subagents` – list subagents with runtime status and queued inbox count.
- `/webot/subagents/history` – fetch persisted snapshot messages for a subagent session.
- `/webot/subagents/cancel` – cancel background runs gracefully.
- `/webot/session-runtime` – primary runtime DTO consumed by Studio / CLI / bridge.
- `/webot/session-mode` – switch execute/agent/plan/review/yolo.
- `/webot/lsp` – OpenSeek-style best-effort diagnostics for a workspace file (Python, TypeScript, JavaScript, JSON).
- `/webot/session-inbox`, `/webot/session-inbox/send`, `/webot/session-inbox/deliver` – inbox list/send/deliver for cross-session messaging.
- `/webot/runs/interrupt` – request interruption for an active runtime run.
- `/webot/session-plan`, `/webot/session-todos`, `/webot/verifications` – plan/todo/verification CRUD.
- `/webot/voice`, `/webot/bridge/attach`, `/webot/bridge/detach`, `/webot/kairos`, `/webot/dream` – browser endpoints for voice, runtime snapshot subscriptions, Kairos, and dream.
- `/webot/tool-policy` – read/write policy and hook definitions.
- `/webot/tool-approvals/resolve` – resolve manual approvals.
- `/webot/ws/{user_id}/{bridge_id}` – websocket transport for Studio runtime snapshots. It accepts `ping` and `refresh`; it does not execute Agent commands. The displayed attach code is not a permission handshake.
- `/proxy_webot_*` (Flask) – front-end-friendly proxies for runtime data, policies, approvals, session mode, and tool approvals.

The browser uses these APIs for runtime inspection and controls. Bridge has no bound model tool or remote command protocol.

## Related Docs

- [`webot-agent-runtime.md`](./webot-agent-runtime.md) – deep dive on runtime concepts and hooks.
- [`webot-claude-gap-analysis.md`](./webot-claude-gap-analysis.md) – matrix vs Claude Code and outstanding parity items.
- [`ports.md`](./ports.md) – route/port map.
