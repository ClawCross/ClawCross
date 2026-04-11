#!/usr/bin/env python3
"""
OpenClaw 自动探测与配置工具。

检测本地 OpenClaw 安装状态，自动探测 gateway 端口、sessions 文件路径、
API token 等配置，并写入 Clawcross 的 config/.env。

用法:
    python selfskill/scripts/configure_openclaw.py --auto-detect       # 自动探测并配置（含 workspace 初始化）
    python selfskill/scripts/configure_openclaw.py --sync-clawcross-llm # 将 Clawcross 当前 LLM 配置回写到 OpenClaw
    python selfskill/scripts/configure_openclaw.py --status            # 仅显示检测状态
    python selfskill/scripts/configure_openclaw.py --install-guide     # 输出 OpenClaw 安装/初始化流程
    python selfskill/scripts/configure_openclaw.py --repair-health     # 检查并修复轻量健康问题
    python selfskill/scripts/configure_openclaw.py --init-workspace    # 仅初始化 workspace 默认模板
"""

import copy
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

# 复用 configure.py 的配置写入逻辑
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
ENV_PATH = os.path.join(PROJECT_ROOT, "config", ".env")
OPENCLAW_HOME = os.path.expanduser(os.getenv("OPENCLAW_HOME", "~/.openclaw"))
OPENCLAW_CONFIG_PATH = os.path.join(OPENCLAW_HOME, "openclaw.json")
OPENCLAW_AGENT_MODELS_PATH = os.path.join(
    OPENCLAW_HOME, "agents", "main", "agent", "models.json"
)
DEFAULT_GATEWAY_PORT = 18789
DEFAULT_WORKSPACE_PATH = os.path.join(OPENCLAW_HOME, "workspace")
DEFAULT_SESSIONS_FILE = os.path.join(
    OPENCLAW_HOME, "agents", "main", "sessions", "sessions.json"
)


INTERNAL_OPENCLAW_WRAPPER = os.path.expanduser(
    "~/.local/lib/openclaw-internal/bin/openclaw"
)
INTERNAL_OPENCLAW_RUNTIME_BASE = os.path.expanduser(
    "~/.local/lib/openclaw-internal/runtime"
)
INTERNAL_OPENCLAW_GATEWAY_PORT = 23001


def is_internal_openclaw():
    """检测是否安装了腾讯内网版 OpenClaw。"""
    return os.path.isfile(INTERNAL_OPENCLAW_WRAPPER) and os.access(
        INTERNAL_OPENCLAW_WRAPPER, os.X_OK
    )


def resolve_openclaw_command():
    """Resolve the most reliable OpenClaw CLI entrypoint for this OS.

    优先使用腾讯内网版 wrapper（~/.local/lib/openclaw-internal/bin/openclaw），
    因为它会自动 source 运行时环境（init_env.sh / init_gf_env.sh）。
    """
    # 优先检测内网版 wrapper
    if is_internal_openclaw():
        return INTERNAL_OPENCLAW_WRAPPER

    candidates = ["openclaw"]
    if os.name == "nt":
        candidates = ["openclaw.cmd", "openclaw"]

    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    return None

# 添加到 sys.path 以便复用 configure.py
sys.path.insert(0, SCRIPT_DIR)
try:
    from configure import set_env_with_validation, read_env
except ImportError:
    # Fallback: 直接实现最小版本
    def read_env():
        if not os.path.exists(ENV_PATH):
            return [], {}
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        kvs = {}
        for line in lines:
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                kvs[k.strip()] = v.strip()
        return lines, kvs

    def set_env_with_validation(key, value):
        lines, _ = read_env()
        key_found = False
        new_lines = []
        for line in lines:
            s = line.strip()
            if s.startswith("#"):
                body = s[1:].lstrip()
                if body.startswith(f"{key}="):
                    if not key_found:
                        new_lines.append(f"{key}={value}\n")
                        key_found = True
                    continue
                new_lines.append(line)
                continue
            if s.startswith(f"{key}="):
                if not key_found:
                    new_lines.append(f"{key}={value}\n")
                    key_found = True
                continue
            new_lines.append(line)
        if not key_found:
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines.append("\n")
            new_lines.append(f"{key}={value}\n")
        os.makedirs(os.path.dirname(ENV_PATH), exist_ok=True)
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        print(f"✅ {key}={value[:20]}{'...' if len(value) > 20 else ''}")
        return True


def run_cmd(cmd, timeout=15):
    """运行命令并返回 (returncode, stdout, stderr)"""
    if cmd and cmd[0] == "openclaw":
        resolved = resolve_openclaw_command()
        if resolved:
            cmd = [resolved, *cmd[1:]]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return -1, "", "command not found"
    except subprocess.TimeoutExpired:
        return -2, "", "timeout"
    except Exception as e:
        return -3, "", str(e)


def mask_secret(value):
    """掩码显示敏感信息。"""
    if not value:
        return "****"
    if len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-4:]


def _read_clawcross_env_kvs():
    """Read Clawcross config/.env using this module's ENV_PATH (tests patch ENV_PATH).

    When configure.read_env is imported, it reads configure.ENV_PATH instead; path
    resolution for OpenClaw must follow configure_openclaw.ENV_PATH.
    """
    if not os.path.isfile(ENV_PATH):
        return {}
    kvs = {}
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#") and "=" in s:
                    k, v = s.split("=", 1)
                    kvs[k.strip()] = v.strip()
    except Exception:
        return {}
    return kvs


def _infer_openclaw_home_from_sessions_file(sessions_file):
    """Infer OpenClaw home from OPENCLAW_SESSIONS_FILE when path shape matches."""
    raw = (sessions_file or "").strip()
    if not raw:
        return None
    p = os.path.abspath(os.path.expanduser(raw))
    # expected: <home>/agents/main/sessions/sessions.json
    if os.path.basename(p) != "sessions.json":
        return None
    sessions_dir = os.path.dirname(p)
    if os.path.basename(sessions_dir) != "sessions":
        return None
    main_dir = os.path.dirname(sessions_dir)
    if os.path.basename(main_dir) != "main":
        return None
    agents_dir = os.path.dirname(main_dir)
    if os.path.basename(agents_dir) != "agents":
        return None
    return os.path.dirname(agents_dir) or None


def _resolve_openclaw_paths():
    """Resolve openclaw.json/models.json paths, preferring OPENCLAW_SESSIONS_FILE inference."""
    config_path = OPENCLAW_CONFIG_PATH
    models_path = OPENCLAW_AGENT_MODELS_PATH
    try:
        kvs = _read_clawcross_env_kvs()
        inferred_home = _infer_openclaw_home_from_sessions_file(
            (kvs.get("OPENCLAW_SESSIONS_FILE") or "").strip()
        )
        if inferred_home:
            config_path = os.path.join(inferred_home, "openclaw.json")
            models_path = os.path.join(
                inferred_home, "agents", "main", "agent", "models.json"
            )
    except Exception:
        pass
    return config_path, models_path


def load_openclaw_config():
    """读取 ~/.openclaw/openclaw.json。"""
    config_path, _ = _resolve_openclaw_paths()
    if not os.path.isfile(config_path):
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_openclaw_agent_models():
    """读取 ~/.openclaw/agents/main/agent/models.json。"""
    _, models_path = _resolve_openclaw_paths()
    if not os.path.isfile(models_path):
        return {}
    try:
        with open(models_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_config_value(*path):
    """从 openclaw.json 中读取嵌套配置。"""
    data = load_openclaw_config()
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def parse_non_banner_value(raw):
    """过滤 CLI banner / 噪声，只取有效值。"""
    if not raw:
        return None

    for line in raw.splitlines():
        value = line.strip()
        if not value:
            continue
        if value.startswith("openclaw") or value.startswith("🦞"):
            continue
        if value.startswith("=") or value.startswith("Docs:"):
            continue
        return value
    return None


def parse_json_output(raw):
    """解析命令输出中的 JSON。"""
    if not raw:
        return None

    text = raw.strip()
    if not text:
        return None

    for candidate in (text, text[text.find("{") :] if "{" in text else ""):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def print_install_guide():
    """输出推荐的 OpenClaw 安装与初始化流程。"""
    is_windows = os.name == "nt"
    sync_cmd = (
        r"powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 check-openclaw"
        if is_windows
        else "bash selfskill/scripts/run.sh check-openclaw"
    )

    # 腾讯内网版安装提示：仅当检测到内网版运行时目录时才显示
    _internal_runtime_exists = os.path.isdir(INTERNAL_OPENCLAW_RUNTIME_BASE)
    if not is_windows and _internal_runtime_exists:
        print("=" * 50)
        print("腾讯内网版 OpenClaw 安装（推荐内网用户使用）:")
        print("  1. 环境准备（自动安装 Homebrew/Node.js/Python/Go 等依赖）:")
        print("     curl -fsSL 'https://mirrors.tencent.com/repository/generic/tencent_openclaw/openclaw/install_prepare.sh' | bash -s -- --token <TOKEN> --bot-id <BOT_ID> --bot-secret <BOT_SECRET>")
        print("  2. 安装内网版 OpenClaw:")
        print("     curl -fsSL 'https://mirrors.tencent.com/repository/generic/tencent_openclaw/openclaw/install_openclaw.sh' | bash -s -- --token <TOKEN> --bot-id <BOT_ID> --bot-secret <BOT_SECRET>")
        print("  3. 安装完成后执行:")
        print(f"     {sync_cmd}")
        print("  注: 内网版默认 gateway 端口为 23001，Clawcross 会自动探测适配。")
        print("")
        print("=" * 50)
        print("")

    print("OpenClaw 安装流程（本地推荐）:")
    print("  1. 确保 Node.js >= 22")
    print("  2. 安装 CLI: npm install -g openclaw@latest --ignore-scripts")
    if is_windows:
        print("  3. Windows / 自动化场景优先使用非交互 onboarding:")
        print("     openclaw onboard --non-interactive --accept-risk --install-daemon")
        print("     如需复用 Clawcross 的 OpenAI key，可追加:")
        print("     openclaw onboard --non-interactive --accept-risk --install-daemon --openai-api-key <LLM_API_KEY>")
    else:
        print("  3. 本地交互模式: openclaw onboard --install-daemon")
        print("     自动化 / 无交互模式: openclaw onboard --non-interactive --accept-risk --install-daemon")
    print("  4. 确认 openclaw 已在 PATH 中；必要时补全 npm 全局 bin 路径")
    print("  5. 启用 HTTP Chat Completions: openclaw config set gateway.http.endpoints.chatCompletions.enabled true")
    print("  6. 重启 Gateway: openclaw gateway restart")
    print(f"  7. 完成后执行: {sync_cmd}")
    print("     这一步会自动同步 Clawcross .env，并清理缺失 transcript 的 session 坏索引")
    print("  8. 如果 Clawcross 已在运行，再执行一次 stop -> start，让 OASIS 重新加载 openclaw CLI")
    if is_windows:
        print("")
        print("Windows 本地控制台若提示 'gateway token missing'，有两种做法：")
        print("  A. 保持 token 模式：把 OPENCLAW_GATEWAY_TOKEN 粘贴到 Control UI settings")
        print("  B. 仅本机 loopback 调试：")
        print("     openclaw config set gateway.auth.mode none")
        print("     openclaw config unset gateway.auth.token")
        print("     openclaw gateway restart")
        print("")
        print("Windows + 微信插件注意事项：")
        print("  - PowerShell 里 bare 'openclaw' 可能命中 openclaw.ps1，并被执行策略拦截；优先用 openclaw.cmd")
        print("  - 官方微信安装器")
        print("    npx -y @tencent-weixin/openclaw-weixin-cli@latest install")
        print("    在 Windows 上可能失败，因为它内部调用了 'which openclaw'")
        print("  - 失败时请改用手动安装：")
        print('    openclaw.cmd plugins install "@tencent-weixin/openclaw-weixin"')
        print("    openclaw.cmd config set plugins.entries.openclaw-weixin.enabled true")
        print("    openclaw.cmd channels login --channel openclaw-weixin")
        print("    openclaw.cmd channels list --json")
        print("    openclaw.cmd gateway restart")
        print("  - 如果 openclaw status 显示 openclaw-weixin = ON / SETUP / no token，说明插件已装好但还没扫码完成")
    print("")
    print("自动化安装时，可使用最小本地模式：")
    print("  TOKEN=$(python - <<'PY'")
    print("import secrets")
    print("print(secrets.token_hex(24))")
    print("PY")
    print("  )")
    print(
        "  openclaw onboard --non-interactive --accept-risk --mode local "
        "--auth-choice skip --gateway-auth token --gateway-bind loopback "
        f"--gateway-port {DEFAULT_GATEWAY_PORT} --gateway-token \"$TOKEN\" "
        "--skip-channels --skip-search --skip-skills --skip-ui --skip-health "
        f"--skip-daemon --workspace {DEFAULT_WORKSPACE_PATH} --json"
    )
    print("")
    print("若需要 HTTP 回退链路常驻运行，再执行:")
    print("  openclaw gateway install --force")
    print("  openclaw gateway start")


def detect_gateway_runtime():
    """检测 gateway 运行状态。"""
    rc, out, err = run_cmd(["openclaw", "gateway", "status"], timeout=20)
    text = "\n".join(part for part in (out, err) if part)
    if "Runtime: running" in text:
        return "running"
    if "Runtime: stopped" in text or "Gateway not running" in text:
        return "stopped"
    port = detect_gateway_port() or DEFAULT_GATEWAY_PORT
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=2):
            return "running"
    except OSError:
        if rc == 0 and not text:
            return None
        return "stopped"
    if rc == 0 and not text:
        return None
    return None


def detect_linger_status():
    """检测 systemd linger 状态。"""
    username = os.getenv("USER") or os.getenv("LOGNAME")
    if not username:
        return None

    rc, out, _ = run_cmd(
        ["loginctl", "show-user", username, "--property=Linger", "--value"],
        timeout=10,
    )
    if rc != 0:
        return None

    value = parse_non_banner_value(out) or out.strip()
    if value in {"yes", "no"}:
        return value
    return None


def is_chat_completions_enabled():
    """检测 OpenAI Chat Completions 兼容端点是否开启。"""
    enabled = get_config_value(
        "gateway", "http", "endpoints", "chatCompletions", "enabled"
    )
    if isinstance(enabled, bool):
        return enabled
    if isinstance(enabled, str):
        return enabled.lower() == "true"
    return False


def enable_chat_completions_endpoint():
    """开启 Gateway 的 /v1/chat/completions 兼容端点。"""
    if is_chat_completions_enabled():
        return True, False

    rc, out, err = run_cmd(
        [
            "openclaw",
            "config",
            "set",
            "gateway.http.endpoints.chatCompletions.enabled",
            "true",
        ],
        timeout=20,
    )
    if rc != 0:
        return False, False

    runtime = detect_gateway_runtime()
    if runtime == "running":
        run_cmd(["openclaw", "gateway", "restart"], timeout=30)

    return True, True


def detect_openclaw_bin():
    """检测 openclaw 可执行文件路径"""
    oc_bin = resolve_openclaw_command()
    if oc_bin:
        return oc_bin

    # 尝试通过 npm bin -g 找到全局安装路径
    try:
        result = subprocess.run(
            ["npm", "bin", "-g"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            npm_bin = result.stdout.strip()
            candidate = os.path.join(npm_bin, "openclaw")
            if os.path.isfile(candidate):
                return candidate
    except Exception:
        pass

    # 尝试通过 npm prefix -g 找到全局安装路径
    try:
        result = subprocess.run(
            ["npm", "prefix", "-g"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            npm_prefix = result.stdout.strip()
            candidate = os.path.join(npm_prefix, "bin", "openclaw")
            if os.path.isfile(candidate):
                return candidate
    except Exception:
        pass

    # 常见位置（含腾讯内网版 wrapper）
    common_paths = [
        INTERNAL_OPENCLAW_WRAPPER,
        os.path.expanduser("~/.local/bin/openclaw"),
        os.path.expanduser("~/.npm/node_modules/bin/openclaw"),
        os.path.expanduser("~/.npm-global/bin/openclaw"),
        "/usr/local/bin/openclaw",
    ]
    if os.name == "nt":
        common_paths = [
            os.path.expanduser(r"~\AppData\Local\Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\node-v24.14.0-win-x64\openclaw.cmd"),
            os.path.expanduser(r"~\AppData\Roaming\npm\openclaw.cmd"),
            *common_paths,
        ]
    for path in common_paths:
        if os.path.isfile(path):
            return path
    return None


def detect_gateway_port():
    """探测 OpenClaw gateway 端口。

    腾讯内网版 OpenClaw 默认使用 23001 端口，开源版使用 18789。
    """
    port = get_config_value("gateway", "port")
    if isinstance(port, int):
        return port
    if isinstance(port, str) and port.isdigit():
        return int(port)

    rc, out, err = run_cmd(["openclaw", "config", "get", "gateway.port"])
    if rc == 0:
        for line in out.splitlines():
            port = line.strip()
            if port.isdigit():
                return int(port)

    # 内网版 OpenClaw 默认端口为 23001
    if is_internal_openclaw():
        return INTERNAL_OPENCLAW_GATEWAY_PORT

    return None


def detect_gateway_auth_mode():
    """探测 OpenClaw gateway.auth.mode。"""
    mode = get_config_value("gateway", "auth", "mode")
    if isinstance(mode, str) and mode.strip():
        return mode.strip().lower()

    rc, out, _ = run_cmd(["openclaw", "config", "get", "gateway.auth.mode"])
    if rc == 0 and out.strip():
        value = parse_non_banner_value(out)
        if value:
            return value.strip().lower()
    return None


def detect_gateway_token():
    """探测 OpenClaw gateway token"""
    token = get_config_value("gateway", "auth", "token")
    if isinstance(token, str) and token and token != "__OPENCLAW_REDACTED__":
        return token

    rc, out, err = run_cmd(["openclaw", "config", "get", "gateway.auth.token"])
    if rc == 0 and out.strip():
        token = parse_non_banner_value(out)
        if token and token != "__OPENCLAW_REDACTED__":
            return token
    return None


def detect_workspace_path():
    """探测 OpenClaw workspace 路径"""
    workspace = get_config_value("agents", "defaults", "workspace")
    if isinstance(workspace, str) and workspace.strip():
        return workspace.strip()

    rc, out, err = run_cmd(["openclaw", "config", "get", "agents.defaults.workspace"])
    if rc == 0 and out.strip():
        workspace = parse_non_banner_value(out)
        if workspace and os.path.sep in workspace:
            return workspace

    # 默认路径
    default_ws = DEFAULT_WORKSPACE_PATH
    if os.path.isdir(default_ws):
        return default_ws

    return None


def detect_sessions_file():
    """探测 OpenClaw sessions.json 文件路径"""
    home = os.path.expanduser("~")
    candidates = [
        DEFAULT_SESSIONS_FILE,
        os.path.join(home, ".moltbot", "agents", "main", "sessions", "sessions.json"),
        "/projects/.moltbot/agents/main/sessions/sessions.json",
        "/projects/.openclaw/agents/main/sessions/sessions.json",
    ]

    workspace = detect_workspace_path()
    if workspace:
        parent = os.path.dirname(workspace)
        candidates.insert(
            0, os.path.join(parent, "agents", "main", "sessions", "sessions.json")
        )

    unique_candidates = []
    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        unique_candidates.append(path)

    for path in unique_candidates:
        if os.path.isfile(path):
            return path

    for path in unique_candidates:
        if os.path.isdir(os.path.dirname(path)):
            return path

    return None


def inspect_sessions_health(store_path):
    """检查 sessions 索引健康状态。"""
    if not store_path or not os.path.isfile(store_path):
        return None

    rc, out, _ = run_cmd(
        [
            "openclaw",
            "sessions",
            "cleanup",
            "--store",
            store_path,
            "--dry-run",
            "--fix-missing",
            "--json",
        ],
        timeout=20,
    )
    if rc != 0:
        return None
    data = parse_json_output(out)
    return data if isinstance(data, dict) else None


def repair_sessions_health(store_path):
    """清理缺失 transcript 的 sessions 坏索引。"""
    report = inspect_sessions_health(store_path)
    if not isinstance(report, dict):
        return None

    missing = int(report.get("missing", 0) or 0)
    would_mutate = bool(report.get("wouldMutate"))
    result = {
        "checked": True,
        "missing": missing,
        "wouldMutate": would_mutate,
        "repaired": False,
        "storePath": store_path,
    }

    if missing <= 0 or not would_mutate:
        return result

    rc, out, err = run_cmd(
        [
            "openclaw",
            "sessions",
            "cleanup",
            "--store",
            store_path,
            "--enforce",
            "--fix-missing",
            "--json",
        ],
        timeout=20,
    )
    if rc != 0:
        result["error"] = err or out or "unknown error"
        return result

    applied = parse_json_output(out)
    if isinstance(applied, dict):
        result["repaired"] = bool(applied.get("applied"))
        result["afterCount"] = applied.get("afterCount")
        result["appliedCount"] = applied.get("appliedCount")
    return result


def wait_for_gateway_runtime(target="running", timeout=20, interval=1.0):
    """等待 Gateway 达到目标状态。"""
    deadline = time.time() + max(timeout, 0)
    last_runtime = None
    while time.time() <= deadline:
        last_runtime = detect_gateway_runtime()
        if last_runtime == target:
            return last_runtime
        time.sleep(interval)
    return last_runtime


def sync_openclaw_runtime_for_clawcross_startup():
    """为 Clawcross 启动准备 OpenClaw runtime。

    该流程只同步 OpenClaw runtime 相关配置，不会把 OpenClaw 的 LLM 配置
    自动导入 Clawcross，避免覆盖用户在 Clawcross 中维护的 provider/model。
    """
    result = {
        "installed": False,
        "cli_path": "",
        "runtime_before": None,
        "runtime_after": None,
        "gateway_started": False,
        "gateway_start_error": "",
        "chat_completions_enabled": False,
        "chat_completions_changed": False,
        "auth_mode": None,
        "api_url": "",
        "token_present": False,
        "sessions_file": "",
        "sessions_health": None,
        "env_updates": [],
    }

    oc_bin = detect_openclaw_bin()
    if not oc_bin:
        return result

    result["installed"] = True
    result["cli_path"] = oc_bin
    result["runtime_before"] = detect_gateway_runtime()

    if result["runtime_before"] != "running":
        rc, out, err = run_cmd(["openclaw", "gateway", "start"], timeout=30)
        if rc == 0:
            result["gateway_started"] = True
            result["runtime_after"] = wait_for_gateway_runtime(target="running", timeout=20)
        else:
            result["gateway_start_error"] = err or out or "failed to start gateway"
            result["runtime_after"] = detect_gateway_runtime()
    else:
        result["runtime_after"] = result["runtime_before"]

    enabled, changed = enable_chat_completions_endpoint()
    result["chat_completions_enabled"] = enabled
    result["chat_completions_changed"] = changed
    if changed:
        result["runtime_after"] = wait_for_gateway_runtime(target="running", timeout=20)
    elif result["runtime_after"] is None:
        result["runtime_after"] = detect_gateway_runtime()

    auth_mode = detect_gateway_auth_mode()
    result["auth_mode"] = auth_mode

    _, kvs = read_env()

    port = detect_gateway_port()
    if port:
        api_url = f"http://127.0.0.1:{port}/v1/chat/completions"
        result["api_url"] = api_url
        if kvs.get("OPENCLAW_API_URL", "").strip() != api_url:
            set_env_with_validation("OPENCLAW_API_URL", api_url)
            result["env_updates"].append("OPENCLAW_API_URL")

    token = detect_gateway_token()
    if token:
        result["token_present"] = True
        if kvs.get("OPENCLAW_GATEWAY_TOKEN", "").strip() != token:
            set_env_with_validation("OPENCLAW_GATEWAY_TOKEN", token)
            result["env_updates"].append("OPENCLAW_GATEWAY_TOKEN")

    sessions_file = detect_sessions_file()
    if sessions_file:
        result["sessions_file"] = sessions_file
        if kvs.get("OPENCLAW_SESSIONS_FILE", "").strip() != sessions_file:
            set_env_with_validation("OPENCLAW_SESSIONS_FILE", sessions_file)
            result["env_updates"].append("OPENCLAW_SESSIONS_FILE")
        result["sessions_health"] = repair_sessions_health(sessions_file)

    if result["runtime_after"] is None:
        result["runtime_after"] = detect_gateway_runtime()

    return result


def print_health_status(sessions_file=None, repair=False):
    """输出轻量健康状态；可选自动修复 sessions 坏索引。"""
    print("\n🔍 检查 OpenClaw 轻量健康状态...")

    linger = detect_linger_status()
    if linger == "yes":
        print("   ✅ systemd linger 已开启")
    elif linger == "no":
        print("   ⚠️ systemd linger 未开启，登出后 Gateway 可能停止")
        print("   可执行: loginctl enable-linger $(whoami)")
    else:
        print("   ℹ️ 未能检测 systemd linger 状态")

    if not sessions_file or not os.path.isfile(sessions_file):
        print("   ℹ️ sessions.json 尚未落盘，跳过索引体检")
        return

    report = repair_sessions_health(sessions_file) if repair else inspect_sessions_health(sessions_file)
    if not isinstance(report, dict):
        print("   ℹ️ 未能读取 sessions 索引健康状态")
        return

    missing = int(report.get("missing", 0) or 0)
    if missing <= 0:
        print("   ✅ Sessions 索引健康")
        return

    if repair and report.get("repaired"):
        print(f"   ✅ 已清理 {missing} 条缺失 transcript 的会话索引")
        return

    print(f"   ⚠️ Sessions 索引存在 {missing} 条缺失 transcript 的坏索引")
    print("   可执行:")
    print(f"   openclaw sessions cleanup --store {sessions_file} --enforce --fix-missing")


def detect_llm_config_from_openclaw():
    """从 OpenClaw 配置中探测 LLM 相关参数（API Key / Base URL / Model / Provider）。

    读取 openclaw.json 中的 models.providers 和 agents.defaults.model，
    返回 dict 形如:
        {"LLM_API_KEY": "...", "LLM_BASE_URL": "...", "LLM_MODEL": "...", "LLM_PROVIDER": "..."}
    仅包含成功探测到的字段。
    """
    result: dict[str, str] = {}

    def _strip_openclaw_base_url_suffix(base_url: str) -> str:
        """OpenClaw baseUrl 常带 /v1；Clawcross LLM_BASE_URL 不带末尾 /v1。"""
        u = (base_url or "").strip()
        if not u:
            return u
        stripped = u.rstrip("/")
        if stripped.endswith("/v1"):
            stripped = stripped[:-3]
        return stripped

    # Step 1: always use openclaw.json defaults.model.primary to decide provider/model.
    config = load_openclaw_config()
    if not config:
        return result

    default_model = get_config_value("agents", "defaults", "model", "primary")
    provider_id = None
    model_id = None
    if isinstance(default_model, str) and "/" in default_model:
        provider_id, model_id = default_model.split("/", 1)
    provider_id = (provider_id or "").strip() or None
    model_id = (model_id or "").strip() or None

    # Also load legacy providers map for fallback.
    providers_legacy = get_config_value("models", "providers") or {}

    # Step 2: read agent models.json to fill apiKey/baseUrl (preferred).
    models_doc = load_openclaw_agent_models()
    providers_doc = models_doc.get("providers") if isinstance(models_doc, dict) else None

    provider_cfg_doc = None
    if isinstance(providers_doc, dict) and provider_id and provider_id in providers_doc:
        provider_cfg_doc = providers_doc.get(provider_id)

    # If defaults didn't provide provider/model, fall back to first provider in models.json.
    if provider_cfg_doc is None and isinstance(providers_doc, dict) and providers_doc:
        for pid, cfg in providers_doc.items():
            if not isinstance(cfg, dict):
                continue
            models_list = cfg.get("models")
            if isinstance(models_list, list) and models_list:
                provider_id = str(pid).strip()
                provider_cfg_doc = cfg
                if not model_id:
                    first = models_list[0]
                    if isinstance(first, dict) and first.get("id"):
                        model_id = str(first.get("id") or "").strip() or None
                break
        # last resort: pick the first provider cfg
        if provider_cfg_doc is None:
            for pid, cfg in providers_doc.items():
                if isinstance(cfg, dict):
                    provider_id = str(pid).strip()
                    provider_cfg_doc = cfg
                    break

    # Fill from models.json provider cfg
    if isinstance(provider_cfg_doc, dict) and provider_cfg_doc:
        api_key = str(provider_cfg_doc.get("apiKey") or "").strip()
        base_url = str(provider_cfg_doc.get("baseUrl") or "").strip()

        # Ensure model_id is valid if we can match it.
        models_list = provider_cfg_doc.get("models")
        if isinstance(models_list, list) and models_list:
            if model_id:
                matched = False
                for m in models_list:
                    if isinstance(m, dict) and str(m.get("id") or "").strip() == model_id:
                        matched = True
                        break
                if not matched:
                    # Sometimes caller might pass "name"; try to map it back to id.
                    for m in models_list:
                        if isinstance(m, dict) and str(m.get("name") or "").strip() == model_id:
                            mid = str(m.get("id") or "").strip()
                            if mid:
                                model_id = mid
                            break
            else:
                first = models_list[0]
                if isinstance(first, dict) and first.get("id"):
                    model_id = str(first.get("id") or "").strip() or None

        if base_url:
            result["LLM_BASE_URL"] = _strip_openclaw_base_url_suffix(base_url)
        if api_key:
            result["LLM_API_KEY"] = api_key
        if model_id:
            result["LLM_MODEL"] = model_id
        if provider_id:
            result["LLM_PROVIDER"] = provider_id

    # Step 3: fallback to openclaw.json providers map if apiKey/baseUrl missing.
    if provider_id:
        provider_cfg_legacy = providers_legacy.get(provider_id, {}) if isinstance(providers_legacy, dict) else {}
        if isinstance(provider_cfg_legacy, dict):
            provider_name = str(provider_id).strip().lower()

            if "LLM_API_KEY" not in result or not result.get("LLM_API_KEY"):
                api_key = str(provider_cfg_legacy.get("apiKey") or "").strip()
                # OpenClaw openai provider prefers env.OPENAI_API_KEY.
                if provider_name == "openai":
                    api_key = ((config.get("env") or {}).get("OPENAI_API_KEY") or "").strip() or api_key
                elif not api_key and not provider_name:
                    api_key = ((config.get("env") or {}).get("OPENAI_API_KEY") or "").strip()
                if api_key:
                    result["LLM_API_KEY"] = api_key

            if "LLM_BASE_URL" not in result or not result.get("LLM_BASE_URL"):
                base_url = str(provider_cfg_legacy.get("baseUrl") or "").strip()
                if base_url:
                    result["LLM_BASE_URL"] = _strip_openclaw_base_url_suffix(base_url)

            # No agent models.json (or empty): still emit model/provider from
            # agents.defaults.model.primary + openclaw.json models.providers.
            if model_id and not result.get("LLM_MODEL"):
                mid = model_id
                models_list = provider_cfg_legacy.get("models")
                if isinstance(models_list, list) and models_list:
                    matched = False
                    for m in models_list:
                        if isinstance(m, dict) and str(m.get("id") or "").strip() == mid:
                            matched = True
                            break
                    if not matched:
                        for m in models_list:
                            if isinstance(m, dict) and str(m.get("name") or "").strip() == mid:
                                resolved = str(m.get("id") or "").strip()
                                if resolved:
                                    mid = resolved
                                break
                result["LLM_MODEL"] = mid

            if not result.get("LLM_PROVIDER"):
                result["LLM_PROVIDER"] = provider_id

    return result


# 模板 / 初始化向导留的默认 BASE_URL，不应阻止从 OpenClaw 导入真实 provider 的 baseUrl
_BOOTSTRAP_LLM_BASE_URLS = frozenset(
    {
        "",
        "https://api.deepseek.com",
        "http://api.deepseek.com",
    }
)


def _is_bootstrap_llm_base_url(url: str) -> bool:
    u = (url or "").strip().lower().rstrip("/")
    if not u:
        return True
    if u in {x.rstrip("/") for x in _BOOTSTRAP_LLM_BASE_URLS if x}:
        return True
    return False


def _llm_base_url_conflicts_with_openclaw(kvs: dict, detected: dict) -> bool:
    """Clawcross 里 BASE_URL 与 OpenClaw 探测结果明显不一致（如 DeepSeek 模板 + MiniMax 模型）。"""
    ex_url = (kvs.get("LLM_BASE_URL") or "").strip().lower()
    det_url = (detected.get("LLM_BASE_URL") or "").strip().lower()
    if not det_url or not ex_url:
        return False
    if ex_url.rstrip("/") == det_url.rstrip("/"):
        return False
    if _is_bootstrap_llm_base_url(ex_url):
        return True
    det_p = (detected.get("LLM_PROVIDER") or "").strip().lower()
    if det_p == "minimax" and "deepseek" in ex_url:
        return True
    if det_p == "deepseek" and "minimaxi" in ex_url:
        return True
    return False


def sync_llm_config_from_openclaw():
    """从 OpenClaw 同步 LLM 配置到 Clawcross .env。

    仅同步未设置或仍为占位符的字段，不覆盖用户已显式设置的值。
    模板默认的 LLM_BASE_URL（如 api.deepseek.com）视为可覆盖，以便导入 OpenClaw 真实 baseUrl。
    返回成功同步的字段数量。
    """
    detected = detect_llm_config_from_openclaw()
    if not detected:
        return 0

    _, kvs = read_env()
    kvs = dict(kvs)
    synced = 0
    placeholder_values = {"your_api_key_here", ""}
    base_url_conflict = _llm_base_url_conflicts_with_openclaw(kvs, detected)

    print("\n🔍 从 OpenClaw 同步 LLM 配置...")
    for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_PROVIDER"):
        new_val = detected.get(key)
        if not new_val:
            continue
        existing = (kvs.get(key) or "").strip()
        if existing and existing not in placeholder_values:
            if key == "LLM_BASE_URL" and (
                _is_bootstrap_llm_base_url(existing) or base_url_conflict
            ):
                pass
            else:
                display = mask_secret(existing) if key in {"LLM_API_KEY"} else existing
                print(f"   ⏭️  {key} 已设置 ({display})，保留不变")
                continue
        set_env_with_validation(key, new_val)
        kvs[key] = new_val
        synced += 1

    if synced > 0:
        print(f"   ✅ 从 OpenClaw 同步了 {synced} 项 LLM 配置")
    else:
        print("   ℹ️ LLM 配置已是最新，无需同步")

    return synced


def _infer_clawcross_provider(provider, base_url="", model=""):
    """根据 Clawcross 当前配置推断 OpenClaw provider id。"""
    if isinstance(provider, str) and provider.strip():
        return provider.strip().lower()

    base = (base_url or "").strip().lower()
    model_name = (model or "").strip().lower()

    base_markers = [
        ("api.deepseek.com", "deepseek"),
        ("api.openai.com", "openai"),
        ("generativelanguage.googleapis.com", "google"),
        ("api.anthropic.com", "anthropic"),
        ("api.minimaxi.com", "minimax"),
        ("127.0.0.1:8045", "antigravity"),
        ("localhost:8045", "antigravity"),
        ("127.0.0.1:11434", "ollama"),
        ("localhost:11434", "ollama"),
    ]
    for marker, provider_id in base_markers:
        if marker in base:
            return provider_id

    model_markers = [
        (("deepseek",), "deepseek"),
        (("claude",), "anthropic"),
        (("gemini",), "google"),
        (("minimax", "abab"), "minimax"),
        (("gpt-", "o1", "o3", "o4"), "openai"),
    ]
    for markers, provider_id in model_markers:
        if any(model_name.startswith(marker) for marker in markers):
            return provider_id

    return "openai"


def _provider_is_local_keyless(provider_id, base_url=""):
    """判断 provider 是否允许本地无 key 运行。"""
    provider = (provider_id or "").strip().lower()
    base = (base_url or "").strip().lower()
    if provider == "ollama":
        return True
    return "127.0.0.1:11434" in base or "localhost:11434" in base


def _openclaw_provider_api(provider_id):
    """将 Clawcross provider 映射到 OpenClaw provider API 类型。"""
    provider = (provider_id or "").strip().lower()
    if provider == "anthropic":
        return "anthropic-messages"
    if provider == "google":
        return "google-generative-ai"
    return "openai-completions"


def _strip_base_url_suffixes(base_url):
    """去掉常见 endpoint 后缀，保留 provider base URL。"""
    value = (base_url or "").strip().rstrip("/")
    if not value:
        return ""

    suffixes = (
        "/chat/completions",
        "/models",
        "/messages",
        "/responses",
    )
    lowered = value.lower()
    for suffix in suffixes:
        if lowered.endswith(suffix):
            value = value[: -len(suffix)]
            lowered = value.lower()
            break
    return value.rstrip("/")


def _normalize_openclaw_base_url(provider_id, base_url):
    """将 Clawcross 的 base URL 规范为 OpenClaw provider 所需格式。"""
    value = _strip_base_url_suffixes(base_url)
    if not value:
        return ""

    api_type = _openclaw_provider_api(provider_id)
    if api_type == "openai-completions":
        if not re.search(r"/v\d+(?:[a-z0-9._-]+)?$", value, re.IGNORECASE):
            value = value.rstrip("/") + "/v1"
    return value


def _looks_like_reasoning_model(model_id):
    model = (model_id or "").strip().lower()
    markers = (
        "reasoner",
        "reasoning",
        "r1",
        "o1",
        "o3",
        "o4",
        "think",
    )
    return any(marker in model for marker in markers)


def _looks_like_vision_model(provider_id, model_id):
    provider = (provider_id or "").strip().lower()
    model = (model_id or "").strip().lower()

    if provider in {"google", "anthropic"}:
        return True

    markers = (
        "vision",
        "image",
        "multimodal",
        "gpt-4o",
        "gpt-4.1",
        "gpt-5",
        "gemini",
        "claude",
        "o1",
        "o3",
        "o4",
    )
    return any(marker in model for marker in markers)


def _build_openclaw_model_entry(model_id, provider_id, existing=None):
    """构造 OpenClaw provider.models 中的模型描述。"""
    entry = existing.copy() if isinstance(existing, dict) else {}
    entry["id"] = model_id
    entry.setdefault("name", model_id)
    entry.setdefault("reasoning", _looks_like_reasoning_model(model_id))
    entry.setdefault(
        "input",
        ["text", "image"] if _looks_like_vision_model(provider_id, model_id) else ["text"],
    )
    entry.setdefault("contextWindow", 128000)
    entry.setdefault("maxTokens", 8192)
    return entry


def _write_openclaw_config(config):
    os.makedirs(os.path.dirname(OPENCLAW_CONFIG_PATH), exist_ok=True)
    with open(OPENCLAW_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")


def export_llm_config_to_openclaw(api_key, base_url, model, provider=""):
    """将 Clawcross 当前 LLM 设置写回 OpenClaw 默认模型配置。"""
    api_key = (api_key or "").strip()
    base_url = (base_url or "").strip()
    model = (model or "").strip()
    provider_id = _infer_clawcross_provider(provider, base_url, model)
    is_local_keyless = _provider_is_local_keyless(provider_id, base_url)

    if not api_key and not is_local_keyless:
        raise ValueError("LLM_API_KEY 不能为空")
    if not base_url:
        raise ValueError("LLM_BASE_URL 不能为空")
    if not model:
        raise ValueError("LLM_MODEL 不能为空")
    if not api_key and is_local_keyless:
        api_key = "ollama"

    config = load_openclaw_config()
    if not isinstance(config, dict):
        config = {}
    original_config = copy.deepcopy(config)

    agents_cfg = config.setdefault("agents", {})
    defaults_cfg = agents_cfg.setdefault("defaults", {})
    model_defaults = defaults_cfg.setdefault("model", {})
    default_models = defaults_cfg.setdefault("models", {})

    models_cfg = config.setdefault("models", {})
    providers_cfg = models_cfg.setdefault("providers", {})

    provider_cfg = providers_cfg.get(provider_id)
    if not isinstance(provider_cfg, dict):
        provider_cfg = {}

    provider_cfg["baseUrl"] = _normalize_openclaw_base_url(provider_id, base_url)
    provider_cfg["apiKey"] = api_key
    provider_cfg["api"] = _openclaw_provider_api(provider_id)

    models_list = provider_cfg.get("models")
    if not isinstance(models_list, list):
        models_list = []

    existing_entry = None
    existing_index = None
    for index, entry in enumerate(models_list):
        if isinstance(entry, dict) and entry.get("id") == model:
            existing_entry = entry
            existing_index = index
            break

    updated_entry = _build_openclaw_model_entry(model, provider_id, existing=existing_entry)
    if existing_index is None:
        models_list.append(updated_entry)
    else:
        models_list[existing_index] = updated_entry
    provider_cfg["models"] = models_list
    providers_cfg[provider_id] = provider_cfg

    if provider_id == "openai":
        env_cfg = config.get("env")
        if not isinstance(env_cfg, dict):
            env_cfg = {}
            config["env"] = env_cfg
        # OpenClaw 的 openai provider 可能优先读取 env.OPENAI_API_KEY。
        # 这里必须和 provider.apiKey 一并写，避免实际运行继续命中旧凭据。
        env_cfg["OPENAI_API_KEY"] = api_key

    model_ref = f"{provider_id}/{model}"
    if not isinstance(default_models, dict):
        default_models = {}
        defaults_cfg["models"] = default_models
    default_models.setdefault(model_ref, {})
    if not isinstance(model_defaults, dict):
        model_defaults = {}
        defaults_cfg["model"] = model_defaults
    model_defaults["primary"] = model_ref

    changed = config != original_config
    if changed:
        _write_openclaw_config(config)

    gateway_runtime = detect_gateway_runtime()
    gateway_running = gateway_runtime == "running"
    gateway_restarted = False
    restart_error = ""
    if changed and gateway_running:
        rc, out, err = run_cmd(["openclaw", "gateway", "restart"], timeout=30)
        gateway_restarted = rc == 0
        if rc != 0:
            restart_error = err or out or "gateway restart failed"

    return {
        "ok": True,
        "provider": provider_id,
        "model": model,
        "model_ref": model_ref,
        "config_path": OPENCLAW_CONFIG_PATH,
        "changed": changed,
        "gateway_running": gateway_running,
        "gateway_restarted": gateway_restarted,
        "restart_error": restart_error,
    }


def read_clawcross_llm_config():
    """读取 Clawcross 当前 LLM 配置。"""
    _, kvs = read_env()
    return {
        "LLM_API_KEY": (kvs.get("LLM_API_KEY") or "").strip(),
        "LLM_BASE_URL": (kvs.get("LLM_BASE_URL") or "").strip(),
        "LLM_MODEL": (kvs.get("LLM_MODEL") or "").strip(),
        "LLM_PROVIDER": (kvs.get("LLM_PROVIDER") or "").strip(),
    }


def sync_clawcross_llm_to_openclaw():
    """将 Clawcross 当前 LLM 配置回写到 OpenClaw。"""
    config = read_clawcross_llm_config()
    provider_id = _infer_clawcross_provider(
        config.get("LLM_PROVIDER", ""),
        config.get("LLM_BASE_URL", ""),
        config.get("LLM_MODEL", ""),
    )
    missing = []
    if not config.get("LLM_BASE_URL"):
        missing.append("LLM_BASE_URL")
    if not config.get("LLM_MODEL"):
        missing.append("LLM_MODEL")
    if not config.get("LLM_API_KEY") and not _provider_is_local_keyless(
        provider_id,
        config.get("LLM_BASE_URL", ""),
    ):
        missing.append("LLM_API_KEY")
    if missing:
        raise ValueError(
            "Clawcross 当前 LLM 配置不完整，缺少: " + ", ".join(missing)
        )

    return export_llm_config_to_openclaw(
        api_key=config["LLM_API_KEY"],
        base_url=config["LLM_BASE_URL"],
        model=config["LLM_MODEL"],
        provider=config.get("LLM_PROVIDER", ""),
    )


def auto_detect_and_configure():
    """自动探测 OpenClaw 配置并写入 .env"""
    oc_bin = detect_openclaw_bin()
    if not oc_bin:
        print("❌ OpenClaw 未安装，无法自动配置")
        print("")
        print_install_guide()
        return False

    print(f"📍 OpenClaw 路径: {oc_bin}")
    if is_internal_openclaw():
        print("📌 版本类型: 腾讯内网版 (gateway 默认端口 23001)")

    # 获取版本信息
    rc, out, _ = run_cmd([oc_bin, "--version"])
    if rc == 0:
        print(f"📌 版本: {out.splitlines()[0] if out else 'unknown'}")
    if os.path.isfile(OPENCLAW_CONFIG_PATH):
        print(f"📁 配置: {OPENCLAW_CONFIG_PATH}")

    changes = 0

    # 0. 从 OpenClaw 同步 LLM 配置（API Key / Base URL / Model / Provider）
    llm_synced = sync_llm_config_from_openclaw()
    changes += llm_synced

    print("\n🔍 检查 OpenAI Chat Completions 兼容端点...")
    enabled, changed = enable_chat_completions_endpoint()
    if enabled:
        if changed:
            print("   ✅ 已启用 gateway.http.endpoints.chatCompletions.enabled=true")
            print("   ℹ️ Clawcross 的 HTTP 回退链路现在可使用 /v1/chat/completions")
        else:
            print("   ✅ /v1/chat/completions 兼容端点已开启")
    else:
        print("   ⚠️ 无法自动开启 /v1/chat/completions 兼容端点")
        print("   请手动执行:")
        print("   openclaw config set gateway.http.endpoints.chatCompletions.enabled true")
        print("   openclaw gateway restart")

    auth_mode = detect_gateway_auth_mode()
    print("\n🔍 探测 Gateway 认证模式...")
    if auth_mode:
        print(f"   Gateway Auth: {auth_mode}")
        if auth_mode == "none":
            print("   ℹ️ 当前为 loopback + 无认证控制台，浏览器访问无需再粘贴 token")
    else:
        print("   ℹ️ 未能探测 gateway.auth.mode，后续按 token 模式兼容处理")

    # 1. 探测 gateway 端口 → OPENCLAW_API_URL
    print("\n🔍 探测 Gateway 端口...")
    port = detect_gateway_port()
    if port:
        api_url = f"http://127.0.0.1:{port}/v1/chat/completions"
        print(f"   Gateway 端口: {port}")
        set_env_with_validation("OPENCLAW_API_URL", api_url)
        changes += 1
    else:
        # 检查 .env 是否已有配置
        _, kvs = read_env()
        if "OPENCLAW_API_URL" in kvs:
            print(f"   ⚠️ 无法自动探测 gateway 端口，保留现有配置: {kvs['OPENCLAW_API_URL']}")
        else:
            print("   ⚠️ 无法自动探测 gateway 端口")
            print("   提示: 确保 OpenClaw gateway 正在运行 (openclaw gateway)")
            default_hint_port = INTERNAL_OPENCLAW_GATEWAY_PORT if is_internal_openclaw() else DEFAULT_GATEWAY_PORT
            print(f"   或手动配置: bash selfskill/scripts/run.sh configure OPENCLAW_API_URL http://127.0.0.1:{default_hint_port}/v1/chat/completions")

    # 2. 探测 gateway token → OPENCLAW_GATEWAY_TOKEN
    print("\n🔍 探测 Gateway Token...")
    token = detect_gateway_token()
    if token:
        print(f"   Token: {mask_secret(token)}")
        set_env_with_validation("OPENCLAW_GATEWAY_TOKEN", token)
        changes += 1
    else:
        _, kvs = read_env()
        if auth_mode == "none":
            if "OPENCLAW_GATEWAY_TOKEN" in kvs and kvs["OPENCLAW_GATEWAY_TOKEN"]:
                print("   ℹ️ Gateway 当前为 auth=none；保留 .env 中已有 token 供兼容调用使用")
            else:
                print("   ℹ️ Gateway 当前为 auth=none；无需额外 token")
        elif "OPENCLAW_GATEWAY_TOKEN" in kvs:
            print("   ⚠️ 无法自动探测 token，保留现有配置")
        else:
            if auth_mode and auth_mode != "token":
                print(f"   ℹ️ 当前 auth={auth_mode}；未检测到 gateway token")
            else:
                print("   ℹ️ 未检测到 gateway token（CLI 模式下通常不需要）")

    # 3. 探测 sessions 文件 → OPENCLAW_SESSIONS_FILE
    print("\n🔍 探测 Sessions 文件...")
    sessions_file = detect_sessions_file()
    if sessions_file:
        print(f"   Sessions: {sessions_file}")
        set_env_with_validation("OPENCLAW_SESSIONS_FILE", sessions_file)
        changes += 1
        if not os.path.isfile(sessions_file):
            print("   ℹ️ sessions.json 尚未生成；首次创建/运行 agent session 后会落盘")
    else:
        _, kvs = read_env()
        if "OPENCLAW_SESSIONS_FILE" in kvs:
            print(f"   ⚠️ 未找到 sessions.json，保留现有配置: {kvs['OPENCLAW_SESSIONS_FILE']}")
        else:
            print("   ⚠️ 未找到 sessions.json 文件")
            print("   提示: 首次创建/运行 OpenClaw agent session 后会自动创建")

    print_health_status(sessions_file=sessions_file, repair=True)

    runtime = detect_gateway_runtime()
    if runtime == "running":
        print("\n✅ Gateway 运行中：HTTP 回退链路可直接使用")
    elif runtime == "stopped":
        print("\n⚠️ Gateway 当前未运行：CLI 集成可用，HTTP 回退链路暂不可用")
        print("   如需启用 HTTP 回退链路，请执行:")
        print("   openclaw gateway start")
    else:
        print("\nℹ️ 未能确认 Gateway 运行状态")

    # 总结
    print(f"\n{'=' * 50}")
    if changes > 0:
        print(f"✅ 已自动配置 {changes} 项 OpenClaw 集成参数")
        print(f"📁 配置已写入: {ENV_PATH}")
    else:
        print("ℹ️ 未检测到新的配置变更")

    print("\n💡 OpenClaw 集成要点：")
    print("   • OpenClaw Agent 优先通过 CLI 调用（无需额外配置）")
    if auth_mode == "none":
        print("   • HTTP 回退模式至少需要 OPENCLAW_API_URL；auth=none 时 token 可留空")
    else:
        print("   • HTTP 回退模式通常需要 OPENCLAW_API_URL + OPENCLAW_GATEWAY_TOKEN")
    print("   • 前端画布需要 OPENCLAW_SESSIONS_FILE 来加载 Agent sessions")

    # 4. 初始化 workspace 默认模板
    init_workspace_templates()

    return True


# --------------- Workspace 模板初始化 ---------------

# OpenClaw 8 个核心文件的默认模板
_WORKSPACE_TEMPLATES = {
    "BOOTSTRAP.md": """\
# BOOTSTRAP

This is the first run of the agent.

Your task is to learn who you are and who the user is.

## Steps

1. Ask the user their preferred name.
2. Ask what kind of assistant you should be (coding, research, general, etc.).
3. Ask their timezone and primary goals.
4. Write the answers to:
   - IDENTITY.md (your name, role, traits)
   - USER.md (user profile and preferences)
   - SOUL.md (update behavior rules based on user needs)

## Guidelines

- Ask one question at a time.
- Keep the conversation natural and concise.
- Do not overwhelm the user.
- Respect existing file content — merge, don't overwrite.

Once finished, you may remove this file or leave it as a record.
""",
    "SOUL.md": """\
# SOUL — Who You Are

You are a practical, efficient AI assistant.

## Core Principles

1. **Start with the answer.** Give the result first, then explain if needed.
2. **Be helpful, not verbose.** Respect the user's time.
3. **Prefer action over explanation.** If you can do it, do it.
4. **Admit uncertainty.** Say "I don't know" rather than guessing.

## Communication Style

- Clear and direct
- Structured with headings and lists when helpful
- Professional but friendly
- No filler phrases ("Great question!", "I'd be happy to help", "Certainly!")

## Thinking Rules

Before answering:

1. Read USER.md for user context and preferences.
2. Check MEMORY.md for relevant past interactions.
3. Determine if tools are needed before writing code.

## Safety Rules

Never execute destructive commands without explicit confirmation:

- Deleting files or directories
- Running unknown shell scripts
- Modifying system configurations
- Force-pushing to git remotes
- Operations that cannot be undone

Always ask for confirmation first.

## Decision Making

When multiple solutions exist:

1. Choose the simplest approach that works.
2. Briefly mention alternatives if they have significant trade-offs.
3. Prefer well-tested, standard solutions over clever hacks.

## Collaboration

This agent may be orchestrated by Clawcross (multi-agent workflow system).
When receiving tasks from Clawcross:

- Follow the task instructions precisely.
- Return structured results when possible.
- Use `[oasis reply start]` and `[oasis reply end]` tags when requested.
""",
    "IDENTITY.md": """\
# IDENTITY

Name: Atlas

Type: Personal AI Assistant

Role: A technical AI assistant that helps the user with coding,
research, automation, and problem-solving.

Traits:

- Analytical — breaks problems into clear steps
- Pragmatic — favors working solutions over perfect ones
- Efficient — minimizes unnecessary output
- Curious — explores context before jumping to conclusions

Emoji: 🦞
""",
    "AGENTS.md": """\
# AGENTS

This workspace supports multi-agent collaboration.

## Primary Agent

**main** (default)
- Role: General-purpose assistant
- Handles: coding, research, automation, Q&A

## Clawcross Integration

This agent can be orchestrated by Clawcross for multi-agent workflows.
Clawcross communicates via CLI (`openclaw agent --agent main --message ...`)
or HTTP gateway as fallback.

When working within a Clawcross workflow:

- You may receive tasks from an orchestrator agent.
- Follow instructions precisely and return structured output.
- Use `[oasis reply start]...[oasis reply end]` tags when the caller expects them.

## Adding More Agents

Use the OpenClaw CLI to add specialized agents:

```
openclaw agents add <name> --workspace <path> --non-interactive
```

Or use Clawcross's frontend to create and manage agents visually.
""",
    "TOOLS.md": """\
# TOOLS

You may use tools when needed. Choose the simplest tool for the task.

## Available Tools

**shell**
Run safe local commands (ls, cat, grep, git, etc.).
Avoid destructive operations without confirmation.

**python**
Use for calculations, data processing, or scripting.

**file_ops**
Read, write, and manage files in the workspace.

**web_search**
Use when information may be outdated or unknown.

## Rules

1. Choose the simplest tool that solves the problem.
2. Briefly explain why a tool is being used if not obvious.
3. Never run dangerous commands (rm -rf, format, etc.) without asking.
4. Prefer reading files before modifying them.
5. When writing code, include error handling.

## Tool Selection Priority

1. Direct answer from knowledge → no tool needed
2. File operation → file_ops
3. Computation or data → python
4. System task → shell
5. Unknown or outdated info → web_search
""",
    "USER.md": """\
# USER

Name: User

Timezone: Asia/Shanghai

## Preferences

- Concise, actionable answers
- Technical depth when needed
- Code examples over lengthy explanations
- Chinese (简体中文) for conversation when preferred

## Interests

- AI agents and multi-agent systems
- Software development and automation
- Machine learning and model training
- DevOps and infrastructure

## Notes

- This file is updated during BOOTSTRAP or manually.
- The agent should adapt behavior based on these preferences.
""",
    "HEARTBEAT.md": """\
# HEARTBEAT

Scheduled and recurring tasks.

## After Each Conversation

- Update MEMORY.md with important decisions or preferences.
- Note any unfinished tasks.

## Daily

- Summarize key interactions if the day was active.
- Check for pending tasks in MEMORY.md.

## Weekly

- Review recurring patterns and suggest optimizations.
- Clean up outdated entries in MEMORY.md.

## Priority

Always prioritize user commands over background tasks.
Never interrupt active work for scheduled tasks.
""",
    "MEMORY.md": """\
# MEMORY

Long-term memory for this workspace.

## What to Remember

- User preferences and working style
- Important decisions and their rationale
- Recurring workflows and shortcuts
- Project-specific context

## Current Notes

- Initial setup via Clawcross auto-configuration.
- Workspace initialized with default templates.

---

*Update this file as you learn more about the user and their workflow.*
""",
}

CLAWCROSS_WORKSPACE_SKILL = "Clawcross"
CLAWCROSS_BLOCK_START = "<!-- CLAWCROSS AUTO START -->"
CLAWCROSS_BLOCK_END = "<!-- CLAWCROSS AUTO END -->"


def _clawcross_run_prefix():
    """返回当前平台的 Clawcross 运维脚本前缀。"""
    if os.name == "nt":
        return "powershell -ExecutionPolicy Bypass -File selfskill/scripts/run.ps1"
    return "bash selfskill/scripts/run.sh"


def _clawcross_openclaw_cmd():
    """返回当前平台更稳妥的 OpenClaw 命令。"""
    if os.name == "nt":
        return "openclaw.cmd"
    return "openclaw"


def _clawcross_skill_templates():
    """生成 Clawcross workspace skill 文件。"""
    run_prefix = _clawcross_run_prefix()
    openclaw_cmd = _clawcross_openclaw_cmd()
    skill_root = os.path.join("skills", CLAWCROSS_WORKSPACE_SKILL)

    return {
        os.path.join(skill_root, "SKILL.md"): f"""\
---
name: Clawcross
description: Operate the local Clawcross repository and services from this OpenClaw workspace. Use when the user asks to install, configure, start, stop, debug, or modify Clawcross, or to manage Clawcross's OpenClaw integration, teams, workflows, channels, or CLI.
metadata:
  short-description: Control the local Clawcross repo
---

# Clawcross

Use this skill when the user wants you to control Clawcross in `{PROJECT_ROOT}`.

## First Read

1. Read the operator entrypoint: `{os.path.join(PROJECT_ROOT, "SKILL.md")}`
2. Use the doc router: `{os.path.join(PROJECT_ROOT, "docs", "index.md")}`
3. Before code changes, read: `{os.path.join(PROJECT_ROOT, "docs", "repo-index.md")}`
4. For OpenClaw integration, read: `{os.path.join(PROJECT_ROOT, "docs", "openclaw-commands.md")}`
5. For command details, read [CLI Cheat Sheet](./references/cli.md) first, then `{os.path.join(PROJECT_ROOT, "docs", "cli.md")}` when you need full flags.

## Working Rules

- Run Clawcross commands from `{PROJECT_ROOT}`.
- Prefer `run.sh` / `run.ps1` and `uv run scripts/cli.py` over ad-hoc edits.
- If OpenClaw was installed or reconfigured, run `{run_prefix} check-openclaw`.
- Do not enable optional integrations, public exposure, or password users unless the user explicitly asks.
- Before touching code, inspect only the files routed by `{os.path.join(PROJECT_ROOT, "docs", "repo-index.md")}` instead of scanning the whole repo.
- When the user asks to operate Clawcross, assume they want action, not just documentation.

## Quick Entry Points

- Service lifecycle and environment: [CLI Cheat Sheet](./references/cli.md)
- Full Clawcross command reference: `{os.path.join(PROJECT_ROOT, "docs", "cli.md")}`
- OpenClaw install / binding / repair notes: `{os.path.join(PROJECT_ROOT, "docs", "openclaw-commands.md")}`
""",
        os.path.join(skill_root, "references", "cli.md"): f"""\
# Clawcross CLI Cheat Sheet

Repository root: `{PROJECT_ROOT}`

Run these commands from the Clawcross repo root.

## Service lifecycle

- `{run_prefix} status`
- `{run_prefix} start`
- `{run_prefix} start-foreground`
- `{run_prefix} stop`
- `{run_prefix} restart`

## Configuration

- `{run_prefix} configure --init`
- `{run_prefix} configure KEY VALUE`
- `{run_prefix} auto-model`

## OpenClaw sync and checks

- `{run_prefix} check-openclaw`
- `{run_prefix} check-openclaw-weixin`
- `{run_prefix} bind-openclaw-channel main openclaw-weixin:<account_id>`
- `{openclaw_cmd} gateway status`
- `{openclaw_cmd} channels list --json`
- `{openclaw_cmd} skills list --json`

## Clawcross repo-local CLI

- `uv run scripts/cli.py status`
- `uv run scripts/cli.py openclaw`
- `uv run scripts/cli.py openclaw detail --name main`
- `uv run scripts/cli.py openclaw channels`
- `uv run scripts/cli.py openclaw bindings --agent main`
- `uv run scripts/cli.py teams`
- `uv run scripts/cli.py visual`

## Reading order

1. `{os.path.join(PROJECT_ROOT, "SKILL.md")}`
2. `{os.path.join(PROJECT_ROOT, "docs", "index.md")}`
3. `{os.path.join(PROJECT_ROOT, "docs", "repo-index.md")}` before code
4. `{os.path.join(PROJECT_ROOT, "docs", "cli.md")}` for full flag details
""",
    }


def _clawcross_workspace_blocks():
    """生成注入到 OpenClaw 核心 workspace 文件中的 Clawcross 引导块。"""
    run_prefix = _clawcross_run_prefix()
    skill_path = os.path.join("skills", CLAWCROSS_WORKSPACE_SKILL, "SKILL.md")
    repo_skill = os.path.join(PROJECT_ROOT, "SKILL.md")
    repo_index = os.path.join(PROJECT_ROOT, "docs", "repo-index.md")
    repo_cli = os.path.join(PROJECT_ROOT, "docs", "cli.md")

    return {
        "BOOTSTRAP.md": f"""\
## Clawcross Bootstrap

This workspace is paired with Clawcross at `{PROJECT_ROOT}`.

If the user wants you to operate Clawcross, read `{skill_path}` and `{repo_skill}` after the basic identity bootstrap. Use them to learn the Clawcross command surface before taking action.
""",
        "AGENTS.md": f"""\
## Clawcross Control

This OpenClaw workspace is connected to Clawcross at `{PROJECT_ROOT}`.

When the user asks about Clawcross, load the workspace skill `{CLAWCROSS_WORKSPACE_SKILL}` first. Then use `{repo_skill}` as the repo's own task router and `{repo_index}` before code changes.
""",
        "TOOLS.md": f"""\
## Clawcross CLI

For Clawcross tasks, run commands from `{PROJECT_ROOT}` and prefer the project wrappers over ad-hoc operations.

- `{run_prefix} status`
- `{run_prefix} start`
- `{run_prefix} stop`
- `{run_prefix} check-openclaw`
- `uv run scripts/cli.py openclaw channels`
- `uv run scripts/cli.py openclaw bindings --agent main`
- Read `{repo_cli}` when you need full CLI flags.
""",
    }


def _upsert_managed_block(filepath: str, block: str):
    """向 Markdown 文件中写入可重复更新的自动管理块。"""
    managed = (
        f"{CLAWCROSS_BLOCK_START}\n"
        f"{block.strip()}\n"
        f"{CLAWCROSS_BLOCK_END}"
    )

    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            current = f.read()
    else:
        current = ""

    if CLAWCROSS_BLOCK_START in current and CLAWCROSS_BLOCK_END in current:
        start = current.find(CLAWCROSS_BLOCK_START)
        end = current.find(CLAWCROSS_BLOCK_END, start)
        if end < 0:
            updated = current.rstrip()
            updated = f"{updated}\n\n{managed}\n" if updated else f"{managed}\n"
        else:
            end += len(CLAWCROSS_BLOCK_END)
            prefix = current[:start].rstrip()
            suffix = current[end:].lstrip("\n")
            parts = []
            if prefix:
                parts.append(prefix)
            parts.append(managed)
            if suffix:
                parts.append(suffix.rstrip())
            updated = "\n\n".join(parts) + "\n"
    else:
        base = current.rstrip()
        updated = f"{base}\n\n{managed}\n" if base else f"{managed}\n"

    if updated == current:
        return False

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(updated)
    return True


def init_workspace_templates(workspace_path: str | None = None):
    """初始化 OpenClaw workspace 默认模板文件。

    默认模板和 Clawcross skill 只在文件不存在时创建；
    Clawcross 引导块会以可重复更新的方式注入到核心 workspace 文件。
    """
    if workspace_path is None:
        workspace_path = detect_workspace_path()

    if not workspace_path:
        workspace_path = DEFAULT_WORKSPACE_PATH

    # 确保目录存在
    os.makedirs(workspace_path, exist_ok=True)

    created = []
    skipped = []
    updated = []

    for filename, content in _WORKSPACE_TEMPLATES.items():
        filepath = os.path.join(workspace_path, filename)
        if os.path.exists(filepath):
            skipped.append(filename)
        else:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            created.append(filename)

    for relpath, content in _clawcross_skill_templates().items():
        filepath = os.path.join(workspace_path, relpath)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        if os.path.exists(filepath):
            skipped.append(relpath)
        else:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            created.append(relpath)

    # 暂时禁用 Clawcross 管理块注入（BOOTSTRAP.md / AGENTS.md / TOOLS.md）
    # 如需恢复，取消注释以下逻辑。
    # for filename, block in _clawcross_workspace_blocks().items():
    #     filepath = os.path.join(workspace_path, filename)
    #     os.makedirs(os.path.dirname(filepath), exist_ok=True)
    #     if _upsert_managed_block(filepath, block):
    #         updated.append(filename)

    # 输出结果
    print(f"\n🏠 Workspace 模板初始化: {workspace_path}")
    if created:
        print(f"   ✅ 新建 {len(created)} 个文件: {', '.join(created)}")
    if updated:
        print(f"   ♻️  更新 {len(updated)} 个文件: {', '.join(updated)}")
    if skipped:
        print(f"   ⏭️  跳过 {len(skipped)} 个已存在文件: {', '.join(skipped)}")
    if not created and not skipped and not updated:
        print("   ℹ️ 无需初始化")

    return workspace_path


def show_status():
    """显示 OpenClaw 检测状态"""
    oc_bin = detect_openclaw_bin()
    print("=== OpenClaw 集成状态 ===")
    print()

    if not oc_bin:
        print("❌ OpenClaw: 未安装")
        print("")
        print_install_guide()
        return

    rc, out, _ = run_cmd([oc_bin, "--version"])
    version = out.splitlines()[0] if out and rc == 0 else "unknown"
    print(f"✅ OpenClaw: {version}")
    print(f"   路径: {oc_bin}")
    if os.path.isfile(OPENCLAW_CONFIG_PATH):
        print(f"   配置: {OPENCLAW_CONFIG_PATH}")

    port = detect_gateway_port()
    if port:
        print(f"✅ Gateway 端口: {port}")
    else:
        print("⚠️ Gateway 端口: 未检测到（gateway 可能未运行）")

    if is_chat_completions_enabled():
        print("✅ HTTP Chat Completions: 已开启")
    else:
        print("⚠️ HTTP Chat Completions: 未开启")

    auth_mode = detect_gateway_auth_mode()
    if auth_mode:
        if auth_mode == "none":
            print("✅ Gateway Auth: none（loopback 下控制台无需 token）")
        else:
            print(f"✅ Gateway Auth: {auth_mode}")
    else:
        print("ℹ️ Gateway Auth: 未知")

    token = detect_gateway_token()
    if auth_mode == "none":
        if token:
            print(f"ℹ️ Gateway Token: {mask_secret(token)}（当前 auth=none，可选）")
        else:
            print("ℹ️ Gateway Token: 当前 auth=none，不需要")
    elif token:
        print(f"✅ Gateway Token: {mask_secret(token)}")
    else:
        print("ℹ️ Gateway Token: 未配置（CLI 模式不需要）")

    sessions = detect_sessions_file()
    if sessions:
        label = "✅ Sessions 文件" if os.path.isfile(sessions) else "ℹ️ Sessions 文件（预期路径）"
        print(f"{label}: {sessions}")
    else:
        print("⚠️ Sessions 文件: 未找到")

    linger = detect_linger_status()
    if linger == "yes":
        print("✅ Systemd linger: 已开启")
    elif linger == "no":
        print("⚠️ Systemd linger: 未开启")
    else:
        print("ℹ️ Systemd linger: 未知")

    if sessions and os.path.isfile(sessions):
        health = inspect_sessions_health(sessions)
        if isinstance(health, dict):
            missing = int(health.get("missing", 0) or 0)
            if missing > 0:
                print(f"⚠️ Sessions 索引: {missing} 条缺失 transcript，可用 --repair-health 清理")
            else:
                print("✅ Sessions 索引: 正常")
        else:
            print("ℹ️ Sessions 索引: 未知")

    runtime = detect_gateway_runtime()
    if runtime == "running":
        print("✅ Gateway 状态: 运行中")
    elif runtime == "stopped":
        print("⚠️ Gateway 状态: 未运行")
    else:
        print("ℹ️ Gateway 状态: 未知")

    # 检查 .env 中的配置
    _, kvs = read_env()
    print()
    print("--- Clawcross .env 中的 OpenClaw 配置 ---")
    for key in ["OPENCLAW_API_URL", "OPENCLAW_GATEWAY_TOKEN", "OPENCLAW_SESSIONS_FILE"]:
        val = kvs.get(key, "（未配置）")
        if key == "OPENCLAW_GATEWAY_TOKEN" and val and val != "（未配置）":
            val = val[:4] + "****" + val[-4:] if len(val) > 8 else "****"
        print(f"  {key} = {val}")


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "--auto-detect":
        auto_detect_and_configure()
    elif cmd == "--sync-clawcross-llm":
        try:
            print("🔄 将 Clawcross 当前 LLM 配置同步到 OpenClaw...")
            result = sync_clawcross_llm_to_openclaw()
            print(f"   Provider: {result.get('provider')}")
            print(f"   Model: {result.get('model_ref')}")
            print(f"   Config: {result.get('config_path')}")
            if result.get("gateway_running"):
                if result.get("gateway_restarted"):
                    print("   ✅ OpenClaw gateway 已自动重载")
                elif result.get("restart_error"):
                    print(f"   ⚠️ 已写入配置，但 gateway 重载失败: {result['restart_error']}")
            print("✅ Clawcross → OpenClaw LLM 同步完成")
        except Exception as e:
            print(f"❌ 同步失败: {e}", file=sys.stderr)
            sys.exit(1)
    elif cmd == "--import-clawcross-llm-from-openclaw":
        # UI / operator automation: read OpenClaw LLM config and write into Clawcross config/.env
        try:
            print("🔄 从 OpenClaw 导入 LLM 配置到 Clawcross（写入 config/.env）...")
            synced = sync_llm_config_from_openclaw()
            print(f"✅ 导入完成：已同步 {synced} 项配置")
        except Exception as e:
            print(f"❌ 导入失败: {e}", file=sys.stderr)
            sys.exit(1)
    elif cmd == "--status":
        show_status()
    elif cmd == "--install-guide":
        print_install_guide()
    elif cmd == "--repair-health":
        sessions_file = detect_sessions_file()
        print_health_status(sessions_file=sessions_file, repair=True)
    elif cmd == "--init-workspace":
        ws = sys.argv[2] if len(sys.argv) > 2 else None
        init_workspace_templates(ws)
    else:
        print(f"未知参数: {cmd}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
