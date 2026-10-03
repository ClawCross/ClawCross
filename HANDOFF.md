# 交接任务接续状态（2026-10-03）

已补齐本地模型能力目录、用户默认/会话思考强度、精确价格查询、Town 按需加载、静态 CSS、图片压缩及群附件总量检查。
276 项后端相关测试、19 项能力/追踪/分层测试、25 项浏览器测试通过；测试仅使用临时 CLAWCROSS_HOME。
沙盒在 feature/landlock-network 的 260b7d3 完成，后续合并与最终验证记录见 docs/integration-validation.md。
原始模型快照保留在本工作树 data/model_catalog/，只提交裁剪后的 common/model_catalog.json，不自动更新。

以下为原始交接记录；其中未完成和未测试状态记录的是交接时的状态。

# 交接：ClawCross 2026-10-03 会话

工作区：worktree `/home/ubuntu/work2/ClawCross-next`，分支 `feature/loop-openclaw-titles-invite`（从 main `e818bc9` 开出）。
**全部未提交、未合并、测试一个都没跑**（用户规则：跑测试/E2E/重启前先问）。只做过 py_compile、node --check、AST 未定义名检查、模块导入检查，均通过。
主工作区 `/home/ubuntu/work2/ClawCross` 未改动；同机还有其他 Claude/Codex 会话在跑。
跑测试必须 `CLAWCROSS_HOME=$(mktemp -d)`，venv 用 `~/.clawcross/venv/bin/python`。

## 已完成

1. **Agent 循环整理（行为不变）**：`src/backend/webot/engine/agent.py` 2625→2204 行；`_call_model` 拆成 `_begin_turn/_select_model/_turn_tool_schemas/_dynamic_context/_prepare_history/_history_view/_invoke/_record_call_usage/_bill_usage/...`；工具节点抽出 `_discover/_inject_identity/_execute`；`UserAwareToolNode(tools, find_internal_session_meta_fn=..., tool_registry=...)` 去掉了没用的第二个参数（调用方和测试已改）。删除死模块：`engine/agent_orchestrator.py`、`engine/workflow_engines.py`、`engine/streaming_tool_executor.py`、`cache_boundary.py`、`notification_system.py`，及其测试类。
2. **OpenClaw 改成和 Codex 一样的 ACP 工具**：删掉 `OPENCLAW` driver、`external/openclaw.py`、`openclaw_routes.py`、`openclaw_config.py`、`teams/openclaw_restore_naming.py`、`scheduler/cron_utils.py`；`agents/store.py` 启动时把 `driver='openclaw'` 一次性迁移为 `acpx`（保留 `global_name` 只用于会话 key）；会话 key `agent:<global_name or main>:clawcross-<owner>-<id>`（`external/session.py`）；ACP reset 一律换新 session key。删除所有写 OpenClaw 的功能（网关预热、chatCompletions、LLM 反写、agent 增删改、技能/频道/工作区编辑、团队快照恢复、Studio OpenClaw 标签/导入弹窗/配置面板、CLI `openclaw`/`openclaw-snapshot`、`sync-openclaw-llm` 等）。保留只读的“从 OpenClaw 导入 LLM 配置”（`ops/setup/configure_openclaw.py` 已精简到 313 行）。文档 `docs/openclaw-commands.md` 重写，AGENTS.md/SKILL.md/cli.md 等已同步。
3. **会话标题**：agent 设置新增 `title`（`agents/routes.py` `_SHARED_SETTINGS`，`session_title()` 规范化，≤80 字）；新工具 `set_session_title`（`webot/mcp/session.py`，always-loaded，session 强制注入）；`AgentClient.update()`；Studio 侧栏显示 标题 > 第一条用户消息 > agent 名，agent 名作副标题；✏️ 编辑里可改标题；agent 可覆盖用户改的标题（用户确认）。
4. **群聊只用邀请链接**：现有“朋友聊天链接”也能在另一台 ClawCross「加入群聊」粘贴，以正式成员加入。远程成员经群主前端 `/relay/*`（`frontend/proxies/group_guests.py`，带签名票据 `X-Group-Invite`）每 2 秒 `POST /relay/poll` 轮询；粘贴本机自己的链接时直连本机群服务器走 WebSocket（`groups/config.own_front_ends`）。客户端表新增 `via` 列（迁移在 `ClientStore.__init__`）。**密码加入、local_join、按群号加入、/sharing、旧 /invite 已全部删除**（用户：“统一用链接”）；CLI `groups join --invite <链接>`、`groups invite` 生成新链接（旧链接失效）；`join_group` 工具只收链接。弹窗按 theme.css 重做（`static/js/group-network-ui.js`、`static/css/group-network.css`）。文档 `docs/group-network.md` 已改。已知坑：临时 Cloudflare 隧道域名一变，远程成员断开。
5. 测试文件已按上述改动更新（未运行）：test_agents、test_teams、test_team_snapshot_upload、test_optional_bootstrap、test_layering、test_integration、test_configure_openclaw_sync、test_group_relay、test_group_guests（新增邀请/轮询/代理/join_link 用例）、test_agent_tool_injection、test_tool_aliases、test_tool_schemas、test_new_features、test_enhanced_features、test_deep_audit_fixes、browser/studio-smoke、browser/mobile-agents-smoke。删除 test/openclaw_live_smoke.py。

## 进行中：WeBot 思考强度 + 模型能力数据（未开始写代码）

用户决定：**现在拉一次 OpenClaw 的公开模型目录作为固定数据，以后不要自动拉取**。快照已下载到 `data/model_catalog/openclaw-catalog-v2.raw.json`（未跟踪文件，1.6MB，来源 `https://catalog.openclaw.ai/models/v2/catalog.json`，MIT，generatedAt 1790699820435，sourceCommit 1d0efa48…，44 provider / 1039 模型）。

调研结论：
- LangGraph 不记录模型能力；LangChain core 1.6 有 `model.profile`（models.dev 数据，随包附带：OpenAI 60、Gemini 40、Anthropic 16、DeepSeek 4 个模型），字段 `image_inputs`、`reasoning_output`、`reasoning_effort_levels`、`reasoning_effort_default`、`max_input_tokens`、`temperature` 等。ChatOpenAI/ChatAnthropic/ChatGoogleGenerativeAI 都有统一的 `reasoning_effort` 参数（各自翻译成本家 API）。
- OpenClaw 目录每个模型有 `input`（text/image/document）、`reasoning`、`contextWindow`、`maxTokens`、`thinkingLevelMap`（统一档位 off/minimal/low/medium/high/xhigh/max → 本家值，null=不支持）、`compat.supportedReasoningEfforts`、`mediaInput.image`（maxSidePx/preferredSidePx）、`pricing`（每百万 token）。档位数据稀疏（约 48 个模型有 supportedReasoningEfforts，36 个有 thinkingLevelMap）。
- ClawCross 现有 4 张手写表要被替换：`webot/message_builder._is_vision_model`（名字匹配）、`webot/context_limits.infer_model_context_window`（名字猜）、`common/llm_factory._model_supports_temperature`（前缀表）、`webot/cost_tracker._MODEL_PRICING`（旧价格）。

建议实现（已向用户提出，用户认可用外部表）：
1. 把快照裁剪成需要的字段，放进仓库（如 `src/backend/common/model_catalog.json`，带来源/许可说明）；加一个**手动**更新脚本（如 `tools/maintenance/update_model_catalog.py`），不做任何自动拉取。
2. 新模块（如 `common/model_capabilities.py`）查询顺序：用户显式设置（`LLM_VISION_SUPPORT`、上下文设置）> LangChain `model.profile` > 仓库内目录（先按 provider+id，再按 id 跨 provider 匹配，去掉 `vendor/` 前缀）> 未知。
3. 思考强度：档位 = profile 的 `reasoning_effort_levels`；没有时，仅当走 OpenAI 协议（ChatOpenAI/ChatDeepSeek）且目录有 `supportedReasoningEfforts` 才用；都没有则不显示该选项。在 `create_chat_model` 传 `reasoning_effort`。设置放 `webot/runtime_settings.py`（现有 user 默认 + session 覆盖结构；`_merge` 只允许 `context`/`approval` 两个 section，需要扩展或放进其中一个），界面放 ＋ → 高级选项（`static/js/runtime-settings.js`）。
4. 用能力数据替换上面 4 张表；`mediaInput.image.preferredSidePx` 可用于以后的“图片上传前本地压缩”。

未决（用户尚未回答）：思考强度按会话还是全局默认+会话覆盖（建议后者，沿用 runtime_settings）；查不到档位时是否完全隐藏（建议隐藏）；上下文窗口是否用目录值作默认（用户手动设置仍优先）。

## 其他未做 / 待确认

- 沙盒：用户要求先不动。
- 前端：消息中心 ↔ Studio 快速切换（整页跳转、`oasis-town.bundle.js` 7MB 同步加载、Tailwind CDN）未做。
- 图片/文件：前端只在 >10MB 才压缩，远端群单条 512KiB 上限，HEIC 静默丢弃——未做。
- 下一步需问用户：跑相关单元测试？提交/合并分支？
- 记忆：`~/.claude/projects/-home-ubuntu/memory/clawcross-openclaw-as-acp.md` 记录了上述决定。
