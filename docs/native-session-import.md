# 登记 ACP 原生会话与查看历史

Studio 与 Mobile 通讯录都提供“登记已有外部会话”。平台列表来自 acpx，包含已知 ACP 平台和主机配置的自定义平台；选择平台不会创建 Agent 或发送消息，点击读取列表也只查询会话元数据。

## 适配器能力

- `session/list`：列出可以登记的原生会话；能力不存在时显示适配器错误。
- `session/load`：加载已有会话，通过 `session/update` 回放完整历史。登记时尝试读取该回放，不调用 `session/prompt` 或 `session/new`。
- `session/resume`：继续原生会话，不保证返回旧历史。找到一个会话并不等于其适配器支持历史回放。

历史读取沿用 acpx 的适配器注册表，使用已安装包或 npm 已有缓存，不运行安装命令。没有适配器、认证失败、不支持回放或读取超时会明确显示原因；已经登记的 Agent 保留，可在通讯录历史页面或登记列表中重试读取。历史读取不连接 ClawCross MCP、拒绝客户端文件和终端请求、不打断忙碌中的 Agent。原生适配器本身是主机已安装的受信任程序。

参考：[ACP 会话初始化、加载和恢复](https://agentclientprotocol.com/protocol/v1/session-setup)、[会话列表](https://agentclientprotocol.com/protocol/v1/session-list)、[acpx](https://github.com/openclaw/acpx)。

## 本地保存与前端展示

- 原生回放保存到该 Agent 的现有 external_agent_history SQLite 数据库，旧消息排在接入后记录之前。
- 文本分片、人类输入、思考、工具输入与结果都保留，原始内容块和工具更新保存在记录元数据中。未由适配器提供的内容无法凭空恢复。
- 每个原生会话只导入一次；重复登记返回已有 Agent。已有本地记录时，识别首条 ClawCross 原始输入作为导入边界，避免重复保存同一段受管对话。
- 接入后的新问答与工具记录继续追加到同一个数据库，旧历史用于前端浏览，不会重放给外部模型。
- Studio 和 Mobile 每次读取一个窗口，提供“加载更早记录”。游标使用记录 ID，聊天继续产生消息不会改变旧分页边界；不存在原来的前 5,000 条读取上限。
- 前端仍保留独立的 acpx 捕获文本来源，该来源可能不含接入前历史或完整工具轨迹。

单次原生读取最多 45 秒、64 MiB。超过上限或读取中断不提交部分回放，用户仍可继续使用已登记 Agent。

## API

- `GET /v1/agents/native-sessions?platform=<platform>&cursor=<cursor>`：返回带短期选择凭证的目录。
- `POST /v1/agents/native-sessions`，参数 `ticket`、`name`：登记并尝试保存历史，返回 `native_history` 状态。
- `POST /v1/agents/<id>/native-history`：为已登记 Agent 重试读取；已完成的导入直接返回保存状态。
- `GET /v1/agents/<id>/history?limit=200&before=<record-id>`：读取较早窗口，返回 `messages`、`has_more`、`next_before` 与原生读取状态。

选择凭证和登记状态绑定用户；其他用户不能使用该凭证登记或读取该 Agent 的历史。默认允许已登录用户使用目录入口；主机可以通过 `CLAWCROSS_NATIVE_SESSION_USERS` 收紧范围。

## 新建 Agent 与工作区

通讯录新建 Agent 的名称和平台为主要选项，人设、工具名单和可选运行时编号位于高级设置。ACP 默认使用 ClawCross 工具；工具名单同时适用于 Studio 和 Mobile 的创建请求。填写人设文本可直接使用，也可从已有的人设库选择；切换平台不会自动创建或清空表单。

本次不迁移工作区。普通 Agent 未指定目录时使用该用户的共享工作区 `workspace/users/<user>`，可为 Agent 指定已有的自定义目录；严格模式使用独立目录。用户 Skills 已存放在共享工作区 `skills/`，Team Skills 在其下 `teams/<team>/skills/`。Team 配置、工作流与这些可写工作区目前仍是不同路径。后续“Team = 项目”、默认用户项目、独立 Agent 工作区的结构调整需要统一处理 Skills 可见性和工具访问范围。
