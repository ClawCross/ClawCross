# 在对话里管理团队

## 从目标开始

直接告诉当前 Agent「帮我建一个写作团队」「把这两个 Agent 放到项目里」或「每周五让团队总结进展」。
团队创建、成员配置、人设、工作流和定时任务由当前 Agent 使用管理工具完成，不再有独立的 Team Creator 页面或三阶段生成任务。
移动端「创造 → 组建协作团队」和 Studio 欢迎页只填写可编辑的消息草稿；用户发送后才调用 Agent。
旧 `/creator` 链接跳转到同一个对话入口。

## 工具与权限

先用 `tool_search` 查询工具的完整参数，不确定参数时先搜索，不自行猜测。

- `manage_team`：列出、查询、新建、修改、改名和删除自己的团队；管理成员、人设、工作流与团队定时任务。
- `manage_group`：管理聊天群的设置和成员。群聊是通讯方式，团队是协作与资源组织方式，二者可独立存在。
- `get_workflow_rules`：编写工作流前阅读当前 YAML 或 Python 格式规则。
- `get_clawcross_help`：读取本地功能说明；`teams` 是本篇，`workflows` 是工作流开发，`workspaces` 是工作区，`apps` 是小程序。
- 私密设置用 `request_configuration` 表单，不要求用户把 API Key 发到聊天里。

这些管理工具是可选能力。当前 Agent 没有工具时，请用户在工具配置中启用，或通过现有界面操作；不要自行扩大工具范围、切换模式或提升其他 Agent 权限。
只读查询与修改遵循现有审核策略，严格沙盒不会因为创建团队而失效。

## 创建与配置流程

1. 确认目标、交付物、需要的成员与预算，优先复用用户已选定的 Agent。
2. 查询已有团队，避免重复创建。`manage_team(action="create", team="项目名", data={"description":"目标与介绍"})` 创建团队。
3. 用 `add_member` 加入已有 Agent，或用 `create_agent` 新建成员。角色、工具和模式按任务需要选择，不默认给全部权限。
4. `save_persona` 保存团队人设；`update_member` 调整角色；`update_agent` 修改成员的非敏感配置。
5. 需要自动流程时先读规则，再 `save_workflow`。保存工作流不会自动开始运行。
6. 用 `get`、`list_workflows`、`list_alarms` 检查结果，并向用户说明实际完成了什么。

删除、替换成员和权限变更必须符合用户请求。删除团队与删除底层 Agent 是不同操作，先读取工具返回的删除行为和范围。

## 工作流与定时任务

`manage_team` 的 `list_workflows/get_workflow/save_workflow/delete_workflow` 操作管理指定团队的 YAML 或 Python 工作流。
`data` 使用 `name`、`kind`、`content` 等字段；具体结构以工具搜索返回为准。
不要把旧版 Creator 的自动 DAG 当作当前工作流规范，也不要因为保存成功就宣称已经跑通。
执行使用现有 OASIS 工具；已有运行异常时先查看状态，不擅自重跑。

团队时钟用 `list_alarms/create_alarm/update_alarm/delete_alarm`；创建目标必须是团队成员。
创建闹钟不代表已经执行。自己的提醒优先使用自身定时工具；给团队外其他 Agent 安排任务用单独的高权限管理工具。

## 工作区、技能与导入导出

团队工作区在运行时按团队归属计算，是 Agent 多个可选工作区来源之一。
用户视图是所有 Agent 的汇总，不是自动把全部 Agent 加进一个真实默认团队。
技能必须放在该 Agent 能访问的工作区来源中；详见 `get_clawcross_help(topic="workspaces")`。

现有团队模板、ZIP/Hub 导入和导出继续可用。导入创建一个团队，遇到同名冲突应明确选择新名称或按用户要求替换，不合并成未知状态。
人设/技能导入保留为独立能力，移除 Creator 页面不会删除已有团队、Agent、聊天历史或技能文件。

更完整的文件结构和 CLI 操作见 [build_team.md](build_team.md)、[team-anatomy.md](team-anatomy.md)；工作流规范见 [create_workflow.md](create_workflow.md)。
