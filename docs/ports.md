# Clawcross 端口大全

> 最后更新：2026-10-01

## 端口总览

| 端口 | 环境变量 | 服务文件 | 说明 | 绑定地址 | 对外暴露 |
|------|----------|----------|------|----------|----------|
| **51200** | `PORT_AGENT` | `src/backend/server.py` | AI Agent 主服务（OpenAI 兼容 API） | `127.0.0.1` | 否 |
| **51201** | `PORT_SCHEDULER` | `src/backend/scheduler/service.py` | 定时任务调度中心 | `127.0.0.1` | 否 |
| **51202** | `PORT_OASIS` | `src/backend/oasis/server.py` | OASIS 论坛 / Agent 管理与编排中心 | `127.0.0.1` | 否 |
| **51203** | `PORT_GROUPS` | `src/backend/groups/server.py` | 独立群聊服务器、群连接与转发 | `127.0.0.1`（可显式更改） | 默认否 |
| **51209** | `PORT_FRONTEND` | `src/frontend/server.py` | 前端 Web UI（Flask） | `0.0.0.0` | 是 Tunnel |
| **51210** | —（硬编码） | `src/frontend/visual.py` | 可视化编排系统（开发用） | `0.0.0.0` | 否 |
| **58010** | `PORT_BARK` | 外部二进制 `bin/bark-server` | Bark 推送服务器 | — | 是 Tunnel |

## 详细说明

### 51200 — AI Agent 主服务

- **文件**：`src/backend/server.py`
- **职责**：
  - 提供 OpenAI 兼容的 `/v1/chat/completions` 接口
  - Agent 核心逻辑（工具调用、多轮对话、记忆管理）
  - `/system_trigger` 内部触发端点（定时任务回调等）
  - `/v1/agents`（本机所有 agent，WeBot 会话也在其中）、`/login`、`/tools`、`/tts`、`/settings`、`/groups` 等 API
- **调用方**：前端 `src/frontend/server.py`（代理转发）、渠道、MCP 模块、OASIS 回调
- **鉴权**：`X-Internal-Token` 或用户密码

### 51201 — 定时任务调度中心

- **文件**：`src/backend/scheduler/service.py`
- **职责**：
  - 管理 cron / 一次性定时任务
  - 提供 `/tasks` 端点供 `mcp_scheduler.py` 调用
  - 任务到期时回调 Agent 的 `/system_trigger`
  - 在启用时恢复 TinyFish 内建搜索任务
- **调用方**：`mcp_scheduler.py`、Agent 内部

### 51202 — OASIS 论坛服务

- **文件**：`src/backend/oasis/server.py`
- **职责**：
  - 多人设讨论引擎（Topics / Experts / Sessions）
  - Town Genesis / swarm blueprint 生成
  - GraphRAG 长期记忆与 ReportAgent（尚未接入，见 oasis-reference.md）
  - `/publicnet/info` 公网信息查询
  - Agent 管理与编排中心（迁移中）
- **调用方**：`mcp_oasis.py`、前端代理、外部脚本
- **注意**：默认绑定 `127.0.0.1`；WSL 下或设置了 `CLAWCROSS_SERVER_HOST` 时会绑定到其他地址。此时只有本机回环地址的调用可以免 token，其他主机的请求必须带 `X-Internal-Token: $INTERNAL_TOKEN`，否则返回 401。

### 51203 — 独立群聊服务

默认本机运行，旧群通过机器凭证 RPC 访问，新群通过群客户端连接。
显式 `GROUP_SERVER_HOST=0.0.0.0` 后，其他设备可凭群号和密码加入。
远端仅接触 `/relay` 群协议，不获得 Agent API 的 INTERNAL_TOKEN。
前端 Tunnel 只公开网页，不自动公开群服务器。公网群服务器应通过 HTTPS/WSS 代理。
使用方式见 [跨设备群聊](group-network.md)。

### 51209 — 前端 Web UI

- **文件**：`src/frontend/server.py`
- **职责**：
  - 用户交互界面（聊天、登录、设置、OASIS 面板）
  - 反向代理：将浏览器请求转发到 Agent / OASIS 等内部服务
  - ClawCross Creator 页面、构建记录与 ClawCross Studio 相关入口
  - ClawCross Studio 右侧 OASIS Town 侧栏、swarm graph、ReportAgent
  - TinyFish 监控状态、手动运行、实时爬取和站点快照查询
  - Session 管理、PWA 支持
- **安全策略**：
  - 本地直连（`127.0.0.1` 且无任何代理头）→ 信任放行
  - 经过任何反向代理的请求 → 要求 Session 登录
  - 公开路由（login、static、OpenAI compat）→ 始终放行
- **唯一对外暴露的核心端口**

### 51210 — 可视化编排系统（开发用）

- **文件**：`src/frontend/visual.py`
- **职责**：独立 Flask 应用，提供 2D 画布拖拽编排 Agent 节点，导出 OASIS 兼容的 YAML 工作流
- **注意**：不在 `launcher.py` 启动序列中，需手动 `python src/frontend/visual.py` 启动

### 58010 — Bark 推送服务器

- **来源**：外部二进制 `bin/bark-server`
- **职责**：接收推送请求并转发到 iOS/macOS 设备
- **数据**：`data/bark/bark.db`
- **公网地址**：当前单一隧道不暴露 Bark，也不写入 `BARK_PUBLIC_URL`

## 启动顺序

由 `launch/launcher.py` 定义：

| 步骤 | 服务 | 端口 | 等待时间 |
|------|------|------|----------|
| 1/5 | 定时调度中心 | 51201 | 2s |
| 2/5 | OASIS 论坛 | 51202 | 2s |
| 3/5 | AI Agent | 51200 | 3s |
| 4/5 | 渠道配置 | — | 交互式 |
| 5/5 | 前端 Web UI | 51209 | 1s |

## Tunnel 暴露策略

由 `launch/tunnel.py` 管理：

```
公网用户
  ↓ HTTPS
[单个 Cloudflare 临时隧道]
  └─→ 127.0.0.1:51209 (frontend) → PUBLIC_DOMAIN
```

- 此隧道只暴露前端（`src/frontend/server.py`）；Bark 保持本地访问
- 每次仅生成一个公网地址；重启后地址可能变化。`cloudflared` 必须预先安装，启动脚本不会下载
- 所有内部服务（Agent、Scheduler、OASIS）**不对外暴露**
- 前端到内部服务的通信全部通过前端（`src/frontend/server.py`）反向代理

## 环境变量配置

在 `config/.env` 中设置（一般无需修改默认值）：

```env
PORT_AGENT=51200
PORT_SCHEDULER=51201
PORT_OASIS=51202
PORT_FRONTEND=51209
```

---

## 前端 src/frontend/server.py 全部接口（:51209）

### 页面 & 静态资源

- `GET /` — 登录/主页面
- `GET /creator` — ClawCross Creator 页面
- `GET /studio` — ClawCross Studio / workflow canvas 页面
- `GET /manifest.json` — PWA manifest
- `GET /sw.js` — Service Worker

### OpenAI 兼容 & Agent / Team API（→ :51200）

- `POST /v1/chat/completions` — 聊天补全（公开路由，Bearer Token 鉴权）；`model` 可以是 agent 地址 / `ag_` 编号，或 `<用户>/<team>`（交给 team 的 lead）
- `GET /v1/models` — 模型列表（公开路由）：你的 agent 与 team
- `/v1/agents…` — 本机所有 agent，一套接口（登录用户以自己的身份转发）：
  - `GET /v1/agents`（`?status=1` 附带运行状态，`?platform=webot` 只列一个平台的）· `POST /v1/agents` 新建（`{name, platform, persona, team, global_name, api_url, model…}`）
  - `GET|PATCH|DELETE /v1/agents/<ref>` · `POST /v1/agents/<ref>/messages` · `POST /v1/agents/<ref>/control`（status / cancel / reset；WeBot 还有 compact / deliver_inbox）· `GET /v1/agents/<ref>/history`
  - WeBot 会话就是 WeBot agent：会话列表 = `GET /v1/agents?status=1&platform=webot`（状态里带 `title`、`last_message`、`message_count`、时间、`mode`、`context`），删除会话 = `DELETE /v1/agents/<id>`
- `/v1/teams…` — team 组合 agent：
  - `GET|POST /v1/teams` · `GET|PATCH|DELETE /v1/teams/<team>`
  - `POST /v1/teams/<team>/members`（`{agent, role?, is_lead?}`）· `PATCH|DELETE /v1/teams/<team>/members/<agent>`
  - `POST /v1/teams/<team>/import`（导入 team 文件夹里的 internal_agents.json / external_agents.json）· `POST /v1/teams/<team>/messages`（交给 lead）

### 登录 & 会话

- `POST /proxy_login` — 登录（公开路由，→ :51200 `/login`）
- `POST /proxy_logout` — 登出
- `GET /proxy_check_session` — 检查登录状态（公开路由）

### Agent 代理（→ :51200）

- `POST /proxy_tts` — 语音合成（→ `/tts`）
- `GET /proxy_tools` — 工具列表（→ `/tools`）
- `GET /proxy_settings` — 获取设置（→ `/settings`）
- `POST /proxy_settings` — 更新设置（→ `/settings`）
- `GET /proxy_settings_full` — 获取完整设置（→ `/settings/full`）
- `POST /proxy_settings_full` — 更新完整设置（→ `/settings/full`）
- `POST /proxy_restart` — 重启服务（→ `/restart`）

### 群组聊天代理（→ :51200）

`/proxy_groups/...` 原样转发到 `/groups/...`（以当前登录用户身份）。成员是 agent（`ag_…`）和人（`u:<用户>`）。

- `GET /proxy_groups` — 群聊列表（含私聊）
- `POST /proxy_groups` — 创建：`{title, kind?: "group"|"direct", agents?: [ref], team?}`；按 team 建群时成员和主 agent 跟随 team
- `GET|PATCH|DELETE /proxy_groups/<id>` — 详情 / `{title?, dnd?}` / 删除
- `GET /proxy_groups/<id>/messages?after_id=` — 增量消息
- `POST /proxy_groups/<id>/messages` — 发送：`{content, mentions?, reply_to?, attachments?, run_mode?}`
- `POST /proxy_groups/<id>/members` — 加成员 `{agent}`；`PATCH|DELETE /proxy_groups/<id>/members/<principal>` — `{muted?, nickname?}` / 移出
- `POST /proxy_groups/<id>/mute_agents` — 全员禁言 `{muted}`
- `PUT /proxy_groups/<id>/primary` — 主 agent `{agent | null}`
- `GET /proxy_groups/<id>/typing` · `GET /proxy_groups/<id>/available_agents`

### OASIS 代理（→ :51202）

- `GET /proxy_oasis/topics` — 话题列表
- `POST /proxy_oasis/topics` — 创建话题；支持 `autogen_swarm`、`swarm_mode`
- `GET /proxy_oasis/topics/<id>` — 话题详情
- `GET /proxy_oasis/topics/<id>/stream` — 话题讨论 SSE 流
- `POST /proxy_oasis/topics/<id>/posts` — 向运行中的 topic 注入人工 nudge
- `POST /proxy_oasis/topics/<id>/swarm/refresh` — 重新生成 swarm / GraphRAG 蓝图
- `POST /proxy_oasis/topics/<id>/report/ask` — 向 ReportAgent 追问当前预测原因
- `POST /proxy_oasis/topics/<id>/cancel` — 取消讨论
- `POST /proxy_oasis/topics/<id>/purge` — 清除话题
- `DELETE /proxy_oasis/topics` — 删除话题
- `GET /proxy_oasis/experts` — 人设列表

补充说明：

- `GET /studio` 页面内包含 ClawCross Studio 主画布和右侧 `🏘️ OASIS Town` 侧栏
- 第一次进入 `/studio` 时默认落在 `Chat` tab，右侧 Town 侧栏折叠、`Town Mode` 关闭、子 tab 默认是 `TOWN`
- Town Mode、`REFORGE`、`EXPLAIN` 都在这条侧栏里，不在消息中心侧栏

### TinyFish 搜索代理（前端本地处理 + TinyFish Web Agent）

- `GET /api/tinyfish/status` — 获取监控配置、目标列表、最近运行、价格变化和最新站点快照
- `POST /api/tinyfish/run` — 提交 TinyFish 监控任务，可选同步等待完成
- `POST /api/tinyfish/live-run` — 透传 TinyFish SSE 实时爬取事件，并在结束后持久化结果
- `GET /api/tinyfish/sites/<site_key>` — 查看单个站点最近一次存储的快照

### ClawCross Creator（前端本地处理 + TinyFish / OASIS）

- `POST /api/team-creator/discover` — ClawCross Creator 第 1 阶段：发现 SOP / 组织结构页面，SSE 流式返回
- `POST /api/team-creator/extract` — ClawCross Creator 第 2 阶段：对单个页面执行 TinyFish 角色提取，SSE 流式返回
- `POST /api/team-creator/smart-select` — 对提取角色做智能筛选并匹配预设专家
- `POST /api/team-creator/build` — ClawCross Creator 第 3 阶段：生成 Team 配置、Persona、workflow DAG 和 YAML
- `POST /api/team-creator/download` — 将构建结果导出为 ZIP
- `POST /api/team-creator/translate` — ClawCross Creator 动态双语翻译
- `GET /api/team-creator/presets` — 兼容旧前端的预设专家列表
- `GET /api/team-creator/jobs` — 最近 ClawCross Creator 构建记录
- `GET /api/team-creator/jobs/<job_id>` — 单条构建记录详情

### 可视化编排代理（本地处理 / → :51202）

- `GET /proxy_visual/experts` — 人设 prompt 列表（含自定义）
- `POST /proxy_visual/experts/custom` — 添加自定义人设 prompt
- `DELETE /proxy_visual/experts/custom/<tag>` — 删除自定义人设 prompt
- `POST /proxy_visual/generate-yaml` — 生成 YAML 工作流
- `POST /proxy_visual/agent-generate-yaml` — AI 生成 YAML
- `POST /proxy_visual/save-layout` — 保存布局
- `GET /proxy_visual/load-layouts` — 布局列表
- `GET /proxy_visual/load-layout/<name>` — 加载布局
- `GET /proxy_visual/load-yaml-raw/<name>` — 原始 YAML
- `DELETE /proxy_visual/delete-layout/<name>` — 删除布局
- `POST /proxy_visual/upload-yaml` — 上传 YAML

### Tunnel 管理

- `GET /proxy_tunnel/status` — Tunnel 状态
- `POST /proxy_tunnel/start` — 启动 Tunnel
- `POST /proxy_tunnel/stop` — 停止 Tunnel

### Teams 管理

- `GET /teams` — 团队列表
- `POST /teams` — 创建团队
- `PATCH /teams/<name>` — 重命名
- `DELETE /teams/<name>` — 删除团队文件夹（agent 不受影响）
- `GET|POST /teams/<name>/alarms` · `DELETE /teams/<name>/alarms/<task_id>` — 团队成员的定时任务（`{agent, schedule_type, cron|run_at, text}`）
- `GET /teams/<name>/experts` — 团队人设 prompt 列表
- `POST /teams/<name>/experts` — 添加团队人设 prompt
- `PUT /teams/<name>/experts/<tag>` — 更新团队人设 prompt
- `DELETE /teams/<name>/experts/<tag>` — 删除团队人设 prompt
- `POST /teams/<name>/generate-from-workflow` — 把画布上的 agent / persona 节点加进团队
- `POST /teams/snapshot/download` — 下载团队快照
- `POST /teams/snapshot/upload` — 上传团队快照

成员增删改走 `/v1/teams/<team>/members…`（见上）。

## 鉴权规则

**原则：不是 127.0.0.1 直连就要密码。**

```
if 公开路由 → 放行
if 127.0.0.1 且无代理头 → 放行（本机直连）
else → 要求登录
```

### 检测的反向代理头

以下任一头存在，即视为经过反向代理，**必须登录**：

- `X-Forwarded-For` — Nginx / Caddy / Traefik / HAProxy / 通用
- `X-Forwarded-Proto` — Nginx / Caddy / 通用
- `X-Forwarded-Host` — Nginx / Traefik
- `X-Real-Ip` — Nginx
- `Cf-Connecting-Ip` — Cloudflare Tunnel
- `Cf-Ray` — Cloudflare Tunnel
- `True-Client-Ip` — Cloudflare / Akamai
- `Forwarded` — RFC 7239 标准头
- `Via` — HTTP 标准代理头

### 判定结果

| 场景 | 结果 |
|------|------|
| 本地浏览器 `127.0.0.1` 直连 | 通过 |
| 本地 agent / MCP 工具直连 | 通过 |
| Cloudflare Tunnel 转发（带 `Cf-Ray`） | 需登录 |
| Nginx 反代转发（带 `X-Forwarded-For`） | 需登录 |
| Caddy / Traefik / HAProxy 转发 | 需登录 |
| 外网 IP 直连 | 需登录 |
