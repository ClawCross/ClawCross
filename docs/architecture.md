# ClawCross 结构：agent 与组合

ClawCross 把一台机器上所有 agent 统一成一种东西：**有编号的黑盒**。
第一层（L1）只管 agent：一张表，一个编号，几种入口。
第二层（L2）是组合：群聊、workflow（OASIS）和 team 都只引用 agent 编号，不关心 agent 内部怎么运行。

```
网页 / 手机 / CLI / 第三方 OpenAI 客户端
   │  /v1/agents · /v1/chat/completions · /system_trigger · /v1/teams · /groups
   ▼
┌ L2 组合 ─ 群聊/私聊（发信息的封装）· workflow（按顺序调用 agent）· team（命名空间）──┐
├ L1 agent ─ 一张表：每个会话一行，会话号 = agent 编号 · 三种入口 · 各运行时 ────────┤
│   运行方式：webot · acpx（codex / claude / gemini…）· openclaw · http · llm（一次调用）│
└──────────────────────────────────────────────────────────────────────────────┘
```

依赖只能向下，`test/test_layering.py` 检查：L1 不 import 群聊、team、OASIS；只有各运行时（`src/external`、`src/webot/driver.py`）直接调用传输层（`integrations.*`）。

## L1：agent（`src/agents/`）

### 一张表：所有会话

`<DATA_DIR>/agents.db` 的 `agents` 表，一个会话一行，主键是（`owner`, `agent_id`）：

| 字段 | 说明 |
|---|---|
| `agent_id` | 会话号。可以是调用方给的任意号（字母、数字、`_`、`-`，至多 64 位），也可以由系统分配 `ag_…`。在所属用户空间内唯一，就像内网地址；不同用户可以用同一个号 |
| `owner` | 所属用户空间 |
| `name` | 显示名，默认等于编号 |
| `driver` | 运行方式，只用来决定把信息转给谁 |
| `config` | 运行方式自己的配置：外部 agent 有 `platform`、`api_url`、`api_key`、`model`、`headers`、`meta`；OpenClaw 还有 `global_name`，指明是哪一个 OpenClaw agent |
| `runtime` | 运行时已经知道的东西（发过的身份 prompt、最后使用时间），只有 L1 读写 |

在各运行时内部，会话都以编号命名：

- WeBot 的线程是 `<owner>#<agent_id>`；
- acpx 的 session 名是 `clawcross-<owner>-<agent_id>`，所以同一个 codex 可以有任意多个会话，每个会话是一个 agent；
- OpenClaw 的 session key 是 `agent:<global_name>:clawcross-<owner>-<agent_id>`；
- 外部往来记录也按这个 session 名保存。

### 三种入口，都按编号

**号不存在，就新建一个 agent**：请求里指定的运行方式，不指定就是 WeBot。读取（GET、控制、历史）不会新建。

| 入口 | 说明 |
|---|---|
| **/v1** | `POST /v1/agents/{id}/messages`：发送并等回复。<br>`POST /v1/chat/completions`：`session_id` 是编号（必填）；`model` 只在新建时决定运行方式，不是运行方式名（如 `gpt-4o`）时按 WeBot 处理 |
| **system trigger** | `POST /system_trigger {user_id, session_id, text}`（内部 token）：交给 agent 立即处理。WeBot 走自己的触发队列，其他运行方式在后台发送 |
| **inbox** | `POST /v1/agents/{id}/inbox`：放进收件箱。WeBot 的收件箱在 `/system_trigger`（带 `inbox_source_session`）里：记下发件人，会话空闲时处理；其他运行方式没有收件箱，直接在后台发送（acpx 自己按会话排队） |

其他接口：

```
GET    /v1/agents                   ?status=1 附带状态
POST   /v1/agents                   {agent_id?, name?, platform, persona?, team?, tools?, global_name?, api_url?, api_key?, model?…}
GET    /v1/agents/{ref}
PATCH  /v1/agents/{ref}             {name?, settings}
DELETE /v1/agents/{ref}             同时从所有 team 与群聊里移除
POST   /v1/agents/{ref}/control     {action: status | cancel | reset}
GET    /v1/agents/{ref}/history
GET    /v1/models                   新 agent 可用的运行方式
```

`ref` 是编号，或 `<team>.<名字>`（见 team）。agent 卡片不返回密钥，只给 `has_api_key`。

### 运行时

每种运行方式是一个运行时（`agents/runtime.py` 的 `Runtime`），对上提供同样的调用接口：

- `ask(agent, msg, *, context, mode, tools, response_format, timeout)`：发送并等回复。`response_format` 由各运行时按自己的能力处理：WeBot 在工具调用阶段结束后，单独用模型服务的受限解码生成最终的结构化回复；外部 agent 按自身协议处理。
- `trigger(agent, msg, …)`：system trigger 语义，交给它立即处理，不等回复。
- `inbox(agent, msg)`：inbox 语义。

控制面是各运行时自己的：`controls` 列出它支持的动作，另有 `status`、`history`，以及删除 agent 时释放资源的 `destroy`。

| 运行时 | 代码 |
|---|---|
| WeBot | `src/webot/driver.py`：调用走 `/v1/chat/completions` 和 `/system_trigger`；控制面直接读引擎，只在 Agent 服务里有 |
| acpx（codex / claude / gemini…） | `src/external/acp.py` |
| OpenClaw | `src/external/openclaw.py` |
| HTTP | `src/external/http.py` |
| llm（一次调用） | `src/external/llm.py` |

外部运行时共用 `src/external/session.py`：以编号命名的会话、身份 prompt、往来记录。

### 单 agent 接口（`gateway.py`）

gateway 按 agent 的 `driver` 找到运行时，把调用交给它：`ask`、`trigger`、`inbox`，控制面 `status`、`control`、`history`、`destroy`；`discard(agent)` 删除临时 WeBot 会话，连同它在表里的行。

附件统一为 `{type, name, mime_type, data}`。`parse_openai_content` 把 OpenAI 格式的图片、音频、文件解析成附件，再由各运行方式按能力发送：图片和音频作为多模态附件，文本文件内联，其他二进制只写文件名。

临时 agent：

- `persona_agent()`：一次带人设的模型调用，不进表；
- `temp_session_agent()`：带工具的临时 WeBot 会话（`tmp__…`），用完即删。

各运行时的控制面：

| 动作 | WeBot | acpx / OpenClaw | HTTP |
|---|---|---|---|
| `status` | 忙碌、待处理消息、上下文占用 | 该会话的状态 | 最近使用记录 |
| `cancel` | 取消当前任务 | acpx cancel | 不支持 |
| `reset` | 清空会话 | 重置会话，忘记已注入的身份 | 忘记已注入的身份 |
| `history` | 会话消息（含工具调用） | 外部往来记录 | 同左 |
| `destroy` | 删除时删掉会话 | 关闭会话并删除往来记录 | 删除往来记录 |

agent 内部的人设、技能、工具，是各运行方式自己的事：

- WeBot 按自己的配置组装 system prompt；
- acpx 在新会话的第一条消息前放身份 prompt；
- OpenClaw 和 HTTP 在身份 prompt 没发过或有变化时才发。

## L2：组合（只引用编号）

### 群聊 / 私聊（`src/comms/`、`src/groups/`）

- 是对"发信息"的封装。
- 存在独立的 `<DATA_DIR>/conversations.db` 里：会话、成员（`u:<用户>` 或 agent 编号）、消息。
- 建群时成员可以写编号或 `<team>.<名字>`；群与 team 没有任何关联。

有人发言时，按下面的规则把信息投递给成员 agent：

- 人发言：没有 @ 时唤醒主 agent，没设主 agent 就唤醒全部；有 @ 时只唤醒被 @ 的。
- agent 发言：只唤醒它 @ 的成员；设了主 agent 时，其他 agent 的发言只送到主 agent。
- `@所有人` 只允许人和主 agent 使用。
- 私聊：唤醒唯一的那个 agent。
- 免打扰的会话、被禁言的成员不唤醒。
- `StormGuard` 限制 agent 之间的连锁唤醒，人一发言就重置。
- 被唤醒的 agent 收到同一种信封，很久没被唤醒的先收到一份未读摘要。

只有本机服务持内部 token 时，才能以 agent 身份发言，而且发言者必须是群成员。

### workflow（OASIS，`oasis/`）

- 按顺序调用 agent。YAML 里的 `agent: <ref>` 在 **team 模式**下先按该 team 的成员名查找，然后按 `<team>.<名字>` 或编号查找；新编号就是新 agent。
- `persona: <tag>` 是临时 agent。
- 不往 agent 里传 team。

### team（`src/teams/`）

- 一个文件夹（`user_files/<owner>/teams/<team>/`），就是一个命名空间，放成员、人设库（`oasis_experts.json`）、技能、定时任务和 workflow。
- 成员记在 `members.json` 里：`{agent: 编号, name: team 内名字, lead?}`。team 内的 agent 可以称作 `<team>.<name>`，三种入口都认这种写法，而且换了机器也能用同一个名字找到对应的 agent。
- `internal_agents.json` / `external_agents.json` 是导入导出格式，只有 `teams/manifest.py` 读写，格式不变：
  - `session`（内部条目）和 `global_name`（外部条目）就是 agent 编号；OpenClaw 条目的 `global_name` 是 OpenClaw agent 名；
  - 导入时，team 里已有同名成员就是那个成员，条目指向已有 agent 就用那个 agent，否则新建；
  - 可移植导出不带编号和密钥。

### 定时任务

- 记录在 `<DATA_DIR>/timeset/tasks.json`，每条指向一个 agent 编号，创建时也可以写 `<team>.<名字>`；到点后按 system trigger 投递。
- team 导出时，定时任务按成员名导出，导入时再映射回编号。

## 代码索引

| 主题 | 文件 |
|---|---|
| agent 表 | `src/agents/store.py` |
| 入口 /v1、inbox | `src/agents/routes.py`, `src/api/openai_service.py` |
| system trigger | `src/api/system_service.py` |
| 单 agent 接口、附件 | `src/agents/gateway.py`, `src/agents/messages.py` |
| 运行时（调用接口与控制面） | `src/agents/runtime.py`, `src/webot/driver.py`, `src/external/` |
| 群聊 | `src/comms/`, `src/groups/` |
| team、导入导出 | `src/teams/store.py`, `src/teams/manifest.py`, `src/teams/routes.py` |
| workflow | `oasis/engine.py`, `oasis/participants.py`, `oasis/agent_center.py` |
| 定时任务 | `src/utils/scheduler_service.py`, `src/utils/internal_alarm_utils.py` |
| 分层检查 | `test/test_layering.py` |
