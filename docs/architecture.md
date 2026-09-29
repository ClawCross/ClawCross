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
│   运行方式：webot · acpx（codex / claude / gemini…）· openclaw · http · llm（模型调用）│
└──────────────────────────────────────────────────────────────────────────────┘
```

依赖只能向下，`test/test_layering.py` 检查：L1 不 import 群聊、team、OASIS；运行时（`src/external`、`src/webot/driver.py`）不知道组合层，也只有 L1 用它们；运行时只在 Agent 服务里，其他进程走入口。

## L1：agent（`src/agents/`）

### 一张表：所有会话

`<DATA_DIR>/agents.db` 的 `agents` 表，一个会话一行，主键是（`owner`, `agent_id`）：

| 字段 | 说明 |
|---|---|
| `agent_id` | 会话号。可以是调用方给的任意号（字母、数字、`_`、`-`，至多 64 位），也可以由系统分配 `ag_…`。在所属用户空间内唯一，就像内网地址；不同用户可以用同一个号 |
| `owner` | 所属用户空间 |
| `name` | 显示名，默认等于编号 |
| `driver` | 运行方式，只用来决定把信息转给谁 |
| `config` | agent 自己的设置：`persona` 是人设文本本身（用人设库里的人设时复制一份进来）；`teams` 是它所在的 team（可以有多个，只由 team 层随成员变动维护，和各 team 的 `members.json` 一致）；WeBot 还有 `tools`（它有的工具，不填为全部）和 `llm`（模型）；外部 agent 有 `platform`、`api_url`、`api_key`、`model`、`headers`、`meta`，OpenClaw 还有 `global_name`，指明是哪一个 OpenClaw agent |
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
POST   /v1/agents                   {agent_id?, name?, platform, persona?, team?, tools?, llm?, global_name?, api_url?, api_key?, model?…}
GET    /v1/agents/{ref}
PATCH  /v1/agents/{ref}             {name?, settings}
DELETE /v1/agents/{ref}             同时从所有 team 与群聊里移除
POST   /v1/agents/{ref}/control     {action: status | cancel | reset}
GET    /v1/agents/{ref}/history
GET    /v1/models                   新 agent 可用的运行方式
```

`ref` 是编号，或 `<team>.<名字>`（见 team）。agent 卡片不返回密钥，只给 `has_api_key`。

`POST /v1/agents/{id}/messages` 的 `timeout` 是秒数，`0` 表示等到 agent 做完为止，不填用运行时的默认值；`response_format` 是 OpenAI 的 `response_format`。

### 运行时

每种运行方式是一个运行时（`agents/runtime.py` 的 `Runtime`），对上提供同样的调用接口：

- `ask(agent, msg, *, context, mode, tools, response_format, timeout)`：发送并等回复。`response_format` 由各运行时按自己的能力处理：WeBot 在工具调用阶段结束后，单独用模型服务的受限解码生成最终的结构化回复；外部 agent 按自身协议处理。
- `trigger(agent, msg, …)`：system trigger 语义，交给它立即处理，不等回复。
- `inbox(agent, msg)`：inbox 语义。

控制面是各运行时自己的：`controls` 列出它支持的动作，另有 `status`、`history`，以及删除 agent 时释放资源的 `destroy`。

| 运行时 | 代码 |
|---|---|
| WeBot | `src/webot/driver.py`：在进程内调用 WeBot 的对话、system trigger 和会话服务；控制面直接读引擎 |
| acpx（codex / claude / gemini…） | `src/external/acp.py`，经 `src/external/acpx.py`（acpx CLI） |
| OpenClaw | `src/external/openclaw.py`（HTTP；取消、重置经 acpx） |
| HTTP | `src/external/http.py` |
| llm（模型调用：不带工具，不记得上一条） | `src/external/llm.py` |

外部运行时自己发送，共用 `src/external/session.py`（以编号命名的会话、身份 prompt）和 `src/external/history.py`（往来记录）。哪些平台是 ACP 工具由 `src/agents/platforms.py` 决定。

WeBot 的代码都在 `src/webot/` 一个包里：

| 位置 | 内容 |
|---|---|
| `driver.py` | `WebotRuntime`：L1 只通过它调用 WeBot |
| `engine/` | agent 循环、工具绑定、工具 schema |
| `api/` | WeBot 自己的服务和路由：对话（`openai_*`）、system trigger 与收件箱（`system_*`）、会话（`session_*`）、运行时面板（`routes.py`、`service.py`） |
| `tools/` | WeBot 的 MCP 工具服务（命令、文件、OASIS、会话、定时、搜索……），由引擎作为子进程启动 |
| 其余模块 | 状态存储、审批、沙箱、技能与记忆、压缩、子 agent 等 |

### 单 agent 接口（`gateway.py`）

gateway 按 agent 的 `driver` 找到运行时，把调用交给它：`ask`、`trigger`、`inbox`，控制面 `status`、`control`、`history`、`destroy`。

`/v1/chat/completions` 也经过 gateway（`chat`）：会说 OpenAI 对话协议的运行时自己回答（WeBot：流式、调用方自带的工具，像聊天窗口发来的消息一样接管当前这一轮）；其他运行时是问它最后一条用户消息，再按协议的格式返回（流式时整段作为一个 delta）。

运行时只在 Agent 服务里。其他进程（OASIS、定时任务）用 `agents/client.py` 走上面的入口：`/v1/agents` 新建、询问、删除，`/system_trigger` 触发。

附件统一为 `{type, name, mime_type, data}`。`parse_openai_content` 把 OpenAI 格式的图片、音频、文件解析成附件，再由各运行方式按能力发送：图片和音频作为多模态附件，文本文件内联，其他二进制只写文件名。

临时 agent 的编号以 `tmp__` 开头，为一件事新建，事情做完就删掉（`DELETE /v1/agents/{id}`）。

各运行时的控制面：

| 动作 | WeBot | acpx / OpenClaw | HTTP |
|---|---|---|---|
| `status` | 忙碌、待处理消息、上下文占用 | 该会话的状态 | 最近使用记录 |
| `cancel` | 取消当前任务 | acpx cancel | 不支持 |
| `reset` | 清空会话 | 重置会话，忘记已注入的身份 | 忘记已注入的身份 |
| `history` | 会话消息（含工具调用） | 外部往来记录 | 同左 |
| `destroy` | 删除时删掉会话 | 关闭会话并删除往来记录 | 删除往来记录 |

agent 的人设和工具是它自己的，各运行方式按自己的方式用：

- WeBot 把人设文本放进会话的 system prompt（会话建立时固定下来，之后改人设对新会话或重置后的会话生效）；只绑定 agent 自己的工具，每次请求的 `enabled_tools` 和运行模式在其中再收窄本轮能执行的；
- acpx 在新会话的第一条消息前放身份 prompt；
- OpenClaw 和 HTTP 在身份 prompt 没发过或有变化时才发。

## L2：组合（只引用编号）

### 群聊 / 私聊（`src/comms/`、`src/groups/`）

- 是对"发信息"的封装。
- 存在独立的 `<DATA_DIR>/conversations.db` 里：会话、成员（`u:<用户>` 或 agent 编号）、消息。
- 建群时成员可以写编号或 `<team>.<名字>`；群与 team 没有任何关联。

有人发言时，按下面的规则把信息投进成员 agent 的 inbox：

- WeBot 成员在会话空闲时处理，按会话自己的模式运行；通知里是一行摘要（在哪、谁、是否 @ 它、开头几十个字），附件随通知一起送到，正文用 `read_session_inbox` 读；
- 其他运行方式没有收件箱，立即收到，按发消息时选的模式运行。

唤醒规则：

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
- `persona: <tag>` 是为这个话题新建的临时 agent（`tmp__<话题>__<tag>__<n>`）：不带工具时运行方式是 llm，带工具时是 WeBot；第一次发言前经 `POST /v1/agents` 新建，话题结束时删除。
- OASIS 是单独的进程，经 `agents/client.py` 按编号调用 agent。
- 不往 agent 里传 team。

### team（`src/teams/`）

- 一个文件夹（`user_files/<owner>/teams/<team>/`），就是一个命名空间，放成员、人设库（`oasis_experts.json`）、技能、定时任务和 workflow。
- 成员记在 `members.json` 里：`{agent: 编号, name: team 内名字, lead?, extra?}`；`extra.tag` 是成员用的 team 人设。team 内的 agent 可以称作 `<team>.<name>`，三种入口都认这种写法，而且换了机器也能用同一个名字找到对应的 agent。
- `internal_agents.json` / `external_agents.json` 是导入导出格式，只有 `teams/manifest.py` 读写，格式不变：
  - `session`（内部条目）和 `global_name`（外部条目）就是 agent 编号；OpenClaw 条目的 `global_name` 是 OpenClaw agent 名；
  - 导入时，team 里已有同名成员就是那个成员，条目指向已有 agent 就用那个 agent，否则新建；
  - 新建的 agent 得到一份自己的人设文本：条目里的 `persona`，或按 `tag` 在人设库里找（先找 team 自己的 `oasis_experts.json`）；`tag` 留在成员上；
  - 导出时写回 `tag` 和 agent 的 `persona` 文本；可移植导出不带编号和密钥。

### 定时任务

- 记录在 `<DATA_DIR>/timeset/tasks.json`，每条指向一个 agent 编号，创建时也可以写 `<team>.<名字>`；到点后按 system trigger 投递。
- team 导出时，定时任务按成员名导出，导入时再映射回编号。

## 代码索引

| 主题 | 文件 |
|---|---|
| agent 表 | `src/agents/store.py` |
| 入口 /v1、inbox | `src/agents/routes.py`, `src/agents/openai.py`（`/v1/chat/completions`、`/v1/models`） |
| system trigger | `src/webot/api/system_service.py` |
| 单 agent 接口、附件 | `src/agents/gateway.py`, `src/agents/messages.py` |
| 运行时（调用接口与控制面） | `src/agents/runtime.py`, `src/webot/driver.py`, `src/external/` |
| 群聊 | `src/comms/`, `src/groups/` |
| team、导入导出 | `src/teams/store.py`, `src/teams/manifest.py`, `src/teams/routes.py` |
| workflow | `oasis/engine.py`, `oasis/participants.py`, `oasis/agent_center.py` |
| 定时任务 | `src/utils/scheduler_service.py`, `src/utils/internal_alarm_utils.py` |
| 分层检查 | `test/test_layering.py` |
