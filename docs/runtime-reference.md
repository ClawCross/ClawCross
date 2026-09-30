# Runtime Reference

This reference captures the architecture, service responsibilities, and runtime data layout for Clawcross’s Claude-Code-inspired agent runtime. It emphasizes WeBot, session state, MCP tooling, and the canonical runtime DTO that now powers the browser UI, CLI/BFF proxies, voice state, and memory/Kairos surfaces.

## Architecture Map

```
Browser / Studio UI
    -> `src/frontend/server.py` (Flask UI + session auth + runtime proxies)
    -> `src/frontend/static/js/main.js` + runtime panel wiring (current-session card and voice controls)
FastAPI services
    -> `src/backend/server.py` (OpenAI-compatible chat endpoints, session history, cancel)
    -> `src/backend/webot/api/routes.py` (runtime + policy APIs via `WeBotService`)
    -> `src/backend/webot/api/service.py` (serializes runtime DTOs, policy/plan/todo persistence)
    -> `src/backend/webot/mcp/webot.py` (MCP tools: subagents, session messages, inbox, plans and todos)
    -> `src/backend/ops/service.py` (voice/TTS + direct connect hooks for audio uploads)
    -> `src/backend/webot/memory.py` / `src/backend/webot/voice.py` (memory and voice services)
Persistence
    -> `data/webot_agents/<user>#<agent>.db` (runs, inbox, approvals, permits, artifacts, session state, memory, voice)
    -> `data/webot_subagents.db` (subagent metadata)
    -> `data/user_files/{user_id}/` (profiles, policies, runtime artifacts, memory dirs, logs)
Side systems
    -> `src/backend/oasis/` (Town Mode, workflows, swarm engine)
    -> `src/backend/external/acpx.py` (ACP exchange with external AI agents via acpx CLI)
    -> WeBot dream pipeline (`webot_memory.py`) as the current browser-native autoDream layer
```

## Service Responsibilities

| Service | Ownership |
|---|---|
| `src/frontend/server.py` | Flask UI shell, authentication, WeBot runtime proxy routes (`/proxy_webot_*`), voice/TTS proxies. |
| `src/backend/server.py` | OpenAI-compatible chat API, session history, cancel, provider routing. |
| `src/backend/webot/api/service.py` | Serializes DTO (mode, plan, todos, approvals, inbox, artifacts, runs, relationships, voice/memory), enforces auth, counts inbox queue, exposes policy endpoints. |
| `src/backend/webot/mcp/webot.py` | Durable spawn/send/cancel workflows, background run leasing, inbox delivery, plan/todo updates, runtime artifact logging. |
| `src/backend/webot/runtime_store.py` | SQLite tables for runs, attempts, inbox messages, artifacts, session modes, verifications, tool approvals, memory state, voice state; helpers for leases/heartbeats/interruption/events. |
| `src/backend/webot/runtime.py` | Mode normalization, blocked tool lists, turn-limit messaging, surgical heuristics for plan/execute/review. |
| `src/backend/webot/policy.py` | Normalizes tool policies, events (`session_start`, `permission_request`, `stop`, etc.), hook definitions, serialization, router for `save_tool_policy_config`. |
| `src/backend/webot/engine/agent.py` | Enforces tool filtering, injects runtime context, proxies MCP tooling into session handler, budgets history with `webot_context`. |
| `src/backend/ops/service.py` | Text-to-speech / audio proxy for voice mode; writes audio metadata into runtime payload via the frontend (`src/frontend/server.py`). |
| `src/backend/webot/profiles.py` | Profile definitions (`general`, `research`, `planner`, `coder`, `reviewer`, `verifier`), helper `slugify`, built-in tool sets, user extension loading. |
| `src/backend/webot/context.py` | Budgeting helpers (tool results, user inputs) that log artifacts, perform compaction, build runtime summaries. |
| `src/backend/webot/workspace.py` | Worktree/remote/shared workspace resolution used when rendering runtime panel workspace text. |
| `src/frontend/proxies/webot.py` | Additional Flask proxies for runtime mode updates, plan/todo/verification APIs, supporting UI actions. |
| `src/backend/webot/memory.py` | Per-project memory directories, `MEMORY.md`, relevant entry recall, daily logs, dream gating, Kairos flags. |
| `src/backend/webot/voice.py` | Voice defaults + persisted per-session voice state derived from current LLM/audio provider. |

## Runtime DTO

Every runtime request (`/webot/session-runtime` → `WeBotService.get_session_runtime`) returns:

- `mode`: current `execute/agent/plan/review/yolo` mode plus reason/status.
- `plan`, `todos`, `verifications`, `approvals`: persisted states from `webot_runtime_store`.
- `inbox`: messages from `webot_session_inbox` with `summary`, delivery status, and `read_at`. `send_to_session` and the inbox API share the queue; idle delivery sends summaries, and `read_session_inbox` retrieves full bodies on demand and marks the returned messages read. The dynamic context includes new/unread counts and up to three new summaries only on the first inference of a turn with queued messages. A delivered digest already carries that notice, and previously notified unread messages are not repeated in later dynamic blocks.
- `artifacts`: runtime artifacts stored when budgets trigger (`webot_context`, `_deliver_inbox_messages`).
- `runs`: `list_runs_for_session` results with `run_kind`, `mode`, `events`.
- `active_run`: latest `queued`/`running` run (main session or child).
- `relationships`: `parent_session` plus `children` aggregated via `list_subagents_for_parent_session`.
- `memory`: per-project memory metadata, daily logs, Kairos flag, dream timestamps, relevant entries, dream eligibility.
- `voice`: enabled flag, provider defaults, STT/TTS models, read-aloud setting, last transcript.

Internal modules use this DTO to keep the runtime panel, Flask proxies, MCP tools, and prompt context injection in sync.

## Data Layout

```
data/
├── webot_agents/     (one DB per Agent: runs / attempts / inbox / approvals / permits / artifacts / session_mode)
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
│       └── ... (artifacts)
```

## API Surface

- `/webot/subagents` – list subagents with runtime status and queued inbox count.
- `/webot/subagents/history` – fetch persisted snapshot messages for a subagent session.
- `/webot/subagents/cancel` – cancel background runs gracefully.
- `/webot/session-runtime` – primary runtime DTO consumed by Studio / CLI.
- `/webot/session-mode` – switch execute/agent/plan/review/yolo.
- `/webot/lsp` – OpenSeek-style best-effort diagnostics for a workspace file (Python, TypeScript, JavaScript, JSON).
- `/webot/session-inbox` – list a session's inbox. Sending is `send_to_session` (or `POST /v1/agents/<id>/inbox`); delivering what is queued is `POST /v1/agents/<id>/control` `{"action": "deliver_inbox"}`.
- `/webot/runs/interrupt` – request interruption for an active runtime run.
- `/webot/session-plan`, `/webot/session-todos`, `/webot/verifications` – plan/todo/verification CRUD.
- `/webot/voice`, `/webot/kairos`, `/webot/dream` – browser endpoints for voice, Kairos, and dream.
- `/webot/tool-policy` – read/write policy and hook definitions.
- `/webot/tool-approvals/resolve` – resolve manual approvals.
- `/proxy_webot_*` (Flask) – front-end-friendly proxies for runtime data, policies, approvals, session mode, and tool approvals.

The browser uses these APIs for runtime inspection and controls. Studio refreshes runtime status through HTTP polling.

## Related Docs

- [`webot-agent-runtime.md`](./webot-agent-runtime.md) – deep dive on runtime concepts and hooks.
- [`webot-claude-gap-analysis.md`](./webot-claude-gap-analysis.md) – matrix vs Claude Code and outstanding parity items.
- [`ports.md`](./ports.md) – route/port map.
