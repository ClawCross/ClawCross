# 跨用户、跨设备群聊

实现分支：`feature/federated-group-service`。群服务器已成为独立进程，仍保留在本仓库。

## 邀请链接

一个群只有一种邀请：群主在消息中心群聊 ⋮ →「邀请朋友」生成的链接，`https://<前端公网地址>/group-guest#<票据>`。

- 朋友用浏览器打开：取名字、设密码即可聊天，不需要账号，也不会登录主站。访客可以发本人文本、@ 成员、查找历史、引用回复、看成员和改自己的名字，不能管理群成员。
- 装了 ClawCross 的朋友在消息中心「+ → 加入群聊」粘贴同一个链接：以正式成员加入，可以在群成员里添加自己的 agent。不需要填写服务器地址、端口或密码。
- 链接 30 天内可用于加入。「换一个新链接」或「停止邀请」后旧链接失效，已经加入的人不受影响；群主可以在成员列表里移除成员。

经链接加入的设备通过群主的前端公网地址收发：前端只转发 `/relay/join`、`/relay/poll`、`/relay/group`、`/relay/messages`、`/relay/search`、`/relay/agents`、`/relay/manage/*`，每次都要带链接里的票据，加入之后再带群连接凭证。前端是 Flask，不转发 WebSocket，所以这类成员每 2 秒轮询一次新消息；本机和直接连到群服务器的设备仍用 WebSocket。粘贴的是本机自己的链接时（本机地址或当前 `PUBLIC_DOMAIN`），加入后直接连本机群服务器。

前端公网地址变了（例如临时 Cloudflare 隧道重启后换了域名），已经经链接加入的远程成员会连不上，需要重新发链接加入；长期使用请配置固定域名。

群服务器默认只监听 `127.0.0.1:51203`，不需要对外开放端口。新群保存于 `~/.clawcross/data/group-relay.db`。旧群仍使用 `conversations.db`，通过独立进程的本机 RPC 提供兼容，历史不重写、不删除；旧群不能邀请其他设备，需要新建网络群。`rg_*` 是设备 API 使用的本地群号，`g_*` 是群服务器上的群号。

加入群只有邀请链接这一种方式，没有群密码，也没有按群号加入；链接由 ClawCross 前端签发，所以群服务器总是和前端一起运行。

## 群名、历史和引用

- 消息中心群聊右上角菜单 →「修改群名」：只有群主可改；本地 `rg_*`、服务器 `g_*`、成员关系与历史保持不变。在线客户端收到元信息变更，访客下一次轮询更新群名。
- 「查找聊天记录」查询群服务器保存的全部历史，按关键词或发送者名字匹配，每页最多 50 条，点击「更早的结果」继续。旧本机群支持按消息正文搜索。搜索只对当前群成员开放，成员被移除后凭证失效。
- 在消息或搜索结果上右键（手机长按），选择菜单中的「引用回复」，会在输入框上方显示原消息，可取消。发送只传原消息 ID；服务器验证消息属于当前群，并提供最多 500 字符的引用片段。发给 Agent 的 inbox 也会包含这段引用。
- 「邀请朋友」同时显示链接与二维码，可复制链接、保存二维码。二维码在本机由 Python `qrcode` 生成，编码完整 URL（含 `#` 后的票据）；扫码和打开链接等效，不向第三方二维码服务发送凭证。缓存的链接可重新显示二维码，不会因此换新邀请。
- 远程群菜单 →「从本机移除」不访问群服务器，断线时也可使用：清理当前用户在本机的凭证、聊天缓存、投递记录和列表标记，并停止自动重连。远端群和成员关系保留；通知服务器退出使用「退出群聊」，群主删除服务器群使用「解散群聊」。远程成员在对话列表左滑删除也执行本机移除。
- 「邀请朋友」显示本机 Cloudflare 通道状态和「关闭公网通道」按钮。关闭后经此通道访问的页面与群连接会断线；固定域名配置保留，自行部署的 Caddy 等反向代理仍由服务器管理。
- 群主在群聊菜单使用「关闭所有非主机连接」暂停当前群的外部联网：断开外部 WebSocket，拒绝外部轮询、发消息及新加入，成员、凭证、邀请、主 Agent 和历史全部保留。本机连接继续可用；菜单切换为「恢复外部联网」，开启后原客户端通过原凭证自动重连，无需重新入群。暂停状态持久保存，HTTP 返回暂时不可用的 503，WebSocket 使用可重试的 1013，客户端不会将其误判为退出或踢群。主机身份通过本机机器凭证确认，不依据客户端自报的设备 ID。此操作不修改其他群，也不停止 Caddy 或 Cloudflare。

## CLI 和 agent

```bash
# 群主生成邀请链接（旧链接随之失效）
uv run src/cli/cli.py -u alice groups invite --group-id rg_本地群号
# 用邀请链接加入，也可同时引入自己拥有的 agent
uv run src/cli/cli.py -u bob groups join --invite 'https://host.example.com/group-guest#…' --agents my_agent
# 返回值给出本地 rg_* 群号，后续均用这个本地群号
uv run src/cli/cli.py -u bob groups send --group-id rg_本地群号 --message '大家好'
uv run src/cli/cli.py -u bob groups leave --group-id rg_本地群号
```

WeBot 有 `join_group`（参数是用户给的邀请链接）、`leave_group` 工具：调用者身份由 runtime 强制注入，加入只能引入自己；agent 退出只移除自己，保留用户和其他 agent。
外部 agent 用 CLI `groups join --invite <链接> --agents <自己编号>`、`groups send --agent <自己编号>`。
动态块每次读取当前群元信息，包括服务器、群号、成员所在用户和设备；正文仍从 inbox 输入。reset 不改变真实成员关系。

## 服务器主机管理

管理接口需要本机来源和 `group-service.key`，不会开放给拿到邀请链接的人。

```bash
uv run --python ~/.clawcross/venv/bin/python src/backend/groups/admin.py list
# create.json 示例：{"title":"朋友群"}
uv run --python ~/.clawcross/venv/bin/python src/backend/groups/admin.py create --data-file create.json
# 管理 JSON 从文件读取；可以设置 title、dnd。
uv run --python ~/.clawcross/venv/bin/python src/backend/groups/admin.py patch --group-id g_服务器群号 --data-file settings.json
uv run --python ~/.clawcross/venv/bin/python src/backend/groups/admin.py remove_member --group-id g_服务器群号 --data-file member.json
uv run --python ~/.clawcross/venv/bin/python src/backend/groups/admin.py delete --group-id g_服务器群号
```

`settings.json` 可为 `{"title":"新群名"}`。
`member.json` 为 `{"principal":"p_成员编号"}`。移除人类成员会撤销其整条设备连接及其 agent；移除 agent 只影响该 agent。
使用独立 `CLAWCROSS_HOME` 时，admin 命令也使用相同目录、`PORT_GROUPS` 配置。群主客户端也可管理自己创建的群。

## 凭证、双向连接和恢复

- 邀请链接里的票据由前端签名，群服务器只保存邀请令牌的摘要；加入后签发群与连接范围内的随机凭证，服务器只保存凭证摘要。访客自设的找回密码用独立随机盐和 PBKDF2 摘要存储。
- 用户/设备名称是自报显示标签，不能当作跨服务器验证过的账号。写身份使用服务器签发的 connection/principal，不由正文 user_id 决定；同号 agent 不串号。
- 客户端主动建立 WebSocket，首帧认证，不把 token 放进 URL。服务器从同一连接推回消息，无需客户端开放端口或回调凭证。
- 客户端保存凭证、事件游标、投递记录和本机 agent 授权，经链接加入的连接还保存链接票据；Unix 数据库权限 0600。
- 断线按 1–30 秒退避重连；加入、退出、改名、禁言、移除成员会通知已连接客户端。撤销后停止读写与投递。
- 消息 client_msg_id 去重；投递前再次检查本机用户、显式授权及当前群成员。WeBot inbox 用固定 delivery_id 去重。
- 外部 runtime 在“已接收、尚未记录投递成功”期间崩溃时，仍可能重复收到一次；不宣称跨所有 runtime 精确一次投递。

## 容量与当前边界

每群最多 128 条成员连接、256 个人/agent，每连接最多 32 个 agent；每设备最多连接 128 个群、每本机用户最多 32 个。
服务器最多 256 条 WebSocket（包括等待认证的连接），每凭证最多 2 条；加入有每 IP/全局频率限制，发消息每凭证每分钟最多 120 次。
消息和附件合计最多 512 KiB；HTTP 与 WebSocket 帧限制 2 MiB，补收分批限制。agent 相互唤醒有持久化风暴预算。
客户端缓存最多 2000 个事件，每次界面查询最多 100 条消息；服务端消息历史保留在 SQLite，磁盘占用需主机定期管理。

这一版没有账号联邦、端到端加密、群主转让、远端 agent 私聊或远端运行控制。参与者可自选自己的 agent 加入，服务器无需启动 agent runtime。
