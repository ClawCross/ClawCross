## CI 修复（2026-11-08）

- Windows npm 启动入口将参数作为 JSON 传给 Python，绕开 PowerShell 的破折号与引号重解析；终端进程和捕获输出明确使用 UTF-8。GitHub 的 Windows/macOS/Linux CLI job 已通过（提交 `4b4ae7e`）。
- 已登录 `/studio` 的集成断言跟随统一聊天入口；已移除模拟旧外壳的测试入口，测试直接覆盖生产嵌入式渲染器与统一界面；过时的独立 Studio 导航断言已移除。
- 浏览器测试固定中文环境，自动语言检测另有显式浏览器语言用例；测试服务器拒绝 Service Worker 注册，避免后台 worker 绕过模拟 API。
- 更新默认折叠平台、能力模板、ACP 独立组件、资料编辑和消息确认的旧测试契约。嵌入式 Studio 提前标记页面形态。
- ACP 编辑器自动连接测试不再重复写入未修改的工具权限，保留关闭原生工具的配置。
- 本机 CLI 12 项与前序 Python 集成 84 项通过；浏览器完整回归及最终 GitHub 全绿仍待本轮验证，不能把此条视为最终完成报告。

# ClawCross 交接（2026-11-08）

## 当前界面修复：统一目录、完整侧栏与加载（2026-11-08）

本节为最新状态；原有 ACP 权限边界和群聊身份保持不变。

- Agent 中心使用右侧主区域，多列卡片；左侧控件固定宽度，修复旧缓存模板缺少 wrapper 时的错位兼容。平台分组默认折叠，当前页面手动展开状态保留。预设/自定义人设属于通讯录人设库，不显示在 Agent 中心。
- 聊天侧栏默认最多 4 个快捷 Agent，记录本浏览器、当前账号实际打开的顺序；初次无记录按稳定创建顺序提供入口，编辑设置的 updated_at 不参与最近排序。可切换全部/Agent/群聊、收起或展开。单 Agent 私聊仍使用原群聊系统并留在群聊区。
- “最近 Agent”右侧侧栏图标可调出原 Studio 完整侧栏：平台、新建、团队、最近列表、管理和归档全部复用。桌面替换外侧列表列；手机按需打开，非持续占位。内嵌选择向父页发送同源身份通知，只更新标题和本地最近记录，不自动调用模型。
- 原“我”仍是左侧第 4 个导航项，底部新增独立头像、设置、外观、语言入口。外观 system/light/dark，语言新增自动跟随浏览器并保留手动覆盖；主页面和内嵌 Studio 跨文档同步。
- 团队/工作流编辑器覆盖“创造”列表使用整块主区域，提供返回按钮；工作流素材列 300px、设置列 260px，为画布留空间。
- 加载修复：不先打开上一次无关会话；已知 ACP Agent 直接加载目标历史，不等待平台探测/全量会话枚举/设置；WeBot 历史不等待状态提示；团队树成功后记缓存，30 秒内不重复请求，隐藏时不预取。群和 Agent 目录并行加载，人设与模型配置提示后台加载；空闲预热只初始化 UI，不创建 Agent、不发模型消息。
- 9 项浏览器回归通过，包括真实 Studio 工具历史/完整侧栏、21 个 Agent、旧 HTML 兼容、私聊归类、慢平台/团队/状态响应、手机和语言/外观；补充真实工作流布局复查通过，1280px 视口下画布占编辑器一半以上且高度足够，未执行工作流。npm 发布文件清单校验 844 文件通过（仅清单校验，未发布 npm）。
- 已在 Agent 空闲且队列为空时受控重载；配置、原生会话、群连接和关闭的原生工具状态均保持。只读检查目录 API 和线上页面正常；单次接口耗时不等于端到端页面加载时间。
- 受限工具沙盒中的 npm 子进程 EPERM 已记录到 self-evolution，授权主机执行同一 verifier 后通过。没有绕过 prepack 或新增模型探针。Hub 仓库未改。


## 当前完成：移除 acpx 运行依赖与统一对话（2026-11-08）

此节为当前状态；下方旧轮次记录只用于追溯，不能据此恢复旧传输或放开权限。

### 内置 ACP

- 所有运行入口统一 `DirectAdapter`，包括旧 Agent、原生会话导入、命名/自定义启动器、其他平台与 SSH。旧驱动名 `acpx`、部分 API 字段/URL 保留为数据兼容，不表示会执行 acpx。`external/acpx.py` 的旧类和 `connections/acpx.py` 只保留历史兼容代码，当前运行选择器不再调用它们；远端旧 run/reconnect 动作已移除。
- Python 官方 SDK 固定 0.12.1。Codex ACP 2.5.0、Claude ACP 0.101.0 已显式安装；其他原生程序 argv 见 `config/acp-launchers.json`，不会打开表单就下载。DSH 尚未安装或进行真实模型验证，其 Node.js 24.9+ 要求保持。OpenClaw 未安装/配置。
- 本机私有 npm 前缀已卸载 acpx（及 41 个依赖包）；`which('acpx')` 为 false，`~/.clawcross/bin/node/node_modules/acpx` 不存在。安装器和全局组件页不再提供 acpx 安装。`~/.acpx` 数据保留，只读恢复别名与原生编号。
- 27 个 Agent、4 个账号全部空闲、queued inbox 为 0 时完成 10 个 ACP Agent 元数据迁移；27 个 Agent 的配置哈希和预期原生 ID 均保留。迁移前运行元数据备份在私有 `$CLAWCROSS_HOME/acp-migration-before-20261108.json`（0600），不得提交或输出原文。
- 旧原生编号匹配当前 owner/Agent、会话代次和 cwd。旧进程仍持有队列或旧会话无原生 ID 时拒绝启动，不重建替代会话。SSH 的远端守护程序保留 stdin，直接传 ACP 字节并在 EOF/取消时回收原生进程组。SSH 所有跨循环操作回到 ACP actor loop。
- 原有 FIFO、每 Agent 64 请求/全局 256 请求、16 进程/每用户 4 进程、OS 租约、退出先停止排队、无 prompt 自动重试均保留。原生目录读取并发额外限 2；list/load 不发 prompt，load 等待 SDK 重放任务结束。
- Avalon `ag_join_b7004516d38a03da15c2d1aa` 保留同一个原生会话与 `read-only` 配置，卸载 acpx 后正常 Agent API test-connection 成功。原群 `rg_cf9828b0f1ef3f8bbb621699` 仍 connected。Cathy 的暂停 Agent 仍 `native_tools:false`，未启用。没有向公共群投递测试消息。
- 独立临时数据库/工作区的真实 Codex：`ACP_OK` 成功、工具调用 0，关闭后 `ACP_RESUME_OK` 成功且原生 ID 相同。Claude/其他平台未做真实模型对话；不能宣称所有原生平台都已实测。
- 不新增原生工具拦截/模型代理/证书，不改变用户已有沙盒与审核策略。`native_tools:false` 仍阻止整个原生进程；不宣称 ACP 客户端能关闭所有原生内置工具。

### 统一对话界面

- 登录后的 `/studio` 和 `/mobile/group_chat` 使用一个 shell，共用“我的 Agent / 群聊”目录与搜索。Agent 复用 `/studio?embedded=1` 的真实 Studio renderer（包括工具历史、SSE、动态 UI 和普通 Agent API）；群聊复用原组件和原群 API。
- 删除 Studio/Message 往返按钮。平台标记通过共享 `platform-icons.js` 使用本地 MIT Lobe sprite；自定义平台使用简洁线框终端图标。Agent 有自定义头像时优先展示头像。
- Agent 中心改为通讯录的卡片视图；团队/工作流入口位于创造。手机会话占完整宽度、隐藏底部导航、有返回；容器跟随 visualViewport。没有模型请求或隐藏 Agent 创建发生在导航中。
- 同源 iframe 用于复用当前两个体量较大的 renderer，不重新实现群聊；后续可逐步抽成共同组件。选择通过受控命令对象传递，不 eval、不接受任意 URL。打开其他群会隐藏 Agent 视图，后台任务沿普通网关继续运行。

### 验证与发布

- 完整 ACP 相关回归：149 passed、8 subtests；移除安装入口/远端旧动作后的窄验证 13 passed；无 acpx、直连/迁移/原生历史/SSH 专项 52 passed。
- 浏览器最终合并回归：6 passed（设置/组件/SSH、统一目录桌面/手机、真实嵌入式 Studio 工具历史），且未发模型消息。截图 `/tmp/clawcross-workspace-desktop.png` 为隔离测试数据，不是生产聊天。
- 受控加载后原群连接正常，公网 `/studio`、`/mobile/group_chat` 均 200。无登录的 `/studio` 仍显示登录页；登录后统一界面。最终小变更已再次在 27 Agent 全空闲时受控加载，配置/原生 ID 全保留，原群 connected。
- npm 本地包 `dist/clawcross-0.0.29.tgz`：843 个运行文件、约 14.9 MB；打包验证禁止数据库、凭证、私有交接和演化报告。未发布 npm registry。
- 本机组件 audit 仍有 3 high（sandbox-runtime、node-forge、zod）；acpx 已不存在，剩余项属于现有沙盒依赖树，未盲目升级运行组件。不要声称部署无已知漏洞。
- Hub 仓库、自启动登记未改；ClawCross 本轮提交以 git log 为准。

## 历史：上一轮部分直连实现（已被上方全量直连取代）

- 用户授权实现自己的 ACP 客户端并重点验证并发。采用固定 `agent-client-protocol==0.12.1` 官方 Python SDK，新增 `external/acp_direct.py` / `acp_profiles.py` / `async_lock.py`；原 Agent、持久收件箱、历史、正常群回复组件继续共用，没有隐藏 Agent 或群聊旁路。
- 新建本机 Codex / Claude 默认 `meta.acp.transport:direct`，固定官方适配器版本见 `config/acp-adapters.json`。Unix 的显式 ACP 命令也可直连；命令使用 argv，不经过 shell。已有 Agent 缺省继续 acpx，SSH 和命名配置使用兼容路径，兼容 acpx 安装固定为 0.19.4。没有迁移或重置 Avalon。
- SDK 随核心 Python 环境准备，适配器由 `install-component acp-codex` / `acp-claude` 或 `join --prepare` 显式准备，安装器跨进程互斥。Studio / 手机创建页、Agent 连接设置和全局连接组件页提供对应入口，静态脚本版本已更新。本机已安装官方 Codex ACP 2.5.0；没有安装 Claude 适配器或 DSH。
- 同一 Agent 的 turn/control 锁跨线程和事件循环共享，键含数据库、用户和 Agent；不同 Agent 并行。直连由单独 actor loop 管理进程，单 Agent 最多 64 个请求，全局最多 256 个；直连进程总计最多 16 个、每用户 4 个，空闲按配置回收，默认 300 秒。OS 文件租约拒绝另一后台进程重复接管同一 Agent；不支持多个 worker 共同消费同一 Agent。
- 原生 ID 在模型 prompt 前保存，排队请求进入锁后重新读身份。取消、超时、断线关闭并回收当前连接；后续新请求恢复同一原生 ID，不静默新建替代会话，也不自动重发不确定是否已执行的 prompt。MCP 配置变更只重连原会话。退出先取消/等待排队与活动操作，再回收进程和管道。SDK 历史重放不会重新发给页面。
- 不声明客户端文件/终端能力；权限询问按只读、拒绝或用户明确的批准策略处理，ClawCross 两个 MCP 包装器继续由后端校验活动调用、身份及权限。MCP 凭证和配置改为完整文件原子发布，避免并发初始化读到空凭证；跨线程退出按任务所属循环取消。输出超限关闭连接，stderr 持续排空但不记录潜在敏感内容。
- **保持用户要求的工具边界**：没有恢复模型代理、证书或响应改写。ACP 客户端不能保证阻止适配器内部直接执行原生工具，仍由原 CLI 自身沙盒和权限管理。`native_tools:false` 继续暂停整个原生进程，新建 CLI 转接伙伴不隐式放开。未声称跨 OS 用户隔离。
- 最终回归：**218 passed、1 skipped、12 subtests passed**；跳过的是需显式启用的独立守护进程启动测试。本轮实际生产服务另经空闲受控重载验证。专项直连 31 项均通过，资源回收警告按失败处理。覆盖 20 条同 Agent FIFO、12 个跨线程调用、不同 Agent/用户并行、12 个并发 HTTP 请求、20 次群投递去重为一次模型调用和正常群回复、跨循环取消、排队上限、关闭/退出、超时/崩溃恢复、MCP 更换、权限和安装锁。
- 真实 Codex 使用独立临时数据库/工作区测试：初始化成功，回复 `ACP_OK`，关闭连接后回复 `ACP_RESUME_OK`，原生 ID 相同，未调用工具。最终代码再次验证通过；没有向真实群发送测试消息。Claude / 其他平台没有进行真实模型对话验证；协议和兼容路径用替身进程及回归验证。
- 浏览器：`component-entrypoints.spec.js` 的 Studio 设置与显式安装入口测试 1 项通过；JS 语法检查、`git diff --check` 通过。本地 `dist/clawcross-0.0.29.tgz` 已重建，836 个运行文件，约 14.9 MB；未发布 npm registry。
- 在 4 个用户、27 个 Agent 全部空闲、待处理 inbox 为 0 时，用既有 `restart_flag` 受控加载。重载后 27 个 Agent 配置和原生 ID 全部一致；Avalon 继续 `acpx`、`native_tools:true`，原群连接 `connected`。公网 `/studio`、`/mobile/group_chat` 均 200。Hub 仓库和自启动登记未改动。
- 安装时 npm audit 发现**原有兼容组件依赖**仍有 3 个 high 项：acpx 0.19.4 的 zod，以及现有 sandbox-runtime 0.0.78 的 node-forge/zod。新增 Codex ACP 树使用 zod 4.6.7，不在该报告项内。未用跨主版本 overrides 或盲目 `npm audit fix` 改动运行中的兼容组件；这些已有依赖需单独升级验证，不能宣称整个部署已无已知漏洞。
- 详细参数与安装说明见 `docs/agent-join.md` 和 `docs/runtime-reference.md`。本轮提交号以 `git log -1 --oneline` 为准。

## Avalon 伙伴已按用户要求恢复（2026-11-08）

- 用户在撤销自建拦截后明确要求“Avalon 的伙伴恢复”，已授权恢复正常原生 ACP 运行。
- 同一 Agent `ag_join_b7004516d38a03da15c2d1aa`、同一原群 `rg_cf9828b0f1ef3f8bbb621699`；`native_tools:true`，原生 `config_options.mode:read-only`，原有 acpx `approve-reads` / 无人值守拒绝策略保留。没有恢复自建代理、证书或模型响应拦截。
- 原生 ACP 初始化 200，实际会话报告 `mode=read-only`；单次私聊真实返回 **“已恢复连接”**、`ok:true`。原群连接 `connected`，成员身份仍在；没有发送新的群测试消息或重放旧群请求。
- ClawCross 工具连接器保持此前关闭状态。此次为运行配置恢复，无源码变更和全局服务重启。下方“当前暂停”的记录属于恢复前的状态。

## 用户决定：撤销自建模型拦截（2026-11-08）

用户明确要求：工具限制应使用 acpx / 适配器原生能力；没有对应能力就不做，不接受自建模型代理作为替代。

- 已撤销 `4e624c0` 引入的 HTTPS 模型代理、进程临时证书、Landlock 代理围栏、`text_only` 特例及对应 CLI 默认、依赖和测试；不再修改或检查模型服务的请求/响应来禁用工具。
- 已先将 Avalon 的 `ag_join_b7004516d38a03da15c2d1aa` 的 `native_tools`、`text_only`、`clawcross_tools` 设为 false，并通过 acpx 的现有清理接口停止其目标连接。原群“未来之室” `rg_cf9828b0f1ef3f8bbb621699` 的成员和历史保留。没有自动放开原生工具；当前应显示 paused，不能再宣称可自动回复。
- 本机实际 acpx 0.19.4 提供 `--deny-all`、`--permission-policy`、`--allowed-tools`、`--no-fs`、`--no-terminal`。这些分别控制 ACP 权限请求、传给适配器的工具选择、向适配器声明的客户端能力；不能据此保证所有适配器内部的所有原生行为均被阻止。
- 仅保留既有的原生权限选项与工具关闭时的进程暂停保护。若需要让这个公网群伙伴恢复原生执行，必须由用户明确选择权限，不能因为撤销代理而隐式开启。
- 不发送新的群消息或模型探针。之前消息 384 是撤销前的真实回复，不能用它证明撤销后的 Agent 正在运行。
- 撤销验证：28 项定向测试通过，本地 npm 包重建通过；自建代理进程数量为 0。确认 27 个 Agent 均空闲后重载，实际 API 返回 `paused` / `native_tools_disabled`，能力卡 `native_tools:false`、`blocked:true`、`block-native-process`，原群成员仍在；公网 Studio / Message 均 200。

## 当前修复：账号归属、普通加群与工具边界（2026-11-08）

本节取代下方旧记录里把 `connected` 等同“可用”的结论。

- 用户最终确认：CLI 本质是调用现有群聊的加群流程；默认用户可以是第一个创建用户，允许选择其他用户，连接不需要向所有本机用户开放。
- 加群仍用 `POST /groups/join`，不新增旁路群或隐藏会话。`join` 返回 `owner`、`message_center`、`agent_ready`；`ok` 仅代表传输正常。首次使用会准备默认用户，`clawcross accounts` 查询，`--user` 选择。侧栏底部和 Message “我的”页增加账号入口，公网不枚举账号，远程切换要求密码。
- 新建 CLI 转接 Agent 只有伙伴目录、最小 ClawCross 工具，没有 CLI 项目、个人共享或团队目录。命令沙盒默认 `auto`，缺少后端时禁止自动退回 `off`；显式用户设置保持有效。
- 首次登记原生会话同样默认关闭原生执行，仅保留原 cwd 作为明确选定路径，不自动开放所有团队目录；未明确开启时原生历史加载返回 paused。
- 原生工具开关是**暂停原生程序**：当前没有验证过能让任意 ACP 适配器全部关闭内置工具后继续聊天的通用机制。`native_tools:false` 在组件探测之前阻止启动、阻止群消息唤醒，并取消正在运行的任务；不是给 prompt 加一句“禁用工具”。新建原生 CLI 转接伙伴默认关闭。原生工具开启后仍由原 CLI 管理，不可声称被 ClawCross 沙盒隔离。
- 无发送工具、纯聊天或关闭连接器时，最终文字通过正常群消息 API / outbox 回到原群，固定编号去重；原生/WeBot 收件箱都保存自动回复目标，重启丢失内存回调后可恢复。
- `clawcross acp --user USER --agent ID` 将正常 WeBot Agent 提供为 ACP v1 stdio server；复用用户、Agent ID、历史、流式对话和控制 API，不引入其他模型运行时。默认新建群聊能力模板；不接受客户端扩大 cwd、MCP 或运行模式。目前只支持文字。
- DeepSeek Harness 平台 `deepseek-harness` 默认 `dsh --profile acp`；可选安装固定官方 npm `@deepseek-ai/dsh@0.2.2-rc.1`。官方包要求 Node 24.9+，已加检查。**本机 Node 22.22.1，因此尚未安装/实测 DSH 模型连通性**，不要将预设配置测试说成真实模型测试。
- 之前新建的 Codex 已先移出群并取消，随后限制权限、空闲受控重载后，作为 `cathy` 的普通成员重新加入原“未来之室”。仍为 Agent `ag_join_a6d96a1144efa8df99a7ed71`、群 `rg_cf9828b0f1ef3f8bbb621699`。实际检查：该账号群成员可见、Agent 状态 `paused`、待执行队列 0；`joined:true`、`agent_ready:false`、连接 `connected`。未发送群测试消息或模型 prompt，未建立第二个系统自启动服务。
- 已加载修复，重载前所有 26 个 Agent 均空闲；本机和公网 Studio / Message 都返回 200。Hub 仓库未修改。
- 验证：126 项 Python 用例、16 个 subtests 通过；真实 WeBot ACP stdio initialize/EOF 退出通过；桌面/手机侧栏浏览器用例 1 项、账号选择器浏览器用例 1 项通过；npm 发布文件清单通过。ACP 完整回复通过真实路由 + 替身模型验证，不消耗线上模型额度。
- 最后针对原生会话、CLI 和权限的 35 项定向回归通过（与上面的测试有重叠）。本地 npm 产物 `dist/clawcross-0.0.29.tgz` 已重建，832 文件、14.9 MB；未发布 npm registry。构建在受限执行沙盒中遇到子进程 EPERM，在获准的本机执行环境中通过，未移除 prepack 校验。
- 旧前端用例要求公网 `/relay/app/poll` 成功，与已经限制为主机使用的 SDK 控制接口冲突。只修正用例为公开签名页面 200、主机 SDK 403；**没有放开 SDK**。


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
