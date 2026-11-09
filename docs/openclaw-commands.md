# OpenClaw 常用配置命令速查

## Clawcross 启动时会做什么

从现在起，`bash selfskill/scripts/run.sh start`、`bash selfskill/scripts/run.sh start-foreground`，以及 PowerShell 对应命令，在检测到本机已安装 OpenClaw 后，会在拉起 Clawcross 服务前自动做一轮 runtime 预热：

- 尝试启动 OpenClaw gateway
- 确保 `gateway.http.endpoints.chatCompletions.enabled=true`
- 刷新 `OPENCLAW_API_URL`、`OPENCLAW_GATEWAY_TOKEN`、`OPENCLAW_SESSIONS_FILE`
- 轻量修复缺失 transcript 的 sessions 坏索引

这一步只处理 OpenClaw runtime 联通性，不会静默把 OpenClaw 的 LLM provider / model / key 覆盖到 Clawcross。LLM 导入仍然走首次登录向导或 `check-openclaw`。

## Web 入口

- OpenClaw Dashboard / Control UI：`http://127.0.0.1:18789/`
- OpenClaw OpenAI 兼容 API：`http://127.0.0.1:18789/v1/chat/completions`
- 如果本机实际 gateway 端口不是 `18789`，请以 `openclaw gateway status` 输出为准
- 如果 Dashboard 提示 `gateway token missing`：
  - 粘贴 `OPENCLAW_GATEWAY_TOKEN` 到 Control UI settings，或
  - 在仅本机 `127.0.0.1` 使用时改成无鉴权：
    `openclaw config set gateway.auth.mode none`
    `openclaw config unset gateway.auth.token`
    `openclaw gateway restart`

## Clawcross / OpenClaw LLM 双向同步

### OpenClaw -> Clawcross

已有的导入路径仍然有效：

- 前端首次登录向导里的 `🦞 从 OpenClaw 导入`
- `bash selfskill/scripts/run.sh check-openclaw`
- `powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 check-openclaw`

它们会自动探测 OpenClaw 的 gateway 地址、默认模型和 provider，并尽量写回 Clawcross 的 `config/.env`。

### Clawcross -> OpenClaw

现在也支持把 Clawcross 当前 LLM 配置反向写回 OpenClaw 默认模型：

```bash
bash selfskill/scripts/run.sh sync-openclaw-llm
```

```powershell
powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 sync-openclaw-llm
```

同步内容包括：

- `models.providers.<provider>.baseUrl`
- `models.providers.<provider>.apiKey`
- `models.providers.<provider>.models[]`
- `agents.defaults.model.primary`

如果 OpenClaw gateway 正在运行，脚本会在写入后自动尝试重载 gateway。

### 自动同步触发规则

`selfskill/scripts/configure.py` 现在会在 OpenClaw 已安装、且 Clawcross LLM 配置完整时尝试自动同步，但只在“足够安全”的情况下触发：

- 更新了 `LLM_MODEL` 或 `LLM_PROVIDER`
- 或者一次批量更新了 `LLM_API_KEY + LLM_BASE_URL + LLM_MODEL`

如果你只改了 `LLM_API_KEY` 或 `LLM_BASE_URL`，脚本会故意停住并打印提示，不会把半成品 provider 切换写回 OpenClaw。

### OpenAI provider 的隐藏坑

OpenClaw 的 `openai` provider 运行时可能优先读取 `env.OPENAI_API_KEY`，而不是只看 `models.providers.openai.apiKey`。

因此 Clawcross 反向同步到 OpenAI provider 时，会同时写两处：

- `models.providers.openai.apiKey`
- `env.OPENAI_API_KEY`

否则会出现“配置文件看起来已同步，但 OpenClaw 实际仍在用旧 key”的假同步问题。

## 疑难杂症总结

### 1. Windows PowerShell 执行策略拦住 `openclaw`

现象：

- 直接执行 `openclaw channels login ...` 报 `PSSecurityException`

原因：

- PowerShell 优先命中 `openclaw.ps1`

解决：

```powershell
openclaw.cmd channels login --channel openclaw-weixin
cmd /c openclaw channels login --channel openclaw-weixin
```

### 2. 官方微信一键安装器在 Windows 可能失败

现象：

- `npx -y @tencent-weixin/openclaw-weixin-cli@latest install` 失败

原因：

- 安装器内部会调用 `which openclaw`，PowerShell/Windows 下可能不存在这条命令

解决：

```powershell
openclaw.cmd plugins install "@tencent-weixin/openclaw-weixin"
openclaw.cmd config set plugins.entries.openclaw-weixin.enabled true
openclaw.cmd channels login --channel openclaw-weixin
openclaw.cmd channels list --json
openclaw.cmd gateway restart
```

### 3. 重新跑 `openclaw onboard` 后先检查 3 件事

`openclaw onboard` 通常不会清掉已有微信绑定，但可能顺手改掉：

- gateway 鉴权模式
- 默认模型
- provider 配置

建议每次重跑后立刻检查：

```powershell
openclaw.cmd gateway status
openclaw.cmd models status --json
uv run scripts/cli.py openclaw bindings --agent main
```

### 4. 从 OpenAI 切到 DeepSeek 不能只换 key

如果只替换 API key，而不改模型和 base URL，Clawcross / OpenClaw 仍会继续按 OpenAI 配置运行。

#### Clawcross 侧

```bash
bash selfskill/scripts/run.sh configure --batch \
  LLM_API_KEY=<deepseek_key> \
  LLM_BASE_URL=https://api.deepseek.com \
  LLM_MODEL=deepseek-chat \
  LLM_PROVIDER=deepseek
bash selfskill/scripts/run.sh stop && bash selfskill/scripts/run.sh start
```

#### OpenClaw 侧

OpenClaw **没有内置的 `deepseek` provider**，也不能通过 `openclaw config set providers.custom.deepseek...` 添加（会报 schema 错误）。正确方式是在 `~/.openclaw/openclaw.json` 中添加 `models.providers.deepseek` 自定义 provider 配置，参考 vLLM provider 的方式：

```json5
{
  "models": {
    "providers": {
      "deepseek": {
        "baseUrl": "https://api.deepseek.com/v1",   // 注意末尾 /v1
        "apiKey": "<deepseek_key>",
        "api": "openai-completions",                 // DeepSeek API 兼容 OpenAI
        "models": [
          {
            "id": "deepseek-chat",
            "name": "DeepSeek Chat (V3)",
            "reasoning": false,
            "input": ["text"],
            "contextWindow": 65536,
            "maxTokens": 8192
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": { "primary": "deepseek/deepseek-chat" }
    }
    // ... 其他已有配置保持不变
  }
}
```

配置后验证：

```bash
openclaw config validate                  # 应输出 Config valid
openclaw models list                      # 应看到 deepseek/deepseek-chat (Auth: yes)
openclaw models status                    # 确认 Default: deepseek/deepseek-chat
openclaw gateway restart                  # 重启 gateway 加载新配置
```

> **踩坑记录**：
> - `openclaw config set models.providers.deepseek.baseUrl ...` 会报 `models: Invalid input: expected array, received undefined`，因为 schema 要求 `models` 数组同时存在。只能直接编辑 JSON 一次性写入整个 provider 块。
> - `openrouter/deepseek/deepseek-chat` 在模型列表中存在，但它走的是 OpenRouter 转发，需要 OpenRouter API key，不是 DeepSeek 原生 key。
> - DeepSeek `baseUrl` 必须带 `/v1` 后缀（`https://api.deepseek.com/v1`），OpenClaw 的 `openai-completions` API 会在此基础上拼接 `/chat/completions`。

DeepSeek 这把 key 当前实测可用模型：

- `deepseek-chat` (V3)
- `deepseek-reasoner` (R1)

### 4.1 从 DeepSeek/OpenAI 切到 Antigravity-Manager（本地反代，免费使用模型）

[Antigravity-Manager](https://github.com/lbjlaq/Antigravity-Manager) 是一个本地反向代理，在 `http://127.0.0.1:8045` 提供 OpenAI 兼容 API，API Key 统一为 `sk-antigravity`。它通过用户已有的 **Google One Pro 会员**（例如通过学生/教育验证获得）来免费访问 Claude / Gemini / GPT 等 67+ 模型，无需额外付费购买 API。

**前提**：用户需拥有 Google One Pro 会员资格，且 Antigravity-Manager 已安装并运行。安装方式：

```bash
# Linux / macOS 一键安装
curl -fsSL https://raw.githubusercontent.com/lbjlaq/Antigravity-Manager/v4.1.31/install.sh | bash
# macOS 启动
open -a "Antigravity Tools"
# 验证
curl -s http://127.0.0.1:8045/v1/models | head -c 200
```

#### Clawcross 侧

```bash
bash selfskill/scripts/run.sh configure --batch \
  LLM_API_KEY=sk-antigravity \
  LLM_BASE_URL=http://127.0.0.1:8045 \
  LLM_MODEL=gemini-3.1-pro \
  LLM_PROVIDER=antigravity
bash selfskill/scripts/run.sh stop && bash selfskill/scripts/run.sh start
```

#### OpenClaw 侧

同样在 `~/.openclaw/openclaw.json` 中添加 `models.providers.antigravity` 自定义 provider：

```json5
{
  "models": {
    "providers": {
      "antigravity": {
        "baseUrl": "http://127.0.0.1:8045/v1",   // 注意末尾 /v1
        "apiKey": "sk-antigravity",
        "api": "openai-completions",               // Antigravity 兼容 OpenAI
        "models": [
          {
            "id": "gemini-3.1-pro",
            "name": "Gemini 3.1 Pro (via Antigravity)",
            "reasoning": false,
            "input": ["text", "image"],
            "contextWindow": 2097152,
            "maxTokens": 65536
          },
          {
            "id": "claude-opus-4-6",
            "name": "Claude Opus 4.6 (via Antigravity)",
            "reasoning": true,
            "input": ["text", "image"],
            "contextWindow": 200000,
            "maxTokens": 32000
          },
          {
            "id": "gemini-3-flash",
            "name": "Gemini 3 Flash (via Antigravity)",
            "reasoning": false,
            "input": ["text", "image"],
            "contextWindow": 1048576,
            "maxTokens": 65536
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": { "primary": "antigravity/gemini-3.1-pro" }
    }
    // ... 其他已有配置保持不变
  }
}
```

配置后验证：

```bash
openclaw config validate                  # 应输出 Config valid
openclaw models list                      # 应看到 antigravity/gemini-3.1-pro (Auth: yes)
openclaw models status                    # 确认 Default: antigravity/gemini-3.1-pro
openclaw gateway restart                  # 重启 gateway 加载新配置
```

> **踩坑记录**：
> - Antigravity 的 `baseUrl` 必须带 `/v1` 后缀（`http://127.0.0.1:8045/v1`），OpenClaw 的 `openai-completions` API 会在此基础上拼接 `/chat/completions`。
> - Antigravity-Manager 默认固定在端口 8045，如果端口被占用需要先停掉占用进程。
> - `sk-antigravity` 是 Antigravity-Manager 的固定 API Key，不需要注册或申请额外的 API 密钥。模型访问通过用户已有的 Google One Pro 会员实现，完全免费。
> - Antigravity 转发的 Gemini/Claude/GPT 模型名会被 Clawcross 的 `_is_vision_model()` 自动识别为视觉模型，无需额外设置 `LLM_VISION_SUPPORT`。
> - `LLM_PROVIDER=antigravity` 在 Clawcross 内部会映射到 `openai` 兼容格式（通过 `llm_factory.py` 的 `_PROVIDER_ALIASES`）。
> - **OpenClaw gateway auth=token 会导致 HTTP 401**：配置完 Antigravity provider 后，如果 `curl` 测试 `http://127.0.0.1:18789/v1/chat/completions` 返回 401，说明 gateway 鉴权模式为 `token`。仅本地使用时建议关闭鉴权：
>   ```bash
>   openclaw config set gateway.auth.mode none
>   openclaw config unset gateway.auth.token
>   openclaw gateway restart
>   ```
>   验证方式：`openclaw config get gateway.auth` 应显示 `"mode": "none"`。

Antigravity 常用推荐模型（均通过 Google One Pro 会员免费使用）：

- `gemini-3.1-pro` — 最新 Gemini，2M context，推荐首选
- `claude-opus-4-6` — Claude 最强推理模型
- `gemini-3-flash` — 快速且经济
- `gpt-4o` — OpenAI 旗舰
- `claude-sonnet-4-6` — Claude 平衡之选

### 4.2 从 DeepSeek/OpenAI 切到 MiniMax（云端 API）

[MiniMax](https://platform.minimaxi.com/) 提供 OpenAI 兼容 API，端点为 `https://api.minimaxi.com/v1`。主力模型 `MiniMax-M2.7` 支持 1M context window 和推理能力。

**前提**：在 [MiniMax 平台](https://platform.minimaxi.com/) 注册并获取 API Key（格式 `sk-api-...`），确保账户有余额。

#### Clawcross 侧

```bash
bash selfskill/scripts/run.sh configure --batch \
  LLM_API_KEY=<minimax_api_key> \
  LLM_BASE_URL=https://api.minimaxi.com \
  LLM_MODEL=MiniMax-M2.7 \
  LLM_PROVIDER=minimax
bash selfskill/scripts/run.sh stop && bash selfskill/scripts/run.sh start
```

#### OpenClaw 侧

同样在 `~/.openclaw/openclaw.json` 中添加 `models.providers.minimax` 自定义 provider：

```json5
{
  "models": {
    "providers": {
      "minimax": {
        "baseUrl": "https://api.minimaxi.com/v1",   // 注意末尾 /v1
        "apiKey": "<minimax_api_key>",
        "api": "openai-completions",                 // MiniMax API 兼容 OpenAI
        "models": [
          {
            "id": "MiniMax-M2.7",
            "name": "MiniMax M2.7",
            "reasoning": true,
            "input": ["text"],
            "contextWindow": 1048576,
            "maxTokens": 16384
          },
          {
            "id": "MiniMax-M2.7-highspeed",
            "name": "MiniMax M2.7 Highspeed",
            "reasoning": false,
            "input": ["text"],
            "contextWindow": 1048576,
            "maxTokens": 16384
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": { "primary": "minimax/MiniMax-M2.7" }
    }
    // ... 其他已有配置保持不变
  }
}
```

配置后验证：

```bash
openclaw config validate                  # 应输出 Config valid
openclaw models list                      # 应看到 minimax/MiniMax-M2.7 (Auth: yes)
openclaw models status                    # 确认 Default: minimax/MiniMax-M2.7
openclaw gateway restart                  # 重启 gateway 加载新配置
```

> **踩坑记录**：
> - MiniMax 的 `baseUrl` 必须带 `/v1` 后缀（`https://api.minimaxi.com/v1`），OpenClaw 的 `openai-completions` API 会在此基础上拼接 `/chat/completions`。
> - Clawcross 的 `LLM_BASE_URL` 可以不带路径（例如 `https://api.minimaxi.com`），因为 minimax 分支会在 baseUrl 仅包含 host 时自动补 `/anthropic`（通过 `llm_factory.py` 的 `_normalize_minimax_base_url`）。
> - `LLM_PROVIDER=minimax` 现在走 minimax 独立分支，并使用 `ChatAnthropic` 来发请求。
> - MiniMax API 错误码中 `billing error` 表示余额不足，需要在 [MiniMax 平台](https://platform.minimaxi.com/) 充值。
> - MiniMax 模型名区分大小写：必须用 `MiniMax-M2.7` 而非 `minimax-m2.7`。

MiniMax 当前可用模型：

- `MiniMax-M2.7` — 旗舰推理模型，1M context
- `MiniMax-M2.7-highspeed` — 高速版本，适合低延迟场景

### 5. `openclaw gateway status` 可能有误导性告警

即使 Dashboard 和 HTTP API 都可用，`openclaw gateway status` 仍可能打印 RPC probe failure。

排查时优先看真实入口是否可用：

- 浏览器打开 `http://127.0.0.1:18789/`
- 直接请求 `http://127.0.0.1:18789/v1/chat/completions`
- `openclaw.cmd channels list --json`

### 6. Clawcross 的 `53000` 接口不是裸开放调试口

现象：

- 直接 POST `http://127.0.0.1:53000/v1/chat/completions` 返回 `认证失败`

说明：

- 这是 Clawcross 自己的鉴权，不等于 LLM provider 配置错了

更稳的验证方式：

```powershell
powershell -ExecutionPolicy Bypass -File selfskill/scripts/run.ps1 status
```

或者在仓库环境里直接验证 `llm_factory`：

```powershell
@'
from dotenv import load_dotenv
load_dotenv('config/.env')
from src.services.llm_factory import create_chat_model, extract_text
resp = create_chat_model(max_tokens=8).invoke('Reply with OK only.')
print(extract_text(resp.content))
'@ | uv run python -
```

## 一、Agent-to-Agent 通信配置

### 1. 查看当前配置
```bash
openclaw config get tools.sessions
openclaw config get tools.agentToAgent
```

### 2. 配置会话可见性
```bash
openclaw config patch --json '{"tools": {"sessions": {"visibility": "all"}}}'
```

### 3. 配置 agent 通信白名单
```bash
openclaw config patch --json '{"tools": {"agentToAgent": {"enabled": true, "allow": ["main", "agent1"]}}}'
```

### 4. 一键配置（推荐）
```bash
openclaw config patch --json '{"tools": {"sessions": {"visibility": "all"}, "agentToAgent": {"enabled": true, "allow": ["main", "agent1"]}}}' && openclaw gateway restart
```

### 5. 验证与重启
```bash
openclaw config get tools.agentToAgent
openclaw gateway restart
```

### 6. 测试跨 agent 通信
```bash
openclaw agent --agent main --message "测试消息"
openclaw sessions --all-agents
```

---

## 二、Channel 绑定配置

### Windows + 微信插件安装坑

在 Windows PowerShell 上有两个常见坑：

1. `openclaw` 可能先命中 `openclaw.ps1`，如果本机执行策略较严，会直接报 `PSSecurityException`
2. 官方一键安装器
   `npx -y @tencent-weixin/openclaw-weixin-cli@latest install`
   在 Windows 上可能失败，因为它内部调用了 `which openclaw`

推荐的 Windows 手动流程是：

```powershell
openclaw.cmd plugins install "@tencent-weixin/openclaw-weixin"
openclaw.cmd config set plugins.entries.openclaw-weixin.enabled true
openclaw.cmd channels login --channel openclaw-weixin
openclaw.cmd channels list --json
openclaw.cmd gateway restart
```

状态判断：

- `openclaw status` 显示 `openclaw-weixin | ON | SETUP | no token`
  说明插件已经安装成功，但扫码登录还没完成

扫码完成后，通常会得到一个 `bind_key`，例如：

```text
openclaw-weixin:cdb0be1f7414-im-bot
```

### 1. 查看当前绑定
```bash
openclaw config get bindings
```

### 2. 设置绑定（推荐）
```bash
# 绑定 Telegram 到 agent1
openclaw config set bindings '[{"type": "route", "agentId": "agent1", "match": {"channel": "telegram"}}]'

# 绑定多个频道
openclaw config set bindings '[{"type": "route", "agentId": "main", "match": {"channel": "telegram"}}, {"type": "route", "agentId": "agent1", "match": {"channel": "webchat"}}]'

# 绑定特定账号
openclaw config set bindings '[{"type": "route", "agentId": "main", "match": {"channel": "telegram", "accountId": "main-bot"}}]'
```

### 3. 重启生效
```bash
openclaw gateway restart
```

### 4. 查看路由
```bash
openclaw agents list --bindings
```

### 5. 绑定微信账号到 OpenClaw agent

```bash
# 先查看微信 channel account / bind_key
uv run scripts/cli.py openclaw channels

# 再把微信账号绑定到 main agent
uv run scripts/cli.py openclaw bind --data '{"agent":"main","channel":"openclaw-weixin:cdb0be1f7414-im-bot"}'

# 验证绑定
uv run scripts/cli.py openclaw bindings --agent main
```

Windows PowerShell 若直接敲 `openclaw` 被执行策略拦住，请改用：

```powershell
openclaw.cmd channels login --channel openclaw-weixin
openclaw.cmd agents bind --agent main --bind openclaw-weixin:cdb0be1f7414-im-bot
openclaw.cmd agents list --bindings --json
```

---

## 三、常用命令速查表

| 用途 | 命令 |
|------|------|
| 查看绑定 | `openclaw config get bindings` |
| 设置绑定 | `openclaw config set bindings '[...]'` |
| 查看 agent 列表 | `openclaw agents list` |
| 查看路由 | `openclaw agents list --bindings` |
| 查看会话 | `openclaw sessions --all-agents` |
| 重启网关 | `openclaw gateway restart` |
| 查看日志 | `openclaw logs --tail 100` |

---

## 四、配置参数说明

### visibility 取值
- `"tree"`: 只能看到当前会话和创建的 subagent（默认）
- `"self"`: 只能看到当前会话
- `"agent"`: 能看到当前 agent 的所有会话
- `"all"`: 能看到所有 agent 的所有会话

### bindings 匹配条件
| 字段 | 说明 | 必填 |
|------|------|------|
| `type` | 固定为 `"route"` | 是 |
| `agentId` | 目标 agent ID | 是 |
| `channel` | 频道类型（telegram/webchat 等） | 是 |
| `accountId` | 账号 ID | 可选 |
| `peerId` | 用户 ID | 可选 |
| `groupId` | 群组 ID | 可选 |

---

## 五、注意事项

1. **配置后需重启**：所有配置修改后需执行 `openclaw gateway restart`
2. **agent ID 格式**：区分大小写和符号，如 `agent1` ≠ `raw_api`
3. **安全性**：`visibility="all"` 会暴露所有会话，确保安全环境使用
4. **匹配优先级**：按 `bindings` 数组顺序匹配，第一个匹配生效

---

## 六、外部 Agent 配置

### 1. 查看已有 OpenClaw Agent

```bash
uv run scripts/cli.py -u <username> openclaw sessions
```

### 2. 读取 Agent 的 IDENTITY 文件

**步骤 1: 查看 agent 详情获取 workspace 路径**
```bash
uv run scripts/cli.py -u <username> openclaw detail --name <agent_name>
```

**步骤 2: 查看 workspace 中的文件列表**
```bash
uv run scripts/cli.py -u <username> openclaw workspace-files --workspace <workspace_path>
```

**步骤 3: 读取具体文件（如 IDENTITY.md）**
```bash
uv run scripts/cli.py -u <username> openclaw workspace-file-read \
  --workspace <workspace_path> \
  --filename IDENTITY.md
```

### 3. 创建新的 OpenClaw Agent

```bash
uv run scripts/cli.py -u <username> openclaw add --data '<JSON_DATA>'
```

**参数说明:**

| 参数 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | Agent 名称，全局唯一（只能包含字母、数字、下划线和连字符） |
| `workspace` | 可选 | 自定义工作区路径（不指定则自动生成） |

**示例 - 创建基础 agent:**
```bash
uv run scripts/cli.py -u Avalon_01 openclaw add --data '{"name": "my_new_agent"}'
```

**示例 - 指定自定义工作区:**
```bash
uv run scripts/cli.py -u Avalon_01 openclaw add \
  --data '{"name": "my_new_agent", "workspace": "/custom/path"}'
```

### 4. 修改 Agent 配置

```bash
uv run scripts/cli.py -u <username> openclaw update-config \
  --data '<JSON_CONFIG_WITH_AGENT_NAME>'
```

**基本配置示例：**
```bash
uv run scripts/cli.py -u Avalon_01 openclaw update-config \
  --data '{
    "agent_name": "my_new_agent",
    "temperature": 0.7,
    "model": "gpt-4o-mini"
  }'
```

**关闭所有 Tool 权限：**

使用 `tools.deny: ["*"]` 可以禁止 Agent 调用所有工具，仅保留纯文本对话能力：
```bash
uv run scripts/cli.py -u Avalon_01 openclaw update-config \
  --data '{
    "agent_name": "my_new_agent",
    "tools": {
      "deny": ["*"]
    }
  }'
```

配置说明：
- `tools.profile`: 工具配置文件路径（可选）
- `tools.alsoAllow`: 额外允许的工具列表（可选）
- `tools.deny`: 禁止的工具列表，`["*"]` 表示禁止所有工具

**验证配置：**
```bash
uv run scripts/cli.py -u Avalon_01 openclaw detail --name my_new_agent
```

### 5. 常用文件操作命令速查

| 用途 | 命令 |
|------|------|
| 查看所有 sessions | `openclaw sessions` |
| 查看 agent 详情 | `openclaw detail --name <agent_name>` |
| 查看 workspace 文件列表 | `openclaw workspace-files --workspace <path>` |
| 读取具体文件 | `openclaw workspace-file-read --workspace <path> --filename <file>` |
| 创建新 agent | `openclaw add --agent-name <name> ...` |
| 修改 agent 配置 | `openclaw update-config --agent <name> ...` |

---

## 七、注意事项

1. **配置后需重启**：所有配置修改后需执行 `openclaw gateway restart`
2. **agent ID 格式**：区分大小写和符号，如 `agent1` ≠ `raw_api`
3. **安全性**：`visibility="all"` 会暴露所有会话，确保安全环境使用
4. **匹配优先级**：按 `bindings` 数组顺序匹配，第一个匹配生效

---
*文档版本：v2.6 | 更新时间：2026-04-01*
