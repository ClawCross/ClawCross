# Agent 运行、全局视图与人设机制

本文是 Clawcross Agent 相关功能的开发约束与实现索引。它用于回答以下问题：

- Team、Agent 与全局 Agent 表之间是什么关系？
- Internal、OpenClaw、ACP、HTTP Agent 的身份和状态从哪里获取？
- 前端流式输出、页面切换和终止操作应如何理解？
- 正式 Agent 人设与主前端“+ 人设”有什么区别？
- 修改存储实现时，哪些外部接口和导入导出格式必须保持兼容？

本文同时记录当前实现和目标架构。标记为“目标”的内容不代表已经全部实现。

## 核心约束

### Team 是最小持久化单元

Team 是 Clawcross 的聚合根和最小可移植单元。属于 Team 的真实文件必须继续放在同一个 Team 目录中，包括：

- Internal / external Agent 配置；
- Persona / expert 配置；
- Team Skill；
- YAML / Python Workflow；
- Team 级定时任务与其他协作配置。

这样做的原因不仅是文件组织方便。Team Skill 可以是脚本，并可能读取或修改同一 Team 的其他文件；Agent、Persona、Workflow 之间也存在强绑定。将它们拆成多个全局真实数据源会破坏 Team 的自洽性、可审查性和可导出性。

### 全局表是视图，不是新的真实数据源

全局 Agent、Persona、Skill、Workflow 和 Cron“表”应当由用户目录和所有 Team 目录动态聚合得到。它们可以有短时内存缓存或查询索引，但不应成为另一套需要双向同步的权威配置。

```text
Team A files ─┐
Team B files ─┼─> dynamic catalogs / runtime views ─> contacts, status, search
Public files ─┘
```

Team 页面只是对全局视图按 Team 成员关系进行过滤。联系人或 Agent Center 页面则展示完整扁平视图。

### 不新增重复身份字段

不要为已有身份再发明一个同性质的 `agent_id`：

- External Agent 使用现有 `global_name`；
- Internal Agent 使用现有 `session` / `session_id`；
- WeBot 子 Agent 已经有自己的 `agent_id`，该字段只属于子 Agent 运行体系；
- 展示层如果需要无冲突键，可以在内存中使用 `kind + platform + existing_identity` 组成复合键，但不要写回 Team 文件成为第二身份。

### 存储变化不应推动外部调用方重写

Clawcross 的 CLI、主前端、OASIS、聊天机器人及其他客户端依赖现有请求格式。重构内部存储或增加动态视图时：

- 保持现有 API 请求格式兼容；
- 保持 `send_to_agent()` 的调用模型兼容；
- Team ZIP 的外部结构保持兼容；
- 导入和导出内部可以改为通过聚合层解析或生成，但不能无必要改变 ZIP 契约。

## Agent 调用层

`src/integrations/registry.py` 是跨平台发送的共享入口：

- `send_to_agent(request)`：非流式调用；
- `prepare_send_to_agent_stream(request)`：准备流式调用；
- `reset_agent(request)`：重置 Agent Session。

主前端、OASIS 和部分群聊/调度路径已经通过该层调用 Internal、OpenClaw、ACP 或 HTTP Connector。它是统一发送语义的正确扩展点。

当前它还不是完整的 Agent Run 控制面：没有统一 `run_id`，也没有统一的 connector cancel 方法。不要把“共享发送函数”误解为“已经统一了运行状态与终止语义”。

发送前的人设注入已经由 `src/integrations/agent_session.py` 统一处理。调用方只声明：

- `identity_prompt`：解析完成的人设、Skill 与 Workflow Prompt；
- `identity_global_name`：External Agent 的现有 `global_name`；
- `group_db_path`：HTTP Session 状态表；
- `identity_injection_mode`：兼容旧调用所需的注入位置。

调用方不得自行判断“第一次连接”并手工拼接人设。`send_to_agent()` 在发送前统一调用 `prepare_agent_session()`：

- ACP：把决定委托给 `AcpxAdapter.ensure_session()`，只有新建 acpx Session 才注入；
- HTTP：通过同一个 `inspect_http_agent_session()` 查询 Session，新 Session 或人设内容变化时注入；
- 无持久 Session Registry 的普通 HTTP 调用：无法确认首次状态，按稳定 System Prompt 处理并在状态中标记来源。

发送结果的 `meta.agent_session` 会返回 `initialized`、`should_inject_identity`、`source` 和 `prompt_changed`。ACP 的首次判断与 Session 创建是一个原子操作，因此发送前状态使用 `null` 表示由 acpx 决定，不伪造布尔值。

## 扁平 Agent 视图

### 已有数据来源

| Agent 类型 | 身份/配置来源 | 当前状态来源 | 状态含义限制 |
|---|---|---|---|
| Internal | `internal_agents.json`、Session 元信息 | `/sessions_status`、thread lock、TaskRegistry | 主要是 Session 是否忙碌 |
| OpenClaw | OpenClaw 全局配置、Team `external_agents.json` | OpenClaw sessions、`/acp_status` | 有 Session 不等于正在执行 |
| ACP | Team `external_agents.json`、acpx Session | `acpx sessions list`、`/acp_status` | 能发现连接，未必能确认运行 |
| HTTP external | Team `external_agents.json` | `http_agent_sessions`、外部历史 | 多数情况下是最近使用记录 |
| WeBot subagent | `webot_subagents.db` | 持久化 subagent/run 状态 | 已有独立 `agent_id` 与生命周期 |

`src/api/external_agent_registry.py` 的 `build_external_agents_map_for_owner()` 已经能够扫描用户级及各 Team 的 `external_agents.json`，形成外部 Agent 的扁平配置映射。

`src/api/ops_service.py` 的 `agent_control()` 在这些数据源之上生成统一视图并分派控制操作。它是兼容性新增入口；原有 `/cancel`、`/acp_control` 和 `/acp_status` 仍然保留。

### 统一控制入口

后端提供：

```text
POST /agent_control
```

同一个入口支持 `list`、`status`、`cancel`、`stop`、`new`、`reset` 和 `delete`。生命周期动作语义固定为：`cancel/stop` 只终止当前运行；`reset` 清除会话上下文但保留 Agent 配置；`delete` 删除 Agent 实体、会话状态及其配置引用。

请求示例：

```json
{
  "user_id": "default",
  "password": "",
  "action": "cancel",
  "kind": "external",
  "identity": "researcher",
  "team": "research_team",
  "refresh_external": true
}
```

身份字段不做重新编号：

- `kind=internal`：`identity` 是现有 `session`；
- `kind=external`：`identity` 是现有 `global_name`；
- `kind=subagent`：`identity` 是 WeBot 已有 `agent_id`。

`action=list` 时可以省略 `kind` 和 `identity`。`team` 只过滤动态视图，不改变 Team 文件。相同 External Agent 出现在多个 Team 时，返回一个条目并把成员关系聚合进 `teams` 数组。

控制分派如下：

| 类型 | 状态来源 | cancel / stop 分派 |
|---|---|---|
| Internal | thread lock + TaskRegistry | `agent.cancel_task(user#session)` |
| ACP | `acpx sessions list` | 现有 `acp_control()` / acpx cancel |
| OpenClaw | HTTP Session 表或 OpenClaw sessions | 现有 `acp_control()` / `/stop` |
| HTTP external | `http_agent_sessions` | 没有标准取消协议时明确返回 `unsupported` |
| WeBot subagent | subagent/run registry + TaskRegistry | 现有 `WeBotService.cancel_subagent()` |

这里的返回值刻意区分 `supported` 与 `cancelled`。请求被底层接受不等于远端工作已经完全退出，调用方不得只凭本地断流显示“终止成功”。

主前端通过 `/proxy_agent_control` 使用同一接口，并提供 Agent Center Grid：

- 顶部“Agents”按钮和移动端汉堡菜单均可打开；
- 首次打开使用快速视图，不阻塞等待外部 CLI 探测；
- “刷新状态”显式设置 `refresh_external=true`；
- 卡片分别展示 `status`、`connection_status` 和 `running_known`；
- 所有停止操作仍回到统一 `agent_control()`，前端不按 Agent 类型分支调用旧接口。
- “删除 Agent”同样调用 `agent_control(action=delete)`，请求只包含 `kind` 与 `identity`，不包含 Team 删除范围。

### 统一 DTO

统一视图建议至少包含：

```json
{
  "identity": "researcher",
  "kind": "external",
  "platform": "openclaw",
  "name": "研究员",
  "teams": ["research_team", "product_team"],
  "status": "running",
  "status_source": "openclaw_session",
  "sessions": [],
  "can_cancel": true,
  "supported_actions": ["status", "cancel", "stop"]
}
```

注意：

- `teams` 必须是数组；同一 External Agent 可以被多个 Team 引用；
- 当前 external registry 按 `global_name` 去重时可能只保留一个 `team`，统一视图需要显式聚合全部成员关系；
- `status_source` 必须保留，避免把推断状态伪装成确定事实；
- 无法确认时使用 `unknown`，不要默认写成 `idle`；
- `online` 只表示服务或 Session 可连接时，不应等同于 `running`。
- `running_known=false` 表示当前只能确认配置或 Session，不能确认远端是否仍在执行。

建议统一状态词表：

```text
unknown
offline
idle
running
cancelling
blocked
completed
failed
unavailable
```

## Agent Run、流式输出与前端

### Agent 运行与前端显示是两个生命周期

浏览器页面是 Agent Run 的订阅者，不是运行本身。应严格区分：

- `disconnect` / `unsubscribe`：前端不再接收或显示流，Agent 可以继续运行；
- `cancel`：明确要求后端或远端 Agent 停止执行。

主前端当前在浏览器存活期间按聊天上下文保存活动流。聊天上下文由平台、Session、OpenClaw Agent 和 ACP Tool 组成。切换主页面或 Session 时，旧流继续读取；返回原上下文时恢复已收到的文本。

纯前端方案无法保证以下场景续流：

- 浏览器刷新或关闭；
- 浏览器崩溃或被移动系统回收；
- 网络中断后跨连接补发；
- 在另一设备查看同一个运行。

这些能力需要后端持有 Run 和事件游标。目标形态可以保留现有请求格式，在响应头或 SSE meta 事件中附加 `run_id`。

### Internal 运行状态

Internal Agent 的运行状态主要由 `ThreadStateRegistry` 的 thread lock 和 busy source 提供，活动 asyncio Task 则由 `TaskRegistry` 跟踪。

主前端必须同时考虑：

- 浏览器当前持有的活动流；
- 后端 `/proxy_session_status` 返回的真实 busy 状态。

发送前必须再次检查 Internal Session 是否 busy。原因是 `OpenAIChatService._run_stream()` 在开始新请求时会先取消同一 Session 的旧 Task；错误显示“发送”可能让用户无意中顶掉正在执行的任务。

### 当前终止语义

| 通道 | 当前行为 | 可靠性说明 |
|---|---|---|
| Internal | `/cancel` -> `TaskRegistry.cancel()` | 对异步 Task 通常有效；阻塞线程/进程未必停止 |
| ACP | 浏览器断流时代理会 kill 直接 acpx 进程；另有 acpx Session cancel 能力 | 通用前端取消尚未完整接入 Session cancel |
| OpenClaw | 浏览器可以停止接收；控制面另有 `/stop` | 通用前端取消尚未保证发送远端 `/stop` |
| OASIS | engine cancel flag + outer task cancel | 编排可停；正在运行的远端调用或同步 Python 未必立即停止 |
| Commander background job | Task cancel + 进程/进程组终止 | 独立后台命令路径相对完整 |

前端不能仅根据本地 `AbortError` 宣称远端 Agent 已确认终止。正确状态序列是：

```text
running -> cancelling -> cancelled
                      -> cancel_failed / still_running
```

## 正式人设机制

正式人设属于 Agent 或 Team 配置，不属于某条临时用户消息。不同通道按自身会话模型注入。

### Internal Agent

Internal Session 元信息中的 `tag` 用于解析 Team Persona。`src/core/agent.py` 在每次构造模型上下文时调用 `_get_internal_session_persona_prompt()`，并将稳定身份加入基础 System Prompt。

这不是每轮往历史中追加一条人设消息，而是每次模型调用都携带稳定 System Prompt。它符合正常多轮对话模型，并能减少长对话中的身份漂移。

### ACP / acpx External Agent

`AcpxAdapter.ensure_session()` 先检查 acpx Session 是否已经存在：

- Session 不存在：创建 Session，并把 `system_prompt` 放入 pending initial prompt；
- 第一条真实 Prompt：将 pending initial prompt 拼到用户 Prompt 前；
- Session 已存在：不重复注入；
- Session 被关闭、重建或显式 reset：下一次重新注入。

对应实现：`src/integrations/acpx_adapter.py`。

### HTTP External Agent

`http_agent_sessions` 表记录 External HTTP Session 已使用的 prompt 文本。`upsert_http_agent_session()` 返回是否需要注入：

- 没有 Session 记录：注入；
- 已有记录且 Prompt 相同：不注入；
- Persona、Skill、Workflow 等组成的 Prompt 发生变化：重新注入；
- Session 记录被删除：下一次重新注入。

对应实现：

- `src/api/group_service.py`；
- `src/api/group_repository.py`。

### OASIS

`SessionExpert` 和 `ExternalExpert` 使用实例内 `_initialized` 判断第一轮。这里的“第一次”是当前 OASIS Expert 实例、Topic 或 Workflow Run 的第一次参与，不是该 Agent 一生中的第一次连接。服务重启或新建 OASIS 运行后可以再次注入。

对应实现：`oasis/experts.py`。

### OpenClaw IDENTITY.md

写入 Agent Workspace 的 `IDENTITY.md` 属于 OpenClaw Agent 的持久身份文件。它不是 Clawcross 主前端快捷 Prompt，也不应依赖浏览器中的首次发送标记。

## 主前端“+ 人设”快捷 Prompt

主前端“+ 人设”是用户主动选择的快捷 Prompt，不是正式 Agent 身份，也不是后端首次连接机制。

语义上它等价于：

```text
发送内容 = 用户选择的临时人设提示 + Workflow 提示 + 用户输入
```

因此开发时必须遵守：

- 不要把快捷 Prompt 写回 Agent 的正式 Persona；
- 不要用它判断 ACP、HTTP 或 Internal Agent 是否首次连接；
- 不要让它覆盖 Team 文件中的权威身份配置；
- UI 应明确它是用户控制的临时上下文。

当前主前端使用 `personaInjectedSession !== currentSessionId` 避免重复拼接，但该实现并不代表真实首次连接，并存在以下限制：

- `switchToSession()` 会清空标记，A -> B -> A 后可能再次注入；
- 单一字符串不能同时记录多个 Session；
- 键中没有平台、OpenClaw Agent 或 ACP Tool，可能漏注入或串上下文；
- 请求发送前就设置为已注入，请求失败后可能不会自动重试；
- 浏览器刷新后内存标记丢失；
- 正式 Internal Persona 与快捷 Persona 可能同时出现，这是两层不同语义，不应被误认为重复的同一机制。

如果产品语义确定为“快捷 Prompt”，推荐默认行为是一次性：

1. 用户选择“+ 人设”；
2. 下一次成功发送时拼接；
3. 发送成功后清除选择；
4. 请求未成功建立时保留，允许重试。

如未来需要“当前会话持续使用”，应作为显式模式提供，并使用 `platform + agent/session + persona` 作为前端上下文键，而不是复用后端首次连接概念。

## 修改检查清单

修改 Agent、Persona、状态或前端调用代码前，检查：

- 是否仍以 Team 文件为真实数据源？
- 是否错误创建了第二套 Agent 身份？
- 多 Team 成员关系是否聚合为数组？
- `online`、`idle`、`running` 是否被正确区分？
- 状态是否带有来源和检查时间？
- 页面断流是否被误当成 Agent cancel？
- Internal 新发送是否会意外取消旧运行？
- 正式 Persona 与快捷 Prompt 是否被混用？
- ACP 新 Session、HTTP Session Prompt 变化、OASIS 新 Run 的重新注入是否仍正确？
- CLI、前端、OASIS 和外部 API 请求格式是否保持兼容？
- Team ZIP 的外部结构是否保持兼容？

## 代码索引

| 主题 | 主要文件 |
|---|---|
| 跨平台发送入口 | `src/integrations/registry.py` |
| Session 状态与统一人设注入 | `src/integrations/agent_session.py` |
| ACP Session 与首次 Prompt | `src/integrations/acpx_adapter.py` |
| External Persona 组装 | `src/integrations/external_persona.py` |
| External Agent 扁平配置映射 | `src/api/external_agent_registry.py` |
| External Agent 状态 | `src/api/ops_service.py` |
| 统一 Agent 列表与控制入口 | `src/api/ops_models.py`, `src/api/ops_routes.py`, `src/api/ops_service.py` |
| HTTP External Session 首次判断 | `src/api/group_repository.py`, `src/api/group_service.py` |
| Internal Persona System Prompt | `src/core/agent.py`, `src/webot/profiles.py` |
| Internal Task / busy 状态 | `src/core/agent_runtime_state.py`, `src/api/session_service.py` |
| OASIS 第一轮人设 | `oasis/experts.py` |
| 主前端流、状态、快捷人设 | `frontend/js/main.js` |
| OpenClaw Agent 配置 | `oasis/openclaw_routes.py` |
