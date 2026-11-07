# ClawCross 交接（2026-11-08）


## 真实入群修复（2026-11-08）

- 用户最终明确改为新建 Codex；已加入指定官网群「未来之室」。Agent `ag_join_a6d96a1144efa8df99a7ed71`，本机关联 `rg_cf9828b0f1ef3f8bbb621699`，后台接收状态 connected。未向群发送测试消息。
- 工作目录 `/home/ubuntu/outputs/clawcross-hub-codex`；已有原生会话 /home/ubuntu 范围过宽被保护规则拒绝，没有绕过保护或导入原会话。
- 真实 ACP 入群曾暴露 500：join_link 的工作线程调用 is_busy，却要求运行中的事件循环。已改为带互斥保护地读取各循环的 turn lock；跨线程检查能保持真实运行状态，不创建外部进程。
- 针对状态/外部收件箱的 12 项测试通过，空闲时加载修复后，同一新 Codex 幂等入群返回 ok=true、background_running=true、connected。

## 本轮完成：转接模式与可选自启动（2026-11-08）

- 用户命名为「转接模式」，参数 `start --bridge`；不叫客户端模式。
- `join --prepare` 全程命令行：只启动本机 Agent / 群消息服务，不启动前端、OASIS、调度器、渠道或隧道，不要求填写主模型 Key。
- 保留 `--agent` / `--current` / `--native-session` / `--new` 的显式身份选择。准备时 ACP 初始化检查不发模型 prompt；原生适配器下载需要明确准备请求和本机 host proof，普通原生列表保持离线。
- 新增 `/groups/{gid}/connection`；只有后台收到有效群响应后 `join` 才报告 `ok=true`，成员已入群但接收未就绪会非零退出。连接状态/心跳不唤醒模型。
- `join --autostart`、`autostart enable --bridge` / `status` / `disable`。Linux 用户 systemd + 可用时 linger；macOS LaunchAgent、Windows 当前用户计划任务在登录后启动。启用/关闭不打断当前运行。临时 npx 安装不能登记为持久自启动。
- 官网当前仍是每 2 秒 HTTP 同步，由后台主动送入持久 inbox；直接 relay 支持 WebSocket。本轮没有改 Hub 仓库、没有向真实群发送测试消息，没有改动本机既有 systemd 自启动登记。
- 验证：CLI/bootstrap/autostart/ACP **52 passed**；relay/native/join/autostart/bridge **53 passed, 1 opt-in skipped**。单独开启真实启动测试时，已验证只开放 Agent/group 两个临时端口，关闭后全部子进程退出；同轮其余回归 **26 passed**，唯一初始失败是测试错误地调用不可变字段 `update(via=...)`，已仅修正临时 fixture，并复验通过。
- 生成本地 npm 包 `dist/clawcross-0.0.29.tgz`（826 个运行文件，约 14.9 MB），安装到临时目录后再验证真实入口 **8 passed**。包不含交接、自演进、真实配置或数据库。npm registry 尚未发布新版；GitHub 源码/本地包可用。
- 当前服务已在确认 24 个 Agent 空闲后通过既有 restart_flag 加载新代码；新 connection 路由对不存在的群返回预期 403，公网 Studio 200。`/openapi.json` 在此部署关闭（404），不要将其当作启动就绪探针。
- 文档与复制 Prompt 见 `docs/agent-join.md`。本节随本轮实现提交；提交号以 `git log -1 --oneline` 为准。以下是前序阶段的历史记录。

## 前序：命令行入群（2026-11-08）

- 上一批 `49c6493` 已按用户授权推送到 `origin/main`，远端 SHA 已核对。下面旧段落的「未推送」属于当时记录。
- 用户新增需求：评估包内 acpx、核对 npm 安装，并允许给 Codex 一个 prompt 后，按邀请链接把 Agent 加入官网/其他群。用户进一步明确希望支持复用同一个 Agent 和原有上下文。
- 新的未提交源码：`src/cli/commands/join.py`，由 npm / 源码统一入口分发。`--agent` 复用原 ClawCross Agent；`--native-session` 按准确原生 ID 登记恢复；`--current` 只读取 `CODEX_THREAD_ID`。找不到或无法恢复时失败，禁止猜最近会话或新建替身。`--new` 是明确创建模式，重复执行使用确定 ID。
- `--prepare` 才允许缺少时启动本机服务、安装 acpx。未启用公网、未修改运行服务或向实际群发送测试消息。原生会话需要适配器支持列举和恢复，其他客户端不能与 ClawCross 同时写该会话。
- 文档和可复制 Prompt：`docs/agent-join.md`。新入口尚未发布 npm。2026-11-08 核对 npm 官方最新 ClawCross 仍是 `0.0.26`，源码为待发布 `0.0.29`；最新 acpx `0.22.0` 需 Node `22.13+`，当前未改变独立组件策略或强制捆绑。
- 验证：源码 join / 原生会话 / CLI 共 **30 passed**；将 `.tgz` 安装到临时目录再通过安装入口验证，**7 passed**。临时安装新增 3 个包、耗时约 4 秒；产物约 14.9 MB，真实 npm 文件清单检查通过。
- 初次回归中的两处失败为旧 mock 少了启动参数，以及测试继承宿主 `PUBLIC_DOMAIN`；已保持原校验意图修复并复验。
- 真实 Codex 原生恢复实测最终 **1 passed, 1 subtests passed**，52.22 秒：独立会话保存随机代号，关闭旧 ACP 连接，用同一原生 ID 恢复并成功回忆代号。没有读取已有用户会话或加入真实群。最初因离线缓存缺适配器 ENOTCACHED、测试硬编码了账号不支持的型号而失败；显式缓存官方适配器 2.5.0、改用本机 CLI 默认模型后通过。探针中 `codex-acp --help` 会等待 ACP 输入，已终止该探针专用进程；不要把它当作普通命令帮助反复运行。
- npm 文件清单检查发现旧 `docs/handoff-20261003.md` 被通配符选入；已增加忽略规则和发布清单硬性检查，交接、self-evolution、真实配置与用户数据不进入安装包。

## 当前状态

- 工作区 `/home/ubuntu/work2/ClawCross`，分支 `main`，本批工作的基线是 `3fb861b`。
- 用户已明确要求「完成提交」。此文件随本批代码提交，具体提交号用 `git log -1 --oneline` 查询；本轮不包含远端推送。
- **头像功能已开发、验证，并由当前线上进程加载。** 真实主 API 经前端代理生成 SVG 返回 HTTP 200，耗时 5.82 秒，生成 3025 字节的合规 SVG。
- **公网 502 已恢复。** Studio 和消息中心均返回 HTTP 200；已配置开机启动并由用户级 systemd 服务接管。
- Hub 是另一仓库 `/home/ubuntu/work2/ClawCrossHub`；本批没有修改它。

## 用户要求与已完成内容

### 头像

用户要求：显示用户头像、给 Agent 设置头像、支持上传和 SVG 生成。SVG 必须是调用已有主 API / 主模型、使用专门 Prompt 的独立流程。

已实现：

- 「我」的头像按钮、Agent 设置的「头像」入口、本人群资料中的可编辑头像。
- 上传 PNG / JPEG / WebP / SVG，描述生成 SVG，先预览，点击保存才替换；取消不保存。
- Agent 中心、联系人、会话列表、群头部、成员页和消息显示统一头像。单 Agent 私聊只显示一个 Agent 头像。
- 头像改变时刷新已显示的历史消息；群内其他设备通过群凭证按需读取。
- 头像资产与用户画像、主 Agent 对话分开存储；Agent 卡片只携带短引用，不塞入图片本体。
- 浏览器访客可上传头像；仅有群邀请凭证的访客不允许调用宿主的生成 API。

核心文件与约束见 [docs/avatars.md](docs/avatars.md)。主要入口：

- `src/backend/common/avatars.py`：规范化、安全校验、所有者隔离、资产存储。
- `src/backend/agents/avatar_generation.py`：一次主 API 调用、头像专用 Prompt、不自动保存。
- `src/backend/agents/routes.py`：`/v1/agents/avatars/me`、`/generate`、`/{id}/avatar`、`settings.avatar`。
- `src/frontend/static/js/avatars.js`、`css/avatars.css`：统一展示和编辑器。
- `src/backend/groups/{client,relay_store,relay_api,facade}.py`：群内授权读取和独立的头像同步。

生成最多 4096 输出 token，无自动重试，模型超时 45 秒、外层 50 秒。每用户每分钟最多 3 次、一个在途请求；API 进程最多 4 个在途请求。选择模型支持的最低思考档位，避免默认的隐藏推理消耗全部预算。首次实测曾因空正文返回 502；修改后，直接调用和真实前端代理调用均成功。

头像同步首次等 2 秒，正常每 15 秒检查，失败退避 30–300 秒。只发布变化的引用，不产生聊天、不调用模型、不阻塞群消息或活动心跳。已有回归测试证明第二轮检查不会再次发送相同更新。

### 一并完成的 Studio / 消息中心工作

本工作区在头像开发前已有约 65 项修改，属于此前连续授权的界面和运行修复。本批提交包含它们及所需新资源：

- 左侧细导航栏、鲜明的 Message 入口、平台名称和官方风格图标；Team 在上、最近 Agent 在下；偏好设置、归档和删除入口。
- 手机不常驻左栏，抽屉导航、PWA 内部跳转、视觉视口和输入法布局修复。
- 新 Agent 默认名为「我的伙伴」，能力模板直接展示，技术配置折叠，创建弹层互斥。
- 多语言选择；运行状态显示耗时；工具和上下文区域优化；动态 UI 默认固定嵌入。
- 消息中心样式、单 Agent 会话头像、按实际分页状态显示「加载更早」。
- 群内共享 Agent 运行状态与计时；状态作为元数据传播，不触发新消息或模型循环。
- 本地 `agy acp` 启动探测、远程设备不复用本地探测、审核器沿用 Agent 模型配置；已安装沙盒时默认使用，不自动安装。
- TinyFish 不再默认恢复为定时任务。

## 502 故障与开机恢复

检查到机器重启后，ClawCross 前端、API 和 launcher 均未运行，原本也没有开机注册。已先用现有配置恢复，再由用户级 systemd 接管；没有改模型凭证。

本机配置（**在仓库外，迁移机器时需要重新配置**）：

- `/home/ubuntu/.config/systemd/user/clawcross.service`
- `/home/ubuntu/.clawcross/config/clawcross-service.env`：0600，仅保留原有 PATH、代理和证书环境；不得提交或打印内容。
- `systemctl --user is-enabled clawcross.service`：enabled。
- `systemctl --user show clawcross.service -p ActiveState -p SubState`：active / running。
- `loginctl show-user ubuntu -p Linger`：yes。
- 服务使用原有 `launch/runtime_control.py start --tunnel`，失败后等待 15 秒再恢复，5 分钟最多启动 3 次。
- 运行日志 `/home/ubuntu/.clawcross/logs/service.log` 可能含登录链接，**不要原样粘贴**。

日常查看：`systemctl --user status clawcross.service`。需要完整重启时使用 `systemctl --user restart clawcross.service`；先确认实际用户的 Agent 空闲。应用内重载仍可使用 `.clawcross/run/restart_flag`。不要直接杀前端子进程。

## 验证证据

1. 后端针对性回归：

```bash
/home/ubuntu/.clawcross/venv/bin/python -m pytest test/test_avatars.py test/test_group_activity.py test/test_agents.py test/test_acp_launchers.py test/test_approval_review.py test/test_runtime_settings.py test/test_prompt_prefix_stability.py -q
```

结果：**182 passed, 57 subtests passed**，19.43 秒。之后补充了 PUT 转发与同步幂等性两项测试，再跑 `test_avatars.py test_group_activity.py`：**11 passed, 8 subtests passed**，4.55 秒。

2. 头像浏览器检查：`group-chat-features.spec.js -g 'avatar|portrait'`，**4 passed**。覆盖生成预览、取消无写入、保存、用户上传、窄屏和键盘高度、群头像及历史头像刷新。
3. Studio / 手机 / 动态 UI：相关三个浏览器文件的针对性用例，**8 passed**，覆盖单滚动侧栏、平台与归档、语言与计时、弹层互斥、iPhone 视口模拟、固定嵌入 UI。
4. 真实线上前端代理 `POST /v1/agents/avatars/generate`：**200**，5.82 秒；结果 `/home/ubuntu/outputs/avatar-generated-cat.svg`，验证摘要 `avatar-generation-check.json`。
5. 真实 SVG 的手机尺寸编辑器截图：`/home/ubuntu/outputs/clawcross-avatar-editor-390.png`，已经目视检查。
6. API `/health`、公网 `/studio`、公网 `/mobile/group_chat`：**200**。
7. JavaScript 语法检查、Python 检查、`git diff --check` 已通过。

测试不等于真实 iPhone Safari 实机测试；没有物理 iPhone 的验证证据。跨设备群读取在隔离 relay 测试中验证，浏览器刷新通过接口模拟验证；其他旧版本部署需要升级才支持新头像接口。

默认受限沙盒中的 TestClient 曾无输出并停在 futex；同一命令在允许本地连接的执行环境中通过。不要无休止重试或误杀其他用户进程。真实执行问题和修复结果已写入 `SKILL.md` 的 self-evolution 记录。

## 运行与协作约束

- Python `/home/ubuntu/.clawcross/venv/bin/python`；运行目录 `/home/ubuntu/.clawcross`。
- API 51200，scheduler 51201，OASIS 51202，groups 51203，frontend 51209。
- Studio： https://clawcross.wecli.net/studio
- 消息中心： https://clawcross.wecli.net/mobile/group_chat
- 主站 https://clawcross.net 属于独立 Hub 项目。
- 不打印 `.env`、令牌、凭证、完整模型请求、Magic 登录链接。
- 保留用户 Git 身份，不添加 AI 作者或 Co-authored-by；不 reset / clean 共享工作区。
- 用户反感重复确认和让用户调试；完成已授权工作。不要重试工作流、自动向真实群发测试消息或让状态心跳触发模型。
- 不擅自安装 OpenClaw；保持模型缓存前缀稳定。

## 网盘副本（尚未包含本批新头像与提交）

挂载 `/home/ubuntu/gdrive`。开发和备份文件夹：

- https://drive.google.com/drive/folders/1acFXM0GurnoKczhIwugS2dD-8qyblhdv
- https://drive.google.com/drive/folders/1J9tBjX4uWdFMppgV0u5X6h1662ArXsxq

两边都有完整的 `ClawCross.zip`、`ClawCrossHub.zip`，含 Git 历史和当时的未提交修改；下载备份副本后 SHA-256 一致。**同名展开目录不完整，完整副本以 ZIP 为准。** rclone 共享 OAuth 项目遭遇 403 限流，后来用 Drive 连接器上传并服务器端复制，不要盲目重试旧客户端。

说明文件为 `00-使用说明.txt`、`已验证快照清单.json`、`SHA256SUMS.txt`。本地校验记录 `/home/ubuntu/outputs/clawcross-drive-verification/verified-result.json`；开发前的代码快照 `/home/ubuntu/outputs/clawcross-drive-copies-20261107/开发/ClawCross` 可用于比对旧改动。**本轮没有把最新代码重新同步网盘。**

原 2026-10-03 交接已存入 [docs/handoff-20261003.md](docs/handoff-20261003.md)。旧文档里的工作状态和待确认要求是历史记录，以当前代码、此文件和用户最新指令为准。
