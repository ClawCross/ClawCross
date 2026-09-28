# Team Anatomy (Runtime Reference)

A single concrete example of what a running ClawCross team is made of. We use **`paper-review-council`** as the case study because it touches everything a team can have: member agents, persona prompts, a YAML workflow, a Python workflow, team-scoped SKILLs, and preset stamps.

> If you want command-line creation, read [`build_team.md`](./build_team.md). If you want the browser flow, read [`team-creator.md`](./team-creator.md). For the full YAML grammar see [`create_workflow.md`](./create_workflow.md). This document is just "what is a team made of."

---

## 1. Where the team lives

```
<CLAWCROSS_HOME>/data/user_files/<user_id>/teams/<team_name>/
```

For the example below: `~/.clawcross/data/user_files/default/teams/paper-review-council/`.

`CLAWCROSS_HOME` defaults to `~/.clawcross` (override with the env var). Forbidden in `<team_name>`: `/`, `\`, leading `.`.

---

## 2. Full layout

```
paper-review-council/
├── oasis_experts.json                # persona prompts (required)
├── team_settings.json                # team-level settings (optional)
├── clawcross_preset_manifest.json    # preset stamp (optional)
├── clawcross_preset_source_map.json  # preset stamp (optional)
├── oasis/
│   ├── yaml/
│   │   └── paper_review_council.yaml      # YAML workflow plan
│   └── python/
│       └── paper_survey_workflow.py       # Python workflow
└── skills/
    ├── SKILLS_INDEX.md
    └── paper-survey/
        ├── SKILL.md
        ├── run.sh
        ├── run.py
        ├── pyproject.toml
        └── runtime_config.example.json
```

The team's **members are not files**. They are agents registered in ClawCross (`<DATA_DIR>/clawcross.db`), and the team records which agents belong to it, under which role name, and which one leads. See §3.

The minimum to be a usable team is: members + `oasis_experts.json` + at least one workflow under `oasis/yaml/` or `oasis/python/`.

Cron / alarm storage is **not** in the team folder either — tasks live in `<DATA_DIR>/timeset/tasks.json` and each one targets an agent by id (§10).

---

## 3. Members — agents with a role

Every agent on the machine — WeBot, Codex, Claude Code, Gemini, OpenClaw, any OpenAI-compatible HTTP service — is one kind of thing: an `ag_…` id, a name, a platform and its settings (the persona `tag` it wears, and for other platforms the runtime name `global_name`, `api_url`, `model`, …). Its address is `<user>/<handle>`, e.g. `default/search-commander`.

A team **composes** agents; it does not own them. Each membership has:

| Field | Meaning |
|---|---|
| role | the member's name in this team — used by `agent: <role>` in workflows, by `@` in the team's group chat, and as a cron target |
| lead | at most one member: speaks for the team (`POST /v1/teams/<team>/messages`, or `model: "<user>/<team>"` on the OpenAI-compatible API) and is the primary agent of the team's group chat |

One agent can be a member of several teams under different role names. Removing a member leaves the agent in place. Deleting a team from the UI or `clawcross team delete` also deletes the member agents that belong to no other team (`DELETE /v1/teams/<team>` removes only the team).

```bash
clawcross team "paper-review-council" members        # role, platform, address of each member
curl -s localhost:51200/v1/teams/paper-review-council -H "Authorization: Bearer <user>:<password>"
```

### 3.1 The import format: `internal_agents.json` / `external_agents.json`

A team *package* — a preset, a snapshot zip, or a folder written by hand or by team-builder — declares its members in two files. `internal_agents.json` lists WeBot members:

```json
[
  { "name": "搜索指挥者",  "tag": "search_commander", "is_primary": true },
  { "name": "论文汇报者",  "tag": "paper_reporter" }
]
```

`external_agents.json` lists members on other platforms:

```json
[
  { "name": "Codex Reviewer", "tag": "critical", "platform": "codex", "global_name": "codex_reviewer" },
  { "name": "Researcher", "tag": "analyst", "platform": "openclaw", "global_name": "paper_researcher",
    "meta": { "model": "agent:paper_researcher" } }
]
```

- `name` — the role name; `tag` — the persona the agent wears; `is_primary` — the lead.
- `platform` — `codex` / `claude-code` / `gemini-cli` / `openclaw` / any HTTP service name; `global_name` — the agent's name on that platform; `meta` — `api_url`, `model`, `headers` (never an `api_key` in a package).
- An entry **without** `session` (internal) / with a `global_name` not yet registered (external) creates a new agent. An internal entry naming an existing `session` joins that existing WeBot agent.

Importing (`clawcross team import "<team>"`, `POST /v1/teams/<team>/import`, installing a preset, uploading a snapshot) turns the entries into agents and memberships and removes the two files from the team folder. Exporting a snapshot writes them again in the same shape, leaving out this machine's `session` / `global_name` and any secrets so that importing elsewhere creates fresh agents.

---

## 4. `oasis_experts.json` — persona prompts

A flat list of **prompts**, not agents. Several members may wear the same `tag`, and every `persona: <tag>` step in a workflow uses the same prompt entry.

```json
[
  {
    "name": "搜索指挥者",
    "tag": "search_commander",
    "persona": "你是团队中的内部 Agent「搜索指挥者」，负责在论文搜索/审读开始前制定检索与审读调度方案…\n\n输出格式：\n[搜索指挥]\n候选专家: tag - 适用范围\n选择专家: tag1, tag2, ...\n主搜索persona: tag1\n搜索重点:\n- tag1: ...",
    "temperature": 0.3,
    "category": "orchestration",
    "description": "内部 Agent 的检索调度与专家选择人设。"
  },
  {
    "name": "机器学习论文审读专家",
    "tag": "ml_reviewer",
    "persona": "你是机器学习与人工智能方向的论文审读专家。\n\n重点审理：模型结构、训练目标、数据划分、基线选择、消融实验…",
    "temperature": 0.4,
    "category": "computer-science",
    "description": "审理 ML/AI/CV/NLP/多模态论文。"
  }
]
```

| Field | Required | Notes |
|---|---|---|
| `name`, `tag`, `persona` | yes | Prompt body in `persona`; multi-line via `\n`. |
| `temperature` | no | Float, 0.3 strict ↔ 0.9 creative. |
| `category`, `description` | no | Free-form, used by listing UIs. |
| `model`, `api_key`, `base_url`, `provider` | no | Per-persona LLM override; falls back to global `LLM_*` env vars when omitted. |

> **Lookup rule:** a persona is found by `tag` — the team's `oasis_experts.json` first, then your custom personas, then the public and agency pools. Get the tag wrong and the agent silently falls back to a public-pool persona of that tag, or speaks with no persona at all.

---

## 5. Members on other platforms

A Codex, Claude Code or Gemini member runs through `acpx` in the session named by its `global_name`; an HTTP member is called at its `api_url`. Nothing else about them differs from a WeBot member — same commands, same workflow syntax, same group chat.

An OpenClaw member is an agent that lives in OpenClaw's own workspace, so it must exist there first (`openclaw add`, or pick one from `openclaw sessions`). Its workspace files and tool configuration can be saved into the team (`openclaw-snapshot export` / `export-all`), which is what lets a snapshot recreate the OpenClaw side on another machine.

---

## 6. `oasis/yaml/<workflow>.yaml` — YAML workflow

`paper_review_council.yaml` — search commander fans out to 7 domain reviewers, all converge on the reporter:

```yaml
version: 2
repeat: false
plan:
- id: p0
  manual: { author: begin, content: "请提供要审读的论文信息…" }

- id: p1
  agent: 搜索指挥者                                   # a team member, by role name
  instruction: "请根据论文信息…输出应选择的专家 tag。"

- id: p2
  persona: ml_reviewer                                # a temporary expert wearing a persona
- id: p3
  persona: systems_reviewer
# … p4..p8 are sibling domain reviewers …

- id: p9
  agent: 论文汇报者

- id: p10
  manual: { author: bend, content: "论文审读汇报已完成。" }

edges:
- [p0, p1]
- [p1, p2]                                            # commander fans out to all 7 reviewers
- [p1, p3]
- [p1, p4]
- [p1, p5]
- [p1, p6]
- [p1, p7]
- [p1, p8]
- [p2, p9]                                            # all reviewers converge on reporter
- [p3, p9]
- [p4, p9]
- [p5, p9]
- [p6, p9]
- [p7, p9]
- [p8, p9]
- [p9, p10]
```

Node forms (see `create_workflow.md` for the full grammar):

| Form | Meaning |
|---|---|
| `manual: { author, content }` | No LLM call; injects fixed text. `begin` / `bend` are the conventional book-ends. |
| `agent: <role>` | A team member speaks, with its own memory and runtime (any platform). |
| `persona: <tag>` | A temporary expert wearing that persona speaks; `tools: none` (default) / `all` / `[names]`; removed when the topic ends. |
| Add `selector: true` | The node's output is treated as a routing key, consumed by `selector_edges:`. |

`edges` are unconditional fan-out / fan-in. Selector nodes use `selector_edges:` instead.

---

## 7. `oasis/python/<workflow>.py` — Python workflow

YAML is enough for fixed graphs, but `paper-review-council` needs to:

1. Parse the user's free-text question for `--topic`, `--max-papers`, conferences/arxiv hints.
2. Ask the search commander persona which 1–3 domain experts to invoke.
3. Subprocess-run `skills/paper-survey/run.sh` (a system command) and stream its progress to the chat.
4. Pass the survey output into each selected expert and the final reporter.

That's dynamic dispatch over data the YAML grammar can't express, so it's written in Python.

### 7.1 The contract

A workflow file is a regular Python module that:

```python
from oasis.workflow import Context, workflow

@workflow
async def main(ctx: Context):
    # ... your orchestration here ...
    ctx.set_result({"key": "value"})
    ctx.set_conclusion("one-line summary shown to the user")
```

- Decorate with `@workflow` (from `oasis.workflow`). If the file is executed as a script (`python my_wf.py …`) the decorator runs the function via `run_cli` and exits. If the file is imported (e.g. for tests) the decorator returns the function unchanged.
- `main` is `async def main(ctx: Context)`. The runtime awaits it.
- `oasis.workflow` import-time side-effects: re-exec into `$CLAWCROSS_VENV_DIR/bin/python` if needed, add project to `sys.path`, load `$CLAWCROSS_CONFIG_DIR/.env`. You do not have to do any of this yourself.
- The file lives at `teams/<team>/oasis/python/<wf>.py`. That fixes its on-disk path:
  ```python
  Path(__file__).resolve().parents[2]   # == teams/<team>/
  ```

### 7.2 What `ctx` gives you

`ctx` is `StandaloneWorkflowContext` (re-exported as `Context`).

**Fields (set by the runtime before `main` is called):**

| Field | Meaning |
|---|---|
| `ctx.user_id` | User id who started the workflow. |
| `ctx.team` | Team folder name. |
| `ctx.question` | The free-text input the user typed (the argument to `clawcross workflow run … question <text>`). |
| `ctx.run_id` | Short unique id for this run; safe for filenames. |
| `ctx.topic_id` | Topic id if `auto_topic` was on (else `None`). |

**Async methods (call with `await`):**

| Method | Purpose |
|---|---|
| `await ctx.publish(content, author="…")` | Push a message to the user-visible topic. Use this for progress updates and final reports. |
| `await ctx.send_persona(tag, prompt)` | Invoke a persona by `tag`. Returns a result object with `.content`, `.ok`, `.error`. |
| `await ctx.send_agent(target, prompt, ...)` | Ask a team member (role name) or any of your agents (handle, address or `ag_…` id). |
| `await ctx.create_empty_topic(question=...)` | Start a fresh topic (only needed when `auto_topic=False`). |
| `await ctx.publish_to_topic(topic_id=..., author=..., content=...)` | Direct topic write. |
| `await ctx.conclude_topic(topic_id=..., conclusion=...)` | Close a topic. |

**Sync helpers:**

| Method | Purpose |
|---|---|
| `ctx.list_agents()` | The team's members: role, agent id, address, platform, persona. |
| `ctx.list_personas()` | List `oasis_experts.json` entries. |
| `ctx.get_agent(target)` / `ctx.get_persona(tag)` | Look up a member / agent, or a persona by tag. |
| `ctx.set_result(value)` | Store the structured final result (becomes the workflow's return payload). |
| `ctx.set_conclusion(text)` | Store the human-readable one-liner shown at the end. |

### 7.3 Annotated walkthrough — `paper_survey_workflow.py`

```python
import asyncio, json, os, re
from pathlib import Path
from oasis.workflow import Context, workflow

DOMAIN_EXPERT_TAGS = {
    "ml_reviewer", "systems_reviewer", "biomed_reviewer",
    "econ_finance_reviewer", "social_science_reviewer",
    "statistics_reviewer", "humanities_reviewer",
}

# Path helpers — derive everything from this file's location.
def _team_dir(ctx):  return Path(__file__).resolve().parents[2]
def _skill_dir(ctx): return _team_dir(ctx) / "skills" / "paper-survey"
def _output_dir(ctx):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", ctx.run_id or "run")
    return _skill_dir(ctx) / "output" / "workflow_runs" / safe


@workflow
async def main(ctx: Context):
    # ── 1. Read team state ───────────────────────────────────────────
    skill_dir = _skill_dir(ctx)
    if not (skill_dir / "run.sh").is_file():
        raise RuntimeError(f"paper-survey skill not found: {skill_dir}")

    # ctx.list_personas() returns the team's oasis_experts.json entries.
    experts = [
        {"tag": p["tag"], "name": p["name"], "summary": p["persona"][:260]}
        for p in ctx.list_personas()
        if p.get("tag") in DOMAIN_EXPERT_TAGS
    ]
    available_tags = {e["tag"] for e in experts}

    # ── 2. Ask the commander persona who to invoke ──────────────────
    commander_prompt = (
        f"用户启动工作流的要求：\n{ctx.question}\n\n"
        f"可用专家人设列表：\n{json.dumps(experts, ensure_ascii=False, indent=2)}\n\n"
        "请输出选择专家和搜索重点。必须包含一行：选择专家: tag1, tag2, ..."
    )
    reply = await ctx.send_persona("search_commander", commander_prompt)
    commander_text = reply.content or ""
    await ctx.publish(commander_text, author="搜索指挥者")

    # ── 3. Parse the commander's tags out of its free text ──────────
    selected_tags = _parse_selected_tags(commander_text, available_tags) or ["ml_reviewer"]
    primary_tag = selected_tags[0]
    output_dir = _output_dir(ctx)

    # ── 4. Stream a SKILL subprocess into the chat ──────────────────
    args = ["--all", "--lite", "--topic", "<topic>",
            "--max-papers", "10",
            "--persona-tag", primary_tag,
            "--output-dir", str(output_dir)]
    exit_code, log = await _run_skill(ctx, skill_dir, args)
    if exit_code != 0:
        raise RuntimeError(f"paper-survey failed: exit_code={exit_code}")

    # ── 5. Load skill outputs from disk ─────────────────────────────
    paper_list = _load_json(output_dir / "paper_list.json") or []
    survey_text = (output_dir / "survey_report.md").read_text(errors="replace")

    # ── 6. Drive each chosen expert sequentially ────────────────────
    expert_reviews = []
    for tag in selected_tags:
        prompt = (f"用户要求：\n{ctx.question}\n\n"
                  f"报告摘录：\n{survey_text[:6000]}")
        rep = await ctx.send_persona(tag, prompt)
        expert_reviews.append({"tag": tag, "content": rep.content, "ok": rep.ok})
        await ctx.publish(rep.content, author=tag)

    # ── 7. Final report by the reporter persona ─────────────────────
    reporter_reply = await ctx.send_persona(
        "paper_reporter",
        f"领域专家补充：\n{json.dumps(expert_reviews, ensure_ascii=False)}\n\n"
        f"请给出最终汇报。"
    )
    await ctx.publish(reporter_reply.content, author="论文汇报者")

    # ── 8. Persist a structured result + a one-line conclusion ─────
    ctx.set_result({
        "ok": True,
        "output_dir": str(output_dir),
        "primary_persona": primary_tag,
        "selected_personas": selected_tags,
        "filtered_count": len(paper_list),
    })
    ctx.set_conclusion(f"paper-survey workflow finished; report: {output_dir}/survey_report.md")


async def _run_skill(ctx, skill_dir, args):
    """Subprocess the skill and stream interesting log lines via ctx.publish."""
    proc = await asyncio.create_subprocess_exec(
        "./run.sh", *args, cwd=str(skill_dir),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    log = []
    while True:
        line = await proc.stdout.readline()
        if not line: break
        text = line.decode("utf-8", "replace").rstrip()
        log.append(text)
        if _interesting(text):
            await ctx.publish(text, author="paper-survey")
    return await proc.wait(), "\n".join(log)
```

(See [`oasis/python/paper_survey_workflow.py`](../data/team_presets/paper-review-council/oasis/python/paper_survey_workflow.py) for the full version with regex parsers, dedup logic, and error paths.)

### 7.4 How a Python workflow is launched

```bash
clawcross workflow run paper_survey_workflow team paper-review-council question "审 attention is all you need"
# CLI dispatch: the runtime sees a .py file under teams/<team>/oasis/python/,
# spawns it with `python -m oasis.python_workflow_runner --workflow … --question …`
```

Or from the chatbot: `/cross workflow run paper_survey_workflow team paper-review-council question 审 attention is all you need`.

The runner sets up `ctx`, awaits `main(ctx)`, captures the result, and posts the conclusion back to the topic.

### 7.5 Python-workflow rules of thumb

- One file = one workflow. Multiple workflows = multiple `.py` files.
- Anything you `import` must be available in the venv that runs it. Standard library is fine; third-party deps that don't come with the ClawCross venv need their own install path (typically inside a team SKILL's `run.sh`).
- Always derive paths from `Path(__file__).resolve().parents[2]`, not hardcoded strings. The team folder may be renamed.
- Prefer `await ctx.publish(...)` over `print()` — `print` lands in the runner's stdout, not in the user's chat.
- A long subprocess should stream into `ctx.publish` so the user can watch progress, not return one giant blob.
- Errors raised from `main` end the workflow with a failure. To finish cleanly with a partial result, catch the error and call `ctx.set_conclusion("..."); ctx.set_result({...})` yourself.

---

## 8. SKILLs — where they live and how they're scoped

A SKILL is a small instruction pack (`SKILL.md` + optional scripts) that an agent reads and follows. ClawCross has **two scopes**:

### 8.1 User-global skills

```
<CLAWCROSS_HOME>/data/user_files/<user_id>/skills/
├── SKILLS_INDEX.md
├── docx/SKILL.md
├── xlsx/SKILL.md
├── pdf/SKILL.md
├── productivity/pptx/SKILL.md
└── …
```

Every team that user owns can use these. This is where general-purpose skills go — file format helpers (docx / xlsx / pdf / pptx), self-improvement loggers, doc co-authoring, etc.

For the `default` user on this machine: `~/.clawcross/data/user_files/default/skills/`.

### 8.2 Team-scoped skills

```
<CLAWCROSS_HOME>/data/user_files/<user_id>/teams/<team_name>/skills/
├── SKILLS_INDEX.md                     # auto-regenerated; don't edit by hand
└── paper-survey/
    ├── SKILL.md
    ├── run.sh
    ├── run.py
    ├── pyproject.toml
    └── runtime_config.example.json
```

Only that one team sees these. Use this scope when the SKILL is meaningless outside the team — e.g. `paper-review-council` ships `paper-survey` here because that script is what its Python workflow actually executes.

### 8.3 Lookup order

When the runtime (`src/webot/skills.py::_scope_skills_dir`) builds the SKILL list for an agent in team T, it merges:

```
team scope:     <user_files>/<user>/teams/<T>/skills/
personal scope: <user_files>/<user>/skills/
```

Team-scoped wins on name collision. `SKILLS_INDEX.md` in each scope is regenerated when a SKILL is added/removed.

### 8.4 Adding a SKILL

Either drop the directory onto disk and let the index regen pick it up, or use the API:

```
POST /teams/<team>/skills/import-zip      # upload a zip → team scope
GET  /teams/<team>/skills                  # list team-scope skills
GET  /teams/<team>/skills/<name>           # read a SKILL.md
```

A SKILL is plain Markdown with frontmatter; nothing in the runtime *runs* it. The agent decides to read `SKILL.md`, then follows it (running scripts via `run_command`, writing source via `write_file`, etc.).

---

## 9. Preset stamps

Teams that were installed from a built-in preset (`data/team_presets/<id>/`) carry two extra files copied verbatim:

```json
// clawcross_preset_manifest.json
{
  "preset_id": "paper-review-council",
  "name": "论文审读汇报团",
  "role_count": 2,
  "persona_count": 9,
  "workflow_files": ["paper_review_council.yaml"],
  "python_workflow_files": ["paper_survey_workflow.py"],
  "default_team_name": "论文审读汇报团",
  "tags": ["paper-review", "research"]
}
```

```json
// clawcross_preset_source_map.json
{
  "preset_id": "paper-review-council",
  "roles": {
    "search_commander": { "name": "搜索指挥者", "source_file": "...", "kind": "internal_agent" },
    "ml_reviewer":      { "name": "机器学习论文审读专家", "source_file": "...", "kind": "persona" }
  }
}
```

These are informational. The runtime does not require them; only `clawcross team … overview` and the Creator UI use them to show "installed from preset X."

---

## 10. Cron / scheduled triggers

Alarms (cron-scheduled or one-shot) live **outside** the team folder, in a global tasks file:

```
<CLAWCROSS_DATA_DIR>/timeset/tasks.json
```

Each task targets one agent by its `ag_…` id, and remembers the team it was created in. When a team is exported as a snapshot its tasks are exported with the member's role name; on import they are restored against the new members.

### 10.1 Create a cron alarm

**CLI** (in a terminal, pickers fill in any missing field):

```bash
clawcross cron add team "<team>" target "<role>" cron "0 9 * * 1" text "周一 9 点提醒整理论文进展"
clawcross cron add team "<team>" target "<role>" once "2026-06-01T09:00:00" text "..."
```

`target` is a member's role name (or any agent's id / address outside a team).

**HTTP** (session-authenticated):

```
GET  /teams/<team>/alarms            → {"targets": [{"agent": "ag_…", "target_name": "<role>", "label": …}], "alarms": [...]}
POST /teams/<team>/alarms
{
  "agent":         "ag_…",                           # a member's id, from "targets"
  "schedule_type": "cron",                           # "cron" | "once"
  "cron":          "0 9 * * 1",                      # required when schedule_type=cron
  "run_at":        "2026-06-01T09:00:00",            # required when schedule_type=once
  "text":          "周一 9 点提醒整理论文进展"
}
```

### 10.2 List & delete

```bash
clawcross cron                            # all of your tasks
clawcross cron "<team>"                   # one team
clawcross cron delete <task_id> team "<team>"
```

```
GET    /teams/<team>/alarms
DELETE /teams/<team>/alarms/<task_id>
```

The mobile UI uses the same handler under `/mobile_alarms` (all agents, or one team's members).

### 10.3 What an alarm does at fire time

When the scheduler ticks, it delivers `text` to the target agent in the team's context, as a message from you. The agent answers in its own conversation — a WeBot agent in its session, a Codex / Claude Code / OpenClaw agent in its runtime session — so its memory carries over from one run to the next.

---

## 11. DIY: build or edit a team with `write_file` / `run_command`

Personas, workflows and skills are plain files; members are registered through one import. If you have shell access (or the `write_file` / `read_file` / `run_command` MCP tools), you can build a team without the UI.

### 11.1 Build from scratch

```bash
clawcross team new quick-demo
TEAM=~/.clawcross/data/user_files/default/teams/quick-demo
mkdir -p "$TEAM/oasis/yaml"
```

```bash
# oasis_experts.json — the persona prompt
cat > "$TEAM/oasis_experts.json" <<'JSON'
[
  {
    "name": "Coordinator",
    "tag":  "coordinator",
    "persona": "你是协调者。把用户任务拆成 3-5 步并指派 SKILL。\n输出格式：[计划] ...",
    "temperature": 0.35
  }
]
JSON

# internal_agents.json — one member, wearing that persona, leading the team
cat > "$TEAM/internal_agents.json" <<'JSON'
[
  { "name": "Coordinator", "tag": "coordinator", "is_primary": true }
]
JSON

# oasis/yaml/quick.yaml — a 3-node workflow
cat > "$TEAM/oasis/yaml/quick.yaml" <<'YAML'
version: 2
repeat: false
plan:
- id: q0
  manual: { author: begin, content: "请描述任务。" }
- id: q1
  agent: Coordinator
- id: q2
  manual: { author: bend,  content: "已规划。" }
edges:
- [q0, q1]
- [q1, q2]
YAML

clawcross team import quick-demo          # registers the member; internal_agents.json is consumed
clawcross team quick-demo members
```

From inside an MCP agent the same writes go through the file tool, then the import through `run_command`:

```
write_file(filename="teams/quick-demo/oasis_experts.json",   content="[ … ]")
write_file(filename="teams/quick-demo/internal_agents.json", content="[ … ]")
write_file(filename="teams/quick-demo/oasis/yaml/quick.yaml", content="version: 2\n…")
run_command(command='clawcross team import "quick-demo"')
```

`filename` is the **required** parameter — not `path` / `name`. Paths are relative to the user's sandbox root, which contains the `teams/` subtree.

### 11.2 Edit an existing team

Files are re-read on every workflow run, so edits take effect immediately. Members change through commands:

| Change | How |
|---|---|
| Add a persona / change a prompt / tune temperature | edit `oasis_experts.json` |
| Add a member | `clawcross team member add <team> agent <ref> [role <r>] [lead]`, or a new agent: `… name <n> platform <p> [global <g>] [persona <tag>]` — or write `internal_agents.json` / `external_agents.json` and `clawcross team import <team>` |
| Rename a member / make it lead | the team panel, or `PATCH /v1/teams/<team>/members/<agent>` with `{role?, is_lead?}`; `scripts/cli.py teams set-lead` |
| Change a member's persona or endpoint | `clawcross team member edit <team> <agent> [persona <tag>] [api_url <u>] [model <m>]` |
| Remove a member | `clawcross team member remove <team> <agent>` (the agent stays) |
| Reroute the workflow / add a parallel branch | edit `oasis/yaml/<wf>.yaml` |
| Rewrite the dynamic orchestration | edit `oasis/python/<wf>.py` |
| Change the team-wide fallback agent | edit `team_settings.json` |

### 11.3 Things to keep consistent

1. Every member's persona `tag` exists in `oasis_experts.json` (or in the public persona pool).
2. Every `agent: <role>` in a workflow is a member's role name; every `persona: <tag>` exists in the persona library.
3. Role names are unique within the team; at most one lead.
4. YAML graph: every `edges` endpoint is a declared `id`; selectors use `selector_edges:`, not `edges:`.
5. Python workflow: derive paths from `Path(__file__).resolve().parents[2]`, not hardcoded strings.

### 11.4 When CLI / Creator is still better

- **OpenClaw members** need the OpenClaw side to exist: `openclaw add` (or pick from `openclaw sessions`), then register the member; `openclaw-snapshot export` stores its workspace config with the team.
- A **ZIP snapshot** should come from `teams snapshot-download` / the Creator, which write the import format with runtime fields and secrets left out.

---

## 12. How the pieces bind (one picture)

```
oasis_experts.json            members (ClawCross)                 oasis/yaml/<wf>.yaml
  tag: "search_commander" ◄──  role "搜索指挥者" wears tag  ◄──────  agent: 搜索指挥者
  persona: "你是…"             agent ag_… (WeBot / Codex / …)
        ▲
        └──────────────────────────────────────────────────────────  persona: ml_reviewer

oasis/python/<wf>.py  ── ctx.send_agent("<role>", prompt)     → a member
                      ── ctx.send_persona("<tag>", prompt)    → a temporary expert
                      ── ctx.list_agents() / ctx.list_personas()
```

**When something looks wrong:**

| Symptom | Likely cause |
|---|---|
| Workflow runs but the agent has the wrong personality | the member's persona tag is missing from `oasis_experts.json` → fallback to a public-pool persona. |
| "agent '<x>' not found; skipping" in the topic log | `agent: <x>` is not a member's role name (check `clawcross team "<team>" members`). |
| `internal_agents.json` still in the team folder | it was never imported — run `clawcross team import "<team>"`. |
| An OpenClaw member does not answer | the agent does not exist on the OpenClaw side (`openclaw sessions`), or OpenClaw is not running. |
| Python workflow crashes before `main` | venv missing a package, or `from oasis.workflow import …` couldn't reach the project root. |

---

## 13. Inspecting a live team

```bash
clawcross team "<team>"               # overview (members + alarm count)
clawcross team "<team>" members       # the team's agents (any platform)
clawcross team "<team>" personas      # oasis_experts.json
clawcross team "<team>" workflows     # yaml + python workflows
clawcross team "<team>" skills        # team-scoped SKILLs
clawcross team "<team>" crons         # cron alarms

clawcross workflow show <wf> team "<team>"
clawcross workflow run  <wf> team "<team>" question "<text…>"
```

Raw inspection:

```bash
cd ~/.clawcross/data/user_files/<user>/teams/<team>/
cat oasis_experts.json
ls oasis/yaml/  oasis/python/  skills/
```

---

## 14. Related docs

- [`build_team.md`](./build_team.md) — CLI to create / edit teams and add members.
- [`team-creator.md`](./team-creator.md) — browser-based Creator (discovery, smart-select, ZIP export).
- [`create_workflow.md`](./create_workflow.md) — full YAML grammar: selectors, conditional edges, dispatchers.
- [`workflowpy.md`](./workflowpy.md) — Python-script workflow mode in more depth (Agent Center, forum helpers).
- [`oasis-reference.md`](./oasis-reference.md) — OASIS runtime model (Town Mode, GraphRAG, ReportAgent).
- [`example_team.md`](./example_team.md) — alternative case study: `demo_team` with a selector-based workflow.
- [`openclaw-commands.md`](./openclaw-commands.md) — OpenClaw agent configuration and snapshots.
- [`repo-index.md`](./repo-index.md) — where the runtime code that reads each file lives.
