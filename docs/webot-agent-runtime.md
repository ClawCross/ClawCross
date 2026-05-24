# WeBot-Agent Runtime Reference

This document records the current, running WeBot delegated runtime that is now closer to Claude Code’s production agent than ever. It centers on the runtime control plane, lifecycle hooks, and the API/UX surfaces that expose mode, inbox, runs, memory, bridge, voice, and buddy state.

## Runtime Control Plane

- **Main session + subagents share a single runtime model.** `agent.py` and `webot_service.py` expose `/webot/session-runtime`, which reports the caller’s `mode`, `plan`, `todos`, `verifications`, `approvals`, `inbox`, `artifacts`, `runs`, `active_run`, `relationships`, `memory`, `bridge`, `voice`, and `buddy` fields. Every MCP call (`spawn_subagent`, `send_subagent_message`, `cancel`, `ultraplan`, `ultrareview`, etc.) reads/writes the same runtime store to keep the main session and children coherent.
- **Durable runs + leases.** `webot_runtime_store.py` now records `WeBotRunRecord` rows with `run_kind`, `mode`, `lease_expires_at`, `heartbeat_at`, `interrupt_requested`, and `metadata_json`. Helper APIs (`claim_run_worker`, `heartbeat_run`, `release_run_worker`, `request_run_interrupt`, `record_run_event`) guarantee runs survive MCP-worker restarts and can emit structured events for the frontend.
- **Inbox + artifact ledger.** `create_inbox_message`, `list_inbox_messages`, `mark_inbox_delivered` persist inbox queue entries. `_deliver_inbox_messages` batches queued entries into a single system trigger, writes the delivery text to `webot_inbox_deliveries`, and records a `session_inbox_delivery` artifact via `create_runtime_artifact`. The frontend consumes `runtime.inbox` plus `runtime.artifacts` to render queues, deliveries, and history.

## Profiles, Modes & Hooks

- **Profiles + tool filtering.** Built-in profiles (`general`, `research`, `planner`, `coder`, `reviewer`, `verifier`) in `webot_profiles.py` declare system prompt fragments, allowed tools, preferred models, and `max_turns`. User-defined profiles live under `data/user_files/{user_id}/webot_agent_profiles.json` and the same MCP paths.
- **Session modes.** `webot_runtime.py` normalizes `execute`, `agent`, `plan`, `review`, and `yolo`. `webot_service.update_session_mode` persists the mode via `save_session_mode` and returns a payload with `reason`, `status`, `mode`. Mode-aware tool filtering uses `filter_tools_for_mode`; plan mode blocks destructive tools, review mode tightens further, and yolo auto-approves only manual policy prompts while still respecting explicit deny rules.
- **Policy/hook pipeline.** `webot_policy.py` now normalizes hooks for events such as `session_start`, `user_prompt_submit`, `pre_tool`, `post_tool`, `permission_request`, `permission_resolved`, `pre_compact`, `stop`, `subagent_stop`, `session_end`. Hooks can log to JSONL, run shell commands, or mutate arguments. `webot_permission_context.py` enforces the decisions before MCP tools execute.
- **Compaction + budgets.** `webot_context.py` trims long tool results, stores archival artifacts, compresses history into summaries, and writes `compact_summary` artifacts under `webot_compactions`. Those summaries are already reused by the current memory/Kairos/dream flow and fit the Claude Code multi-tier compaction idea.

## Feature Coverage

- **Subagent orchestration.** `mcp_webot.py` handles synchronous `spawn_subagent(wait=True)` flows, background queues, recoveries (`_recover_background_runs`), notifications to parent sessions, and explicit mode propagation (planner->plan, reviewer->review). Runs record `agent_type`, workspace metadata, and `run_events` produced by `record_run_event`.
- **Ultraplan & Ultrareview.** New APIs `ultraplan_start/status` and `ultrareview_start/status` create telemetry-rich runs, spawn child reviewers for each angle, aggregate findings, and record artifacts/logs so the frontend can surface plan approvals or reviewer summaries.
- **Memory, Kairos, AutoDream.** `webot_memory.py` maintains per-project memory directories, `MEMORY.md`, relevant-entry recall, daily logs, and runtime-store sync. `run_auto_dream` applies time/session/lock gates, writes dream summaries, and updates `runtime.memory` so Kairos-style follow-ups can be triggered from the same control plane.
- **Bridge / Remote control.** `webot_bridge.py` issues attachable bridge sessions, `webot_routes.py` exposes `/webot/ws/{user_id}/{bridge_id}`, and `webot_service.py` now publishes runtime snapshots to connected bridge clients after mode, inbox, run, voice, Kairos, dream, buddy, and approval changes. The browser runtime panel consumes the same `runtime.bridge` payload and auto-reconnects while the sidebar is open.
- **Voice & Buddy (product parity focus).** The existing audio stack (`ops_service` for TTS, `main.js` recording + TTS UI) now persists `runtime.voice` per session and exposes toggle APIs/MCP tools. `webot_buddy.py` provides deterministic per-user companion state, durable reactions, and runtime-panel actions instead of Claude Code’s terminal sprite renderer.

## Runtime Flow Recap

1. User hits `/studio` with a logged-in session; `main.js` loads the runtime panel via `/proxy_webot_session_runtime`.
2. The runtime DTO includes the current session (main thread) plus subagents in `relationships.children`.
3. Mode, plan, todos, verifications, approvals, inbox, artifacts, runs, voice, bridge, buddy, and memory metadata all come from `webot_service.get_session_runtime` and its helper serializers.
4. Actions (mode switch, deliver inbox, start ultraplan/ultrareview, voice record/play) call the corresponding MCP/Flask endpoints; the runtime store updates runs and artifacts, keeping the main session in sync with the control plane.

## File Map

| File | Role |
|---|---|
| `src/mcp_servers/webot.py` | Core orchestrator: queues, leases, ultrareview/ultraplan, dream gating, bridge/voice/buddy tools, runtime artifact logging |
| `src/webot/runtime_store.py` | Durable tables for runs, attempts, inbox, artifacts, session state |
| `src/webot/service.py` | Runtime API that serializes DTO for the frontend and proxies, tracks workspace descriptions, counts inbox/gate details |
| `src/webot/runtime.py` | Utility functions (`normalize_session_mode`, mode messages, stop conditions, max_turn resolution) |
| `src/webot/lsp.py` | OpenSeek-style best-effort workspace diagnostics used by the LSP MCP/API bridge |
| `src/webot/context.py` | Context budgeting, artifact logging for oversized inputs/results, compaction guardrails |
| `src/webot/policy.py` | Policy normalization, hook/approval parsing, event enumeration |
| `src/core/agent.py` | Permits MCP tools, enforces tool filtering, injects runtime prompts, loads `webot_runtime` helpers |
| `src/webot/bridge.py` | Browser-native bridge session issuance, websocket connection registry, publish helpers |
| `src/webot/memory.py` | Per-project memory directories, Kairos state, daily logs, dream summaries |
| `src/webot/buddy.py` | Deterministic companion generation plus durable reaction state |
| `src/webot/voice.py` | Session voice defaults/state adapter layered on top of existing audio providers |
| `src/routes/front_webot_routes.py` | Flask proxies for runtime APIs, bridging the JS UI with FastAPI backends |
| `src/webot/profiles.py` | Profile definitions plus helper to build/parse `subagent__...` session ids |
| `src/webot/workspace.py` | Worktree/remote/shared workspace resolution describing `workspace_mode` for the runtime card |
| `src/api/ops_service.py` | Text-to-speech (voice) backend that feeds audio metadata into runtime payloads |

## Runtime Best Practices

- Always call `spawn_subagent` with a `profile` that matches the work (planner/reviewer/coder) so `webot_profiles` can apply the right mode and tool set.
- Keep `max_turns` low for research modes; `webot_runtime.resolve_max_turns` already prefers explicit overrides and stops internal tools once limits hit.
- Use `send_subagent_message` for follow-ups so the existing session record is reused instead of creating duplicate sidechains.
- Keep the runtime panel open; it now renders `plan`, `todos`, `approvals`, `runs`, `inbox`, and `artifacts` for both the current session and any selected subagent.
- Policy hooks can mutate args and log events at every stage (session start, pre/post tool, permission request/resolution, session end).

## Related Docs

- [`runtime-reference.md`](./runtime-reference.md): service topography and auth.
- [`webot-claude-gap-analysis.md`](./webot-claude-gap-analysis.md): capability matrix vs Claude Code.
