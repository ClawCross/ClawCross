# ClawCross 交接（2026-11-07）

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
