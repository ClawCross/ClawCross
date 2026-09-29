# Runtime Database Notes

This document summarizes the runtime-related databases used by Clawcross, what they store, and the current artifact-write behavior.

## Database Roles

- `data/agent_checkpoints/*.db`
  - Primary append-only conversation context store.
  - One SQLite file per `thread_id` / session.
  - Main table: `context_messages` (one row per message); legacy `agent_state`,
    `checkpoints`, and `writes` tables may remain after an upgrade.
  - Purpose: persist conversation context without forcing all sessions
    to contend on one shared SQLite writer.

- `data/webot_agents/<user>#<agent>.db`
  - One SQLite file per Agent for its inbox, approvals, execution permits, runs,
    run events, artifacts, mode, plans, todos, verifications, memory, voice,
    and Claude keepalive state.
  - A sender writes an inbox message to the recipient Agent's file. Cross-Agent
    listings scan these files and deduplicate legacy rows by record ID.

- `data/webot_runtime.db`
  - Legacy store. Existing session rows are copied into the relevant Agent file
    on first access; new rows for the features above are written only to Agent files.
  - Bridge, goal, and user-level buddy records still use this database.

## Per-Agent Runtime Tables

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
  - `webot_verifications`, `webot_tool_approvals`, `webot_execution_permits`
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
