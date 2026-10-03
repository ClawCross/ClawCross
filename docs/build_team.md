# Build Team via CLI (Quick Reference)

> 用 `src/cli/cli.py` 从零搭一个团队的最短路径。浏览器里从任务描述 / SOP / 工作流画布生成团队，请看 [team-creator.md](./team-creator.md)。

---

## 1. Prerequisites

- Clawcross services must be running (Agent, Scheduler, OASIS, Frontend)
- Check service status:
  ```bash
  bash launch/run.sh status
  ```
- Default ports: Agent(51200), Scheduler(51201), OASIS(51202), Frontend(51209)

---

## 2. 三个概念

| 概念 | 是什么 | 在哪 |
|---|---|---|
| **agent** | 本机上的一个会话，编号就是会话号（可自定，如 `coder`；不给则系统分配 `ag_…`），加上名字、平台（`webot`、`codex`、`claude-code`、`gemini-cli`、`openclaw`、任意 HTTP 服务）和设置（人设 tag 等）。所有平台是**同一种东西**，用同一套命令管理；给一个没用过的编号发信息，就新建一个 agent | `agents` 命令 / `/v1/agents` |
| **team** | 一个命名空间：把 agent、人设、技能、定时任务、工作流放进一个文件夹。每个成员有一个 team 内名字，在任何能写编号的地方都可以用 `<team>.<名字>` 找到它；可以标一个 lead。team 不拥有 agent：同一个 agent 可以在多个 team 里 | `teams` 命令 / `/v1/teams` |
| **persona（人设）** | 一段角色 prompt，按 `tag` 存在人设库（公共 / agency / 你自己的 / team 的 `oasis_experts.json`）。agent 用 `persona` 设置穿上它；工作流里的临时专家也按 tag 取它 | `personas` 命令 |

命令里的 `--agent` 写 agent 编号，或 `<team>.<名字>`。

---

## 3. Team Management

```bash
uv run src/cli/cli.py teams create --team-name demo_team     # 新建
uv run src/cli/cli.py teams list                              # 列出
uv run src/cli/cli.py teams info --team-name demo_team        # 详情
uv run src/cli/cli.py teams members --team-name demo_team     # 成员（任何平台）
uv run src/cli/cli.py teams rename --team-name demo_team --new-name demo2
uv run src/cli/cli.py teams delete --team-name demo_team      # 只删 team 文件夹，agent 不受影响
```

---

## 4. Agents

### 4.1 新建 agent（任何平台同一条命令）

```bash
# WeBot（ClawCross 自己的 agent）；agent_id 就是会话号，不写则系统分配
uv run src/cli/cli.py agents create --name "Coder" --data '{"agent_id": "coder", "persona": "creative"}'

# ACP 工具（codex / claude-code / gemini-cli / aider …）：每个 agent 是该工具里的一个会话，同一工具可以有任意多个
uv run src/cli/cli.py agents create --name "Codex Reviewer" --platform codex \
  --data '{"agent_id": "codex-reviewer", "persona": "critical"}'

# 任意 OpenAI 兼容 HTTP 服务
uv run src/cli/cli.py agents create --name "My Service" --platform my_service \
  --data '{"agent_id": "my-service", "api_url": "http://127.0.0.1:8080/v1", "model": "gpt-4o"}'
```

同一个编号只能建一次；重复会返回已有的那个（HTTP 409）。也可以不建，直接给新编号发信息（`agents ask --agent <新编号>`），会按 WeBot 新建。

### 4.2 查看、修改、控制、删除

```bash
uv run src/cli/cli.py agents list [--status]
uv run src/cli/cli.py agents show   --agent coder
uv run src/cli/cli.py agents update --agent coder --name "Coder" --data '{"settings": {"persona": "coder"}}'
uv run src/cli/cli.py agents ask    --agent coder --message "你好"
uv run src/cli/cli.py agents inbox  --agent coder --message "空了看一下"   # 放进收件箱
uv run src/cli/cli.py agents status --agent coder     # 同样适用于 cancel / reset
uv run src/cli/cli.py agents delete --agent coder     # 同时退出所有 team 和群聊
```

### 4.3 加入 / 移出 team

```bash
uv run src/cli/cli.py teams add-member    --team-name demo_team --agent coder --role "Coder" [--lead]
uv run src/cli/cli.py teams set-lead      --team-name demo_team --agent coder
uv run src/cli/cli.py teams remove-member --team-name demo_team --agent coder   # agent 本身保留
```

`--role` 是成员在这个 team 里的名字：`demo_team.Coder` 就能找到它，team 模式的工作流里写 `agent: Coder`。省略时用 agent 名称。

### 4.4 OpenClaw agent

OpenClaw 和 Codex 一样是 ACP 工具，经 acpx 的 `openclaw acp` 对话，连 OpenClaw 的 `main` agent；ClawCross 不改 OpenClaw 的配置：

```bash
uv run src/cli/cli.py agents create --name "Researcher" --platform openclaw \
  --data '{"agent_id": "researcher", "persona": "analyst"}'
uv run src/cli/cli.py teams add-member --team-name demo_team --agent researcher --role "Researcher"
```

前提和限制见 [openclaw-commands.md](openclaw-commands.md)。

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
- `session`（内部）/ `global_name`（外部）是本机的 agent 编号
- team 里已有同名成员就是那个成员；条目指向已有编号就用那个 agent；否则新建

导入：
```bash
uv run src/cli/cli.py teams import --team-name demo_team
```
导入后成员登记在 ClawCross 里，这两个文件被移走；导出团队（`teams snapshot-download`）时会按同样格式重新生成。

---

## 6. Personas

```bash
uv run src/cli/cli.py personas list
uv run src/cli/cli.py personas add \
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
uv run src/cli/cli.py teams create --team-name demo_team

uv run src/cli/cli.py agents create --name "Coordinator" --data '{"agent_id": "coordinator", "persona": "synthesis", "team": "demo_team"}'
uv run src/cli/cli.py agents create --name "Codex Reviewer" --platform codex \
  --data '{"agent_id": "codex-reviewer", "persona": "critical", "team": "demo_team"}'

uv run src/cli/cli.py teams add-member --team-name demo_team --agent coordinator --lead
uv run src/cli/cli.py teams add-member --team-name demo_team --agent codex-reviewer

uv run src/cli/cli.py teams members --team-name demo_team
uv run src/cli/cli.py agents ask --agent demo_team.Coordinator --message "你好"
uv run src/cli/cli.py cron new --team demo_team --agent coordinator --cron "0 9 * * 1" --text "整理本周进展"
```

---

## 9. Tips

- **一个运行时一个 agent**：WeBot 会话、`平台 + global_name` 都只能登记一次；想在多个 team 里用同一个 agent，直接 `add-member` 到各个 team。
- **角色名在 team 内唯一**：工作流、群聊 @、定时任务都靠它找成员。
- **删除**：`teams remove-member` 只是移出；`agents delete` 才删除 agent（并退出所有 team 和群聊）。
- **工作流行为**：不要自动重复开启工作流；子 agent 未被点名时不要自行开启子工作流；用 `topics show --topic-id <ID>` 做非阻塞状态检查。
