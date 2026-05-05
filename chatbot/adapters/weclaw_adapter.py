"""
WeClaw 适配器 - 微信桥接

WeClaw（github.com/fastclaw-ai/weclaw）是一个 Go 写的微信 bot 桥，
本身已实现完整的微信协议（扫码登录、心跳、消息收发、CDN 加解密等）。
本适配器以"子进程托管"方式接入，并在前面加一层本地 proxy：

  WeChat ──> weclaw 子进程 ──HTTP──> 本地 proxy(51298) ──HTTP──> /v1/chat/completions
                                          │
                                          └─ 拦截 "/cross" 命令 → 返回前端 magic link

  1. 启动时自动检测 weclaw 二进制；缺失则跑 scripts/weclaw_install.sh 自动安装。
  2. 写 ~/.weclaw/config.json：把 proxy URL 注册为 default HTTP agent。
  3. spawn `weclaw start -f`，捕获 stdout：
     - 检测 ASCII QR 块，独立保存到 data/weclaw_qr.txt 并打印 banner
     - 其余日志按行 forward 到 logger
  4. proxy 解析每条 chat completion 请求：
     - "/cross" 开头 → 调 frontend /generate_login_link，把 magic link 当 assistant 回复返回
     - 其他 → 透传到真正的 agent endpoint（含流式）

环境变量：
  WECLAW_ENABLED=true            启用
  WECLAW_BIN=weclaw              二进制路径或可执行名（默认 PATH 查找）
  WECLAW_USERNAME=default        传给 agent 的 username
  WECLAW_CONFIG=~/.weclaw/config.json
  WECLAW_PROXY_PORT=51298        proxy 监听端口
  WECLAW_AUTO_INSTALL=true       缺二进制时自动安装
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

_chatbot_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_project_root = os.path.dirname(_chatbot_dir)
load_dotenv(dotenv_path=os.path.join(_project_root, "config", ".env"))

from .base import ChannelAdapter

logger = logging.getLogger("chatbot.weclaw")

# QR ASCII 块字符（unicode 半/全块、白/黑、阴影）
_QR_CHARS = set("█▀▄▌▐░▒▓ ▉▊▋▍▎▏▔▕")
_QR_FILE_RELPATH = "data/weclaw_qr.txt"
_INSTALL_SCRIPT_RELPATH = "scripts/weclaw_install.sh"


def _looks_like_qr_line(line: str) -> bool:
    """启发式：50% 以上字符在 QR 块字符集，且总长 >= 12。"""
    s = line.rstrip("\n").rstrip()
    if len(s) < 12:
        return False
    hits = sum(1 for c in s if c in _QR_CHARS)
    return hits / max(1, len(s)) >= 0.5


def _last_user_text(payload: dict) -> str:
    """从 OpenAI 兼容 chat completion 请求中取最后一条 user 消息的文本。"""
    msgs = payload.get("messages") or []
    for m in reversed(msgs):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    return part.get("text", "") or ""
        return ""
    return ""


class WeClawAdapter(ChannelAdapter):
    """以子进程方式托管 weclaw 二进制 + 本地拦截 proxy，路由微信消息到 ClawCross agent。"""

    channel = "weclaw"

    def __init__(self):
        super().__init__()
        self._bin = os.getenv("WECLAW_BIN", "weclaw")
        self._username = os.getenv("WECLAW_USERNAME", "default")
        self._config_path = os.path.expanduser(
            os.getenv("WECLAW_CONFIG", "~/.weclaw/config.json")
        )
        self._proxy_port = int(os.getenv("WECLAW_PROXY_PORT", "51298"))
        self._proxy_host = os.getenv("WECLAW_PROXY_HOST", "127.0.0.1")
        self._auto_install = os.getenv("WECLAW_AUTO_INSTALL", "true").lower() in ("1", "true", "yes", "on")
        self._frontend_port = os.getenv("PORT_FRONTEND", "51209")
        self._proc: subprocess.Popen | None = None
        self._http_server: ThreadingHTTPServer | None = None
        self._qr_buffer: list[str] = []
        self._qr_path = os.path.join(_project_root, _QR_FILE_RELPATH)

    # ── 抽象方法（weclaw 自己处理协议层）─────────────────────────────

    async def verify_permission(self, raw_message: Any) -> tuple[bool, str | None]:
        return True, self._username

    async def build_content(self, raw_message: Any) -> list[dict]:
        return []

    async def handle_message(self, raw_message: Any) -> str:
        return ""

    # ── 安装与配置 ───────────────────────────────────────────────────

    def _resolve_bin(self) -> str | None:
        path = shutil.which(self._bin)
        if path:
            return path
        if Path(self._bin).is_file() and os.access(self._bin, os.X_OK):
            return self._bin
        return None

    def _try_auto_install(self) -> str | None:
        """缺二进制时跑 scripts/weclaw_install.sh；返回安装后的路径或 None。"""
        if not self._auto_install:
            return None
        script = os.path.join(_project_root, _INSTALL_SCRIPT_RELPATH)
        if not os.path.exists(script):
            logger.error(f"找不到安装脚本 {script}")
            return None
        logger.info("WeClaw 二进制未找到，自动安装中（执行 scripts/weclaw_install.sh）...")
        try:
            result = subprocess.run(
                ["bash", script],
                cwd=_project_root,
                timeout=300,
                capture_output=True,
                text=True,
            )
            if result.stdout:
                for ln in result.stdout.splitlines():
                    logger.info(f"[install] {ln}")
            if result.returncode != 0:
                logger.error(f"weclaw 安装失败 (rc={result.returncode}): {result.stderr[:500]}")
                return None
        except subprocess.TimeoutExpired:
            logger.error("weclaw 安装超时（>5min）")
            return None
        except Exception as e:
            logger.error(f"weclaw 安装异常: {e}")
            return None
        return self._resolve_bin()

    def _write_weclaw_config(self) -> None:
        cfg_dir = os.path.dirname(self._config_path)
        os.makedirs(cfg_dir, exist_ok=True)

        existing: dict = {}
        if os.path.exists(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    existing = json.load(f) or {}
            except Exception as e:
                logger.warning(f"读取已有 weclaw 配置失败，将重写: {e}")
                existing = {}

        api_key = self.build_api_key(self._username)
        proxy_endpoint = f"http://{self._proxy_host}:{self._proxy_port}/v1/chat/completions"

        agents = existing.get("agents", {}) or {}
        agents["clawcross"] = {
            "type": "http",
            "endpoint": proxy_endpoint,
            "api_key": api_key,
            "model": self._llm_model or "default",
        }
        existing["agents"] = agents
        existing["default_agent"] = "clawcross"

        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        logger.info(f"已写入 weclaw 配置: {self._config_path} (agent endpoint=proxy@{self._proxy_port})")

    # ── proxy: 拦截 /cross，其余透传 ──────────────────────────────────

    def _gen_magic_link_sync(self, user_id: str) -> str | None:
        url = f"http://127.0.0.1:{self._frontend_port}/generate_login_link"
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(url, json={"user_id": user_id})
                if resp.status_code != 200:
                    logger.warning(f"生成 magic link 失败: {resp.status_code} {resp.text[:200]}")
                    return None
                return resp.json().get("link")
        except Exception as e:
            logger.warning(f"调用 generate_login_link 异常: {e}")
            return None

    def _username_from_auth(self, auth_header: str) -> str:
        """从 'Bearer <token>:<username>:<channel>' 取出 username；失败回退默认。"""
        if not auth_header:
            return self._username
        token = auth_header.split(" ", 1)[-1].strip()
        parts = token.split(":")
        if len(parts) >= 2:
            return parts[1] or self._username
        return self._username

    def _start_proxy_server(self) -> None:
        adapter_self = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                logger.debug("weclaw-proxy: " + fmt % args)

            def _send_json(self, status: int, payload: dict):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length > 0 else b""
                try:
                    data = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    self._send_json(400, {"error": "invalid json"})
                    return

                text = _last_user_text(data)
                if adapter_self.is_cross_command(text):
                    user_id = adapter_self._username_from_auth(self.headers.get("Authorization", ""))
                    link = adapter_self._gen_magic_link_sync(user_id)
                    content = adapter_self.format_cross_reply(link)
                    self._send_json(200, {
                        "id": "weclaw-cross",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": data.get("model", ""),
                        "choices": [{
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }],
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    })
                    logger.info(f"/cross 命令处理完成 (user={user_id})")
                    return

                # 透传到真正的 agent endpoint（含流式）
                fwd_headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")}
                try:
                    with httpx.Client(timeout=120.0) as client:
                        with client.stream(
                            "POST",
                            adapter_self._agent_url,
                            content=raw,
                            headers=fwd_headers,
                        ) as upstream:
                            self.send_response(upstream.status_code)
                            for k, v in upstream.headers.items():
                                if k.lower() in ("content-length", "transfer-encoding", "content-encoding"):
                                    continue
                                self.send_header(k, v)
                            self.end_headers()
                            for chunk in upstream.iter_raw():
                                if chunk:
                                    self.wfile.write(chunk)
                                    try:
                                        self.wfile.flush()
                                    except BrokenPipeError:
                                        return
                except Exception as e:
                    logger.error(f"proxy 转发失败: {e}")
                    try:
                        self._send_json(502, {"error": f"upstream error: {e}"})
                    except Exception:
                        pass

        # 端口占用预检
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((self._proxy_host, self._proxy_port))
            except OSError as e:
                logger.error(f"WECLAW_PROXY_PORT={self._proxy_port} 已被占用: {e}")
                raise

        server = ThreadingHTTPServer((self._proxy_host, self._proxy_port), _Handler)
        self._http_server = server
        t = threading.Thread(target=server.serve_forever, daemon=True, name="weclaw-proxy")
        t.start()
        logger.info(f"weclaw proxy 已启动: http://{self._proxy_host}:{self._proxy_port}")

    # ── 启动与生命周期 ────────────────────────────────────────────────

    async def run(self) -> None:
        if not self._internal_token:
            logger.error("INTERNAL_TOKEN 未配置，weclaw 无法以用户身份调 agent")
            return

        bin_path = self._resolve_bin()
        if not bin_path:
            bin_path = self._try_auto_install()
        if not bin_path:
            logger.error(
                f"找不到 weclaw 二进制 ({self._bin})，且自动安装失败。"
                f"请手动执行: bash {_INSTALL_SCRIPT_RELPATH}"
            )
            return

        try:
            self._start_proxy_server()
        except Exception as e:
            logger.error(f"启动 proxy 失败: {e}")
            return

        try:
            self._write_weclaw_config()
        except Exception as e:
            logger.error(f"写入 weclaw 配置失败: {e}")
            return

        logger.info(f"启动 weclaw 子进程: {bin_path} start -f")
        try:
            self._proc = subprocess.Popen(
                [bin_path, "start", "-f"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                start_new_session=True,
            )
        except Exception as e:
            logger.error(f"启动 weclaw 失败: {e}")
            return

        loop = asyncio.get_event_loop()
        try:
            loop.add_signal_handler(signal.SIGTERM, self._terminate)
            loop.add_signal_handler(signal.SIGINT, self._terminate)
        except (NotImplementedError, RuntimeError):
            pass  # Windows / 子线程情况

        await asyncio.gather(
            self._pump_logs(),
            self._wait_proc(),
        )

    async def _pump_logs(self) -> None:
        """边读边检测 QR 块；非 QR 行 forward 到 logger。"""
        if not self._proc or not self._proc.stdout:
            return
        loop = asyncio.get_event_loop()
        in_qr = False
        while True:
            line = await loop.run_in_executor(None, self._proc.stdout.readline)
            if not line:
                break
            if _looks_like_qr_line(line):
                self._qr_buffer.append(line.rstrip("\n"))
                in_qr = True
                continue
            if in_qr:
                # QR 块结束，落盘 + 发 banner
                self._flush_qr_buffer()
                in_qr = False
            logger.info(f"[weclaw] {line.rstrip()}")

    def _flush_qr_buffer(self) -> None:
        if not self._qr_buffer:
            return
        try:
            os.makedirs(os.path.dirname(self._qr_path), exist_ok=True)
            with open(self._qr_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self._qr_buffer) + "\n")
        except Exception as e:
            logger.warning(f"保存 QR 失败: {e}")
            self._qr_buffer = []
            return

        rel = os.path.relpath(self._qr_path, _project_root)
        banner = (
            "\n"
            + "=" * 60 + "\n"
            + "📱 WeChat 登录二维码已就绪！请用微信扫码：\n"
            + f"  - 文件：{rel}\n"
            + f"  - 终端查看：cat {rel}\n"
            + "  - 或直接看下面的 ASCII 二维码：\n"
            + "=" * 60 + "\n"
            + "\n".join(self._qr_buffer) + "\n"
            + "=" * 60 + "\n"
        )
        # 同时写到 stderr（即使 stdout 被重定向到 launcher.log，stderr 仍可能可见）
        try:
            import sys
            sys.stderr.write(banner)
            sys.stderr.flush()
        except Exception:
            pass
        logger.info(f"WeChat QR 已保存到 {rel}（共 {len(self._qr_buffer)} 行）")
        self._qr_buffer = []

    async def _wait_proc(self) -> None:
        if not self._proc:
            return
        loop = asyncio.get_event_loop()
        rc = await loop.run_in_executor(None, self._proc.wait)
        logger.warning(f"weclaw 子进程退出，returncode={rc}")
        if self._http_server:
            self._http_server.shutdown()

    def _terminate(self) -> None:
        if self._http_server:
            try:
                self._http_server.shutdown()
            except Exception:
                pass
        if not self._proc or self._proc.poll() is not None:
            return
        logger.info("收到关停信号，终止 weclaw 子进程")
        try:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
        except Exception:
            self._proc.terminate()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("weclaw 未在 10s 内退出，强制 kill")
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
            except Exception:
                self._proc.kill()
