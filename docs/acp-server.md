# WeBot ACP 与 DeepSeek Harness

## 将 WeBot 提供为 ACP 服务

启动已有本机后台后执行：

```bash
clawcross acp --user alice --agent '已有WeBot编号'
```

ACP 客户端将该命令作为 stdio server 启动。没有额外 HTTP 监听端口，也不需要打开前端。
账号默认沿用 `clawcross accounts` 的默认用户。省略 `--agent` 时，`session/new` 会通过
正常 Agent API 创建一个可在 Studio / Message 找到的 WeBot；默认能力模板是群聊伙伴。
`--template chat|group|personal` 可选择创建模板，`--name` 可设置名字。

支持 ACP v1 initialize、session/new、session/list、session/load、session/resume、
session/prompt、session/cancel，输出 `session/update` 文字流。
加载时回放最近最多 1000 条普通对话，工具内部消息不作为普通文本回放。
session ID 就是正常 Agent ID；所有请求绑定启动时的用户，不能加载其他用户的 Agent。
客户端关闭时取消本次在执行的请求，不删除 Agent 或历史。

模型使用现有 WeBot 模型配置；对话、工具、审批与命令沙盒由现有后端执行。
客户端不能通过 cwd、MCP 或模式参数扩大工作区或新增工具。
当前 ACP 入口只接收文字，不声明图片、终端、客户端文件系统或外部 MCP 支持。

## DeepSeek Harness 默认 ACP 适配器

平台名 `deepseek-harness`，默认命令为官方 `dsh --profile acp`，不需要自定义启动命令。
显式安装：

```bash
clawcross install-component deepseek-harness
```

当前固定官方 npm 版本 `@deepseek-ai/dsh@0.2.2-rc.1`；包要求 Node.js 24.9+。
安装器和 `join --prepare` 都检查此要求，版本不足时不安装不兼容依赖。
安装在 ClawCross 的私有可选组件目录，保存精确版本；不会拉取另一份项目源码、
替换系统 Node、修改原 CLI 登录或创建后台模型任务。

安装其他项目作为运行时依赖是正常做法；这里按需使用发布包。安装、ACP 初始化和
连接检查本身不发送模型 prompt。DeepSeek 的模型账号和配置仍由官方 Harness 管理。
原生工具开关的限制与其他 ACP 平台相同，见 [转接模式](agent-join.md)。
