# Runtime Reference

This reference captures the architecture, service responsibilities, and runtime data layout for Clawcross’s Claude-Code-inspired agent runtime. It emphasizes WeBot, session state, MCP tooling, and the canonical runtime DTO that now powers the browser UI, CLI/BFF proxies, voice state, and memory/Kairos surfaces.

## Architecture Map

```
Browser / Studio UI
    -> `src/frontend/server.py` (Flask UI + session auth + runtime proxies)
    -> `src/frontend/static/js/main.js` + runtime panel wiring (current-session card and voice controls)
FastAPI services
    -> `src/backend/server.py` (OpenAI-compatible chat endpoints, session history, cancel)
    -> `src/backend/webot/api/routes.py` (runtime + policy APIs via `WeBotService`)
    -> `src/backend/webot/api/service.py` (serializes runtime DTOs, policy/plan/todo persistence)
    -> `src/backend/webot/mcp/webot.py` (MCP tools: subagents, session messages, inbox, plans and todos)
    -> `src/backend/ops/service.py` (voice/TTS + direct connect hooks for audio uploads)
    -> `src/backend/webot/memory.py` / `src/backend/webot/voice.py` (memory and voice services)
Persistence
    -> `data/webot_agents/<user>#<agent>.db` (runs, inbox, approvals, permits, artifacts, session state, memory, voice)
    -> `data/webot_subagents.db` (subagent metadata)
    -> `data/user_files/{user_id}/` (profiles, policies, runtime artifacts, memory dirs, logs)
Side systems
    -> `src/backend/oasis/` (Town Mode, workflows, swarm engine)
    -> `src/backend/external/acpx.py` (ACP exchange with external AI agents via acpx CLI)
    -> WeBot dream pipeline (`webot_memory.py`) as the current browser-native autoDream layer
```

## Service Responsibilities

| Service | Ownership |
|---|---|
| `src/frontend/server.py` | Flask UI shell, authentication, WeBot runtime proxy routes (`/proxy_webot_*`), voice/TTS proxies. |
| `src/backend/server.py` | OpenAI-compatible chat API, session history, cancel, provider routing. |
| `src/backend/webot/api/service.py` | Serializes DTO (mode, plan, todos, approvals, inbox, artifacts, runs, relationships, voice/memory), enforces auth, counts inbox queue, exposes policy endpoints. |
| `src/backend/webot/mcp/webot.py` | Durable spawn/send/cancel workflows, background run leasing, inbox delivery, plan/todo updates, runtime artifact logging. |
| `src/backend/webot/runtime_store.py` | SQLite tables for runs, attempts, inbox messages, artifacts, session modes, verifications, tool approvals, memory state, voice state; helpers for leases/heartbeats/interruption/events. |
| `src/backend/webot/runtime.py` | Mode normalization, blocked tool lists, turn-limit messaging, surgical heuristics for plan/execute/review. |
| `src/backend/webot/policy.py` | Normalizes tool policies, events (`session_start`, `permission_request`, `stop`, etc.), hook definitions, serialization, router for `save_tool_policy_config`. |
| `src/backend/webot/engine/agent.py` | Enforces tool filtering, injects runtime context, proxies MCP tooling into session handler, budgets history with `webot_context`. |
| `src/backend/ops/service.py` | Text-to-speech / audio proxy for voice mode; writes audio metadata into runtime payload via the frontend (`src/frontend/server.py`). |
| `src/backend/webot/profiles.py` | Profile definitions (`general`, `research`, `planner`, `coder`, `reviewer`, `verifier`), helper `slugify`, built-in tool sets, user extension loading. |
| `src/backend/webot/context.py` | Runtime state assembly and deltas, including group metadata only; rebase the first retained state after compression. |
| `src/backend/webot/compression.py` | Persistent rolling summaries, whole-turn boundaries, summary caps, temporary bounded views and oversized input artifacts. Original messages remain stored. |
| `src/backend/webot/engine/background_compaction.py` | Prepare summaries from frozen snapshots after a turn; version checks and reset generation prevent stale commits. |
| `src/backend/webot/workspace.py` | Worktree/remote/shared workspace resolution used when rendering runtime panel workspace text. |
| `src/frontend/proxies/webot.py` | Additional Flask proxies for runtime mode updates, plan/todo/verification APIs, supporting UI actions. |
| `src/backend/webot/memory.py` | Per-project memory directories, `MEMORY.md`, relevant entry recall, daily logs, dream gating, Kairos flags. |
| `src/backend/webot/voice.py` | Voice defaults + persisted per-session voice state derived from current LLM/audio provider. |

## Runtime DTO

Every runtime request (`/webot/session-runtime` → `WeBotService.get_session_runtime`) returns:

- `mode`: current `execute/agent/plan/review/yolo` mode plus reason/status.
- `plan`, `todos`, `verifications`, `approvals`: persisted states from `webot_runtime_store`.
- `inbox`: messages from `webot_session_inbox` with `summary`, delivery status, and `read_at`. `send_to_session` and the inbox API share the queue; idle delivery sends summaries, and `read_session_inbox` retrieves full bodies on demand and marks the returned messages read. The dynamic context includes new/unread counts and up to three new summaries only on the first inference of a turn with queued messages. A delivered digest already carries that notice, and previously notified unread messages are not repeated in later dynamic blocks.
- `artifacts`: runtime artifacts stored when budgets trigger (`webot_context`, `_deliver_inbox_messages`).
- `runs`: `list_runs_for_session` results with `run_kind`, `mode`, `events`.
- `active_run`: latest `queued`/`running` run (main session or child).
- `relationships`: `parent_session` plus `children` aggregated via `list_subagents_for_parent_session`.
- `memory`: per-project memory metadata, daily logs, Kairos flag, dream timestamps, relevant entries, dream eligibility.
- `voice`: enabled flag, provider defaults, STT/TTS models, read-aloud setting, last transcript.

Internal modules use this DTO to keep the runtime panel, Flask proxies, MCP tools, and prompt context injection in sync.

## Data Layout

```
data/
├── webot_agents/     (one DB per Agent: runs / attempts / inbox / approvals / permits / artifacts / session_mode)
├── webot_subagents.db (agent metadata: id/session/parent/status)
├── user_files/
│   └── {user_id}/
│       ├── webot_tool_policy.json
│       ├── webot_agent_profiles.json
│       ├── webot_inbox_deliveries/
│       ├── webot_tool_events.jsonl
│       ├── webot_compactions/
│       ├── projects/{project_slug}/memory/
│       │   ├── MEMORY.md
│       │   └── logs/YYYY/MM/YYYY-MM-DD.md
│       └── ... (artifacts)
```

## API Surface

- `/webot/subagents` – list subagents with runtime status and queued inbox count.
- `/webot/subagents/history` – fetch persisted snapshot messages for a subagent session.
- `/webot/subagents/cancel` – cancel background runs gracefully.
- `/webot/session-runtime` – primary runtime DTO consumed by Studio / CLI.
- `/webot/session-mode` – switch execute/agent/plan/review/yolo.
- `/webot/lsp` – OpenSeek-style best-effort diagnostics for a workspace file (Python, TypeScript, JavaScript, JSON).
- `/webot/session-inbox` – list a session's inbox. Sending is `send_to_session` (or `POST /v1/agents/<id>/inbox`); delivering what is queued is `POST /v1/agents/<id>/control` `{"action": "deliver_inbox"}`.
- `/webot/runs/interrupt` – request interruption for an active runtime run.
- `/webot/session-plan`, `/webot/session-todos`, `/webot/verifications` – plan/todo/verification CRUD.
- `/webot/voice`, `/webot/kairos`, `/webot/dream` – browser endpoints for voice, Kairos, and dream.
- `/webot/tool-policy` – read/write policy and hook definitions.
- `/webot/tool-approvals/resolve` – resolve manual approvals.

Automatic review uses complete original human requests, including human group messages with server-recorded sender and group identity. Inbox summaries and agent messages remain evidence, not authorization. Group requests authorize task work in the agent workspace; broader host operations still need specific justification. If the inference view was compacted, review recovers original requests from persisted history. Missing originals produce a normal request for confirmation, rather than a reviewer exception.
- `/proxy_webot_*` (Flask) – front-end-friendly proxies for runtime data, policies, approvals, session mode, and tool approvals.

The browser uses these APIs for runtime inspection and controls. Studio refreshes runtime status through HTTP polling.

## Related Docs

- [`webot-agent-runtime.md`](./webot-agent-runtime.md) – deep dive on runtime concepts and hooks.
- [`webot-claude-gap-analysis.md`](./webot-claude-gap-analysis.md) – matrix vs Claude Code and outstanding parity items.
- [`ports.md`](./ports.md) – route/port map.

### Frontend optional components

Studio: 上下文与审核 → 工具审核 → 命令沙盒。Mobile: 加号里的运行模式 → 命令沙盒。
命令沙盒默认关闭；可选 SRT、Linux Landlock 或自动选择。SRT 旁边显示组件状态及显式下载按钮，下载安装不会打开沙盒。自动选择只执行无副作用的探测命令，Linux SRT 不兼容时改用 Landlock；绝不把用户命令作为后端探测或退出隔离执行。Landlock 要求 Linux x86_64/aarch64、内核 ABI ≥ 6、libseccomp 和非 root 账号，不需要新容器或修改 AppArmor。x86_64 Linux 已实测；其他平台不据此声称已验证。
Auto 审核输出 `Y`（批准一次）、`N`（拒绝）或 `KEEP Y`（批准并在当前 Agent 记住），要求简短 JSON 决定，默认输出预算 4096 tokens。Y 与 KEEP Y 都必须引用原始用户请求中的有效授权来源；持续授权须有持续使用该范围的依据。旧版 `ask_user`、审核超时和格式错误均按拒绝处理，不产生等待人类确认的记录；用户后续自然语言明确同意时，下一次审核读取原始对话重新判断。摘要、工具结果和其他群成员不能代替 Agent 所有者授权。
人工审核仍提供网页横幅按钮和固定【操作授权请求】对话气泡；`Y <approval_id>`、`N <approval_id>`、`KEEP Y <approval_id>` 与按键使用同一审批记录，多个请求必须指定编号。
`request_sandbox_permission` 已从 Agent 工具中移除，`run_command` 不再接受 Agent 提权参数。启用命令沙盒的前台命令先执行；成功直接返回，失败后系统仅在能确定一个具体路径/域名时申请审核，批准后在沙盒内最多重试一次，返回最终结果。代理记录了网络权限拒绝时，即使 curl 或捕获 HTTP 异常的脚本退出码为 0，也按权限拒绝处理；上游网站自身返回的 HTTP 403 不触发提权。stderr 是不可信证据而不是授权；不明确的权限错误、超时、普通程序错误和隔离初始化失败均不自动提权。首次执行可能产生部分工作区变更，获批重试会重放整条命令。后台与交互任务仍不自动重放，失败保留在任务结果中。
提权最大范围只由服务器环境配置：`CLAWCROSS_SANDBOX_MAX_READ_PATHS`、`CLAWCROSS_SANDBOX_MAX_WRITE_PATHS` 和 `CLAWCROSS_SANDBOX_MAX_DOMAINS`，均为 JSON 字符串数组。文件上限默认空数组，禁止扩展；网络上限未设置时允许对具体公网目标送审（`["*"]`），显式空数组或无效配置禁止网络扩展。每次批准仍只授予具体目标，路径只可位于指定范围内，域名/端口精确匹配。凭据、其他用户数据、系统账户配置、设备路径、整个 home、工作区上级和 host 执行禁止提权；模型和人工审批都不能突破上限。上限参与审批绑定，重试启动前再次检查。根目录销毁、格式化设备等命令绝对拦截也不能通过审核或 Manual 解除。
Linux Landlock 默认只读程序/Python 安装路径，工作区可读写；禁止工作区外访问、全部 socket 网络、进程调试和跨隔离域信号。管理员上限内可批准一个额外读/写路径；只读授权不允许删除，文件写授权不授予父目录删除权限。可管理的 systemd 主机提供按域名/端口过滤的 HTTP/HTTPS、SOCKS 代理，禁止绕过代理直连和访问私有地址；用户可在运行设置中填写 `sandbox_allowed_domains`，网络新目标在管理员上限内审核。其他环境保持离线，网络请求不降级为直接联网。资源限制是每进程 CPU 120 秒/地址空间 2 GiB、每文件 128 MiB、每进程 256 FD；进程数量为同 UID 现有线程数加 64 的共享上限，在受控联网临时 unit 下另有任务合计内存 2 GiB、任务进程数 128 和整体超时限制，CPU/磁盘仍非总额配额。后台/交互使用相同隔离，暂不自动提权重试。详见 [命令隔离方案](command-isolation-plan.md)。
关闭沙盒时，`run_command` 在服务进程的宿主账号下执行；工作目录和关键词黑名单不提供 OS 隔离，自动审核也不能替代隔离。
Node.js/npm、Linux bwrap/socat/rg 等缺失依赖会在同一处提示；Debian/Ubuntu 提供单独的系统依赖安装按钮（需服务器管理员权限），macOS 使用已有 Homebrew，其余平台提示手动步骤；缺少隔离能力时命令拒绝执行，不回退到宿主机。
`apply-seccomp` 写入 `setgroups`、`uid_map` 或 `gid_map` 失败属于隔离初始化故障，不能通过普通路径/域名提权解决，也不能据此认定沙盒已验证安全。管理员需检查 AppArmor 的 `bwrap//&unpriv_bwrap` 策略及嵌套 user namespace 支持；不得自动关闭 seccomp、开放所有 Unix socket 或切换到宿主执行。
管理员明确批准并安装 root 所有的 `/usr/local/libexec/clawcross/bwrap` 后，ClawCross 可通过 SRT 的 `bwrapPath` 使用专用 AppArmor 配置；二进制及其所有上级目录必须由 root 持有且不可由普通用户修改。程序不会自行安装该配置或修改全局 sysctl。
新建 Agent 可以选择 ACP 连接方式；表单内提供 acpx 显式安装按钮。运行编号可留空自动生成，中文显示名不需要手填编号。

ACP 运行状态轮询仅检查本机任务锁，不启动外部适配器；会话追踪使用 `sessions list --local`，只显示登录用户已注册 Agent 的会话。查询和关闭接口需要认证，关闭操作验证会话所有权。外部 Agent 的 `model` 设置通过 acpx `--model` 传递，只影响该 Agent；留空时使用外部程序默认配置。初始化使用 Agent 的超时设置，保留 acpx 默认适配器下载行为。压缩后的用量估算按摘要版本与 API 用量基准复用，避免页面轮询反复读取并分词整个历史。

### 外部 Agent 设置与工具通道

统一使用 acpx，Codex 的 ACP 适配器连接 Codex App Server；不再额外解析原生 CLI 文本。`GET /v1/agents/{id}/capabilities` 从该 Agent 的本地 acpx 记录读取实际配置选项，不启动适配器。Studio 加号的运行设置、Agent 详情及手机设置支持原生模型、模式、思考强度和连接时限。Codex 的 `reasoning_effort` 与 Claude 的 `effort` 分别使用适配器返回的值；通过 `PATCH /v1/agents/{id}/acp-settings` 独立保存，下一轮只应用变化的设置。acpx 0.19 持久会话使用 `set`，`--config-option` 仅用于一次性 `exec`。

ClawCross MCP 工具默认启用，可按 Agent 关闭。启用时每个 Agent 使用独立的凭证及 MCP 配置，连接器只暴露 `tool_search` 和 `tool_call`；工具搜索返回准确参数。服务端验证当前用户、Agent、活动调用、工具名单、每轮模式，并通过 WeBot 的工具执行节点执行命令规则和审核。身份参数不能由外部 Agent 覆盖。acpx 权限策略仅将带 ClawCross 完整命名空间的两个 MCP 包装器委托给后端审核，不对原生 shell 自动放行；否则 Claude 的只读批准会拒绝通用 MCP 包装器。原生 CLI 工具继续使用自身的沙盒与审批；ClawCross 的自动审核不接管原生 shell。群聊通过 MCP 发送时使用相同的身份与群成员校验；未启用时提供已安装 Python 的 CLI 命令，避免 `uv run` 因缓存写入触发额外审核。

ACP 工具事件在调用期间通过 gateway SSE 转发；手机端通过有用户认证的 `/v1/agents/{id}/events?after=...` 读取当前群的最近活动，保留最多 256 个事件。失败工具显示失败。工具完成但没有普通文本属于合法结束，JSON-RPC 请求、通知、用量和原始提示词不会回退为正文；旧审计记录仅在展示时过滤协议日志，不修改原始记录。

acpx 0.19 的 MCP 配置固定在队列连接生命周期内。更换连接器时，仅在收到 `QUEUE_MCP_CONFIG_CONFLICT` 后调用该安装版本的 lease 校验与传输清理函数，保留未关闭的 acpx 记录和原生 session ID，再连接原会话；不执行 `sessions close`。此兼容层依赖 acpx 导出的 `terminateQueueOwnerForSession`，未来版本缺少该函数时明确报错并保留原会话，不隐式重置。
组件安装只接受登录用户的同源操作和固定白名单，不在启动或打开表单时自动下载。

### Context usage after compaction

API 实测用量绑定到该次调用使用的压缩视图。摘要提交后，状态接口立即按新摘要和保留原文重算当前占用，
沿用上次实测的系统提示词/工具定义分摊，并用上次 API 与本地计数的比例校准历史。显示明确标为估算，
旧输出不重复计入，旧缓存命中量清零。重启后仍根据已存 API 测量及最新压缩视图计算；下一次调用重新采用 API 实测值。

模型、系统提示词、工具定义和原有消息前缀一致时，新增纯用户输入或工具结果可用
`当前 input - 上次 input - 上次 output` 归因；动态块变化、多种消息混合、推理输出等不能单独归因时，
回退到本地分词分摊。差分分项仍是推断：输出转为输入的序列化成本可能不同，多条新增消息只有整体增量可知。

Studio 加号二级菜单仅保留当前对话运行选项：沙盒与审核、上下文与压缩、模式、工具、人设、工作流、对话面板与附件。全局设置 → 外部 Agent 提供 acpx 安装；机器人集成中提供 WeClaw / NoneBot / QQ / Telegram 安装；公网访问设置中提供 cloudflared 安装。群聊加入操作位于消息中心。安装不自动开启渠道、Agent 或公网通道。手动摘要提交后，用量投影失败不影响任务完成；终态清除前端残留的压缩忙状态。


### 内外 Agent 的提示词与动态状态

WeBot 与外部 Agent 共享基础对话规则、Team/Skill 目录、模式说明和群元信息校验。
WeBot 每次请求读取并重组系统提示词；有记忆的外部会话首次收到身份，身份成分也纳入同一动态块交付快照，后续只接收发生变化的块，reset 清空整个快照并重新发送完整状态。
ACP 启用 ClawCross MCP 时使用 MCP 版技能目录，工作流规则按需通过工具获取；关闭时保留 CLI 入口和兼容说明。
ClawCross MCP 搜索和调用的结果可附带 `runtime_context`，只包含相对本轮已交付快照的变化，成功结束后记在 Agent 表上。群成员变化可在同轮工具调用后生效；来源群以最新成员资格校验，正文仍由当前输入或 inbox 提供，不注入群元信息。
此机制不改写 Codex/Claude 的原生工具结果，也不接管它们的历史压缩或内部循环。外部 Agent 仍使用原生持久会话与审批，MCP 走 ClawCross 的工具执行和审核节点。

外部提示词交付使用统一状态机（`prompt_context_version=2`）：`identity_*` 与群聊、Team、技能、模式一起存入 `dynamic_context`，首次全量、后续增量、成功才提交、reset 全清。旧版 identity_sections 可迁入快照，不再输出独立版本号或系统提示词补丁。原生历史中的旧消息不会被物理删除。

新 ACP 会话复用已知选项目录时，设置页面显示 ClawCross 的明确初始选择，首轮调用使用同一份选项；不复用其他会话的 currentValue。已建立会话仍以自身返回的选项和该 Agent 已保存的覆盖值为准。

Agent 设置页的“测试连接”显式调用 `/v1/agents/{id}/test-connection`，仅初始化或恢复该用户的 ACP 会话并刷新配置；不发 session/prompt、不生成问答、不提交动态块快照。忙碌 Agent 返回 409，连接失败显示错误。

运行模式在 CLI、Studio、Mobile 和 Agent API 中使用相同值：`chat` 不开放工具；`readonly` 只开放只读工具；`manual` 开放全部工具、允许的操作直接执行，需要批准的操作交给人类；`auto` 开放全部工具，需要批准的操作交给 AI；`bypass` 开放全部工具并跳过审核。Manual 的批准按钮与当前对话中的 Y/N/KEEP Y 使用同一审批单，待批准或拒绝时不执行。显式禁止规则、命令硬拦截和沙盒限制在所有模式下生效。外部 Agent 的 ClawCross MCP 工具遵循该模式；原生 CLI 工具继续使用适配器的权限机制。

自动审核通过供应商原生 JSON Schema 输出接口返回 `Y` / `N` / `KEEP Y`，不依赖仅写在提示词中的 JSON 格式要求，也不创建用于回复的工具。DeepSeek 使用 Responses API 的 `text.format`；OpenAI 兼容模型使用 `response_format.json_schema`；Anthropic / Gemini 使用原生结构化输出。后端仍验证字段、原始用户授权来源及管理员上限。空响应、输出截断、Schema 不合规或接口失败均拒绝执行，不自动切换到人工弹窗；用户后续明确授权后可重新审核。

`web_search` 与 `web_fetch` 在联网前走统一审核，即使默认工具策略为 allow。Auto 交模型、Manual 交人类，Bypass 跳过确认但保留显式 deny 与现有 URL 限制。搜索审核完整查询内容，`fetch_top` 抓取的每个结果 URL 另走 `web_fetch` 审核。用户及 Agent 身份由 runtime 强制注入，MCP 服务消费同一份短期、单次、完整参数执行许可，避免重复审核；缺少身份的直接调用不联网。此规则适用于 ClawCross 工具，外部 CLI 原生 Web 工具仍由其自身权限策略管理。

沙盒单次 `Y` 或访问成功不会自动保存白名单。模型与人工 `KEEP Y` 使用相同保存机制：沙盒重试记录具体域名/IP 和端口，或具体路径及读/写类型，存于当前 Agent 的 `approval.sandbox_grants`；其他工具仅记住当前 Agent 的完整操作参数。后续命令在启动前重新验证每项沙盒授权，将有效权限合入隔离配置；读权限不变为写权限，不继承给其他 Agent，不改变管理员上限。管理员收紧上限、路径变为其他符号链接目标或目标被删除时，旧授权不再使用。权限在服务重启后保留，可在运行设置的“已记住的沙盒权限”中移除并保存，或恢复 Agent 继承设置。`sandbox_allowed_domains` 仍是用户显式配置的网站许可。命令沙盒的联网许可不替代 Web 工具对具体查询和 URL 的审核。

提权过程由系统完成：原命令执行 → 检测可确定的权限拒绝 → 检查管理员上限 → 审核 Y/N/KEEP Y → 在新建的沙盒中带上该项权限重试一次。审核模型只返回决定，不能修改宿主权限、关闭沙盒或生成超出上限的授权。审计保存原始授权来源、决定和保存结果；执行许可使用保存后的策略绑定并只消费一次。

人工审核按钮和输入框中的精确 `Y` / `N` / `KEEP Y` 共用 `/webot/tool-approvals/resolve`。这些输入作为审核操作处理，不作为普通消息发给 Agent 或群成员；多项待审核时必须填写审批编号。后端按当前用户和 Agent 验证审批归属、有效期和策略，原子更新一次后排队恢复。内部 Agent 直接重试保存的工具和参数，再继续原任务；外部 Agent 通过 gateway 恢复原生会话。群聊回复通道与内部工具范围随审核保存，批准不扩大其他操作的权限。拒绝也恢复说明结果，不执行被拒操作。重复点击、已用授权、过期及策略改变都不会重复执行。

Studio 和 Mobile 的审核按钮放在对话授权气泡内；按钮由后端当前待审记录产生，不由工具/Agent 文本生成。点击等同于输入对应的 Y/N/KEEP Y，显示用户的确认信息并恢复执行，上方审核栏不再重复展示。Agent 中心的审核管理入口仍可处理其他 Agent 的待审操作。
