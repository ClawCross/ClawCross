from flask import Flask, render_template, request, jsonify, session, Response, redirect, stream_with_context, send_file
from werkzeug.middleware.proxy_fix import ProxyFix
import hashlib
import base64
import requests
import os
import json
import re
import subprocess
import uuid
import shutil
import tempfile
import zipfile
import html
import mimetypes
import socket
import threading
import sys as _sys
from pathlib import Path
from io import BytesIO

from agents.platforms import acpx_agent_command_names
from typing import Any
from urllib.parse import quote, urljoin, urlparse
from dotenv import load_dotenv
from utils.env_settings import read_env_all, write_env_settings
from utils.runtime_paths import DATA_DIR, ENV_FILE, LOGS_DIR, PID_DIR, USER_FILES_DIR, USERS_FILE, WORKSPACE_DIR, set_subprocess_env, venv_python
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from utils.internal_alarm_utils import export_team_alarms, restore_team_alarms, team_alarm_targets
from services.llm_factory import create_chat_model, extract_text, infer_provider
from routes.front_group_routes import register_group_routes
from routes.front_agent_routes import PUBLIC_AGENT_ENDPOINTS, register_agent_routes
from routes.front_oasis_routes import register_oasis_routes
from routes.front_session_routes import register_session_routes
from routes.front_webot_routes import register_webot_routes
from services.tinyfish_monitor_service import (
    DEFAULT_BASE_URL as TINYFISH_DEFAULT_BASE_URL,
    DEFAULT_DB_PATH as TINYFISH_DEFAULT_DB_PATH,
    DEFAULT_TARGETS_PATH as TINYFISH_DEFAULT_TARGETS_PATH,
    get_latest_site_snapshots,
    get_monitor_overview,
    poll_pending_runs_once,
    probe_api_access,
    stream_live_run,
    submit_monitor_run,
)
from services.team_creator_service import (
    build_from_roles,
    build_attachment_content_disposition,
    build_team_zip,
    build_team_creator_download_name,
    create_job,
    distill_colleague_skill_artifacts,
    get_job,
    import_colleague_skill,
    import_mentor_skill,
    import_personal_skill,
    list_jobs,
    map_roles_to_team,
    parse_extracted_roles,
    serialize_extracted_roles,
    smart_select_roles,
    stream_discovery,
    stream_extraction,
    translate_texts_via_llm,
    update_job,
    PRESET_POOL,
)
from services.team_preset_assets import install_team_preset, list_team_presets
from services.team_snapshot_skills import (
    SNAPSHOT_OPENCLAW_AGENTS_DIR,
    SNAPSHOT_OPENCLAW_MANAGED_DIR,
    add_team_skills_to_zip,
    add_user_skills_to_zip,
    restore_skills_from_team_dir,
)

# 加载 .env 配置
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
load_dotenv(dotenv_path=str(ENV_FILE))

# 本机服务互调不能走桌面代理：no_proxy 里常见的 "127.*" 写法 HTTP 客户端并不匹配，
# loopback 请求会被送进代理并拿到 502。详见 utils/local_no_proxy.py。
from utils.local_no_proxy import ensure_localhost_no_proxy

ensure_localhost_no_proxy()

runtime_working_dir = str(WORKSPACE_DIR)
os.makedirs(runtime_working_dir, exist_ok=True)
WORKFLOW_PYTHON = str(venv_python())
if not os.path.isfile(WORKFLOW_PYTHON):
    WORKFLOW_PYTHON = _sys.executable
WORKFLOW_IMPORT_PATHS = os.pathsep.join([root_dir, os.path.join(root_dir, "src")])
ACPX_WORKING_DIR = os.path.join(runtime_working_dir, "acpx")
os.makedirs(ACPX_WORKING_DIR, exist_ok=True)

app = Flask(__name__,
            template_folder=os.path.join(root_dir, 'frontend', 'templates'),
            static_folder=os.path.join(root_dir, 'frontend'),
            static_url_path='/static')

# 信任反向代理的 X-Forwarded-Proto / X-Forwarded-For 等头
# 这样 Cloudflare Tunnel 转发的 HTTPS 请求会被正确识别为 HTTPS，
# Flask 才会在 HTTP 内部连接上正确读取 Secure cookie
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# 基于 INTERNAL_TOKEN 生成稳定的 secret_key，避免每次重启时所有 session 失效
_token = os.getenv("INTERNAL_TOKEN", "")
app.secret_key = hashlib.sha256(f"clawcross-session-{_token}".encode()).digest() if _token else os.urandom(24)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB for image uploads

_weclaw_login_proc: subprocess.Popen | None = None
_weclaw_login_lock = threading.Lock()


def _weclaw_settings() -> tuple[str, str, str]:
    settings = read_env_all(str(ENV_FILE))
    weclaw_bin = settings.get("WECLAW_BIN") or os.getenv("WECLAW_BIN") or "weclaw"
    resolved_bin = shutil.which(weclaw_bin) or weclaw_bin
    config_path = os.path.expanduser(
        settings.get("WECLAW_CONFIG")
        or os.getenv("WECLAW_CONFIG")
        or "~/.weclaw/config.json"
    )
    accounts_dir = os.path.join(os.path.dirname(config_path), "accounts")
    return resolved_bin, config_path, accounts_dir


def _check_weclaw_bin(resolved_bin: str) -> str | None:
    if shutil.which(resolved_bin):
        return None
    if os.path.isfile(resolved_bin) and os.access(resolved_bin, os.X_OK):
        return None
    return f"找不到 weclaw 二进制: {resolved_bin}"


def _weclaw_account_files(accounts_dir: str) -> list[str]:
    if not os.path.isdir(accounts_dir):
        return []
    return sorted(
        str(p)
        for p in Path(accounts_dir).glob("*.json")
        if not p.name.endswith(".sync.json")
    )


def _is_tcp_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _weclaw_session_expired_from_log() -> bool:
    log_path = LOGS_DIR / "launcher.log"
    try:
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 200_000), os.SEEK_SET)
            text = f.read().decode("utf-8", errors="ignore")
        return "WeChat session expired and cannot be auto-recovered" in text
    except Exception:
        return False


def _weclaw_session_expired(accounts: list[str], status_output: str) -> bool:
    """Treat stale launcher.log warnings as non-authoritative when account files still exist."""
    if accounts:
        return False
    text = str(status_output or "").lower()
    if "session expired" in text or "cannot be auto-recovered" in text:
        return True
    return _weclaw_session_expired_from_log()


def _stop_managed_weclaw_proxy(settings: dict[str, str]) -> None:
    proxy_host = settings.get("WECLAW_PROXY_HOST") or os.getenv("WECLAW_PROXY_HOST") or "127.0.0.1"
    proxy_port = int(settings.get("WECLAW_PROXY_PORT") or os.getenv("WECLAW_PROXY_PORT") or "51298")
    if not _is_tcp_port_open(proxy_host, proxy_port):
        return
    try:
        requests.post(
            f"http://{proxy_host}:{proxy_port}/_weclaw/stop",
            json={},
            timeout=2,
        )
    except Exception:
        pass

# --- 配置区 ---
from datetime import datetime, timedelta
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
app.config['SESSION_COOKIE_HTTPONLY'] = True      # 防止 XSS 读取 Session Cookie
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'     # 防止 CSRF 跨站请求携带 Cookie
# 不硬编码 SESSION_COOKIE_SECURE：因为用户可能同时通过 HTTPS tunnel 和 HTTP localhost 访问。
# ProxyFix 已让 request.is_secure 能正确识别反向代理转发的 HTTPS。
# SameSite=Lax 对两种场景都已足够安全。

PORT_AGENT = int(os.getenv("PORT_AGENT", "51200"))
# [已弃用] 旧端点 URL，已被 /v1/chat/completions 替代
# LOCAL_AGENT_URL = f"http://127.0.0.1:{PORT_AGENT}/ask"
# LOCAL_AGENT_STREAM_URL = f"http://127.0.0.1:{PORT_AGENT}/ask_stream"
LOCAL_AGENT_CANCEL_URL = f"http://127.0.0.1:{PORT_AGENT}/cancel"
LOCAL_LOGIN_URL = f"http://127.0.0.1:{PORT_AGENT}/login"
LOCAL_TOOLS_URL = f"http://127.0.0.1:{PORT_AGENT}/tools"
LOCAL_UPDATE_CHECK_URL = f"http://127.0.0.1:{PORT_AGENT}/update_check"
LOCAL_UPDATE_START_URL = f"http://127.0.0.1:{PORT_AGENT}/update_start"
LOCAL_UPDATE_STATUS_URL = f"http://127.0.0.1:{PORT_AGENT}/update_status"
LOCAL_SESSIONS_URL = f"http://127.0.0.1:{PORT_AGENT}/sessions"
LOCAL_SESSION_HISTORY_URL = f"http://127.0.0.1:{PORT_AGENT}/session_history"
LOCAL_DELETE_SESSION_URL = f"http://127.0.0.1:{PORT_AGENT}/delete_session"
LOCAL_TTS_URL = f"http://127.0.0.1:{PORT_AGENT}/tts"
LOCAL_SESSION_STATUS_URL = f"http://127.0.0.1:{PORT_AGENT}/session_status"
PORT_SCHEDULER = int(os.getenv("PORT_SCHEDULER", "51201"))
SCHEDULER_TASKS_URL = f"http://127.0.0.1:{PORT_SCHEDULER}/tasks"
# OpenAI 兼容端点
LOCAL_OPENAI_COMPLETIONS_URL = f"http://127.0.0.1:{PORT_AGENT}/v1/chat/completions"
INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "")

# OASIS Forum proxy
PORT_OASIS = int(os.getenv("PORT_OASIS", "51202"))
OASIS_BASE_URL = f"http://127.0.0.1:{PORT_OASIS}"


# ============================================================================
# Token Login Support - Magic Link Authentication
# Using INTERNAL_TOKEN + user_id + timestamp with HMAC signature
# ============================================================================
import time
import secrets
import hmac
import hashlib
from utils.logging_utils import get_logger
from integrations.openclaw_restore_naming import (
    openclaw_entries_ordered,
    restore_agent_id,
    restore_display_name,
    restore_external_global_name,
)

_logger_oc_restore = get_logger("clawcross.openclaw_restore")
_logger_history = get_logger("clawcross.external_history")

def generate_login_token(user_id: str, valid_hours: int = 24) -> str:
    """Generate HMAC-signed login token.
    Token format: base64(user_id:expire_ts:random:signature)
    Signature = HMAC(INTERNAL_TOKEN, user_id:expire_ts:random)
    
    Args:
        user_id: The user ID to generate token for
        valid_hours: Token validity period in hours (default: 24)
    
    Returns:
        URL-safe token string with HMAC signature
    """
    expire_ts = int(time.time()) + valid_hours * 3600
    random_str = secrets.token_urlsafe(8)
    payload = f"{user_id}:{expire_ts}:{random_str}"
    # Generate HMAC signature using INTERNAL_TOKEN as key
    signature = hmac.new(
        INTERNAL_TOKEN.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    token = base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode().rstrip('=')
    return token


def verify_login_token(token: str) -> str | None:
    """Verify HMAC-signed login token.
    
    Args:
        token: The signed token to verify
    
    Returns:
        user_id if signature is valid and not expired, None otherwise
    """
    try:
        # Add padding back for base64 decoding
        padded = token + '=' * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode()
        parts = decoded.rsplit(':', 1)  # Split from right to separate signature
        if len(parts) != 2:
            return None
        payload, signature = parts
        user_id, expire_ts, random_str = payload.split(':')
        expire_ts = int(expire_ts)
        
        # Check expiration
        if time.time() > expire_ts:
            return None
        
        # Verify HMAC signature
        expected = hmac.new(
            INTERNAL_TOKEN.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()[:16]
        
        if not hmac.compare_digest(signature, expected):
            return None
        
        return user_id
    except Exception:
        return None


def login_token_expire_ts(token: str) -> int | None:
    """Extract the expiry timestamp from a generated login token."""
    try:
        padded = token + '=' * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode()
        payload, _signature = decoded.rsplit(':', 1)
        _user_id, expire_ts, _random_str = payload.split(':')
        return int(expire_ts)
    except Exception:
        return None


register_group_routes(app, port_agent=PORT_AGENT, internal_token=INTERNAL_TOKEN)
register_agent_routes(
    app,
    port_agent=PORT_AGENT,
    internal_token=INTERNAL_TOKEN,
)
register_oasis_routes(app, oasis_base_url=OASIS_BASE_URL)
register_session_routes(
    app,
    port_agent=PORT_AGENT,
    internal_token=INTERNAL_TOKEN,
    local_sessions_url=LOCAL_SESSIONS_URL,
    local_session_history_url=LOCAL_SESSION_HISTORY_URL,
    local_session_status_url=LOCAL_SESSION_STATUS_URL,
    local_delete_session_url=LOCAL_DELETE_SESSION_URL,
)
register_webot_routes(
    app,
    port_agent=PORT_AGENT,
    internal_token=INTERNAL_TOKEN,
)

# --- users.json 检查（密码登录时验证用户是否存在）---
USERS_PATH = str(USERS_FILE)

def _load_users_json() -> dict[str, str]:
    if not os.path.exists(USERS_PATH):
        return {}
    try:
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _write_users_json(users: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=4)

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def _user_exists_in_users_json(username: str) -> bool:
    """检查用户名是否在 users.json 中（有密码记录）"""
    return username in _load_users_json()


# --- Unified auth: before_request hook ---
# Routes that do NOT require login
_PUBLIC_ROUTES = frozenset({
    'index', 'manifest', 'service_worker', 'static',
    'proxy_openai_completions', 'proxy_openai_models',
    'proxy_login', 'proxy_logout', 'proxy_check_session',
    'proxy_login_with_token', 'magic_login',
    'group_chat_mobile', 'group_chat_mobile_alias', 'studio',
    'llm_config_status', 'setup_status', 'import_openclaw_config',
}) | PUBLIC_AGENT_ENDPOINTS


def _is_direct_local_request():
    """判断是否为本机直连（非经过任何反向代理）。

    只有同时满足两个条件才算本地直连：
    1. remote_addr 是 127.0.0.1 / ::1
    2. 没有任何常见反向代理注入的头（说明不是被代理转发过来的）

    兼容：Cloudflare Tunnel、Nginx、Caddy、Traefik、HAProxy、Apache 等。
    """
    remote = request.remote_addr or ''
    if remote not in ('127.0.0.1', '::1'):
        return False
    # 任何反向代理都会注入至少一个这类头
    _PROXY_HEADERS = (
        'X-Forwarded-For',      # Nginx / Caddy / Traefik / HAProxy / 通用
        'X-Forwarded-Proto',    # Nginx / Caddy / 通用
        'X-Forwarded-Host',     # Nginx / Traefik
        'X-Real-Ip',            # Nginx
        'Cf-Connecting-Ip',     # Cloudflare Tunnel
        'Cf-Ray',               # Cloudflare Tunnel
        'True-Client-Ip',       # Cloudflare / Akamai
        'Forwarded',            # RFC 7239 标准头
        'Via',                  # HTTP 标准代理头
    )
    return not any(request.headers.get(h) for h in _PROXY_HEADERS)


@app.before_request
def _unified_auth_check():
    """鉴权入口，规则极简：

    1. 公开路由 → 放行
    2. 本机直连（127.0.0.1 且无代理头）→ 放行
       - 如果请求携带 X-User-Id header，自动注入 session（CLI 场景）
    3. 其余一律要登录（包括所有反向代理转发的请求）
    """
    if request.endpoint in _PUBLIC_ROUTES:
        return None
    if _is_direct_local_request():
        # CLI / 内部调用：如果带了 X-User-Id，注入到 Flask session
        header_uid = request.headers.get("X-User-Id", "").strip()
        if header_uid and not session.get("user_id"):
            session["user_id"] = header_uid
        return None
    if not session.get('user_id'):
        return jsonify({'error': '未登录'}), 401
    return None


def _internal_auth_headers():
    """Build headers for Flask → backend internal communication.
    Uses INTERNAL_TOKEN instead of forwarding user password.
    """
    return {"X-Internal-Token": INTERNAL_TOKEN}


def _agent_api_headers(user_id: str) -> dict:
    """Authorization for the Agent service's /v1 APIs, acting as *user_id*."""
    return {"Authorization": f"Bearer {INTERNAL_TOKEN}:{user_id}"}


def _internal_auth_params(extra: dict | None = None):
    """Build common params (user_id) + merge extra params."""
    params = {"user_id": session.get("user_id", "")}
    if extra:
        params.update(extra)
    return params


@app.route("/")
def index():
    """主页 - 默认跳转移动端群聊页面。支持通过 URL 参数携带 Token 自动登录。"""
    token = request.args.get('token', '')
    user_id = request.args.get('user', '')
    
    # 如果 URL 中包含有效的 Token，自动创建 session
    if token and user_id:
        verified_user = verify_login_token(token)
        if verified_user == user_id:
            session['user_id'] = user_id
            session.permanent = True
            # 重定向到移动端群聊页面（去除 URL 中的 token）
            return redirect('/mobile_group_chat')
    
    # 如果带有 redirect=group_chat 参数，转发到 studio 进行登录后再跳回
    redirect_param = request.args.get('redirect', '')
    if redirect_param:
        return redirect(f'/studio?redirect={redirect_param}')
    
    return redirect('/mobile_group_chat')


@app.route("/api/llm_config_status")
def llm_config_status():
    """检查 LLM API 是否已配置，供前端判断是否显示提示横幅。"""
    llm_config = _read_saved_clawcross_llm_config()
    configured = _llm_config_complete(llm_config)
    return jsonify({"configured": configured})


@app.route("/api/setup_status")
def setup_status():
    """首次登录向导状态检测：返回 LLM、OpenClaw、Antigravity、密码等配置状态。"""
    import shutil
    llm_config = _read_saved_clawcross_llm_config()
    api_key = llm_config["api_key"]
    base_url = llm_config["base_url"]
    model = llm_config["model"]
    provider = llm_config["provider"]
    llm_configured = _llm_config_complete(llm_config)

    # Check OpenClaw
    openclaw_installed = shutil.which("openclaw") is not None

    # Check Antigravity (probe port 8045)
    antigravity_running = False
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:8045/v1/models")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                antigravity_running = True
    except Exception:
        pass

    # Check if any password users exist
    users_json_path = str(USERS_FILE)
    password_set = False
    if os.path.isfile(users_json_path):
        try:
            import json as _json
            with open(users_json_path, "r", encoding="utf-8") as f:
                users_data = _json.load(f)
            if isinstance(users_data, dict) and len(users_data) > 0:
                password_set = True
        except Exception:
            pass

    return jsonify({
        "llm_configured": llm_configured,
        "openclaw_installed": openclaw_installed,
        "antigravity_running": antigravity_running,
        "password_set": password_set,
        "current_provider": provider,
        "current_model": model,
        "current_base_url": base_url,
    })


@app.route("/api/import_openclaw_config")
def import_openclaw_config():
    """从本地 OpenClaw 读取 LLM 配置（API Key / Base URL / Model / Provider），
    返回给前端 wizard 用于一键导入。不直接写入 .env。"""
    import subprocess, shutil, sys

    oc_bin = shutil.which("openclaw")
    if not oc_bin:
        return jsonify({"error": "OpenClaw 未安装", "found": False}), 404

    # 复用 configure_openclaw.py 的探测逻辑
    script_dir = os.path.join(root_dir, "selfskill", "scripts")
    sys_path_backup = list(sys.path)
    try:
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        from configure_openclaw import detect_llm_config_from_openclaw
        detected = detect_llm_config_from_openclaw()
    except Exception as e:
        return jsonify({"error": f"读取 OpenClaw 配置失败: {e}", "found": True}), 500
    finally:
        sys.path[:] = sys_path_backup

    if not detected:
        return jsonify({
            "error": "OpenClaw 已安装但未检测到 LLM 配置",
            "found": True,
        }), 404

    return jsonify({
        "found": True,
        "api_key": detected.get("LLM_API_KEY", ""),
        "base_url": detected.get("LLM_BASE_URL", ""),
        "model": detected.get("LLM_MODEL", ""),
        "provider": detected.get("LLM_PROVIDER", ""),
    })


def _read_saved_clawcross_llm_config():
    settings = read_env_all(str(ENV_FILE))
    return {
        "api_key": (settings.get("LLM_API_KEY") or "").strip(),
        "base_url": (settings.get("LLM_BASE_URL") or "").strip(),
        "model": (settings.get("LLM_MODEL") or "").strip(),
        "provider": (settings.get("LLM_PROVIDER") or "").strip(),
    }


def _provider_is_local_keyless(provider: str, base_url: str) -> bool:
    normalized_provider = (provider or "").strip().lower()
    normalized_base_url = (base_url or "").strip().lower()
    if normalized_provider == "ollama":
        return True
    return "127.0.0.1:11434" in normalized_base_url or "localhost:11434" in normalized_base_url


def _provider_allows_default_base_url(provider: str) -> bool:
    """Whether the runtime can call the provider without an explicit LLM_BASE_URL."""
    normalized_provider = (provider or "").strip().lower()
    return normalized_provider in {
        "google",
        "anthropic",
        "ollama",
        "openrouter",
        "groq",
        "xai",
        "mistral",
        "perplexity",
        "together",
        "fireworks",
        "deepinfra",
        "cerebras",
        "cohere",
        "nvidia-nim",
        "novita",
        "vercel-gateway",
    }


def _default_base_url_for_provider(provider: str) -> str:
    normalized_provider = (provider or "").strip().lower()
    defaults = {
        "google": "https://generativelanguage.googleapis.com/v1beta",
        "anthropic": "https://api.anthropic.com",
        "ollama": "http://127.0.0.1:11434",
        "openrouter": "https://openrouter.ai/api/v1",
        "groq": "https://api.groq.com/openai/v1",
        "xai": "https://api.x.ai/v1",
        "mistral": "https://api.mistral.ai/v1",
        "perplexity": "https://api.perplexity.ai",
        "together": "https://api.together.xyz/v1",
        "fireworks": "https://api.fireworks.ai/inference/v1",
        "deepinfra": "https://api.deepinfra.com/v1/openai",
        "cerebras": "https://api.cerebras.ai/v1",
        "cohere": "https://api.cohere.ai/compatibility/v1",
        "nvidia-nim": "https://integrate.api.nvidia.com/v1",
        "novita": "https://api.novita.ai/v3/openai",
        "vercel-gateway": "https://ai-gateway.vercel.sh/v1",
    }
    return defaults.get(normalized_provider, "")


def _llm_config_complete(config: dict[str, str]) -> bool:
    api_key = (config.get("api_key") or "").strip()
    base_url = (config.get("base_url") or "").strip()
    model = (config.get("model") or "").strip()
    provider = (
        (config.get("provider") or "").strip()
        or infer_provider(
            model=model,
            base_url=base_url,
            provider="",
            api_key=api_key,
        )
    )
    if not model:
        return False
    if not base_url and not _provider_allows_default_base_url(provider):
        return False
    if _provider_is_local_keyless(provider, base_url):
        return True
    return bool(api_key) and api_key != "your_api_key_here"


def _read_saved_openclaw_runtime_config():
    settings = read_env_all(str(ENV_FILE))
    gateway_token = (settings.get("OPENCLAW_GATEWAY_TOKEN") or os.getenv("OPENCLAW_GATEWAY_TOKEN") or "").strip()
    api_key = (settings.get("OPENCLAW_API_KEY") or os.getenv("OPENCLAW_API_KEY") or "").strip()
    return {
        "api_url": (settings.get("OPENCLAW_API_URL") or os.getenv("OPENCLAW_API_URL") or "").strip(),
        "api_key": gateway_token or api_key,
    }


def _normalize_openclaw_chat_url(api_url: str) -> str:
    """Point OPENCLAW_API_URL at /v1/chat/completions when only the gateway root was set."""
    u = (api_url or "").strip().rstrip("/")
    if not u:
        return ""
    path = (urlparse(u).path or "").lower()
    if "chat/completions" in path:
        return u
    return urljoin(u + "/", "v1/chat/completions").rstrip("/")


def _resolve_clawcross_llm_config(data: dict | None):
    payload = data or {}
    saved = _read_saved_clawcross_llm_config()

    def pick(field: str):
        value = str(payload.get(field) or "").strip()
        if value and "****" not in value:
            return value
        return saved.get(field, "")

    resolved = {
        "api_key": pick("api_key"),
        "base_url": pick("base_url"),
        "model": pick("model"),
        "provider": pick("provider"),
    }
    if "api_key" in payload and not str(payload.get("api_key") or "").strip():
        provider_hint = str(payload.get("provider") or resolved["provider"] or "").strip()
        base_url_hint = str(payload.get("base_url") or resolved["base_url"] or "").strip()
        if _provider_is_local_keyless(provider_hint, base_url_hint):
            resolved["api_key"] = ""
    if not resolved["provider"]:
        resolved["provider"] = infer_provider(
            model=resolved["model"],
            base_url=resolved["base_url"],
            provider="",
            api_key=resolved["api_key"],
        )
    return resolved


@app.route("/api/export_openclaw_config", methods=["POST"])
def export_openclaw_config():
    """将当前 Clawcross LLM 设置写回 OpenClaw 默认 provider/model。"""
    import shutil
    import sys

    oc_bin = shutil.which("openclaw")
    if not oc_bin:
        return jsonify({"ok": False, "error": "OpenClaw 未安装"}), 404

    resolved = _resolve_clawcross_llm_config(request.get_json(force=True) or {})
    api_key = resolved["api_key"]
    base_url = resolved["base_url"] or _default_base_url_for_provider(resolved["provider"])
    model = resolved["model"]
    provider = resolved["provider"]

    if not base_url or not model:
        return jsonify({
            "ok": False,
            "error": "base_url and model are required",
        }), 400
    if not api_key and not _provider_is_local_keyless(provider, base_url):
        return jsonify({
            "ok": False,
            "error": "api_key, base_url and model are required",
        }), 400

    script_dir = os.path.join(root_dir, "selfskill", "scripts")
    sys_path_backup = list(sys.path)
    try:
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        from configure_openclaw import export_llm_config_to_openclaw
        result = export_llm_config_to_openclaw(
            api_key=api_key,
            base_url=base_url,
            model=model,
            provider=provider,
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"写入 OpenClaw 配置失败: {e}"}), 500
    finally:
        sys.path[:] = sys_path_backup

    return jsonify(result)


@app.route("/api/discover_models", methods=["POST"])
def discover_models():
    """代理调用 /v1/models 端点，返回可用模型列表。
    前端 setup wizard 用此端点检测模型。
    """
    resolved = _resolve_clawcross_llm_config(request.get_json(force=True) or {})
    api_key = resolved["api_key"]
    base_url = resolved["base_url"] or _default_base_url_for_provider(resolved["provider"])
    provider = resolved["provider"]

    if not base_url:
        return jsonify({"error": "base_url required"}), 400
    if not api_key and not _provider_is_local_keyless(provider, base_url):
        return jsonify({"error": "api_key required"}), 400

    try:
        import urllib.request
        import urllib.error
        import json as _json

        if (provider or "").strip().lower() == "google":
            models_url = base_url.rstrip("/") + "/models?key=" + quote(api_key)
            req = urllib.request.Request(models_url, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = _json.loads(resp.read().decode())
            models_data = body.get("models", [])
            model_ids = []
            for item in models_data:
                name = (item.get("name") or "").strip()
                mid = name.removeprefix("models/")
                methods = item.get("supportedGenerationMethods") or []
                if mid and (not methods or "generateContent" in methods):
                    model_ids.append(mid)
        else:
            # Build OpenAI-compatible /v1/models URL.
            models_url = base_url.rstrip("/")
            if not models_url.endswith("/v1"):
                models_url += "/v1"
            models_url += "/models"

            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            req = urllib.request.Request(models_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = _json.loads(resp.read().decode())

            models_data = body.get("data", [])
            model_ids = []
            for m in models_data:
                mid = m.get("id", "")
                if mid and not mid.startswith("ft:") and not mid.startswith("dall-e"):
                    model_ids.append(mid)
        model_ids.sort()

        return jsonify({"models": model_ids})
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode()[:300]
        except Exception:
            pass
        return jsonify({"error": f"API error {e.code}", "detail": err_body}), e.code
    except urllib.error.URLError as e:
        return jsonify({"error": f"Cannot connect: {e.reason}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _read_saved_tinyfish_settings() -> dict[str, str]:
    settings = read_env_all(str(ENV_FILE))
    return {
        "TINYFISH_API_KEY": (settings.get("TINYFISH_API_KEY") or "").strip(),
        "TINYFISH_BASE_URL": (settings.get("TINYFISH_BASE_URL") or "").strip(),
        "TINYFISH_MONITOR_DB_PATH": (settings.get("TINYFISH_MONITOR_DB_PATH") or "").strip(),
        "TINYFISH_MONITOR_TARGETS_PATH": (settings.get("TINYFISH_MONITOR_TARGETS_PATH") or "").strip(),
        "TINYFISH_MONITOR_ENABLED": (settings.get("TINYFISH_MONITOR_ENABLED") or "").strip(),
        "TINYFISH_MONITOR_CRON": (settings.get("TINYFISH_MONITOR_CRON") or "").strip(),
    }


def _mask_secret_value(value: str) -> str:
    normalized = (value or "").strip()
    if len(normalized) > 8:
        return normalized[:4] + "****" + normalized[-4:]
    return normalized


def _resolve_tinyfish_settings(payload: dict | None) -> dict[str, str]:
    incoming = payload or {}
    saved = _read_saved_tinyfish_settings()

    def pick(key: str, default: str = "") -> str:
        value = str(incoming.get(key) or "").strip()
        if value and "****" not in value:
            return value
        saved_value = saved.get(key, "")
        if saved_value:
            return saved_value
        return default

    enabled_value = str(incoming.get("TINYFISH_MONITOR_ENABLED") or "").strip()
    if not enabled_value:
        enabled_value = saved.get("TINYFISH_MONITOR_ENABLED") or "false"

    cron_value = str(incoming.get("TINYFISH_MONITOR_CRON") or "").strip()
    if not cron_value:
        cron_value = saved.get("TINYFISH_MONITOR_CRON") or ""

    return {
        "TINYFISH_API_KEY": pick("TINYFISH_API_KEY"),
        "TINYFISH_BASE_URL": pick("TINYFISH_BASE_URL", str(TINYFISH_DEFAULT_BASE_URL)),
        "TINYFISH_MONITOR_DB_PATH": pick("TINYFISH_MONITOR_DB_PATH", str(TINYFISH_DEFAULT_DB_PATH)),
        "TINYFISH_MONITOR_TARGETS_PATH": pick("TINYFISH_MONITOR_TARGETS_PATH", str(TINYFISH_DEFAULT_TARGETS_PATH)),
        "TINYFISH_MONITOR_ENABLED": enabled_value,
        "TINYFISH_MONITOR_CRON": cron_value,
    }


@app.route("/api/tinyfish/configure", methods=["POST"])
def tinyfish_configure():
    """Validate TinyFish API access, apply defaults, and persist settings."""
    body = request.get_json(silent=True) or {}
    resolved = _resolve_tinyfish_settings(body.get("settings") or body)
    api_key = resolved["TINYFISH_API_KEY"]
    if not api_key:
        return jsonify({"ok": False, "error": "TINYFISH_API_KEY is required"}), 400

    try:
        probe_api_access(
            api_key=api_key,
            base_url=resolved["TINYFISH_BASE_URL"],
            request_timeout=15,
        )
    except Exception as e:
        message = str(e)
        status = 502 if "Failed to reach TinyFish" in message else 400
        return jsonify({"ok": False, "error": message}), status

    write_env_settings(str(ENV_FILE), resolved)
    load_dotenv(dotenv_path=str(ENV_FILE), override=True)
    for key, value in resolved.items():
        os.environ[key] = value

    return jsonify({
        "ok": True,
        "config": {
            **resolved,
            "TINYFISH_API_KEY_MASKED": _mask_secret_value(api_key),
        },
        "targets_path_exists": os.path.exists(resolved["TINYFISH_MONITOR_TARGETS_PATH"]),
    })


@app.route("/api/tinyfish/status")
def tinyfish_status():
    """Return TinyFish monitor config, recent runs, changes, and latest snapshots."""
    sync = request.args.get("sync", "").strip().lower() in {"1", "true", "yes"}
    if sync:
        try:
            poll_pending_runs_once()
        except Exception:
            # Overview should still be readable even if polling fails.
            pass

    try:
        overview = get_monitor_overview(
            recent_change_limit=int(request.args.get("changes", "20")),
            recent_run_limit=int(request.args.get("runs", "10")),
            latest_site_limit=int(request.args.get("sites", "10")),
            snapshots_per_site=int(request.args.get("snapshots", "20")),
        )
        return jsonify({"ok": True, **overview})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tinyfish/run", methods=["POST"])
def tinyfish_run():
    """Submit a TinyFish monitor run. Defaults to async submission only."""
    body = request.get_json(silent=True) or {}
    raw_sites = body.get("site_keys") or body.get("sites") or []
    selected_sites = {str(item).strip() for item in raw_sites if str(item).strip()} or None
    wait = bool(body.get("wait", False))
    try:
        result = submit_monitor_run(
            selected_sites=selected_sites,
            wait=wait,
            poll_interval=float(body.get("poll_interval", 5.0)),
            max_wait_seconds=int(body.get("max_wait", 900)),
            request_timeout=int(body.get("request_timeout", 60)),
        )
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tinyfish/live-run", methods=["POST"])
def tinyfish_live_run():
    """Proxy TinyFish run-sse and persist the final run into the monitor DB."""
    body = request.get_json(silent=True) or {}
    site_key = str(body.get("site_key") or body.get("site") or "").strip()
    if not site_key:
        return jsonify({"ok": False, "error": "site_key is required"}), 400

    request_timeout = int(body.get("request_timeout", 300))

    def generate():
        try:
            for event in stream_live_run(site_key=site_key, request_timeout=request_timeout):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            payload = {"type": "ERROR", "error": str(exc)}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/tinyfish/sites/<site_key>")
def tinyfish_site_latest(site_key):
    """Return latest stored snapshots for a single competitor site."""
    try:
        data = get_latest_site_snapshots(site_key, snapshots_limit=int(request.args.get("limit", "50")))
        return jsonify({"ok": True, "site": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/creator")
def creator():
    """ClawCross Creator page with TinyFish Live Crawl integration."""
    return render_template("creator.html")


# ──────────────────────────────────────────────────────────────
# ClawCross Creator API — three-stage pipeline
# ──────────────────────────────────────────────────────────────

@app.route("/api/team-creator/discover", methods=["POST"])
def team_creator_discover():
    """Stage 1: Discovery — stream SSE events while TinyFish searches for SOP/org pages."""
    body = request.get_json(silent=True) or {}
    task_description = str(body.get("task_description") or body.get("task") or "").strip()
    search_url = str(body.get("search_url") or "").strip()

    if not task_description:
        return jsonify({"ok": False, "error": "task_description is required"}), 400

    def generate():
        try:
            for event in stream_discovery(task_description, search_url):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            payload = {"type": "ERROR", "error": str(exc)}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/team-creator/extract", methods=["POST"])
def team_creator_extract():
    """Stage 2: Extraction — stream SSE events while TinyFish extracts roles from a page."""
    body = request.get_json(silent=True) or {}
    page_url = str(body.get("url") or body.get("page_url") or "").strip()
    page_title = str(body.get("title") or "").strip()

    if not page_url:
        return jsonify({"ok": False, "error": "url is required"}), 400

    def generate():
        try:
            for event in stream_extraction(page_url, page_title):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            payload = {"type": "ERROR", "error": str(exc)}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/team-creator/build", methods=["POST"])
def team_creator_build():
    """Stage 3: Build — convert roles into Clawcross team config.

    Accepts either:
    - Pre-extracted roles: {"roles": [...], "team_name": "...", "task": "..."}
    - Or extraction results: {"extraction_results": [...], "team_name": "...", "task": "..."}

    Returns the team config JSON (experts + agents + YAML workflow).
    """
    body = request.get_json(silent=True) or {}
    team_name = str(body.get("team_name") or body.get("team") or "").strip()
    task = str(body.get("task_description") or body.get("task") or "").strip()

    if not team_name:
        return jsonify({"ok": False, "error": "team_name is required"}), 400

    roles_data = body.get("roles")
    extraction_results = body.get("extraction_results")
    owner_id = str(session.get("user_id") or "").strip()

    if not ((roles_data and isinstance(roles_data, list)) or (extraction_results and isinstance(extraction_results, list))):
        return jsonify({"ok": False, "error": "Provide 'roles' (array) or 'extraction_results'"}), 400

    def _normalize_role_records(items):
        normalized = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            role_name = str(item.get("role_name") or "").strip()
            if not role_name:
                continue
            normalized.append(
                {
                    "role_name": role_name,
                    "personality_traits": list(item.get("personality_traits") or []),
                    "primary_responsibilities": list(item.get("primary_responsibilities") or []),
                    "depends_on": list(item.get("depends_on") or item.get("input_dependency") or []),
                    "tools_used": list(item.get("tools_used") or []),
                    "source_url": str(item.get("source_url") or "").strip(),
                    "expert_tag": str(item.get("expert_tag") or item.get("_expert_tag") or "").strip(),
                    "output_target": list(item.get("output_target") or []),
                }
            )
        return normalized

    job = create_job(task, team_name, owner_id=owner_id)
    extracted_roles_payload = []

    try:
        update_job(job.job_id, owner_id=owner_id, status="running", error="")
        if roles_data and isinstance(roles_data, list):
            # Direct role input
            normalized_roles = _normalize_role_records(roles_data)
            extracted_roles_payload = normalized_roles
            team_config = build_from_roles(normalized_roles, team_name, task)
        elif extraction_results and isinstance(extraction_results, list):
            # Parse from TinyFish extraction results
            roles = parse_extracted_roles(extraction_results)
            if not roles:
                update_job(job.job_id, owner_id=owner_id, status="failed", error="No roles could be extracted from results")
                return jsonify({"ok": False, "error": "No roles could be extracted from results", "job_id": job.job_id}), 400
            extracted_roles_payload = serialize_extracted_roles(roles)
            team_config = map_roles_to_team(roles, team_name, task)

        saved_job = update_job(
            job.job_id,
            owner_id=owner_id,
            status="complete",
            extracted_roles=extracted_roles_payload,
            team_config=team_config,
            error="",
        )
        return jsonify({"ok": True, "team_config": team_config, "job": saved_job.to_dict() if saved_job else {"job_id": job.job_id}})
    except Exception as e:
        saved_job = update_job(
            job.job_id,
            owner_id=owner_id,
            status="failed",
            extracted_roles=extracted_roles_payload,
            error=str(e),
        )
        return jsonify({"ok": False, "error": str(e), "job_id": job.job_id, "job": saved_job.to_dict() if saved_job else None}), 500


@app.route("/api/team-creator/download", methods=["POST"])
def team_creator_download():
    """Download the built team as a ZIP snapshot (same format as /teams/snapshot/download).

    Accepts the team_config from /api/team-creator/build.
    """
    body = request.get_json(silent=True) or {}
    team_name = str(body.get("team_name") or body.get("team") or "").strip()
    team_config = body.get("team_config")

    if not team_name:
        return jsonify({"ok": False, "error": "team_name is required"}), 400
    if not team_config:
        return jsonify({"ok": False, "error": "team_config is required"}), 400

    try:
        zip_bytes = build_team_zip(team_config, team_name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = build_team_creator_download_name(team_name, timestamp)

        return Response(
            zip_bytes,
            mimetype="application/zip",
            headers={"Content-Disposition": build_attachment_content_disposition(filename)},
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/team-creator/smart-select", methods=["POST"])
def team_creator_smart_select():
    """LLM-powered intelligent role selection + preset expert matching.

    Accepts:
        {"roles": [...], "max_roles": 8, "task_description": "..."}

    Returns:
        {"ok": true, "selected_indices": [0,2,5], "preset_matches": [...], "reasoning": "..."}
    """
    body = request.get_json(silent=True) or {}
    roles = body.get("roles")
    max_roles = int(body.get("max_roles", 8))
    task_desc = str(body.get("task_description") or "").strip()

    if not roles or not isinstance(roles, list):
        return jsonify({"ok": False, "error": "roles (array) is required"}), 400

    try:
        result = smart_select_roles(roles, max_roles, task_desc)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/team-creator/translate", methods=["POST"])
def team_creator_translate():
    """Translate ClawCross Creator dynamic UI text into the requested language."""
    body = request.get_json(silent=True) or {}
    texts = body.get("texts")
    target_lang = str(body.get("target_lang") or "").strip().lower()
    source_lang = str(body.get("source_lang") or "").strip()
    context = str(body.get("context") or "").strip()

    if not isinstance(texts, list):
        return jsonify({"ok": False, "error": "texts (array) is required"}), 400
    if target_lang not in {"zh", "zh-cn", "en"}:
        return jsonify({"ok": False, "error": "target_lang must be zh or en"}), 400

    try:
        translations = translate_texts_via_llm(
            [str(item or "") for item in texts],
            target_lang=target_lang,
            source_lang=source_lang,
            context=context,
        )
        return jsonify({"ok": True, "translations": translations})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/team-creator/presets")
def team_creator_presets():
    """Return available preset expert tags for matching UI.

    Now returns richer data including category, description, name_zh
    to support the new expert pool browser in ClawCross Creator.
    Note: The primary frontend now uses /proxy_visual/experts directly.
    This endpoint remains for backward compatibility and lightweight usage.
    """
    presets = [
        {
            "tag": v["tag"],
            "name": v["name"],
            "name_zh": v.get("name_zh", ""),
            "source": v["source"],
            "category": v.get("category", ""),
            "description": v.get("description", ""),
            "temperature": v.get("temperature", 0.7),
        }
        for v in PRESET_POOL.values()
    ]
    return jsonify({"ok": True, "presets": presets, "count": len(presets)})


def _resolve_team_creator_import_path(raw_path: str) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        raise ValueError("import path is required")
    candidate = Path(os.path.expanduser(value))
    if not candidate.is_absolute():
        candidate = (Path(root_dir) / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def _read_team_creator_import_text(raw_path: str, label: str) -> str:
    path = _resolve_team_creator_import_path(raw_path)
    if not path.is_file():
        raise ValueError(f"{label} not found: {raw_path}")
    return path.read_text(encoding="utf-8", errors="replace")


def _read_team_creator_import_json(raw_path: str, label: str) -> dict:
    try:
        parsed = json.loads(_read_team_creator_import_text(raw_path, label))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {raw_path}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must contain a JSON object: {raw_path}")
    return parsed


def _load_colleague_import_from_paths(
    *,
    colleague_dir_path: str = "",
    meta_path: str = "",
    persona_path: str = "",
    work_path: str = "",
) -> tuple[dict, str, str]:
    base_dir: Path | None = None
    if str(colleague_dir_path or "").strip():
        base_dir = _resolve_team_creator_import_path(colleague_dir_path)
        if not base_dir.is_dir():
            raise ValueError(f"colleague directory not found: {colleague_dir_path}")

    resolved_meta_path = meta_path or (str(base_dir / "meta.json") if base_dir else "")
    resolved_persona_path = persona_path or (str(base_dir / "persona.md") if base_dir else "")
    resolved_work_path = work_path or (str(base_dir / "work.md") if base_dir else "")

    meta_json = _read_team_creator_import_json(resolved_meta_path, "meta.json")
    persona_md = _read_team_creator_import_text(resolved_persona_path, "persona.md")
    work_md = ""
    if str(resolved_work_path or "").strip():
        try:
            work_md = _read_team_creator_import_text(resolved_work_path, "work.md")
        except ValueError:
            if not base_dir:
                raise
    return meta_json, persona_md, work_md


def _load_mentor_import_from_paths(
    *,
    mentor_json_path: str = "",
    skill_md_path: str = "",
) -> tuple[dict, str]:
    mentor_json = _read_team_creator_import_json(mentor_json_path, "mentor_json")
    skill_md = ""
    if str(skill_md_path or "").strip():
        skill_md = _read_team_creator_import_text(skill_md_path, "skill_md")
    return mentor_json, skill_md


@app.route("/api/team-creator/import-colleague", methods=["POST"])
def team_creator_import_colleague():
    """Import a colleague-skill output (meta.json + persona.md + work.md) into ClawCross Creator.

    Accepts JSON body:
      - meta_json: dict (parsed meta.json content)
      - persona_md: string (raw persona.md content)
      - work_md: string (raw work.md content, optional)
      - team_name: string (optional)
      - task_description: string (optional)

    Or multipart form upload with files: meta_json, persona_md, work_md
    """
    try:
        if request.is_json:
            body = request.get_json(silent=True) or {}
            meta_json = body.get("meta_json") or {}
            persona_md = str(body.get("persona_md") or "").strip()
            work_md = str(body.get("work_md") or "").strip()
            colleague_dir_path = str(body.get("colleague_dir_path") or "").strip()
            meta_path = str(body.get("meta_path") or "").strip()
            persona_path = str(body.get("persona_path") or "").strip()
            work_path = str(body.get("work_path") or "").strip()
            team_name = str(body.get("team_name") or "").strip()
            task_description = str(body.get("task_description") or "").strip()
        else:
            # Multipart form upload
            import json as _json
            meta_file = request.files.get("meta_json")
            persona_file = request.files.get("persona_md")
            work_file = request.files.get("work_md")

            if meta_file:
                meta_json = _json.loads(meta_file.read().decode("utf-8"))
            else:
                raw = request.form.get("meta_json", "{}")
                meta_json = _json.loads(raw) if isinstance(raw, str) else raw

            persona_md = persona_file.read().decode("utf-8") if persona_file else request.form.get("persona_md", "")
            work_md = work_file.read().decode("utf-8") if work_file else request.form.get("work_md", "")
            colleague_dir_path = request.form.get("colleague_dir_path", "")
            meta_path = request.form.get("meta_path", "")
            persona_path = request.form.get("persona_path", "")
            work_path = request.form.get("work_path", "")
            team_name = request.form.get("team_name", "")
            task_description = request.form.get("task_description", "")

        if colleague_dir_path or meta_path or persona_path or work_path:
            loaded_meta_json, loaded_persona_md, loaded_work_md = _load_colleague_import_from_paths(
                colleague_dir_path=colleague_dir_path,
                meta_path=meta_path,
                persona_path=persona_path,
                work_path=work_path,
            )
            if not meta_json:
                meta_json = loaded_meta_json
            if not persona_md:
                persona_md = loaded_persona_md
            if not work_md:
                work_md = loaded_work_md

        if not meta_json:
            return jsonify({"ok": False, "error": "meta_json is required"}), 400
        if not persona_md:
            return jsonify({"ok": False, "error": "persona_md is required"}), 400

        team_config = import_colleague_skill(
            meta_json=meta_json,
            persona_md=persona_md,
            work_md=work_md,
            team_name=team_name,
            task_description=task_description,
        )

        return jsonify({
            "ok": True,
            "team_config": team_config,
            "summary": team_config.get("summary"),
            "import_source": "colleague-skill",
        })

    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/team-creator/import-mentor", methods=["POST"])
def team_creator_import_mentor():
    """Import a supervisor/mentor skill output ({name}.json + SKILL.md) into ClawCross Creator.

    Accepts JSON body:
      - mentor_json: dict (parsed {name}.json content)
      - skill_md: string (raw SKILL.md content, optional)
      - team_name: string (optional)
      - task_description: string (optional)

    Or multipart form upload with files: mentor_json, skill_md
    """
    try:
        if request.is_json:
            body = request.get_json(silent=True) or {}
            mentor_json = body.get("mentor_json") or {}
            skill_md = str(body.get("skill_md") or "").strip()
            mentor_json_path = str(body.get("mentor_json_path") or "").strip()
            skill_md_path = str(body.get("skill_md_path") or "").strip()
            team_name = str(body.get("team_name") or "").strip()
            task_description = str(body.get("task_description") or "").strip()
        else:
            import json as _json
            mentor_file = request.files.get("mentor_json")
            skill_file = request.files.get("skill_md")

            if mentor_file:
                mentor_json = _json.loads(mentor_file.read().decode("utf-8"))
            else:
                raw = request.form.get("mentor_json", "{}")
                mentor_json = _json.loads(raw) if isinstance(raw, str) else raw

            skill_md = skill_file.read().decode("utf-8") if skill_file else request.form.get("skill_md", "")
            mentor_json_path = request.form.get("mentor_json_path", "")
            skill_md_path = request.form.get("skill_md_path", "")
            team_name = request.form.get("team_name", "")
            task_description = request.form.get("task_description", "")

        if mentor_json_path or skill_md_path:
            loaded_mentor_json, loaded_skill_md = _load_mentor_import_from_paths(
                mentor_json_path=mentor_json_path,
                skill_md_path=skill_md_path,
            )
            if not mentor_json:
                mentor_json = loaded_mentor_json
            if not skill_md:
                skill_md = loaded_skill_md

        if not mentor_json:
            return jsonify({"ok": False, "error": "mentor_json is required"}), 400

        team_config = import_mentor_skill(
            mentor_json=mentor_json,
            skill_md=skill_md,
            team_name=team_name,
            task_description=task_description,
        )

        return jsonify({
            "ok": True,
            "team_config": team_config,
            "summary": team_config.get("summary"),
            "import_source": "supervisor-mentor",
        })

    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/team-creator/import-personal", methods=["POST"])
def team_creator_import_personal():
    """Import a personal/relationship-type skill into ClawCross Creator.

    Handles ex-skill (前任), crush-skill, yourself-skill, pig-skill (群友), etc.
    All share the same format: meta.json + persona.md + memory.md / self.md

    Accepts JSON body:
      - meta_json:    dict (parsed meta.json)
      - persona_md:   string (raw persona.md — 5-layer persona)
      - memory_md:    string (raw memory.md / self.md, optional)
      - skill_type:   string (one of: ex, crush, yourself, pig — default "ex")
      - team_name:    string (optional)
      - task_description: string (optional)

    Or multipart form upload with files: meta_json, persona_md, memory_md
    """
    try:
        import json as _json

        if request.is_json:
            body = request.get_json(silent=True) or {}
            meta_json = body.get("meta_json") or {}
            persona_md = str(body.get("persona_md") or "").strip()
            memory_md = str(body.get("memory_md") or body.get("self_md") or "").strip()
            skill_type = str(body.get("skill_type") or "ex").strip()
            team_name = str(body.get("team_name") or "").strip()
            task_description = str(body.get("task_description") or "").strip()
        else:
            meta_file = request.files.get("meta_json")
            persona_file = request.files.get("persona_md")
            memory_file = request.files.get("memory_md") or request.files.get("self_md")

            if meta_file:
                meta_json = _json.loads(meta_file.read().decode("utf-8"))
            else:
                raw = request.form.get("meta_json", "{}")
                meta_json = _json.loads(raw) if isinstance(raw, str) else raw

            persona_md = persona_file.read().decode("utf-8") if persona_file else request.form.get("persona_md", "")
            memory_md = memory_file.read().decode("utf-8") if memory_file else request.form.get("memory_md", "")
            skill_type = request.form.get("skill_type", "ex")
            team_name = request.form.get("team_name", "")
            task_description = request.form.get("task_description", "")

        if not meta_json:
            return jsonify({"ok": False, "error": "meta_json is required"}), 400
        if not persona_md:
            return jsonify({"ok": False, "error": "persona_md is required"}), 400

        team_config = import_personal_skill(
            meta_json=meta_json,
            persona_md=persona_md,
            memory_md=memory_md,
            skill_type=skill_type,
            team_name=team_name,
            task_description=task_description,
        )

        return jsonify({
            "ok": True,
            "team_config": team_config,
            "summary": team_config.get("summary"),
            "import_source": f"{skill_type}-skill",
        })

    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/team-creator/arxiv-search", methods=["POST"])
def team_creator_arxiv_search():
    """Search ArXiv for papers by author and return a ready-to-import mentor JSON.

    Phase 3: Python-native ArXiv search, no Node.js required.

    Body:
      - author_name: string (required)
      - affiliation: string (optional)
      - max_results: int (optional, default 20)
      - auto_import: bool (optional, default false — if true, also runs import_mentor_skill)
    """
    from services.skill_import_tools import search_arxiv, arxiv_papers_to_mentor_json

    body = request.get_json(silent=True) or {}
    author_name = str(body.get("author_name") or body.get("name") or "").strip()
    if not author_name:
        return jsonify({"ok": False, "error": "author_name is required"}), 400

    affiliation = str(body.get("affiliation") or "").strip()
    max_results = min(int(body.get("max_results") or 20), 100)
    auto_import = bool(body.get("auto_import"))

    try:
        papers = search_arxiv(author_name, max_results=max_results)
        if not papers:
            return jsonify({"ok": True, "papers": [], "mentor_json": None,
                            "message": f"No papers found for '{author_name}' on ArXiv"})

        mentor_json = arxiv_papers_to_mentor_json(papers, author_name, affiliation)

        result: dict[str, Any] = {
            "ok": True,
            "papers_count": len(papers),
            "papers": [
                {"title": p.title, "year": p.year, "authors": p.authors[:3], "arxiv_id": p.arxiv_id}
                for p in papers[:10]
            ],
            "mentor_json": mentor_json,
        }

        if auto_import:
            team_name = str(body.get("team_name") or "").strip()
            task_description = str(body.get("task_description") or "").strip()
            team_config = import_mentor_skill(
                mentor_json=mentor_json,
                team_name=team_name,
                task_description=task_description,
            )
            result["team_config"] = team_config
            result["summary"] = team_config.get("summary")
            result["auto_imported"] = True

        return jsonify(result)

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/team-creator/feishu-collect", methods=["POST"])
def team_creator_feishu_collect():
    """Collect Feishu messages for a colleague and return colleague-compatible data.

    Phase 3: Python-native Feishu API, no external tools required.

    Body:
      - app_id: string (Feishu App ID)
      - app_secret: string (Feishu App Secret)
      - target_name: string (colleague name to filter messages)
      - msg_limit: int (optional, default 500)
      - company/role/level/gender/mbti: string (optional profile info)
      - personality_tags: list[str] (optional)
      - culture_tags: list[str] (optional)
      - impression: string (optional)
      - auto_distill: bool (optional)
      - auto_import: bool (optional; implies auto_distill)
      - team_name / task_description: string (optional, used when auto_import=true)
    """
    from services.skill_import_tools import feishu_collect_user_messages, feishu_messages_to_colleague_meta

    body = request.get_json(silent=True) or {}
    app_id = str(body.get("app_id") or "").strip()
    app_secret = str(body.get("app_secret") or "").strip()
    target_name = str(body.get("target_name") or body.get("name") or "").strip()

    if not app_id or not app_secret:
        return jsonify({"ok": False, "error": "app_id and app_secret are required"}), 400
    if not target_name:
        return jsonify({"ok": False, "error": "target_name is required"}), 400

    msg_limit = min(int(body.get("msg_limit") or 500), 5000)
    auto_import = bool(body.get("auto_import"))
    auto_distill = bool(body.get("auto_distill")) or auto_import

    try:
        messages_text = feishu_collect_user_messages(
            app_id=app_id,
            app_secret=app_secret,
            target_name=target_name,
            msg_limit=msg_limit,
        )

        meta_json = feishu_messages_to_colleague_meta(
            target_name=target_name,
            messages_text=messages_text,
            company=str(body.get("company") or ""),
            role=str(body.get("role") or ""),
            level=str(body.get("level") or ""),
            gender=str(body.get("gender") or ""),
            mbti=str(body.get("mbti") or ""),
            personality_tags=body.get("personality_tags"),
            culture_tags=body.get("culture_tags"),
            impression=str(body.get("impression") or ""),
        )

        result: dict[str, Any] = {
            "ok": True,
            "meta_json": meta_json,
            "messages_text": messages_text,
            "messages_length": len(messages_text),
            "hint": "Use the returned meta_json + an LLM-generated persona.md with /api/team-creator/import-colleague",
        }

        if auto_distill:
            distilled = distill_colleague_skill_artifacts(meta_json=meta_json, messages_text=messages_text)
            tags = meta_json.setdefault("tags", {})
            tags["personality"] = list(dict.fromkeys([
                *(tags.get("personality") or []),
                *(distilled.get("personality_tags") or []),
            ]))
            tags["culture"] = list(dict.fromkeys([
                *(tags.get("culture") or []),
                *(distilled.get("culture_tags") or []),
            ]))
            if not str(meta_json.get("impression") or "").strip():
                meta_json["impression"] = distilled.get("impression") or ""

            result["meta_json"] = meta_json
            result["persona_md"] = distilled.get("persona_md") or ""
            result["work_md"] = distilled.get("work_md") or ""
            result["distillation"] = {
                "personality_tags": tags.get("personality") or [],
                "culture_tags": tags.get("culture") or [],
                "impression": str(meta_json.get("impression") or ""),
                "evidence_summary": str(distilled.get("evidence_summary") or ""),
            }
            result["hint"] = "persona.md / work.md generated and ready for import"

        if auto_import:
            team_name = str(body.get("team_name") or "").strip()
            task_description = str(body.get("task_description") or "").strip()
            team_config = import_colleague_skill(
                meta_json=meta_json,
                persona_md=str(result.get("persona_md") or ""),
                work_md=str(result.get("work_md") or ""),
                team_name=team_name,
                task_description=task_description,
            )
            result["team_config"] = team_config
            result["summary"] = team_config.get("summary")
            result["auto_imported"] = True
            result["hint"] = "Collected, distilled, and imported into ClawCross Creator"

        return jsonify(result)

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/team-creator/jobs")
def team_creator_jobs():
    """List all build jobs."""
    owner_id = str(session.get("user_id") or "").strip()
    limit = request.args.get("limit", type=int)
    return jsonify({"ok": True, "jobs": list_jobs(owner_id=owner_id, limit=limit)})


@app.route("/api/team-creator/jobs/<job_id>")
def team_creator_job_status(job_id):
    """Get status of a specific build job."""
    owner_id = str(session.get("user_id") or "").strip()
    job = get_job(job_id, owner_id=owner_id)
    if not job:
        return jsonify({"ok": False, "error": "Job not found"}), 404
    return jsonify({"ok": True, "job": job.to_dict(include_payload=True)})


@app.route("/studio")
def studio():
    """ClawCross Studio 页面"""
    return render_template("index.html")


@app.route("/mobile/group_chat")
def group_chat_mobile():
    """移动端群组群聊页面 - 需要登录访问"""
    return render_template("group_chat_mobile.html")


@app.route("/mobile_group_chat")
def group_chat_mobile_alias():
    """移动端群组群聊页面(别名) - 需要登录访问"""
    return render_template("group_chat_mobile.html")


def _resolve_local_preview_path(raw_path: str) -> tuple[Path | None, str | None]:
    value = str(raw_path or "").strip()
    if not value:
        return None, "Missing path"

    candidate = Path(os.path.expanduser(value))
    if candidate.is_absolute():
        candidate = candidate.resolve()
    else:
        candidate = (Path(root_dir) / candidate).resolve()
    if not candidate.exists():
        return None, "File does not exist"
    if not candidate.is_file():
        return None, "Path is not a file"
    return candidate, None


def _local_preview_error_page(title: str, message: str, status: int) -> Response:
    return Response(
        (
            "<html><body style='font-family:sans-serif;padding:24px;'>"
            f"<h3>{html.escape(title)}</h3>"
            f"<p>{html.escape(message)}</p>"
            "</body></html>"
        ),
        status=status,
        mimetype="text/html",
    )


def _guess_preview_kind(path: Path) -> tuple[str, str]:
    mime_type, _ = mimetypes.guess_type(str(path))
    mime_type = mime_type or "application/octet-stream"
    if mime_type.startswith("image/"):
        return "image", mime_type
    if mime_type.startswith("video/"):
        return "video", mime_type
    if mime_type.startswith("audio/"):
        return "audio", mime_type
    if mime_type == "application/pdf":
        return "pdf", mime_type
    if mime_type in {
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
        "application/vnd.ms-powerpoint.presentation.macroenabled.12",
    }:
        return "powerpoint", mime_type
    if mime_type.startswith("text/") or mime_type in {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-sh",
        "application/yaml",
    }:
        return "text", mime_type
    return "binary", mime_type


def _build_public_file_url(relative_path: str) -> str:
    relative = "/" + str(relative_path or "").lstrip("/")
    public_domain = _get_public_domain().strip().rstrip('/')
    if public_domain:
        if not public_domain.startswith(("http://", "https://")):
            public_domain = f"https://{public_domain}"
        return f"{public_domain}{relative}"
    return urljoin(request.url_root, relative.lstrip("/"))


_PPT_CONVERT_LOCK = threading.Lock()


def _find_ppt_converter() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _ppt_preview_cache_dir() -> Path:
    cache_dir = Path(tempfile.gettempdir()) / "clawcross-preview-cache" / "ppt-pdf"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _convert_powerpoint_to_pdf(source_path: Path) -> tuple[Path | None, str | None]:
    converter = _find_ppt_converter()
    if not converter:
        return None, "未检测到 LibreOffice/soffice，暂时无法在本机把 PPT 转成 PDF 预览。"

    source_stat = source_path.stat()
    cache_key = hashlib.sha256(
        f"{source_path.resolve()}::{source_stat.st_mtime_ns}::{source_stat.st_size}".encode("utf-8")
    ).hexdigest()
    target_dir = _ppt_preview_cache_dir() / cache_key
    pdf_path = target_dir / f"{source_path.stem}.pdf"
    if pdf_path.exists():
        return pdf_path, None

    target_dir.mkdir(parents=True, exist_ok=True)
    with _PPT_CONVERT_LOCK:
        if pdf_path.exists():
            return pdf_path, None
        cmd = [
            converter,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(target_dir),
            str(source_path),
        ]
        try:
            completed = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return None, "PPT 转 PDF 超时，请稍后重试或直接下载原文件。"
        except Exception as exc:
            return None, f"PPT 转 PDF 失败：{exc}"

        if completed.returncode != 0 or not pdf_path.exists():
            stderr = (completed.stderr or completed.stdout or "").strip()
            detail = f" 转换器输出：{stderr}" if stderr else ""
            return None, f"PPT 转 PDF 失败。{detail}".strip()

    return pdf_path, None


def _render_powerpoint_html_preview(source_path: Path) -> tuple[str | None, str | None]:
    try:
        from pptx import Presentation
        from PIL import Image
    except Exception as exc:
        return None, f"纯 Python PPT 预览依赖加载失败：{exc}"

    try:
        prs = Presentation(str(source_path))
    except Exception as exc:
        return None, f"PPT 文件解析失败：{exc}"

    slide_html_parts: list[str] = []
    for slide_index, slide in enumerate(prs.slides, start=1):
        text_runs: list[str] = []
        notes_runs: list[str] = []
        image_blocks: list[str] = []

        for shape in slide.shapes:
            try:
                if getattr(shape, "has_text_frame", False) and shape.text_frame:
                    text = "\n".join(
                        paragraph.text.strip()
                        for paragraph in shape.text_frame.paragraphs
                        if paragraph.text and paragraph.text.strip()
                    ).strip()
                    if text:
                        text_runs.append(text)
                if shape.shape_type == 13 and getattr(shape, "image", None):
                    image_blob = shape.image.blob
                    with Image.open(BytesIO(image_blob)) as img:
                        width, height = img.size
                    image_b64 = base64.b64encode(image_blob).decode("ascii")
                    ext = (shape.image.ext or "png").lower()
                    mime = "image/png" if ext == "png" else f"image/{ext}"
                    alt = html.escape(getattr(shape, "name", "") or f"slide-{slide_index}-image")
                    image_blocks.append(
                        "<figure class='ppt-image'>"
                        f"<img src='data:{mime};base64,{image_b64}' alt='{alt}'>"
                        f"<figcaption>{alt} · {width}×{height}</figcaption>"
                        "</figure>"
                    )
            except Exception:
                continue

        notes_text_frame = getattr(getattr(slide, "notes_slide", None), "notes_text_frame", None)
        if notes_text_frame:
            notes_text = "\n".join(
                paragraph.text.strip()
                for paragraph in notes_text_frame.paragraphs
                if paragraph.text and paragraph.text.strip()
            ).strip()
            if notes_text:
                notes_runs.append(notes_text)

        body_html = ""
        if text_runs:
            body_html += "".join(
                f"<pre class='ppt-text-block'>{html.escape(block)}</pre>"
                for block in text_runs
            )
        if image_blocks:
            body_html += "<div class='ppt-image-grid'>" + "".join(image_blocks) + "</div>"
        if notes_runs:
            body_html += (
                "<details class='ppt-notes'><summary>讲者备注</summary>"
                + "".join(f"<pre class='ppt-text-block ppt-notes-text'>{html.escape(block)}</pre>" for block in notes_runs)
                + "</details>"
            )
        if not body_html:
            body_html = "<div class='empty-state'><div class='hint'>这一页没有可提取的文本或图片内容。</div></div>"

        slide_html_parts.append(
            "<section class='ppt-slide'>"
            f"<div class='ppt-slide-header'>第 {slide_index} 页</div>"
            f"<div class='ppt-slide-body'>{body_html}</div>"
            "</section>"
        )

    summary = f"共提取 {len(prs.slides)} 页"
    preview_html = (
        f"<div class='hint'>{summary} · 纯 Python 简化预览（文本、图片、备注）</div>"
        "<div class='ppt-preview-stack'>"
        + "".join(slide_html_parts)
        + "</div>"
    )
    return preview_html, None


@app.route("/local-file-converted")
def local_file_converted():
    if not session.get("user_id"):
        return redirect("/", code=302)

    raw_path = request.args.get("path", "")
    target_path, error = _resolve_local_preview_path(raw_path)
    if error or target_path is None:
        return _local_preview_error_page("Cannot open file", error or "Unknown error", 400)

    kind, _mime_type = _guess_preview_kind(target_path)
    if kind != "powerpoint":
        return _local_preview_error_page("Cannot convert file", "This file is not a PowerPoint document", 400)

    pdf_path, convert_error = _convert_powerpoint_to_pdf(target_path)
    if convert_error or pdf_path is None:
        return _local_preview_error_page("PPT preview unavailable", convert_error or "Conversion failed", 503)

    return send_file(
        str(pdf_path),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=pdf_path.name,
        conditional=True,
        etag=True,
        max_age=300,
    )


@app.route("/local-file-raw")
def local_file_raw():
    if not session.get("user_id"):
        return redirect("/", code=302)

    raw_path = request.args.get("path", "")
    target_path, error = _resolve_local_preview_path(raw_path)
    if error or target_path is None:
        return _local_preview_error_page("Cannot open file", error or "Unknown error", 400)

    kind, mime_type = _guess_preview_kind(target_path)
    as_attachment = request.args.get("download", "").strip() == "1"
    return send_file(
        str(target_path),
        mimetype=mime_type,
        as_attachment=as_attachment or kind == "binary",
        download_name=target_path.name,
        conditional=True,
        etag=True,
        max_age=300,
    )


@app.route("/local-file-check")
def local_file_check():
    if not session.get("user_id"):
        return jsonify({"ok": False, "error": "not logged in"}), 401

    raw_path = request.args.get("path", "")
    target_path, error = _resolve_local_preview_path(raw_path)
    if error or target_path is None:
        return jsonify({"ok": True, "exists": False, "error": error or "Unknown error"})

    return jsonify({
        "ok": True,
        "exists": True,
        "path": str(target_path),
        "name": target_path.name,
    })


@app.route("/local-file-view")
def local_file_view():
    if not session.get("user_id"):
        return redirect("/", code=302)

    raw_path = request.args.get("path", "")
    try:
        line = max(1, int(request.args.get("line", "1") or "1"))
    except ValueError:
        line = 1
    try:
        col = max(1, int(request.args.get("col", "1") or "1"))
    except ValueError:
        col = 1

    target_path, error = _resolve_local_preview_path(raw_path)
    if error or target_path is None:
        return _local_preview_error_page("Cannot open file", error or "Unknown error", 400)

    preview_kind, mime_type = _guess_preview_kind(target_path)
    title = html.escape(str(target_path))
    raw_url = f"/local-file-raw?path={request.args.get('path', '')}"
    raw_url_escaped = html.escape(raw_url, quote=True)
    download_url_escaped = html.escape(f"{raw_url}&download=1", quote=True)

    if preview_kind != "text":
        if preview_kind == "image":
            preview_html = f"<img class='media media-image' src='{raw_url_escaped}' alt='{title}'>"
        elif preview_kind == "video":
            preview_html = f"<video class='media' controls playsinline preload='metadata' src='{raw_url_escaped}'></video>"
        elif preview_kind == "audio":
            preview_html = f"<audio class='media-audio' controls preload='metadata' src='{raw_url_escaped}'></audio>"
        elif preview_kind == "pdf":
            preview_html = (
                f"<iframe class='media media-pdf' src='{raw_url_escaped}#view=FitH' title='{title}'></iframe>"
                f"<div class='hint'>如果手机浏览器不支持内嵌 PDF，可点下方“下载原文件”。</div>"
            )
        elif preview_kind == "powerpoint":
            converted_pdf_url = f"/local-file-converted?path={request.args.get('path', '')}"
            converted_pdf_url_escaped = html.escape(converted_pdf_url, quote=True)
            converter_available = _find_ppt_converter() is not None
            raw_absolute_url = _build_public_file_url(raw_url)
            office_embed_url = "https://view.officeapps.live.com/op/embed.aspx?src=" + requests.utils.requote_uri(raw_absolute_url)
            office_view_url = "https://view.officeapps.live.com/op/view.aspx?src=" + requests.utils.requote_uri(raw_absolute_url)
            host_only = (request.host.split(":", 1)[0] or "").strip().lower()
            can_embed_office = host_only not in {"127.0.0.1", "localhost", "::1", "[::1]"}
            if converter_available:
                preview_html = (
                    f"<iframe class='media media-pdf' src='{converted_pdf_url_escaped}#view=FitH' title='{title}'></iframe>"
                    "<div class='hint'>已在本机将 PPT/PPTX 转成 PDF 进行预览；如果内容更新，重新打开此链接会自动刷新转换缓存。</div>"
                )
                if can_embed_office:
                    preview_html += (
                        f"<div class='actions' style='margin-top:12px;'><a class='btn btn-secondary' href='{html.escape(office_view_url, quote=True)}' target='_blank' rel='noopener noreferrer'>在 Office Viewer 打开</a></div>"
                    )
            else:
                python_preview_html, python_preview_error = _render_powerpoint_html_preview(target_path)
                if python_preview_html:
                    preview_html = python_preview_html
                    if can_embed_office:
                        preview_html += (
                            f"<div class='actions' style='margin-top:12px;'><a class='btn btn-secondary' href='{html.escape(office_view_url, quote=True)}' target='_blank' rel='noopener noreferrer'>在 Office Viewer 打开</a></div>"
                        )
                elif can_embed_office:
                    preview_html = (
                        f"<iframe class='media media-pdf' src='{html.escape(office_embed_url, quote=True)}' title='{title}'></iframe>"
                        "<div class='hint'>PPT/PPTX 通过 Office Web Viewer 远程预览；若加载失败，可点“在 Office Viewer 打开”或“下载原文件”。</div>"
                        f"<div class='actions' style='margin-top:12px;'><a class='btn btn-secondary' href='{html.escape(office_view_url, quote=True)}' target='_blank' rel='noopener noreferrer'>在 Office Viewer 打开</a></div>"
                    )
                else:
                    public_domain = _get_public_domain()
                    if public_domain:
                        preview_html = (
                            "<div class='empty-state'>"
                            "<div class='empty-title'>当前是本机地址，已为你准备远程 PPT 预览</div>"
                            f"<div class='hint'>{html.escape(python_preview_error or '当前环境未检测到本地 PPT→PDF 转换器。')} 已改用 Tunnel 公网地址生成远程预览链接。</div>"
                            "</div>"
                            f"<div class='actions' style='margin-top:12px;justify-content:center;'><a class='btn btn-primary' href='{html.escape(office_view_url, quote=True)}' target='_blank' rel='noopener noreferrer'>打开远程 PPT 预览</a><a class='btn btn-secondary' href='{html.escape(raw_absolute_url, quote=True)}' target='_blank' rel='noopener noreferrer'>打开公网原文件</a></div>"
                            f"<div class='hint' style='text-align:center;'>Tunnel 地址：{html.escape(public_domain)}</div>"
                        )
                    else:
                        preview_html = (
                            "<div class='empty-state'>"
                            "<div class='empty-title'>当前无法直接预览 PPT</div>"
                            f"<div class='hint'>{html.escape(python_preview_error or '当前既没有本地 PPT→PDF 转换器，也没有可用的 Tunnel 公网地址。')} 你仍可先下载原文件。</div>"
                            "</div>"
                        )
        else:
            preview_html = (
                "<div class='empty-state'>"
                "<div class='empty-title'>暂不支持在线预览此文件类型</div>"
                f"<div class='hint'>MIME: {html.escape(mime_type)}</div>"
                "</div>"
            )
        return Response(
            (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1, viewport-fit=cover'>"
                f"<title>{title}</title>"
                "<style>"
                "body{margin:0;background:#f3f6fb;color:#1e293b;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}"
                ".wrap{padding:16px;max-width:1080px;margin:0 auto;}"
                ".meta{margin-bottom:16px;padding:12px 14px;border:1px solid #d7e0ea;border-radius:14px;background:#ffffff;box-shadow:0 10px 30px rgba(15,23,42,0.06);}"
                ".meta .path{font-size:14px;font-weight:600;word-break:break-all;line-height:1.5;color:#0f172a;}"
                ".meta .sub{margin-top:6px;color:#64748b;font-size:12px;}"
                ".actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px;}"
                ".btn{display:inline-flex;align-items:center;justify-content:center;padding:10px 14px;border-radius:10px;text-decoration:none;font-size:13px;font-weight:600;}"
                ".btn-primary{background:#2563eb;color:#fff;}"
                ".btn-secondary{background:#ffffff;color:#334155;border:1px solid #cbd5e1;}"
                ".viewer{background:#ffffff;border:1px solid #d7e0ea;border-radius:14px;padding:14px;min-height:240px;box-shadow:0 16px 40px rgba(15,23,42,0.05);}"
                ".media{display:block;width:100%;max-width:100%;border:none;border-radius:8px;background:#000;min-height:240px;}"
                ".media-image{width:auto;max-width:100%;max-height:75vh;margin:0 auto;object-fit:contain;}"
                ".media-pdf{height:80vh;}"
                ".media-audio{width:100%;}"
                ".hint{margin-top:10px;color:#64748b;font-size:12px;line-height:1.6;}"
                ".empty-state{padding:24px 12px;text-align:center;}"
                ".empty-title{font-size:16px;font-weight:700;margin-bottom:8px;color:#0f172a;}"
                ".ppt-preview-stack{display:flex;flex-direction:column;gap:16px;}"
                ".ppt-slide{border:1px solid #d7e0ea;border-radius:14px;background:#f8fbff;overflow:hidden;box-shadow:0 8px 24px rgba(15,23,42,0.04);}"
                ".ppt-slide-header{padding:10px 14px;background:linear-gradient(180deg,#eef6ff,#e8f1ff);border-bottom:1px solid #d7e0ea;font-size:13px;font-weight:700;color:#1d4ed8;}"
                ".ppt-slide-body{padding:14px;display:flex;flex-direction:column;gap:12px;}"
                ".ppt-text-block{margin:0;padding:12px;border-radius:10px;background:#ffffff;color:#0f172a;white-space:pre-wrap;word-break:break-word;font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;border:1px solid #e2e8f0;}"
                ".ppt-image-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;}"
                ".ppt-image{margin:0;padding:10px;border-radius:12px;background:#ffffff;border:1px solid #dbe4ee;}"
                ".ppt-image img{display:block;max-width:100%;max-height:320px;object-fit:contain;margin:0 auto 8px auto;border-radius:8px;background:#f8fafc;}"
                ".ppt-image figcaption{font-size:11px;color:#64748b;word-break:break-word;}"
                ".ppt-notes{border:1px dashed #93c5fd;border-radius:10px;padding:10px;background:#f8fbff;}"
                ".ppt-notes summary{cursor:pointer;color:#1d4ed8;font-size:12px;font-weight:600;}"
                ".ppt-notes-text{margin-top:10px;}"
                "@media (max-width: 640px){.wrap{padding:12px;}.media-pdf{height:70vh;}}"
                "</style></head><body>"
                "<div class='wrap'>"
                f"<div class='meta'><div class='path'>{title}</div><div class='sub'>Remote preview · {html.escape(mime_type)}</div>"
                f"<div class='actions'><a class='btn btn-primary' href='{download_url_escaped}'>下载原文件</a><a class='btn btn-secondary' href='{raw_url_escaped}' target='_blank' rel='noopener noreferrer'>新窗口打开</a></div></div>"
                f"<div class='viewer'>{preview_html}</div>"
                "</div></body></html>"
            ),
            mimetype="text/html",
        )

    try:
        content = target_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return _local_preview_error_page("Cannot read file", str(exc), 500)

    lines = content.splitlines()
    gutter_width = max(3, len(str(max(1, len(lines)))))
    rendered_lines = []
    for idx, text in enumerate(lines or [""], start=1):
        escaped_text = html.escape(text)
        line_id = f"L{idx}"
        active_class = " is-active" if idx == line else ""
        rendered_lines.append(
            f"<div class='code-line{active_class}' id='{line_id}'>"
            f"<a class='gutter' href='#{line_id}'>{str(idx).rjust(gutter_width)}</a>"
            f"<span class='code'>{escaped_text or ' '}</span>"
            "</div>"
        )

    title = html.escape(str(target_path))
    return Response(
        (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1, viewport-fit=cover'>"
            f"<title>{title}</title>"
            "<style>"
            "body{margin:0;background:#0b1020;color:#e5e7eb;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;}"
            ".wrap{padding:16px;max-width:1200px;margin:0 auto;}"
            ".meta{margin-bottom:16px;padding:12px 14px;border:1px solid #334155;border-radius:10px;background:#111827;}"
            ".meta .path{font-size:14px;font-weight:600;word-break:break-all;}"
            ".meta .sub{margin-top:6px;color:#94a3b8;font-size:12px;}"
            ".actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px;}"
            ".btn{display:inline-flex;align-items:center;justify-content:center;padding:10px 14px;border-radius:10px;text-decoration:none;font-size:13px;font-weight:600;}"
            ".btn-primary{background:#2563eb;color:#fff;}"
            ".btn-secondary{background:#1f2937;color:#e5e7eb;border:1px solid #334155;}"
            ".viewer{border-top:1px solid #1f2937;border-bottom:1px solid #1f2937;background:#020617;overflow:auto;}"
            ".code-line{display:flex;min-height:22px;line-height:22px;}"
            ".code-line.is-active{background:rgba(59,130,246,0.15);}"
            ".gutter{flex:0 0 auto;width:64px;padding:0 12px;color:#64748b;text-decoration:none;text-align:right;user-select:none;border-right:1px solid #1f2937;}"
            ".code{white-space:pre;display:block;padding:0 16px;min-width:max-content;}"
            "@media (max-width: 640px){.wrap{padding:12px;}.gutter{width:52px;padding:0 8px;}.code{padding:0 10px;font-size:12px;}}"
            "</style></head><body>"
            "<div class='wrap'>"
            f"<div class='meta'><div class='path'>{title}</div><div class='sub'>Line {line}, Column {col}</div>"
            f"<div class='actions'><a class='btn btn-primary' href='{download_url_escaped}'>下载原文件</a><a class='btn btn-secondary' href='{raw_url_escaped}' target='_blank' rel='noopener noreferrer'>原始内容</a></div></div>"
            f"<div class='viewer'>{''.join(rendered_lines)}</div>"
            "</div>"
            f"<script>location.hash='L{line}';</script>"
            "</body></html>"
        ),
        mimetype="text/html",
    )


@app.route("/manifest.json")
def manifest():
    """Serve PWA manifest for iOS/Android Add-to-Home-Screen support."""
    manifest_data = {
        "name": "Clawcross",
        "short_name": "Clawcross",
        "description": "WeBot AI Agent - Intelligent Control Assistant",
        "start_url": "/mobile_group_chat",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#111827",
        "theme_color": "#111827",
        "lang": "zh-CN",
        "categories": ["productivity", "utilities"],
        "icons": [
            {
                "src": "https://img.icons8.com/fluency/192/robot-2.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable"
            },
            {
                "src": "https://img.icons8.com/fluency/512/robot-2.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable"
            }
        ]
    }
    return app.response_class(
        response=__import__("json").dumps(manifest_data),
        mimetype="application/manifest+json"
    )


@app.route("/sw.js")
def service_worker():
    """Serve Service Worker for PWA offline support and caching."""
    sw_code = """
// Clawcross Service Worker v4 — network-first for all resources
const CACHE_NAME = 'clawcross-v4';
const PRECACHE_URLS = ['/'];

self.addEventListener('install', event => {
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(PRECACHE_URLS))
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    // CRITICAL: Only handle GET requests. Non-GET (POST, PUT, DELETE) must pass through directly.
    if (event.request.method !== 'GET') return;

    // API / dynamic GET requests must NEVER be cached by SW — pass through directly
    const url = event.request.url;
    if (url.includes('/proxy_') || url.includes('/ask') || url.includes('/v1/') || url.includes('/api/')
        || url.includes('/teams') || url.includes('/internal_agent') || url.includes('/login')
        || url.includes('/status') || url.includes('/sessions') || url.includes('/experts')) return;

    // Network-first for static assets (JS/CSS/images/HTML):
    // Always try network first to get the latest version;
    // fall back to cache only when offline.
    event.respondWith(
        fetch(event.request).then(response => {
            if (response.ok) {
                const clone = response.clone();
                caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
            }
            return response;
        }).catch(() => caches.match(event.request))
    );
});
"""
    return app.response_class(
        response=sw_code,
        mimetype="application/javascript",
        headers={"Service-Worker-Allowed": "/"}
    )


@app.route("/v1/chat/completions", methods=["POST", "OPTIONS"])
def proxy_openai_completions():
    """OpenAI 兼容端点透传：前端直接发 OpenAI 格式，原样转发到后端"""
    if request.method == "OPTIONS":
        # CORS preflight
        resp = Response("", status=204)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp

    # 认证策略：
    # 1. 有 Flask session（前端网页登录）→ 用 INTERNAL_TOKEN:user_id 构造认证头
    # 2. 无 session 但有 Authorization（远程 CLI / 第三方客户端经 Tunnel）→ 原样透传
    auth_header = request.headers.get("Authorization", "")
    user_id = session.get("user_id")
    if user_id:
        # 前端网页走 session，用 INTERNAL_TOKEN 补全认证，不暴露密码
        auth_header = f"Bearer {INTERNAL_TOKEN}:{user_id}"
    # else: 外部调用自带 Bearer user:password，原样透传

    body = request.get_json(silent=True)
    try:
        r = requests.post(
            LOCAL_OPENAI_COMPLETIONS_URL,
            json=body if isinstance(body, dict) else {},
            headers={"Authorization": auth_header, "Content-Type": "application/json"},
            stream=True,
            timeout=None,
        )
        if r.status_code != 200:
            return Response(r.content, status=r.status_code, content_type=r.headers.get("content-type", "application/json"))

        # 判断是否是流式响应
        content_type = r.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            def generate():
                for chunk in r.iter_content(chunk_size=None):
                    if chunk:
                        yield chunk
            return Response(
                generate(),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            return Response(r.content, status=r.status_code, content_type=content_type)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/v1/models", methods=["GET"])
def proxy_openai_models():
    """透传 /v1/models：新 agent 可用的运行方式。"""
    auth_header = request.headers.get("Authorization", "")
    user_id = session.get("user_id")
    if user_id:
        auth_header = f"Bearer {INTERNAL_TOKEN}:{user_id}"
    try:
        r = requests.get(
            f"http://127.0.0.1:{PORT_AGENT}/v1/models",
            headers={"Authorization": auth_header} if auth_header else {},
            timeout=10,
        )
        return Response(r.content, status=r.status_code, content_type=r.headers.get("content-type", "application/json"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_check_session")
def proxy_check_session():
    """轻量 session 校验：前端页面加载时调用，确认后端 session 仍然有效"""
    user_id = session.get("user_id")
    if user_id:
        return jsonify({
            "valid": True,
            "user_id": user_id,
            "has_password": _user_exists_in_users_json(user_id),
            "mode": session.get("login_mode", ""),
        })
    return jsonify({"valid": False})


@app.route("/proxy_login", methods=["POST"])
def proxy_login():
    """代理登录请求到后端 Agent
    
    支持两种登录方式：
    1. 密码登录：user_id + password
    2. 本机免密登录：本地 127.0.0.1 直连时，只需要 user_id，不需要密码
    """
    body = request.get_json(silent=True) or {}
    user_id = str(body.get("user_id") or "").strip()
    password = str(body.get("password") or "")
    is_local = _is_direct_local_request()

    if not user_id:
        return jsonify({
            "error": "请输入用户名 / Username required",
            "error_code": "user_id_required",
        }), 400

    # 本机免密登录：127.0.0.1 直连且未提供密码时
    if is_local and not password:
        # 直接创建 session，不需要验证密码
        session["user_id"] = user_id
        session["login_mode"] = "local_no_password"
        session.permanent = True
        return jsonify({
            "ok": True,
            "user_id": user_id,
            "mode": "local_no_password",
            "has_password": _user_exists_in_users_json(user_id),
        })

    # 密码登录
    if not password:
        return jsonify({
            "error": "请输入密码 / Password required",
            "error_code": "password_required",
        }), 400

    # 检查用户是否在 users.json 中（有密码记录）
    # 仅免密用户（不在 users.json 中）不允许密码登录
    if not _user_exists_in_users_json(user_id):
        return jsonify({
            "error": (
                f"用户 '{user_id}' 未设置密码，无法使用密码登录。"
                f"请先使用「本机免密登录」，再到设置页为这个用户名创建密码。"
                f" / User '{user_id}' does not have a password configured, so password login is unavailable. "
                f"Use Local No-Password Login first, then create a password for this username in Settings."
            ),
            "error_code": "password_login_not_available",
            "user_id": user_id,
        }), 403

    try:
        r = requests.post(LOCAL_LOGIN_URL, json={"user_id": user_id, "password": password}, timeout=10)
        if r.status_code == 200:
            # Login succeeded — only store user_id, NOT password.
            # Subsequent requests use INTERNAL_TOKEN for backend auth.
            session["user_id"] = user_id
            session["login_mode"] = "password"
            session.permanent = True
            payload = r.json()
            if isinstance(payload, dict):
                payload.setdefault("has_password", True)
                payload.setdefault("mode", "password")
            return jsonify(payload)
        else:
            return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/current_user/password", methods=["POST"])
def save_current_user_password():
    """为当前登录用户创建或更新密码登录凭据。"""
    user_id = str(session.get("user_id") or "").strip()
    if not user_id:
        return jsonify({"error": "未登录"}), 401

    body = request.get_json(silent=True) or {}
    password = str(body.get("password") or "")
    if not password:
        return jsonify({
            "error": "请输入密码 / Password required",
            "error_code": "password_required",
        }), 400

    try:
        users = _load_users_json()
        operation = "updated" if user_id in users else "created"
        users[user_id] = _hash_password(password)
        _write_users_json(users)
        return jsonify({
            "ok": True,
            "user_id": user_id,
            "status": operation,
            "has_password": True,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────────────────────
# [已弃用] proxy_ask 和 proxy_ask_stream — 已被前端直接调用
# /v1/chat/completions 替代，以下端点注释保留备查。
# ──────────────────────────────────────────────────────────────
# @app.route("/proxy_ask", methods=["POST"])
# def proxy_ask():
#     ...
#
# @app.route("/proxy_ask_stream", methods=["POST"])
# def proxy_ask_stream():
#     ...

@app.route("/proxy_cancel", methods=["POST"])
def proxy_cancel():
    """代理取消请求到后端 Agent"""
    user_id = session.get("user_id", "")
    session_id = request.json.get("session_id", "default") if request.is_json else "default"
    try:
        r = requests.post(LOCAL_AGENT_CANCEL_URL, json={"user_id": user_id, "session_id": session_id}, headers=_internal_auth_headers(), timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/proxy_tts", methods=["POST"])
def proxy_tts():
    """代理 TTS 请求到后端 Agent，返回 mp3 音频流"""
    user_id = session.get("user_id", "")

    text = request.json.get("text", "")
    voice = request.json.get("voice")
    if not text.strip():
        return jsonify({"error": "文本不能为空"}), 400

    try:
        payload = {"user_id": user_id, "text": text}
        if voice:
            payload["voice"] = voice
        r = requests.post(LOCAL_TTS_URL, json=payload, headers=_internal_auth_headers(), timeout=60)
        if r.status_code != 200:
            return jsonify({"error": f"TTS 服务错误: {r.status_code}"}), r.status_code

        return Response(
            r.content,
            mimetype="audio/mpeg",
            headers={"Content-Disposition": "inline; filename=tts_output.mp3"},
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/proxy_tools")
def proxy_tools():
    """代理获取工具列表请求到后端 Agent"""
    try:
        r = requests.get(LOCAL_TOOLS_URL, headers={"X-Internal-Token": INTERNAL_TOKEN}, timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e), "tools": []}), 500

@app.route("/proxy_logout", methods=["POST"])
def proxy_logout():
    session.clear()
    return jsonify({"status": "success"})


@app.route("/login-link/<token>")
def magic_login(token):
    """Magic link login endpoint - login with a token in URL.
    
    Usage: /login-link/<token>?user=<user_id>
    """
    user_id = request.args.get('user', '')
    if not user_id:
        return jsonify({"error": "Missing user parameter"}), 400
    
    verified_user = verify_login_token(token)
    if verified_user == user_id:
        session['user_id'] = user_id
        session.permanent = True
        # Redirect to mobile group chat after successful login
        return redirect('/mobile_group_chat')
    else:
        return jsonify({"error": "Invalid or expired token"}), 401


@app.route("/generate_login_link", methods=["POST"])
def generate_login_link():
    """Generate a login token link for a user.
    
    Body: { "user_id": "username" }
    Returns: { "ok": true, "token": "...", "link": "https://.../login-link/xxx" }
    
    Note: This endpoint can ONLY be called from localhost (127.0.0.1) for security.
    """
    # Security: Only allow direct localhost requests
    if not _is_direct_local_request():
        return jsonify({"error": "Forbidden - localhost only"}), 403
    
    body = request.get_json(force=True)
    user_id = body.get("user_id", "")
    
    if not user_id:
        return jsonify({"error": "user_id is required"}), 400
    
    # Generate a fresh token for each request so /front never reuses an expired link.
    valid_hours = 24
    generated_ts = int(time.time())
    token = generate_login_token(user_id, valid_hours=valid_hours)
    expires_ts = login_token_expire_ts(token) or (generated_ts + valid_hours * 3600)

    # Re-read PUBLIC_DOMAIN from .env each call so tunnel updates take effect
    # without restarting the front process. Normalize scheme: PUBLIC_DOMAIN may be
    # stored as either bare host or full https://host (current convention).
    public_domain = _get_public_domain().strip().rstrip('/')

    if public_domain:
        if public_domain.startswith(("http://", "https://")):
            base_url = public_domain
        else:
            base_url = f"https://{public_domain}"
    else:
        base_url = request.host_url.rstrip('/')

    magic_link = f"{base_url}/login-link/{token}?user={user_id}"
    
    return jsonify({
        "ok": True,
        "token": token,
        "link": magic_link,
        "user_id": user_id,
        "generated_at": generated_ts,
        "expires_at": expires_ts,
        "valid_hours": valid_hours,
    })


@app.route("/proxy_login_with_token", methods=["POST"])
def proxy_login_with_token():
    """Login with a magic token.
    
    Body: { "user_id": "username", "token": "xxx" }
    Returns: { "ok": true, "user_id": "..." } or { "error": "..." }
    """
    body = request.get_json(force=True)
    user_id = body.get("user_id", "")
    token = body.get("token", "")
    
    if not user_id or not token:
        return jsonify({"error": "user_id and token are required"}), 400
    
    # Verify token
    verified_user = verify_login_token(token)
    if verified_user != user_id:
        return jsonify({"error": "Invalid or expired token"}), 401
    
    # Create session
    session['user_id'] = user_id
    session["login_mode"] = "token_login"
    session.permanent = True
    
    return jsonify({
        "ok": True,
        "user_id": user_id,
        "mode": "token_login",
        "has_password": _user_exists_in_users_json(user_id),
    })


LOCAL_SETTINGS_URL = f"http://127.0.0.1:{PORT_AGENT}/settings"


@app.route("/proxy_settings", methods=["GET"])
def proxy_get_settings():
    """代理获取系统配置"""
    user_id = session.get("user_id", "")
    try:
        r = requests.get(LOCAL_SETTINGS_URL, params={"user_id": user_id}, headers=_internal_auth_headers(), timeout=10)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_settings", methods=["POST"])
def proxy_update_settings():
    """代理更新系统配置"""
    user_id = session.get("user_id", "")
    try:
        data = request.get_json(force=True)
        data["user_id"] = user_id
        r = requests.post(LOCAL_SETTINGS_URL, json=data, headers=_internal_auth_headers(), timeout=10)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


LOCAL_SETTINGS_FULL_URL = f"http://127.0.0.1:{PORT_AGENT}/settings/full"
LOCAL_CHATBOT_WHITELIST_URL = f"http://127.0.0.1:{PORT_AGENT}/chatbot/whitelist"
LOCAL_RESTART_URL = f"http://127.0.0.1:{PORT_AGENT}/restart"


@app.route("/proxy_settings_full", methods=["GET"])
def proxy_get_settings_full():
    """代理获取全量系统配置（不受白名单限制）"""
    user_id = session.get("user_id", "")
    try:
        r = requests.get(LOCAL_SETTINGS_FULL_URL, params={"user_id": user_id}, headers=_internal_auth_headers(), timeout=10)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_settings_full", methods=["POST"])
def proxy_update_settings_full():
    """代理更新全量系统配置（不受白名单限制）"""
    user_id = session.get("user_id", "")
    try:
        data = request.get_json(force=True)
        data["user_id"] = user_id
        r = requests.post(LOCAL_SETTINGS_FULL_URL, json=data, headers=_internal_auth_headers(), timeout=10)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_chatbot_whitelist", methods=["GET"])
def proxy_get_chatbot_whitelist():
    """代理获取 chatbot 白名单"""
    user_id = session.get("user_id", "")
    try:
        r = requests.get(LOCAL_CHATBOT_WHITELIST_URL, params={"user_id": user_id}, headers=_internal_auth_headers(), timeout=10)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_chatbot_whitelist", methods=["POST"])
def proxy_update_chatbot_whitelist():
    """代理更新 chatbot 白名单"""
    user_id = session.get("user_id", "")
    try:
        data = request.get_json(force=True)
        data["user_id"] = user_id
        r = requests.post(LOCAL_CHATBOT_WHITELIST_URL, json=data, headers=_internal_auth_headers(), timeout=10)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_weclaw_qr", methods=["GET"])
def proxy_get_weclaw_qr():
    """读取 WeClaw 扫码登录二维码（ASCII）。"""
    qr_path = os.path.join(str(DATA_DIR), "weclaw_qr.txt")
    try:
        login_running = _weclaw_login_proc is not None and _weclaw_login_proc.poll() is None
        if not os.path.exists(qr_path):
            return jsonify({
                "status": "pending",
                "qr": "",
                "path": qr_path,
                "login_running": login_running,
                "message": "尚未发现新的扫码二维码。请点击“重新扫码登录”启动 WeClaw 内置 login；若已启动，请稍等几秒后刷新。",
            })
        with open(qr_path, "r", encoding="utf-8") as f:
            qr = f.read()
        return jsonify({
            "status": "success" if qr.strip() else "pending",
            "qr": qr,
            "path": qr_path,
            "login_running": login_running,
            "message": "请用微信扫描下方二维码登录。",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_weclaw_status", methods=["GET"])
def proxy_weclaw_status():
    """读取 WeClaw 登录/运行状态。"""
    try:
        resolved_bin, config_path, accounts_dir = _weclaw_settings()
        settings = read_env_all(str(ENV_FILE))
        bin_error = _check_weclaw_bin(resolved_bin)
        accounts = _weclaw_account_files(accounts_dir)
        login_running = _weclaw_login_proc is not None and _weclaw_login_proc.poll() is None
        proxy_host = settings.get("WECLAW_PROXY_HOST") or os.getenv("WECLAW_PROXY_HOST") or "127.0.0.1"
        proxy_port = int(settings.get("WECLAW_PROXY_PORT") or os.getenv("WECLAW_PROXY_PORT") or "51298")
        proxy_running = _is_tcp_port_open(proxy_host, proxy_port)
        bridge_running = _is_tcp_port_open("127.0.0.1", 18011)
        session_expired = _weclaw_session_expired_from_log()
        status_output = ""
        if not bin_error:
            try:
                result = subprocess.run(
                    [resolved_bin, "status"],
                    cwd=str(WORKSPACE_DIR),
                    env=set_subprocess_env(os.environ),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                status_output = ((result.stdout or "") + (result.stderr or "")).strip()
            except Exception as e:
                status_output = f"status failed: {e}"
        session_expired = _weclaw_session_expired(accounts, status_output)
        return jsonify({
            "status": "success",
            "bin": resolved_bin,
            "bin_error": bin_error,
            "config_path": config_path,
            "accounts_dir": accounts_dir,
            "accounts": accounts,
            "has_login": bool(accounts),
            "login_running": login_running,
            "proxy_running": proxy_running,
            "bridge_running": bridge_running,
            "session_expired": session_expired,
            "proxy": f"{proxy_host}:{proxy_port}",
            "weclaw_status": status_output,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _capture_weclaw_login_output(proc: subprocess.Popen, qr_path: str) -> None:
    try:
        with open(qr_path, "w", encoding="utf-8") as f:
            if proc.stdout is not None:
                for line in proc.stdout:
                    f.write(line)
                    f.flush()
            rc = proc.wait()
            f.write(f"\n[weclaw login exited: {rc}]\n")
            if rc == 0:
                restart_flag = os.path.join(str(PID_DIR), "restart_flag")
                with open(restart_flag, "w", encoding="utf-8") as rf:
                    rf.write("restart")
                f.write("[clawcross restart requested]\n")
            f.flush()
    except Exception as e:
        try:
            with open(qr_path, "a", encoding="utf-8") as f:
                f.write(f"\n[failed to capture weclaw login output: {e}]\n")
        except Exception:
            pass


@app.route("/proxy_weclaw_login", methods=["POST"])
@app.route("/proxy_weclaw_reset", methods=["POST"])
def proxy_start_weclaw_login():
    """使用 WeClaw 内置 login 流程进行新登录/重新登录。"""
    global _weclaw_login_proc
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "not logged in"}), 401
    try:
        settings = read_env_all(str(ENV_FILE))
        resolved_bin, _config_path, _accounts_dir = _weclaw_settings()
        bin_error = _check_weclaw_bin(resolved_bin)
        if bin_error:
            return jsonify({"error": bin_error}), 400

        qr_path = os.path.join(str(DATA_DIR), "weclaw_qr.txt")
        os.makedirs(os.path.dirname(qr_path), exist_ok=True)

        with _weclaw_login_lock:
            if _weclaw_login_proc is not None and _weclaw_login_proc.poll() is None:
                return jsonify({
                    "status": "running",
                    "message": "WeClaw 登录流程已在运行，二维码会自动显示。",
                    "path": qr_path,
                    "pid": _weclaw_login_proc.pid,
                })

            if os.path.isfile(qr_path) or os.path.islink(qr_path):
                os.unlink(qr_path)

            _stop_managed_weclaw_proxy(settings)
            time.sleep(1)

            _weclaw_login_proc = subprocess.Popen(
                [resolved_bin, "login"],
                cwd=str(WORKSPACE_DIR),
                env=set_subprocess_env(os.environ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            threading.Thread(
                target=_capture_weclaw_login_output,
                args=(_weclaw_login_proc, qr_path),
                daemon=True,
                name="weclaw-login-capture",
            ).start()

            return jsonify({
                "status": "success",
                "message": "已启动微信扫码登录。请等待二维码出现并扫码。",
                "path": qr_path,
                "pid": _weclaw_login_proc.pid,
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_weclaw_stop", methods=["POST"])
def proxy_stop_weclaw():
    """使用 WeClaw 内置 stop 终止微信渠道。"""
    global _weclaw_login_proc
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "not logged in"}), 401
    try:
        settings = read_env_all(str(ENV_FILE))
        resolved_bin, _config_path, _accounts_dir = _weclaw_settings()
        bin_error = _check_weclaw_bin(resolved_bin)
        if bin_error:
            return jsonify({"error": bin_error}), 400
        with _weclaw_login_lock:
            if _weclaw_login_proc is not None and _weclaw_login_proc.poll() is None:
                _weclaw_login_proc.terminate()
                try:
                    _weclaw_login_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _weclaw_login_proc.kill()
            _weclaw_login_proc = None
        _stop_managed_weclaw_proxy(settings)
        result = subprocess.run(
            [resolved_bin, "stop"],
            cwd=str(WORKSPACE_DIR),
            env=set_subprocess_env(os.environ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return jsonify({
            "status": "success",
            "message": "已发送 WeClaw 终止命令。",
            "output": ((result.stdout or "") + (result.stderr or "")).strip(),
            "returncode": result.returncode,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_restart", methods=["POST"])
def proxy_restart_services():
    """直接写重启信号文件，不经过 mainagent（避免响应返回前进程被杀）"""
    user_id = session.get("user_id", "")
    try:
        restart_flag = os.path.join(str(PID_DIR), "restart_flag")
        with open(restart_flag, "w") as f:
            f.write("restart")
        return jsonify({"status": "success", "message": "重启信号已发送"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_restart_chatbot", methods=["POST"])
def proxy_restart_chatbot():
    """只重启社交媒体机器人，让 channel 配置保存后尽快生效。"""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "not logged in"}), 401
    try:
        restart_flag = os.path.join(str(PID_DIR), "chatbot_restart_flag")
        with open(restart_flag, "w") as f:
            f.write("restart")
        return jsonify({"status": "success", "message": "chatbot 重启信号已发送"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_update_check", methods=["POST"])
def proxy_update_check():
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "not logged in"}), 401
    try:
        data = request.get_json(silent=True) or {}
        body = {
            "user_id": user_id,
            "refresh_remote": bool(data.get("refresh_remote", True)),
        }
        r = requests.post(LOCAL_UPDATE_CHECK_URL, json=body, headers=_internal_auth_headers(), timeout=45)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_update_start", methods=["POST"])
def proxy_update_start():
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "not logged in"}), 401
    try:
        body = {
            "user_id": user_id,
        }
        r = requests.post(LOCAL_UPDATE_START_URL, json=body, headers=_internal_auth_headers(), timeout=20)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_update_status", methods=["POST"])
def proxy_update_status():
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "not logged in"}), 401
    try:
        body = {
            "user_id": user_id,
        }
        r = requests.post(LOCAL_UPDATE_STATUS_URL, json=body, headers=_internal_auth_headers(), timeout=20)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_user_profile", methods=["GET"])
def proxy_user_profile():
    """读取当前用户的用户画像文本（user_profile.txt）"""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "not logged in"}), 401
    profile_path = os.path.join(str(USER_FILES_DIR), user_id, "user_profile.txt")
    profile_text = ""
    try:
        if os.path.isfile(profile_path):
            with open(profile_path, "r", encoding="utf-8") as f:
                profile_text = f.read().strip()
    except Exception:
        pass
    return jsonify({"user_id": user_id, "profile": profile_text})


@app.route("/proxy_user_profile", methods=["PUT"])
def proxy_save_user_profile():
    """保存当前用户的用户画像文本到 user_profile.txt"""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "not logged in"}), 401
    data = request.get_json() or {}
    profile_text = data.get("profile", "")
    profile_dir = os.path.join(str(USER_FILES_DIR), user_id)
    profile_path = os.path.join(profile_dir, "user_profile.txt")
    try:
        os.makedirs(profile_dir, exist_ok=True)
        with open(profile_path, "w", encoding="utf-8") as f:
            f.write(profile_text)
        return jsonify({"ok": True, "profile": profile_text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_openclaw_sessions")
def proxy_openclaw_sessions():
    """Proxy to fetch OpenClaw session list from OASIS server."""

    filter_kw = request.args.get("filter", "")
    try:
        r = requests.get(
            f"{OASIS_BASE_URL}/sessions/openclaw",
            params={"filter": filter_kw},
            timeout=10,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e), "sessions": [], "available": False}), 500


@app.route("/proxy_openclaw_add", methods=["POST"])
def proxy_openclaw_add():
    """Proxy to create a new OpenClaw agent via OASIS server."""

    try:
        r = requests.post(
            f"{OASIS_BASE_URL}/sessions/openclaw/add",
            json=request.get_json(force=True),
            timeout=35,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/proxy_openclaw_default_workspace", methods=["GET"])
def proxy_openclaw_default_workspace():
    """Proxy to get the default OpenClaw workspace parent directory."""

    try:
        r = requests.get(f"{OASIS_BASE_URL}/sessions/openclaw/default-workspace", timeout=10)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/proxy_openclaw_workspace_files", methods=["GET"])
def proxy_openclaw_workspace_files():
    """Proxy to list core files in an OpenClaw agent's workspace."""

    try:
        r = requests.get(
            f"{OASIS_BASE_URL}/sessions/openclaw/workspace-files",
            params={"workspace": request.args.get("workspace", "")},
            timeout=10,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/proxy_openclaw_workspace_file", methods=["GET"])
def proxy_openclaw_workspace_file_read():
    """Proxy to read a single workspace file."""

    try:
        r = requests.get(
            f"{OASIS_BASE_URL}/sessions/openclaw/workspace-file",
            params={"workspace": request.args.get("workspace", ""),
                    "filename": request.args.get("filename", "")},
            timeout=10,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/proxy_openclaw_workspace_file", methods=["POST"])
def proxy_openclaw_workspace_file_save():
    """Proxy to save a workspace file."""

    try:
        r = requests.post(
            f"{OASIS_BASE_URL}/sessions/openclaw/workspace-file",
            json=request.get_json(force=True),
            timeout=15,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/proxy_openclaw_agent_detail", methods=["GET"])
def proxy_openclaw_agent_detail():
    """Proxy to get detailed agent config (skills, tools, profile)."""

    try:
        r = requests.get(
            f"{OASIS_BASE_URL}/sessions/openclaw/agent-detail",
            params={"name": request.args.get("name", "")},
            timeout=15,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

@app.route("/proxy_openclaw_skills", methods=["GET"])
def proxy_openclaw_skills():
    """Proxy to OASIS /sessions/openclaw/skills, passing optional agent name for filtering."""

    try:
        agent_name = request.args.get("agent", "")
        params = {}
        if agent_name:
            params["name"] = agent_name
        r = requests.get(
            f"{OASIS_BASE_URL}/sessions/openclaw/skills",
            params=params,
            timeout=20,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/proxy_openclaw_tool_groups", methods=["GET"])
def proxy_openclaw_tool_groups():
    """Proxy to get available tool groups and profiles."""

    try:
        r = requests.get(f"{OASIS_BASE_URL}/sessions/openclaw/tool-groups", timeout=10)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/proxy_openclaw_update_config", methods=["POST"])
def proxy_openclaw_update_config():
    """Proxy to update an agent's skills/tools config."""

    try:
        r = requests.post(
            f"{OASIS_BASE_URL}/sessions/openclaw/update-config",
            json=request.get_json(force=True),
            timeout=15,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/proxy_openclaw_channels", methods=["GET"])
def proxy_openclaw_channels():
    """Proxy to list all available channels."""

    try:
        r = requests.get(f"{OASIS_BASE_URL}/sessions/openclaw/channels", timeout=15)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/proxy_openclaw_agent_bindings", methods=["GET"])
def proxy_openclaw_agent_bindings():
    """Proxy to get an agent's current channel bindings."""

    try:
        r = requests.get(
            f"{OASIS_BASE_URL}/sessions/openclaw/agent-bindings",
            params={"agent": request.args.get("agent", "")},
            timeout=15,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/proxy_openclaw_agent_bind", methods=["POST"])
def proxy_openclaw_agent_bind():
    """Proxy to bind/unbind a channel to an agent."""

    try:
        r = requests.post(
            f"{OASIS_BASE_URL}/sessions/openclaw/agent-bind",
            json=request.get_json(force=True),
            timeout=15,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/proxy_openclaw_remove", methods=["DELETE"])
def proxy_openclaw_remove():
    """Proxy to delete an OpenClaw agent via OASIS server."""

    try:
        body = request.get_json(force=True)
        agent_name = body.get("name", "")
        if not agent_name:
            return jsonify({"ok": False, "error": "Agent name is required"}), 400
        
        r = requests.get(
            f"{OASIS_BASE_URL}/sessions/openclaw/remove",
            params={"name": agent_name},
            timeout=15,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _list_acpx_tools() -> list[str]:
    """Agent subcommands from `acpx --help` (cached)."""
    return sorted(acpx_agent_command_names())


@app.route("/proxy_acpx_status", methods=["GET"])
def proxy_acpx_status():
    """Return whether the acpx CLI is on PATH (main chat ACP modes)."""
    import shutil

    available = bool(shutil.which("acpx"))
    return jsonify({"available": available, "tools": _list_acpx_tools() if available else []})


# ------------------------------------------------------------------
# Team OpenClaw Snapshot — export/restore agent configs in team folder
# ------------------------------------------------------------------

def _team_storage_dir(user_id: str, team: str) -> str:
    runtime_path = os.path.join(str(USER_FILES_DIR), user_id, "teams", team)
    if app.config.get("TESTING"):
        legacy_path = os.path.join(str(root_dir), "data", "user_files", user_id, "teams", team)
        if os.path.exists(legacy_path):
            return legacy_path
    return runtime_path


def _teams():
    from teams.store import get_team_store
    return get_team_store()


@app.route("/teams/<team_name>/alarms", methods=["GET", "POST"])
def team_alarms(team_name):
    user_id = session.get("user_id", "")
    teams = _teams()
    if not teams.exists(user_id, team_name):
        return jsonify({"error": "Team not found"}), 404

    if request.method == "GET":
        return jsonify({
            "ok": True,
            "team": team_name,
            "alarms": export_team_alarms(teams, user_id=user_id, team=team_name),
            "targets": team_alarm_targets(teams, user_id, team_name),
        })

    body = request.get_json(force=True)
    agent = str(body.get("agent") or "").strip()
    schedule_type = str(body.get("schedule_type") or "cron").strip().lower()
    cron = str(body.get("cron") or "").strip()
    run_at = str(body.get("run_at") or "").strip()
    text = str(body.get("text") or "").strip()
    if schedule_type not in {"cron", "once"}:
        return jsonify({"error": "schedule_type must be cron or once"}), 400
    if not agent or not text or (schedule_type == "cron" and not cron) or (schedule_type == "once" and not run_at):
        return jsonify({"error": "agent, schedule and text are required"}), 400
    if agent not in {t["agent"] for t in team_alarm_targets(teams, user_id, team_name)}:
        return jsonify({"error": "The target is not a member of this team"}), 404
    payload = {"user_id": user_id, "cron": cron, "schedule_type": schedule_type, "run_at": run_at,
               "text": text, "agent": agent, "team": team_name}
    try:
        resp = requests.post(SCHEDULER_TASKS_URL, json=payload, timeout=10)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"error": resp.text}
        return jsonify(data), resp.status_code
    except Exception as e:
        return jsonify({"error": f"Scheduler unavailable: {e}"}), 502


@app.route("/teams/<team_name>/alarms/<task_id>", methods=["DELETE"])
def delete_team_alarm(team_name, task_id):
    user_id = session.get("user_id", "")
    teams = _teams()
    if not teams.exists(user_id, team_name):
        return jsonify({"error": "Team not found"}), 404
    alarms = export_team_alarms(teams, user_id=user_id, team=team_name)
    if not any(str(item.get("task_id") or "") == task_id for item in alarms):
        return jsonify({"error": "Alarm not found in this team"}), 404
    try:
        resp = requests.delete(f"{SCHEDULER_TASKS_URL}/{task_id}", timeout=10)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"status": resp.text}
        return jsonify(data), resp.status_code
    except Exception as e:
        return jsonify({"error": f"Scheduler unavailable: {e}"}), 502


def _openclaw_members(user_id: str, team: str) -> list:
    """``(member, entry)`` for the team's OpenClaw members; ``entry`` in the team-package shape."""
    result = []
    for m in _teams().members(user_id, team):
        if m.agent.driver != "openclaw":
            continue
        entry = {"name": m.role, "tag": "openclaw", "platform": "openclaw",
                 "global_name": m.agent.config.get("global_name", "")}
        entry.update({k: m.extra[k] for k in ("config", "workspace_files") if k in m.extra})
        result.append((m, entry))
    return result


def _fetch_openclaw_snapshot(global_name: str) -> dict:
    r = requests.get(f"{OASIS_BASE_URL}/sessions/openclaw/agent-snapshot", params={"name": global_name}, timeout=30)
    return r.json()


def _keep_openclaw_snapshot(user_id: str, team: str, member, snapshot: dict, *, drop_channels: bool = False) -> None:
    config = dict(snapshot.get("config") or {})
    if drop_channels:
        config.pop("channels", None)
        config.pop("bindings", None)
    _teams().add(user_id, team, member.agent.agent_id, role=member.role, is_lead=member.is_lead,
                 extra={**member.extra, "config": config, "workspace_files": snapshot.get("workspace_files", {})})


@app.route("/team_openclaw_snapshot", methods=["GET"])
def team_openclaw_snapshot_get():
    """The team's OpenClaw members with their saved snapshots. Query: ?team=<name>"""
    user_id = session.get("user_id", "")
    team = request.args.get("team", "")
    if not team:
        return jsonify({"ok": False, "error": "team is required"}), 400
    return jsonify({"ok": True, "agents": [entry for _m, entry in _openclaw_members(user_id, team)]})


@app.route("/team_openclaw_snapshot/export", methods=["POST"])
def team_openclaw_snapshot_export():
    """Save an OpenClaw agent's config and workspace into the team (adding it as a member).
    Body: { "team", "agent_name": OpenClaw agent name, "short_name": role name in the team }
    """
    user_id = session.get("user_id", "")
    body = request.get_json(force=True)
    team = body.get("team", "")
    agent_name = body.get("agent_name", "")
    short_name = body.get("short_name", "") or agent_name
    if not team or not agent_name:
        return jsonify({"ok": False, "error": "team and agent_name are required"}), 400
    teams = _teams()
    if not teams.exists(user_id, team):
        return jsonify({"ok": False, "error": "Team not found"}), 404
    try:
        snapshot = _fetch_openclaw_snapshot(agent_name)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    if not snapshot.get("ok"):
        return jsonify({"ok": False, "error": snapshot.get("error", "Export failed")}), 502

    try:
        agent = teams.member(user_id, team, short_name).agent
    except LookupError:
        agent = teams.agents.create(user_id, name=short_name, driver="openclaw",
                                    config={"platform": "openclaw", "global_name": agent_name, "persona": "", "team": team})
    member = teams.add(user_id, team, agent.agent_id, role=short_name)
    _keep_openclaw_snapshot(user_id, team, member, snapshot)
    file_count = len(snapshot.get("workspace_files", {}))
    return jsonify({
        "ok": True, "short_name": short_name, "agent_name": agent_name, "file_count": file_count, "cron_count": 0,
        "message": f"Exported '{agent_name}' → team snapshot as '{short_name}' ({file_count} files)",
    })


def _refresh_openclaw_snapshots(user_id: str, team: str, *, drop_channels: bool) -> tuple[int, list[str]]:
    refreshed, errors = 0, []
    for member, entry in _openclaw_members(user_id, team):
        name = entry["global_name"]
        try:
            snapshot = _fetch_openclaw_snapshot(name)
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue
        if not snapshot.get("ok"):
            errors.append(f"{name}: {snapshot.get('error', 'failed')}")
            continue
        _keep_openclaw_snapshot(user_id, team, member, snapshot, drop_channels=drop_channels)
        refreshed += 1
    return refreshed, errors


@app.route("/team_openclaw_snapshot/sync_all", methods=["POST"])
def team_openclaw_snapshot_sync_all():
    """Refresh the saved snapshot of every OpenClaw member (channels and bindings left out)."""
    user_id = session.get("user_id", "")
    team = (request.get_json(force=True) or {}).get("team", "")
    if not _teams().exists(user_id, team):
        return jsonify({"ok": False, "error": "Team not found"}), 404
    synced, errors = _refresh_openclaw_snapshots(user_id, team, drop_channels=True)
    resp = {"ok": True, "synced": synced, "agents": [e for _m, e in _openclaw_members(user_id, team)]}
    if errors:
        resp["warnings"] = errors
    return jsonify(resp)


@app.route("/team_openclaw_snapshot/export_all", methods=["POST"])
def team_openclaw_snapshot_export_all():
    """Refresh the saved snapshot of every OpenClaw member."""
    user_id = session.get("user_id", "")
    team = (request.get_json(force=True) or {}).get("team", "")
    if not _teams().exists(user_id, team):
        return jsonify({"ok": False, "error": "Team not found"}), 404
    total = len(_openclaw_members(user_id, team))
    exported, errors = _refresh_openclaw_snapshots(user_id, team, drop_channels=False)
    return jsonify({"ok": True, "exported": exported, "total": total, "errors": errors,
                    "message": f"Exported {exported}/{total} agents to team snapshot"})


def _restore_openclaw_member(user_id: str, team: str, member, entry: dict, ordered: list, target_name: str = "") -> dict:
    """Recreate an OpenClaw agent from its saved snapshot and point the member's agent at it."""
    target_name = target_name or restore_agent_id(team, entry, ordered)
    t_http = time.perf_counter()
    r = requests.post(
        f"{OASIS_BASE_URL}/sessions/openclaw/agent-restore",
        json={"agent_name": target_name, "display_name": restore_display_name(team, entry["name"]),
              "config": entry.get("config", {}), "workspace_files": entry.get("workspace_files", {})},
        timeout=60,
    )
    result = r.json()
    result["agent"] = target_name
    result["client_http_ms"] = round((time.perf_counter() - t_http) * 1000, 2)
    result["status_code"] = r.status_code
    _logger_oc_restore.info("[clawcross-restore] agent=%s status=%s client_http_ms=%s oasis=%s",
                            target_name, r.status_code, result["client_http_ms"], result.get("restore_timing_ms"))
    if result.get("ok"):
        agents = _teams().agents
        agents.update(user_id, member.agent.agent_id, config={**member.agent.config, "global_name": target_name})
    return result


@app.route("/team_openclaw_snapshot/restore", methods=["POST"])
def team_openclaw_snapshot_restore():
    """Recreate one OpenClaw member from the team snapshot.
    Body: { "team", "short_name": role name, "target_agent_name": optional ASCII id }
    """
    user_id = session.get("user_id", "")
    body = request.get_json(force=True)
    team, short_name = body.get("team", ""), body.get("short_name", "")
    if not team or not short_name:
        return jsonify({"ok": False, "error": "team and short_name are required"}), 400
    members = _openclaw_members(user_id, team)
    found = next(((m, e) for m, e in members if e["name"] == short_name), None)
    if not found:
        return jsonify({"ok": False, "error": f"No snapshot found for '{short_name}' in team '{team}'"}), 404
    try:
        result = _restore_openclaw_member(user_id, team, *found, [e for _m, e in members],
                                          body.get("target_agent_name", ""))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify(result), result.pop("status_code")


@app.route("/team_openclaw_snapshot/restore_all", methods=["POST"])
def team_openclaw_snapshot_restore_all():
    """Recreate every OpenClaw member from the team snapshot."""
    user_id = session.get("user_id", "")
    team = (request.get_json(force=True) or {}).get("team", "")
    if not team:
        return jsonify({"ok": False, "error": "team is required"}), 400
    members = _openclaw_members(user_id, team)
    if not members:
        return jsonify({"ok": True, "restored": 0, "message": "No openclaw snapshots found"}), 200
    ordered = [e for _m, e in members]
    restored, errors, rows = 0, [], []
    for member, entry in members:
        try:
            result = _restore_openclaw_member(user_id, team, member, entry, ordered)
        except Exception as e:
            errors.append(f"{entry['name']}: {e}")
            rows.append({"agent": entry["name"], "ok": False, "exception": str(e)})
            continue
        rows.append({"agent": result["agent"], "ok": bool(result.get("ok")), "client_http_ms": result["client_http_ms"],
                     "oasis_timing_ms": result.get("restore_timing_ms"), "errors": result.get("errors")})
        if result.get("ok"):
            restored += 1
        else:
            errors.append(f"{result['agent']}: {result.get('errors', result.get('error', 'failed'))}")
    return jsonify({
        "ok": True, "openclaw_per_agent_restore": rows, "restored": restored, "total": len(members),
        "errors": errors, "message": f"Restored {restored}/{len(members)} agents from team snapshot",
    })


# ──────────────────────────────────────────────────────────────
# Visual Orchestration – proxy endpoints
# ──────────────────────────────────────────────────────────────
import sys as _sys, math as _math, re as _re, yaml as _yaml

# Import expert pool & conversion helpers from visual/main.py
_VISUAL_DIR = os.path.join(root_dir, "visual")
if _VISUAL_DIR not in _sys.path:
    _sys.path.insert(0, _VISUAL_DIR)

try:
    # visual/main.py exports DEFAULT_EXPERTS_LIST / TAG_EMOJI_MAP (not DEFAULT_EXPERTS / TAG_EMOJI)
    from main import (
        DEFAULT_EXPERTS_LIST as _VIS_EXPERTS,
        TAG_EMOJI_MAP as _VIS_TAG_EMOJI,
        layout_to_yaml as _vis_layout_to_yaml,
        _build_llm_prompt as _vis_build_llm_prompt,
        _extract_yaml_from_response as _vis_extract_yaml,
        _validate_generated_yaml as _vis_validate_yaml,
    )
except Exception:
    # Fallback: define minimal versions if visual module unavailable
    _VIS_EXPERTS = []
    _VIS_TAG_EMOJI = {}
    _vis_layout_to_yaml = None
    _vis_build_llm_prompt = None
    _vis_extract_yaml = None
    _vis_validate_yaml = None

# Import YAML→Layout converter (used for on-the-fly layout generation from saved YAML)
try:
    from oasis.layout import yaml_to_layout as _vis_yaml_to_layout
except Exception:
    _vis_yaml_to_layout = None


def _extract_tagged_block(text: str, tag: str) -> str:
    """Extract a tagged payload like <TAG>...</TAG> from LLM output."""
    if not text:
        return ""
    pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
    match = _re.search(pattern, text, _re.DOTALL | _re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _extract_python_from_response(text: str) -> str:
    """Extract workflowpy code from possible wrappers or markdown fences."""
    tagged = _extract_tagged_block(text, "WORKFLOWPY_CODE")
    if tagged:
        return tagged

    fenced = _re.search(r"```(?:python)?\s*\n(.*?)```", text or "", _re.DOTALL | _re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()

    return (text or "").strip()


def _extract_python_explain_from_response(text: str) -> str:
    """Extract workflow explanation from LLM output."""
    tagged = _extract_tagged_block(text, "WORKFLOWPY_EXPLAIN")
    return tagged or ""


def _extract_yaml_explain_from_response(text: str) -> str:
    """Extract YAML workflow explanation from LLM output."""
    tagged = _extract_tagged_block(text, "OASIS_EXPLAIN")
    return tagged or ""


def _yaml_dir(user_id: str, team: str = "") -> str:
    """Return the YAML workflow directory path for a user (team-scoped when team is provided)."""
    if team:
        return os.path.join(str(USER_FILES_DIR), user_id, "teams", team, "oasis", "yaml")
    return os.path.join(str(USER_FILES_DIR), user_id, "oasis", "yaml")


def _python_dir(user_id: str, team: str = "") -> str:
    """Return the workflowpy directory path for a user (team-scoped when team is provided)."""
    if team:
        return os.path.join(str(USER_FILES_DIR), user_id, "teams", team, "oasis", "python")
    return os.path.join(str(USER_FILES_DIR), user_id, "oasis", "python")


def _workflow_mode() -> str:
    mode = (request.args.get("mode") or request.form.get("mode") or "").strip().lower()
    if not mode and request.is_json:
        body = request.get_json(silent=True) or {}
        mode = str(body.get("mode") or "").strip().lower()
    return "python" if mode == "python" else "yaml"


def _workflow_dir(user_id: str, team: str = "", mode: str = "yaml") -> str:
    return _python_dir(user_id, team) if mode == "python" else _yaml_dir(user_id, team)


def _workflow_ext(mode: str = "yaml") -> str:
    return ".py" if mode == "python" else ".yaml"


def _spawn_standalone_python_workflow(
    *,
    user_id: str,
    python_file: str,
    question: str,
    team: str = "",
) -> dict[str, str | int]:
    runs_dir = os.path.join(str(DATA_DIR), "python_workflow_runs")
    os.makedirs(runs_dir, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    log_path = os.path.join(runs_dir, f"{run_id}.log")
    result_path = os.path.join(runs_dir, f"{run_id}.json")
    cmd = [
        WORKFLOW_PYTHON,
        python_file,
        "--user-id",
        user_id or "default",
        "--question",
        question or "",
        "--result-file",
        result_path,
    ]
    if team:
        cmd.extend(["--team", team])

    log_file = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=runtime_working_dir,
        env=set_subprocess_env({
            **os.environ,
            "CLAWCROSS_PROJECT_ROOT": root_dir,
            "CLAWCROSS_PYTHONPATH": WORKFLOW_IMPORT_PATHS,
            "PYTHONPATH": WORKFLOW_IMPORT_PATHS + (
                os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""
            ),
        }),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_file.close()
    return {
        "run_id": run_id,
        "pid": proc.pid,
        "log_file": log_path,
        "result_file": result_path,
        "python_file": python_file,
        "python_executable": WORKFLOW_PYTHON,
    }


@app.route("/proxy_visual/experts", methods=["GET"])
def proxy_visual_experts():
    """Return available expert pool for orchestration canvas (public + user custom + team)."""
    user_id = session.get("user_id", "")
    team = request.args.get("team", "")
    # Fetch full expert list from OASIS server (public + user custom + team)
    all_experts = []
    try:
        params = {"user_id": user_id}
        if team:
            params["team"] = team
        if str(request.args.get("full", "")).strip().lower() in {"1", "true", "yes", "on"}:
            params["full"] = "1"
        r = requests.get(f"{OASIS_BASE_URL}/experts", params=params, timeout=5)
        if r.ok:
            all_experts = r.json().get("experts", [])
    except Exception:
        pass

    # Fallback to static list if OASIS unavailable
    if not all_experts:
        all_experts = [{**e, "source": "public"} for e in _VIS_EXPERTS]

    # Agency 专家按 category 分配不同的 emoji
    _AGENCY_CAT_EMOJI = {
        "design": "🎨", "engineering": "⚙️", "marketing": "📢",
        "product": "📦", "project-management": "📋",
        "spatial-computing": "🥽", "specialized": "🔬",
        "support": "🛡️", "testing": "🧪",
    }

    result = []
    for e in all_experts:
        emoji = _VIS_TAG_EMOJI.get(e.get("tag", ""), "")
        if not emoji:
            # Agency 专家: 根据 category 分配 emoji
            emoji = _AGENCY_CAT_EMOJI.get(e.get("category", ""), "⭐")
        if e.get("source") == "custom":
            emoji = "🛠️"
        if e.get("source") == "team":
            emoji = "👥"
        result.append({
            **e,
            "emoji": emoji,
            "deletable": e.get("deletable", e.get("source") not in {"public", "agency"}),
        })
    return jsonify(result)


@app.route("/proxy_visual/experts/custom", methods=["POST"])
def proxy_visual_add_custom_expert():
    """Add a custom expert via OASIS server (team-scoped when team param provided)."""
    user_id = session.get("user_id", "")
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    team = request.args.get("team", "") or data.get("team", "")
    try:
        r = requests.post(
            f"{OASIS_BASE_URL}/experts/user",
            json={"user_id": user_id, "team": team, **data},
            timeout=10,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_visual/experts/custom/<tag>", methods=["DELETE"])
def proxy_visual_delete_custom_expert(tag):
    """Delete a custom expert via OASIS server (team-scoped when team param provided)."""
    user_id = session.get("user_id", "")
    team = request.args.get("team", "")
    try:
        params = {"user_id": user_id}
        if team:
            params["team"] = team
        r = requests.delete(
            f"{OASIS_BASE_URL}/experts/user/{tag}",
            params=params,
            timeout=10,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_visual/generate-yaml", methods=["POST"])
def proxy_visual_generate_yaml():
    """Convert canvas layout to OASIS YAML (rule-based)."""
    data = request.get_json()
    if not data or not _vis_layout_to_yaml:
        return jsonify({"error": "No data or visual module unavailable"}), 400
    try:
        yaml_out = _vis_layout_to_yaml(data)
        return jsonify({"yaml": yaml_out})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_visual/agent-generate-yaml", methods=["POST"])
def proxy_visual_agent_generate_yaml():
    """Build prompt + call the configured LLM directly for one-shot workflow generation."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    mode = str(data.get("mode") or "yaml").strip().lower()
    mode = "python" if mode == "python" else "yaml"
    guidance = str(data.get("guidance") or "").strip()
    current_code = str(data.get("current_code") or "").rstrip()
    prompt_context = data.get("prompt_context")

    try:
        if mode == "python":
            from oasis.workflow_rules import WORKFLOW_WRITING_RULES
            prompt = (
                "Write a self-bootstrapping standalone Python workflow script for ClawCross/OASIS.\n"
                "Output exactly these two tagged blocks, in this order:\n"
                "<WORKFLOWPY_EXPLAIN>\n"
                "Short workflow explanation here.\n"
                "</WORKFLOWPY_EXPLAIN>\n"
                "<WORKFLOWPY_CODE>\n"
                "# code\n"
                "</WORKFLOWPY_CODE>\n"
                "No text before or after the tags. No prose, no markdown fences, no explanation outside the tags.\n\n"
                "Follow these authoring rules:\n\n"
                f"{WORKFLOW_WRITING_RULES}\n"
                "\nFor WORKFLOWPY_EXPLAIN:\n"
                "- 3-6 short bullet lines.\n"
                "- Summarize what the workflow does, whether it creates an OASIS topic, which agents/personas it uses, and the final output shape.\n"
                "- Do not repeat the code.\n\n"
                f"Task:\n{data.get('question') or 'Implement a useful workflowpy script'}\n"
            )
            if current_code:
                prompt += (
                    "\nExisting workflowpy script to revise or extend:\n"
                    "<EXISTING_WORKFLOWPY>\n"
                    f"{current_code}\n"
                    "</EXISTING_WORKFLOWPY>\n"
                    "Preserve the user's working structure when possible. Improve or complete it instead of rewriting everything unless the current code is fundamentally wrong for the task.\n"
                )
        else:
            base_prompt = _vis_build_llm_prompt(data) if _vis_build_llm_prompt else "Error: visual module unavailable"
            prompt = (
                "You are designing a YAML workflow for the OASIS orchestration engine.\n"
                "Return exactly these two tagged blocks, in this order:\n"
                "<OASIS_EXPLAIN>\n"
                "Short workflow explanation here.\n"
                "</OASIS_EXPLAIN>\n"
                "<OASIS_YAML>\n"
                "version: 2\n"
                "...\n"
                "</OASIS_YAML>\n"
                "Do not output anything before or after those tags.\n"
                "This is a one-shot generation request, not a chat session.\n"
                "The YAML must be directly runnable by OASIS.\n\n"
                "Reference behavior from docs/create_workflow.md and docs/oasis-reference.md.\n"
                "Hard rules:\n"
                "- Use version: 2 graph mode.\n"
                "- Every node in plan must have a unique id.\n"
                "- Nodes with no incoming edges are entry points.\n"
                "- Use regular edges for normal fan-out/fan-in dependencies.\n"
                "- Use conditional_edges only for true runtime branching.\n"
                "- If a node has selector: true, its outgoing branches MUST be declared in selector_edges, not regular edges.\n"
                "- Manual begin/bend nodes are allowed when they make the flow clearer.\n"
                "- Keep the schedule valid for OASIS discussion/execution mode without unsupported fields.\n\n"
                "Design goals:\n"
                "- Keep the workflow compact and practical.\n"
                "- Preserve real branching, review loops, or selectors only when they add value.\n"
                "- Avoid redundant nodes and decorative complexity.\n\n"
                "For OASIS_EXPLAIN:\n"
                "- 3-6 short bullet lines.\n"
                "- Summarize what the workflow does, key stages/branches, which agents/personas are used, and the final output shape.\n"
                "- Do not repeat the YAML.\n\n"
                f"{base_prompt}"
            )
        if prompt_context:
            try:
                prompt += "\n\nCurrent ClawCross workspace context:\n"
                prompt += json.dumps(prompt_context, ensure_ascii=False, indent=2)
                prompt += "\nUse this context when deciding whether to design for public scope vs team scope, which personas are available (persona: <tag>), and which agents exist (agent: <ref>).\n"
            except Exception:
                pass
        if guidance:
            prompt += f"\n\nAdditional user guidance:\n{guidance}\n"

        llm = create_chat_model(
            temperature=0.2 if mode == "yaml" else 0.25,
            max_tokens=4096,
            timeout=90,
        )
        response = llm.invoke(prompt)
        agent_reply = extract_text(response.content if hasattr(response, "content") else str(response)).strip()

        if mode == "yaml":
            tagged_yaml = _extract_tagged_block(agent_reply, "OASIS_YAML")
            agent_yaml = tagged_yaml or (_vis_extract_yaml(agent_reply) if _vis_extract_yaml else agent_reply)
            agent_explain = _extract_yaml_explain_from_response(agent_reply)
        else:
            agent_yaml = _extract_python_from_response(agent_reply)
            agent_explain = _extract_python_explain_from_response(agent_reply)
        validation = (
            _vis_validate_yaml(agent_yaml) if (mode == "yaml" and _vis_validate_yaml)
            else {"valid": bool(str(agent_yaml).strip()), "steps": 0, "step_types": ["python"] if mode == "python" else []}
        )

        user_id = session.get("user_id", "")
        # Auto-save valid workflow to user's oasis directory (team-scoped)
        saved_path = None
        if validation.get("valid"):
            try:
                import time as _time
                team = data.get("team", "")
                yd = _workflow_dir(user_id, team, mode)
                os.makedirs(yd, exist_ok=True)
                fname = data.get("save_name") or f"orch_{_time.strftime('%Y%m%d_%H%M%S')}"
                if mode == "python":
                    if not fname.endswith(".py"):
                        fname += ".py"
                elif not fname.endswith((".yaml", ".yml")):
                    fname += ".yaml"
                fpath = os.path.join(yd, fname)
                with open(fpath, "w", encoding="utf-8") as _yf:
                    if mode == "python":
                        _yf.write(agent_yaml if str(agent_yaml).endswith("\n") else f"{agent_yaml}\n")
                    else:
                        _yf.write(f"# Auto-generated from visual orchestrator\n{agent_yaml}")
                saved_path = fname
            except Exception as save_err:
                saved_path = f"save_error: {save_err}"

        return jsonify({"prompt": prompt, "mode": mode, "agent_yaml": agent_yaml, "agent_explain": agent_explain, "agent_reply_raw": agent_reply, "validation": validation, "saved_file": saved_path})

    except ValueError as e:
        return jsonify({"prompt": "", "error": str(e), "agent_yaml": None}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_visual/save-layout", methods=["POST"])
def proxy_visual_save_layout():
    """Save a workflow in either YAML(canvas) or workflowpy mode."""
    user_id = session.get("user_id", "")
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    mode = str(data.get("mode") or "yaml").strip().lower()
    mode = "python" if mode == "python" else "yaml"
    name = data.get("name", "untitled")
    safe = "".join(c for c in name if c.isalnum() or c in "-_ ").strip() or "untitled"
    team = data.get("team", "")
    yd = _workflow_dir(user_id, team, mode)
    os.makedirs(yd, exist_ok=True)
    ext = _workflow_ext(mode)
    fpath = os.path.join(yd, f"{safe}{ext}")
    if mode == "python":
        content = str(data.get("content") or "")
        if not content.strip():
            return jsonify({"error": "No python workflow content"}), 400
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content if content.endswith("\n") else content + "\n")
        return jsonify({"saved": True, "mode": mode, "file": os.path.basename(fpath), "path": fpath, "name": safe})

    if not _vis_layout_to_yaml:
        return jsonify({"error": "Layout-to-YAML converter unavailable"}), 500
    try:
        yaml_out = _vis_layout_to_yaml(data)
    except Exception as e:
        return jsonify({"error": f"YAML conversion failed: {e}"}), 500
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(f"# Saved from visual orchestrator\n{yaml_out}")
    return jsonify({"saved": True, "mode": mode, "file": os.path.basename(fpath), "path": fpath, "name": safe})


@app.route("/proxy_visual/import-python-template", methods=["POST"])
def proxy_visual_import_python_template():
    user_id = session.get("user_id", "")
    data = request.get_json(silent=True) or {}
    template_name = str(data.get("template") or "").strip().lower()
    team = str(data.get("team") or "").strip()
    template_map = {
        "sequential": "team_all_agents_sequential.py",
        "parallel": "team_all_agents_parallel.py",
    }
    filename = template_map.get(template_name)
    if not filename:
        return jsonify({"error": "Unknown template"}), 400
    template_path = os.path.join(root_dir, "oasis", "workflow_templates", filename)
    if not os.path.isfile(template_path):
        return jsonify({"error": "Template file not found"}), 404
    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()
    yd = _workflow_dir(user_id, team, "python")
    os.makedirs(yd, exist_ok=True)
    workflow_name = filename[:-3]
    fpath = os.path.join(yd, filename)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content if content.endswith("\n") else content + "\n")
    return jsonify({
        "saved": True,
        "mode": "python",
        "template": template_name,
        "file": filename,
        "path": fpath,
        "name": workflow_name,
    })


@app.route("/proxy_visual/load-layouts", methods=["GET"])
def proxy_visual_load_layouts():
    """List saved workflows for the selected mode (team-scoped)."""
    user_id = session.get("user_id", "")
    team = request.args.get("team", "")
    mode = _workflow_mode()
    yd = _workflow_dir(user_id, team, mode)
    if not os.path.isdir(yd):
        return jsonify([])
    if mode == "python":
        return jsonify([f[:-3] for f in sorted(os.listdir(yd)) if f.endswith(".py")])
    return jsonify([f.replace('.yaml', '').replace('.yml', '') for f in sorted(os.listdir(yd)) if f.endswith((".yaml", ".yml"))])


@app.route("/proxy_visual/run-python-workflow", methods=["POST"])
def proxy_visual_run_python_workflow():
    """Run a saved python workflow through the standalone runner used by current frontends.

    The script itself decides whether to auto-create and conclude an OASIS topic.
    """
    user_id = session.get("user_id", "")
    data = request.get_json(silent=True) or {}
    python_file = str(data.get("python_file") or "").strip()
    question = str(data.get("question") or "").strip()
    team = str(data.get("team") or "").strip()
    if not python_file:
        return jsonify({"error": "Missing python_file"}), 400
    if not question:
        return jsonify({"error": "Missing question"}), 400
    try:
        payload = _spawn_standalone_python_workflow(
            user_id=user_id,
            python_file=python_file,
            question=question,
            team=team,
        )
        return jsonify({
            "started": True,
            "mode": "standalone_python",
            **payload,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_visual/load-layout/<name>", methods=["GET"])
def proxy_visual_load_layout(name):
    """Load a workflow by mode."""
    user_id = session.get("user_id", "")
    safe = "".join(c for c in name if c.isalnum() or c in "-_ ").strip()
    team = request.args.get("team", "")
    mode = _workflow_mode()
    yd = _workflow_dir(user_id, team, mode)
    if mode == "python":
        fpath = os.path.join(yd, f"{safe}.py")
        if not os.path.isfile(fpath):
            return jsonify({"error": "Not found"}), 404
        with open(fpath, "r", encoding="utf-8") as f:
            return jsonify({"name": safe, "mode": mode, "content": f.read(), "path": fpath})

    if not _vis_yaml_to_layout:
        return jsonify({"error": "YAML-to-layout converter unavailable"}), 500
    fpath = os.path.join(yd, f"{safe}.yaml")
    if not os.path.isfile(fpath):
        fpath = os.path.join(yd, f"{safe}.yml")
    if not os.path.isfile(fpath):
        return jsonify({"error": "Not found"}), 404
    with open(fpath, "r", encoding="utf-8") as f:
        yaml_content = f.read()
    try:
        layout = _vis_yaml_to_layout(yaml_content)
        layout["name"] = safe
        return jsonify(layout)
    except Exception as e:
        return jsonify({"error": f"YAML-to-layout conversion failed: {e}"}), 500


@app.route("/proxy_visual/load-yaml-raw/<name>", methods=["GET"])
def proxy_visual_load_yaml_raw(name):
    """Return raw YAML text for a saved workflow."""
    user_id = session.get("user_id", "")
    safe = "".join(c for c in name if c.isalnum() or c in "-_ ").strip()
    team = request.args.get("team", "")
    yd = _yaml_dir(user_id, team)
    fpath = os.path.join(yd, f"{safe}.yaml")
    if not os.path.isfile(fpath):
        fpath = os.path.join(yd, f"{safe}.yml")
    if not os.path.isfile(fpath):
        return jsonify({"error": "Not found"}), 404
    with open(fpath, "r", encoding="utf-8") as f:
        return jsonify({"yaml": f.read(), "path": fpath, "name": safe, "mode": "yaml"})


@app.route("/proxy_visual/delete-layout/<name>", methods=["DELETE"])
def proxy_visual_delete_layout(name):
    """Delete a saved workflow for the selected mode."""
    user_id = session.get("user_id", "")
    safe = "".join(c for c in name if c.isalnum() or c in "-_ ").strip()
    team = request.args.get("team", "")
    mode = _workflow_mode()
    yd = _workflow_dir(user_id, team, mode)
    if mode == "python":
        fpath = os.path.join(yd, f"{safe}.py")
    else:
        fpath = os.path.join(yd, f"{safe}.yaml")
        if not os.path.isfile(fpath):
            fpath = os.path.join(yd, f"{safe}.yml")
    if os.path.isfile(fpath):
        os.remove(fpath)
        return jsonify({"deleted": True})
    return jsonify({"error": "Not found"}), 404


@app.route("/proxy_visual/upload-yaml", methods=["POST"])
def proxy_visual_upload_yaml():
    """Upload a YAML file: save it and convert to layout data for canvas import."""
    user_id = session.get("user_id", "")
    data = request.get_json()
    if not data or not data.get("content"):
        return jsonify({"error": "No content"}), 400

    filename = data.get("filename", "upload.yaml")
    content = data["content"]

    # Validate YAML syntax
    try:
        _yaml.safe_load(content)
    except Exception as e:
        return jsonify({"error": f"Invalid YAML: {e}"}), 400

    # Save the file (team-scoped)
    safe = "".join(c for c in os.path.splitext(filename)[0] if c.isalnum() or c in "-_ ").strip() or "upload"
    team = data.get("team", "")
    yd = _yaml_dir(user_id, team)
    os.makedirs(yd, exist_ok=True)
    fpath = os.path.join(yd, f"{safe}.yaml")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content)

    # Convert to layout data if converter available
    layout = None
    if _vis_yaml_to_layout:
        try:
            layout = _vis_yaml_to_layout(content)
            layout["name"] = safe
        except Exception:
            layout = None

    return jsonify({"saved": True, "name": safe, "layout": layout})


@app.route("/proxy_visual/sessions-status", methods=["GET"])
def proxy_visual_sessions_status():
    """Return all sessions with their running status for the canvas display."""
    user_id = session.get("user_id", "")
    try:
        r = requests.post(LOCAL_SESSIONS_URL, json={"user_id": user_id}, headers=_internal_auth_headers(), timeout=10)
        if r.status_code != 200:
            return jsonify([])
        sessions_data = r.json()
        return jsonify(sessions_data if isinstance(sessions_data, list) else [])
    except Exception:
        return jsonify([])


# ===== Tunnel Control API =====

import subprocess as _subprocess
import signal as _signal
import platform as _platform

_IS_WINDOWS = _platform.system().lower() == "windows"
_TUNNEL_PIDFILE = os.path.join(str(PID_DIR), "tunnel.pid")
_TUNNEL_SCRIPT = os.path.join(root_dir, "scripts", "tunnel.py")


def _tunnel_running() -> tuple[bool, int | None]:
    """Check if tunnel is running, return (running, pid).
    Cleans up stale PID file if the process is dead."""
    if not os.path.isfile(_TUNNEL_PIDFILE):
        return False, None
    try:
        with open(_TUNNEL_PIDFILE) as f:
            pid = int(f.read().strip())
        if _IS_WINDOWS:
            # Windows: use tasklist to check if PID exists
            result = _subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            if str(pid) not in result.stdout:
                raise OSError("Process not found")
        else:
            os.kill(pid, 0)  # check if alive (Unix)
        return True, pid
    except (ValueError, OSError):
        # PID file exists but process is dead — clean up stale PID file
        try:
            os.remove(_TUNNEL_PIDFILE)
        except OSError:
            pass
        return False, None


def _get_public_domain() -> str:
    """Read PUBLIC_DOMAIN from .env."""
    from dotenv import dotenv_values
    vals = dotenv_values(str(ENV_FILE))
    domain = vals.get("PUBLIC_DOMAIN", "")
    if domain == "wait to set":
        return ""
    return domain


@app.route("/proxy_tunnel/status", methods=["GET"])
def proxy_tunnel_status():
    """Return tunnel running status and public URL."""
    running, pid = _tunnel_running()
    domain = _get_public_domain() if running else ""
    return jsonify({"running": running, "pid": pid, "public_domain": domain})


@app.route("/proxy_tunnel/start", methods=["POST"])
def proxy_tunnel_start():
    """Start cloudflare tunnel in background."""
    user_id = session.get("user_id", "")

    running, pid = _tunnel_running()
    if running:
        domain = _get_public_domain()
        return jsonify({"status": "already_running", "pid": pid, "public_domain": domain})

    # Start tunnel.py in background
    log_dir = str(LOGS_DIR)
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "tunnel.log")

    try:
        import sys as _sys
        log_fh = open(log_file, "w")
        popen_kwargs = dict(
            stdout=log_fh,
            stderr=_subprocess.STDOUT,
            cwd=runtime_working_dir,
            env=set_subprocess_env(os.environ),
        )
        if _IS_WINDOWS:
            popen_kwargs["creationflags"] = (
                _subprocess.CREATE_NO_WINDOW | _subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            popen_kwargs["start_new_session"] = True

        proc = _subprocess.Popen(
            [_sys.executable, _TUNNEL_SCRIPT],
            **popen_kwargs,
        )
        with open(_TUNNEL_PIDFILE, "w") as f:
            f.write(str(proc.pid))
        return jsonify({"status": "started", "pid": proc.pid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_tunnel/stop", methods=["POST"])
def proxy_tunnel_stop():
    """Stop the running tunnel."""
    user_id = session.get("user_id", "")

    running, pid = _tunnel_running()
    if not running:
        # Clean up stale pidfile
        if os.path.isfile(_TUNNEL_PIDFILE):
            os.remove(_TUNNEL_PIDFILE)
        return jsonify({"status": "not_running"})

    try:
        if _IS_WINDOWS:
            # Windows: use taskkill to terminate process tree
            _subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
            )
        else:
            os.kill(pid, _signal.SIGTERM)
            # Wait briefly for exit
            import time as _time
            for _ in range(10):
                try:
                    os.kill(pid, 0)
                    _time.sleep(0.5)
                except OSError:
                    break
            else:
                # Force kill
                try:
                    os.kill(pid, _signal.SIGKILL)
                except OSError:
                    pass
    except OSError:
        pass

    if os.path.isfile(_TUNNEL_PIDFILE):
        os.remove(_TUNNEL_PIDFILE)

    # Clear PUBLIC_DOMAIN from .env so stale URLs are not used
    _clear_public_domain()

    return jsonify({"status": "stopped"})


def _clear_public_domain():
    """Clear PUBLIC_DOMAIN in config/.env after tunnel stops."""
    env_file = str(ENV_FILE)
    if not os.path.exists(env_file):
        return
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        new_lines = []
        for line in lines:
            if line.strip().startswith("PUBLIC_DOMAIN="):
                new_lines.append("PUBLIC_DOMAIN=\n")
            else:
                new_lines.append(line)
        with open(env_file, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        # Also clear from current process env
        os.environ.pop("PUBLIC_DOMAIN", None)
    except Exception:
        pass


# ------------------------------------------------------------------
# Teams: folders hold a team's assets; its members are agents (see teams.store)
# ------------------------------------------------------------------

@app.route("/teams", methods=["GET"])
def list_teams():
    """List all team names for the current user."""
    return jsonify({"status": "success", "teams": _teams().teams(session.get("user_id", ""))})


@app.route("/teams", methods=["POST"])
def create_team():
    """Create a new, empty team."""
    from teams.store import valid_team_name

    user_id = session.get("user_id", "")
    team = str((request.get_json(force=True) or {}).get("team", "")).strip()
    if not valid_team_name(team):
        return jsonify({"error": "Invalid team name"}), 400
    if _teams().exists(user_id, team):
        return jsonify({"error": "Team already exists"}), 400
    _teams().create(user_id, team)
    return jsonify({"success": True, "message": f"Team '{team}' created"})


@app.route("/api/team-presets", methods=["GET"])
def list_builtin_team_presets():
    return jsonify({"ok": True, "presets": list_team_presets()})


@app.route("/api/team-presets/install", methods=["POST"])
def install_builtin_team_preset():
    user_id = session.get("user_id", "")
    body = request.get_json(force=True) or {}
    preset_id = str(body.get("preset_id") or "").strip()
    team_name = str(body.get("team") or "").strip()

    if not preset_id:
        return jsonify({"ok": False, "error": "preset_id is required"}), 400
    if not team_name:
        return jsonify({"ok": False, "error": "team is required"}), 400
    if "/" in team_name or "\\" in team_name or team_name.startswith("."):
        return jsonify({"ok": False, "error": "Invalid team name"}), 400

    try:
        result = install_team_preset(
            user_id=user_id,
            team_name=team_name,
            preset_id=preset_id,
        )
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, **result})


@app.route("/teams/<team_name>", methods=["PATCH"])
def rename_team(team_name):
    """Rename a team (its folder and memberships)."""
    from teams.store import valid_team_name

    user_id = session.get("user_id", "")
    new_name = str((request.get_json(force=True) or {}).get("new_name") or "").strip()
    teams = _teams()
    if not valid_team_name(new_name):
        return jsonify({"error": "Invalid new team name"}), 400
    if not teams.exists(user_id, team_name):
        return jsonify({"error": "Team not found"}), 404
    if new_name == team_name:
        return jsonify({"success": True, "team": new_name, "message": "unchanged"})
    try:
        teams.rename(user_id, team_name, new_name)
    except FileExistsError:
        return jsonify({"error": "Target team name already exists"}), 400
    return jsonify({"success": True, "team": new_name, "message": f"Team renamed to '{new_name}'"})


@app.route("/teams/<team_name>", methods=["DELETE"])
def delete_team(team_name):
    """Delete a team: its folder goes, its agents stay."""
    user_id = session.get("user_id", "")
    teams = _teams()
    if not teams.exists(user_id, team_name):
        return jsonify({"error": "Team not found"}), 404
    teams.delete(user_id, team_name)
    return jsonify({"success": True, "message": f"Team '{team_name}' deleted"})


def _team_settings_path(user_id: str, team_name: str) -> str:
    return os.path.join(str(USER_FILES_DIR), user_id, "teams", team_name, "team_settings.json")


def _team_settings_load(user_id: str, team_name: str) -> dict:
    """Load team settings. Returns default empty dict if not found."""
    path = _team_settings_path(user_id, team_name)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _team_settings_save(user_id: str, team_name: str, settings: dict) -> None:
    """Save team settings."""
    team_dir = os.path.join(str(USER_FILES_DIR), user_id, "teams", team_name)
    os.makedirs(team_dir, exist_ok=True)
    path = _team_settings_path(user_id, team_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


@app.route("/teams/<team_name>/settings", methods=["GET"])
def get_team_settings(team_name):
    """Get team-level settings including fallback_agent."""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    if "/" in team_name or "\\" in team_name or team_name.startswith("."):
        return jsonify({"error": "Invalid team name"}), 400
    team_dir = os.path.join(str(USER_FILES_DIR), user_id, "teams", team_name)
    if not os.path.exists(team_dir):
        return jsonify({"error": "Team not found"}), 404
    settings = _team_settings_load(user_id, team_name)
    return jsonify({"ok": True, "settings": settings})


@app.route("/teams/<team_name>/settings", methods=["PUT"])
def update_team_settings(team_name):
    """Update team-level settings (e.g., fallback_agent)."""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    if "/" in team_name or "\\" in team_name or team_name.startswith("."):
        return jsonify({"error": "Invalid team name"}), 400
    team_dir = os.path.join(str(USER_FILES_DIR), user_id, "teams", team_name)
    if not os.path.exists(team_dir):
        return jsonify({"error": "Team not found"}), 404
    body = request.get_json(force=True) or {}
    settings = _team_settings_load(user_id, team_name)
    # Only update provided fields
    if "fallback_agent" in body:
        settings["fallback_agent"] = str(body["fallback_agent"] or "").strip()
    if "fallback_agent_config" in body:
        settings["fallback_agent_config"] = body["fallback_agent_config"]
    _team_settings_save(user_id, team_name, settings)
    return jsonify({"ok": True, "settings": settings})


@app.route("/teams/<team_name>/skills", methods=["GET"])
def get_team_skills(team_name):
    """List team-scoped and shared managed skills for a team."""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    if "/" in team_name or "\\" in team_name or team_name.startswith("."):
        return jsonify({"error": "Invalid team name"}), 400
    team_dir = os.path.join(str(USER_FILES_DIR), user_id, "teams", team_name)
    if not os.path.exists(team_dir):
        return jsonify({"error": "Team not found"}), 404

    from webot.skills import list_skills

    return jsonify({
        "ok": True,
        "team": team_name,
        "skills": {
            "team": list_skills(user_id, team=team_name),
            "personal": list_skills(user_id),
        },
    })


def _safe_extract_skill_zip(zip_path: Path, target_dir: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"Unsafe zip entry: {info.filename}")
            if info.file_size > 5 * 1024 * 1024:
                raise ValueError(f"Zip entry too large: {info.filename}")
            target = target_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _copy_direct_skill_dirs(extracted_dir: Path, user_id: str, team_name: str) -> dict[str, Any]:
    from webot.skills import _parse_frontmatter, _rebuild_index, _security_scan, _skills_dir, _team_skills_dir, _validate_name

    candidates: list[Path] = []
    root_skill = extracted_dir / "SKILL.md"
    if root_skill.is_file():
        candidates.append(extracted_dir)
    for skill_md in extracted_dir.rglob("SKILL.md"):
        if skill_md.parent == extracted_dir:
            continue
        if "__MACOSX" in skill_md.parts:
            continue
        candidates.append(skill_md.parent)

    imported: list[str] = []
    skipped: list[str] = []
    target_root = _team_skills_dir(user_id, team_name) if team_name else _skills_dir(user_id)
    seen: set[Path] = set()
    for skill_dir in candidates:
        resolved = skill_dir.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        skill_md = skill_dir / "SKILL.md"
        try:
            content = skill_md.read_text(encoding="utf-8")
            if len(content.encode("utf-8")) > 100 * 1024:
                skipped.append(f"{skill_dir.name}: SKILL.md too large")
                continue
            violations = _security_scan(content)
            if violations:
                skipped.append(f"{skill_dir.name}: security scan failed")
                continue
            meta, _body = _parse_frontmatter(content)
            raw_name = str(meta.get("name") or skill_dir.name or "skill").strip().lower()
            normalized = re.sub(r"[^a-z0-9._-]+", "-", raw_name).strip(".-_")[:64]
            skill_name = _validate_name(normalized or "skill")
        except Exception as exc:
            skipped.append(f"{skill_dir.name}: {exc}")
            continue

        for support_file in skill_dir.rglob("*"):
            if support_file.is_file() and support_file.stat().st_size > 5 * 1024 * 1024:
                skipped.append(f"{skill_name}: file too large")
                break
        else:
            target = target_root / skill_name
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(skill_dir, target)
            imported.append(skill_name)

    if imported:
        _rebuild_index(user_id, team=team_name)
    return {"imported": imported, "skipped": skipped}


@app.route("/skills", methods=["GET"])
def get_global_skills():
    """List user-level shared managed skills."""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    from webot.skills import list_skills

    return jsonify({
        "ok": True,
        "skills": {
            "personal": list_skills(user_id),
        },
    })


@app.route("/skills/import-zip", methods=["POST"])
def import_global_skill_zip():
    """Import managed skills from a Skill ZIP into the user-level shared scope."""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "file is required"}), 400
    if not file.filename.lower().endswith(".zip"):
        return jsonify({"error": "Only .zip files are supported"}), 400

    with tempfile.TemporaryDirectory(prefix="clawcross_global_skill_zip_") as tmp:
        tmp_dir = Path(tmp)
        zip_path = tmp_dir / "skill.zip"
        file.save(zip_path)
        extract_dir = tmp_dir / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            _safe_extract_skill_zip(zip_path, extract_dir)
            direct = _copy_direct_skill_dirs(extract_dir, user_id, "")
        except zipfile.BadZipFile:
            return jsonify({"error": "Invalid zip file"}), 400
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    if not direct.get("imported"):
        return jsonify({"error": "No SKILL.md found in zip", "direct": direct}), 400

    return jsonify({"ok": True, "direct": direct})


@app.route("/skills/<skill_name>", methods=["GET"])
def get_global_skill_detail(skill_name):
    """Get a single user-level shared managed skill detail."""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    from webot.skills import get_skill

    skill = get_skill(user_id, name=skill_name)
    if not skill:
        return jsonify({"error": f"Skill '{skill_name}' not found"}), 404
    return jsonify({"ok": True, "skill": skill})


@app.route("/skills/<skill_name>", methods=["PUT"])
def update_global_skill_detail(skill_name):
    """Update a single user-level shared managed skill's SKILL.md content."""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(force=True) or {}
    content = str(body.get("content") or "")
    if not content.strip():
        return jsonify({"error": "content is required"}), 400

    from webot.skills import edit_skill, get_skill

    result = edit_skill(user_id, name=skill_name, content=content)
    if not result.get("success"):
        return jsonify({"error": result.get("error") or "Update failed"}), 400

    return jsonify({"ok": True, "skill": get_skill(user_id, name=skill_name), "result": result})


@app.route("/skills/<skill_name>", methods=["POST"])
def create_global_skill_detail(skill_name):
    """Create a new user-level shared managed skill from SKILL.md content."""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(force=True) or {}
    content = str(body.get("content") or "")
    if not content.strip():
        return jsonify({"error": "content is required"}), 400

    from webot.skills import create_skill, get_skill

    result = create_skill(
        user_id, name=skill_name, content=content,
        category=str(body.get("category") or ""),
    )
    if not result.get("success"):
        err = result.get("error") or "Create failed"
        code = 409 if "already exists" in err else 400
        return jsonify({"error": err}), code
    return jsonify({"ok": True, "skill": get_skill(user_id, name=skill_name), "result": result})


@app.route("/skills/<skill_name>", methods=["DELETE"])
def delete_global_skill_detail(skill_name):
    """Delete a user-level shared managed skill."""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    from webot.skills import delete_skill

    result = delete_skill(user_id, name=skill_name)
    if not result.get("success"):
        return jsonify({"error": result.get("error") or "Delete failed"}), 400
    return jsonify({"ok": True, "result": result})


@app.route("/teams/<team_name>/skills/import-zip", methods=["POST"])
def import_team_skill_zip(team_name):
    """Import managed skills from a Skill ZIP into the selected team."""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    if "/" in team_name or "\\" in team_name or team_name.startswith("."):
        return jsonify({"error": "Invalid team name"}), 400
    team_dir = os.path.join(str(USER_FILES_DIR), user_id, "teams", team_name)
    if not os.path.exists(team_dir):
        return jsonify({"error": "Team not found"}), 404

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "file is required"}), 400
    if not file.filename.lower().endswith(".zip"):
        return jsonify({"error": "Only .zip files are supported"}), 400

    with tempfile.TemporaryDirectory(prefix="clawcross_skill_zip_") as tmp:
        tmp_dir = Path(tmp)
        zip_path = tmp_dir / "skill.zip"
        file.save(zip_path)
        extract_dir = tmp_dir / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            _safe_extract_skill_zip(zip_path, extract_dir)
            direct = _copy_direct_skill_dirs(extract_dir, user_id, team_name)
        except zipfile.BadZipFile:
            return jsonify({"error": "Invalid zip file"}), 400
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    imported_count = len(direct.get("imported") or [])
    if imported_count == 0:
        return jsonify({
            "error": "No SKILL.md found in zip",
            "direct": direct,
        }), 400

    return jsonify({
        "ok": True,
        "team": team_name,
        "restored": {
            "restored_user_skill_dirs": 0,
            "restored_user_files": 0,
            "restored_team_skill_dirs": 0,
            "restored_team_files": 0,
        },
        "direct": direct,
    })


@app.route("/teams/<team_name>/skills/<skill_name>", methods=["GET"])
def get_team_skill_detail(team_name, skill_name):
    """Get a single team/shared managed skill detail including SKILL.md content."""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    if "/" in team_name or "\\" in team_name or team_name.startswith("."):
        return jsonify({"error": "Invalid team name"}), 400
    team_dir = os.path.join(str(USER_FILES_DIR), user_id, "teams", team_name)
    if not os.path.exists(team_dir):
        return jsonify({"error": "Team not found"}), 404

    scope = str(request.args.get("scope") or "team").strip().lower()
    if scope not in {"team", "personal"}:
        return jsonify({"error": "Invalid scope"}), 400

    from webot.skills import get_skill

    skill = get_skill(user_id, name=skill_name, team=team_name if scope == "team" else "")
    if not skill:
        return jsonify({"error": f"Skill '{skill_name}' not found"}), 404

    return jsonify({"ok": True, "skill": skill})


@app.route("/teams/<team_name>/skills/<skill_name>", methods=["PUT"])
def update_team_skill_detail(team_name, skill_name):
    """Update a single managed skill's SKILL.md content."""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    if "/" in team_name or "\\" in team_name or team_name.startswith("."):
        return jsonify({"error": "Invalid team name"}), 400
    team_dir = os.path.join(str(USER_FILES_DIR), user_id, "teams", team_name)
    if not os.path.exists(team_dir):
        return jsonify({"error": "Team not found"}), 404

    scope = str(request.args.get("scope") or "team").strip().lower()
    if scope not in {"team", "personal"}:
        return jsonify({"error": "Invalid scope"}), 400

    body = request.get_json(force=True) or {}
    content = str(body.get("content") or "")
    if not content.strip():
        return jsonify({"error": "content is required"}), 400

    from webot.skills import edit_skill, get_skill

    result = edit_skill(user_id, name=skill_name, content=content, team=team_name if scope == "team" else "")
    if not result.get("success"):
        return jsonify({"error": result.get("error") or "Update failed"}), 400

    skill = get_skill(user_id, name=skill_name, team=team_name if scope == "team" else "")
    return jsonify({"ok": True, "skill": skill, "result": result})


@app.route("/teams/<team_name>/skills/<skill_name>", methods=["POST"])
def create_team_skill_detail(team_name, skill_name):
    """Create a new team/shared managed skill from SKILL.md content."""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    if "/" in team_name or "\\" in team_name or team_name.startswith("."):
        return jsonify({"error": "Invalid team name"}), 400
    team_dir = os.path.join(str(USER_FILES_DIR), user_id, "teams", team_name)
    if not os.path.exists(team_dir):
        return jsonify({"error": "Team not found"}), 404

    scope = str(request.args.get("scope") or "team").strip().lower()
    if scope not in {"team", "personal"}:
        return jsonify({"error": "Invalid scope"}), 400

    body = request.get_json(force=True) or {}
    content = str(body.get("content") or "")
    if not content.strip():
        return jsonify({"error": "content is required"}), 400

    from webot.skills import create_skill, get_skill

    result = create_skill(
        user_id, name=skill_name, content=content,
        category=str(body.get("category") or ""),
        team=team_name if scope == "team" else "",
    )
    if not result.get("success"):
        err = result.get("error") or "Create failed"
        code = 409 if "already exists" in err else 400
        return jsonify({"error": err}), code
    skill = get_skill(user_id, name=skill_name, team=team_name if scope == "team" else "")
    return jsonify({"ok": True, "skill": skill, "result": result})


@app.route("/teams/<team_name>/skills/<skill_name>", methods=["DELETE"])
def delete_team_skill_detail(team_name, skill_name):
    """Delete a team/shared managed skill from the selected scope."""
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    if "/" in team_name or "\\" in team_name or team_name.startswith("."):
        return jsonify({"error": "Invalid team name"}), 400
    team_dir = os.path.join(str(USER_FILES_DIR), user_id, "teams", team_name)
    if not os.path.exists(team_dir):
        return jsonify({"error": "Team not found"}), 404

    scope = str(request.args.get("scope") or "team").strip().lower()
    if scope not in {"team", "personal"}:
        return jsonify({"error": "Invalid scope"}), 400

    from webot.skills import delete_skill

    result = delete_skill(user_id, name=skill_name, team=team_name if scope == "team" else "")
    if not result.get("success"):
        return jsonify({"error": result.get("error") or "Delete failed"}), 400

    return jsonify({"ok": True, "result": result})


# ------------------------------------------------------------------
# Scheduled tasks from the mobile message center: any of the user's agents,
# or one team's members (?team=…)
# ------------------------------------------------------------------

def _scheduler_tasks() -> list:
    resp = requests.get(SCHEDULER_TASKS_URL, timeout=10)
    tasks = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else []
    return tasks if isinstance(tasks, list) else []


@app.route("/mobile_alarms", methods=["GET", "POST"])
def mobile_alarms():
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "not logged in"}), 401
    teams = _teams()
    team = str((request.args if request.method == "GET" else request.get_json(force=True) or {}).get("team") or "").strip()
    if team == "__public__":
        team = ""
    if team and not teams.exists(user_id, team):
        return jsonify({"error": "Team not found"}), 404
    targets = (team_alarm_targets(teams, user_id, team) if team else [
        {"agent": a.agent_id, "target_name": a.name, "label": f"{a.name} · {a.platform}"} for a in teams.agents.list(user_id)
    ])

    if request.method == "GET":
        if team:
            return jsonify({"ok": True, "team": team, "alarms": export_team_alarms(teams, user_id=user_id, team=team),
                            "targets": targets})
        try:
            tasks = [t for t in _scheduler_tasks() if isinstance(t, dict) and t.get("user_id") == user_id]
        except Exception as e:
            return jsonify({"error": f"Scheduler unavailable: {e}"}), 502
        names = {t["agent"]: t["target_name"] for t in targets}
        alarms = [{**t, "target_name": names.get(t.get("agent", ""), t.get("agent", ""))} for t in tasks]
        return jsonify({"ok": True, "alarms": alarms, "targets": targets})

    body = request.get_json(force=True) or {}
    agent = str(body.get("agent") or "").strip()
    schedule_type = str(body.get("schedule_type") or "cron").strip().lower()
    cron = str(body.get("cron") or "").strip()
    run_at = str(body.get("run_at") or "").strip()
    text = str(body.get("text") or "").strip()
    if schedule_type not in {"cron", "once"}:
        return jsonify({"error": "schedule_type must be cron or once"}), 400
    if not agent or not text or (schedule_type == "cron" and not cron) or (schedule_type == "once" and not run_at):
        return jsonify({"error": "agent, schedule and text are required"}), 400
    if agent not in {t["agent"] for t in targets}:
        return jsonify({"error": "Unknown target agent"}), 404
    payload = {"user_id": user_id, "cron": cron, "schedule_type": schedule_type, "run_at": run_at, "text": text,
               "agent": agent, "team": team}
    try:
        resp = requests.post(SCHEDULER_TASKS_URL, json=payload, timeout=10)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"error": resp.text}
        return jsonify(data), resp.status_code
    except Exception as e:
        return jsonify({"error": f"Scheduler unavailable: {e}"}), 502


@app.route("/mobile_alarms/<task_id>", methods=["DELETE"])
def delete_mobile_alarm(task_id):
    user_id = session.get("user_id", "")
    if not user_id:
        return jsonify({"error": "not logged in"}), 401
    try:
        target = next((t for t in _scheduler_tasks() if isinstance(t, dict) and t.get("task_id") == task_id), None)
        if not target:
            return jsonify({"error": "Alarm not found"}), 404
        if target.get("user_id") != user_id:
            return jsonify({"error": "Forbidden"}), 403
        resp = requests.delete(f"{SCHEDULER_TASKS_URL}/{task_id}", timeout=10)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"error": resp.text}
        return jsonify(data), resp.status_code
    except Exception as e:
        return jsonify({"error": f"Scheduler unavailable: {e}"}), 502


# ------------------------------------------------------------------
# Team-specific expert CRUD  (stored in {team_dir}/oasis_experts.json)
# ------------------------------------------------------------------

def _team_experts_path(user_id: str, team_name: str) -> str:
    """Return the oasis_experts.json path for a team."""
    return os.path.join(str(USER_FILES_DIR), user_id, "teams", team_name, "oasis_experts.json")


def _team_experts_load(user_id: str, team_name: str) -> list:
    """Load team-specific experts list."""
    path = _team_experts_path(user_id, team_name)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _team_experts_save(user_id: str, team_name: str, experts: list) -> None:
    """Save team-specific experts list."""
    path = _team_experts_path(user_id, team_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(experts, f, ensure_ascii=False, indent=2)


@app.route("/teams/<team_name>/experts", methods=["GET"])
def get_team_experts(team_name):
    """List all custom experts defined for this team."""
    user_id = session.get("user_id", "")
    if "/" in team_name or "\\" in team_name or team_name.startswith("."):
        return jsonify({"error": "Invalid team name"}), 400
    team_dir = os.path.join(str(USER_FILES_DIR), user_id, "teams", team_name)
    if not os.path.exists(team_dir):
        return jsonify({"error": "Team not found"}), 404
    experts = _team_experts_load(user_id, team_name)
    return jsonify({"status": "success", "team": team_name, "experts": experts})


@app.route("/teams/<team_name>/experts", methods=["POST"])
def add_team_expert(team_name):
    """Add a custom expert to this team's expert pool."""
    user_id = session.get("user_id", "")
    if "/" in team_name or "\\" in team_name or team_name.startswith("."):
        return jsonify({"error": "Invalid team name"}), 400
    team_dir = os.path.join(str(USER_FILES_DIR), user_id, "teams", team_name)
    if not os.path.exists(team_dir):
        return jsonify({"error": "Team not found"}), 404

    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    tag = (body.get("tag") or "").strip()
    persona = (body.get("persona") or "").strip()
    if not name or not tag or not persona:
        return jsonify({"error": "name, tag, and persona are required"}), 400

    expert = {
        "name": name,
        "tag": tag,
        "persona": persona,
        "temperature": float(body.get("temperature", 0.7)),
    }
    # Preserve optional fields
    for key in ("name_en", "category", "description"):
        if body.get(key):
            expert[key] = body[key]

    experts = _team_experts_load(user_id, team_name)
    if any(e["tag"] == tag for e in experts):
        return jsonify({"error": f"Tag \"{tag}\" already exists in this team"}), 409
    experts.append(expert)
    _team_experts_save(user_id, team_name, experts)
    return jsonify({"status": "success", "expert": expert})


@app.route("/teams/<team_name>/experts/<tag>", methods=["PUT"])
def update_team_expert(team_name, tag):
    """Update an existing team expert by tag."""
    user_id = session.get("user_id", "")
    if "/" in team_name or "\\" in team_name or team_name.startswith("."):
        return jsonify({"error": "Invalid team name"}), 400
    team_dir = os.path.join(str(USER_FILES_DIR), user_id, "teams", team_name)
    if not os.path.exists(team_dir):
        return jsonify({"error": "Team not found"}), 404

    body = request.get_json(force=True)
    experts = _team_experts_load(user_id, team_name)
    for i, e in enumerate(experts):
        if e["tag"] == tag:
            name = (body.get("name") or e["name"]).strip()
            persona = (body.get("persona") or e["persona"]).strip()
            if not name or not persona:
                return jsonify({"error": "name and persona cannot be empty"}), 400
            updated = {
                "name": name,
                "tag": tag,
                "persona": persona,
                "temperature": float(body.get("temperature", e.get("temperature", 0.7))),
            }
            for key in ("name_en", "category", "description"):
                val = body.get(key, e.get(key))
                if val:
                    updated[key] = val
            experts[i] = updated
            _team_experts_save(user_id, team_name, experts)
            return jsonify({"status": "success", "expert": updated})
    return jsonify({"error": f"Expert tag \"{tag}\" not found"}), 404


@app.route("/teams/<team_name>/experts/<tag>", methods=["DELETE"])
def delete_team_expert(team_name, tag):
    """Delete a team expert by tag."""
    user_id = session.get("user_id", "")
    if "/" in team_name or "\\" in team_name or team_name.startswith("."):
        return jsonify({"error": "Invalid team name"}), 400
    team_dir = os.path.join(str(USER_FILES_DIR), user_id, "teams", team_name)
    if not os.path.exists(team_dir):
        return jsonify({"error": "Team not found"}), 404

    experts = _team_experts_load(user_id, team_name)
    for i, e in enumerate(experts):
        if e["tag"] == tag:
            deleted = experts.pop(i)
            _team_experts_save(user_id, team_name, experts)
            return jsonify({"status": "success", "deleted": deleted})
    return jsonify({"error": f"Expert tag \"{tag}\" not found"}), 404


@app.route("/teams/<team_name>/generate-from-workflow", methods=["POST"])
def generate_team_from_workflow(team_name):
    """Add a canvas's participants to a team.

    Body: {"nodes": [{"type": "persona", "tag", "name", "persona"?, "temperature"}
                     | {"type": "agent", "agent": ref, "name"}],
           "create_if_missing": bool, "resolutions": {tag: "skip" | "overwrite"}}
    Persona nodes go into the team's persona library (their text taken from the
    library when not given); agent nodes join as members.
    """
    user_id = session.get("user_id", "")
    teams = _teams()
    body = request.get_json(force=True) or {}
    if not teams.exists(user_id, team_name):
        if not body.get("create_if_missing"):
            return jsonify({"error": "Team not found"}), 404
        teams.create(user_id, team_name)
    resolutions = body.get("resolutions", {})
    added, skipped, overwritten, errors = [], [], [], []

    experts = _team_experts_load(user_id, team_name)
    experts_dirty = False
    library: dict[str, dict] = {}
    if any(n.get("type") == "persona" and not n.get("persona") for n in body.get("nodes", [])):
        try:
            r = requests.get(f"{OASIS_BASE_URL}/experts", params={"user_id": user_id, "full": "1"}, timeout=5)
            library = {e["tag"]: e for e in r.json().get("experts", [])} if r.ok else {}
        except (requests.RequestException, ValueError):
            library = {}
    for node in body.get("nodes", []):
        kind = node.get("type", "")
        name = str(node.get("name") or "").strip()
        if kind == "persona":
            tag = str(node.get("tag") or "").strip()
            if not tag:
                continue
            conflict = any(e["tag"] == tag for e in experts)
            resolution = resolutions.get(tag, "skip") if conflict else "add"
            if resolution == "skip":
                skipped.append({"tag": tag, "name": name, "type": kind})
                continue
            experts = [e for e in experts if e["tag"] != tag]
            persona = node.get("persona") or library.get(tag, {}).get("persona", "")
            experts.append({"name": name, "tag": tag, "persona": persona,
                            "temperature": node.get("temperature", 0.7)})
            (overwritten if conflict else added).append({"tag": tag, "name": name, "type": kind})
            experts_dirty = True
        elif kind == "agent":
            ref = str(node.get("agent") or name).strip()
            agent = teams.agents.get(user_id, ref) or teams.address(user_id, ref)
            if agent is None:
                errors.append(f"{ref}: no such agent")
                continue
            if any(m.agent.agent_id == agent.agent_id for m in teams.members(user_id, team_name)):
                skipped.append({"name": name or agent.name, "type": kind})
                continue
            teams.add(user_id, team_name, agent.agent_id, role=name or agent.name)
            added.append({"name": name or agent.name, "type": kind})
    if experts_dirty:
        _team_experts_save(user_id, team_name, experts)
    return jsonify({"team": team_name, "added": added, "skipped": skipped, "overwritten": overwritten,
                    "errors": errors})


@app.route("/teams/snapshot/preview", methods=["POST"])
def preview_team_snapshot():
    """Preview what would be exported in a team snapshot.
    Returns a JSON summary of all exportable sections:
    agents (internal_agents), personas (oasis_experts),
    skills (openclaw workspace/managed skills), cron jobs, workflows (yaml/python files),
    and preset metadata files.
    """
    user_id = session.get("user_id", "")

    body = request.get_json(force=True)
    team = body.get("team", "")

    if not team:
        return jsonify({"error": "team is required"}), 400

    team_dir = os.path.join(str(USER_FILES_DIR), user_id, "teams", team)

    if not os.path.exists(team_dir):
        return jsonify({"error": "Team not found"}), 404

    result = {"team": team, "sections": {}}

    from teams.manifest import export_entries

    teams = _teams()
    internal_entries, ext_data = export_entries(teams, user_id, team, portable=False)

    # --- 1. agents (the team's WeBot members) ---
    agents_info = [{"name": e.get("name", "?"), "tag": e.get("tag", "")} for e in internal_entries]
    result["sections"]["agents"] = {"count": len(agents_info), "items": agents_info}

    # --- 2. personas (oasis_experts.json) ---
    experts_path = os.path.join(team_dir, "oasis_experts.json")
    personas_info = []
    if os.path.exists(experts_path):
        try:
            with open(experts_path, "r", encoding="utf-8") as f:
                experts_list = json.load(f)
            if isinstance(experts_list, list):
                for item in experts_list:
                    personas_info.append({
                        "tag": item.get("tag", "?"),
                        "name": item.get("name", item.get("tag", "?")),
                    })
        except Exception:
            pass
    result["sections"]["personas"] = {"count": len(personas_info), "items": personas_info}

    # --- 3. external agents (the team's other members) ---
    external_agents_info = [
        {"name": e.get("name", "?"), "tag": e.get("tag", ""), "platform": e.get("platform", ""),
         "global_name": e.get("global_name", "")}
        for e in ext_data
    ]
    openclaw_info = [{"name": e["name"], "global_name": e.get("global_name", "")}
                     for e in ext_data if e.get("platform") == "openclaw"]
    result["sections"]["external_agents"] = {"count": len(external_agents_info), "items": external_agents_info}

    # --- 4. skills (workspace + managed) for openclaw agents + ClawCross managed skills ---
    skills_info = []
    managed_skills_info = []  # [{"name": ..., "source": "managed"}]
    if isinstance(ext_data, list):
        managed_collected = False
        for entry in ext_data:
            if entry.get("platform") != "openclaw":
                continue
            short_name = entry.get("name", "")
            agent_name = entry.get("global_name", "") or short_name
            try:
                r = requests.get(
                    f"{OASIS_BASE_URL}/sessions/openclaw/agent-detail",
                    params={"name": agent_name},
                    timeout=15,
                )
                resp = r.json()
                if not resp.get("ok"):
                    continue
                agent_detail = resp.get("agent", {})
                workspace = agent_detail.get("workspace", "")

                # List workspace skill directory names only (no file contents)
                ws_skill_names = []
                if workspace:
                    ws_skills_dir = os.path.join(os.path.expanduser(workspace), "skills")
                    if os.path.isdir(ws_skills_dir):
                        for item in sorted(os.listdir(ws_skills_dir)):
                            if os.path.isdir(os.path.join(ws_skills_dir, item)):
                                ws_skill_names.append(item)

                skills_info.append({
                    "agent": short_name,
                    "skills": ws_skill_names,
                })

                # Collect managed skills (once)
                if not managed_collected:
                    user_skills = resp.get("user_skills", [])
                    for sk in user_skills:
                        if sk.get("source") == "managed" and sk.get("name"):
                            managed_skills_info.append({"name": sk["name"]})
                    managed_collected = True
            except Exception:
                skills_info.append({"agent": short_name, "skills": []})
    result["sections"]["skills"] = {
        "agents": openclaw_info,
        "details": skills_info,
        "managed": managed_skills_info,
    }
    try:
        from webot.skills import list_skills as list_managed_skills

        result["sections"]["skills"]["clawcross_personal"] = [
            {"name": item.get("name", ""), "category": item.get("category", "")}
            for item in list_managed_skills(user_id)
        ]
        result["sections"]["skills"]["clawcross_team"] = [
            {"name": item.get("name", ""), "category": item.get("category", "")}
            for item in list_managed_skills(user_id, team=team)
        ]
    except Exception:
        result["sections"]["skills"]["clawcross_personal"] = []
        result["sections"]["skills"]["clawcross_team"] = []

    # --- 5. scheduled tasks of the team's members ---
    alarm_items = export_team_alarms(teams, user_id=user_id, team=team)
    cron_info = {}
    for item in alarm_items:
        target = item.get("target_name") or "unknown"
        cron_info.setdefault(target, {"count": 0, "items": []})
        cron_info[target]["count"] += 1
        cron_info[target]["items"].append({
            "name": item.get("task_id", ""),
            "schedule": item.get("run_at", "") if item.get("schedule_type") == "once" else item.get("cron", ""),
            "schedule_type": item.get("schedule_type", "cron"),
            "text": item.get("text", ""),
        })
    result["sections"]["cron"] = cron_info

    # --- 6. workflows (yaml + python files) ---
    workflow_files = []
    oasis_dir = os.path.join(team_dir, "oasis")
    for root_path, dirs, files in os.walk(oasis_dir):
        for file in files:
            if file.endswith(('.yaml', '.yml', '.py')):
                file_path = os.path.join(root_path, file)
                rel_path = os.path.relpath(file_path, team_dir)
                workflow_files.append(rel_path)
    result["sections"]["workflows"] = {"count": len(workflow_files), "items": workflow_files}

    # --- 7. preset metadata ---
    preset_files = []
    for filename in ("clawcross_preset_manifest.json", "clawcross_preset_source_map.json"):
        if os.path.isfile(os.path.join(team_dir, filename)):
            preset_files.append(filename)
    result["sections"]["preset_metadata"] = {"count": len(preset_files), "items": preset_files}

    return jsonify(result)


@app.route("/teams/snapshot/download", methods=["POST"])
def download_team_snapshot():
    """Download a compressed snapshot of the team's data.
    Includes: the members as internal_agents.json / external_agents.json (without this
             machine's sessions, global_names or api keys), oasis_experts.json, preset
             metadata, all .yaml/.yml/.py workflow files, and skill folders (workspace +
             managed) for each OpenClaw member.
    Supports selective export via 'include' field in request body.
    Simple mode: {"team": "...", "include": {"agents": true, "personas": true, "skills": true, "cron": true, "workflows": true}}
    Granular mode for skills — select per-agent and per-skill:
      {"include": {"skills": {"AgentName": ["Skill1", "Skill2"], "Agent2": true}}}
    If 'include' is omitted, all sections are exported.
    """
    user_id = session.get("user_id", "")
    
    body = request.get_json(force=True)
    team = body.get("team", "")
    include = body.get("include", None)  # Selective export filter
    
    if not team:
        return jsonify({"error": "team is required"}), 400
    
    # Build include flags — default all True if 'include' not provided
    def _inc(section):
        if include is None:
            return True
        val = include.get(section, False)
        # For skills: value can be True/False or a dict for granular selection
        if isinstance(val, dict):
            return True  # dict means granular selection — section is included
        return bool(val)

    def _inc_agent_skill(agent_short_name, skill_name=None):
        """Check if a specific agent's skill should be included.
        include.skills can be: True, False, or {"AgentName": true/[skill_list], ...}
        """
        if include is None:
            return True
        skills_val = include.get("skills", False)
        if skills_val is True:
            return True
        if skills_val is False or not skills_val:
            return False
        if isinstance(skills_val, dict):
            agent_val = skills_val.get(agent_short_name, False)
            if agent_val is True:
                return True
            if agent_val is False or not agent_val:
                return False
            if isinstance(agent_val, list):
                if skill_name is None:
                    return True  # agent is selected, check skills individually
                return skill_name in agent_val
        return True

    def _inc_managed_skill(scope: str, skill_name: str | None = None) -> bool:
        if include is None:
            return True
        skills_val = include.get("skills", False)
        if skills_val is True:
            return True
        if skills_val is False or not skills_val:
            return False
        if isinstance(skills_val, dict):
            key = "_managed_team" if scope == "team" else "_managed_personal"
            scope_val = skills_val.get(key, False)
            if scope_val is True:
                return True
            if scope_val is False or not scope_val:
                return False
            if isinstance(scope_val, list):
                if skill_name is None:
                    return True
                return skill_name in scope_val
        return True

    team_dir = os.path.join(str(USER_FILES_DIR), user_id, "teams", team)
    
    if not os.path.exists(team_dir):
        return jsonify({"error": "Team not found"}), 404
    
    import zipfile
    import io
    import shutil
    from datetime import datetime
    
    try:
        # Create a zip file in memory
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # The team's members in the package format, without this machine's
            # runtimes (sessions / global_names) or secrets.
            from teams.manifest import dumps, export_entries

            teams = _teams()
            portable_internal, portable_external = export_entries(teams, user_id, team, portable=True)
            if _inc("agents"):
                zipf.writestr("internal_agents.json", dumps(portable_internal))
            if portable_external and (_inc("external_agents") or _inc("skills") or _inc("cron")):
                zipf.writestr("external_agents.json", dumps(portable_external))
            experts_file = os.path.join(team_dir, "oasis_experts.json")
            if _inc("personas") and os.path.exists(experts_file):
                zipf.write(experts_file, "oasis_experts.json")

            # Add preset metadata files
            for preset_file in ("clawcross_preset_manifest.json", "clawcross_preset_source_map.json"):
                preset_path = os.path.join(team_dir, preset_file)
                if os.path.isfile(preset_path):
                    zipf.write(preset_path, preset_file)

            # Add workflow files (.yaml/.yml/.py)
            if _inc("workflows"):
                oasis_dir = os.path.join(team_dir, "oasis")
                for root_path, dirs, files in os.walk(oasis_dir):
                    for file in files:
                        if file.endswith(('.yaml', '.yml', '.py')):
                            file_path = os.path.join(root_path, file)
                            # Use relative path inside zip
                            rel_path = os.path.relpath(file_path, team_dir)
                            zipf.write(file_path, rel_path)

            # --- Add skill folders for each OpenClaw member, and the members' scheduled tasks ---
            managed_skills_added = False

            if _inc("skills"):
                ext_data = export_entries(teams, user_id, team, portable=False)[1]
                if isinstance(ext_data, list):
                    for entry in ext_data:
                        if entry.get("platform") != "openclaw":
                            continue
                        short_name = entry.get("name", "")
                        agent_name = entry.get("global_name", "") or short_name
                        
                        # Fetch agent detail from oasis server to get workspace path and user_skills
                        if _inc("skills") and _inc_agent_skill(short_name):
                            try:
                                r = requests.get(
                                    f"{OASIS_BASE_URL}/sessions/openclaw/agent-detail",
                                    params={"name": agent_name},
                                    timeout=15,
                                )
                                resp = r.json()
                                if resp.get("ok"):
                                    agent_detail = resp.get("agent", {})
                                    workspace = agent_detail.get("workspace", "")

                                    # 1. Add workspace skills to zip: skills/openclaw_agents/{short_name}/
                                    if workspace:
                                        ws_skills_dir = os.path.join(os.path.expanduser(workspace), "skills")
                                        if os.path.isdir(ws_skills_dir):
                                            for item in os.listdir(ws_skills_dir):
                                                item_path = os.path.join(ws_skills_dir, item)
                                                if not os.path.isdir(item_path):
                                                    continue
                                                # Check if this specific skill is selected
                                                if not _inc_agent_skill(short_name, item):
                                                    continue
                                                for dirpath, dirnames, filenames in os.walk(item_path):
                                                    for fname in filenames:
                                                        abs_path = os.path.join(dirpath, fname)
                                                        rel_in_skills = os.path.relpath(abs_path, ws_skills_dir)
                                                        zip_path = os.path.join(
                                                            SNAPSHOT_OPENCLAW_AGENTS_DIR,
                                                            short_name,
                                                            rel_in_skills,
                                                        )
                                                        zipf.write(abs_path, zip_path)

                                    # 2. Add managed skills to zip: skills/openclaw_managed/ (once)
                                    if not managed_skills_added:
                                        user_skills = resp.get("user_skills", [])
                                        for sk in user_skills:
                                            if sk.get("source") == "managed" and sk.get("path"):
                                                sk_path = sk["path"]
                                                if os.path.isdir(sk_path):
                                                    for dirpath, dirnames, filenames in os.walk(sk_path):
                                                        for fname in filenames:
                                                            abs_path = os.path.join(dirpath, fname)
                                                            rel_in_sk = os.path.relpath(abs_path, sk_path)
                                                            zip_path = os.path.join(SNAPSHOT_OPENCLAW_MANAGED_DIR, sk["name"], rel_in_sk)
                                                            zipf.write(abs_path, zip_path)
                                    managed_skills_added = True

                            except Exception:
                                pass
            # Save internal scheduler alarms to zip: cron_jobs.json
            if _inc("cron"):
                alarm_jobs_data = export_team_alarms(teams, user_id=user_id, team=team)
                if alarm_jobs_data:
                    zipf.writestr("cron_jobs.json", json.dumps(alarm_jobs_data, ensure_ascii=False, indent=2))

            # Add ClawCross managed skills (personal + team scoped).
            if _inc("skills"):
                personal_names = None
                team_names = None
                if include is not None and isinstance(include.get("skills"), dict):
                    skills_val = include.get("skills", {})
                    personal_raw = skills_val.get("_managed_personal", False)
                    team_raw = skills_val.get("_managed_team", False)
                    personal_names = {str(item) for item in personal_raw} if isinstance(personal_raw, list) else None
                    team_names = {str(item) for item in team_raw} if isinstance(team_raw, list) else None

                if _inc_managed_skill("personal"):
                    add_user_skills_to_zip(zipf, user_id, selected_names=personal_names)
                if _inc_managed_skill("team"):
                    add_team_skills_to_zip(zipf, user_id, team, selected_names=team_names)
        
        zip_buffer.seek(0)
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"team_{team}_snapshot_{timestamp}.zip"
        
        return Response(
            zip_buffer.read(),
            mimetype='application/zip',
            headers={
                'Content-Disposition': build_attachment_content_disposition(filename)
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/teams/snapshot/upload", methods=["POST"])
def upload_team_snapshot():
    """Upload and restore a team snapshot from a zip file.
    Extracts the assets to the team folder and makes the listed members agents of this user.
    """
    user_id = session.get("user_id", "")
    
    # Get team name from form data
    team = request.form.get("team", "")
    if not team:
        return jsonify({"error": "team is required"}), 400
    
    # Validate team name
    if "/" in team or "\\" in team or team.startswith("."):
        return jsonify({"error": "Invalid team name"}), 400
    
    # Check for uploaded file
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    if not file.filename.endswith('.zip'):
        return jsonify({"error": "File must be a .zip file"}), 400
    
    team_dir = os.path.join(str(USER_FILES_DIR), user_id, "teams", team)
    
    # Create team directory if it doesn't exist
    os.makedirs(team_dir, exist_ok=True)
    
    import zipfile
    import tempfile
    import shutil

    temp_path = None
    skills_extract_root = None
    try:
        # Save uploaded file to temp location
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as temp_file:
            file.save(temp_file.name)
            temp_path = temp_file.name

        skills_extract_root = tempfile.mkdtemp(prefix="team_snapshot_skills_")

        # Extract zip file
        with zipfile.ZipFile(temp_path, 'r') as zip_ref:
            # Validate zip contents (only allow safe file types)
            for file_info in zip_ref.infolist():
                filename = file_info.filename
                # Skip directories and absolute paths
                if filename.endswith('/') or filename.startswith('/'):
                    continue
                if ".." in Path(filename).parts:
                    return jsonify({"error": f"Invalid path in zip: {filename}"}), 400
                # Allow files inside the unified skills/ tree, plus legacy managed-skill
                # roots for backward-compatible imports. For other files, allow team
                # metadata plus workflow formats (json/yaml/python).
                is_skill_payload = (
                    filename.startswith('skills/')
                    or filename.startswith('clawcross_user_skills/')
                    or filename.startswith('clawcross_team_skills/')
                )
                if not is_skill_payload:
                    if not filename.endswith(('.json', '.yaml', '.yml', '.py')):
                        return jsonify({"error": f"Invalid file type in zip: {filename}"}), 400
                # Preserve relative directory structure from zip
                target_root = skills_extract_root if is_skill_payload else team_dir
                target_path = os.path.join(target_root, filename)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with zip_ref.open(file_info) as source, open(target_path, 'wb') as target:
                    target.write(source.read())
        
        # Clean up temp file
        os.unlink(temp_path)
        temp_path = None
        
        # The package lists the team's members; this machine gives them runtimes.
        from teams.manifest import EXTERNAL_FILE, INTERNAL_FILE, import_entries, read_folder

        internal_entries, openclaw_data = read_folder(Path(team_dir))

        # External members get runtime names here; OpenClaw ones are recreated first.
        openclaw_restored = 0
        openclaw_errors = []
        openclaw_restore_details = []

        # Load team settings for fallback agent
        team_settings = _team_settings_load(user_id, team)
        fallback_agent = team_settings.get("fallback_agent", "")
        fallback_agent_config = team_settings.get("fallback_agent_config", {})

        # Paths for extracted skill folders
        extracted_skills_dir = os.path.join(skills_extract_root, "skills")
        extracted_openclaw_agents_dir = os.path.join(skills_extract_root, SNAPSHOT_OPENCLAW_AGENTS_DIR)
        managed_skills_src = os.path.join(skills_extract_root, SNAPSHOT_OPENCLAW_MANAGED_DIR)
        legacy_managed_skills_src = os.path.join(extracted_skills_dir, "_managed")

        if openclaw_data:
            try:
                external_ordered = [e for e in openclaw_data if isinstance(e, dict)]
                for agent_entry in external_ordered:
                    if agent_entry.get("platform") == "openclaw":
                        continue
                    agent_entry["global_name"] = restore_external_global_name(
                        team, agent_entry, external_ordered
                    )

                oc_ordered = openclaw_entries_ordered(openclaw_data)
                for agent_entry in openclaw_data:
                    if agent_entry.get("platform") != "openclaw":
                        continue
                    short_name = agent_entry.get("name", "")
                    agent_snapshot = agent_entry
                    target_name = restore_agent_id(team, agent_entry, oc_ordered)
                    display_oc_name = restore_display_name(team, short_name)
                    try:
                        t_http = time.perf_counter()
                        r = requests.post(
                            f"{OASIS_BASE_URL}/sessions/openclaw/agent-restore",
                            json={
                                "agent_name": target_name,
                                "display_name": display_oc_name,
                                "config": agent_snapshot.get("config", {}),
                                "workspace_files": agent_snapshot.get("workspace_files", {}),
                            },
                            timeout=60,
                        )
                        client_http_ms = round((time.perf_counter() - t_http) * 1000, 2)
                        result = r.json()
                        skills_ms = None
                        if result.get("ok"):
                            openclaw_restored += 1
                            # Update global_name in JSON to reflect the new agent name
                            agent_entry["global_name"] = target_name
                            # --- Restore skill folders into agent workspace ---
                            workspace = result.get("workspace", "")
                            if workspace:
                                t_skills = time.perf_counter()
                                ws_skills_target = os.path.join(os.path.expanduser(workspace), "skills")
                                agent_skills_src = os.path.join(extracted_openclaw_agents_dir, short_name)
                                legacy_agent_skills_src = os.path.join(extracted_skills_dir, short_name)

                                # Clear existing skills folder and rebuild
                                if os.path.isdir(ws_skills_target):
                                    shutil.rmtree(ws_skills_target)
                                os.makedirs(ws_skills_target, exist_ok=True)

                                # Copy workspace skills from snapshot
                                skills_source_dir = agent_skills_src if os.path.isdir(agent_skills_src) else legacy_agent_skills_src
                                if os.path.isdir(skills_source_dir):
                                    for item in os.listdir(skills_source_dir):
                                        src_item = os.path.join(skills_source_dir, item)
                                        dst_item = os.path.join(ws_skills_target, item)
                                        if os.path.isdir(src_item):
                                            shutil.copytree(src_item, dst_item, dirs_exist_ok=True)
                                        else:
                                            shutil.copy2(src_item, dst_item)

                                # Merge managed skills into the same workspace skills folder
                                managed_source_dir = managed_skills_src if os.path.isdir(managed_skills_src) else legacy_managed_skills_src
                                if os.path.isdir(managed_source_dir):
                                    for item in os.listdir(managed_source_dir):
                                        src_item = os.path.join(managed_source_dir, item)
                                        dst_item = os.path.join(ws_skills_target, item)
                                        if os.path.isdir(src_item) and not os.path.exists(dst_item):
                                            shutil.copytree(src_item, dst_item)
                                        elif os.path.isdir(src_item):
                                            shutil.copytree(src_item, dst_item, dirs_exist_ok=True)
                                skills_ms = round((time.perf_counter() - t_skills) * 1000, 2)
                        else:
                            # Restore failed — try fallback agent if configured
                            if fallback_agent and fallback_agent_config:
                                _logger_oc_restore.info(
                                    "[clawcross-restore] route=snapshot_upload agent=%s restore failed, trying fallback=%s",
                                    target_name,
                                    fallback_agent,
                                )
                                try:
                                    t_fb = time.perf_counter()
                                    fb_r = requests.post(
                                        f"{OASIS_BASE_URL}/sessions/openclaw/agent-restore",
                                        json={
                                            "agent_name": fallback_agent,
                                            "display_name": display_oc_name,
                                            "config": fallback_agent_config,
                                            "workspace_files": {},
                                        },
                                        timeout=60,
                                    )
                                    fb_result = fb_r.json()
                                    fb_ms = round((time.perf_counter() - t_fb) * 1000, 2)
                                    if fb_result.get("ok"):
                                        result = fb_result
                                        result["fallback_used"] = True
                                        openclaw_restored += 1
                                        agent_entry["global_name"] = fallback_agent
                                        agent_entry["_fallback"] = True
                                        _logger_oc_restore.info(
                                            "[clawcross-restore] route=snapshot_upload agent=%s fallback=ok agent=%s",
                                            target_name,
                                            fallback_agent,
                                        )
                                    else:
                                        openclaw_errors.append(
                                            f"{target_name}: {result.get('errors', result.get('error', 'failed'))} (fallback={fallback_agent} also failed)"
                                        )
                                except Exception as fb_e:
                                    openclaw_errors.append(
                                        f"{target_name}: {result.get('errors', result.get('error', 'failed'))} (fallback exception: {fb_e})"
                                    )
                            else:
                                openclaw_errors.append(
                                    f"{target_name}: {result.get('errors', result.get('error', 'failed'))}"
                                )
                        detail = {
                            "agent": target_name,
                            "ok": bool(result.get("ok")),
                            "client_http_ms": client_http_ms,
                            "skills_copy_ms": skills_ms,
                            "oasis_timing_ms": result.get("restore_timing_ms"),
                            "errors": result.get("errors"),
                        }
                        openclaw_restore_details.append(detail)
                        _logger_oc_restore.info(
                            "[clawcross-restore] route=snapshot_upload agent=%s client_http_ms=%s skills_copy_ms=%s oasis=%s ok=%s",
                            target_name,
                            client_http_ms,
                            skills_ms,
                            result.get("restore_timing_ms"),
                            result.get("ok"),
                        )
                    except Exception as e:
                        openclaw_errors.append(f"{target_name}: {e}")
                        openclaw_restore_details.append(
                            {"agent": target_name, "ok": False, "exception": str(e)}
                        )
                        _logger_oc_restore.warning(
                            "[clawcross-restore] route=snapshot_upload agent=%s failed: %s",
                            target_name,
                            e,
                        )
            except Exception as e:
                openclaw_errors.append(f"Failed to restore external agents: {e}")

        # An OpenClaw member that could not be recreated has no runtime to join with.
        restorable = [e for e in openclaw_data if isinstance(e, dict) and e.get("global_name")]
        members = import_entries(_teams(), user_id, team, internal_entries, restorable)
        for name in (INTERNAL_FILE, EXTERNAL_FILE):
            (Path(team_dir) / name).unlink(missing_ok=True)

        skill_restore_result = restore_skills_from_team_dir(skills_extract_root, user_id, team)
        
        # --- Restore internal scheduler alarms from cron_jobs.json ---
        cron_jobs_path = os.path.join(team_dir, "cron_jobs.json")
        cron_restored_total = 0
        cron_errors = []
        
        if os.path.exists(cron_jobs_path):
            try:
                with open(cron_jobs_path, "r", encoding="utf-8") as f:
                    cron_jobs_data = json.load(f)
                if isinstance(cron_jobs_data, dict):
                    # Backward compatibility for old snapshots: skip OpenClaw cron dicts.
                    cron_jobs_data = []
                if isinstance(cron_jobs_data, list):
                    restored, errors = restore_team_alarms(
                        _teams(), alarms=cron_jobs_data, user_id=user_id, team=team, scheduler_url=SCHEDULER_TASKS_URL,
                    )
                    cron_restored_total += restored
                    cron_errors.extend(errors)
                
                # Clean up cron_jobs.json from team folder (it was only temporary)
                os.unlink(cron_jobs_path)
            except Exception as e:
                cron_errors.append(f"Failed to restore cron jobs: {e}")
        
        msg_parts = [f"Team '{team}' snapshot uploaded"]
        msg_parts.append(f"{len(members)} members restored")
        restored_skills_total = (
            int(skill_restore_result.get("restored_user_skill_dirs", 0) or 0)
            + int(skill_restore_result.get("restored_team_skill_dirs", 0) or 0)
        )
        if restored_skills_total:
            msg_parts.append(f"{restored_skills_total} managed skills restored")
        if openclaw_restored > 0 or openclaw_errors:
            msg_parts.append(f"{openclaw_restored} OpenClaw agents restored")
        if cron_restored_total > 0 or cron_errors:
            msg_parts.append(f"{cron_restored_total} cron jobs restored")
        
        return jsonify({
            "success": True,
            "message": ", ".join(msg_parts),
            "skill_restore": skill_restore_result,
            "openclaw_errors": openclaw_errors if openclaw_errors else None,
            "openclaw_restore_details": openclaw_restore_details if openclaw_restore_details else None,
            "cron_errors": cron_errors if cron_errors else None,
        })
    except zipfile.BadZipFile:
        return jsonify({"error": "Invalid zip file"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        if skills_extract_root and os.path.isdir(skills_extract_root):
            shutil.rmtree(skills_extract_root, ignore_errors=True)


@app.route("/teams/snapshot/import_from_url", methods=["POST"])
def import_team_from_url():
    """Download a zip from a remote URL, then delegate to /teams/snapshot/upload."""
    import io
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    team = (data.get("team") or "").strip()

    if not url:
        return jsonify({"error": "url is required"}), 400
    if not team:
        return jsonify({"error": "team is required"}), 400

    # Download the zip
    try:
        dl = requests.get(url, timeout=120, stream=True, allow_redirects=True)
        dl.raise_for_status()
    except Exception as e:
        return jsonify({"error": f"下载失败: {e}"}), 502

    zip_bytes = dl.content

    # Internally call the existing upload endpoint
    with app.test_client() as c:
        # Copy session cookie so upload_team_snapshot sees the same user
        with c.session_transaction() as sess:
            sess.update(dict(session))
        resp = c.post("/teams/snapshot/upload", data={
            "team": team,
            "file": (io.BytesIO(zip_bytes), "team_import.zip"),
        }, content_type="multipart/form-data")

    return resp.data, resp.status_code, dict(resp.headers)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT_FRONTEND", "51209")), debug=False, threaded=True)
