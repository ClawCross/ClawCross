# WeBot-Agent Runtime Reference

This document records the current, running WeBot delegated runtime that is now closer to Claude Code’s production agent than ever. It centers on the runtime control plane, lifecycle hooks, and the API/UX surfaces that expose mode, inbox, runs, memory and voice state.

## Runtime Control Plane

- **Main session + subagents use the same runtime API.** `/webot/session-runtime` reports `mode`, `plan`, `todos`, `verifications`, `approvals`, `inbox`, `artifacts`, `runs`, `active_run`, `relationships`, `memory`, and `voice`. Each Agent keeps its runtime records in its own database file; cross-Agent views scan those files.
- **Durable runs + leases.** `webot_runtime_store.py` now records `WeBotRunRecord` rows with `run_kind`, `mode`, `lease_expires_at`, `heartbeat_at`, `interrupt_requested`, and `metadata_json`. Helper APIs (`claim_run_worker`, `heartbeat_run`, `release_run_worker`, `request_run_interrupt`, `record_run_event`) guarantee runs survive MCP-worker restarts and can emit structured events for the frontend.
- **Durable session inbox.** Both `send_to_session` and `POST /v1/agents/<id>/inbox` persist messages as `queued`. A worker waits for the target session to become idle, then sends one short notification for consecutive passive messages: unread count, message IDs, sender, and summaries. The sender can supply a summary; otherwise the first 100 characters of the body form a preview. `read_session_inbox` returns full bodies and marks exactly those messages read. `mark_session_inbox_read` can mark selected or all messages without returning their bodies. `delivered` means notified or explicitly marked read, while `read_at` tracks reading separately. A queued message waiting for a synchronous reply keeps its queued delivery status when read, so the worker can still finish the reply. `wait=true` does not use the inbox: it is an ask through `/system_trigger` (`wait_reply`), a turn after the target's current one whose reply comes back to the sender. Startup resumes queued entries.
- **Live system prompts.** Each provider request reads current templates, persona, user profile and SOUL and assembles its system message anew, including repair retries and terminal structured decoding. Persisted first-turn system prompts are not used for inference. Unchanged sources keep identical bytes; edits take effect without resetting a session.
- **Dynamic context transitions.** Before each normal model step, WeBot compares the current runtime block, Team membership and Skill/Memory catalog with the last snapshot still visible in the model's conversation window. A session's first inference sends a full snapshot, including after a fork; later steps append only added and removed lines to the current user message or final tool result. The snapshot and injected change are stored as message metadata, so subsequent requests replay the same model-visible history across turns and restarts while history APIs keep the original message content. If compaction removes the prior snapshot, the next step sends a full snapshot again. `tool_search` returns matches in its tool result and never adds their schemas to the bound tool definitions.
- **Context token accounting.** The provider's input and output token totals are the source of truth. A local tokenizer estimates the shares for the system prompt, tool schemas, all runtime-state transitions still in the model history, summary, ordinary messages, and tool results; those shares are scaled to the provider's input total. Output is added separately. The component split is an estimate, even when the total is exact.
- **DeepSeek structured turns.** A structured WeBot turn sends the active tool declarations and `text.format=json_schema` together through the DeepSeek Responses API. Tool calls return to the normal tool loop; a terminal text reply is validated against the requested schema. OASIS LLM agents use the same text-schema path without a synthetic reply tool.
- **Optional command sandbox.** `command_sandbox=srt` runs only after the existing command blacklist and approval gate. SRT is installed explicitly with `install-component srt`; ordinary startup does not download it. On POSIX, the wrapped command receives CPU, address-space, file-size, open-file, and process-count limits in addition to the existing timeout and output cap. Windows sandbox commands fail closed until an equivalent resource-limited runner is available.

## Profiles, Modes & Hooks

- **Profiles + tool filtering.** Built-in profiles (`general`, `research`, `planner`, `coder`, `reviewer`, `verifier`) in `webot_profiles.py` declare system prompt fragments, allowed tools, preferred models, and `max_turns`. User-defined profiles live under `data/user_files/{user_id}/webot_agent_profiles.json` and the same MCP paths.
- **Session modes.** `webot_runtime.py` normalizes `execute`, `agent`, `plan`, `review`, and `yolo`. `webot_service.update_session_mode` persists the mode via `save_session_mode` and returns a payload with `reason`, `status`, `mode`. Mode-aware tool filtering uses `filter_tools_for_mode`; plan mode blocks destructive tools, review mode tightens further, and yolo auto-approves only manual policy prompts while still respecting explicit deny rules.
- **Policy/hook pipeline.** `webot_policy.py` now normalizes hooks for events such as `session_start`, `user_prompt_submit`, `pre_tool`, `post_tool`, `permission_request`, `permission_resolved`, `pre_compact`, `stop`, `subagent_stop`, `session_end`. Hooks can log to JSONL, run shell commands, or mutate arguments. `webot_permission_context.py` enforces the decisions before MCP tools execute.
- **Compaction + budgets.** Before every model call, including inside tool loops, the background manager checks pressure and picks up completed summaries. It waits near the safe window limit; oversized tool turns can compact at complete tool boundaries. Normal compaction retains configured recent turns and summarizes transcripts in bounded chunks. Originals remain append-only, with every committed summary recorded in `context_compaction_history`. `webot/runtime_settings.py` supplies user/session budgets and summary settings; see [compact-approval-audit.md](./compact-approval-audit.md).
- **Independent approval review.** `webot/approval_review.py` unifies manual tool policy and high-risk command approvals. Review defaults to the user; optional `auto_review` consults a separate model with exact actions and original user authorization. Invalid or uncertain verdicts return to the user. Explicit deny rules and command hard blocks remain enforced, with exact one-use permits between Agent and MCP.

## Feature Coverage

- **Subagent orchestration.** `mcp_webot.py` handles synchronous `spawn_subagent(wait=True)` flows, background queues, recoveries (`_recover_background_runs`), notifications to parent sessions, and explicit mode propagation (planner->plan, reviewer->review). Runs record `agent_type`, workspace metadata, and `run_events` produced by `record_run_event`.
- **Planning & review fleets.** There are no dedicated ultraplan/ultrareview tools: the agent plans with `spawn_subagent(agent_type="planner", workspace_mode="worktree")` and reviews from several angles by spawning `reviewer` subagents in parallel.
- **Memory, Kairos, AutoDream.** `webot_memory.py` maintains per-project memory directories, `MEMORY.md`, relevant-entry recall, daily logs, and runtime-store sync. `run_auto_dream` applies time/session/lock gates, writes dream summaries, and updates `runtime.memory` so Kairos-style follow-ups can be triggered from the same control plane.
- **Voice.** The existing audio stack (`ops_service` for TTS, `main.js` recording + TTS UI) persists `runtime.voice` per session and exposes toggle APIs.

## Runtime Flow Recap

1. User hits `/studio` with a logged-in session; `main.js` loads the runtime panel via `/proxy_webot_session_runtime`.
2. The runtime DTO includes the current session (main thread) plus subagents in `relationships.children`.
3. Mode, plan, todos, verifications, approvals, inbox, artifacts, runs, voice, and memory metadata all come from `webot_service.get_session_runtime` and its helper serializers.
4. Actions (mode switch, deliver inbox, voice record/play, kairos, dream, verification records) call the corresponding MCP/Flask endpoints — the session toggles and verification records are UI/API features, not model tools; the runtime store updates runs and artifacts, keeping the main session in sync with the control plane.

## File Map

| File | Role |
|---|---|
| `src/backend/webot/mcp/webot.py` | Model-facing tools: subagents, plan/todo, session inbox, session mode, Claude Code keepalive, runtime artifact logging |
| `src/backend/webot/runtime_store.py` | Durable tables for runs, attempts, inbox, artifacts, session state |
| `src/backend/webot/api/service.py` | Runtime API that serializes DTO for the frontend and proxies, tracks workspace descriptions, counts inbox/gate details |
| `src/backend/webot/runtime.py` | Utility functions (`normalize_session_mode`, mode messages, stop conditions, max_turn resolution) |
| `src/backend/webot/lsp.py` | OpenSeek-style best-effort workspace diagnostics used by the `/webot/lsp` API (the agent runs the same checks through `run_command`) |
| `src/backend/webot/context.py` | Context budgeting, artifact logging for oversized inputs/results, compaction guardrails |
| `src/backend/webot/compression.py` | Chunked summaries, whole-turn retention, version-checked persistence and compaction metrics |
| `src/backend/webot/runtime_settings.py` | Validated user/session context and approval overrides with atomic persistence |
| `src/backend/webot/approval_review.py` | Unified approval broker and independent structured reviewer |
| `src/backend/webot/approval_actions.py` | Canonical exact execution parameters shared with MCP |
| `src/backend/webot/policy.py` | Policy normalization, hook/approval parsing, event enumeration |
| `src/backend/webot/engine/agent.py` | Permits MCP tools, enforces tool filtering, injects runtime prompts, loads `webot_runtime` helpers |
| `src/backend/webot/memory.py` | Per-project memory directories, Kairos state, daily logs, dream summaries |
| `src/frontend/proxies/webot.py` | Flask proxies for runtime APIs, bridging the JS UI with FastAPI backends |
| `src/backend/webot/profiles.py` | Profile definitions plus helper to build/parse `subagent__...` session ids |
| `src/backend/webot/workspace.py` | Worktree/remote/shared workspace resolution describing `workspace_mode` for the runtime card |
| `src/backend/ops/service.py` | Text-to-speech (voice) backend that feeds audio metadata into runtime payloads |

## Runtime Best Practices

- **Preserve the Agent's tool prefix.** Generate API definitions and `tool_search.desc` from this Agent's intrinsic table. Mode and temporary selection changes travel in the runtime block and affect search/execution checks; they do not rewrite that prefix. Editing the intrinsic table intentionally updates the catalog. External ClawCross MCP discovery follows the same rule.

- Always call `spawn_subagent` with a `profile` that matches the work (planner/reviewer/coder) so `webot_profiles` can apply the right mode and tool set.
- Keep `max_turns` low for research modes; `webot_runtime.resolve_max_turns` already prefers explicit overrides and stops internal tools once limits hit.
- Use `send_subagent_message` for follow-ups so the existing session record is reused instead of creating duplicate sidechains.
- Keep the runtime panel open; it now renders `plan`, `todos`, `approvals`, `runs`, `inbox`, and `artifacts` for both the current session and any selected subagent.
- Policy hooks can mutate args and log events at every stage (session start, pre/post tool, permission request/resolution, session end).

## Related Docs

- [`runtime-reference.md`](./runtime-reference.md): service topography and auth.
- [`webot-claude-gap-analysis.md`](./webot-claude-gap-analysis.md): capability matrix vs Claude Code.
