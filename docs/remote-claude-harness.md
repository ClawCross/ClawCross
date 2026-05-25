# Remote Claude Harness 接入流程

本文记录把一台新的远端电脑接入 ClawCross Project Harness 的标准流程。目标是让远端 Claude Code / Claude Agents session 能被本机 ClawCross 发现、收发消息、读取 dashboard TODO，并通过私有 harness 更新任务状态和评论。

不要把密码、`INTERNAL_TOKEN`、Claude remote-control 链接、Claude session id 或 harness runtime state 写进 dashboard 仓库。dashboard 只保留项目、TODO、comment、result 等任务信息；Claude 配置和控制链路只放在 ClawCross 里。

## 架构约定

- 本机 ClawCross 运行 `mainagent`、frontend 和 `harness_conductor`。
- 远端电脑通过 Tailscale 和 SSH 接入。
- 远端 `127.0.0.1:51200` 通过 SSH reverse tunnel 指回本机 ClawCross `127.0.0.1:51200`。
- 远端 Claude 通过 `~/.local/bin/clawcross-harness-agent` 读 dashboard TODO、写 harness heartbeat/status/comment。
- 远端项目根目录可以维护一个 `TASK.md`，作为 plan → execution → modification → experiment/result 的本地工作日志；ClawCross 可把它和 dashboard TODO 双向同步。
- 本机前端 `/mobile/group_chat` 通过 Tailscale 枚举远端 Claude sessions，并把项目卡片里的 TODO 和 worker session 绑定显示。
- 每台远端电脑默认绑定一个主项目；没有 TODO 时保留一个 standby session，避免项目卡片消失。

## 1. 远端电脑准备

在远端 Ubuntu/Linux 上执行：

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4
tailscale status
```

启用 SSH：

```bash
sudo apt-get update
sudo apt-get install -y openssh-server tmux git curl python3
sudo systemctl enable --now ssh
sudo systemctl status ssh --no-pager
sudo ss -ltnp | grep ':22'
```

确保 Tailscale 不屏蔽入站 SSH：

```bash
tailscale debug prefs | grep -i shield || true
sudo tailscale set --shields-up=false
```

如果启用了 UFW，允许 Tailscale 网卡访问 SSH：

```bash
sudo ufw status verbose || true
sudo ufw allow in on tailscale0 to any port 22 proto tcp || true
```

验证远端能 ping 到本机 Tailscale IP：

```bash
tailscale ping --timeout=5s --c=3 <local-tailscale-ip>
```

`direct connection not established` 不是硬错误；只要能 `pong`，走 DERP 也可以工作。

## 2. 本机验证 SSH

在本机 ClawCross 主机上确认能连远端：

```bash
ssh <remote-user>@<remote-tailscale-ip> 'whoami; hostname; tailscale ip -4; sudo systemctl status ssh --no-pager'
```

建议后续配置好 SSH key；如果当下只用密码，也不要把密码写入 repo、dashboard 或 docs。

## 3. 安装远端 harness 配置

确保本机 ClawCross 至少启动过一次，这样 `~/.clawcross/config/.env` 里有 `INTERNAL_TOKEN`。

ClawCross 仓库不保存任何具体 dashboard 内容或默认项目。先在本机环境中提供 dashboard 配置：

```bash
export CLAWCROSS_DASHBOARD_URL="<dashboard-web-url>"
export CLAWCROSS_DASHBOARD_ROOT="<local-dashboard-repo>/dashboard"  # 只在本机需要读写 dashboard/state/*.json 时使用
```

从本机 ClawCross repo 执行：

```bash
cd $CLAWCROSS_REPO

python3 scripts/configure_remote_claude_dashboard.py \
  <remote-user>@<remote-tailscale-ip> \
  --default-project-id <project-id> \
  --project-id <extra-project-id-if-needed> \
  --no-batch-mode
```

这个脚本会在远端安装或更新：

- `~/.claude/CLAUDE.md` 的 ClawCross 管理块
- `~/.local/bin/clawcross-harness-agent`
- `~/.clawcross/harness.env`
- Claude Code settings 里允许的 harness/dashboard 命令
- `~/.local/bin/clawcross-claude` wrapper，让普通交互 Claude 默认带上 `--effort max --permission-mode auto --remote-control`

脚本也会把远端 SSH 用户映射记录到本机：

```text
~/.clawcross/data/remote_claude_targets.json
```

ClawCross 默认会用 `tailscale status --json` 枚举在线 Linux 远端；这个 registry 主要用于把 Tailscale host/ip 映射到正确 SSH user。

## 4. 建立 reverse tunnel

远端 harness client 默认访问远端本机的 `http://127.0.0.1:51200`。因此每台远端电脑都需要一条 SSH reverse tunnel 指回本机 ClawCross。

在本机执行：

```bash
tmux new-session -d -s clawcross-tunnel-<short-host> \
  'ssh -N \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=3 \
    -R 127.0.0.1:51200:127.0.0.1:51200 \
    <remote-user>@<remote-tailscale-ip>'
```

验证远端 tunnel：

```bash
ssh <remote-user>@<remote-tailscale-ip> \
  'curl -fsSL http://127.0.0.1:51200/v1/models'
```

预期返回包含 `webot` model 的 JSON。若失败，先重启 tunnel，再检查本机 `clawcross-mainagent` 是否在跑。

## 5. 启动远端 Claude session

远端所有项目默认放在远端自己的 `~/workspace` 下。

普通交互 session：

```bash
ssh <remote-user>@<remote-tailscale-ip>
mkdir -p ~/workspace
cd ~/workspace/<project-repo-or-folder>
~/.local/bin/clawcross-claude
```

进入 Claude 后应确认：

```text
/effort max
/permission-mode auto
/remote-control
```

如果使用 Claude Agents 后台 session，可以在远端启动：

```bash
claude --effort max --permission-mode auto agents
```

后台 session 也会出现在 `~/.claude/sessions/*.json`，ClawCross 可以通过 daemon reply 发送消息。

## 6. 绑定 worker 到项目

本机会枚举远端 sessions：

```bash
cd $CLAWCROSS_REPO
PYTHONPATH=src python3 - <<'PY'
from integrations.remote_claude_agents import list_remote_claude_sessions
import json
print(json.dumps(list_remote_claude_sessions(limit=40, tail_lines=10), ensure_ascii=False, indent=2))
PY
```

找到目标 session 的 key，形如：

```text
<remote-user>@<remote-tailscale-ip>::session_...
```

让远端 session 或本机通过 harness 发 heartbeat：

```bash
~/.local/bin/clawcross-harness-agent heartbeat \
  --agent-id "<project-id>-<hostname-or-role>" \
  --project-id "<project-id>" \
  --status idle \
  --session-ref "<remote-user>@<remote-tailscale-ip>::session_..." \
  --remote-host "<remote-user>@<remote-tailscale-ip>" \
  --worktree "$HOME/workspace/<project-repo-or-folder>" \
  --message "Worker online; waiting for dashboard TODO."
```

如果正在处理某个 TODO，把 `--status idle` 改成 `--status running`，并加上：

```bash
--task-id "<dashboard-task-id>" --current-task-id "<dashboard-task-id>"
```

任务状态更新规范：

```bash
~/.local/bin/clawcross-harness-agent task-status \
  --agent-id "<agent-id>" \
  --project-id "<project-id>" \
  --task-id "<task-id>" \
  --status doing \
  --message "Started: <short plan>"

~/.local/bin/clawcross-harness-agent comment \
  --agent-id "<agent-id>" \
  --project-id "<project-id>" \
  --task-id "<task-id>" \
  --kind comment \
  --message "Progress: <evidence>"

~/.local/bin/clawcross-harness-agent task-status \
  --agent-id "<agent-id>" \
  --project-id "<project-id>" \
  --task-id "<task-id>" \
  --status done \
  --message "Result: <evidence and artifact path>"
```

实验、推理、评测类任务不能只写自然语言结果。需要包含 `run_id`、`git_sha`、实际命令、日志/metrics 路径和 verifier/test 结果。

## 6.1 使用 TASK.md 做远端任务工作日志

`TASK.md` 不是简单 TODO 表，而是远端 worker 对一个项目的可追溯工作记录。它应该记录：

- `plan`: 准备怎么做、关键假设、风险。
- `execution`: 实际执行过的命令、路径、步骤。
- `modifications`: 改了哪些文件、为什么改。
- `experiments`: smoke/test/eval/verifier 命令、退出码、日志、metrics、sha256。
- `result`: 可审查结论或交付物。
- `next`: 剩余 blocker、下一步、需要用户/主控做什么。

在远端项目目录中拉取 dashboard TODO 到 `TASK.md`：

```bash
cd ~/workspace/<project-repo-or-folder>
clawcross-harness-agent task-md export --project-id <project-id> --path TASK.md
```

编辑 `TASK.md` 里每个 task 的 `update` JSON 字段后，把状态和日志写回 ClawCross harness：

```bash
clawcross-harness-agent task-md import --path TASK.md
```

如果需要一条命令完成“先导入本地 `TASK.md` 更新，再从 dashboard 刷新任务列表”：

```bash
clawcross-harness-agent task-md sync --project-id <project-id> --path TASK.md
```

本机也可以直接同步任意 `TASK.md`，用于测试或从远端拉回文件后处理：

```bash
cd $CLAWCROSS_REPO
python3 scripts/sync_task_md.py \
  --project-id <project-id> \
  --task-md /path/to/TASK.md \
  --direction both
```

双向同步规则：

- dashboard → TASK.md: dashboard/status/TODO/comment 会被渲染进 `TASK.md` 的托管 JSON block。
- TASK.md → dashboard: `update.status` 和 lifecycle 字段会先写入私有 harness，再由 ClawCross 同步成 dashboard status/comment。
- `TASK.md` 上半部分是给人和 agent 快速阅读的项目工作日志，会展示每个 TODO 的描述和最新 comment；下半部分托管 JSON block 是双向同步的唯一机器源。
- `TASK.md` 不保存 Claude session id、remote-control link、token、密码或 harness runtime state。
- 导入后会重写托管 block，清空已消费的 `update.*` 字段，避免重复 comment。

## 7. 项目绑定和并行 session 规则

- 一个远端电脑默认服务一个主项目。
- 一个任务可以绑定一个 worker；同一项目多个 TODO 可以开多个 session 并行。
- 没有 TODO 时，每台电脑只保留一个 standby session。
- 已提交待审查的 TODO 不要自动删除对应 session；等主机验收或用户审查。
- 不可聊天、remote-control 断开且没有可用 bridge/session id 的 session 可以归档删除。
- session 标题格式由 conductor 管理：`<Project Label> | <Remote Hostname> [| <Task Label>]`。
- 扫描到的普通 Claude Code session 不等于 harness worker。Project Harness 只接管已经有 `clawcross-harness-agent heartbeat` 且 `session_ref` 能匹配真实 Claude session 的 worker。
- 其他人手动打开的 Claude Code session 会在 Remote Claude 列表里标为“未接管”，conductor 不会自动分配 TODO、不会自动发送消息、不会改名、不会清理。
- 只有在明确需要旧式自动接管时，才可临时设置 `CLAWCROSS_HARNESS_AUTOBIND_UNBOUND_SESSIONS=1`。默认不要开启，避免误接管共享账号或他人正在使用的 session。

## 8. 前端验证

打开：

```text
http://127.0.0.1:51209/mobile/group_chat
```

Project Harness 应显示：

- 项目卡片数符合当前主项目数
- 每个项目卡片上方是 TODO 横向列表
- 下方是 Workers 横向列表
- `worker 在线` 只在真实 Claude session 和 harness heartbeat 都存在时计数
- `0 失联` 表示没有 stale worker

点击 worker 卡片后，右侧 transcript 应能显示对应远端 Claude session 内容。可用一条无副作用消息测试：

```text
ClawCross 连通性测试：请只回复「收到：<session-id> 在线」，不要执行命令，不要修改文件。
```

## 9. Dashboard 同步边界

允许同步到 dashboard：

- TODO status
- task comments
- result summary
- human-readable evidence

禁止同步到 dashboard：

- Claude session id
- remote-control URL
- `INTERNAL_TOKEN`
- SSH/Tailscale credentials
- harness heartbeat/runtime state
- Claude settings 或 CLAUDE.md 管理块

ClawCross conductor 可以把 harness 中的 TODO 状态和 comment 同步回 dashboard；dashboard 不负责保存 worker runtime。

## 10. 常见故障

### SSH 连接不上

在远端检查：

```bash
tailscale ip -4
sudo systemctl status ssh --no-pager
sudo ss -ltnp | grep ':22'
tailscale debug prefs | grep -i shield || true
sudo ufw status verbose || true
```

常见原因：

- SSH service 没启动
- Tailscale shields-up 开着
- UFW 没允许 `tailscale0` 进 22
- 本机记录的 SSH user 不对

### 新电脑没有出现在前端

检查本机：

```bash
tailscale status
cat ~/.clawcross/data/remote_claude_targets.json
```

如果 Tailscale hostname 不能推断正确用户，重新运行：

```bash
python3 scripts/configure_remote_claude_dashboard.py <remote-user>@<remote-tailscale-ip> --default-project-id <project-id> --no-batch-mode
```

### `clawcross-harness-agent: command not found`

远端使用全路径：

```bash
~/.local/bin/clawcross-harness-agent heartbeat ...
```

或者重新运行配置脚本，确保 `~/.local/bin` 已加入 shell 环境。

### Harness heartbeat 失败

先验证 reverse tunnel：

```bash
ssh <remote-user>@<remote-tailscale-ip> \
  'curl -fsSL http://127.0.0.1:51200/v1/models'
```

失败时重启本机 tunnel：

```bash
tmux kill-session -t clawcross-tunnel-<short-host> 2>/dev/null || true
tmux new-session -d -s clawcross-tunnel-<short-host> \
  'ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -R 127.0.0.1:51200:127.0.0.1:51200 <remote-user>@<remote-tailscale-ip>'
```

### Claude Agents 里看不到 ClawCross session

- 交互 Claude 需要 `/remote-control` 或通过 `~/.local/bin/clawcross-claude` 启动。
- Claude Agents 后台 session 需要从 `claude agents` 创建；它是 `kind=bg`，不一定是交互 terminal session。
- ClawCross 绑定时优先使用带 `bridge_session_id=session_...` 的后台/remote-control session。

### 前端显示 session 失联

检查两件事必须同时存在：

1. 真实 Claude session 仍在 `~/.claude/sessions/*.json`。
2. 对应 agent 的 `last_heartbeat_at` 没超过 stale 阈值。

补 heartbeat：

```bash
~/.local/bin/clawcross-harness-agent heartbeat \
  --agent-id "<agent-id>" \
  --project-id "<project-id>" \
  --status idle \
  --session-ref "<remote-user>@<remote-tailscale-ip>::session_..." \
  --remote-host "<remote-user>@<remote-tailscale-ip>" \
  --worktree "$HOME/workspace/<project-repo-or-folder>" \
  --message "Heartbeat refreshed."
```
