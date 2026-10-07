# 每个 Agent 的 MCP 连接接口

状态：接口方案。当前运行时提供内置 MCP 工具，以及给外部 ACP Agent 使用的 ClawCross MCP 桥接；本文的自定义服务器登记、测试和连接 API 尚未实现。

## 用户怎么用

Agent 中心 → 选择 Agent → 高级 → MCP 连接。

1. 添加连接，选择本地程序或服务器 URL，填写名称。
2. 需要凭证时，用户在私密表单中填写；Agent 只能得到“已填写”，收不到密钥。
3. 点击“测试连接”，展示连接状态和服务器的工具列表。这一步不调用模型、不创建对话、不安装程序。
4. 勾选给这个 Agent 的工具。默认不勾选；连接成功和授权工具是两件事。
5. 保存后，下轮调用生效。可暂停连接、重新测试、修改选择或删除登记。

创建 Agent 时也可选择已有连接，工具范围仍按 Agent 单独保存。共享连接定义不自动给所有 Agent 开启工具。

## 传输与凭证

首批支持 `stdio` 和 `streamable_http`。MCP 的这两种标准传输分别通过子进程标准输入输出与 HTTP 交换消息；旧 HTTP+SSE 可作为后续兼容项。[MCP 传输规范](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)

- `stdio`：明确指定已安装的程序与参数数组；不通过 shell 拼接执行，不默认使用会自动下载安装的 `npx`/`uvx` 命令。组件安装独立确认。
- `streamable_http`：填写 HTTPS 服务地址。本机可以使用 HTTP；内网地址必须由用户明确登记。测试与连接复用同一套网络规则。
- 认证：Bearer/API Key 使用秘密引用；支持 OAuth 的服务通过用户登录流程授权。API 返回 `has_credentials`，不回传密钥、真实授权头或带密钥的 URL。
- 第三方子进程只接收选定的环境变量，不继承 ClawCross 内部服务凭证。

## 存储与 API

连接定义与秘密存储在用户私有控制目录 `.control`，避开 Agent 工作区。Agent 表只保存连接 ID 和授权工具，不保存密钥。

示意登记结构：

```json
{
  "id": "mcp_research",
  "name": "论文检索",
  "transport": "streamable_http",
  "url": "https://example.org/mcp",
  "credential_ref": "secret_opaque_id"
}
```

Agent 配置示意：

```json
{
  "mcp_bindings": [
    {"server_id": "mcp_research", "enabled": true, "tools": ["search", "get_paper"]}
  ]
}
```

拟定接口均验证当前登录用户与 Agent 归属：

| 接口 | 操作 |
|---|---|
| `GET /v1/agents/{id}/mcp-servers` | 列出这个 Agent 的连接、授权范围和状态 |
| `POST /v1/agents/{id}/mcp-servers` | 创建或绑定连接；不启动第三方程序 |
| `PATCH /v1/agents/{id}/mcp-servers/{server}` | 修改连接与启用状态；秘密只允许写入 |
| `DELETE /v1/agents/{id}/mcp-servers/{server}` | 解除绑定，停止该绑定的进程与任务 |
| `POST /v1/agents/{id}/mcp-servers/{server}/test-connection` | 在该 Agent 权限下初始化连接、查询工具 |
| `GET /v1/agents/{id}/mcp-servers/{server}/tools` | 分页查看工具说明与参数 |
| `PUT /v1/agents/{id}/mcp-servers/{server}/tools` | 保存明确选择的工具列表 |

状态采用 `disabled / configured / connecting / ready / error`。测试结果包含阶段、错误信息、工具数和耗时；禁止把秘密写入日志。禁用后保留配置，删除后再清理未被其他绑定引用的秘密。

## 运行时接入

所有自定义工具先经过 ClawCross 的工具目录、身份注入与审核路径。

```text
WeBot / 外部 ACP Agent
  → tool_search 查询名称与简介
  → tool_call 获取并校验完整参数
  → 当前 Agent 授权名单与模式
  → 审核和沙盒边界
  → 对应 MCP 连接
```

- 工具使用稳定的 `mcp__<server_id>__<tool_alias>` 名称。名称长度受控，截断时附摘要以避免碰撞；服务器原始名称保留在绑定记录中。
- 默认只展示名称和简介，按需查询完整 schema；不把全部工具定义塞入 system prompt。
- 外部 Codex、Claude 等复用现有 ClawCross 桥接。修改绑定不重建原生会话，不默认直接给原生 CLI 添加可绕过 ClawCross 审核的服务器连接。
- 目录版本按绑定更新。若服务器宣告 `tools/list_changed`，刷新目录；新增工具保持未授权。MCP 的工具注解只是提示，不能据此自动信任“只读”或绕过审核。[MCP 工具规范](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- 子 Agent 的连接和工具取父级允许范围的交集，不自动获得新连接。
- 不同用户、Agent 和权限版本隔离连接状态与调用缓存。暂停/删绑定时取消相关调用；已撤销权限的结果不再注入会话。

## 与现有安全设置的关系

本地 MCP 程序是可执行代码，不能因为它提供了 tool schema 就把它当成可信函数。

- 严格模式：本地 MCP 子进程必须由实际沙盒启动，使用该 Agent 的初始文件、网络和资源限制；不允许提权。平台无法隔离时拒绝启动。
- 普通模式：默认仍在沙盒中执行，系统可按已有审核机制申请有上限的临时/会话权限。配置和密钥的保护边界不因批准而取消。
- 远程 MCP：本地 sandbox 只能约束连接访问，远端操作范围取决于远端账号凭证。用户明确授权的服务和工具，加上 ClawCross 审核，决定可调用范围。
- `chat` 无工具；`readonly` 默认不开放未知的远端工具，除非用户确认相应只读范围；`manual/auto/bypass` 控制审核方式。`bypass` 不增加已授权工具，也不越过严格模式边界。
- URL 校验与调用防止无授权内网访问；重定向重新检查目标，跨来源不转发认证信息。服务器返回的文本视为工具结果，不能修改用户授权名单和运行时配置。

实现顺序：私有登记与凭证表单 → 连接测试与工具选择 → WeBot 调用路由 → 外部桥接 → 目录更新与跨平台沙盒验证。每步单独验证身份、秘密保护与撤销行为。
