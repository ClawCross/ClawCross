# 新建 Agent 模板与管理工具

Studio 与 Mobile 的新建 Agent 使用同一份后端模板。选择模板或切换平台只修改待填配置，点击创建才登记 Agent，不会自动安装外部程序。

Mobile 通讯录的新建入口先提供聊天伙伴、专家顾问、讨论主持三种群聊用途。它们填入角色人设，手动人设或选定的人设池优先；权限模板仍在高级设置中。WeBot 初始使用群聊伙伴模板，外部 CLI 初始使用兼容的私人助手模板，原生 CLI 权限仍由其自身控制。修改高级模板后，平台切换保留该选择。

| 模板 | 模式 | 沙盒 | 用途 |
|---|---|---|---|
| 纯聊天 | chat | strict | 没有工具，文字问答；群和私聊回复由系统转发 |
| 群聊伙伴 | auto | strict | 加入群、收发群消息、读写伴生工作区；不启用跨会话指挥和管理工具 |
| 私人助手 | auto | standard | 文件、命令、搜索、自己的闹钟与子 Agent；提权经过审核 |
| 管理员 | manual | standard | 明确开启所有内置工具；需要审核的操作交给用户 |

模板分别保存工具、模式、沙盒等级和工作区选择；它们是初始配置，创建后可逐个 Agent 调整。严格模板默认只有伴生工作区，不带用户共享、CLI、Team 工作区。组件未安装时保持拒绝执行，不自动下载。

纯聊天与群聊伙伴暂时只允许 WeBot：外部 CLI 的原生工具权限无法由 ClawCross 的沙盒完整保证。其他模板可用于外部 Agent，ClawCross 工具约束适用于桥接工具，原生工具仍由对应 CLI 控制。

## 管理与参与分开

以下管理工具沿用 Agent 的普通工具表：`tools=null` 表示全部工具，`tools=[]` 表示无工具，具体列表表示选择这些工具。私人助手与群聊模板使用明确的列表来排除管理能力。默认需要审核；auto 使用 AI 审核，manual 使用人工审核，bypass 跳过确认但仍检查工具表、模式和对象归属。

- `manage_team`：列出、查看、新建、改名、删除真实 Team；加入/移除成员、设置角色与负责人；维护 Team 人设池；新建成员和修改成员的非敏感配置；列出、读取、保存、删除该 Team 的 YAML/Python 工作流；管理该 Team 的闹钟。调用者不必加入 Team。删除 Team 保留 Agent。虚拟“用户空间”不属于可编辑 Team。
- `manage_group`：以当前用户身份管理自己拥有的群，包括改名、成员、主要 Agent、删除与暂停/恢复外部联网。调用者不必加入群。不能管理其他用户拥有的群。
- `manage_agent_alarms`：给本用户的其他 Agent 创建、查看、删除闹钟。
- `send_to_session`：跨会话投递指令；私人助手与群聊模板不默认启用。

低权限的 `join_group/leave_group` 只处理自己的参与状态，`add_alarm/list_alarms/delete_alarm` 只处理当前 Agent 的闹钟。工具名与管理能力不会混用。

管理工具按用户身份校验归属，不能借 `data` 修改用户 ID 或指定任意文件路径。API Key 和连接秘密使用用户私密表单填写，不通过这些工具交给模型。

Team 工作流使用已有 `teams/<team>/oasis/yaml` 与 `oasis/python` 目录。`save_workflow` 保存配置，运行仍使用 `start_new_oasis`。Team 闹钟创建或换目标时必须指定当前成员，`team` 字段由工具固定；查询、修改和删除只涉及当前用户、选定 Team 的闹钟，修改保留 task_id。`manage_agent_alarms` 保留对其他自有 Agent 的管理能力。

## 查找会话

- `list_sessions(query=...)`：从 Agent 表按名字、ID、标题或 Team 查找，包括没有对话历史的 Agent 和外部 Agent。
- `search_sessions`：检索已有会话内容。
- `get_session_details(target_session=...)`：查看该用户 Agent 的模式、工具、沙盒和工作区；可选择近期 ClawCross 历史，禁止返回连接秘密，不启动外部 CLI。

这些工具沿用 `tool_search → tool_call` 的按需查询机制。旧的 `call_llm_api` 不再作为 Agent 工具发布，工作流内部的模型调用保持原实现。

每个 Agent 的自定义 MCP 连接接口方案见 [agent-mcp-interface-design.md](./agent-mcp-interface-design.md)。
