# Agent 工作区

一个工作区是一个文件夹；一个 Agent 可以启用多个文件夹。工作区管理的是文件工具、ClawCross 命令沙盒和 Skills 的目录范围，工作流仍由服务进程运行，是否调用由用户配置的工具名单决定。

## 保存什么

Agent 的 `settings.workspaces` 只保存自动来源的开关和用户添加的目录：

```json
{
  "companion": true,
  "user_shared": false,
  "cli": true,
  "teams": true,
  "paths": ["/absolute/custom/project"]
}
```

`paths` 最多 32 个，必须是已经存在的绝对目录。自动来源不保存解析后的路径；CLI 启动目录不写入 Agent 配置或数据库。GET `/v1/agents/<id>/workspaces` 返回运行时生成的目录与当前 cwd。

## 自动来源

所有路径相对于当前实例的 `WORKSPACE_DIR` 生成：

| 来源 | 运行时目录 | 生效条件 |
| --- | --- | --- |
| 伴生 | `agents/<user>/<hash(agent_id)>` | `companion` 开启 |
| 用户共享 | `users/<user>` | `user_shared` 开启 |
| CLI | CLI 本次调用的启动目录 | `cli` 开启且服务取得运行上下文 |
| Team | `teams/<user>/<team>/workspace` | `teams` 开启且 Agent 属于该 Team |
| 自定义 | 用户填写的已有目录 | 列入 `paths` |

CLI 每次调用带入当前目录，服务只在内存保留这个来源。MCP 工具进程通过内部认证接口读取同一运行状态；服务重启后需要再次从 CLI 调用取得。导入原生 ACP 会话时，适配器恢复所用的原生 cwd 会在连接时附加为 CLI 来源。适配器自身的会话恢复元数据不属于工作区配置。

新 Agent 默认开启伴生、CLI、Team 来源，关闭用户共享。CLI 有运行上下文时 cwd 优先使用 CLI 目录，其次是首个自定义目录，再其次是首个自动目录。命令可以指定集合内任意目录作为 cwd。新伴生目录不会嵌在用户共享目录中，开启共享也不会自动开放其他 Agent 的伴生目录。

每个用户有一个虚拟默认项目 `__default__`，成员来自该用户的完整 Agent 登记表。属于默认项目不等于开启用户共享目录；共享开关仍单独控制文件访问。默认项目不能删除、改名或由导入替换。普通 Team 加入、退出和改名会更新有效工作区；改名保留工作区文件。删除 Team 不删除可写工作区文件，退出后不再授予其自动来源。

前端将虚拟默认项目显示为固定的「用户空间」，与普通 Team 分开。它没有独立的 `members.json`，其 Skills 使用用户共享工作区的目录，列表不会重复列出同一批 Skills。

## Team 目录结构

真实 Team 的配置与可写工作区分开。以下路径按运行时目录设置解析，默认根目录为 `~/.clawcross`：

```text
data/user_files/<用户>/teams/<Team>/
├── members.json              # 成员、角色、主 Agent
├── oasis_experts.json         # 可选人设池
└── oasis/
    ├── yaml/                 # YAML 工作流
    └── python/               # Python 工作流

workspace/teams/<用户>/<Team>/workspace/
├── skills/<Skill目录>/
│   ├── SKILL.md
│   └── scripts/、references/、assets/ 等支持文件
└── 其他项目文件
```

`internal_agents.json`、`external_agents.json` 是导入导出清单，导入后成员登记到 Agent 数据库，并非独立的在线 Agent 表。定时任务由调度服务统一保存，关联用户、Agent 或 Team。虚拟「用户空间」没有上述 Team 配置实体，它的共享工作区是 `workspace/users/<用户>/`；是否允许 Agent 访问仍由 `user_shared` 开关决定。

旧 Agent 没有 `workspaces` 设置时保留旧单目录行为，已有聊天、配置和用户文件不整体迁移。首次从 CLI 调用旧 Agent 会只增加 CLI 来源开关，保留原有目录。旧子 Agent 的 worktree、custom、remote 配置仍兼容；新的委派在默认 isolated/shared 模式下从父 Agent 动态继承项目目录，isolated 使用自己的伴生目录，shared 也开放父伴生目录。继承不把自动目录路径复制进子 Agent 设置。

## Skills 和权限

各目录内的 `skills/<name>/SKILL.md` 与其支持文件可读写，脚本可通过命令工具运行。动态块只列出当前启用目录里的 Skills；Memory 文件工具使用相同可见范围，未启用的用户共享或 Team 目录不能通过 Memory 接口读取。条目接口仍使用 ID/名称，动态块会给出可用 Skill 的实际路径，方便阅读支持文件和运行脚本。

已有用户 Skills 保留在用户共享目录。旧 Team Skills 会连同支持文件迁移到对应 Team 工作区，已有目标文件优先，冲突旧文件保留。

SRT 与 Linux Landlock 共同使用解析后的全部目录作为基础读写范围。严格模式不允许审核扩大范围、不使用历史提权；普通模式继续使用现有审核与管理员上限。配置目录与后台任务控制文件始终位于工作区外，不能作为工作区选择。其他用户的框架管理目录不可被添加为自定义目录。来源或自定义目录变化会使旧审批执行凭证失效。

外部 CLI 原生工具由自己的权限策略管理；这里的命令隔离与文件范围约束 ClawCross 工具，不代表接管外部 CLI 的全部原生权限。

## 前端和 CLI

Studio 加号 → 工作区；Agent 中心 → 高级 → 工作区。手机端 Agent 详情 → 工作区，也可从运行设置进入。可以切换四种自动来源、编辑自定义目录列表，并查看解析后的目录。

CLI 的 `webot` 和 `agents create/ask` 自动传递当前启动目录。修改目录来源：

```sh
python src/cli/cli.py -u alice agents update --agent agent-id --data '{"settings":{"workspaces":{"companion":true,"user_shared":false,"cli":true,"teams":true,"paths":[]}}}'
```

## 验证范围

本次在 Linux 实测 Landlock 的多目录读写，以及工作区外读、写和 Python 删除拒绝；检查 SRT 配置包含全部目录。配置接口、CLI 上下文、Skills 可见性、Team 加入/退出、子 Agent 继承和严格文件工具有回归测试。Studio 与手机表单有浏览器测试。未在 Windows/macOS 主机上实测，本次不新增平台后端或下载组件。
