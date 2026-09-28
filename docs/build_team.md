# Build Team via CLI (Quick Reference)

> 用 `scripts/cli.py` 从零搭一个团队的最短路径。浏览器里从任务描述 / SOP / 工作流画布生成团队，请看 [team-creator.md](./team-creator.md)。

---

## 1. Prerequisites

- Clawcross services must be running (Agent, Scheduler, OASIS, Frontend)
- Check service status:
  ```bash
  bash selfskill/scripts/run.sh status
  ```
- Default ports: Agent(51200), Scheduler(51201), OASIS(51202), Frontend(51209)

---

## 2. 三个概念

| 概念 | 是什么 | 在哪 |
|---|---|---|
| **agent** | 本机上的一个 agent：`ag_…` 编号 + 名字 + 平台（`webot`、`codex`、`claude-code`、`gemini-cli`、`openclaw`、任意 HTTP 服务）+ 设置（人设 tag 等）。所有平台是**同一种东西**，用同一套命令管理 | `agents` 命令 / `/v1/agents` |
| **team** | 若干 agent 的组合。每个成员有一个**角色名**（role），可以有一个 **lead**（代表团队发言、在团队群里当主 agent）。team 不拥有 agent：同一个 agent 可以在多个 team 里 | `teams` 命令 / `/v1/teams` |
| **persona（人设）** | 一段角色 prompt，按 `tag` 存在人设库（公共 / agency / 你自己的 / team 的 `oasis_experts.json`）。agent 用 `persona` 设置穿上它；工作流里的临时专家也按 tag 取它 | `personas` 命令 |

agent 的地址是 `<用户>/<handle>`（如 `alice/coder`）；命令里的 `--agent` 可以写 `ag_` 编号、地址或 handle。

---

## 3. Team Management

```bash
uv run scripts/cli.py teams create --team-name demo_team     # 新建
uv run scripts/cli.py teams list                              # 列出
uv run scripts/cli.py teams info --team-name demo_team        # 详情
uv run scripts/cli.py teams members --team-name demo_team     # 成员（任何平台）
uv run scripts/cli.py teams rename --team-name demo_team --new-name demo2
uv run scripts/cli.py teams delete --team-name demo_team      # 不在其他 team 里的成员 agent 一并删除
```

---

## 4. Agents

### 4.1 新建 agent（任何平台同一条命令）

```bash
# WeBot（ClawCross 自己的 agent，会话自动创建）
uv run scripts/cli.py agents create --name "创意人设" --data '{"persona": "creative"}'

# ACP 工具（codex / claude-code / gemini-cli / aider …）：global_name 是它在该工具里的会话名
uv run scripts/cli.py agents create --name "Codex Reviewer" --platform codex \
  --data '{"global_name": "codex_reviewer", "persona": "critical"}'

# 任意 OpenAI 兼容 HTTP 服务
uv run scripts/cli.py agents create --name "My Service" --platform my_service \
  --data '{"global_name": "my_service", "api_url": "http://127.0.0.1:8080/v1", "model": "gpt-4o"}'
```

同一个运行时（同一个 WeBot 会话 / 同一个平台 + global_name）只能登记一个 agent；重复登记会返回已有的那个（HTTP 409）。

### 4.2 查看、修改、控制、删除

```bash
uv run scripts/cli.py agents list [--status]
uv run scripts/cli.py agents show   --agent alice/coder
uv run scripts/cli.py agents update --agent coder --name "Coder" --data '{"settings": {"persona": "coder"}}'
uv run scripts/cli.py agents ask    --agent coder --message "你好"
uv run scripts/cli.py agents status --agent coder     # 同样适用于 cancel / reset
uv run scripts/cli.py agents delete --agent coder     # 同时退出所有 team 和群聊
```

### 4.3 加入 / 移出 team

```bash
uv run scripts/cli.py teams add-member    --team-name demo_team --agent coder --role "Coder" [--lead]
uv run scripts/cli.py teams set-lead      --team-name demo_team --agent coder
uv run scripts/cli.py teams remove-member --team-name demo_team --agent coder   # agent 本身保留
```

`--role` 是成员在这个 team 里的名字：工作流里的 `agent: <角色名>`、团队群里的显示名、定时任务的目标都用它。省略时用 agent 名称。

### 4.4 OpenClaw agent

OpenClaw 的 agent 住在 OpenClaw 自己的工作区里，所以先在 OpenClaw 里建（或选一个已有的），再登记成 ClawCross agent：

```bash
# 1. 查已有 / 新建 OpenClaw agent
uv run scripts/cli.py openclaw sessions
uv run scripts/cli.py openclaw add --data '{"name": "demo_team_researcher", "workspace": "~/.openclaw/workspace-demo_team_researcher"}'

# 2. 登记为 ClawCross agent，并加入 team
uv run scripts/cli.py agents create --name "Researcher" --platform openclaw \
  --data '{"global_name": "demo_team_researcher", "persona": "analyst", "team": "demo_team"}'
uv run scripts/cli.py teams add-member --team-name demo_team --agent researcher --role "Researcher"

# 3.（可选）把 OpenClaw 工作区配置存进 team，便于导出 / 迁移
uv run scripts/cli.py openclaw-snapshot export --team demo_team --agent-name demo_team_researcher --short-name Researcher
```

深度配置（工具权限、channels 等）见 [openclaw-commands.md](openclaw-commands.md)。

---

## 5. 用文件声明成员（导入格式）

团队包（preset、快照 zip、team-builder 写出的文件夹）用两个 JSON 文件声明成员，格式固定：

`teams/<team>/internal_agents.json` —— WeBot 成员
```json
[
  { "name": "Coordinator", "tag": "coordinator", "is_primary": true },
  { "name": "Writer",      "tag": "writer" }
]
```

`teams/<team>/external_agents.json` —— 其他平台成员
```json
[
  { "name": "Codex Reviewer", "tag": "critical", "platform": "codex", "global_name": "codex_reviewer" }
]
```

- `name` = 角色名，`tag` = 人设，`is_primary` = lead（至多一条）
- 不写 `session` / `global_name` 时，导入会新建 agent；写了则引用本机已有的那个运行时

导入：
```bash
uv run scripts/cli.py teams import --team-name demo_team
```
导入后成员登记在 ClawCross 里，这两个文件被移走；导出团队（`teams snapshot-download`）时会按同样格式重新生成。

---

## 6. Personas

```bash
uv run scripts/cli.py personas list
uv run scripts/cli.py personas add \
  --tag architect --persona-name "🏗️ 架构师" \
  --persona "You are an experienced software architect ..." \
  --temperature 0.4 [--team demo_team]
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--tag` | Yes | 人设标识（如 `coder`、`architect`） |
| `--persona-name` | Yes | 显示名 |
| `--persona` | Yes | 角色 prompt |
| `--temperature` | Yes | 0.0-1.0 |
| `--team` | No | 只放进这个 team 的人设库（`oasis_experts.json`） |

让某个 agent 穿上人设：`agents update --agent <ref> --data '{"settings": {"persona": "<tag>"}}'`，或建 agent 时直接给 `persona`。

公共人设 10 个（`creative`、`critical`、`data`、`synthesis`、`economist`、`lawyer`、`cost_controller`、`revenue_planner`、`entrepreneur`、`common_person`），另有 68 个 agency 专业人设（design / engineering / marketing / product / project-management / spatial-computing / specialized / support / testing）。

---

## 7. 在工作流里用团队

工作流只有两种参与者写法（详见 [create_workflow.md](create_workflow.md)）：

```yaml
version: 2
plan:
  - id: plan
    agent: Coordinator          # 团队成员：写角色名，任何平台都一样
  - id: ideas
    parallel:
      - persona: creative       # 临时专家：按 tag 取人设，话题结束即清理
      - agent: Codex Reviewer
edges:
  - [plan, ideas]
```

---

## 8. Complete Example

```bash
uv run scripts/cli.py teams create --team-name demo_team

uv run scripts/cli.py agents create --name "Coordinator" --data '{"persona": "synthesis", "team": "demo_team"}'
uv run scripts/cli.py agents create --name "Codex Reviewer" --platform codex \
  --data '{"global_name": "codex_reviewer", "persona": "critical", "team": "demo_team"}'

uv run scripts/cli.py teams add-member --team-name demo_team --agent coordinator --lead
uv run scripts/cli.py teams add-member --team-name demo_team --agent codex-reviewer

uv run scripts/cli.py teams members --team-name demo_team
uv run scripts/cli.py cron new --team demo_team --agent coordinator --cron "0 9 * * 1" --text "整理本周进展"
```

---

## 9. Tips

- **一个运行时一个 agent**：WeBot 会话、`平台 + global_name` 都只能登记一次；想在多个 team 里用同一个 agent，直接 `add-member` 到各个 team。
- **角色名在 team 内唯一**：工作流、群聊 @、定时任务都靠它找成员。
- **删除**：`teams remove-member` 只是移出；`agents delete` 才删除 agent（并退出所有 team 和群聊）。
- **工作流行为**：不要自动重复开启工作流；子 agent 未被点名时不要自行开启子工作流；用 `topics show --topic-id <ID>` 做非阻塞状态检查。
