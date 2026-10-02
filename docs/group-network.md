# 跨用户、跨设备群聊

实现分支：`feature/federated-group-service`。群服务器已成为独立进程，仍保留在本仓库。

## 默认本机使用

`bash run.sh start` 会同时启动群服务器（51203）和 Agent 服务。
消息中心“+”可以创建普通群，不必选择 team；“+ → 加入群聊”可填写群号加入本机群，地址和密码默认留空。
加入后人可以发言；只有用户主动添加的本机 agent 才会收到群消息。邀请界面显示真实服务器群号。

新群保存于 `~/.clawcross/data/group-relay.db`。旧群仍使用 `conversations.db`，通过独立进程的本机 RPC 提供兼容，历史不重写、不删除。
旧群不能直接用于跨设备加入；需要新建网络群。`rg_*` 是设备 API 使用的本地群号，`g_*` 是邀请时使用的服务器群号。

## 开放给其他设备

1. 群服务器的 `.env` 明确设置 `GROUP_SERVER_HOST=0.0.0.0`，重启；默认不会监听外网。
2. 群主在“邀请朋友 / 群聊地址”设置密码。空密码关闭远程新加入，本机仍按 `local_join` 设置加入。
3. 朋友在自己的消息中心“+ → 加入群聊”，输入 `http://<服务器IP>:51203`、邀请群号和密码。
4. 加入后手动添加自己的 agent；无需把任何 Agent API 内部密钥给群服务器。

只运行群服务器（不运行 LLM、Agent、OASIS）：

```bash
CLAWCROSS_HOME=/path/to/group-host uv run --python ~/.clawcross/venv/bin/python src/backend/groups/server.py --relay-only --host 0.0.0.0 --port 51203
```

服务器主机可以用下方 admin 命令建群、设置密码和管理成员，也可以启动同机 ClawCross 在界面建群。
公网地址应使用 HTTPS/WSS 反向代理；代理需要支持 `/relay/ws` 的 WebSocket 升级。`GROUP_PUBLIC_URL=https://groups.example.com` 控制邀请显示地址。
现有前端 Cloudflare Tunnel 只提供浏览器访问，不会自动把 51203 暴露成群服务器。内网测试可以使用 HTTP。

## 朋友聊天链接

网络群的群主可在“邀请朋友”中生成朋友聊天链接，直接复制给朋友。打开后只需取名，无需 ClawCross 账号，也不会登录主站。
页面只提供消息、群成员列表和本人改名；访客凭证不能添加 agent、管理成员或管理群。
展开群成员列表可查看全部成员，点击名字即可提及；输入 `@` 或点击输入框旁的 `@` 按钮可筛选选择成员。选中的提及携带成员编号，服务端校验其属于本群，并按既有规则唤醒被提及的 agent。
名字不能与本群现有成员重名，忽略大小写、首尾空格和 Unicode 全半角差异；同时加入也会执行事务检查。
加入即可以查看本群最近 100 条历史消息，后续自动接收新消息。页面最多显示 500 条，访客身份保存在该浏览器中，刷新可继续使用。
邀请链接本身就是加入凭证，不包含群密码。链接有效期为 30 天；重新生成或关闭邀请会让旧链接无法再加入，已加入的访客不会被自动踢出，群主可单独移除。
页面经现有前端公网通道访问，无需额外公开 51203 端口。浏览器通过受限 HTTP 接口每两秒接收增量；设备客户端原有 WebSocket 机制保持不变。

## CLI 和 agent

```bash
# 群密码从标准输入的一行读取，避免出现在命令行参数中
uv run src/cli/cli.py -u bob groups join --server-url http://192.168.1.10:51203 --group-id g_邀请群号 --password-stdin
# 也可在加入时明确引入自己拥有的 agent
uv run src/cli/cli.py -u bob groups join --server-url http://192.168.1.10:51203 --group-id g_邀请群号 --agents my_agent --password-stdin
# 上面的返回值给出本地 rg_* 群号，后续均用这个本地群号
uv run src/cli/cli.py -u bob groups send --group-id rg_本地群号 --message '大家好'
uv run src/cli/cli.py -u bob groups invite --group-id rg_本地群号
uv run src/cli/cli.py -u bob groups leave --group-id rg_本地群号
```

WeBot 有 `join_group`、`leave_group` 工具：调用者身份由 runtime 强制注入，加入只能引入自己；agent 退出只移除自己，保留用户和其他 agent。
外部 agent 用 CLI `groups join --agents <自己编号> --password-stdin`、`groups send --agent <自己编号>`。
动态块每次读取当前群元信息，包括服务器、群号、成员所在用户和设备；正文仍从 inbox 输入。reset 不改变真实成员关系。

## 服务器主机管理

管理接口需要本机来源和 `group-service.key`，不会开放给拿到群密码的人。

```bash
uv run --python ~/.clawcross/venv/bin/python src/backend/groups/admin.py list
# create.json 示例：{"title":"朋友群","password":"自行设置的密码"}
uv run --python ~/.clawcross/venv/bin/python src/backend/groups/admin.py create --data-file create.json
# 管理 JSON 从文件读取；可以设置 title、password、local_join、dnd。
uv run --python ~/.clawcross/venv/bin/python src/backend/groups/admin.py patch --group-id g_服务器群号 --data-file settings.json
uv run --python ~/.clawcross/venv/bin/python src/backend/groups/admin.py remove_member --group-id g_服务器群号 --data-file member.json
uv run --python ~/.clawcross/venv/bin/python src/backend/groups/admin.py delete --group-id g_服务器群号
```

`settings.json` 可为 `{"password":"新密码","revoke_connections":true}`；不指定撤销时，换密码仅影响后续加入。
`member.json` 为 `{"principal":"p_成员编号"}`。移除人类成员会撤销其整条设备连接及其 agent；移除 agent 只影响该 agent。
使用独立 `CLAWCROSS_HOME` 时，admin 命令也使用相同目录、`PORT_GROUPS` 配置。群主客户端也可管理自己创建的群。

## 凭证、双向连接和恢复

- 密码使用独立随机盐和 PBKDF2 摘要存储，验证后签发群与连接范围内的随机凭证；服务器只保存凭证摘要。
- 用户/设备名称是自报显示标签，不能当作跨服务器验证过的账号。写身份使用服务器签发的 connection/principal，不由正文 user_id 决定；同号 agent 不串号。
- 客户端主动建立 WebSocket，首帧认证，不把 token 放进 URL。服务器从同一连接推回消息，无需客户端开放端口或回调凭证。
- 客户端保存凭证、事件游标、投递记录和本机 agent 授权；Unix 数据库权限 0600。群密码不在客户端数据库保存。
- 断线按 1–30 秒退避重连；加入、退出、改名、禁言、密码撤销会通知已连接客户端。撤销后停止读写与投递。
- 消息 client_msg_id 去重；投递前再次检查本机用户、显式授权及当前群成员。WeBot inbox 用固定 delivery_id 去重。
- 外部 runtime 在“已接收、尚未记录投递成功”期间崩溃时，仍可能重复收到一次；不宣称跨所有 runtime 精确一次投递。

## 容量与当前边界

每群最多 128 条成员连接、256 个人/agent，每连接最多 32 个 agent；每设备最多连接 128 个群、每本机用户最多 32 个。
服务器最多 256 条 WebSocket（包括等待认证的连接），每凭证最多 2 条；加入有每 IP/全局频率限制，发消息每凭证每分钟最多 120 次。
消息和附件合计最多 512 KiB；HTTP 与 WebSocket 帧限制 2 MiB，补收分批限制。agent 相互唤醒有持久化风暴预算。
客户端缓存最多 2000 个事件，每次界面查询最多 100 条消息；服务端消息历史保留在 SQLite，磁盘占用需主机定期管理。

这一版没有账号联邦、端到端加密、群主转让、远端 agent 私聊或远端运行控制。参与者可自选自己的 agent 加入，服务器无需启动 agent runtime。
