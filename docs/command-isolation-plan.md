# 命令隔离后端

命令沙盒默认关闭。启用后，不可用或初始化失败均拒绝执行；绝不自动运行宿主命令。

## 平台与选择

- `srt`：现有 Anthropic Sandbox Runtime。Linux 使用 bwrap，macOS 使用原生隔离。当前 ClawCross Windows 路径因资源限制未适配而拒绝执行；不能宣称 Windows 已通过测试。
- `landlock`：Linux 内核 Landlock + libseccomp，直接在现有容器/虚拟机中执行，不创建容器或 user namespace，不改变 AppArmor。要求 ABI ≥ 6、x86_64/aarch64、非 root 账号。正式执行前验证所有过滤器，失败不 exec 用户命令。
- `auto`：Linux 先以无副作用的 `true` 探测 SRT；失败时选择 Landlock。macOS/Windows 仍选择 SRT。显式选择 SRT 时不会擅自切换后端。

Linux x86_64 真实内核与正式 `run_command` 已测试；aarch64、macOS、Windows 未在本服务器验证。

## 文件、网络与提权

Landlock 允许工作区读写、程序/Python 安装目录只读；隐藏工作区外用户数据。子进程继承限制，Python 删除、编码执行、symlink、换工具名称无法解除内核规则。它不提供独立 rootfs，不等同于完整容器：部分文件元数据仍可见，工作区内配置文件没有 SRT 的额外保护规则。

Landlock 在可管理的 systemd 主机上使用每条命令临时的非 root unit：IPAddressDeny=any / IPAddressAllow=localhost 提供内核出口限制，Landlock 只允许连接本条命令的 HTTP/SOCKS 代理端口。代理按精确域名/公网 IPv4 和可选端口过滤，拒绝私有、回环、保留和元数据地址，解析结果直接用于连接以避免二次 DNS 查询。没有 TLS 解密。每条命令先用合成端点确认规则确实阻止非回环连接，失败不启动用户命令。

`approval.sandbox_allowed_domains` 是用户可配置的直接放行列表，默认空；新目标在管理员最大范围内走已有系统审核与一次重试。每个代理都有临时凭证，不能通过删除代理环境变量直连。域名白名单不接受通配符；裸域名覆盖其端口，指定 host:port 只覆盖该端口。DNS 在白名单检查后才执行。

没有 systemd 管理权限时，保持离线 Landlock；配置了网络放行或申请网络权限则明确拒绝。不会安装 sudoers 规则、修改 AppArmor、启动新容器或改变全局防火墙。禁止 ptrace、跨进程内存访问、namespace 创建、危险内核接口及 SysV IPC；Landlock scope 限制跨隔离域信号。

Agent 只调用 `run_command`。前台权限失败时系统根据明确错误定位一个目标，检查管理员最大范围后送审核，批准则新建受限进程重放原命令一次。读授权不允许删除；单文件写授权不允许删除父目录内容。首次执行可能已有部分副作用，系统不承诺事务。模糊权限错误和初始化失败不提权。后台/交互暂不自动重放。

最大范围由 `CLAWCROSS_SANDBOX_MAX_READ_PATHS`、`CLAWCROSS_SANDBOX_MAX_WRITE_PATHS`、`CLAWCROSS_SANDBOX_MAX_DOMAINS` 的 JSON 数组定义；文件提权上限默认空，网络上限未设置时为 `["*"]`，允许对具体公网目标送审，批准后只授予这个目标。显式设置 `[]` 或无效配置继续禁用网络提权。`*` 仅用于管理员上限，不是脚本的无限网络授权；用户直接放行列表仍只接受具体域名/IP 和可选端口。任何审核不能突破上限或解除隔离。Landlock 可在受控联网可用时接受 network 扩展；始终禁止 host 扩展。

## 资源限制与边界

硬限制：每进程 CPU 120 秒、地址空间 2 GiB、文件大小 128 MiB、FD 256。宿主监督器限制墙钟时间、输出并清理进程组。Linux Landlock/SRT 的进程/线程上限考虑相同 UID 当前用量加 64，因为 RLIMIT_NPROC 是共享账号限制，不能固定成 256 而导致繁忙服务器无法 fork；SRT 在进入 PID 隔离前计算宿主用量。

受控联网的临时 unit 同时使用 MemoryMax=2G、TasksMax=128、RuntimeMaxSec 与整组清理，限制任务及子进程的合计内存和进程数。CPU 仍为每进程时间上限，磁盘仍为每文件上限，不提供 CPU 占用率或工作区总容量配额；没有 systemd 的离线模式仅使用基础 rlimit。Landlock 禁止 setsid/setpgid，阻止子进程脱离受监督进程组。

没有安装 Docker、Podman、gVisor，没有下载新二进制，也没有修改 AppArmor/sysctl。缺少 libseccomp 或内核能力时仅返回明确错误。

## 验证

`test/test_command_landlock_integration.py` 使用正式启动器与真实命令执行，临时合成数据替代在线用户数据；审核决定使用固定测试响应，未据此宣称在线审核模型已验证。
