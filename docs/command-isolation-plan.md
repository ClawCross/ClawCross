# 命令隔离与系统提权方案

## 当前状态

- 系统控制前台命令提权；Agent 没有独立提权工具，也不能指定提权参数。
- Auto 只批准或拒绝；人工按键和对话 Y/N 保留。
- 本机原生 SRT 仍因嵌套 user namespace 初始化失败而无法执行，不视为普通命令权限拒绝。
- 未安装 Docker、Podman、gVisor；未发现 `/dev/kvm`。没有修改 AppArmor、sysctl 或关闭 seccomp。
- 最大提权范围默认为空；管理员设置普通路径和精确域名后才可审核。审批不能授予 host 权限。

## 推荐部署：受限容器执行器，gVisor systrap 可选强化

虚拟机内可以再用容器。root 管理的容器运行时创建隔离，不需要为普通应用放开 AppArmor 的 user namespace 策略。gVisor 的 systrap 在虚拟机内不需要嵌套 KVM；不要采用依赖当前受限 user namespace 的 rootless 方案来假设已经解决兼容性。

1. 命令执行器作为独立服务，ClawCross 只能提交固定结构的执行请求。服务验证用户/Agent/工作区，不接受任意 Docker 参数或挂载源。
2. 只挂载当前用户当前工作区。镜像固定版本，由管理员显式拉取；不挂载宿主 home、仓库配置、其他用户目录、Docker socket 或宿主 Python 环境。
3. 默认只读根文件系统，独立 `/tmp`，容器内非 root 用户，删除所有 capabilities，开启 no-new-privileges 和 seccomp，禁止 privileged、host PID/network 和设备映射。
4. 默认拒绝出网；批准的域名走独立代理，代理还须禁止解析后的内网/loopback/云 metadata 地址，不能仅按域名字符串判断。
5. 使用 cgroup 限制整棵进程树：示例 CPU 1 核、内存 1 GiB、PID 64；同时限制运行时间、临时文件大小、输出量和并发数。当前 SRT 的 RLIMIT 不等同于进程树总资源配额。
6. 容器 runtime socket 仅执行器可访问；Agent、MCP 和容器内进程都不能访问。执行器应验证挂载路径的真实目标和文件身份，防止符号链接与检查/使用竞态。
7. 可在相同执行器上选择 gVisor/runsc 的 systrap 平台。先验证所需命令、文件系统和网络代理的兼容性，再启用；普通 OCI 容器与 gVisor 是不同强度，界面应明确标识。

Docker daemon 具有强大宿主权限；不能把当前服务用户加入 docker 组后让 Agent 任意调用 Docker。仅安装 Docker 不构成安全执行器。

## 系统提权状态机

`校验工具名单/绝对拦截 → 沙盒执行 → 成功返回 / 失败分类 → 最小权限候选 → 上限检查 → 审核 → 一次有限重试 → 最终结果`

- 初始化失败、超时、一般程序错误、不明确的 EACCES：停止并返回原因，不申请 host 或关闭沙盒。
- 明确拒绝一个路径或域名：失败证据作为不可信材料，审核仍须读取原始人类请求。
- Auto 拒绝：告诉 Agent 所缺授权；人类后续自然语言同意进入普通对话，下一次审核读取它。不把自然语言直接当成按钮操作。
- 人工审核：横幅和对话气泡共享审批编号；Y/N 对应原有按键。批准在上限内且仅对应该命令/参数/策略的一次重试。
- 第一次执行可能已产生部分副作用；批准重试需评估整条命令重放的影响。不能只凭 stderr 含有 permission denied 就自动放宽。

## 后台与交互执行的后续接入

当前这两类任务保留原有隔离与最终日志，不自动重放，避免重复后台作业或重放已输入的交互操作。要与前台统一，需要由独立执行器维护可信任务记录：保存原始命令、失败阶段、审批 ID 和权限快照，完成通知由系统发起审核，不依赖 Agent 轮询；只有能确认可安全重试的阶段才重放。交互终端已执行的输入不能整体重放。此接入尚未实现。

## 实施边界

目前只完成现有前台 SRT 路径与审核语义调整。容器/gVisor 后端及后台/交互提权监督器尚未实现或部署。安装系统运行时、启动特权执行器与拉取镜像需单独显式执行，均不是默认启动下载项。

## 官方资料

- [Docker Engine security](https://docs.docker.com/engine/security/)：namespaces、cgroups 与 daemon 权限风险。
- [gVisor platforms](https://gvisor.dev/docs/user_guide/platforms/)：虚拟机中的 systrap 平台与 KVM 平台区别。
- [Anthropic sandbox-runtime](https://github.com/anthropics/sandbox-runtime)：Linux bwrap、seccomp 与 namespace 要求。
