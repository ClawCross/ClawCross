# 命令隔离后端

命令沙盒默认关闭。启用后，不可用或初始化失败均拒绝执行；绝不自动运行宿主命令。

## 平台与选择

- `srt`：Anthropic Sandbox Runtime。Linux 使用 bwrap，macOS 使用原生隔离，Windows 使用 SRT 0.0.78+ 的专用账号、文件 ACL 和 WFP 网络隔离。Windows 命令通过原生 argv 接口进入隔离，再用嵌套 Job Object 限制资源；不经宿主 `.cmd` 解析用户命令。缺少组件、系统初始化或资源限制能力时拒绝执行。
- `landlock`：Linux 内核 Landlock + libseccomp，直接在现有容器/虚拟机中执行，不创建容器或 user namespace，不改变 AppArmor。要求 ABI ≥ 6、x86_64/aarch64、非 root 账号。正式执行前验证所有过滤器，失败不 exec 用户命令。
- `auto`：Linux 先以无副作用的 `true` 探测 SRT；失败时选择 Landlock。macOS/Windows 仍选择 SRT。显式选择 SRT 时不会擅自切换后端。

Linux x86_64 真实内核与正式 `run_command` 已测试；aarch64、macOS、Windows 未在本服务器实测。Windows 增加独立 CI，验证真实 Job Object 和 SRT 接口契约；完整 SRT 隔离另有显式开启的真机测试，不能把接口模拟测试当作完整隔离验证。

Windows 首次使用需要明确初始化：在沙盒组件设置中点击“初始化 Windows 沙盒（需管理员确认）”，或管理员运行 `srt windows-install`。SRT 创建专用账号并安装该账号的 WFP 网络规则，可能出现 UAC 确认；启动和执行命令不会自动安装或初始化。组件页面分别显示 npm 包与系统隔离是否就绪，旧包可显式更新。文件/网络策略、普通/严格等级及已有审批机制仍共用；初始化故障不触发路径/域名提权，不降级宿主执行。SRT Windows 后端仍为 alpha。

## 文件、网络与提权

### 两个安全等级与默认工作区

- 普通：保留沙盒内的基础资源查询、子进程操作和后台任务管理；文件、网络范围可在管理员上限内经审核扩大。
- 严格：同样保留基础运行能力，强制开启命令沙盒，每个 Agent 使用独立工作区；不使用历史提权授权，也不允许审核扩大范围。用户预设的网站许可仍有效，Bypass 不解除这些限制。
- 新 Agent 的基础目录集合由伴生、用户共享、CLI、所属 Team 和自定义来源组成，两种安全等级使用相同来源配置；严格模式禁止运行时扩大集合。自动目录由当前实例、用户和 Agent/Team 标识生成，配置只保存开关与自定义路径。旧 Agent 未调整配置时保留共享或严格独立目录的旧行为。工作区基础目录位于项目源码内时，仍改用源码外默认目录。详见 [工作区配置](workspaces.md)。
- 运行设置和工具策略存放在 `USER_FILES_DIR/.control/<user>/`；命令沙盒配置及后台任务控制文件位于 `CONFIG_DIR/sandbox/`，工作区不能覆盖这些目录。已有设置第一次读取时保留内容迁移到控制目录。

严格等级也适用于 ClawCross 文件和 Web 工具：文件操作限定到独立工作区；Web 搜索与抓取执行同一网站许可列表，空列表离线，审批不可扩大范围。自定义策略 hook 在严格模式禁用。Windows 文件工具用原生句柄固定路径，不依赖 POSIX API。详细的平台差异、验证范围与恶意用户/API 风险见 [严格模式安全审查](strict-security-review.md)；不能把可由用户关闭的 Agent 设置当作恶意租户隔离。

### 进程操作

普通模式只读开放 `/proc`，支持 `ps`、`top`、`free` 和新子进程的资源查询，写入、跨隔离域信号、ptrace 仍被内核拒绝。这个选择接近 CLI 沙盒的宽读限制写模型，但不提供同一 Unix 账号下的租户隐私：操作系统原本允许读取的进程环境也可见。需要隔离服务凭证时使用严格模式，不要把普通模式当作多租户隔离边界。

严格模式只授权系统资源统计和调用开始时已有 PID 的基本信息，不授权 `environ`、`mem`、`fd`、`root` 等敏感入口。可用 Python 读取 `/proc/meminfo`、`/proc/stat` 和当前启动器 PID 的状态。Landlock 的 inode 授权不能匹配以后创建的 PID，严格模式中一般的 `ps/top` 子进程可能不可用；这不是提权目标，也不会退出沙盒重试。后台任务仍通过已有管理工具查看状态、输出和取消。

同一次命令中的子进程可正常启动、等待和接收信号，本地 `socketpair` 可用于 asyncio 等内部通信。跨轮后台任务由已有 `background_command_io` 和 `cancel_background_command` 管理，后端校验用户和 Agent 归属。不同命令的 Landlock 信号隔离仍有效，不能通过新命令的任意 PID 操作系统或其他命令的进程。

`ptrace`、跨进程内存访问仍禁止；`setsid/setpgid` 仍禁止，避免进程逃出运行器负责清理的进程组。Landlock 目前不约束 chmod/chown 的路径范围，保留这些系统调用限制；不能为了工作区内 chmod 同时放开宿主配置的权限修改。

Landlock 允许工作区读写、程序/Python 安装目录及上述资源统计只读；隐藏工作区外用户数据。子进程继承限制，Python 删除、编码执行、symlink、换工具名称无法解除内核规则。它不提供独立 rootfs，不等同于完整容器：部分文件元数据仍可见。后端配置和任务控制文件位于工作区外。

Landlock 在可管理的 systemd 主机上使用每条命令临时的非 root unit：IPAddressDeny=any / IPAddressAllow=localhost 提供内核出口限制，Landlock 只允许连接本条命令的 HTTP/SOCKS 代理端口。代理按精确域名/公网 IPv4 和可选端口过滤，拒绝私有、回环、保留和元数据地址，解析结果直接用于连接以避免二次 DNS 查询。没有 TLS 解密。每条命令先用合成端点确认规则确实阻止非回环连接，失败不启动用户命令。

`approval.sandbox_allowed_domains` 是用户可配置的直接放行列表，默认空；新目标在管理员最大范围内走已有系统审核与一次重试。每个代理都有临时凭证，不能通过删除代理环境变量直连。域名白名单不接受通配符；裸域名覆盖其端口，指定 host:port 只覆盖该端口。DNS 在白名单检查后才执行。

单次 Y 的路径/网络扩展只属于当前命令重试，不写入 Agent 的沙盒设置；服务器重启或下一条命令不会继承。`KEEP Y` 在当前 Agent 保存具体路径访问类型或网络目标，后续调用仍检查最大范围，不影响其他 Agent，严格模式不使用这些历史授权。设置页保存的 `sandbox_allowed_domains` 是持续有效的 Agent/用户配置。内部人工审批恢复时，系统通过 MCP 私有参数传递原授权，直接执行一次已批准的重试；这些参数在 Agent 可见的 schema 和工具搜索中隐藏，Agent 自行提交被拒绝。

没有 systemd 管理权限时，保持离线 Landlock；配置了网络放行或申请网络权限则明确拒绝。不会安装 sudoers 规则、修改 AppArmor、启动新容器或改变全局防火墙。禁止 ptrace、跨进程内存访问、namespace 创建、危险内核接口及 SysV IPC；Landlock scope 限制跨隔离域信号。

Agent 只调用 `run_command`。前台权限失败时系统根据明确错误定位一个目标，检查管理员最大范围后送审核，批准则新建受限进程重放原命令一次。读授权不允许删除；单文件写授权不允许删除父目录内容。首次执行可能已有部分副作用，系统不承诺事务。模糊权限错误和初始化失败不提权。后台/交互暂不自动重放。

最大范围由 `CLAWCROSS_SANDBOX_MAX_READ_PATHS`、`CLAWCROSS_SANDBOX_MAX_WRITE_PATHS`、`CLAWCROSS_SANDBOX_MAX_DOMAINS` 的 JSON 数组定义；文件提权上限默认空，网络上限未设置时为 `["*"]`，允许对具体公网目标送审，批准后只授予这个目标。显式设置 `[]` 或无效配置继续禁用网络提权。`*` 仅用于管理员上限，不是脚本的无限网络授权；用户直接放行列表仍只接受具体域名/IP 和可选端口。任何审核不能突破上限或解除隔离。Landlock 可在受控联网可用时接受 network 扩展；始终禁止 host 扩展。

## 资源限制与边界

硬限制：每进程 CPU 120 秒、地址空间 2 GiB、文件大小 128 MiB、FD 256。宿主监督器限制墙钟时间、输出并清理进程组。Linux Landlock/SRT 的进程/线程上限考虑相同 UID 当前用量加 64，因为 RLIMIT_NPROC 是共享账号限制，不能固定成 256 而导致繁忙服务器无法 fork；SRT 在进入 PID 隔离前计算宿主用量。

上述 rlimit 适用于 Linux/macOS。Windows 使用 Job Object：每进程及整组用户态 CPU 时间 120 秒、每进程及整组提交内存 2 GiB、128 个进程，并限制命令墙钟时间。工作进程以挂起状态创建，设置限制并归入 Job 后才恢复；禁止 breakaway，关闭 Job handle 会终止剩余子进程。Windows 暂无与 RLIMIT_FSIZE/NOFILE 等价的每文件大小和 FD 限制，不提供工作区磁盘总额配额。命令超时/取消先尝试让 SRT 清理，再清理进程树。

Windows 真机完整测试需先显式安装并初始化 SRT，然后在 PowerShell 运行：`$env:CLAWCROSS_WINDOWS_SRT_INTEGRATION='1'; python -m pytest test/test_windows_resource_limits.py test/test_windows_srt.py -q`。该测试只访问临时合成文件，不读取真实用户文件；普通 CI 不自动初始化 SRT 或更改网络规则。

受控联网的临时 unit 同时使用 MemoryMax=2G、TasksMax=128、RuntimeMaxSec 与整组清理，限制任务及子进程的合计内存和进程数。CPU 仍为每进程时间上限，磁盘仍为每文件上限，不提供 CPU 占用率或工作区总容量配额；没有 systemd 的离线模式仅使用基础 rlimit。Landlock 禁止 setsid/setpgid，阻止子进程脱离受监督进程组。

没有安装 Docker、Podman、gVisor，没有下载新二进制，也没有修改 AppArmor/sysctl。缺少 libseccomp 或内核能力时仅返回明确错误。

## 验证

`test/test_command_landlock_integration.py` 使用正式启动器与真实命令执行，临时合成数据替代在线用户数据；审核决定使用固定测试响应，未据此宣称在线审核模型已验证。

2026-10-04：Linux x86_64 宿主隔离临时目录验证，启用 `CLAWCROSS_NETWORK_INTEGRATION=1` 的沙盒/审核/设置/文件工具组合测试 159 项及 70 项子测试通过，包含真实 example.com HTTPS、代理直连拒绝、逐轮提权、KEEP Y、后台取消和严格模式。补充的严格文件读写与设置迁移测试组合 28 项及 16 项子测试通过。普通模式 ps/free/top 均成功；严格模式资源统计可读、进程环境和工作区外文件拒绝。没有以这些测试替代其他操作系统实测。
