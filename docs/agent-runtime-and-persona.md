# Agent 三层结构：统一层 / 通讯层 / 组合层

本文是 ClawCross 里 agent 相关代码的开发约束与实现索引。它回答：

- 一个 agent 是什么？WeBot、Codex、Claude Code、OpenClaw、HTTP 服务之间有什么区别？
- 消息怎样在 agent 与人之间流动？
- team、群聊、OASIS、定时任务怎样使用 agent？
- 人设在哪里、怎样注入？
- 改代码时哪些边界不能越过？

## 总览

```
公网前端 / 第三方客户端 / 手机 / chatbot / CLI
   │  /v1/agents · /v1/teams · /v1/models · /v1/chat/completions · /groups
   ▼
┌ L3 组合层 ─ team（视图）· 群聊 · OASIS · 定时任务 ─────────────────────────┐
│   只用 agent 编号；调用时通过 context 传 team 上下文；不碰运行时                  │
├ L2 通讯层 ─ 会话 · 成员 · 消息 · 唤醒 · 未读摘要 · 发言者认证 ───────────────┤
│   人 ↔ agent、agent ↔ agent 的所有消息都走这里；只认编号                         │
├ L1 统一层 ─ agent 总表 + 单 agent 接口 ────────────────────────────────────┤
│   ask · deliver · discard · status · cancel · reset · history · cleanup       │
│   驱动：webot · acpx（codex / claude-code / gemini…）· openclaw · http · llm    │
└────────────────────────────────────────────────────────────────────────────┘
```

依赖只能向下：L3 → L2 → L1 → 驱动/传输。`test/test_layering.py` 检查：

- `src/agents` 不 import 群聊、team、OASIS、routes 等上层模块；
- `src/comms` 不 import team、groups、OASIS、api、routes；
- L1 以外的代码不读 agent 的 `driver` / `config`，也不引用驱动名（team 文件格式 `teams/manifest.py` 除外）；
- 只有 `src/agents` 直接调用传输层（`integrations.registry` / connectors）。唯一的例外名单是 `src/front.py` 的直连聊天代理（`/proxy_openclaw_chat`、`/proxy_acpx_chat`），等 L1 提供 `stream()` 后迁走；名单只能缩小。

## L1 统一层（`src/agents/`）

### 一个 agent = 一条记录

本机所有 agent 存在 `<DATA_DIR>/clawcross.db` 的 `agents` 表，一个 agent 一行：

| 字段 | 说明 |
|---|---|
| `agent_id` | `ag_` + 10 位 base32，永不改变 |
| `owner` / `handle` | handle 在 owner 内唯一；地址 `owner/handle`（如 `alice/coder`） |
| `name` | 显示名 |
| `driver` | `webot` / `acpx` / `openclaw` / `http` |
| `config` | 驱动私有：webot 有 `session`、`persona`、`team`、`tools`；其他平台有 `platform`、`global_name`、`api_url`、`api_key`、`model`、`headers`、`meta`、`persona`、`team` |
| `runtime_key` | `driver:session` 或 `driver:global_name`，owner 内唯一：一个运行时只登记一次 |
| `runtime` | 运行时已经知道什么：发过的身份 prompt、最后使用时间。所有平台同一列，只有 L1 读写 |

对外只有平台（`platform`：`webot`、`codex`、`claude-code`、`openclaw`、任意 HTTP 服务名），驱动由平台推出。引用一个 agent 可以写 `ag_` 编号、地址或 handle（`AgentStore.resolve`）。

`persona` 是人设 tag；`team` 是没有调用上下文时（例如直接私聊）使用的默认 team，决定 WeBot 加载哪个 team 的技能与人设库。

### 单 agent 接口

`AgentGateway`（`gateway.py`）：

- `ask(agent, msg, *, context, mode, tools, response_format, timeout)` —— 发消息并等回复；`response_format` 是 Pydantic 模型，驱动按运行时能力转换（内部 WeBot 在 ReAct 工具阶段结束后单独用 provider 的限制解码生成最终结构化回复；外部 agent 按自身协议处理）；
- `deliver(agent, msg, *, context, mode)` —— 投进 agent 的收件箱立即返回，agent 通过 `reply_channel` 回到会话（WeBot 用 `send_to_group` 工具，其他平台用 CLI `groups send --agent <地址>`）；
- `discard(agent)` —— 删除临时 WeBot 会话。

`context` 对 L1 不透明：外部 agent 用 `team` 解析人设，`conversation_id` 用于外部历史归档。WeBot 当前按 agent 记录里的默认 `team` 解析人设与技能，不根据调用上下文切换团队。

临时参与者也是 agent，只是不入表：`persona_agent()` 是一次带人设的模型调用（驱动 `llm`），`temp_session_agent()` 是带工具的临时 WeBot 会话。

`AgentControl`（`control.py`，运行在 Agent 服务里）：

| 动作 | WeBot | acpx / OpenClaw | HTTP |
|---|---|---|---|
| `status` | thread 忙碌、待处理系统消息、上下文占用 | acpx session 状态 | 最近使用记录 |
| `cancel` | 取消当前任务 | acpx cancel | 不支持（明确报错） |
| `reset` | 清空会话 | 关闭 session 并忘记已注入的人设 | 忘记已注入的人设 |
| `history` | 会话消息（含工具调用） | 外部历史库里该 session 的往来（只记外部运行时） | 同左 |
| `cleanup` | 删除 agent 时清空会话 | 关闭 session 并删除外部历史 | 删除外部历史 |

`status` 返回 `state`（`running` / `idle` / `unknown`）和 `actions`（该 agent 支持的动作），不把推断状态伪装成确定事实。

### HTTP API

```
GET    /v1/agents                  ?status=1 附带状态；?runtime=webot:<session> 反查
POST   /v1/agents                  {name, platform, persona, team, session | global_name, api_url, model…}
GET    /v1/agents/{ref}
PATCH  /v1/agents/{ref}            {name?, settings: {persona, team, tools | api_url, api_key, model, headers, meta}}
DELETE /v1/agents/{ref}            同时退出所有 team 与会话
POST   /v1/agents/{ref}/messages   ask（或 deliver: true）
POST   /v1/agents/{ref}/control    {action: status | cancel | reset}
GET    /v1/agents/{ref}/history
```

agent 卡片里不返回密钥（`api_key` 只给出 `has_api_key`）；WeBot agent 暴露 `settings.session` 供前端打开会话。`/v1/models` 与 `/v1/chat/completions` 同样按 agent 地址 / 编号路由，`model: "<user>/<team>"` 交给 team 的 lead。

### 人设注入（每个驱动一次，只在 L1）

| 驱动 | 方式 |
|---|---|
| webot | `core/agent.py` 每次构造上下文时按 agent 的 `persona` + 默认 `team` 解析人设，放进 system prompt；不额外注入团队成员或工作流列表，也不重复列出工具名称 |
| acpx | `AcpxAdapter.ensure_session()`：新建 session 时把身份 prompt 拼到第一条消息前；session 已存在不重复注入 |
| openclaw / http | gateway 对比 agent 的 `runtime.identity_prompt`：没发过或变了才发，成功后记回 agent；重置 agent 会清空它 |
| llm（临时人设） | 每次调用带人设 |

群聊规则与回复方式来自 `data/prompts/conversation_rules.txt`，WeBot 的 system prompt 和外部 agent 的身份 prompt 共用这一份。

## L2 通讯层（`src/comms/`）

`clawcross.db` 里的三张表：

- `conversations`：`conv_id`、owner、title、`kind`（`group` / `direct`）、`primary_agent`、`dnd`、`meta`（如 `{"team": …}`）；
- `conversation_members`：成员（`u:<user>` 或 `ag_…`）、昵称、禁言、`read_cursor`；agent 被删除时成员行级联删除；
- `conversation_messages`：发言者编号、内容、结构化 mentions、reply_to、附件、`client_msg_id`（去重）。

`Conversations.post()` 存消息后唤醒：

- 人发言：没有 @ → 唤醒主 agent（没有主 agent 则唤醒全部）；有 @ → 只唤醒被 @ 的；
- agent 发言：有主 agent 时，其他 agent 的发言只唤醒主 agent；主 agent（或没有主 agent 时的任何 agent）只唤醒它 @ 的成员，不 @ 就谁也不唤醒；
- `@所有人` 唤醒全部 agent，只允许人和主 agent 使用；
- 私聊：唤醒那一个 agent；
- 免打扰（`dnd`）的会话不唤醒任何 agent；禁言的成员不被唤醒；
- `StormGuard` 限制 agent 之间连锁唤醒的深度与频率，人一发言就重置；
- 被唤醒的 agent 收到同一种信封（会话、发言者、内容、回复方式）；很久没被唤醒的成员先收到一份未读摘要（按 `read_cursor`）。

发言者认证：人以自己身份发言；本机服务持内部 token 时才能以 agent 身份发言（`POST /groups/{id}/messages {agent}`），且发言者必须是成员。

## L3 组合层

### Team（`src/teams/`）

- `team_members`（owner, team, agent_id, role, is_lead, position）：team 是 agent 的组合，不拥有 agent；同一 agent 可在多个 team；删除 agent 时自动退出所有 team。
- team 文件夹（`user_files/<owner>/teams/<team>/`）只放资产：`oasis_experts.json`（人设库）、workflow、skills、`team_settings.json`。
- `internal_agents.json` / `external_agents.json` 只是**导入导出格式**（`teams/manifest.py` 是唯一读写它们的地方）：导入把条目变成 agent + 成员关系并移走文件；导出按原格式重新生成，可移植导出去掉 `session` / `global_name` 与密钥。
- 调用 team 成员时由 L3 通过 `context={"team": …}` 传 team 上下文。

### 群聊（`src/groups/`）

`GroupService` 在 L2 会话上加微信式规则：群主管理、私聊唯一、team 群跟随 team（成员 = team 成员，主 agent = lead）。HTTP 见 `src/groups/routes.py`，前端经 `/proxy_groups/...` 原样转发。

### OASIS（`oasis/`）

参与者一律是 agent：`agent: <ref>`（团队里按角色名解析）或 `persona: <tag>`（临时 agent，`tools: none | all | [..]`，话题结束 `discard`）。`oasis/participants.py` 的 `Participant` 统一通过 gateway 的 `ask(response_format=…)` 取结构化回复；帖子记录作者编号 `author_id`。

### 定时任务

`<DATA_DIR>/timeset/tasks.json` 每条任务指向一个 agent 编号（可附 team）；到点由调度器经 L1 `deliver`。team 导出时按角色名导出，导入时映射回新成员。

## 前端

- Agent Center、团队面板、编排侧栏、手机页都只用 `/v1/agents`、`/v1/teams`、`/proxy_groups`：一种 agent，按平台分组显示，控制按钮对所有平台相同（status / cancel / reset）。
- 平台专属的只有运行时自身的配置界面（OpenClaw 工作区文件 / 工具 / channels、HTTP 的 `api_url` / `model`、ACP 的超时与权限），它们改的是 agent 的 `settings` 或运行时本身。

### 运行与显示是两个生命周期

浏览器页面是运行的订阅者：`disconnect` 只是不再显示，`cancel` 才要求 agent 停止。前端不能仅凭本地 `AbortError` 宣称远端已停止；以 `/v1/agents/{ref}/control` 的 `status` 为准。

### 主前端“+ 人设”快捷 Prompt

它是用户主动选择的临时提示，不是 agent 的正式人设：不要写回 agent 的 `persona`，不要用它判断首次连接。推荐一次性使用：下一次发送成功后清除。

## 修改检查清单

- L1 以外有没有读 `driver` / `config`、引用驱动名、直接调用传输层？（`test_layering.py`）
- 有没有在 L2 / L3 按平台分支？平台差异应在 L1 驱动里消化。
- 新的身份字段？agent 只有 `ag_` 编号；人是 `u:<user>`。
- team 有没有“拥有” agent？删除 team 不应删除别的 team 还在用的 agent。
- 人设是否只在 L1 注入一次？
- 导入导出格式（`internal_agents.json` / `external_agents.json` / 快照 zip）是否保持原样？

## 代码索引

| 主题 | 文件 |
|---|---|
| agent 总表 | `src/agents/store.py` |
| 单 agent 接口、驱动、回复渠道 | `src/agents/gateway.py`, `src/agents/messages.py` |
| 状态 / 取消 / 重置 / 历史 | `src/agents/control.py` |
| 身份 prompt 发送与放置 | `src/agents/gateway.py`（何时发）, `src/integrations/agent_session.py`（放在哪） |
| `/v1/agents` | `src/agents/routes.py` |
| 会话、唤醒、未读摘要 | `src/comms/store.py`, `src/comms/conversations.py`, `src/comms/delivery.py` |
| team 成员关系、导入导出格式、`/v1/teams` | `src/teams/store.py`, `src/teams/manifest.py`, `src/teams/routes.py` |
| 群聊 | `src/groups/service.py`, `src/groups/routes.py` |
| OASIS 参与者 | `oasis/participants.py`, `oasis/engine.py`, `oasis/agent_center.py` |
| 定时任务 | `src/utils/scheduler_service.py`, `src/utils/internal_alarm_utils.py` |
| 数据升级（`PRAGMA user_version`，启动时按版本执行） | `src/migrations/unify.py` |
| WeBot 人设 system prompt | `src/core/agent.py`, `src/webot/profiles.py` |
| ACP session 与首次 prompt | `src/integrations/acpx_adapter.py` |
| 分层检查 | `test/test_layering.py` |
