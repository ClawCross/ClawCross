# ACP live validation — 2026-10-09

Used temporary Agents through the running ClawCross HTTP API and real native
Codex/Claude processes. Inputs used fresh random file values, mail identifiers
and group questions; providers were not mocked.

| Check | Codex (`gpt-6-sol`) | Claude (native default) |
| --- | --- | --- |
| MCP searches and file reads; calculate values unknown to the prompt | Passed | Passed |
| Incoming inbox message, read receipt and live runtime context | Passed | Passed |
| Change ClawCross workspace without losing the native session | Passed | Passed |
| Reset; current history becomes empty; new MCP call works | Passed | Passed |
| Delete temporary Agent; catalog lookup returns 404 | Passed | Passed |
| MCP join with a frontend-signed invitation | Passed | Passed |
| MCP send to the joined group | Passed | Passed |
| Human group message → inbox wake → MCP reply to that group | Passed | Passed |
| Remove only the temporary test Agents/groups and local records | Passed | Passed |

The current ChatGPT login rejected `gpt-6.1-sol`, although the adapter advertised
it. Native `gpt-6-sol` requests succeeded. The regression now treats native model
metadata warnings followed by structured provider errors as failures rather than
successful assistant text. User-requested ordinary JSON remains text.

Backend validation: 92 tests and 4 subtests passed, including busy/queued reset,
inbox, group isolation, date/member filters and prompt prefix stability.
Browser checks covered long Team scrolling, recent Agent folding, unified
creation and draft retention, running indicators, search highlighting, context
view, quotation and mobile/desktop containment. Hub TypeScript production build
passed for the compatible relay search/context endpoints.

No existing Agents, groups or conversation histories were reset or removed.
