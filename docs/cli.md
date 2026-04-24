# Clawcross CLI 命令大全

> 最后更新：2026-04-23

## 运行方式

**使用项目根目录作为工作目录，通过 `uv run` 直接执行：**

```bash
cd /path/to/Clawcross
uv run scripts/cli.py [参数...]
```

**Windows PowerShell：**

```powershell
Set-Location C:\path\to\Clawcross
uv run .\scripts\cli.py [参数...]
```

> 不需要额外安装 `clawcross` 命令或设置 alias。直接在项目目录下用 `uv run` 即可，它会自动解析项目依赖并执行。
> 如果你更想复用当前虚拟环境，也可以直接运行 `.venv\Scripts\python.exe .\scripts\cli.py [参数...]`。

## 概览

```bash
uv run scripts/cli.py [-u USER] <子命令> [参数...]
```

**全局参数：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-u`, `--user` | 用户名（通过 `X-User-Id` header 传递给后端） | 环境变量 `CLI_USER`，默认 `admin` |

> `-u` 对所有走 front.py 的命令生效（internal-agents / teams / visual / openclaw-snapshot 等）。

---

## 目录

1. [chat](#1-chat) — 发送消息
2. [sessions](#2-sessions) — 会话列表
3. [sessions-status](#3-sessions-status) — 所有会话忙碌状态
4. [session-status](#4-session-status) — 单个会话状态
5. [history](#5-history) — 会话历史
6. [delete-session](#6-delete-session) — 删除会话
7. [settings](#7-settings) — 查看/修改设置
8. [tools](#8-tools) — 可用工具
9. [tts](#9-tts) — 文字转语音
10. [cancel](#10-cancel) — 取消当前生成
11. [restart](#11-restart) — 重启 Agent
12. [groups](#12-groups) — 群组管理
13. [openclaw](#13-openclaw) — OpenClaw Agent 管理
14. [openclaw-snapshot](#14-openclaw-snapshot) — OpenClaw 快照管理
15. [visual](#15-visual) — 可视化编排管理
16. [internal-agents](#16-internal-agents) — 内部 Agent CRUD
17. [teams](#17-teams) — Team 管理
18. [topics](#18-topics) — OASIS 话题管理
19. [personas](#19-personas) — 人设管理
20. [workflows](#20-workflows) — YAML / Python Workflow 管理
21. [tunnel](#21-tunnel) — Cloudflare Tunnel 管理
22. [token](#22-token) — Token 生成与验证
23. [status](#23-status) — 服务状态检查

---

## 1. chat

**发送消息（流式输出）**

```bash
# 基本用法（-s 必填）
uv run scripts/cli.py -u Avalon_01 chat "你好" -s mysession

```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `message` | 消息内容（位置参数） | 是 | — |
| `-s`, `--session` | 会话 ID | 是 | — |

---

## 2. sessions

**查看会话列表**

```bash
uv run scripts/cli.py -u Avalon_01 sessions
```

无额外参数。

---

## 3. sessions-status

**查看所有会话忙碌状态**

```bash
uv run scripts/cli.py sessions-status
```

无额外参数。

---

## 4. session-status

**查看单个会话状态**

```bash
uv run scripts/cli.py session-status -s mysession
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `-s`, `--session` | 会话 ID | 否 | `default` |

---

## 5. history

**查看会话历史**

```bash
# 查看完整历史
uv run scripts/cli.py -u Avalon_01 history -s mysession

# 最近 5 条
uv run scripts/cli.py -u Avalon_01 history -s mysession -n 5

# 不截断长消息
uv run scripts/cli.py -u Avalon_01 history -s mysession --full
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `-s`, `--session` | 会话 ID | 否 | `default` |
| `-n`, `--limit` | 最近 N 条 | 否 | 全部 |
| `--full` | 不截断长消息 | 否 | `False` |

---

## 6. delete-session

**删除会话**

```bash
uv run scripts/cli.py -u Avalon_01 delete-session mysession
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `session` | 会话 ID（位置参数） | 是 | — |

---

## 7. settings

**查看/修改设置**

```bash
# 查看基本设置
uv run scripts/cli.py settings

# 查看完整设置（含高级项）
uv run scripts/cli.py settings --full

# 修改设置
uv run scripts/cli.py settings --set model gpt-4o
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `--full` | 显示完整设置 | 否 | `False` |
| `--set KEY VALUE` | 修改设置项 | 否 | — |

---

## 8. tools

**查看可用工具**

```bash
# 完整工具列表
uv run scripts/cli.py tools

# 仅显示名称
uv run scripts/cli.py tools --brief
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `--brief` | 仅显示工具名称 | 否 | `False` |

---

## 9. tts

**文字转语音**

```bash
uv run scripts/cli.py tts "你好世界" -o hello.mp3 --voice alloy
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `text` | 要转换的文本（位置参数） | 是 | — |
| `-o`, `--output` | 输出文件 | 否 | `tts_output.mp3` |
| `--voice` | 语音角色 | 否 | — |

---

## 10. cancel

**取消当前生成**

```bash
uv run scripts/cli.py cancel -s mysession
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `-s`, `--session` | 会话 ID | 否 | `default` |

---

## 11. restart

**重启 Agent 服务**

本地操作，写入 `.restart_flag` 文件触发 launcher 重启。

```bash
uv run scripts/cli.py restart
```

无额外参数，无 HTTP 请求。

---

## 12. groups

**群组管理**

```bash
# 列出群组
uv run scripts/cli.py groups
uv run scripts/cli.py groups list

# 创建群组
uv run scripts/cli.py groups create --name "测试群" --data '{"members":["bot1","bot2"]}'

# 查看群组详情
uv run scripts/cli.py groups get --group-id abc123

# 更新群组
uv run scripts/cli.py groups update --group-id abc123 --data '{"name":"新名字"}'

# 删除群组
uv run scripts/cli.py groups delete --group-id abc123

# 查看消息
uv run scripts/cli.py groups messages --group-id abc123
uv run scripts/cli.py groups messages --group-id abc123 --after-id msg_100

# 发送消息
uv run scripts/cli.py groups send --group-id abc123 --message "大家好"

# 静音/取消静音
uv run scripts/cli.py groups mute --group-id abc123
uv run scripts/cli.py groups unmute --group-id abc123
uv run scripts/cli.py groups mute-status --group-id abc123

# 群组会话
uv run scripts/cli.py groups sessions --group-id abc123
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `action` | 操作 | 否 | `list` |
| `--group-id` | 群组 ID | 视操作而定 | — |
| `--name` | 群组名称 | create 时 | — |
| `--message` | 消息内容 | send 时 | — |
| `--data` | JSON 数据 | 否 | — |
| `--after-id` | 增量消息起点 | 否 | — |



---

## 13. openclaw

**OpenClaw Agent 管理**

```bash
# 列出 OpenClaw 会话
uv run scripts/cli.py openclaw
uv run scripts/cli.py openclaw sessions --filter keyword

# 添加 Agent
uv run scripts/cli.py openclaw add --data '{"name":"mybot","api_url":"..."}'

# 查看 Agent 详情
uv run scripts/cli.py openclaw detail --name mybot

# 查看工作区
uv run scripts/cli.py openclaw default-workspace
uv run scripts/cli.py openclaw workspace-files --workspace /path
uv run scripts/cli.py openclaw workspace-file-read --workspace /path --filename main.py

# 保存文件
uv run scripts/cli.py openclaw workspace-file-save --data '{"workspace":"/path","filename":"main.py","content":"..."}'

# 技能与工具
uv run scripts/cli.py openclaw skills --agent mybot
uv run scripts/cli.py openclaw tool-groups

# 频道与绑定
uv run scripts/cli.py openclaw channels
uv run scripts/cli.py openclaw bindings --agent mybot
uv run scripts/cli.py openclaw bind --data '{"agent":"mybot","channel":"ch1"}'

# 微信账号绑定示例
uv run scripts/cli.py openclaw channels
uv run scripts/cli.py openclaw bind --data '{"agent":"main","channel":"openclaw-weixin:cdb0be1f7414-im-bot"}'

# 更新配置
uv run scripts/cli.py openclaw update-config --data '{"name":"mybot","config":{...}}'

# 移除 Agent
uv run scripts/cli.py openclaw remove --name mybot
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `action` | 操作 | 否 | `sessions` |
| `--filter` | 过滤关键词 | 否 | — |
| `--name` | Agent 名称 | 视操作 | — |
| `--agent` | Agent 名称 | 视操作 | — |
| `--workspace` | 工作区路径 | 视操作 | — |
| `--filename` | 文件名 | 视操作 | — |
| `--data` | JSON 数据 | 视操作 | — |


---

## 14. openclaw-snapshot

**OpenClaw 快照管理**

```bash
# 获取快照列表
uv run scripts/cli.py -u Avalon_01 openclaw-snapshot get --team myteam

# 导出单个快照
uv run scripts/cli.py -u Avalon_01 openclaw-snapshot export --team myteam --agent-name "Agent全名" --short-name "显示名"

# 导出全部
uv run scripts/cli.py -u Avalon_01 openclaw-snapshot export-all --team myteam

# 同步全部
uv run scripts/cli.py -u Avalon_01 openclaw-snapshot sync-all --team myteam

# 恢复单个
uv run scripts/cli.py -u Avalon_01 openclaw-snapshot restore --team myteam --short-name "显示名" --target-name "目标Agent"

# 恢复全部
uv run scripts/cli.py -u Avalon_01 openclaw-snapshot restore-all --team myteam
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `action` | 操作 | 否 | `get` |
| `--team` | Team 名称 | 是 | — |
| `--agent-name` | Agent 全名 | export 时 | — |
| `--short-name` | 显示名 | export/restore 时 | — |
| `--target-name` | 恢复目标 Agent 名 | restore 时 | — |


---

## 15. visual

**可视化编排管理**

```bash
# 人设列表
uv run scripts/cli.py -u Avalon_01 visual personas --team myteam

# 添加/删除自定义人设
uv run scripts/cli.py -u Avalon_01 visual add-persona --data '{"tag":"myexpert","name":"我的人设","prompt":"..."}' --team myteam
uv run scripts/cli.py -u Avalon_01 visual delete-persona --tag myexpert --team myteam

# 生成 YAML
uv run scripts/cli.py -u Avalon_01 visual generate-yaml --data '{"nodes":[...],"edges":[...]}' --team myteam
uv run scripts/cli.py -u Avalon_01 visual agent-generate-yaml --data '{"prompt":"..."}' --team myteam

# 布局管理
uv run scripts/cli.py -u Avalon_01 visual save-layout --data '{"name":"myflow","nodes":[...],"edges":[...]}' --team myteam
uv run scripts/cli.py -u Avalon_01 visual load-layouts --team myteam
uv run scripts/cli.py -u Avalon_01 visual load-layout --name myflow --team myteam
uv run scripts/cli.py -u Avalon_01 visual load-yaml-raw --name myflow --team myteam
uv run scripts/cli.py -u Avalon_01 visual delete-layout --name myflow --team myteam

# 上传 YAML
uv run scripts/cli.py -u Avalon_01 visual upload-yaml --data '{"name":"myflow","yaml_content":"..."}' --team myteam

# 编排会话状态
uv run scripts/cli.py -u Avalon_01 visual sessions-status
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `action` | 操作 | 否 | `personas` |
| `--team` | Team 名称 | 否 | — |
| `--tag` | 人设 tag | delete-persona 时 | — |
| `--name` | 布局名称 | load/delete 时 | — |
| `--data` | JSON 数据 | 视操作 | — |

---

## 16. internal-agents

**内部 Agent CRUD**

```bash
# 列出
uv run scripts/cli.py -u Avalon_01 internal-agents list --team myteam

# 添加
uv run scripts/cli.py -u Avalon_01 internal-agents add --team myteam --data '{"session":"s1","meta":{"name":"bot","tag":"assistant"}}'
uv run scripts/cli.py -u Avalon_01 internal-agents add --team myteam --session s1 --data '{"meta":{"name":"bot"}}'

# 更新（--sid 必填）
uv run scripts/cli.py -u Avalon_01 internal-agents update --sid s1 --team myteam --data '{"meta":{"name":"new_name"}}'

# 删除（--sid 必填）
uv run scripts/cli.py -u Avalon_01 internal-agents delete --sid s1 --team myteam
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `action` | 操作 | 否 | `list` |
| `--team` | Team 名称 | 否 | — |
| `--sid` | Session ID | update/delete 时必填 | — |
| `--session` | Session ID（add 时自动补入 data） | 否 | — |
| `--data` | JSON 数据 | add/update 时 | — |

---

## 17. teams

**Team 管理**

```bash
# 团队列表
uv run scripts/cli.py -u Avalon_01 teams

# 一次性查看 team 完整信息（聚合成员、人设、workflows、话题等）
uv run scripts/cli.py -u Avalon_01 teams info --team-name team2

# 创建 / 删除团队
uv run scripts/cli.py -u Avalon_01 teams create --team-name newteam --data '{"description":"..."}'
uv run scripts/cli.py -u Avalon_01 teams delete --team-name oldteam

# 成员管理
uv run scripts/cli.py -u Avalon_01 teams members --team-name myteam
uv run scripts/cli.py -u Avalon_01 teams add-ext-member --team-name myteam --data '{"user_id":"bob","role":"member"}'
uv run scripts/cli.py -u Avalon_01 teams update-ext-member --team-name myteam --data '{"user_id":"bob","role":"admin"}'
uv run scripts/cli.py -u Avalon_01 teams delete-ext-member --team-name myteam --data '{"user_id":"bob"}'

# 团队人设管理
uv run scripts/cli.py -u Avalon_01 teams personas --team-name myteam
uv run scripts/cli.py -u Avalon_01 teams add-persona --team-name myteam --data '{"tag":"myexpert","name":"...","prompt":"..."}'
uv run scripts/cli.py -u Avalon_01 teams update-persona --team-name myteam --tag myexpert --data '{"name":"..."}'
uv run scripts/cli.py -u Avalon_01 teams delete-persona --team-name myteam --tag myexpert

# 团队快照 — 预览
uv run scripts/cli.py -u Avalon_01 teams snapshot-preview --team-name myteam

# 团队快照 — 全量导出
uv run scripts/cli.py -u Avalon_01 teams snapshot-download --team-name myteam -o snapshot.zip

# 团队快照 — 选择性导出（通过 --include JSON）
uv run scripts/cli.py -u Avalon_01 teams snapshot-download --team-name myteam \
  --include '{"agents":true,"personas":true,"skills":{"OpenClaw助手":["Clawcross","ChatBot"]},"cron":true,"workflows":true}'

# 团队快照 — 上传恢复
uv run scripts/cli.py -u Avalon_01 teams snapshot-upload --team-name myteam --file snapshot.zip
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `action` | 操作 (`list`, `info`, `create`, `delete`, `members`, `snapshot-preview`, `snapshot-download`, ...) | 否 | `list` |
| `--team-name` | Team 名称 | 视操作 | — |
| `--tag` | 人设 tag | update-persona/delete-persona 时 | — |
| `--data` | JSON 数据 | 视操作 | — |
| `-o`, `--output` | 输出文件路径 | 否 | `team_{name}_snapshot.zip` |
| `--file` | 上传文件路径 | snapshot-upload 时 | — |
| `--include` | 选择性导出 JSON（snapshot-download 时） | 否 | 全量导出 |

> `info` 命令一次性聚合调用多个 API（成员、人设、workflows、话题、OpenClaw 快照），美化输出该 team 的完整信息快照，不直接对应单个接口。

### 快照预览 (`snapshot-preview`)

预览 team 中可导出的所有内容，不实际导出：

```bash
uv run scripts/cli.py -u Avalon_01 teams snapshot-preview --team-name myteam
```

输出示例：
```
════════════════════════════════════════════════════════════
📋 Team 'myteam' 可导出内容预览
════════════════════════════════════════════════════════════

🤖 agents — 内部 Agent (2 个):
  • ChatBot  [assistant]
  • Coder    [developer]

🧑‍🏫 personas — 自定义人设 (3 个):
  • [creative] 创意专家
  • [critical] 批判分析师
  • [summary] 总结专家

🔧 skills — OpenClaw Agent 技能 (1 个 Agent):
  • OpenClaw助手  (global: main)  — 5 skills:
    - Clawcross
    - ChatBot
    - WebSearch
    - CodeHelper
    - DataAnalyzer
  📦 managed skills (1 个):
    - some_managed_skill

⏰ cron — 定时任务 (共 2 个):
  • OpenClaw助手 (2 个):
    - 日报汇总  [0 9 * * *]
    - 周报提醒  [0 10 * * 1]

📐 workflows — YAML 文件 (3 个):
  • oasis/yaml/brainstorm_workflow.yaml
  • oasis/yaml/product_review_workflow.yaml
  • oasis/yaml/creative_critical_workflow.yaml

════════════════════════════════════════════════════════════
💡 选择性导出示例:
  ...
```

### 选择性导出 (`--include`)

`snapshot-download` 支持通过 `--include` 参数传入 JSON，精确控制导出内容。

#### 可选的 section

| Section | 说明 | 对应文件 |
|---------|------|----------|
| `agents` | 内部 Agent 配置 | `internal_agents.json` |
| `personas` | 自定义人设 | `oasis_experts.json` |
| `skills` | OpenClaw Agent 技能文件夹 | `external_agents.json` + workspace skills |
| `cron` | 定时任务 | `external_agents.json` + `cron_jobs.json` |
| `workflows` | YAML workflow 文件 | `oasis/yaml/*.yaml` |

#### `skills` 的 3 种选择模式

`skills` 字段支持层级 JSON，可以精确到每个 Agent 和每个 Skill：

| 模式 | 格式 | 含义 |
|------|------|------|
| 全量 | `"skills": true` | 导出所有 Agent 的所有 skill |
| 按 Agent | `"skills": {"OpenClaw助手": true}` | 只导出该 Agent 的全部 skill |
| 按 Skill | `"skills": {"OpenClaw助手": ["Clawcross", "ChatBot"]}` | 只导出该 Agent 下指定的 skill |

#### 使用示例

```bash
# 1. 全量导出（默认行为，等价于不传 --include）
uv run scripts/cli.py -u admin teams snapshot-download --team-name myteam

# 2. 只导出 agents 和 workflows
uv run scripts/cli.py -u admin teams snapshot-download --team-name myteam \
  --include '{"agents":true,"workflows":true}'

# 3. 导出所有内容
uv run scripts/cli.py -u admin teams snapshot-download --team-name myteam \
  --include '{"agents":true,"personas":true,"skills":true,"cron":true,"workflows":true}'

# 4. 只导出特定 Agent 的全部 skill
uv run scripts/cli.py -u admin teams snapshot-download --team-name myteam \
  --include '{"skills":{"OpenClaw助手":true}}'

# 5. 只导出特定 Agent 的特定 skill
uv run scripts/cli.py -u admin teams snapshot-download --team-name myteam \
  --include '{"skills":{"OpenClaw助手":["Clawcross","ChatBot"]}}'

# 6. 混合选择：导出 agents + 特定 skills + cron
uv run scripts/cli.py -u admin teams snapshot-download --team-name myteam \
  --include '{"agents":true,"skills":{"OpenClaw助手":["Clawcross"]},"cron":true}'

# 7. 多个 Agent，一个全选，一个选特定 skill
uv run scripts/cli.py -u admin teams snapshot-download --team-name myteam \
  --include '{"skills":{"OpenClaw助手":["Clawcross"],"另一个Agent":true}}'
```

> 先运行 `snapshot-preview` 查看可导出内容，再根据预览结果构造 `--include` JSON。`snapshot-preview` 命令末尾也会自动生成选择性导出的示例命令。

---

## 18. topics

**OASIS 话题管理**

```bash
# 列出话题
uv run scripts/cli.py topics
uv run scripts/cli.py topics list

# 查看话题详情（美化输出：时间线 + 发言记录 + 结论）
uv run scripts/cli.py topics show --topic-id t123
uv run scripts/cli.py topics show --topic-id t123 --full    # 不截断长发言
uv run scripts/cli.py topics show --topic-id t123 --raw     # 输出原始 JSON

# 实时跟踪讨论过程（SSE 流式输出，Ctrl+C 退出）
uv run scripts/cli.py topics watch --topic-id t123

# 取消讨论
uv run scripts/cli.py topics cancel --topic-id t123

# 清除话题
uv run scripts/cli.py topics purge --topic-id t123

# 删除全部话题
uv run scripts/cli.py topics delete-all
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `action` | 操作 (`list`, `show`, `watch`, `cancel`, `purge`, `delete-all`) | 否 | `list` |
| `--topic-id` | 话题 ID | show/watch/cancel/purge 时必填 | — |
| `--raw` | 输出原始 JSON (show 时) | 否 | `False` |
| `--full` | 不截断长发言内容 (show 时) | 否 | `False` |

> 三种查看方式的区别：
> - `show` — 一次性获取话题的完整快照（时间线 + 所有发言 + 结论），适合话题结束后回顾
> - `watch` — 实时流式输出讨论进展（连接后端 SSE），适合启动 workflow 后实时跟踪
> - `conclusion` (在 workflows 子命令中) — 阻塞等待直到讨论结束并返回结论


## 19. personas

**OASIS 人设管理**

> `personas` 命令管理"人设"(persona)——即 **expert persona prompt**，一种定义 Agent 角色、性格和能力的特殊提示词配置，而非一个独立的 agent。团队目录下的 `oasis_experts.json` 就是这些 persona prompt 的集合文件。

```bash
# 列出人设
uv run scripts/cli.py -u Avalon_01 personas
uv run scripts/cli.py -u Avalon_01 personas list
uv run scripts/cli.py -u Avalon_01 personas list --team team2

# 添加自定义人设
uv run scripts/cli.py -u Avalon_01 personas add --tag my_analyst --persona-name "数据分析师" --persona "你是资深数据分析师"
uv run scripts/cli.py -u Avalon_01 personas add --tag my_lawyer --persona-name "法律顾问" --persona "你是法律顾问" --team team2

# 更新人设（仅更新 persona，name 保持不变）
uv run scripts/cli.py -u Avalon_01 personas update --tag my_analyst --persona "你是AI数据分析师，擅长深度学习"

# 更新人设（同时修改 name）
uv run scripts/cli.py -u Avalon_01 personas update --tag my_analyst --persona-name "高级分析师" --persona "你是AI数据分析师"

# 删除人设
uv run scripts/cli.py -u Avalon_01 personas delete --tag my_analyst
uv run scripts/cli.py -u Avalon_01 personas delete --tag my_lawyer --team team2
```
| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `action` | 操作 | 否 | `list` |
| `--tag` | 人设标签（唯一标识） | add/update/delete 时必填 | — |
| `--persona-name` | 人设显示名称 | add 时必填，update 时可选（不传则保持原值） | — |
| `--persona` | 人设描述 | 否 | — |
| `--temperature` | 温度参数 (0-2) | 否 | 0.7 |
| `--team` | Team 名称 | 否 | — |

---

## 20. workflows

**OASIS YAML / Python Workflow 管理**

```bash
# 列出 personal + 全部 team 的 YAML/Python workflow
uv run scripts/cli.py -u Avalon_01 workflows
uv run scripts/cli.py -u Avalon_01 workflows list

# 只看 YAML 或 Python
uv run scripts/cli.py -u Avalon_01 workflows list --type yaml
uv run scripts/cli.py -u Avalon_01 workflows list --type python

# 指定 team 后，只列该 team 下的 workflow
uv run scripts/cli.py -u Avalon_01 workflows list --team team2

# 查看 workflow 内容
uv run scripts/cli.py -u Avalon_01 workflows show --name creative_critical_workflow
uv run scripts/cli.py -u Avalon_01 workflows show --type python --name my_python_flow
uv run scripts/cli.py -u Avalon_01 workflows show --name test2flow --team team2

# 保存 YAML workflow（从文件）
uv run scripts/cli.py -u Avalon_01 workflows save --name my_flow --yaml-file /path/to/flow.yaml --description "我的工作流"
uv run scripts/cli.py -u Avalon_01 workflows save --name my_flow --yaml-file /path/to/flow.yaml --team team2

# 保存 YAML workflow（直接传 YAML）
uv run scripts/cli.py -u Avalon_01 workflows save --name quick_flow --yaml 'version: 2
plan:
  - id: s1
    expert: creative#temp#1
  - id: s2
    expert: critical#temp#1
edges:
  - [s1, s2]'

# 运行 YAML workflow（使用已保存的文件名）
uv run scripts/cli.py -u Avalon_01 workflows run --name creative_critical_workflow --question "分析AI发展趋势"
uv run scripts/cli.py -u Avalon_01 workflows run --name test2flow --team team2 --question "讨论产品策略"

# 运行 YAML workflow（从 YAML 文件）
uv run scripts/cli.py -u Avalon_01 workflows run --yaml-file /path/to/flow.yaml --question "分析数据"

# 运行 Python workflow（已保存文件）
uv run scripts/cli.py -u Avalon_01 workflows run --type python --name my_python_flow --question "生成报告"
uv run scripts/cli.py -u Avalon_01 workflows run --type python --name team_flow --team team2 --question "执行任务"

# 运行 Python workflow（直接指定文件路径）
uv run scripts/cli.py -u Avalon_01 workflows run --type python --python-file /path/to/flow.py --question "执行任务"

# 运行选项
uv run scripts/cli.py -u Avalon_01 workflows run --name my_flow --question "问题" --max-rounds 10 --discussion true
uv run scripts/cli.py -u Avalon_01 workflows run --name my_flow --question "问题" --early-stop

# Human 节点回复（当 workflow 正在等待人工输入时）
uv run scripts/cli.py -u Avalon_01 topics show --topic-id abc12345 --raw
uv run scripts/cli.py -u Avalon_01 topics human-reply --topic-id abc12345 --node-id on2 --round-num 2 --message "继续执行，风险可接受"

# 获取结论（阻塞等待直到讨论结束）
uv run scripts/cli.py -u Avalon_01 workflows conclusion --topic-id abc12345
uv run scripts/cli.py -u Avalon_01 workflows conclusion --topic-id abc12345 --timeout 600
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `action` | 操作 | 否 | `list` |
| `--team` | Team 名称 | 否 | — |
| `--type` | workflow 类型：`all`/`yaml`/`python` | 否 | `all` |
| `--name` | Workflow 文件名（YAML 可省略 `.yaml`，Python 可省略 `.py`） | show/save 时必填, run 时可选 | — |
| `--python-file` | Python workflow 文件路径 | run 时可选（与 `--name` 二选一） | — |
| `--yaml` | 直接传入 YAML 内容 | save/run 时可选（与 `--yaml-file` 二选一） | — |
| `--yaml-file` | YAML 文件路径 | save/run 时可选（与 `--yaml`/`--name` 二选一） | — |
| `--description` | Workflow 描述 | 否 | — |
| `--question` | 讨论问题/任务 | run 时必填 | — |
| `--max-rounds` | 最大讨论轮数 (1-20) | 否 | 5 |
| `--discussion` | 讨论模式 (true=讨论, false=执行) | 否 | YAML 中设定 |
| `--early-stop` | 提前终止 | 否 | false |
| `--topic-id` | 话题 ID | conclusion 时必填 | — |
| `--timeout` | 等待超时秒数 | 否 | 300 |

> `list` 在 **未指定 `--team` 时**，会自动聚合：
>
> - `data/user_files/{user}/oasis/yaml/`
> - `data/user_files/{user}/oasis/python/`
> - `data/user_files/{user}/teams/*/oasis/yaml/`
> - `data/user_files/{user}/teams/*/oasis/python/`
>
> 指定 `--team` 后，只显示该 team 下的 YAML/Python workflow。
>
> `show` 直接读取本地文件，不走 HTTP。默认按文件名后缀自动判断；也可显式传 `--type python`。
>
> `run` 分两类：
>
> - YAML workflow：返回 `topic_id`，用 `conclusion` 或 `topics show` 查看结果。
> - Python workflow：standalone 启动，直接返回 `run_id`、`log`、`result` 路径。
>
> 如果 workflow 包含 `human` 节点，运行时回复入口不在 workflow 编辑器本身，而在 OASIS topic detail 中；CLI 对应命令是 `topics human-reply`。

---

## 21. tunnel

**Cloudflare Tunnel 管理**

本地进程操作，通过 `scripts/tunnel.py` 和 pid 文件管理。

```bash
# 查看状态
uv run scripts/cli.py tunnel
uv run scripts/cli.py tunnel status

# 启动 / 停止
uv run scripts/cli.py tunnel start
uv run scripts/cli.py tunnel stop
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `action` | 操作 | 否 | `status` |

无 HTTP 请求。

---

## 22. token

**Token 生成与验证**

本地操作，生成/验证 HMAC 签名的登录 Token。

```bash
# 生成单用户 token
uv run scripts/cli.py token generate --user admin

# 生成多用户 tokens
uv run scripts/cli.py token generate --users admin,user1,user2

# 指定有效期
uv run scripts/cli.py token generate --user admin --valid-hours 48

# 验证 token
uv run scripts/cli.py token verify --token "xxx"

# 解码 token
uv run scripts/cli.py token decode --token "xxx"
```

| 参数 | 说明 | 必填 | 默认值 |
|------|------|------|--------|
| `action` | 操作 (`generate`, `verify`, `decode`) | 否 | `generate` |
| `--user` | 用户名 (generate 时，也可通过 `-u` 指定) | 否 | — |
| `--users` | 多用户列表，逗号分隔 | 否 | — |
| `--token` | Token 字符串 (verify/decode 时) | 视操作 | — |
| `--valid-hours` | Token 有效期 (小时) | 否 | 24 |

无 HTTP 请求，纯本地操作。

---

## 23. status

**检查各服务状态**

同时探测三个核心服务的健康状态。

```bash
uv run scripts/cli.py status
```

无额外参数。

---

## 常用组合示例

```bash
# 检查服务是否正常
uv run scripts/cli.py status

# 以 Avalon_01 身份管理 team2
uv run scripts/cli.py -u Avalon_01 teams members --team-name team2
uv run scripts/cli.py -u Avalon_01 internal-agents list --team team2
uv run scripts/cli.py -u Avalon_01 workflows list --team team2
uv run scripts/cli.py -u Avalon_01 workflows show --name test2flow --team team2

# 聊天
uv run scripts/cli.py -u Avalon_01 chat "帮我分析这段数据" -s analysis_session

# 查看聊天历史（最近 10 条，完整输出）
uv run scripts/cli.py -u Avalon_01 history -s analysis_session -n 10 --full

# 可视化编排：查看布局 → 查看 YAML → 查看会话状态
uv run scripts/cli.py -u Avalon_01 visual load-layouts --team myteam
uv run scripts/cli.py -u Avalon_01 visual load-yaml-raw --name myflow --team myteam
uv run scripts/cli.py -u Avalon_01 visual sessions-status

# OpenClaw 快照：导出全部 → 恢复全部
uv run scripts/cli.py -u Avalon_01 openclaw-snapshot export-all --team myteam
uv run scripts/cli.py -u Avalon_01 openclaw-snapshot restore-all --team myteam

```
