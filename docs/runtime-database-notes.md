# Runtime Database Notes

This document summarizes the runtime-related databases used by Clawcross, what they store, and the current artifact-write behavior.

## Database Roles

- `data/agent_checkpoints/*.db`
  - Primary LangGraph checkpoint store.
  - One SQLite file per `thread_id` / session.
  - Main tables: `checkpoints`, `writes`.
  - Purpose: persist graph state/checkpoint history without forcing all sessions
    to contend on one shared SQLite writer.

- `data/webot_runtime.db`
  - WeBot runtime control-plane store (not full chat transcript storage).
  - Stores run lifecycle, runtime artifacts index, session state, approvals, memory/bridge/voice/buddy runtime state.

## `webot_runtime.db` Core Tables

- `webot_runs`
  - One record per runtime task execution (`run_id`), including status, timeout, worker lease/heartbeat, result/error.

- `webot_run_attempts`
  - Event timeline for each run (prepared/started/completed/failed/etc), with details payloads.

- `webot_session_inbox`
  - Cross-session message delivery queue (`source_session` -> `target_session`).

- `webot_runtime_artifacts`
  - Index of runtime artifacts with fields such as `kind`, `title`, `summary`, `path`, `metadata_json`.
  - `path` points to on-disk text files in `data/user_files/<user>/...`.

- Other state tables
  - `webot_session_state`, `webot_session_plans`, `webot_session_todos`
  - `webot_verifications`, `webot_tool_approvals`
  - `webot_memory_state`, `webot_bridge_sessions`, `webot_voice_state`, `webot_buddy_state`

## Runtime Artifacts: What Is Stored

Common `kind` values currently observed:

- `user_input`
  - Created when a `HumanMessage` cannot be kept inline due to size or remaining per-round user budget.
  - Full text is written to `webot_user_inputs/...`, and an artifact index row is inserted.

- `tool_result`
  - Created when a `ToolMessage` exceeds tool-result budget limits.
  - Full text is written to `webot_tool_results/...`, with an index row.

- `compact_summary`
  - Created during history compaction.
  - Summary text is written to `webot_compactions/...`, with an index row.

## Important Behavior Notes

- Context compression is real and happens before model invocation:
  - user/tool budgeting
  - history compaction
  - token-level compression

- Artifact index growth can be much larger than unique files:
  - repeated runtime passes may append many index rows pointing to the same path.

## Artifact Write Control (New)

Environment variable:

- `WEBOT_RUNTIME_ARTIFACTS_ENABLED`
  - Default: disabled (`0`)
  - Set to `1` / `true` / `on` / `yes` (or any value other than `0` / `false` / `off` / `no`) to enable:
    - writing runtime text files for budgeted user/tool/compaction content
    - inserting `webot_runtime_artifacts` rows for these events

When disabled, context budgeting/compaction still runs; only artifact persistence is skipped.
