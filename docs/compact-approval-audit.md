# Compact 与工具审核

已实现跨 provider 的用户/会话配置、分块摘要和独立自动代审。压缩由程序选择边界和预算，再由 LLM 总结；模型不可用时退回机械摘录。原始消息保留在 checkpoint，后续请求使用“摘要 + 未折叠原文”。没有接入 OpenAI Responses 的原生、不透明 compaction 状态项。

## 使用入口

桌面侧栏、聊天上下文详情和 Agent Center 中的“上下文与审核”打开设置。可设置“我的默认设置”或当前会话覆盖，保存后下一次调用生效。会话仅保存修改的字段，其他字段继承用户默认值；“恢复继承设置”删除该范围的覆盖。

设置保存在 `USER_FILES_DIR/{user_id}/webot_runtime_settings.json`，保存前验证全部预算和现有会话覆盖，使用文件锁及原子替换避免并发更新丢失。会话覆盖优先于用户设置，用户设置优先于环境默认。历史预算填 0 时，继续使用模型窗口和 `WEBOT_CONTEXT_TOKEN_BUDGET` / `WEBOT_SUBAGENT_CONTEXT_TOKEN_BUDGET` 的现有推导规则。

| 设置 | 默认值与含义 |
| --- | --- |
| `context.context_window_tokens` | 1,000,000；默认 1M，手动值直接控制运行预算，不再被模型名称推断覆盖；占用条显示当前输入视图，归档原文不重复计入 |
| `context.auto_compact` | true；只控制自动触发，手动压缩仍可用，已保存摘要仍被复用 |
| `context.history_tokens` | 0；历史预算，运行时扣除系统提示词、工具 schema、动态状态和输出预留，并限制在模型可用窗口内 |
| `context.trigger_tokens` | 0；自动使用历史预算 × 触发比例，默认 0.90；同时检查上一轮 API 用量 |
| `context.target_tokens` | 0；自动使用历史预算 × 目标比例，默认 0.55；按“摘要 + 原文尾部”选择边界 |
| `context.preserve_recent_turns` | 4；保留完整近期对话轮次，工具调用与结果不拆开 |
| `context.summary_tokens` | 2000；摘要估算 token 上限，也受目标预算及原有字符上限约束 |
| `context.summarizer_input_tokens` | 8000；分块摘要输入预算，扣除先前摘要和指示，限制在摘要模型窗口内 |
| `context.summarizer_model` | 空；使用默认模型，兼容 `WEBOT_SUMMARIZER_MODEL` |
| `context.preserve_instructions` | 空；额外保留要求，默认摘要已保留目标、限制、决定、证据、待办和恢复位置 |
| `approval.mode` | `auto`；交流 chat、只读 readonly、全工具 bypass、代审 auto |
| `approval.approvals_reviewer` | 旧 API 兼容字段；审核者由模式决定：Auto 用独立模型，其余非 Bypass 模式由用户审核 |
| `approval.reviewer_model` | 空；使用默认模型 |
| `approval.reviewer_policy` | 空；补充审核要求，不能解除明确禁止规则 |
| `approval.reviewer_timeout_seconds` | 30；独立模型审核超时后回到人工审核 |
| `approval.command_sandbox` | 默认 `off`，命令在宿主机运行；设为 `srt` 时，所有允许执行命令的模式均使用 SRT，支持前台、后台、交互。旧 `container` 配置迁移到 `srt` |

`trigger_tokens` 必须大于 `target_tokens`，且不能超过显式历史预算；摘要和保留指示必须在摘要输入预算内。部分设置更新合并到已有覆盖，非法更新不会修改文件。用户默认修改若与现有会话覆盖冲突，也会拒绝保存并显示原因。

近期原文可能已超过目标预算：程序保留配置指定的轮次，在压缩记录中标记 `target_met=false`，不会为满足目标静默丢弃这些轮次。token 数为本地估算，不能替代 provider 返回的实际用量。

## 压缩实现与可观察性

- 先选择满足目标的最早完整轮次边界，尽可能多保留原文；自动触发保留最少新增消息防抖，手动跳过阈值。
- 分块送入完整转录，包括独立存储的 `tool_calls` 参数及结果 ID；超长单个参数也切分处理，不仅保留前缀。
- 滚动合并先前摘要，每次限制摘要长度；中文 token 使用与历史一致的估算方法。
- 空输出、模型创建/调用失败或输入窗口不足时使用机械摘录。机械摘录会丢失语义细节，原文仍可回放。
- 保存前执行版本校验，同进程同会话串行，跨进程旧版本不能覆盖新版本，较短旧快照不能覆盖较新记录。保存失败保持原视图，手动接口返回失败。
- 压缩记录包括触发和目标预算、前后估算 token、耗时、模型调用/回退次数、原文索引和 `target_met`。设置界面显示最近一次压缩的前后估算和耗时。

## 审核流程

工具策略的 allow / deny / manual 与审核者设置分开。Auto 只把审核者从用户换成独立模型；allow 直接执行，deny 始终拒绝，manual 才进入审核。沙盒提权和文件工具访问工作区外目标也要求单次批准。Bypass 跳过批准但保留明确禁止规则。未启用沙盒时，命令的既有高风险检查仍进入审核。

审批记录查询保留原有的流程豁免，避免禁止策略让 Agent 无法查看拒绝原因。

Auto 模式遇到需要批准的请求时，调用独立的结构化审核模型，不提供行动工具。输入包含具体工具和全部实际参数、原始用户请求、相关工具证据及策略，输出 `approve` / `deny` / `ask_user`、风险、理由和用户请求来源 ID。

- 原始用户输入由运行时标记来源并赋予稳定 ID。系统触发、助手消息、摘要、工具调用和输出作为不可信证据，不能充当用户授权。即使最近一百条都是工具活动，也另外恢复最近的原始用户请求。
- 审核者批准必须引用有效的原始用户请求 ID。旧消息没有可信来源、授权原文过长、完整材料超过窗口、结构无效、超时或模型失败，都回到人工审核。
- 代审不能覆盖明确 deny、plan/review 限制、注入身份或命令硬禁止；也不写入永久授权。只有人工“批准并记住”可记住完整参数，参数改变重新审核。
- 批准绑定用户、会话、实际工具、完整参数、用户请求、策略/审核设置、模式及子 Agent 工作区配置。批准后相关状态改变则不执行。
- 批准通过条件 UPDATE 单次消费。Agent 与 MCP 之间使用短期、精确参数的单次执行凭证，避免高风险命令重复询问；批量等待全部结束后重新核验，再发放凭证。
- 连续三次自动拒绝停止当前轮执行并要求新的用户指示；批准或普通允许操作重置连续拒绝计数。取消等待和超时关闭 pending 请求。
- 审核记录包含模型、结构化决策、理由、绑定信息；Agent 工具执行后记录返回/异常状态。桌面和移动端待审批卡片可查看完整参数及代审理由，REST 可读取历史记录。

## REST

FastAPI 入口（现有用户认证或内部 token）：

- `GET /webot/runtime-settings?user_id=...&session_id=...`：有效设置、用户/会话覆盖和上次压缩信息。
- `POST /webot/runtime-settings`：`user_id`、可选 `session_id`、部分 `settings`、可选 `reset`。
- `GET /webot/tool-approvals`：沿用审批查询，增加 `args`、`resolution_reason`、`review`。

前端通过 `GET/POST /proxy_webot_runtime_settings` 代理，用户身份取已登录会话，不能通过 JSON 中的 `user_id` 替其他用户修改。

会话级更新示例：

```json
{
  "user_id": "alice",
  "session_id": "session-1",
  "settings": {
    "context": {
      "history_tokens": 24000,
      "trigger_tokens": 20000,
      "target_tokens": 12000,
      "preserve_recent_turns": 3,
      "summary_tokens": 1500,
      "preserve_instructions": "保留用户限制、修改过的文件和测试结果"
    },
    "approval": {
      "mode": "auto"
    }
  }
}
```

## 四种运行模式与占用条

交流模式在解码绑定和执行端都不提供工具。只读模式以明确的读取工具集合过滤，拒绝写文件、发送消息、启动子 Agent、执行命令及终端输入；后台输出仍可读。Bypass 跳过人工和模型确认，但保留显式 deny 和关键命令硬拦截。Auto 仅代审工具策略中标记 manual 的调用，以及沙盒提权、工作区外文件访问；来源不足、模型失败或超时时仍转交人工。模式随用户默认/会话覆盖保存，桌面、手机和 CLI 使用同一组名称；旧 manual/plan/yolo 值兼容。

Auto 代审目前只接入内置 Agent；外部 ACP Agent 不具备这个审核通道，Auto 使用 approve-reads + deny，拒绝写操作，避免静默放行。

命令沙盒与审核模式独立，默认关闭。`approval.command_sandbox=srt` 时，前台、后台、交互的 shell 与 Python 命令使用当前机器的解释器和虚拟环境，在原生 SRT 沙盒内执行。默认策略禁止网络及 Unix socket，只允许写会话工作区与临时目录，阻止读取用户主目录中工作区以外的数据及常见凭据。系统目录仍可读，以便程序加载依赖。后台 runner 持有策略文件至任务结束再清理；交互输入仍逐条经过工具策略和模式限制。沙盒拒绝后不会自动重跑可能已有副作用的命令；Agent 可用同一 `run_command` 明确申请 `read_path`（一个已存在的工作区外路径）、`write_path`（一个已存在的工作区外路径）、`network`（一个域名）或 `host`（本次命令跳出沙盒），附上失败原因。每次提权绑定完整命令和参数；Auto 交独立模型，其他非 Bypass 执行模式交用户，Bypass 依其定义跳过确认。明确 deny 与命令硬拦截不能提权覆盖。SRT 或依赖不可用时默认命令拒绝执行，不自动回退宿主机。需要 SRT 0.0.77 或更新版本；Linux 需要 `bwrap`、`socat`、`rg`，macOS 需要 `rg`，Windows 支持为 alpha。旧 `container` 设置会安全迁移到 `srt`。

普通 `list_files` / `read_file` / `write_file` / `delete_file` 在解析符号链接后若目标超出当前会话工作区，也需要单次批准；文件工具既有白名单、黑名单及 manual 规则照常优先生效。内置 memory/Skill 条目不按文件路径触发这项审批。文件工具本身不在 SRT 命令沙盒中。

本机使用临时安装的 SRT 0.0.77 和 `socat` 做过真实启动探针：策略被读取，但 Ubuntu 的 `kernel.apparmor_restrict_unprivileged_userns=1` 阻止了 SRT 的嵌套 user namespace，命令以 `apply-seccomp: write /proc/self/setgroups ... Permission denied` 退出，未执行脚本或写入工作区外。没有为了测试修改系统级 AppArmor/sysctl 配置。启用前需要管理员按 [SRT 官方 Linux 指引](https://github.com/anthropics/sandbox-runtime#platform-specific-dependencies) 配置允许的 user namespace；不能通过关闭沙盒回退来掩盖此错误。

上下文详情与设置面板显示分段长条，区分对话历史、工具结果、压缩摘要、提示词/工具定义和剩余。API 总数为真值，分项仍为本地估算；没有 API 用量时仅估算实际压缩视图。默认窗口 1M；手动值控制窗口与默认历史预算，历史还扣除提示词、工具定义和输出预留。它不会扩大服务商实际容量，需要填写服务商支持的窗口。会话设置优先于用户默认设置。

旧版本按模型名把 deepseek-flash 推断成 64K，覆盖了前端较大的手动窗口；历史默认预算也沿用了推断。两处已修正，历史查询和状态轮询会按最新设置重算分母、百分比和剩余，保留 API 实测用量。

工具目录按大类折叠展示具体工具，分类元信息不影响每个工具原有授权。用量工具改为 usage_status，旧名称兼容。

## 已修复的旧机制问题

摘要遗漏工具调用、真实 API 用量掩盖小历史预算、空摘要丢内容、落盘失败仍报成功、同步摘要阻塞服务、小字符上限失效；审批重复消费、过期/取消请求仍能批准、交互输入漏内容规则、参数被 hook 改写后未重检、remember 扩大授权、损坏策略退回放行，以及 review 模式可以向终端输入。

## 验证范围

Python 回归使用临时数据库和模拟模型，覆盖继承/重置/并发保存、认证、完整轮次边界、中文摘要上限、长参数分块、版本冲突、代审决策及人工回退、取消、单次消费、策略和原始请求变更、批量审核与 MCP 衔接。Playwright 使用模拟接口检查保存范围、继承重置、错误显示和 HTML 转义。

最终验证：完整 Python 测试 735 项、289 个子测试通过，另有一条 Google SDK 依赖弃用警告；5 项设置浏览器测试及 1 项移动消息中心回归通过。实际运行页面另验证了 9 类 / 51 个工具、分类折叠和逐项开关计数。本机浏览器验证使用已有 Chromium。

额外的 Studio 冒烟检查中，OASIS Town 画布测试失败；独立重跑仍在等待 `#oasis-town-runtime-smoke-host canvas` 时超时。此项未修复，不能将整套 Studio 浏览器测试标为通过。并发运行不同 Playwright 命令还会共用测试服务与输出目录，应顺序运行并使用各自的输出目录。

尚未使用真实 provider 验证摘要质量、审核质量、结构化响应支持及端到端模型调用；不支持结构化响应的 provider 会回到人工审核。离线测试不能证明没有其他 bug。现有工具参数 strict 的真实模型验证也仍未执行。

## Codex 对照来源

- [Configuration Reference](https://learn.chatgpt.com/docs/config-file/config-reference)：自动压缩和审核者配置。
- [Auto-review](https://learn.chatgpt.com/docs/sandboxing/auto-review)：独立审核具体动作，代审保留权限边界。
- [OpenAI Compaction](https://developers.openai.com/api/docs/guides/compaction)：Responses 的原生不透明状态项；本实现使用可移植摘要机制，没有调用该接口。
