# WorkflowPy

`workflowpy` is the standalone Python workflow mode for OASIS.

Use it when YAML graph scheduling is too rigid and you want:

- normal Python control flow
- loops, retries, fan-out, and synthesis logic
- direct `send_agent(...)` / `send_persona(...)` calls
- scripts that can run from ClawCross, MCP, or plain CLI
- scripts that can live inside or outside the repository tree

This is the current recommended model. New Python workflow files should be
written using the single-import `oasis.workflow` entry point.

---

## Current Model

A Python workflow is just a `.py` file that:

1. imports from `oasis.workflow`
2. defines `async def main(ctx)`
3. either uses the `@workflow` decorator OR ends with an explicit
   `raise SystemExit(run(main))`

That same file can be started from:

- the orchestration page
- mobile workflow start
- MCP / API wrappers
- plain command line

### Minimum viable example (decorator style)

```python
from oasis.workflow import Context, workflow


@workflow
async def main(ctx: Context):
    agents = ctx.list_agents()
    await ctx.publish(f"loaded {len(agents)} agents", author="workflowpy")
    ctx.set_result({"agent_count": len(agents)})
    ctx.set_conclusion("workflow finished")
```

That is the entire file. No `if __name__ == "__main__"` block, no
`sys.path` bootstrap, no `load_dotenv`, no venv setup.

### Explicit style (when main has helpers defined after it)

```python
from oasis.workflow import Context, run


async def main(ctx: Context):
    ...


if __name__ == "__main__":
    raise SystemExit(run(main))
```

Use this form if you need module-level code to run AFTER `main` is defined.
The decorator triggers `SystemExit` immediately, so anything below an
`@workflow` `main` would be skipped.

---

## What `oasis.workflow` does for you at import time

1. Re-execs into `<project>/.venv/bin/python` if that venv exists and the
   current interpreter is something else. Guarded by `OASIS_REEXEC=1` so it
   cannot loop.
2. Adds `<project>` and `<project>/src` to `sys.path`.
3. Loads `<project>/config/.env` if it is present.
4. Re-exports:
   - `Context` (= `StandaloneWorkflowContext`)
   - `run` (= `run_cli`)
   - `workflow` decorator
   - `PROJECT_ROOT` constant

The project root is derived from the `oasis` package location, so once
`import oasis` works, the venv and config are found automatically.

Source: [`oasis/workflow.py`](../oasis/workflow.py).

---

## Making `oasis` importable

`from oasis.workflow import ...` requires that the `oasis` package is on the
import path. Two practical ways:

- **OASIS auto-launch** (the orchestration page, mobile, `front.py`): the
  runner already injects `CLAWCROSS_PYTHONPATH` and `CLAWCROSS_PROJECT_ROOT`
  into the subprocess environment. No setup needed.
- **Plain CLI from any folder**: install ClawCross once with
  `pip install -e /path/to/ClawCross`, OR set
  `PYTHONPATH=/path/to/ClawCross` for that shell.

After either step, a workflow file in `/tmp`, your home directory, or any
team folder can be run with just:

```bash
python my_workflow.py --question "Do the work" --user-id xinyuan --team my-team
```

Optional flags accepted by `run`:

- `--result-file /tmp/run.json`
- `--no-auto-topic`

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

Definition: [`oasis/python_workflow_cli.py`](../oasis/python_workflow_cli.py)
(re-exported as `Context` from `oasis.workflow`).

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
    f"Original task:\n{ctx.question}\n\nCreative said:\n{r1.content}\n\nRespond to it."
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

By default, `run(main)` auto-creates an OASIS topic.

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

See: [`oasis/workflow_templates/team_all_agents_sequential.py`](../oasis/workflow_templates/team_all_agents_sequential.py)

### Parallel discussion

See: [`oasis/workflow_templates/team_all_agents_parallel.py`](../oasis/workflow_templates/team_all_agents_parallel.py)

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

---

## Agent-Written Workflow Guidance

If an AI agent is asked to generate a Python workflow, it should follow these rules:

- start with `from oasis.workflow import Context, workflow` (or `Context, run`
  for the explicit style)
- implement `async def main(ctx)`
- prefer the `@workflow` decorator; use the explicit `if __name__ == "__main__"`
  form only when other top-level code must run after `main`
- prefer `ctx.send_persona(...)` for persona-only speaking roles
- prefer unique `agent["id"]` when using `ctx.send_agent(...)`
- do not assume implicit memory is enough
- store structured outputs in `ctx.set_result(...)`
- use `ctx.set_conclusion(...)` for a short final summary
- never re-add the legacy try/except sys.path bootstrap; `oasis.workflow`
  handles the venv, sys.path, and `.env` automatically

---

## Legacy Path

There is still an older `python_file -> /topics -> PythonWorkflowEngine` path in
the OASIS server for compatibility.

That path uses the old injected-style execution model and should be treated as a
legacy entrypoint.

The previous "self-bootstrapped script" pattern that imported from
`oasis.python_workflow_cli` directly with a `try/except ModuleNotFoundError`
block still works, but new files should use `oasis.workflow` instead.

Relevant files:

- [`oasis/workflow.py`](../oasis/workflow.py) — single-import entry point
- [`oasis/python_workflow_cli.py`](../oasis/python_workflow_cli.py) — underlying runtime
- [`scripts/run_python_workflow.py`](../scripts/run_python_workflow.py)
- [`src/front.py`](../src/front.py)
- [`oasis/server.py`](../oasis/server.py)

---

## Related Files

- [`oasis/workflow.py`](../oasis/workflow.py)
- [`oasis/python_workflow_cli.py`](../oasis/python_workflow_cli.py)
- [`oasis/python_workflow.py`](../oasis/python_workflow.py)
- [`oasis/agent_center.py`](../oasis/agent_center.py)
- [`oasis/forum_client.py`](../oasis/forum_client.py)
- [`oasis/workflow_templates/team_all_agents_sequential.py`](../oasis/workflow_templates/team_all_agents_sequential.py)
- [`oasis/workflow_templates/team_all_agents_parallel.py`](../oasis/workflow_templates/team_all_agents_parallel.py)
- [`workflowpy_example.py`](./workflowpy_example.py)
- [`create_workflow.md`](./create_workflow.md)
- [`oasis-reference.md`](./oasis-reference.md)
