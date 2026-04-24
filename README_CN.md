# Clawcross

**[English README](./README.md)**

<p align="center">
  <img src="docs/poster.jpg" alt="Clawcross Poster" width="360" />
</p>

> **一个本地 AI 工作台：多位专家 Agent 协作、辩论、执行——自带可视化工作流引擎、长期记忆、一键公网访问。**

## Clawcross 是什么？

Clawcross 把一个单聊机器人变成**可编程的多专家系统**。你创建一个 **Team**（一组拥有不同角色和人设的 AI Agent），让它们通过可视化工作流协作完成任务。每次讨论都会沉淀为**持续演化的知识图谱**，跨会话保留。

**核心概念一览：**

| 术语 | 含义 |
|------|------|
| **Team** | 一组内部 Agent、外部 Agent 和人设的协作组合 |
| **OASIS** | 可视化工作流引擎，编排多专家讨论（顺序、并行、分支、DAG） |
| **OASIS Town** | Studio 侧栏的像素小镇可视化，实时观看讨论、查看 swarm 图谱 |
| **WeBot** | 类 Claude Code 的委托运行时：角色化子 Agent、计划/待办/验证、审批感知工具策略 |
| **GraphRAG** | 从每次讨论中构建的活知识图谱，本地 SQLite 存储（可选镜像到 Zep） |
| **Team 预设** | 15 个开箱即用的专家团队——战略分析、内容创作、科技领袖等——一键安装 |
| **ACP (acpx)** | Agent 客户端协议，用于与外部 AI Agent 通信（OpenClaw、Codex、Claude、Gemini、Aider） |
| **OpenClaw** | 可集成到 Team 中的外部 Agent 运行时 |

## 产品视频

<p align="center">
  <a href="https://youtube.com/shorts/OKuZNwz-CP0">
    <img src="./docs/media/clawcross-demo-poster.jpg" alt="Clawcross 演示视频" width="360" />
  </a>
</p>

<p align="center">
  <a href="https://youtube.com/shorts/OKuZNwz-CP0">点击在 YouTube 观看 Clawcross 演示视频</a>
</p>

## 快速开始

### 环境要求

- **Python 3.11+**
- **Node.js 18+**（用于 acpx 和前端构建）
- **Git**
- macOS / Linux / Windows（WSL 或 PowerShell）

### 通过 AI Code CLI 安装

在 **Codex**、**Cursor**、**Claude Code**、**CodeBuddy**、**Trae** 之类的 AI 编码助手里输入：

```text
Clone https://github.com/ClawCross/ClawCross.git，读取 AGENTS.md，然后安装 Clawcross。
```

### 手动安装

<details>
<summary>点击展开手动安装步骤</summary>

**最快启动方式**

```bash
bash run.sh start
```

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1 start
```

启动后访问 `http://127.0.0.1:51209`。

**停止 / 查看状态**

```bash
bash run.sh status
bash run.sh stop
```

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1 status
powershell -ExecutionPolicy Bypass -File .\run.ps1 stop
```

**说明**

- `run.sh` / `run.ps1` 是项目根目录下的转发入口，实际仍调用 `selfskill/scripts/` 里的正式脚本。
- `start` 依然会自动处理 venv、依赖、`acpx`、`.env` 初始化和服务启动。
- 如果 `LLM_MODEL` 为空，启动现在会提前停止并提示你先配置，而不是拉起一个看似在线但 Agent 不工作的服务。

**原有完整路径命令仍然可用**

```bash
bash selfskill/scripts/run.sh start
bash selfskill/scripts/run.sh status
bash selfskill/scripts/run.sh stop
```

```powershell
powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 start
powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 status
powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 stop
```

</details>

完整安装指南（OpenClaw、Antigravity、MiniMax、WSL、手动 CLI 配置、故障排查）请参见 [`SKILL.md`](./SKILL.md)。

## 为什么是 Clawcross

### 多专家协作，不只是聊天

- **Team 统一编排** — 将内部 Agent、OpenClaw Agent、外部 API Agent 组合成单一 Team，支持一键导入导出
- **15 个内置 Team 预设** — LLM 顾问团、女娲全明星、内容帝国、战略分析团、科技巨头等——安装即用
- **自带 AI 团队构建器** — ClawCross Creator 发现 SOP 页面、用 TinyFish 抽取角色，生成可编辑的人设和 DAG 工作流
- **可视化编排** — 在 OASIS 中设计顺序、并行、分支或 DAG 风格的专家协作工作流

### 类 Claude Code 的委托能力：WeBot

- **角色化子 Agent** — 通用、研究、规划、编码、审阅、验证等多种模式
- **持久化状态** — 运行/任务生命周期、计划/待办/验证原语、上下文压缩、产物日志
- **审批感知工具策略** — 配置哪些工具需要人工审批，支持事件日志和钩子
- **桥接会话** — WeBot 运行时与 UI 之间的实时 WebSocket 连接

### 长期记忆与实时观战

- **GraphRAG** — 每次讨论沉淀为活知识图谱，本地 SQLite 存储，可选镜像到 Zep
- **OASIS Town** — 在像素小镇可视化中观看讨论展开、查看 swarm 图谱、实时插入 nudge
- **ReportAgent** — 追问"为什么这样预测"，返回带图谱证据的解释

### 完整的运维能力

- **OpenAI 兼容 API** — 本地 `/v1/chat/completions` 端点，兼容标准客户端和 MCP 工具
- **Bot 集成** — Telegram / QQ，支持白名单访问控制
- **多模态 I/O** — 图片、文件、语音输入、TTS
- **自动化** — 定时任务、长流程工作流执行
- **TinyFish 搜索代理** — 基于 TinyFish Web Agent API 的互联网搜索代理
- **远程访问** — Cloudflare Tunnel + token / 密码登录
- **Flow 分发** — 通过 [ClawcrossHub](https://clawcross.net) 浏览和分享工作流

## 现在已经能做什么

| 能力 | 价值 |
|---|---|
| **OpenAI 兼容 API** | 给应用、脚本、客户端提供本地模型入口 |
| **Web UI** | 聊天、设置、OASIS 面板、群聊、隧道控制、WeBot 运行面板 |
| **Team 预设** | 15 个开箱即用的专家团队——安装即可开始协作 |
| **ClawCross Creator** | 把任务描述或 SOP 页面转换成角色、人设和 OASIS 工作流 |
| **OASIS 工作流** | 顺序、并行、分支、DAG 风格的专家编排 |
| **OASIS Town** | 像素小镇可视化 + swarm 图谱 + 实时 nudge |
| **WeBot 运行时** | 类 Claude Code 委托：角色、模式、工具策略、桥接会话 |
| **GraphRAG 长期记忆** | 本地 SQLite + 可选 Zep 镜像 |
| **ReportAgent** | 带图谱证据的预测解释和决策支持 |
| **MCP 工具** | 内置命令、文件、会话、搜索、调度、OASIS、WeBot、LLM API 工具 |
| **Team 系统** | 公共/私有 Agent、人设、Workflow、Team 快照、导入导出 |
| **ACP 通信** | 通过 Agent 客户端协议与 OpenClaw、Codex、Claude、Gemini、Aider 通信 |
| **多模态 I/O** | 图片、文件、语音输入、TTS |
| **Bot 集成** | Telegram / QQ |
| **自动化** | 定时任务、长流程工作流执行 |
| **TinyFish 搜索代理** | 互联网搜索代理，支持网页抓取与结构化数据提取 |
| **Flow 分发平台** | 通过 [ClawcrossHub](https://clawcross.net) 浏览和分享 |
| **远程访问** | Cloudflare Tunnel + token / 密码登录 |

## Flow 分发平台

**[ClawcrossHub](https://clawcross.net)** 是配套的 Flow 分发平台：浏览、分发和分享 Clawcross Flows。

## 典型使用场景

- **本地 AI 工作台** — 浏览器里直接用，也能给其他工具当 OpenAI 兼容后端
- **多专家讨论与执行** — 让多个专家相互挑战、补充、汇总结论
- **开箱即用的专家顾问团** — 安装预设团队（LLM 顾问团、战略分析、内容创作）直接使用
- **实时观战与插话** — 在 Town 侧栏用像素小镇观察讨论进展并插入 prompt
- **委托式 Agent 工作** — 用 WeBot 将研究、编码或审阅任务委托给角色化子 Agent
- **预测 / GraphRAG 控制台** — 追踪节点、边、证据和报告
- **AI 集成中枢** — 接 Bot、接 OpenClaw、接外部 API Agent
- **TinyFish 搜索代理** — 基于 TinyFish Web Agent 的互联网搜索与数据提取
- **运维控制面板** — 统一管理设置、音频、端口、用户、工作流和公网访问

## 致谢

- [`msitarzewski/agency-agents`](https://github.com/msitarzewski/agency-agents) — 参考其思路扩展了预设专家池
- [`AGI-Villa/agent-town`](https://github.com/AGI-Villa/agent-town) — 参考其设计实现了 OASIS Town
- [`tanweai/pua`](https://github.com/tanweai/pua) — 用于改进批判专家为 PUA 风格

## 文档

| 文件 | 读者 | 用途 |
|---|---|---|
| [`AGENTS.md`](./AGENTS.md) | AI Agent | 行为规则、任务路由、渐进式披露 |
| [`SKILL.md`](./SKILL.md) | Agent + 人类 | 完整安装、配置、Debug、故障排查指南 |
| [`docs/index.md`](./docs/index.md) | 两者 | 任务型文档索引 |

## 社区

- 问题反馈与功能建议：[GitHub Issues](https://github.com/ClawCross/ClawCross/issues)
- 工作流与预设分享：[ClawcrossHub](https://clawcross.net)

## 许可证

Apache License 2.0 — 详见 [LICENSE](./LICENSE)。
