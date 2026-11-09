# Build Team via CLI (Quick Reference)

> This document provides a quick reference for building a team using Clawcross CLI, focusing on core commands without trial-and-error details.

If you want the browser-based flow that starts from a task description, discovered SOP pages, or a workflow canvas, read [team-creator.md](./team-creator.md) first.

---

## 1. Prerequisites

- Clawcross services must be running (Agent, Scheduler, OASIS, Frontend)
- Check service status:
  ```bash
  bash selfskill/scripts/run.sh status
  ```
- Default ports: Agent(51200), Scheduler(51201), OASIS(51202), Frontend(51209)

---

## 2. Team Management

### 2.1 Create a Team

```bash
uv run scripts/cli.py teams create --team-name <TEAM_NAME>
```

Example:
```bash
uv run scripts/cli.py teams create --team-name demo_team
```

### 2.2 List All Teams

```bash
uv run scripts/cli.py teams list
```

### 2.3 View Team Details

```bash
uv run scripts/cli.py teams show --team-name <TEAM_NAME>
```

### 2.4 View Team Members

```bash
uv run scripts/cli.py teams members --team-name <TEAM_NAME>
```

Output example:
```
Team Name: demo_team
Members (1):
- name: my_new_agent, global_name: my_new_agent, type: ext
```

---

## 3. Agent Management

Clawcross supports **3 types** of agents: Internal, OpenClaw, and External.

External agents communicate via HTTP or the **Agent Client Protocol (ACP)** through the `acpx` CLI adapter. ACP supports tools like `openclaw`, `codex`, `claude`, `gemini`, and `aider`. `acpx` is automatically installed during `bash selfskill/scripts/run.sh setup`.

### 3.1 Internal Agent (Lightweight)

> **关键概念区分**：
> - `#oasis#` 格式 = Internal Agent：在 OASIS 工作流 YAML 中，使用 `#oasis#` 格式引用的是 **Internal Agent**（团队内部已定义的 session agent）。
>   - 例如：`architect#oasis#my_architect` 表示使用 tag 为 `architect` 的人设，通过名为 `my_architect` 的 internal session agent 执行。
>   - Internal Agent 需要预先在 `internal_agents.json` 中定义。
> - `#temp#` 格式 = 临时人设：不需要预先定义 Internal Agent，只需要有对应的人设 prompt 即可。
>   - 例如：`creative#temp#1` 表示使用 tag 为 `creative` 的人设，创建一个临时的、无状态的实例。
>   - **Temp Agent 不需要预先定义**，只要 `oasis_experts.json` 或公共人设中存在该 tag 的 prompt 即可使用。
>   - 适合用于辩论、头脑风暴等不需要跨轮次记忆的场景。

**Add an Internal Agent:**
```bash
uv run scripts/cli.py internal-agents add \
  --team <TEAM_NAME> \
  --data '{"session":"<SESSION_ID>","meta":{"name":"<AGENT_NAME>","tag":"<TAG>"}}'
```

Example - Add a creative persona:
```bash
uv run scripts/cli.py internal-agents add \
  --team demo_team \
  --data '{"session":"creative_s1","meta":{"name":"创意人设","tag":"creative"}}'
```

**List Internal Agents in a Team:**
```bash
uv run scripts/cli.py internal-agents list --team <TEAM_NAME>
```

**Update an Internal Agent:**
```bash
uv run scripts/cli.py internal-agents update \
  --sid <SESSION_ID> --team <TEAM_NAME> \
  --data '{"meta":{"name":"<NEW_NAME>"}}'
```

**Delete an Internal Agent:**
```bash
uv run scripts/cli.py internal-agents delete \
  --sid <SESSION_ID> --team <TEAM_NAME>
```

### 3.2 OpenClaw Agent

OpenClaw Agent 是运行在 OASIS 后端的真实 Agent 实例。添加到 Team 需要**先确保后端有真实配置**，再同步到 JSON。

> 在将 OpenClaw agent 加入 Team 之前，**必须**先执行以下命令检查已有的 OpenClaw agent：
> ```bash
> uv run scripts/cli.py -u <username> openclaw sessions
> ```
> 确认目标 agent 的名称存在于返回的列表中。如果不存在，需要先用 `openclaw sessions add` 创建，或选择列表中已有的 agent。**禁止**在未确认 agent 存在的情况下直接将 `global_name` 写入 `external_agents.json`。

#### 完整流程

```
┌─────────────────────────────────────────────────────────────────────────┐
│  CLI 添加/设置 OpenClaw Agent 的完整流程                                  │
└─────────────────────────────────────────────────────────────────────────┘

  ┌──────────────────┐
  │ 1. 查找或创建后端 │  ← 先确定后端有真实配置
  └────────┬─────────┘
           │
           ├─ A. 查找已有: `openclaw sessions`
           │           GET /sessions/openclaw
           │           查看已运行的 OpenClaw Agent
           │
           └─ B. 新建: `openclaw sessions add`
              POST /sessions/openclaw/add

  ┌──────────────────┐
  │ 2. (可选)修改配置 │
  └────────┬─────────┘
           │
           └─ `openclaw update-config`
              POST /sessions/openclaw/update-config

  ┌──────────────────┐
  │ 3. 添加到 JSON   │  ← 骨架信息 (name, tag, global_name)
  └────────┬─────────┘
           │
           └─ `teams add-ext-member`
              POST /teams/{team}/members/external

  ┌──────────────────┐
  │ 4. 导出完整配置  │  ← 关键！将后端配置同步到 JSON
  └────────┬─────────┘
           │
           └─ `openclaw snapshot export`
              POST /team_openclaw_snapshot/export
```

#### 步骤详解

**Step 1: 查找已有的 OpenClaw Agent**
```bash
uv run scripts/cli.py -u <username> openclaw sessions
```

**Step 2: 或创建新的 OpenClaw Agent**
```bash
uv run scripts/cli.py -u <username> openclaw sessions add \
  --agent-name <AGENT_NAME> \
  --backend openai \
  --model gpt-4o \
  --api-key <API_KEY> \
  --base-url <BASE_URL>
```

**Step 3: (可选) 修改 Agent 配置**
```bash
uv run scripts/cli.py -u <username> openclaw update-config \
  --agent-name <AGENT_NAME> \
  --config '{"temperature": 0.7}'
```

**Step 4: 添加到 Team JSON（骨架信息）**
```bash
uv run scripts/cli.py -u <username> teams add-ext-member \
  --team-name <TEAM_NAME> \
  --data '{
    "name": "<AGENT_NAME>",
    "tag": "openclaw",
    "global_name": "<GLOBAL_NAME>"
  }'
```

**Step 5: 导出后端完整配置到 JSON**
```bash
uv run scripts/cli.py -u <username> openclaw snapshot export \
  --team-name <TEAM_NAME> \
  --name <AGENT_NAME>
```

> CLI 需要用户显式执行 `export` 才能将后端配置同步到 JSON；而前端在关闭 Agent 配置面板时会自动执行 sync_all。

### 3.3 External Agent (API-based)

如需对 OpenClaw 外部 Agent 进行深度控制（如修改温度参数、限制工具权限等），请先阅读 **[openclaw-commands.md](openclaw-commands.md)** 文档中的"六、外部 Agent 配置"章节。

**Add an External Member:**
```bash
uv run scripts/cli.py teams add-ext-member \
  --team-name <TEAM_NAME> \
  --data '{
    "name": "<MEMBER_NAME>",
    "global_name": "<GLOBAL_NAME>",
    "type": "external",
    "meta": {
      "description": "<DESCRIPTION>",
      "emoji": "<EMOJI>",
      "disabled": false
    }
  }'
```

**Note:**
- `name` and `global_name` are required fields. Keep them consistent to point to the same entity.
- **Important**: When adding an OpenClaw agent, the `tag` field should be set to `"openclaw"` (recommended). This allows the system to correctly identify and route requests to the appropriate ACP (Agent Communication Protocol) handler. Other possible values include `codex`, but `openclaw` is the recommended tag for OpenClaw agents.

Example (adding OpenClaw agent `my_new_agent` to a team):
```bash
uv run scripts/cli.py teams add-ext-member \
  --team-name demo_team \
  --data '{
    "name": "my_new_agent",
    "global_name": "my_new_agent",
    "type": "external",
    "tag": "openclaw",
    "meta": {
      "description": "My newly created OpenClaw agent for testing",
      "emoji": "🤖",
      "disabled": false
    }
  }'
```

---

## 4. Create Custom Persona

When public personas don't meet your needs, you can create custom personas (人设) for your team.

> **Note:** In YAML and CLI, the field/command name is `expert`, but it represents a **persona (人设)** — an **expert persona prompt** that defines an Agent's role, personality, and capabilities. It is NOT a separate agent. The team-level file `oasis_experts.json` is essentially a **persona prompt collection** — each entry is a prompt, not an agent.

### 4.1 Create a Custom Persona

**Command:**
```bash
uv run scripts/cli.py personas add \
  --tag <PERSONA_TAG> \
  --persona-name "<PERSONA_DISPLAY_NAME>" \
  --persona "<PERSONA_DESCRIPTION>" \
  --temperature <TEMPERATURE_VALUE> \
  [--team <TEAM_NAME>]
```

**Parameters:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--tag` | Yes | Unique identifier for the persona (e.g., `coder`, `architect`) |
| `--persona-name` | Yes | Display name with emoji (e.g., `"💻 代码达人"`) |
| `--persona` | Yes | System prompt defining the persona's role, skills, and behavior |
| `--temperature` | Yes | Creativity level (0.0-1.0), lower = more deterministic |
| `--team` | No | Associate persona with a specific team (optional) |

**Example - Create a coder persona:**
```bash
uv run scripts/cli.py personas add \
  --tag coder \
  --persona-name "💻 代码达人"  --persona "You are an expert programmer proficient in multiple programming languages. You write clean, efficient, and well-documented code. You follow best practices, design patterns, and coding standards. You can debug complex issues, optimize performance, and provide code reviews." \
  --temperature 0.3
```

**Example - Create an architect persona for a specific team:**
```bash
uv run scripts/cli.py personas add \
  --tag architect \
  --persona-name "🏗️ 架构师"  --persona "You are an experienced software architect with deep expertise in system design, microservices, cloud-native architecture, and scalability patterns. You provide high-level technical guidance and help teams make informed decisions about technology stacks." \
  --temperature 0.4 \
  --team demo_team
```

### 4.2 Add Custom Persona to Team

After creating a custom persona, add it as an Internal Agent:

```bash
uv run scripts/cli.py internal-agents add \
  --team <TEAM_NAME> \
  --data '{"session":"<SESSION_ID>","meta":{"name":"<AGENT_NAME>","tag":"<PERSONA_TAG>"}}'
```

**Example:**
```bash
uv run scripts/cli.py internal-agents add \
  --team demo_team \
  --data '{"session":"architect_s1","meta":{"name":"🏗️ 架构师","tag":"architect"}}'
```

---

## 6. View Public Persona List

```bash
uv run scripts/cli.py personas list
```

### Core Public Personas (10)

| Name | Tag |
|------|-----|
| 🎨 创意人设 | `creative` |
| 🔍 批判人设 | `critical` |
| 📊 数据分析师 | `data` |
| 🎯 综合顾问 | `synthesis` |
| 📈 经济学家 | `economist` |
| ⚖️ 法学家 | `lawyer` |
| 💰 成本限制者 | `cost_controller` |
| 📊 收益规划者 | `revenue_planner` |
| 🚀 创新企业家 | `entrepreneur` |
| 🧑 普通人 | `common_person` |

### Agency Professional Personas (68)

| Category | Count | Examples |
|----------|-------|----------|
| 🎨 Design | 8 | Brand Guardian, UI Designer, UX Architect, Image Prompt Engineer... |
| ⚙️ Engineering | 11 | Senior Developer, Backend Architect, Frontend Developer, DevOps Automator, AI Engineer... |
| 📢 Marketing | 11 | Content Creator, Growth Hacker, TikTok Strategist, WeChat Official Account Manager... |
| 📦 Product | 4 | Sprint Prioritizer, Trend Researcher, Feedback Synthesizer, Behavioral Nudge Engine |
| 📋 Project Management | 5 | Senior Project Manager, Studio Producer, Experiment Tracker... |
| 🥽 Spatial Computing | 6 | visionOS Spatial Engineer, XR Immersive Developer, Metal Engineer... |
| 🔬 Specialist | 9 | Agents Orchestrator, Developer Advocate, Data Analytics Reporter... |
| 🛡️ Support | 6 | Finance Tracker, Legal Compliance Checker, Infrastructure Maintainer... |
| 🧪 Testing | 8 | API Tester, Performance Benchmarker, Accessibility Auditor... |

### Custom Personas (8)

| Name | Tag |
|------|-----|
| 场景段描述者 | `VLM` |
| 日志阅读者LLM | `LLM-log` |
| 时序判别器 | `time-series` |
| 用户画像阅读者 | `statistic` |
| 信息总结器 | `summaryfor4` |
| 子段阅读者 | `subsegment` |
| 搜索者 | `searcher` |
| 任务发布者 | `tasksender` |

---

## 7. Complete Workflow Example

Below is a complete example of building a team from scratch:

```bash
# Step 1: Create a new team
uv run scripts/cli.py teams create --team-name demo_team

# Step 2: View available public personas
uv run scripts/cli.py personas list

# Step 3: Add Internal Agent (creative persona)
uv run scripts/cli.py internal-agents add \
  --team demo_team \
  --data '{"session":"creative_s1","meta":{"name":"创意人设","tag":"creative"}}'

# Step 4: Add Internal Agent (critical persona)
uv run scripts/cli.py internal-agents add \
  --team demo_team \
  --data '{"session":"critical_s1","meta":{"name":"批判人设","tag":"critical"}}'

# Step 5: Add External Member (OpenClaw agent)
uv run scripts/cli.py teams add-ext-member \
  --team-name demo_team \
  --data '{
    "name": "my_new_agent",
    "global_name": "my_new_agent",
    "type": "external",
    "meta": {
      "description": "My newly created OpenClaw agent for testing",
      "emoji": "🤖",
      "disabled": false
    }
  }'

# Step 6: Verify team members
uv run scripts/cli.py teams members --team-name demo_team
```

---

## 8. Tips & Notes

- **Team name duplication**: If a team name already exists, the create command will report an error. Use `teams list` to check first.
- **Tag matching**: When adding public personas as Internal Agents, the `tag` field must match the persona's tag exactly (e.g., `creative`, `critical`).
- **Session ID**: Each Internal Agent in a team needs a unique `session` ID. Use descriptive names like `creative_s1` for easy identification.
- **Multiple agent types**: A single team can contain a mix of Internal, OpenClaw, and External agents.
- **Required fields for External Members**: `name` and `global_name` are required. Keep them consistent to point to the same entity.

---

## 8. Summary

The core workflow for building a Clawcross team using CLI is:
**Create Team → Add Members (Internal/OpenClaw/External) → Add Personas → Verify**.

All operations are based on `scripts/cli.py`, ensure execution under `uv run` environment.

---

## 9. Agent设置和Expert设置经验总结

### 9.1 Agent设置最佳实践

**OpenClaw Agent配置要点：**
- **全局唯一标识**：`global_name`必须全局唯一，避免命名冲突
- **Session管理**：相同session共享上下文，不同session保持独立
- **配置同步**：添加agent后必须执行`openclaw snapshot export`同步后端配置到JSON

**Internal Agent配置要点：**
- **Session ID唯一性**：每个Internal Agent需要唯一的session ID
- **Tag匹配**：tag必须与persona的tag完全匹配（如`creative`、`critical`）
- **人设复用**：同一persona可被多个agent使用，但session需不同

### 9.2 Expert设置经验

**Persona（人设）配置规则：**
- **Expert字段格式**：必须使用`tag#ext#id`格式（如：`openclaw#ext#my_new_agent`）
- **Model字段格式**：支持session扩展，如`agent:main:default`或`agent:my_new_agent`
- **温度设置**：根据persona类型设置合适的temperature（创意类0.7-0.9，分析类0.3-0.5）

**工作流行为控制：**
- **禁止自动重复开启**：即使工作流看似无结果，也不得自动重复开启
- **子agent行为限制**：子agent除非收到点名要求，否则不得自行开启子工作流
- **状态检查**：使用`topics show`进行非阻塞状态检查，避免阻塞等待

### 9.3 常见问题解决

**工作流重复启动问题：**
- 使用`topics list`查看所有活跃工作流
- 使用`topics show --topic-id <ID>`检查具体状态
- 确认失败后再决定是否重试，避免重复启动

**多级讨论控制：**
- 在OASIS提示词中明确禁止子agent自动开启子工作流
- 保持单级讨论原则，防止嵌套失控
- 明确点名机制，只有被点名时才开启子讨论
