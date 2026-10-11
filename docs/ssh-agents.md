# SSH 设备与统一 Agent 管理

本机 WeBot、本机 ACP Agent 和 SSH 远端 ACP Agent 共用 `agents` 表、Gateway、
对话、收件箱、Team 与群聊入口。SSH 是运行设备配置，不是另一套聊天系统。

## 使用

1. Studio Agent 中心或 Mobile 通讯录点击“设备与 SSH”。
2. 添加设备名称、IP/域名/SSH 配置别名、用户名、端口。支持 SSH Agent 或密钥文件；
   私钥内容不存入 Agent、不发给模型。
3. 点击“连接并检测”。远端需要 Python 3、acpx，以及所选 CLI 的安装和登录。
   检测不会安装软件。没有完整 ClawCross 的设备也可以接入。
4. 点击“新增远端 Agent”，填写名称、ACP 平台和可选远端原生工作目录。
   默认创建 `~/.clawcross/workspaces/<agent_id>`。
5. 新 Agent 出现在原有 Agent 中心、通讯录和平台会话列表，标明设备名称与 SSH。
   已创建 ACP Agent 也可以在 Agent 设置中选择运行设备。

会话开始后不能随意更换设备或原生目录；新建或明确重置后才允许更换。
现有连接已绑定 Agent 时不能改成另一台设备，也不能直接删除连接。

## Gateway 与连接生命周期

```text
Agent ID → AgentGateway → AcpRuntime → 本机 AcpxAdapter
                                   → SSHAcpxAdapter → 保存的 SSH 连接 → 远端 acpx
```

- 每个连接属于一个 ClawCross 用户。其他用户不能绑定、检测、修改该连接。
- Linux/macOS 客户端使用 OpenSSH 连接复用；Windows 客户端保留转发连接，
  操作使用独立 SSH 通道。当前实测覆盖 Linux；Windows/macOS 尚未实机测试。
- 主动断开保存为“暂停”。群聊或收件箱不会自动解除暂停；明确点击连接才恢复。
- 网络异常结束连接后，下一次请求可重连。不会重放已经成功发送的历史消息。
- 取消、重置、历史和删除沿用现有 Agent API；暂停时取消/重置只改变本机请求状态，
  返回未确认远端进程状态的说明。暂停时删除移除本机登记，保留远端原生会话。

## 工具、工作区与凭证

远端原生 CLI 工具在远端执行，并遵循 CLI 的权限与沙盒。
ClawCross MCP 工具通过 SSH 回到本机工具节点，继续使用 Agent 工具表、模式和审核。
ClawCross 的文件、Skill 和 command 工具使用本机 ClawCross 工作区；动态块明确说明
它与远端 `native_cwd` 的区别。这里没有声称本机沙盒会限制另一台设备上的原生 CLI。

回程端口只在远端 loopback 监听，只连接本机专用 MCP relay。
Relay 仅提供 `/external/tool-bridge`，不转发整个 Agent API；使用 Agent 专属凭证，
后台验证活跃调用及工具范围。SSH 配置默认检查已知主机密钥，可显式选择接纳新主机，
不允许关闭主机密钥检查。[OpenSSH 说明](https://man.openbsd.org/ssh)

## API

- `GET/POST /v1/connections`：列出／保存设备。
- `PATCH/DELETE /v1/connections/{id}`：编辑／删除。
- `POST /v1/connections/{id}/connect`：连接并检测 Python/acpx。
- `POST /v1/connections/{id}/disconnect`：暂停连接。
- `POST /v1/agents`：增加 `connection_id`、`remote_cwd`。
- `PATCH /v1/agents/{id}`：通过 `settings` 修改上述字段。

示例 Agent 配置：

```json
{"name":"笔记本代码助手","platform":"claude","connection_id":"ssh_…","remote_cwd":"/projects/demo"}
```

SSH 当前接入 ACP/CLI Agent；远端 WeBot 服务转发和远端既有原生会话批量导入不在此实现中。
ClawCross 自己记录的对话历史照常可查看；不会把本机原生历史导入误用于另一台设备。

## Fleet 兼容性清理

旧任务看板、心跳和已有数据保留。默认启动不再隐式开启 Fleet 主控；旧 Dashboard
自动发布、Supabase 同步和 Codex 审核默认关闭，显式配置仍可启用。
Mobile 常规刷新不再扫描远端 Claude/Tailscale。

`claude_code_status`、`probe_claude_code`、`configure_claude_keepalive` 不再注册为 Agent 工具。
旧 Claude API 与运行状态展示仅供设置 `CLAWCROSS_LEGACY_CLAUDE_CODE=1` 的兼容部署使用。
新接入统一使用已登记 Agent 的测试连接、消息及控制接口。

## 验证范围

隔离临时 sshd、临时密钥、工作区和远端 runtime 目录：真实 SSH 握手、复用、文件传输、
ACP 事件流、MCP 回程与 Agent 专属凭证、Gateway 递增动态块、重置、取消、删除、
断开和重连。CLI 提供者使用测试进程；没有借此声称已测试用户的真实远端 Claude/Codex 账户。
