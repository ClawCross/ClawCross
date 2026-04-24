#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeBot 跨平台启动器

功能：
- 支持 Linux/macOS/Windows 多平台
- 精确管理子进程 PID
- 安全关闭：Ctrl+C、关闭窗口、kill 信号 均能正常清理

用法：python scripts/launcher.py
"""

import sys

# Python version guard: fail fast with a clear message if run under Python 2
if sys.version_info < (3, 9):
    sys.stderr.write(
        "\n"
        "ERROR: Clawcross requires Python 3.11+, but this script is running under Python {}.{}.\n"
        "\n"
        "Common cause: on macOS, system 'python' may point to Python 2.7.\n"
        "Solutions:\n"
        "  1. Use the canonical startup: bash selfskill/scripts/run.sh start\n"
        "  2. Or activate the venv first: source .venv/bin/activate && python scripts/launcher.py\n"
        "  3. Or use the venv python directly: .venv/bin/python scripts/launcher.py\n"
        "\n".format(sys.version_info[0], sys.version_info[1])
    )
    sys.exit(1)

import subprocess
import os
import signal
import atexit
import time
import stat
import platform
import shutil
import locale
import urllib.request
import webbrowser
from dotenv import load_dotenv

# 确保 Python 输出使用 UTF-8 编码
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
for stream_name in ("stdout", "stderr"):
    stream = getattr(sys, stream_name, None)
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# 切换到项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)
ENV_FILE_PATH = os.path.join(PROJECT_ROOT, "config", ".env")
PLACEHOLDER = "wait to set"

# 检查 .env 配置文件是否存在（推荐用 run.sh/run.ps1 start，会自动 configure --init）
if not os.path.exists("config/.env"):
    print("❌ 未找到 config/.env。")
    print("   请执行: bash selfskill/scripts/run.sh start（或 Windows: selfskill\\scripts\\run.ps1 start）")
    print("   会先按模板生成 .env；不必事先填写 LLM Key，可用 Magic link 登录后在网页向导配置或从 OpenClaw 导入。")
    sys.exit(1)

# 加载 .env 配置
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, "config", ".env"))

_allow_empty_llm_model = (os.getenv("CLAWCROSS_ALLOW_EMPTY_LLM_MODEL") or "").strip().lower()
if _allow_empty_llm_model not in ("1", "true", "yes", "on"):
    llm_model = (os.getenv("LLM_MODEL") or "").strip()
    if not llm_model or llm_model == PLACEHOLDER:
        print("❌ LLM_MODEL 未配置，已停止启动。")
        print("   请先设置模型，例如：")
        if sys.platform == "win32":
            print("   powershell -ExecutionPolicy Bypass -File .\\selfskill\\scripts\\run.ps1 configure LLM_MODEL deepseek-chat")
            print("   可先查看可用模型：powershell -ExecutionPolicy Bypass -File .\\selfskill\\scripts\\run.ps1 auto-model")
        else:
            print("   bash selfskill/scripts/run.sh configure LLM_MODEL deepseek-chat")
            print("   可先查看可用模型：bash selfskill/scripts/run.sh auto-model")
        print("   如需临时允许空模型启动，可设置环境变量 CLAWCROSS_ALLOW_EMPTY_LLM_MODEL=1。")
        sys.exit(1)

# 读取各服务端口配置
PORT_SCHEDULER = os.getenv("PORT_SCHEDULER", "51201")
PORT_AGENT = os.getenv("PORT_AGENT", "51200")
PORT_FRONTEND = os.getenv("PORT_FRONTEND", "51209")
PORT_OASIS = os.getenv("PORT_OASIS", "51202")

# 使用当前 Python 解释器（虚拟环境已由 run.sh/run.ps1 激活）
venv_python = sys.executable

# 子进程列表（用于管理所有启动的服务）
child_procs = []
cleanup_done = False


def _init_env_placeholder(key: str):
    """如果 config/.env 中缺少某个配置项，写入占位符值

    参数：
        key: 环境变量名称
    """
    current_value = os.getenv(key, "").strip()
    if current_value and current_value != PLACEHOLDER:
        # 已有有效值（例如 tunnel.py 已设置），跳过
        return

    # 写入占位符到 .env，使用户知道该字段存在
    if os.path.exists(ENV_FILE_PATH):
        with open(ENV_FILE_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    else:
        lines = []

    key_found = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}="):
            new_lines.append(f"{key}={PLACEHOLDER}\n")
            key_found = True
        else:
            new_lines.append(line)

    if not key_found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{key}={PLACEHOLDER}\n")

    with open(ENV_FILE_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def cleanup():
    """清理所有子进程（优雅关闭 + 强制终止）

    关闭策略：
    1. 先发送 SIGTERM（优雅关闭）
    2. 等待最多 5 秒进程退出
    3. 超时未退出的进程发送 SIGKILL（强制终止）
    4. 等待所有进程最终结束
    """
    global cleanup_done
    if cleanup_done:
        return
    cleanup_done = True

    print("\n🛑 正在关闭所有服务...")

    # 第一步：发送 SIGTERM（优雅关闭）
    for proc in child_procs:
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    # 第二步：等待进程退出（最多 5 秒）
    for _ in range(50):
        if all(p.poll() is not None for p in child_procs):
            break
        time.sleep(0.1)

    # 第三步：超时未退出的进程强制杀掉
    for proc in child_procs:
        if proc.poll() is None:
            try:
                print(f"⚠️  进程 {proc.pid} 未响应，强制终止...")
                proc.kill()
            except Exception:
                pass

    # 第四步：等待所有进程结束
    for proc in child_procs:
        try:
            proc.wait(timeout=2)
        except Exception:
            pass

    print("✅ 所有服务已关闭")


def resolve_openclaw_cli():
    """Return the preferred OpenClaw CLI binary path when available.

    优先使用腾讯内网版 wrapper（~/.local/lib/openclaw-internal/bin/openclaw），
    因为它会自动 source 运行时环境。
    """
    # 优先检测内网版 wrapper
    internal_wrapper = os.path.expanduser(
        "~/.local/lib/openclaw-internal/bin/openclaw"
    )
    if os.path.isfile(internal_wrapper) and os.access(internal_wrapper, os.X_OK):
        return internal_wrapper

    candidates = ["openclaw.cmd", "openclaw"] if sys.platform == "win32" else ["openclaw"]
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return path
    return None


def ensure_openclaw_gateway_running():
    """Best-effort startup for OpenClaw Gateway when the CLI is installed."""
    _no_oc = (os.getenv("CLAWCROSS_NO_OPENCLAW") or "").strip().lower()
    if _no_oc in ("1", "true", "yes", "on"):
        print("⏭️  已跳过 OpenClaw 联动（CLAWCROSS_NO_OPENCLAW）— 不预热 Gateway、不刷新 OPENCLAW_*")
        return
    openclaw_cli = resolve_openclaw_cli()
    if not openclaw_cli:
        return

    try:
        script_dir = os.path.join(PROJECT_ROOT, "selfskill", "scripts")
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)

        from configure_openclaw import sync_openclaw_runtime_for_clawcross_startup
    except Exception as exc:
        print(f"🦞 OpenClaw 已安装，但运行时预检查不可用: {exc}")
        return

    print("🦞 检测 OpenClaw Gateway...")

    try:
        result = sync_openclaw_runtime_for_clawcross_startup()
        load_dotenv(dotenv_path=ENV_FILE_PATH, override=True)

        runtime_after = result.get("runtime_after")
        api_url = result.get("api_url")
        auth_mode = result.get("auth_mode") or "unknown"
        sessions_file = result.get("sessions_file")
        env_updates = result.get("env_updates") or []

        if runtime_after == "running":
            detail_parts = []
            if result.get("gateway_started"):
                detail_parts.append("gateway started")
            if result.get("chat_completions_enabled"):
                if result.get("chat_completions_changed"):
                    detail_parts.append("chatCompletions enabled")
                else:
                    detail_parts.append("chatCompletions ready")
            if api_url:
                detail_parts.append(api_url)
            detail = ", ".join(detail_parts) if detail_parts else "gateway running"
            print(f"   ✅ OpenClaw 已就绪 ({detail})")
            print(f"   ℹ️ Auth: {auth_mode}")
            if sessions_file:
                print(f"   ℹ️ Sessions: {sessions_file}")
            if env_updates:
                print(f"   ℹ️ 已刷新 .env: {', '.join(env_updates)}")
            return

        detail = result.get("gateway_start_error") or runtime_after or "gateway unavailable"
        print(f"   ⚠️ OpenClaw 已安装，但未能准备好 runtime: {detail}")
    except Exception as exc:
        print(f"   ⚠️ OpenClaw 已安装，但启动预热失败: {exc}")


def _command_output(args):
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=2,
            check=False,
        )
        return result.returncode, _decode_command_output(result.stdout)
    except Exception:
        return -1, ""


def _decode_command_output(data):
    if isinstance(data, str):
        return data

    encodings = []
    preferred = locale.getpreferredencoding(False)
    if preferred:
        encodings.append(preferred)
    encodings.extend(["utf-8", "gbk", "cp936", "mbcs"])

    tried = set()
    for encoding in encodings:
        normalized = (encoding or "").strip().lower()
        if not normalized or normalized in tried:
            continue
        tried.add(normalized)
        try:
            return data.decode(encoding)
        except Exception:
            continue

    return data.decode("utf-8", errors="replace")


def is_port_listening(port):
    port = str(port)

    if sys.platform == "win32":
        code, output = _command_output(["netstat", "-ano", "-p", "tcp"])
        if code == 0:
            target = f":{port}"
            for line in output.splitlines():
                upper = line.upper()
                if target in line and "LISTENING" in upper:
                    return True
        return False

    code, output = _command_output(["ss", "-ltn"])
    if code == 0:
        target = f":{port}"
        for line in output.splitlines():
            if target in line:
                return True

    code, output = _command_output(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"])
    if code == 0 and output.strip():
        return True

    code, output = _command_output(["netstat", "-ltn"])
    if code == 0:
        target = f":{port}"
        for line in output.splitlines():
            if target in line:
                return True

    return False


def wait_for_service_ready(
    proc,
    port,
    label,
    timeout=20.0,
    poll_interval=0.2,
):
    deadline = time.monotonic() + timeout
    last_error = None

    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"{label} 在端口 {port} 就绪前已退出（exit code {proc.returncode}）"
            )

        try:
            if is_port_listening(port):
                return
        except Exception as exc:
            last_error = exc
        else:
            last_error = None

        time.sleep(poll_interval)

    if proc.poll() is not None:
        raise RuntimeError(
            f"{label} 在端口 {port} 就绪前已退出（exit code {proc.returncode}）"
        )

    detail = f": {last_error}" if last_error else ""
    raise TimeoutError(f"{label} 在 {timeout:.1f}s 内未监听端口 {port}{detail}")


def start_service(service):
    print(service["message"])
    proc = subprocess.Popen(
        [venv_python, service["script"]],
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=None,
        stderr=None,
    )
    child_procs.append(proc)
    service["proc"] = proc
    return proc


def wait_for_started_services(services):
    for service in services:
        wait_for_service_ready(
            proc=service["proc"],
            port=service["port"],
            label=service["label"],
            timeout=service.get("timeout", 20.0),
        )
        print(f"   ✅ {service['label']} 已就绪 (port {service['port']})")


def launch_services(services):
    for service in services:
        start_service(service)

    wait_for_started_services(services)


# 注册退出清理函数
atexit.register(cleanup)


# 信号处理函数
def signal_handler(signum, frame):
    """处理 SIGINT/SIGBREAK 信号，触发 atexit 清理"""
    sys.exit(0)  # 触发 atexit


signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
signal.signal(signal.SIGTERM, signal_handler)  # kill

# Windows 特殊处理：捕获关闭窗口事件
if sys.platform == "win32":
    try:
        import win32api
        win32api.SetConsoleCtrlHandler(lambda x: cleanup() or True, True)
    except ImportError:
        try:
            signal.signal(signal.SIGBREAK, signal_handler)
        except Exception:
            pass

print("🚀 启动 WeBot...")
print()

# 确保 INTERNAL_TOKEN 在所有服务启动前已存在
# （mainagent 首次启动会自动生成，但 OASIS 比 mainagent 先启动，会读到空值）
if not os.getenv("INTERNAL_TOKEN"):
    import secrets, re
    _token = secrets.token_hex(32)
    with open(ENV_FILE_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    if re.search(r"^INTERNAL_TOKEN=", content, re.MULTILINE):
        content = re.sub(r"^INTERNAL_TOKEN=.*$", f"INTERNAL_TOKEN={_token}", content, flags=re.MULTILINE)
    else:
        content += f"\nINTERNAL_TOKEN={_token}\n"
    with open(ENV_FILE_PATH, "w") as f:
        f.write(content)
    os.environ["INTERNAL_TOKEN"] = _token
    print(f"🔑 已自动生成 INTERNAL_TOKEN 并写入 .env")

ensure_openclaw_gateway_running()

# 服务配置列表
services = [
    {
        "message": f"⏰ [1/5] 启动定时调度中心 (port {PORT_SCHEDULER})...",
        "label": "定时调度中心",
        "script": "src/utils/scheduler_service.py",
        "port": PORT_SCHEDULER,
        "timeout": 15.0,
    },
    {
        "message": f"🏛️ [2/5] 启动 OASIS 论坛服务 (port {PORT_OASIS})...",
        "label": "OASIS 论坛服务",
        "script": "oasis/server.py",
        "port": PORT_OASIS,
        "timeout": 20.0,
    },
    {
        "message": f"🤖 [3/5] 启动 AI Agent (port {PORT_AGENT})...",
        "label": "AI Agent",
        "script": "src/mainagent.py",
        "port": PORT_AGENT,
        "timeout": 25.0,
    },
]

# Chatbot 启动（可选组件）
chatbot_setup = os.path.join(PROJECT_ROOT, "chatbot", "setup.py")
is_headless = os.getenv("WEBOT_HEADLESS", "0") == "1"
if os.path.exists(chatbot_setup):
    if is_headless or not sys.stdin.isatty():
        print(f"💬 [4/5] 跳过聊天机器人交互式配置（headless / 非交互模式）")
        print(f"   提示: 如需配置 chatbot，请在人工模式下运行 run.sh / run.ps1 或手动编辑 config/.env")
    else:
        print(f"💬 [4/5] 启动聊天机器人...")
        chatbot_dir = os.path.join(PROJECT_ROOT, "chatbot")
        subprocess.run([venv_python, "setup.py"], cwd=chatbot_dir)
    services.append(
        {
            "message": f"🌐 [5/5] 启动前端 Web UI (port {PORT_FRONTEND})...",
            "label": "前端 Web UI",
            "script": "src/front.py",
            "port": PORT_FRONTEND,
            "timeout": 20.0,
        }
    )
else:
    services.append(
        {
            "message": f"🌐 [4/4] 启动前端 Web UI (port {PORT_FRONTEND})...",
            "label": "前端 Web UI",
            "script": "src/front.py",
            "port": PORT_FRONTEND,
            "timeout": 20.0,
        }
    )

# 启动所有服务
launch_services(services)

print()
print("============================================")
print("  ✅ WeBot 已全部启动！")
print(f"  🌐 访问: http://127.0.0.1:{PORT_FRONTEND}")
print("  按 Ctrl+C 停止所有服务")
print("============================================")
print()

# 自动打开浏览器（后台线程执行）
# 在无 GUI 环境下，webbrowser 可能尝试启动文本浏览器 (lynx/w3m) 并占用 stdin 导致卡死
# 预防方式：无 DISPLAY 时将 BROWSER 设为 "true"（/usr/bin/true），静默跳过
import threading

def _open_browser():
    """在后台线程中打开浏览器"""
    url = f"http://127.0.0.1:{PORT_FRONTEND}"
    if not os.environ.get("DISPLAY") and sys.platform != "darwin" and sys.platform != "win32":
        # 无图形环境，设 BROWSER=true 让 webbrowser 调用 /usr/bin/true 而非文本浏览器
        os.environ.setdefault("BROWSER", "true")
    try:
        webbrowser.open(url)
        print(f"🌐 已自动打开浏览器: {url}")
    except Exception:
        print(f"⚠️  无法自动打开浏览器，请手动访问: {url}")

threading.Thread(target=_open_browser, daemon=True).start()

# 重启信号文件路径
RESTART_FLAG = os.path.join(PROJECT_ROOT, ".restart_flag")
# 启动时清理残留的重启信号
if os.path.isfile(RESTART_FLAG):
    os.remove(RESTART_FLAG)

# 主循环：监测子进程退出和重启信号
try:
    while True:
        # 检测重启信号文件（由 CLI restart 命令写入）
        if os.path.isfile(RESTART_FLAG):
            print("\n🔄 检测到重启信号，正在重启所有服务...")
            os.remove(RESTART_FLAG)

            # 停止所有子进程
            for proc in child_procs:
                if proc.poll() is None:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
            for _ in range(50):
                if all(p.poll() is not None for p in child_procs):
                    break
                time.sleep(0.1)
            for proc in child_procs:
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            for proc in child_procs:
                try:
                    proc.wait(timeout=2)
                except Exception:
                    pass

            # 等待端口释放（避免 Address already in use）
            time.sleep(2)

            # 重新加载 .env 配置
            load_dotenv(dotenv_path=ENV_FILE_PATH, override=True)
            ensure_openclaw_gateway_running()

            # 重新启动所有服务
            child_procs.clear()
            cleanup_done = False
            print()
            launch_services(services)
            print()
            print("✅ 所有服务已重启！")
            print()
            continue

        # 检测子进程异常退出
        for proc in child_procs:
            if proc.poll() is not None:
                print(f"⚠️ 服务 (PID {proc.pid}) 异常退出，正在关闭其余服务...")
                sys.exit(1)
        time.sleep(0.5)
except KeyboardInterrupt:
    pass

sys.exit(0)
