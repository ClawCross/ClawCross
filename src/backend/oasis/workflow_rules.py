"""Single source of truth for ClawCross/OASIS Python workflow authoring rules.

Both the in-app workflow code generator (``src/frontend/server.py``) and the MCP tool
``get_workflow_rules(kind="python")`` (in ``src/backend/webot/mcp/oasis.py``) read from
this module so the rules stay consistent everywhere they are surfaced.
"""

from __future__ import annotations


WORKFLOW_WRITING_RULES = """\
# ClawCross Python Workflow (workflowpy) Authoring Rules

A workflowpy script is a self-bootstrapping standalone Python file. It must
be runnable directly:

    python my_workflow.py --question '...' --user-id '...' --team '...'

Reference docs: `docs/workflowpy.md`, `docs/oasis-reference.md`.

## Required structure
- `from oasis.workflow import Context, workflow`
- `async def main(ctx: Context):`
- Decorate it with `@workflow` directly above the def. No `if __name__`
  block is needed; the decorator handles invocation.
- Only switch to the explicit form below when you have other top-level code
  that must execute *after* main:

      from oasis.workflow import Context, run

      async def main(ctx: Context): ...

      if __name__ == "__main__":
          raise SystemExit(run(main))

## Context API (`ctx`)
- Identity: `ctx.question`, `ctx.user_id`, `ctx.team`, `ctx.topic_id`, `ctx.run_id`
- Discovery (synchronous, no `await`):
  - `ctx.list_agents()`, `ctx.list_personas()`
  - `ctx.get_agent(...)`, `ctx.get_persona(...)`
- Sending (async, `await` required):
  - `await ctx.send_agent(agent_id, prompt)`
  - `await ctx.send_agent_once(prompt)` — a newly created temporary agent
  - `await ctx.send_agent_once(name, prompt, tools=[...])` — one task with tools;
    the first argument is its display name, not an existing Agent id
  - `await ctx.send_persona(persona_tag, prompt, tools=[...])`
  - `async with ctx.temporary_agent(name, tools=[...]) as agent:` then
    `await agent.send(prompt)` — a reusable temporary WeBot with its own memory;
    closed at scope exit, also cleaned up at workflow completion/failure/cancellation
  - `await ctx.call_llm(prompt, temperature=..., model=...)` — one-shot bare
    LLM call: no persona, no memory, no tools
  - `await ctx.publish(text, author=...)`
- Topics (async):
  - `await ctx.create_empty_topic(...)`
  - `await ctx.publish_to_topic(...)`
  - `await ctx.conclude_topic(...)`
- Output:
  - `ctx.set_conclusion(short_string)` — short summary
  - `ctx.set_result(structured_dict)` — structured payload

## Topic auto-creation
- The runtime auto-creates an OASIS topic before `main(ctx)` starts, so
  `ctx.topic_id` is usually already set.
- `ctx.publish(...)` writes local logs and also mirrors the message into the
  auto-created topic when one exists.
- Only call `ctx.create_empty_topic(...)` yourself if you intentionally want
  *additional* topics beyond the default one.

## Send result handling
- Send methods return an `AgentReply` with `.ok`, `.content`, `.error`.
- Use attribute access (`reply.content`, `reply.ok`); do NOT treat it as a
  plain dict.
- When persisting send results, only keep JSON-serializable fields like
  `reply.content`, not the raw response object.

## Agent vs Persona selection
- Use `send_agent(...)` for existing concrete agents (call by `agent['id']`).
- Use `send_persona(...)` for role-based one-off speaking (e.g. `creative`,
  `critical`, `entrepreneur`).
- Use `send_agent_once(prompt)` when the workflow needs a newly generated,
  temporary independent agent. Do not invent or ask for random external agent
  ids.
- Tags like `creative`/`critical` are not unique. Prefer iterating over
  `ctx.list_agents()` and passing the chosen `agent['id']` into
  `send_agent(...)` rather than relying on a tag.
- If a required agent is missing, fail clearly via `ctx.set_result(...)` or
  by raising — do NOT silently fall back to "pick the first agent".

## Memory & multi-round prompts
- `send_agent(...)` uses an existing Agent's memory; workflow-critical context should still be passed explicitly
  when later steps depend on earlier outputs.
- `send_agent_once(...)` and `send_persona(...)` start fresh for each task and
  delete their temporary Agent afterward. Without tools they use a lightweight
  model call; `tools=[...]` or `tools="all"` uses a WeBot's full tool loop.
- `temporary_agent(...)` keeps memory between `.send(...)` calls only within
  this workflow. Use separate temporary Agents with `asyncio.gather` for parallel work.
  Tools are stored in each Agent's intrinsic table at creation. The default
  `creation_template="personal"` uses AI review, a command sandbox and a private
  companion workspace; `creation_template="group"` uses strict isolation.
  Optional `workspaces={...}`, `persona_tag=...`, `persona_override=...` and
  `llm={...}` configure this new Agent. No existing Agent is reset or deleted.
- `call_llm(...)` is the cheapest primitive: a single stateless LLM
  completion with no persona framing and no tools. Use it for pure text
  transforms (summarize, classify, rewrite).
- Persist useful replies with `ctx.publish`, `ctx.set_result` or output files
  before the temporary Agent is destroyed. Python workflow code itself is a
  trusted server-side script; the Agent's tool sandbox does not sandbox that Python code.

## Recommended patterns
- Default: get `ctx.list_agents()` for the current team scope, run them
  sequentially, splice prior outputs into the next prompt when later agents
  should see earlier results.
- For "team discussion", prefer serial execution over hidden concurrency
  unless the task clearly benefits from parallel fan-out.
- Hybrid orchestration: fan out to several agents in parallel via
  `asyncio.gather(...)`, publish or collect their replies, then use one
  later serial step to synthesize the combined results.

## Hard constraints
- Use `async`/`await` throughout.
- The script must be directly executable with plain Python.
- Do NOT add any `sys.path` / `PYTHONPATH` / `CLAWCROSS_PYTHONPATH` /
  `load_dotenv` / venv re-exec bootstrap. `oasis.workflow` handles all of
  that at import time.
- Do NOT import from `oasis.python_workflow_cli` directly; always import
  from `oasis.workflow`.
- The workflow file does not need to live inside the repo tree.
- Import extra modules only when needed.
- Finish with `ctx.set_conclusion(...)`, `ctx.set_result(...)`, or by
  returning a final value naturally.
"""


def get_workflow_writing_rules() -> str:
    """Return the canonical workflow authoring rules text."""
    return WORKFLOW_WRITING_RULES


__all__ = ["WORKFLOW_WRITING_RULES", "get_workflow_writing_rules"]
