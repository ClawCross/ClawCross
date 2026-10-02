# 命令隔离后端

命令沙盒默认关闭。启用后，不可用或初始化失败均拒绝执行；绝不自动运行宿主命令。

## 平台与选择

- `srt`：现有 Anthropic Sandbox Runtime。Linux 使用 bwrap，macOS 使用原生隔离。当前 ClawCross Windows 路径因资源限制未适配而拒绝执行；不能宣称 Windows 已通过测试。
- `landlock`：Linux 内核 Landlock + libseccomp，直接在现有容器/虚拟机中执行，不创建容器或 user namespace，不改变 AppArmor。要求 ABI ≥ 6、x86_64/aarch64、非 root 账号。正式执行前验证所有过滤器，失败不 exec 用户命令。
- `auto`：Linux 先以无副作用的 `true` 探测 SRT；失败时选择 Landlock。macOS/Windows 仍选择 SRT。显式选择 SRT 时不会擅自切换后端。

Linux x86_64 真实内核与正式 `run_command` 已测试；aarch64、macOS、Windows 未在本服务器验证。

## 文件、网络与提权

Landlock 允许工作区读写、程序/Python 安装目录只读；隐藏工作区外用户数据。子进程继承限制，Python 删除、编码执行、symlink、换工具名称无法解除内核规则。它不提供独立 rootfs，不等同于完整容器：部分文件元数据仍可见，工作区内配置文件没有 SRT 的额外保护规则。

Landlock 禁止所有新 socket，暂不支持网络域名白名单/代理。禁止 ptrace、跨进程内存访问、namespace 创建、危险内核接口及 SysV IPC；Landlock scope 限制跨隔离域信号。

Agent 只调用 `run_command`。前台权限失败时系统根据明确错误定位一个目标，检查管理员最大范围后送审核，批准则新建受限进程重放原命令一次。读授权不允许删除；单文件写授权不允许删除父目录内容。首次执行可能已有部分副作用，系统不承诺事务。模糊权限错误和初始化失败不提权。后台/交互暂不自动重放。

最大范围由 `CLAWCROSS_SANDBOX_MAX_READ_PATHS`、`CLAWCROSS_SANDBOX_MAX_WRITE_PATHS`、`CLAWCROSS_SANDBOX_MAX_DOMAINS` 的 JSON 数组定义；默认全空。任何审核不能突破上限或解除隔离。Landlock 不接受 network/host 扩展。

## 资源限制与边界

硬限制：每进程 CPU 120 秒、地址空间 2 GiB、文件大小 128 MiB、FD 256。宿主监督器限制墙钟时间、输出并清理进程组。Landlock 进程/线程上限按相同 UID 当前用量加 64 设置，因为 RLIMIT_NPROC 是共享账号限制，不能固定成 256 而导致繁忙服务器无法 fork。

这些不是进程树总内存/CPU/磁盘配额。当前环境 cgroup 只读，未实现任务独立 cgroup；Landlock 后端禁止 setsid/setpgid，阻止子进程脱离受监督进程组；共享 UID 进程配额与内存总额仍需独立监督。不得因此宣称资源消耗已完全隔离。

没有安装 Docker、Podman、gVisor，没有下载新二进制，也没有修改 AppArmor/sysctl。缺少 libseccomp 或内核能力时仅返回明确错误。

## 验证

`test/test_command_landlock_integration.py` 使用正式启动器与真实命令执行，临时合成数据替代在线用户数据；审核决定使用固定测试响应，未据此宣称在线审核模型已验证。
