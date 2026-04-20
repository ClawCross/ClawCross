# WorkflowPy

`workflowpy` is the standalone Python workflow mode for OASIS.

Use it when YAML graph scheduling is too rigid and you want:

- normal Python control flow
- loops, retries, fan-out, and synthesis logic
- direct `send_agent(...)` / `send_persona(...)` calls
- scripts that can run from ClawCross, MCP, or plain CLI
- scripts that can live inside or outside the repository tree

This is the current recommended model. New Python workflow files should be
written as **self-bootstrapped scripts**, not the old injected top-level style.

---

## Current Model

A Python workflow is just a `.py` file that:

1. makes `oasis.python_workflow_cli` importable
2. imports `StandaloneWorkflowContext` and `run_cli`
3. defines `async def main(ctx)`
4. ends with `raise SystemExit(run_cli(main))`

That means the same file can be started from:

- the orchestration page
- mobile workflow start
- MCP / API wrappers
- plain command line

Example:

```python
import os
import sys

try:
    from oasis.python_workflow_cli import StandaloneWorkflowContext, run_cli
except ModuleNotFoundError:
    extra_paths = [
        p for p in os.environ.get("CLAWCROSS_PYTHONPATH", "").split(os.pathsep) if p
    ]
    project_root = os.environ.get("CLAWCROSS_PROJECT_ROOT", "").strip()
    if project_root:
        extra_paths.append(project_root)
    for path_entry in extra_paths:
        if path_entry and path_entry not in sys.path:
            sys.path.insert(0, path_entry)
    from oasis.python_workflow_cli import StandaloneWorkflowContext, run_cli


async def main(ctx: StandaloneWorkflowContext):
    agents = ctx.list_agents()
    await ctx.publish(f"loaded {len(agents)} agents", author="workflowpy")
    ctx.set_result({"agent_count": len(agents)})
    ctx.set_conclusion("workflow finished")


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
```

This is the **minimum viable pattern**:

- import the runtime
- define `main(ctx)`
- return results through `ctx`
- exit through `run_cli(main)`

If `oasis.python_workflow_cli` is already importable, do not add any bootstrap code.
If it is not importable yet, any import strategy is acceptable:

- set `PYTHONPATH`
- append your own custom `sys.path`
- use a wrapper script
- set `CLAWCROSS_PYTHONPATH`
- set `CLAWCROSS_PROJECT_ROOT`
- install the project in editable mode

The workflow file does not need to live under the repository tree.

Two practical consequences:

- a file under `data/user_files/.../oasis/python/` is just a convenient saved workflow location
- a file under `/tmp`, another repo, or your home directory can also work if imports are available

---

## How To Run

### 1. From plain Python

```bash
python my_workflow.py --question "Do the work" --user-id xinyuan --team my-team
```

If the script lives in any arbitrary folder and the imports are not already
available, run it with an explicit import path:

```bash
CLAWCROSS_PYTHONPATH="/abs/path/to/ClawCross:/abs/path/to/ClawCross/src" \
python /any/folder/my_workflow.py --question "Do the work" --user-id xinyuan --team my-team
```

Optional:

- `--result-file /tmp/run.json`
- `--no-auto-topic`

### 1a. Repo-external script, explicit import path

This is the safest way to run a workflow file that lives outside the repository:

```bash
CLAWCROSS_PYTHONPATH="/abs/path/to/ClawCross:/abs/path/to/ClawCross/src" \
/abs/path/to/ClawCross/.venv/bin/python /any/folder/my_workflow.py \
  --question "Do the work" \
  --user-id xinyuan \
  --team my-team
```

This works because:

- the interpreter comes from the project runtime
- `CLAWCROSS_PYTHONPATH` makes both `oasis/` and `src/` imports available

Using the project interpreter alone is often not enough for repo-external files.
The import path still has to be available.

### 2. From the wrapper script

```bash
python scripts/run_python_workflow.py my_workflow.py --question "Do the work" --user-id xinyuan --team my-team
```

### 3. From ClawCross

- Orchestration page Python mode
- Mobile workflow start

These now call the standalone runner path for Python workflows.

---

## What `ctx` Provides

The workflow entrypoint receives:

- `ctx.question`
- `ctx.user_id`
- `ctx.team`
- `ctx.run_id`
- `ctx.topic_id`
- `ctx.auto_topic`
- `ctx.result`
- `ctx.conclusion`
- `ctx.published_messages`

Helper methods:

- `ctx.list_agents()`
- `ctx.list_personas()`
- `ctx.get_agent(target)`
- `ctx.get_persona(target)`
- `await ctx.send_agent(...)`
- `await ctx.send_persona(...)`
- `await ctx.publish(...)`
- `await ctx.create_empty_topic(...)`
- `await ctx.publish_to_topic(...)`
- `await ctx.conclude_topic(...)`
- `ctx.set_result(value)`
- `ctx.set_conclusion(text)`

Definition:

- [`oasis/python_workflow_cli.py`](../oasis/python_workflow_cli.py)

---

## Important Rules

### 1. `list_agents()` and `list_personas()` are synchronous

Use:

```python
agents = ctx.list_agents()
```

Not:

```python
agents = await ctx.list_agents()
```

### 2. Prefer unique agent ids

Do not assume tags like `creative` are unique.

Safer pattern:

```python
agents = ctx.list_agents()
target = next((a for a in agents if a.get("id") == "internal:创意专家"), None)
```

If you only need a persona-style one-shot response, prefer:

```python
reply = await ctx.send_persona("creative", ctx.question)
```

### 3. `send_agent(...)` returns `SendToAgentResult`

Use attribute access:

```python
reply = await ctx.send_agent(agent_id, prompt)
text = reply.content or ""
ok = reply.ok
err = reply.error
```

Do not rely on dict-style response parsing for new code.

### 4. Do not depend on implicit history for correctness

Some agents may have session memory, but workflow-critical context should still
be explicitly included in later prompts.

If round 2 depends on round 1:

```python
r1 = await ctx.send_persona("creative", ctx.question)
r2 = await ctx.send_persona(
    "critical",
    f"Original task:\\n{ctx.question}\\n\\nCreative said:\\n{r1.content}\\n\\nRespond to it."
)
```

### 5. `set_conclusion(...)` should be a string

Put structured payloads into:

```python
ctx.set_result({...})
```

Use:

```python
ctx.set_conclusion("workflow finished")
```

---

## Auto Topic Behavior

By default, `run_cli(main)` auto-creates an OASIS topic.

That means:

- `ctx.topic_id` usually exists at startup
- `await ctx.publish(...)` mirrors messages into that topic
- completion auto-concludes the topic
- failures are also mirrored into the topic

Disable with:

```bash
python my_workflow.py --question "..." --no-auto-topic
```

If you disable auto topic, the script can still create one manually:

```python
topic = await ctx.create_empty_topic(question=ctx.question, max_rounds=1)
await ctx.publish_to_topic(
    topic_id=topic["topic_id"],
    author="script",
    content="workflow started",
)
```

---

## `publish(...)` and OASIS Reply JSON

`ctx.publish(...)` supports two modes.

### Plain text

```python
await ctx.publish("hello", author="workflowpy")
```

This posts plain text.

### Structured OASIS reply

If content is valid JSON like:

```json
{
  "clawcross_type": "oasis reply",
  "reply_to": 2,
  "content": "I agree with this direction",
  "votes": [
    {"post_id": 1, "direction": "up"}
  ]
}
```

then `ctx.publish(...)` will automatically:

- publish `content`
- attach `reply_to`
- apply `votes`

Example:

```python
await ctx.publish(
    '{"clawcross_type":"oasis reply","reply_to":2,"content":"同意这个方向","votes":[{"post_id":1,"direction":"up"}]}',
    author="workflowpy",
)
```

If the content is not valid OASIS reply JSON, it is posted as normal text.

---

## Human-Written Workflow Patterns

### Sequential discussion

See:

- [`oasis/workflow_templates/team_all_agents_sequential.py`](../oasis/workflow_templates/team_all_agents_sequential.py)

### Parallel discussion

See:

- [`oasis/workflow_templates/team_all_agents_parallel.py`](../oasis/workflow_templates/team_all_agents_parallel.py)

### Hybrid fan-out then synthesis

The default editor scaffold uses:

- parallel fan-out
- publish successful replies
- one later agent synthesizes

This is usually a good default for team discussion workflows.

### Software delivery loop

For an engineering team, a useful Python-native pattern is:

1. Product/PM creates the scoped delivery brief
2. Architect defines build order and interfaces
3. Frontend and backend run in parallel
4. QA performs review / ATE-style acceptance checks
5. PM acts as final product acceptance gate
6. If rejected, review feedback loops back into another implementation round
7. DevOps writes the deployment and rollback plan

This kind of iterative delivery loop is awkward in YAML but straightforward in Python.

Concrete examples:

- repo example: [`docs/workflowpy_example.py`](./workflowpy_example.py)
- repo-external example: [`/home/avalon/.openclaw/workspace/skills/testworkflow_code_team.py`](/home/avalon/.openclaw/workspace/skills/testworkflow_code_team.py:1)
- team-saved example: [`data/user_files/default/teams/Code Team/oasis/python/code_team_full_delivery_loop.py`](/home/avalon/.openclaw/workspace/skills/ClawCross/data/user_files/default/teams/Code%20Team/oasis/python/code_team_full_delivery_loop.py:1)

---

## Agent-Written Workflow Guidance

If an AI agent is asked to generate a Python workflow, it should follow these rules:

- output a self-bootstrapped standalone script
- import `StandaloneWorkflowContext` and `run_cli`
- implement `async def main(ctx)`
- finish with `raise SystemExit(run_cli(main))`
- prefer `ctx.send_persona(...)` for persona-only speaking roles
- prefer unique `agent["id"]` when using `ctx.send_agent(...)`
- do not assume implicit memory is enough
- store structured outputs in `ctx.set_result(...)`
- use `ctx.set_conclusion(...)` for a short final summary
- when generating a repo-external file, do not hardcode a repository path; prefer normal imports first, then `CLAWCROSS_PYTHONPATH` / `CLAWCROSS_PROJECT_ROOT`

---

## Legacy Path

There is still an older `python_file -> /topics -> PythonWorkflowEngine` path in
the OASIS server for compatibility.

That path uses the old injected-style execution model and should be treated as a
legacy entrypoint.

New frontends and new Python workflow files should use the standalone runner path
instead.

Relevant files:

- [`oasis/python_workflow_cli.py`](../oasis/python_workflow_cli.py)
- [`scripts/run_python_workflow.py`](../scripts/run_python_workflow.py)
- [`src/front.py`](../src/front.py)
- [`oasis/server.py`](../oasis/server.py)

---

## Related Files

- [`oasis/python_workflow_cli.py`](../oasis/python_workflow_cli.py)
- [`oasis/python_workflow.py`](../oasis/python_workflow.py)
- [`oasis/agent_center.py`](../oasis/agent_center.py)
- [`oasis/forum_client.py`](../oasis/forum_client.py)
- [`oasis/workflow_templates/team_all_agents_sequential.py`](../oasis/workflow_templates/team_all_agents_sequential.py)
- [`oasis/workflow_templates/team_all_agents_parallel.py`](../oasis/workflow_templates/team_all_agents_parallel.py)
- [`workflowpy_example.py`](./workflowpy_example.py)
- [`create_workflow.md`](./create_workflow.md)
- [`oasis-reference.md`](./oasis-reference.md)
