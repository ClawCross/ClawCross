import sys as _sys
import os as _os
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

"""
MCP 指令执行工具服务 — 审核和可选的 Auto SRT 隔离
- 每个用户有独立的工作目录 (data/user_files/<username>/)
- 支持白名单/黑名单两种命令准入模式
- 超时保护、输出截断、路径穿越防护
- 跨平台支持（Linux/macOS/Windows）
"""

import os
import sys
import asyncio
import contextlib
import hashlib
from collections import deque
import json
import re
import shlex
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
import time
import uuid
from typing import Literal

import httpx
from dotenv import load_dotenv
from utils.mcp_tool_docs import DocumentedFastMCP as FastMCP
from utils.runtime_paths import ENV_FILE, USER_FILES_DIR

from webot.workspace import resolve_session_workspace
from webot.command_sandbox import build_srt_command, normalize_escalation, SandboxUnavailable, SrtCommand
from webot.approval_review import authorize_action, policy_binding
from webot.approval_actions import canonical_action_args
from webot.runtime_store import consume_execution_permit, get_session_mode
from utils.bash_safety import analyze_command, RiskLevel
from utils.bg_notify import register_pending_notify

mcp = FastMCP("Commander")

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 加载 .env 配置
load_dotenv(dotenv_path=str(ENV_FILE))

# 用户文件根目录（与 mcp_filemanager.py 共享）
USER_FILES_BASE = str(USER_FILES_DIR)

# 平台检测
IS_WINDOWS = sys.platform == "win32"

# ======== 安全配置（支持 .env 自定义）========

# 内置默认白名单（按平台区分）
if IS_WINDOWS:
    _DEFAULT_COMMANDS = {
        # 文件与目录
        "dir", "type", "more", "find", "findstr", "where", "tree",
        "copy", "move", "ren",
        # 文本处理
        "sort", "fc",
        # 系统信息（只读）
        "echo", "date", "time", "whoami", "hostname",
        "systeminfo", "set", "ver", "vol",
        "tasklist", "wmic",
        # 实用工具
        "cd", "chdir", "certutil",
        # Python
        "python", "python3",
        # 网络（只读）
        "ping", "curl", "ipconfig", "nslookup", "tracert", "netstat",
        # PowerShell 常用（安全子集）
        "powershell","npm","npx","git","node"
    }
else:
    _DEFAULT_COMMANDS = {
        # 文件与目录
        "ls", "cat", "head", "tail", "wc", "du", "find", "file", "stat",
        "rg", "nl", "mkdir", "touch", "cp", "mv",
        "dirname", "basename", "realpath", "readlink", "split",
        # 文本处理
        "grep", "awk", "sed", "sort", "uniq", "cut", "tr", "diff", "comm",
        "paste", "printf", "xargs", "jq", "cmp", "tee",
        # 系统信息（只读）
        "echo", "date", "cal", "whoami", "uname", "hostname",
        "uptime", "free", "df", "env", "printenv", "ps",
        # 实用工具
        "pwd", "which", "expr", "seq", "yes", "true", "false",
        "sleep", "timeout", "time",
        "base64", "md5sum", "sha256sum", "xxd",
        "tar", "zip", "unzip",
        # Python
        "python", "python3",
        # 网络（只读）
        "ping", "curl", "wget",
        "npm","npx","git","node"
    }

# 从 .env 读取用户自定义白名单，留空或不设置则使用默认
_env_commands = os.getenv("ALLOWED_COMMANDS", "").strip()
if _env_commands:
    ALLOWED_COMMANDS = {cmd.strip() for cmd in _env_commands.split(",") if cmd.strip()}
else:
    ALLOWED_COMMANDS = _DEFAULT_COMMANDS

COMMANDER_COMMAND_MODE = (os.getenv("COMMANDER_COMMAND_MODE", "blacklist") or "").strip().lower()
if COMMANDER_COMMAND_MODE not in {"whitelist", "blacklist"}:
    COMMANDER_COMMAND_MODE = "blacklist"

# 黑名单模式下额外拦截的高危基础命令名（命中即拒绝）
if IS_WINDOWS:
    BLOCKED_COMMANDS = {
        "del", "erase", "format", "diskpart", "shutdown", "restart", "logoff",
        "reg", "runas", "schtasks", "taskkill",
    }
else:
    BLOCKED_COMMANDS = {
        "rm", "sudo", "su", "shutdown", "reboot", "halt", "poweroff",
        "systemctl", "service", "init", "mkfs", "dd", "mount", "umount",
        "iptables",
    }

# 严格禁止的命令（即使在白名单中也拒绝这些子命令/参数模式）
if IS_WINDOWS:
    BLOCKED_PATTERNS = [
        "del /s /q c:\\", "format ", "diskpart", "bcdedit",
        "reg delete", "reg add",
        "shutdown", "restart", "logoff",
        "net user", "net localgroup", "runas",
        "taskkill /f /im", "schtasks /delete",
        "powershell -enc", "powershell -e ",  # 编码执行，可绕过审查
        "invoke-expression", "iex ", "iex(",
        "remove-item -recurse -force c:\\",
    ]
else:
    BLOCKED_PATTERNS = [
        "rm -rf /", "rm -rf /*", "mkfs", "dd if=", ":(){ :", "fork bomb",
        "> /dev/sd", "chmod 777 /", "chown root", "/etc/passwd", "/etc/shadow",
        "sudo", "su ", "shutdown", "reboot", "halt", "poweroff",
        "systemctl", "service ", "init ",
    ]

# 执行超时（秒）— 支持 .env 自定义
EXEC_TIMEOUT = int(os.getenv("EXEC_TIMEOUT", "180"))
BACKGROUND_EXEC_TIMEOUT = int(os.getenv("BACKGROUND_EXEC_TIMEOUT", str(max(EXEC_TIMEOUT, 300))))
MAX_EXEC_TIMEOUT = int(os.getenv("MAX_EXEC_TIMEOUT", "1800"))

# 输出最大长度（字符数）— 支持 .env 自定义
MAX_OUTPUT_LENGTH = int(os.getenv("MAX_OUTPUT_LENGTH", "8000"))
MAX_CAPTURE_LENGTH = int(os.getenv("MAX_CAPTURE_LENGTH", str(max(MAX_OUTPUT_LENGTH, 20000))))
DEFAULT_BACKGROUND_READ_CHARS = 12000
MAX_BACKGROUND_READ_CHARS = 50000
_BACKGROUND_JOBS: dict[str, "BackgroundJob"] = {}
_DETACHED_RUNNERS: list[subprocess.Popen] = []
_SANDBOX_RETRY_HINT = (
    "沙盒报告了权限拒绝。核对具体路径或域名后，可用同一 run_command 的 "
    "sandbox_access 与 escalation_target 申请单次提权；本次命令不会自动重跑。"
)


@dataclass
class BackgroundJob:
    job_id: str
    username: str
    command: str
    workspace: str
    mode: str
    remote: str
    stdout_path: str
    stderr_path: str
    timeout_seconds: int
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    status: str = "running"
    exit_code: int | None = None
    error: str = ""
    session_id: str = ""
    pid: int | None = None
    notify_on_done: bool = False   # opt-in：任务完成时唤醒发起的 agent 会话（system_trigger）
    interactive: bool = False      # 在伪终端里运行，可经 stdin_path（FIFO）输入
    stdin_path: str = ""


def _jobs_dir(workspace: str) -> Path:
    path = Path(workspace) / ".mcp_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_meta_path(workspace: str, job_id: str) -> Path:
    return _jobs_dir(workspace) / f"{job_id}.json"


def _persist_job(job: BackgroundJob) -> None:
    payload = {
        "job_id": job.job_id,
        "username": job.username,
        "command": job.command,
        "workspace": job.workspace,
        "mode": job.mode,
        "remote": job.remote,
        "stdout_path": job.stdout_path,
        "stderr_path": job.stderr_path,
        "timeout_seconds": job.timeout_seconds,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "status": job.status,
        "exit_code": job.exit_code,
        "error": job.error,
        "session_id": job.session_id,
        "pid": job.pid,
        "notify_on_done": job.notify_on_done,
        "interactive": job.interactive,
        "stdin_path": job.stdin_path,
    }
    _job_meta_path(job.workspace, job.job_id).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_job_from_workspace(workspace: str, job_id: str) -> BackgroundJob | None:
    path = _job_meta_path(workspace, job_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return BackgroundJob(
        job_id=str(payload.get("job_id") or job_id),
        username=str(payload.get("username") or ""),
        command=str(payload.get("command") or ""),
        workspace=str(payload.get("workspace") or workspace),
        mode=str(payload.get("mode") or "shared"),
        remote=str(payload.get("remote") or ""),
        stdout_path=str(payload.get("stdout_path") or (_jobs_dir(workspace) / f"{job_id}.stdout.log")),
        stderr_path=str(payload.get("stderr_path") or (_jobs_dir(workspace) / f"{job_id}.stderr.log")),
        timeout_seconds=int(payload.get("timeout_seconds") or BACKGROUND_EXEC_TIMEOUT),
        started_at=float(payload.get("started_at") or time.time()),
        finished_at=float(payload["finished_at"]) if payload.get("finished_at") is not None else None,
        status=str(payload.get("status") or "unknown"),
        exit_code=payload.get("exit_code"),
        error=str(payload.get("error") or ""),
        session_id=str(payload.get("session_id") or ""),
        pid=int(payload["pid"]) if payload.get("pid") is not None else None,
        notify_on_done=bool(payload.get("notify_on_done") or False),
        interactive=bool(payload.get("interactive") or False),
        stdin_path=str(payload.get("stdin_path") or ""),
    )


def _reap_detached_runners() -> None:
    _DETACHED_RUNNERS[:] = [proc for proc in _DETACHED_RUNNERS if proc.poll() is None]


# ── 后台任务完成主动推送（opt-in：notify_on_done=True 时才生效）──────────────
# NOTE: 通知由长驻的主进程（mainagent）驱动，不在 commander 进程内 watch。
# commander 是 per-tool-call 的短命 stdio 子进程，工具一返回进程就被销毁，进程内
# 的 asyncio watcher 会随之死掉、永不发通知；而 detached runner 处于沙箱、没有
# INTERNAL_TOKEN，也无法自己补发。所以这里只「登记」一个待通知指针（见
# utils.bg_notify.register_pending_notify），由 mainagent 的 background_notify_loop
# 轮询、在任务达终态时调用 /system_trigger 唤醒发起会话。


def _pid_is_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        if IS_WINDOWS:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
            return str(pid) in result.stdout
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _refresh_background_job(job: BackgroundJob) -> BackgroundJob:
    if job.status != "running":
        return job
    fresh = _load_job_from_workspace(job.workspace, job.job_id)
    if fresh is not None and fresh.status != "running":
        return fresh
    if _pid_is_running(job.pid):
        return fresh or job
    job.status = "failed"
    job.error = job.error or "后台 runner 已退出但未写入最终状态。"
    job.finished_at = job.finished_at or time.time()
    _persist_job(job)
    return job


def _resolve_background_job(job_id: str, username: str = "", session_id: str = "", cwd: str = "") -> BackgroundJob | None:
    _reap_detached_runners()
    key = (job_id or "").strip()
    if not key:
        return None
    live = _BACKGROUND_JOBS.get(key)
    if live is not None:
        refreshed = _refresh_background_job(live)
        _BACKGROUND_JOBS[key] = refreshed
        return refreshed
    workspace_state = resolve_session_workspace(username, session_id, explicit_cwd=cwd)
    job = _load_job_from_workspace(str(workspace_state.cwd), key)
    if job is None:
        return None
    return _refresh_background_job(job)


_RUNNER_SCRIPT = """#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request


def _write_meta(meta_path, updates):
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        payload = {}
    payload.update(updates)
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _notify_done(cfg):
    # Event push: tell the long-lived main process this job finished so it can
    # wake the originating session. Carries only the job id (no token); the main
    # process resolves the session from its own trusted pointer. Best-effort.
    url = cfg.get("notify_url")
    job_id = cfg.get("job_id")
    if not url or not job_id:
        return
    try:
        data = json.dumps({"job_id": job_id}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).close()
    except Exception:
        pass


def main():
    cfg = json.loads(sys.argv[1])
    meta_path = cfg["meta_path"]
    proc = None
    started = time.time()
    try:
        with open(cfg["stdout_path"], "ab", buffering=0) as stdout_handle, open(
            cfg["stderr_path"], "ab", buffering=0
        ) as stderr_handle:
            kwargs = {
                "shell": not bool(cfg.get("exec_argv")),
                "cwd": cfg["workspace"],
                "env": cfg["env"],
                "stdout": stdout_handle,
                "stderr": stderr_handle,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(cfg.get("exec_argv") or cfg["command"], **kwargs)
            _write_meta(meta_path, {"child_pid": proc.pid, "status": "running"})
            try:
                return_code = proc.wait(timeout=int(cfg["timeout_seconds"]))
                status = "completed" if return_code == 0 else "failed"
                _write_meta(
                    meta_path,
                    {
                        "status": status,
                        "exit_code": return_code,
                        "finished_at": time.time(),
                    },
                )
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    proc.kill()
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
                return_code = proc.wait()
                _write_meta(
                    meta_path,
                    {
                        "status": "timeout",
                        "exit_code": return_code,
                        "finished_at": time.time(),
                        "error": f"命令执行超时（{cfg['timeout_seconds']}秒限制），已终止。",
                    },
                )
    except BaseException as exc:
        _write_meta(
            meta_path,
            {
                "status": "failed",
                "finished_at": time.time(),
                "error": str(exc),
            },
        )
    finally:
        for path in (cfg.get("sandbox_settings_path"), cfg.get("cleanup_script")):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
    # Terminal meta is written on every path above — push the completion event.
    _notify_done(cfg)


if __name__ == "__main__":
    main()
"""


def _write_runner_script(jobs_dir: Path, script: str | None = None, prefix: str = "runner") -> Path:
    # Version the cached script by content hash so template changes auto-invalidate
    # it. A fixed "runner.py" name would be reused forever and silently run stale
    # logic (e.g. miss the completion push), since the old write was skipped
    # whenever the file already existed.
    script = _RUNNER_SCRIPT if script is None else script
    digest = hashlib.sha1(script.encode("utf-8")).hexdigest()[:10]
    script_path = jobs_dir / f"{prefix}_{digest}.py"
    if not script_path.exists():
        script_path.write_text(script, encoding="utf-8")
    return script_path


def _launch_detached_background_job(
    job: BackgroundJob, env: dict[str, str], *, sandbox: SrtCommand | None = None,
    cleanup_script: str = "",
) -> None:
    jobs_dir = _jobs_dir(job.workspace)
    runner_path = (
        _write_runner_script(jobs_dir, _PTY_RUNNER_SCRIPT, "pty_runner")
        if job.interactive
        else _write_runner_script(jobs_dir)
    )
    payload = {
        "command": job.command,
        "workspace": job.workspace,
        "env": env,
        "stdout_path": job.stdout_path,
        "stderr_path": job.stderr_path,
        "meta_path": str(_job_meta_path(job.workspace, job.job_id)),
        "timeout_seconds": job.timeout_seconds,
        "job_id": job.job_id,
        "stdin_path": job.stdin_path,
        "exec_argv": list(sandbox.argv) if sandbox is not None else None,
        "sandbox_settings_path": str(sandbox.settings_path) if sandbox is not None else "",
        "cleanup_script": cleanup_script,
    }
    if job.notify_on_done and job.session_id:
        # Loopback push target; main process resolves the session from its
        # trusted pointer, so the sandboxed runner needs no token.
        port_agent = os.getenv("PORT_AGENT", "51200")
        payload["notify_url"] = f"http://127.0.0.1:{port_agent}/internal/bg_job_done"
    kwargs = {
        "cwd": job.workspace,
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": not IS_WINDOWS,
    }
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    runner = subprocess.Popen(
        [_python_cmd(), str(runner_path), json.dumps(payload, ensure_ascii=False)],
        **kwargs,
    )
    _DETACHED_RUNNERS.append(runner)
    job.pid = runner.pid
    _persist_job(job)


def _terminate_background_job(job: BackgroundJob) -> None:
    child_pid = None
    with contextlib.suppress(Exception):
        payload = json.loads(_job_meta_path(job.workspace, job.job_id).read_text(encoding="utf-8"))
        if payload.get("child_pid") is not None:
            child_pid = int(payload["child_pid"])
    try:
        if IS_WINDOWS:
            if child_pid and _pid_is_running(child_pid):
                subprocess.run(
                    ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                )
        else:
            if child_pid and _pid_is_running(child_pid):
                os.killpg(child_pid, signal.SIGKILL)
        # Let the runner observe the child exit and remove its private SRT
        # policy and Python script before terminating the runner itself.
        for _ in range(20):
            fresh = _load_job_from_workspace(job.workspace, job.job_id)
            if fresh is not None and fresh.status != "running":
                break
            time.sleep(0.05)
        else:
            if job.pid and _pid_is_running(job.pid):
                if IS_WINDOWS:
                    subprocess.run(["taskkill", "/PID", str(job.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                else:
                    os.killpg(job.pid, signal.SIGKILL)
    except Exception:
        for pid in (child_pid, job.pid):
            if pid:
                with contextlib.suppress(Exception):
                    os.kill(pid, signal.SIGKILL)



def _bounded_int(value: int, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed <= 0:
        parsed = default
    return max(minimum, min(parsed, maximum))


class _StreamingCapture:
    def __init__(self, limit: int) -> None:
        self.limit = max(256, limit)
        self._current: list[str] = []
        self._current_len = 0
        self._truncated = False
        self._head = ""
        self._tail: deque[str] = deque()
        self._tail_len = 0
        self._head_limit = self.limit // 2
        self._tail_limit = self.limit - self._head_limit
        self.total_chars = 0

    def append(self, text: str) -> None:
        if not text:
            return
        self.total_chars += len(text)
        if not self._truncated:
            self._current.append(text)
            self._current_len += len(text)
            if self._current_len > self.limit:
                joined = "".join(self._current)
                self._head = joined[:self._head_limit]
                self._set_tail(joined[-self._tail_limit :])
                self._current = []
                self._current_len = 0
                self._truncated = True
            return
        self._append_tail(text)

    def _set_tail(self, text: str) -> None:
        self._tail.clear()
        self._tail_len = 0
        self._append_tail(text)

    def _append_tail(self, text: str) -> None:
        if not text:
            return
        self._tail.append(text)
        self._tail_len += len(text)
        while self._tail_len > self._tail_limit and self._tail:
            overflow = self._tail_len - self._tail_limit
            first = self._tail[0]
            if len(first) <= overflow:
                self._tail.popleft()
                self._tail_len -= len(first)
            else:
                self._tail[0] = first[overflow:]
                self._tail_len -= overflow

    def render(self) -> str:
        if not self._truncated:
            return "".join(self._current)
        omitted = max(0, self.total_chars - len(self._head) - self._tail_len)
        tail = "".join(self._tail)
        return (
            self._head
            + f"\n\n... [输出过长，已截断，省略约 {omitted} 字符] ...\n\n"
            + tail
        )

def _sandbox_env(workspace: str, username: str) -> dict:
    """构造最小宿主机命令环境；本身不提供 OS 级沙盒。"""
    if IS_WINDOWS:
        return {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
            "COMSPEC": os.environ.get("COMSPEC", r"C:\Windows\system32\cmd.exe"),
            "USERPROFILE": workspace,
            "USERNAME": username,
            "TEMP": os.environ.get("TEMP", workspace),
            "TMP": os.environ.get("TMP", workspace),
        }
    else:
        return {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": workspace,
            "USER": username,
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "TERM": "xterm",
        }

def _python_cmd() -> str:
    """返回当前平台的 Python 命令名"""
    return sys.executable

def _user_workspace(username: str, session_id: str = "", cwd: str = "") -> str:
    """获取用户独立工作目录，自动创建"""
    return str(resolve_session_workspace(username, session_id, explicit_cwd=cwd).cwd)

def _validate_command(command: str) -> str | None:
    """
    验证命令安全性，返回 None 表示通过，返回字符串表示拒绝原因
    """
    stripped = command.strip()
    if not stripped:
        return "命令不能为空"

    # 白名单模式：命令名必须在允许列表中（黑名单模式下跳过此检查，由上层审批机制控制）
    import re
    parts = re.split(r'[;|&]+', stripped)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 获取命令名（去掉可能的 env 变量赋值前缀）
        tokens = part.split()
        cmd_name = None
        for token in tokens:
            if "=" in token and token.index("=") > 0:
                continue  # 跳过 VAR=value 形式的环境变量
            cmd_name = os.path.basename(token)  # 取基础命令名
            break

        if not cmd_name:
            continue

        if COMMANDER_COMMAND_MODE == "whitelist" and cmd_name not in ALLOWED_COMMANDS:
            return f"安全策略拒绝：命令 '{cmd_name}' 不在白名单中。允许的命令：{', '.join(sorted(ALLOWED_COMMANDS))}"

    return None


async def _wait_for_command_approval(
    username: str,
    session_id: str,
    command: str,
    reason: str,
    *,
    tool_name: str = "run_command",
    action_args: dict | None = None,
) -> tuple[bool, str]:
    from webot.policy import ToolPolicyDecision
    args = dict(action_args if action_args is not None else {"command": command})
    args.update(username=username, session_id=session_id or "default")
    result = await authorize_action(
        user_id=username, session_id=session_id or "default", tool_name=tool_name,
        args=args, decision=ToolPolicyDecision(allowed=False, requires_approval=True, reason=reason),
    )
    return result.allowed, result.reason


def _truncate_output(text: str, max_len: int = MAX_OUTPUT_LENGTH) -> str:
    """截断过长输出"""
    if len(text) <= max_len:
        return text
    half = max_len // 2
    return (
        text[:half]
        + f"\n\n... [输出过长，已截断，共 {len(text)} 字符] ...\n\n"
        + text[-half:]
    )


async def _consume_stream(stream: asyncio.StreamReader | None, capture: _StreamingCapture) -> None:
    if stream is None:
        return
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        capture.append(chunk.decode("utf-8", errors="replace"))


async def _stop_sandbox_group(proc: asyncio.subprocess.Process) -> None:
    """Give SRT a chance to restore mounts/ACLs, then stop stragglers."""
    if os.name == "nt":
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)


async def _collect_process_output(
    proc: asyncio.subprocess.Process,
    *,
    timeout_seconds: int,
    max_output_chars: int,
    process_group: bool = False,
) -> tuple[bool, str, str]:
    stdout_capture = _StreamingCapture(max_output_chars)
    stderr_capture = _StreamingCapture(max_output_chars)
    stdout_task = asyncio.create_task(_consume_stream(proc.stdout, stdout_capture))
    stderr_task = asyncio.create_task(_consume_stream(proc.stderr, stderr_capture))
    try:
        await asyncio.wait_for(
            asyncio.gather(proc.wait(), stdout_task, stderr_task),
            timeout=timeout_seconds,
        )
        return False, stdout_capture.render().strip(), stderr_capture.render().strip()
    except asyncio.TimeoutError:
        if process_group:
            await _stop_sandbox_group(proc)
        else:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        return True, stdout_capture.render().strip(), stderr_capture.render().strip()


def _job_summary(job: BackgroundJob) -> str:
    lines = [
        f"🆔 job_id: {job.job_id}",
        f"📁 工作目录: {job.workspace}",
        f"🧭 workspace mode: {job.mode}",
        f"📌 状态: {job.status}" + ("（交互）" if job.interactive else ""),
        f"⏱️ timeout: {job.timeout_seconds}s",
    ]
    if job.remote:
        lines.append(f"🌐 remote: {job.remote}")
    if job.exit_code is not None:
        lines.append(f"🚪 exit_code: {job.exit_code}")
    if job.error:
        lines.append(f"⚠️ error: {job.error}")
    if job.status in {"failed", "completed"}:
        with contextlib.suppress(OSError):
            if "<sandbox_violations>" in Path(job.stderr_path).read_text(encoding="utf-8", errors="replace"):
                lines.append(_SANDBOX_RETRY_HINT)
    lines.append(f"📤 stdout: {job.stdout_path}")
    lines.append(f"📤 stderr: {job.stderr_path}")
    return "\n".join(lines)

_PTY_RUNNER_SCRIPT = """#!/usr/bin/env python3
# Owns one interactive program on a pseudo-terminal for as long as it runs.
# The MCP server lives for a single tool call, so the terminal cannot live
# there: this runner keeps it, appends everything the program prints to the
# job's stdout log, and forwards whatever is written to the job's stdin FIFO.
import json
import os
import pty
import select
import signal
import sys
import time
import urllib.request


def _write_meta(meta_path, updates):
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        payload = {}
    payload.update(updates)
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _notify_done(cfg):
    url = cfg.get("notify_url")
    job_id = cfg.get("job_id")
    if not url or not job_id:
        return
    try:
        data = json.dumps({"job_id": job_id}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).close()
    except Exception:
        pass


def _drain(master, out):
    while True:
        ready, _, _ = select.select([master], [], [], 0.1)
        if not ready:
            return
        try:
            data = os.read(master, 65536)
        except OSError:
            return
        if not data:
            return
        out.write(data)


def main():
    cfg = json.loads(sys.argv[1])
    meta_path = cfg["meta_path"]
    fifo_path = cfg["stdin_path"]
    final = {"status": "failed", "error": ""}
    pid = None
    try:
        if not os.path.exists(fifo_path):
            os.mkfifo(fifo_path, 0o600)
        # O_RDWR keeps a reader and a writer open on the FIFO, so select()
        # never sees EOF between two tool calls writing input.
        in_fd = os.open(fifo_path, os.O_RDWR | os.O_NONBLOCK)
        pid, master = pty.fork()
        if pid == 0:
            os.chdir(cfg["workspace"])
            argv = cfg.get("exec_argv") or ["/bin/sh", "-c", cfg["command"]]
            os.execvpe(argv[0], argv, cfg["env"])
        try:
            import fcntl
            import struct
            import termios
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        except Exception:
            pass
        _write_meta(meta_path, {"child_pid": pid, "status": "running"})
        deadline = time.time() + int(cfg["timeout_seconds"])
        wait_status = None
        with open(cfg["stdout_path"], "ab", buffering=0) as out:
            while True:
                if time.time() > deadline:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except OSError:
                        pass
                    os.waitpid(pid, 0)
                    final = {
                        "status": "timeout",
                        "error": "交互任务超时（%s秒限制），已终止。" % cfg["timeout_seconds"],
                    }
                    break
                try:
                    ready, _, _ = select.select([master, in_fd], [], [], 0.2)
                except InterruptedError:
                    continue
                if master in ready:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        data = b""
                    if data:
                        out.write(data)
                if in_fd in ready:
                    try:
                        data = os.read(in_fd, 65536)
                    except BlockingIOError:
                        data = b""
                    if data:
                        os.write(master, data)
                done_pid, status = os.waitpid(pid, os.WNOHANG)
                if done_pid:
                    wait_status = status
                    _drain(master, out)
                    break
        if wait_status is not None:
            exit_code = os.waitstatus_to_exitcode(wait_status)
            final = {"status": "completed" if exit_code == 0 else "failed", "exit_code": exit_code, "error": ""}
    except BaseException as exc:
        final = {"status": "failed", "error": str(exc)}
        if pid:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
    final["finished_at"] = time.time()
    _write_meta(meta_path, final)
    try:
        os.unlink(fifo_path)
    except OSError:
        pass
    for path in (cfg.get("sandbox_settings_path"), cfg.get("cleanup_script")):
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
    _notify_done(cfg)


if __name__ == "__main__":
    main()
"""

# Terminal output carries colour codes, cursor moves, and carriage-return
# redraws that mean nothing to a model reading text.
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_=>78]")
_SHELL_PROGRAMS = {"bash", "sh", "zsh", "fish", "dash", "ksh", "csh", "tcsh", "cmd", "powershell", "pwsh"}


def _clean_terminal_text(text: str) -> str:
    text = _ANSI_RE.sub("", text).replace("\r\n", "\n")
    # A bare \r redraws the line in place: keep what the screen ends up showing.
    return "\n".join(line.rstrip("\r").rsplit("\r", 1)[-1] for line in text.split("\n"))


def _program_name(command: str) -> str:
    for token in command.strip().split():
        if "=" in token and token.index("=") > 0:
            continue
        return os.path.basename(token)
    return ""


def _quote_command(parts: list[str]) -> str:
    return subprocess.list2cmdline(parts) if IS_WINDOWS else shlex.join(parts)


def _write_python_script(workspace: str, code: str) -> str:
    """Write *code* to its own file: concurrent calls must not share one script path."""
    path = _jobs_dir(workspace) / f"py_{uuid.uuid4().hex[:12]}.py"
    path.write_text(code, encoding="utf-8")
    return str(path)


async def _command_safety_gate(
    username: str, session_id: str, command: str, *, check_names: bool = True,
    tool_name: str = "run_command", action_args: dict | None = None,
) -> tuple[str | None, str]:
    """Run the command checks; return (rejection message or None, approval note)."""
    normalized_session = session_id or "default"
    normalized_args = canonical_action_args(tool_name, {
        **(action_args or {"command": command}), "username": username, "session_id": normalized_session,
    })
    from webot.runtime import effective_session_mode
    mode = effective_session_mode(username, normalized_session)
    if mode in {"chat", "readonly", "plan", "review"}:
        return f"❌ 当前会话处于 {mode} 模式，禁止执行命令或输入。", ""
    if check_names:
        reject_reason = _validate_command(command)
        if reject_reason:
            return f"❌ {reject_reason}", ""
    cmd_analysis = analyze_command(command)
    if cmd_analysis.blocked or cmd_analysis.risk_level == RiskLevel.CRITICAL:
        return f"❌ 命令被安全策略阻止: {'; '.join(cmd_analysis.reasons)}", ""
    if consume_execution_permit(username, normalized_session, tool_name, normalized_args, policy_binding(username, normalized_session)):
        return None, "✅ 当前操作已通过统一审核。"
    result = await authorize_action(
        user_id=username, session_id=normalized_session, tool_name=tool_name, args=normalized_args,
        risk_reason=f"高风险命令需批准: {'; '.join(cmd_analysis.reasons)}" if cmd_analysis.risk_level == RiskLevel.HIGH else "",
    )
    return (None, result.reason) if result.allowed else ("❌ " + result.reason, "")


async def _run_foreground(
    argv_or_command: list[str] | str,
    *,
    label: str,
    workspace_state,
    username: str,
    timeout_value: int,
    capture_limit: int,
    approval_note: str,
    sandbox: SrtCommand | None = None,
) -> str:
    workspace = str(workspace_state.cwd)
    # SRT inherits its own environment into the wrapped process. Never hand
    # API keys or other host environment secrets to that process.
    env = _sandbox_env(workspace, username)
    if sandbox is not None:
        env["PATH"] = os.environ.get("PATH", env["PATH"])
        env["TMPDIR"] = str(Path(sandbox.settings_path).parent)
    if isinstance(argv_or_command, str):
        proc = await asyncio.create_subprocess_shell(
            argv_or_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=env,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *argv_or_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=env,
            start_new_session=sandbox is not None and os.name != "nt",
        )
    try:
        timed_out, out, err = await _collect_process_output(
            proc,
            timeout_seconds=timeout_value,
            max_output_chars=capture_limit,
            process_group=sandbox is not None,
        )
    finally:
        if proc.returncode is None:
            if sandbox is not None:
                await _stop_sandbox_group(proc)
            else:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
        elif sandbox is not None and os.name != "nt":
            # A wrapped program may have left children after the CLI exits.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        if sandbox is not None:
            sandbox.settings_path.unlink(missing_ok=True)
    location = [f"📁 工作目录: {workspace}", f"🧭 workspace mode: {workspace_state.mode}"]
    if workspace_state.remote:
        location.append(f"🌐 remote: {workspace_state.remote}")
    if timed_out:
        parts = [f"⏱️ {label}执行超时（{timeout_value}秒限制），已终止。", *location]
        if out:
            parts.append(f"📤 截止超时前的标准输出:\n{out}")
        if err:
            parts.append(f"📤 截止超时前的标准错误:\n{err}")
        return "\n\n".join(parts)
    parts = [approval_note] if approval_note else []
    if proc.returncode == 0:
        parts.append(f"✅ {label}执行成功 (exit code: 0)")
    else:
        parts.append(f"⚠️ {label}执行完毕 (exit code: {proc.returncode})")
    parts.extend(location)
    if out:
        parts.append(f"📤 标准输出:\n{out}")
    if err:
        parts.append(f"📤 标准错误:\n{err}")
    if sandbox is not None and "<sandbox_violations>" in err:
        parts.append(_SANDBOX_RETRY_HINT)
    if not out and not err:
        parts.append("(无输出)")
    return "\n\n".join(parts)


async def _wait_for_output(path: str, start: int, wait_seconds: float, job: "BackgroundJob") -> None:
    """Wait until the program's output settles (or wait_seconds pass)."""
    deadline = time.monotonic() + wait_seconds
    last_size, last_change = start, time.monotonic()
    while time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        now = time.monotonic()
        if size != last_size:
            last_size, last_change = size, now
        elif size > start and now - last_change >= 0.6:
            return
        if _refresh_background_job(job).status != "running" and now - last_change >= 0.3:
            return


@mcp.tool()
async def run_command(
    username: str,
    command: str,
    language: Literal["shell", "python"] = "shell",
    mode: Literal["foreground", "background", "interactive"] = "foreground",
    session_id: str = "",
    cwd: str = "",
    timeout_seconds: int = 0,
    max_output_chars: int = 0,
    notify_on_done: bool = False,
    sandbox_access: Literal["default", "read_path", "write_path", "network", "host"] = "default",
    escalation_target: str = "",
    escalation_reason: str = "",
) -> str:
    """
    在会话工作目录中运行 shell 命令或 Python 代码。mode=foreground 等待结束并返回输出；
    background 立即返回 job_id，适合长任务；interactive 在终端里启动交互式程序（如
    python、bash、ssh），之后用 background_command_io 输入并查看输出。检查代码可直接
    运行 python -m py_compile、node --check 或 npx tsc --noEmit。

    :param command: shell 命令；language=python 时是 Python 代码（interactive 下先执行它再进入 REPL，可为空）
    :param language: shell 或 python
    :param mode: foreground / background / interactive
    :param cwd: 工作目录，相对当前会话工作区解析；留空用会话工作区根目录
    :param timeout_seconds: 超时秒数；0 表示默认值（前台 180，后台和交互至少 300），上限 MAX_EXEC_TIMEOUT
    :param max_output_chars: 前台模式最多返回的输出字符数；0 表示默认值（8000），最小 256
    :param notify_on_done: 后台或交互任务结束后用一条系统消息唤醒本会话并附上状态和输出尾部，无需轮询
    :param sandbox_access: default 使用当前 SRT 沙盒；read_path / write_path / network 仅放宽一个目标；host 请求本次命令在宿主机运行。提权均需单次审核，不自动重跑失败命令
    :param escalation_target: read_path/write_path 为已存在的绝对路径，network 为一个域名或域名:端口；default/host 留空
    :param escalation_reason: 提权时说明所需权限和此前的失败；理由本身不能替代用户授权
    """
    is_python = language == "python"
    interactive = mode == "interactive"
    if not command.strip() and not (is_python and interactive):
        return "❌ command 不能为空"
    if interactive and IS_WINDOWS:
        return "❌ 交互模式暂不支持 Windows。"
    from webot.runtime_settings import get_runtime_settings
    sandbox_selected = get_runtime_settings(username, session_id or "default").approval.command_sandbox == "srt"
    if sandbox_access != "default" and not sandbox_selected:
        return "❌ 沙盒提权请求仅适用于启用 SRT 的会话。"
    if sandbox_access != "default" and not escalation_reason.strip():
        return "❌ 沙盒提权需要说明本次请求的原因。"
    workspace_state = resolve_session_workspace(username, session_id, explicit_cwd=cwd)
    try:
        escalation_target = normalize_escalation(sandbox_access, escalation_target, workspace_state.root)
    except SandboxUnavailable as exc:
        return f"❌ {exc}"

    approval_note = ""
    reject, approval_note = await _command_safety_gate(
            username, session_id, command,
            check_names=not is_python,
            action_args={
                "command": command, "language": language, "mode": mode,
                "cwd": cwd, "session_id": session_id, "timeout_seconds": timeout_seconds,
                "max_output_chars": max_output_chars, "notify_on_done": notify_on_done,
                "sandbox_access": sandbox_access, "escalation_target": escalation_target,
                "escalation_reason": escalation_reason,
            },
    )
    if reject:
        return reject

    workspace = str(workspace_state.cwd)

    try:
        use_srt = sandbox_selected and sandbox_access != "host"
        if mode == "foreground":
            if use_srt:
                script = _write_python_script(workspace, command) if is_python else ""
                sandbox = None
                try:
                    try:
                        sandbox = build_srt_command(
                            root=workspace_state.root,
                            cwd=workspace_state.cwd, command=command, language=language,
                            python_executable=_python_cmd(),
                            script_path=Path(script) if script else None,
                            access=sandbox_access, target=escalation_target,
                        )
                    except SandboxUnavailable as exc:
                        return f"❌ {exc}"
                    return await _run_foreground(
                        list(sandbox.argv), label="SRT 内 Python 代码" if is_python else "SRT 内命令",
                        workspace_state=workspace_state, username=username,
                        timeout_value=_bounded_int(timeout_seconds, EXEC_TIMEOUT, 1, MAX_EXEC_TIMEOUT),
                        capture_limit=_bounded_int(max_output_chars, MAX_OUTPUT_LENGTH, 256, MAX_CAPTURE_LENGTH),
                        approval_note=approval_note, sandbox=sandbox,
                    )
                finally:
                    if sandbox is not None:
                        sandbox.settings_path.unlink(missing_ok=True)
                    if script:
                        with contextlib.suppress(OSError):
                            os.remove(script)
            script = _write_python_script(workspace, command) if is_python else ""
            try:
                return await _run_foreground(
                    [_python_cmd(), script] if is_python else command,
                    label="Python 代码" if is_python else "命令",
                    workspace_state=workspace_state,
                    username=username,
                    timeout_value=_bounded_int(timeout_seconds, EXEC_TIMEOUT, 1, MAX_EXEC_TIMEOUT),
                    capture_limit=_bounded_int(max_output_chars, MAX_OUTPUT_LENGTH, 256, MAX_CAPTURE_LENGTH),
                    approval_note=approval_note,
                )
            finally:
                if script:
                    with contextlib.suppress(OSError):
                        os.remove(script)

        shell_command = command
        script = ""
        sandbox = None
        launched = False
        if is_python:
            script = _write_python_script(workspace, command)
            shell_command = _quote_command([_python_cmd(), *(["-i"] if interactive else []), script])
        try:
            if use_srt:
                try:
                    sandbox = build_srt_command(
                        root=workspace_state.root, cwd=workspace_state.cwd,
                        command=command, language=language, python_executable=_python_cmd(),
                        script_path=Path(script) if script else None, interactive=interactive,
                        access=sandbox_access, target=escalation_target,
                    )
                except SandboxUnavailable as exc:
                    return f"❌ {exc}"
            job_id = uuid.uuid4().hex[:12]
            jobs_dir = _jobs_dir(workspace)
            job = BackgroundJob(
            job_id=job_id,
            username=username,
            command=shell_command,
            workspace=workspace,
            mode=workspace_state.mode,
            remote=workspace_state.remote,
            stdout_path=str(jobs_dir / f"{job_id}.stdout.log"),
            stderr_path=str(jobs_dir / f"{job_id}.stderr.log"),
            timeout_seconds=_bounded_int(timeout_seconds, BACKGROUND_EXEC_TIMEOUT, 1, MAX_EXEC_TIMEOUT),
            session_id=session_id,
            notify_on_done=bool(notify_on_done),
            interactive=interactive,
            stdin_path=str(jobs_dir / f"{job_id}.stdin") if interactive else "",
            )
            Path(job.stdout_path).write_text("", encoding="utf-8")
            Path(job.stderr_path).write_text("", encoding="utf-8")
            _persist_job(job)
            env = _sandbox_env(workspace, username)
            if interactive:
                # The basic REPL echoes plain lines; the default one redraws the
                # input line on every keystroke, which reads as noise.
                env["PYTHON_BASIC_REPL"] = "1"
            if sandbox is not None:
                env["PATH"] = os.environ.get("PATH", env["PATH"])
                env["TMPDIR"] = str(sandbox.settings_path.parent)
            _launch_detached_background_job(job, env, sandbox=sandbox, cleanup_script=script)
            launched = True
        finally:
            if not launched:
                if sandbox is not None:
                    sandbox.settings_path.unlink(missing_ok=True)
                if script:
                    Path(script).unlink(missing_ok=True)
        _BACKGROUND_JOBS[job_id] = job
        if job.notify_on_done and job.session_id:
            # 登记待通知指针；由长驻的 mainagent.background_notify_loop 在任务达终态时
            # 调 /system_trigger 唤醒发起会话（commander 进程用完即销毁，不能自己 watch）。
            register_pending_notify(job_id, str(_job_meta_path(job.workspace, job_id)))

        if interactive:
            await _wait_for_output(job.stdout_path, 0, 3.0, job)
            screen = _clean_terminal_text(Path(job.stdout_path).read_text(encoding="utf-8", errors="replace"))
            result = (
                "✅ 交互任务已启动，用 background_command_io(job_id, input=...) 输入\n"
                + _job_summary(_refresh_background_job(job))
                + f"\n\n🖥️ 当前输出:\n{screen[-MAX_OUTPUT_LENGTH:] or '(暂无输出)'}"
            )
        else:
            result = "✅ 后台任务已启动\n" + _job_summary(job)
        if approval_note:
            result = approval_note + "\n\n" + result
        return result
    except Exception as e:
        return f"❌ 执行异常: {str(e)}"


@mcp.tool()
async def background_command_io(
    job_id: str,
    username: str = "",
    session_id: str = "",
    input: str = "",
    enter: bool = True,
    wait_seconds: int = 2,
    stream: str = "stdout",
    cwd: str = "",
    offset: int = 0,
    limit: int = 0,
) -> str:
    """
    查看后台或交互任务的状态和一段输出。对交互任务传 input 就像在终端里打字，
    返回这次输入之后的新输出。

    :param job_id: run_command 返回的 job_id
    :param input: 发给交互任务的输入；只按回车传 "\\r"；控制键用转义字符，如 "\\u0003" 是 Ctrl-C
    :param enter: 输入后是否按回车
    :param wait_seconds: 输入后最多等多少秒收集输出（0-30）
    :param stream: 读取哪路输出：stdout 或 stderr（交互任务只有 stdout）
    :param cwd: 通常留空；只有启动任务时指定了 cwd，才传同一个值以定位任务
    :param offset: 读取起点；首次传 0，之后用上一次结果给出的 offset 继续。传了 input 时忽略，从输入之前的位置读
    :param limit: 本次最多读取的字符数；0 表示默认值（12000），上限 50000
    """
    job = _resolve_background_job(job_id, username=username, session_id=session_id, cwd=cwd)
    if not job:
        return f"❌ 未找到后台任务 '{job_id}'。"

    stream_name = (stream or "stdout").strip().lower()
    if stream_name not in {"stdout", "stderr"}:
        return "❌ stream 只支持 stdout 或 stderr。"
    path = job.stdout_path if stream_name == "stdout" else job.stderr_path
    safe_offset = max(0, int(offset or 0))

    if input:
        if not job.interactive:
            return "❌ 这不是交互任务，不能输入；需要交互请用 run_command(mode=\"interactive\") 启动。"
        if job.status != "running":
            return "ℹ️ 交互任务已结束，无法再输入\n" + _job_summary(job)
        # What is typed into a shell is a command like any other.
        reject, _approval = await _command_safety_gate(
            job.username or username,
            job.session_id or session_id,
            input,
            check_names=_program_name(job.command) in _SHELL_PROGRAMS,
            tool_name="background_command_io",
            action_args={
                "job_id": job_id, "input": input, "enter": enter, "cwd": cwd,
                "wait_seconds": wait_seconds, "stream": stream, "offset": offset, "limit": limit,
            },
        )
        if reject:
            return reject
        text = input if not enter or input.endswith(("\r", "\n")) else input + "\r"
        try:
            safe_offset = os.path.getsize(job.stdout_path)
        except OSError:
            safe_offset = 0
        try:
            fd = os.open(job.stdin_path, os.O_WRONLY | os.O_NONBLOCK)
            try:
                os.write(fd, text.encode("utf-8"))
            finally:
                os.close(fd)
        except OSError as exc:
            return f"❌ 输入失败（交互程序可能已退出）: {exc}\n" + _job_summary(_refresh_background_job(job))
        await _wait_for_output(job.stdout_path, safe_offset, max(0, min(int(wait_seconds or 0), 30)), job)
        job = _refresh_background_job(job)
        path = job.stdout_path

    summary = _job_summary(job)
    safe_limit = _bounded_int(limit, DEFAULT_BACKGROUND_READ_CHARS, 256, MAX_BACKGROUND_READ_CHARS)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            handle.seek(safe_offset)
            content = handle.read(safe_limit)
            next_offset = handle.tell()
    except FileNotFoundError:
        return f"{summary}\n\n📄 {stream_name} 尚无输出。"
    except OSError as exc:
        return f"{summary}\n\n❌ 读取后台输出失败: {exc}"
    if job.interactive:
        content = _clean_terminal_text(content)
    if not content.strip():
        return f"{summary}\n\n📄 {stream_name} 暂无新输出（offset: {safe_offset}）。"
    return (
        f"{summary}\n\n"
        f"📄 {stream_name} 输出片段（offset: {safe_offset}）：\n"
        f"{content}\n➡️ 下一段可用 `offset={next_offset}` 继续读取。"
    )


@mcp.tool()
async def cancel_background_command(job_id: str, username: str = "", session_id: str = "", cwd: str = "") -> str:
    """
    取消一个后台命令任务。

    :param job_id: start_background_command 返回的 job_id
    :param cwd: 通常留空；只有启动任务时指定了 cwd，才传同一个值以定位任务
    """
    job = _resolve_background_job(job_id, username=username, session_id=session_id, cwd=cwd)
    if not job:
        return f"❌ 未找到后台任务 '{job_id}'。"
    if job.status != "running":
        return "ℹ️ 后台任务已结束\n" + _job_summary(job)
    _terminate_background_job(job)
    if job.stdin_path:
        with contextlib.suppress(OSError):
            os.unlink(job.stdin_path)
    job.status = "cancelled"
    job.error = "后台任务已取消。"
    job.finished_at = time.time()
    _persist_job(job)
    _BACKGROUND_JOBS[job.job_id] = job
    return "🛑 后台任务已取消\n" + _job_summary(job)

if __name__ == "__main__":
    mcp.run()
