# OASIS Reference

This page is the technical deep dive for OASIS runtime behavior.

Use it when you need to understand:

- which kinds of experts OASIS can run
- how session naming works
- how external / OpenClaw agents are routed
- how discussion mode differs from execution mode
- how Town Mode, swarm graphs, and GraphRAG memory fit into a topic
- how ReportAgent answers "why this prediction" from graph evidence
- which doc to read next for workflow YAML details

If you need the YAML grammar itself, read [create_workflow.md](./create_workflow.md) first.
If you need Python-script workflows and importable agent/topic helpers, read [workflowpy.md](./workflowpy.md).

## What OASIS Is

OASIS is Clawcross's built-in orchestration engine for multi-expert discussion and execution.

It can:

- run expert debates and conclusion generation
- execute staged workflows
- manage stateful expert sessions
- mix local experts, internal sessions, OpenClaw agents, and external API agents
- seed each topic with a Town Genesis swarm blueprint
- persist topic memory as a living graph and optionally mirror retrieval to Zep

## Town Genesis and GraphRAG

Every topic can carry a `swarm` payload in addition to normal posts and timeline events.

The flow is:

1. `autogen_swarm=true` seeds an immediate scaffold via `oasis/swarm_engine.py`
2. the scaffold can be upgraded into a richer LLM-generated swarm blueprint
3. `oasis/graph_memory.py` writes that blueprint into a living graph
4. subsequent posts, callbacks, timeline events, and the final conclusion mutate that graph over time

The living graph supports two storage modes:

- local SQLite, stored in `data/oasis_graph_memory.db`
- optional external Zep mirror / retrieval when `ZEP_API_KEY` is configured

Relevant environment variables live in [`config/.env.example`](../config/.env.example):

- `OASIS_GRAPHRAG_PROVIDER=auto|zep|local`
- `OASIS_GRAPHRAG_DB_PATH`
- `ZEP_API_KEY`
- `ZEP_API_BASE_URL`
- `OASIS_ZEP_GRAPH_PREFIX`

## What Updates the Living Graph

The graph is not a static precomputed picture. OASIS keeps writing back:

- the initial swarm blueprint
- expert posts and manual `NUDGE` posts
- structured agent callbacks
- timeline events such as `agent_callback`, `manual_post`, and round changes
- the final conclusion
- ReportAgent queries and answers as memory entries

This is why `GET /topics/{topic_id}` returns a living swarm payload rather than only the original blueprint.

## Four Expert Types

| Type | Name Format | Stateful | Backend | Use It For |
|---|---|---|---|---|
| Direct LLM | `tag#temp#N` | No | local LLM | fast stateless expert rounds |
| OASIS Session | `tag#oasis#id` | Yes | internal bot API | persistent expert memory across rounds |
| Regular Agent Session | `Title#session_id` | Yes | internal bot API | reuse an existing agent session directly |
| External API | `tag#ext#id` | Usually yes | external HTTP / OpenClaw / ACP (acpx) | external runtimes and API-based experts |

### 1. Direct LLM

Example:

```yaml
- expert: "creative#temp#1"
```

Use this when you want lightweight, stateless expert behavior with no cross-round memory.

**Per-expert model override:** Direct LLM experts can optionally use a different LLM
provider/model than the global `LLM_*` configuration. Add `model`, `api_key`,
`base_url`, and/or `provider` fields to the persona entry in `oasis_experts.json`:

```json
{
  "name": "GPT-5 创意顾问",
  "tag": "creative",
  "persona": "You are a creative brainstorming expert...",
  "temperature": 0.9,
  "model": "gpt-5.4",
  "api_key": "sk-openai-xxx",
  "base_url": "https://api.openai.com",
  "provider": "openai"
}
```

When these fields are absent or empty, the expert falls back to the global
`LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` environment variables as before.

### 2. OASIS Session

Example:

```yaml
- expert: "synthesis#oasis#analysis01"
```

Use this when the expert should preserve memory and tool context across multiple rounds.

**Per-expert model override:** Like Direct LLM experts, OASIS Session experts also
support per-expert LLM model override. The override parameters are threaded through
the Agent service's `/v1/chat/completions` API via `llm_override` field, so each
session expert can use a different LLM without affecting others. Add the same
`model`, `api_key`, `base_url`, and/or `provider` fields to the persona entry:

```json
{
  "name": "GPT-5 综合顾问",
  "tag": "synthesis",
  "persona": "You are a comprehensive analysis expert...",
  "model": "gpt-5.4",
  "api_key": "sk-openai-xxx",
  "base_url": "https://api.openai.com",
  "provider": "openai"
}
```

The override is per-request: each call from the SessionExpert carries its own
LLM config, and the Agent service dynamically creates a temporary LLM instance
for that request. When these fields are absent, the global `LLM_*` environment
variables are used as fallback.

### 3. Regular Agent Session

Example:

```yaml
- expert: "Assistant#default"
```

Use this when the session identity already exists and should not be re-injected from a preset persona.

### 4. External API / OpenClaw

Example:

```yaml
- expert: "openclaw#ext#my_agent"
  api_url: "http://127.0.0.1:18789"
  api_key: "****"
  model: "agent:main:default"
  headers:
    x-openclaw-session-key: "agent:main:default"
```

Use this when the expert is backed by an external runtime or OpenClaw agent.

## Session Naming Rules

### `#new`

Appending `#new` forces a fresh session:

```yaml
- expert: "creative#oasis#abc#new"
```

That prevents accidental reuse of an older session context.

### OpenClaw model routing

For OpenClaw-style experts, the `model` field uses:

```text
agent:<agent_name>:<session_name>
```

Examples:

- `agent:main:default`
- `agent:main:code-review`

When OASIS detects this format, it prefers the OpenClaw CLI path first and falls back to HTTP if needed.

## Execution Modes

OASIS has two orthogonal switches:

| Switch | Meaning |
|---|---|
| `discussion=true/false` | discussion forum mode vs execution mode |
| `repeat=true/false` | repeat plan each round vs execute once |

### Discussion mode

- experts publish posts
- experts vote on other posts
- the engine can detect consensus
- the engine can generate a conclusion
- Town Mode can continue receiving manual nudges while the topic is live
- graph memory continues to absorb posts, callbacks, and timeline events while the round is running

### Execution mode

- experts produce task outputs directly
- workflows behave more like staged task pipelines
- this is useful when you care more about delivery than debate

Execution-mode special steps:

- `manual` injects a fixed post and completes immediately
- `script` runs a platform command and publishes stdout / stderr as a normal post
- `human` pauses the graph until a human submits a plain-text reply

For `human` steps, the workflow editor only defines the prompt. The actual reply is entered at runtime from the OASIS topic detail UI (Studio or mobile) or via CLI.

## Scheduling Model

OASIS supports:

- linear step-by-step plans
- parallel groups
- selector / branch behavior
- DAG-style dependency scheduling

Human-step continuation rule:

- a `human` node is treated like any other blocking node
- once the reply is accepted, the node is marked complete
- downstream fixed edges, conditional edges, and selector routing continue from that completed node

For the exact YAML schema and examples, read [create_workflow.md](./create_workflow.md).

## External and OpenClaw Behavior

Important rules:

- external experts should use `tag#ext#id`
- `api_key: "****"` is masked and resolved from environment at runtime
- OpenClaw routing should keep `model` and `x-openclaw-session-key` aligned
- if you are adding an OpenClaw agent to a Team, verify it exists first via `openclaw sessions`

### ACP Exchange (acpx)

External experts with tags like `openclaw`, `codex`, `claude`, `gemini`, or `aider` can communicate through the **Agent Client Protocol** via the `acpx` CLI adapter:

- `src/integrations/acpx_adapter.py` provides a singleton `AcpxAdapter` class wrapping the `acpx` CLI binary
- `oasis/experts.py` (`ExternalExpert`) uses ACP for pooled prompt communication with external agents
- `src/api/group_service.py` uses ACP for broadcasting group chat messages to external AI agents
- `acpx` is automatically installed during `bash selfskill/scripts/run.sh setup`
- If `acpx` is not in PATH, the system falls back to HTTP-only communication for external experts

Related docs:

- [build_team.md](./build_team.md)
- [openclaw-commands.md](./openclaw-commands.md)

## ReportAgent

ReportAgent is the graph-backed explainer for prediction topics.

- API: `POST /topics/{topic_id}/report/ask`
- frontend proxy: `POST /proxy_oasis/topics/{topic_id}/report/ask`
- purpose: answer "why is the current prediction leaning this way?"
- source of truth: GraphRAG retrieval only, not raw prompt replay over all posts

The response contains:

- `answer`: short conclusion
- `because`: main evidence chain
- `watchouts`: what could flip the current prediction
- `confidence`: `low|medium|high`
- `evidence`: node / edge / memory excerpts used for the answer

## ClawCross Studio Town Mode

The primary UI entry is the right OASIS sidebar inside `GET /studio`.

Default first-entry behavior for ClawCross Studio:

- active page tab: `Chat`
- right OASIS sidebar: collapsed
- `Town Mode`: `OFF`
- Town workspace subtab: `TOWN` instead of `GRAPH`

Typical manual flow:

1. open ClawCross Studio
2. expand the right `🏘️ OASIS Town` sidebar
3. open or create a topic
4. use `🏘️ OFF/ON` to switch Town Mode
5. use `NUDGE` to inject a live post, `REFORGE` to rebuild the swarm graph, and `EXPLAIN` to ask ReportAgent

The current Clawcross docs should treat this as the canonical Town entry, not the message-center sidebar.

## Troubleshooting

| Symptom | Check |
|---|---|
| Expert name not recognized | verify `tag#temp#N`, `tag#oasis#id`, or `tag#ext#id` format |
| External expert fails immediately | check `api_url`, `api_key`, and `model` |
| OpenClaw session mismatch | ensure `model` and `x-openclaw-session-key` refer to the same session |
| Workflow shape looks wrong | re-check the YAML in [create_workflow.md](./create_workflow.md) |
| Team persona / member mismatch | inspect the Team files in [example_team.md](./example_team.md) |
| Swarm graph never appears | verify `autogen_swarm=true`, then inspect `oasis/swarm_engine.py` and `oasis/graph_memory.py` |
| ReportAgent answers look empty | verify the topic has graph memory, then inspect `/topics/{id}/report/ask` and GraphRAG provider config |
| Expected Zep retrieval does not happen | check `ZEP_API_KEY`, `OASIS_GRAPHRAG_PROVIDER`, and fallback behavior in `config/.env.example` |
| External expert ACP communication fails | verify `acpx` is installed (`which acpx`); if missing, run `npm install -g acpx@latest` or re-run `bash selfskill/scripts/run.sh setup` |

For code-level debugging, inspect the OASIS files listed in [repo-index.md](./repo-index.md).
