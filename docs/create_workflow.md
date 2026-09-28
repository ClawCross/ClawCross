# Create OASIS Workflow YAML

> This document describes the OASIS workflow YAML format (Version 2 — Graph Mode) and how to create workflow schedules for Clawcross teams. The format rules are extracted from the visual orchestrator prompt system.

---

## 1. Overview

OASIS workflows define how agents collaborate to solve tasks. A workflow is a directed graph where:
- **Nodes** (`plan`) represent agent / persona steps, manual injections, script execution, human interaction, or special control nodes (selectors)
- **Edges** define execution order — a node runs when all its incoming edges are satisfied
- **Conditional edges** enable branching based on runtime conditions
- **Selector edges** enable LLM-powered routing (the selector node chooses which branch to take)

All YAML schedules use **version: 2** with an explicit graph model.

---

## 2. YAML Format Rules (Version 2 — Graph Mode)

### 2.1 Basic Graph Structure

```yaml
version: 2
repeat: false
plan:
  - id: n1                        # Every node MUST have a unique id
    persona: creative              # Temporary expert wearing the "creative" persona
  - id: n2
    persona: critical
  - id: n3
    agent: Coder                   # One of your agents (in a team: its role name)
  - id: n4
    agent: alice/codex             # Any platform — WeBot, Codex, Claude Code, OpenClaw… — is written the same way
  - id: m1
    manual:
      author: "主持人"
      content: "Please summarize"

edges:                             # Fixed edges: always fire when source completes
  - [n1, n3]                       # n1 → n3
  - [n2, n3]                       # n2 → n3 (fan-in: n3 waits for BOTH n1 and n2)
  - [n3, n4]                       # n3 → n4
  - [n4, m1]                       # n4 → m1
```

### 2.2 Conditional Branching

```yaml
conditional_edges:
  - source: n3
    condition: "last_post_contains:APPROVED"
    then: n4                       # condition true → go to n4
    else: n2                       # condition false → loop back to n2
```

**Supported conditions:**
- `last_post_contains:<keyword>` — last message contains keyword
- `last_post_not_contains:<keyword>` — last message does not contain keyword
- `post_count_gte:<N>` — message count >= N
- `post_count_lt:<N>` — message count < N
- `always` — always true
- `!<expr>` — negate any expression

### 2.3 Selector Routing (LLM-powered Branching)

A selector node is a participant marked with `selector: true`. Its reply determines which branch to take.

```yaml
plan:
  - id: router
    persona: router_tag            # a selector may be an agent or a persona
    selector: true                 # Mark as selector node

selector_edges:
  - source: router
    choices:
      1: branch_a                  # {"clawcross_type": "oasis choose", "choose": 1} → branch_a
      2: branch_b                  # {"clawcross_type": "oasis choose", "choose": 2} → branch_b
      3: __end__                   # {"clawcross_type": "oasis choose", "choose": 3} → end
```

### 2.4 Parallel Groups (within plan)

```yaml
plan:
  - id: brainstorm
    parallel:
      - persona: creative
      - persona: critical
      - agent: Coder
```

### 2.5 Everyone at once

```yaml
plan:
  - id: discuss
    all_experts: true              # every participant named elsewhere in the plan speaks simultaneously
```

---

## 3. Graph Rules

| Rule | Description |
|------|-------------|
| **Unique ID** | Every step MUST have a unique `id` field |
| **Edge-driven execution** | Edges define execution order; nodes with all incoming edges satisfied run in parallel automatically |
| **Entry points** | Nodes with no incoming edges are entry points (start immediately) |
| **Termination** | Use `__end__` as a target in edges to terminate the workflow |
| **Cycles** | The graph supports cycles via conditional/selector edges (for loops and debates) |
| **Fan-in** | A node with multiple incoming edges waits for ALL predecessors to complete |
| **Fan-out** | A node with multiple outgoing edges triggers ALL successors |
| **Selector edges** | Selector nodes (`selector: true`) MUST use `selector_edges` for outgoing connections, NOT regular `edges`. Regular edges after a selector will cause incorrect behavior. |

---

## 4. Participants

A participant is always an agent. There are two ways to name one:

| Key | Who speaks | Memory | Example |
|-----|------------|--------|---------|
| `agent: <ref>` | One of your agents. In a team workflow write the member's **role name**; you may also write its handle, address (`alice/coder`) or `ag_…` id. The platform does not matter — WeBot, Codex, Claude Code, Gemini, OpenClaw and HTTP agents are all written this way | its own, across topics | `agent: Coder` |
| `persona: <tag>` | A temporary expert created for this topic, wearing the persona `<tag>` from the persona library (team `oasis_experts.json`, your custom personas, public and agency personas). Removed when the topic ends | this topic only | `persona: critical` |

Options of `persona:`:

| Option | Default | Meaning |
|--------|---------|---------|
| `tools` | `none` | `none` = one model call per turn (lightest); `all` or a list such as `[read_file, web_search]` = a temporary WeBot session that may call those tools |
| `instance` | `1` | several copies of the same persona in one topic: `instance: 2`, `instance: 3`, … |

`instruction:` (optional, on any participant step) tells that participant what to focus on in this step.

### 4.1 Which one to use

- **`agent:`** when the role needs its own memory, tools or runtime — a coder that keeps context, a Codex/Claude Code agent working in a repository, the team lead.
- **`persona:`** for debates, brainstorming, reviews and one-shot analysis — cheap, parallel, nothing left behind.

### 4.2 Personas vs agents in a team

A team's `oasis_experts.json` is its persona library (prompts, looked up by `tag`). Its members are agents with role names (`clawcross team "<team>" members`). A member usually wears one of the team's personas, but the workflow names the member (`agent: <role>`), not the persona.

---

## 5. Available Step Types

All step types require an `id` field.

| Step Type | Key | Description |
|-----------|-----|-------------|
| Agent | `agent: "coder"` | One of your agents speaks (see §4) |
| Persona | `persona: "critical"` (+ `tools`, `instance`) | A temporary expert speaks (see §4) |
| Parallel | `parallel: [...]` | Several participants speak simultaneously |
| Everyone | `all_experts: true` | Every participant named in the plan speaks at once |
| Manual | `manual: {author, content}` | Inject fixed text (no LLM call) |
| Script | `script: {...}` | Run a platform command via Python-managed subprocess |
| Human | `human: {...}` | Pause workflow and wait for a plain-text human reply |
| Selector | `selector: true` + `agent`/`persona` | Routing node: its reply picks the branch |

`agent:` and `persona:` work inside `parallel:` lists too:

```yaml
plan:
  - id: plan
    agent: Planner                 # the team's role "Planner"
  - id: review
    parallel:
      - agent: alice/codex         # an external agent by address
      - persona: critical          # one LLM call, no tools
      - persona: security_auditor  # a temporary session that may read files
        tools: [read_file, list_files]
```

### 5.1 Manual Nodes — Special Authors

Manual nodes support special `author` values for workflow control:

| Author | Purpose |
|--------|---------|
| `begin` | Marks the workflow start point |
| `bend` | Marks the workflow end point |
| Custom string | Displays as the speaker name (e.g., `"主持人"`) |

```yaml
- id: start
  manual:
    author: begin
    content: "讨论开始"
- id: end
  manual:
    author: bend
    content: "讨论结束"
```

### 5.2 Script Nodes

Script nodes execute a command and publish the result as a normal forum post, so downstream
conditions, selectors, and summary steps can consume the output directly.

```yaml
- id: run_tests
  script:
    unix_command: "pytest -q"
    windows_command: "python -m pytest -q"
    timeout: 120
    cwd: "."
```

Rules:
- Use `unix_command` / `windows_command` when the command differs by platform
- Or use a shared `command` field when one command works everywhere
- `timeout` is optional; when omitted, the runtime falls back to the workflow timeout defaults
- `cwd` is optional and is constrained to the Clawcross project root or current team directory

### 5.3 Human Nodes

Human nodes behave like blocking workflow steps: OASIS posts the prompt, pauses at that node,
and waits for a human to submit a normal text reply.

```yaml
- id: confirm_release
  human:
    prompt: "请确认是否继续发布，并说明原因"
    author: "主持人"
```

Rules:
- Human replies do **not** require JSON
- The workflow canvas only configures the prompt and display author; it does **not** capture the reply itself
- The runtime reply is submitted from the OASIS topic detail UI or from the CLI `topics human-reply` command
- In both Studio and mobile chat, the reply box belongs to the **OASIS topic detail view**, not the workflow editor
- Downstream nodes receive the human reply as a normal forum post

Runtime behavior:
- When execution reaches a `human` node, OASIS creates a normal post for the prompt and marks the topic as waiting for human input
- The topic detail page shows a plain-text input only while that node is pending
- After the human reply is submitted, the `human` node is considered complete and downstream edges continue normally
- If no reply arrives before timeout, OASIS records a timeout post and the workflow proceeds according to its graph

---

## 6. Complete Examples

### 6.1 Simple Sequential Pipeline

Three personas discuss in sequence:

```yaml
version: 2
repeat: false
plan:
  - id: n1
    persona: creative
  - id: n2
    persona: critical
  - id: n3
    persona: synthesis
edges:
  - [n1, n2]
  - [n2, n3]
```

```mermaid
graph LR
    n1["💡 Creative"] --> n2["🔍 Critical"] --> n3["🎯 Synthesis"]
```

### 6.2 Fan-in Parallel → Merge

Two personas work in parallel, then a synthesizer merges their outputs:

```yaml
version: 2
repeat: false
plan:
  - id: creative
    persona: creative
  - id: data
    persona: data
  - id: merge
    persona: synthesis
edges:
  - [creative, merge]
  - [data, merge]
```

```mermaid
graph LR
    creative["💡 Creative"] --> merge["🎯 Synthesis"]
    data["📊 Data"] --> merge
```

### 6.3 Selector Loop with Exit

A reviewer checks work and decides to loop back or finish:

```yaml
version: 2
repeat: false
plan:
  - id: start
    manual:
      author: begin
      content: "开始代码审查"
  - id: coder
    persona: coder
  - id: reviewer
    persona: critical
    selector: true
  - id: done
    manual:
      author: bend
      content: "审查完成"
edges:
  - [start, coder]
  - [coder, reviewer]
selector_edges:
  - source: reviewer
    choices:
      1: coder       # needs revision → loop back
      2: done         # approved → end
```

```mermaid
graph LR
    start["🟢 Begin"] --> coder["💻 Coder"]
    coder --> reviewer{"🔍 Reviewer"}
    reviewer -->|"1: revise"| coder
    reviewer -->|"2: approve"| done["🔴 End"]
```

### 6.4 Script + Human Hybrid Flow

Use a script node to gather machine output, then pause for a human decision:

```yaml
version: 2
discussion: false
repeat: false
plan:
  - id: collect_status
    script:
      unix_command: "git status --short"
      windows_command: "git status --short"
      timeout: 30
  - id: human_gate
    human:
      prompt: "请检查上面的脚本输出，并决定是否继续"
      author: "主持人"
  - id: summarize
    persona: synthesis
edges:
  - [collect_status, human_gate]
  - [human_gate, summarize]
```

### 6.5 Mixed Pipeline: Personas, Team Members and a Selector

Combines temporary personas, two team members (one on WeBot, one on OpenClaw) and a selector:

```yaml
version: 2
repeat: false
plan:
  - id: begin
    manual:
      author: begin
      content: "讨论开始"
  - id: creative
    persona: creative
  - id: synth
    agent: 综合顾问                 # a team member (WeBot) with its own memory
  - id: arch
    persona: architect
  - id: ext_agent
    agent: Researcher               # a team member running on OpenClaw — written the same way
  - id: selector
    persona: selector
    selector: true
  - id: end
    manual:
      author: bend
      content: "讨论结束"
edges:
  - [begin, creative]
  - [creative, synth]
  - [synth, arch]
  - [arch, ext_agent]
  - [ext_agent, selector]
selector_edges:
  - source: selector
    choices:
      1: ext_agent    # continue discussion
      2: end          # finish
```

### 6.6 Conditional Branching

Route based on content of the last message:

```yaml
version: 2
repeat: false
plan:
  - id: analyzer
    persona: data
  - id: approve_path
    persona: synthesis
  - id: reject_path
    persona: critical
edges:
  - [analyzer, approve_path]       # default edge (may be overridden by conditional)
conditional_edges:
  - source: analyzer
    condition: "last_post_contains:REJECT"
    then: reject_path
    else: approve_path
```

---

## 7. Settings Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `version` | int | `2` | Must be `2` for graph mode |
| `repeat` | bool | `false` | `true` = repeat plan every round (debates), `false` = execute once (pipelines) |

---

## 8. Workflow File Location

Workflow YAML files are stored at:

- **Team workflows**: `data/user_files/{user_id}/teams/{team}/oasis/yaml/*.yaml`
- **Public workflows**: `data/user_files/{user_id}/oasis/yaml/*.yaml`

### 8.1 Save via CLI

```bash
# Save a workflow for a team
uv run scripts/cli.py workflows save \
  --team <TEAM_NAME> \
  --name <WORKFLOW_NAME> \
  --yaml-file <PATH_TO_YAML>
```

### 8.2 Save via MCP Tool

The `save_oasis_workflow` MCP tool can be used to save a workflow:
- Provide a descriptive `name` (e.g., `code_review_pipeline`, `brainstorm_trio`)
- Pass the YAML as `content`, with `kind="yaml"`

---

## 9. Execute and Monitor Workflow via CLI

After saving a workflow, you can execute and monitor it using the CLI:

### 9.1 List Workflows

```bash
# List all workflows for a team
uv run scripts/cli.py workflows list --team <TEAM_NAME>
```

### 9.2 Run a Workflow

```bash
# Execute a workflow with a question
uv run scripts/cli.py workflows run \
  --team <TEAM_NAME> \
  --name <WORKFLOW_NAME> \
  --question "your question or task here" \
  --max-rounds <MAX_ROUNDS> \
  [--output <OUTPUT_FILE>]
```

**Parameters:**
- `--question`: The input question or task for the workflow (e.g., "需要开发一个新的系统...")
- `--max-rounds`: Maximum number of discussion rounds (e.g., `--max-rounds 10`)
- `--output`: Optional output file to save the conversation JSON

**Example:**
```bash
uv run scripts/cli.py workflows run \
  --team DevTeam \
  --name product_review_pipeline \
  --question "需要开发一个在线客服系统" \
  --max-rounds 10
```

The command will print the topic ID (e.g., `Topic created: 94a2cbb7`) for tracking.

### 9.3 Monitor Workflow Status

```bash
# View workflow execution details and current status
uv run scripts/cli.py topics show --topic-id <TOPIC_ID>
```

### 9.4 Get Final Conclusion

```bash
# Wait for workflow completion and retrieve final summary
uv run scripts/cli.py workflows conclusion \
  --topic-id <TOPIC_ID> \
  [--output <OUTPUT_FILE>] \
  [--timeout <SECONDS>]
```

**Parameters:**
- `--topic-id`: The topic ID returned when running the workflow
- `--timeout`: Maximum time to wait for completion in seconds (default: 120)

### 9.5 Live Watch

```bash
# Real-time monitoring of workflow progress
uv run scripts/cli.py topics watch --topic-id <TOPIC_ID>
```

---

## 10. Tips & Best Practices

1. **Maximize parallelism**: Nodes with no dependency relationship should run concurrently. Use fan-in/fan-out patterns.
2. **Use selectors for loops**: When you need iterative refinement, use a selector node to decide whether to loop or exit.
3. **Begin/End markers**: Use `manual` nodes with `author: begin` and `author: bend` to clearly mark workflow boundaries.
4. **Agents for work that needs memory or a runtime**: `agent: <role>` — a coder keeping context, a Codex / Claude Code / OpenClaw agent with its own tools.
5. **Personas for debates**: `persona: <tag>` — lightweight, parallel, gone when the topic ends; add `tools:` only when the step must read files or search.
6. **Edge ordering**: Selector node outgoing edges should be defined in `selector_edges`, not in regular `edges`.
7. **Selector edge restriction**: Selector nodes (`selector: true`) must NOT have outgoing edges defined in the regular `edges` section. All outgoing edges from a selector MUST be defined in `selector_edges`. This is a critical rule — violating it will cause the workflow to behave incorrectly.
