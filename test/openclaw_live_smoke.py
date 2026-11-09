"""
Opt-in live smoke test for OpenClaw.

Flow:
1. Create an isolated OpenClaw profile
2. Run non-interactive onboard in local mode
3. Export Clawcross/CI LLM settings into the OpenClaw config
4. Enable /v1/chat/completions
5. Start `openclaw gateway run` in foreground
6. Send a tiny OpenAI-compatible chat completion request through the gateway
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import utils.scheduler_service
from collections import deque
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "selfskill" / "scripts"


def _read_env_file() -> dict[str, str]:
    env_path = PROJECT_ROOT / "config" / ".env"
    if not env_path.exists():
        return {}

    data: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _pick(cli_value: str, env_keys: list[str], file_keys: list[str], saved: dict[str, str]) -> str:
    value = (cli_value or "").strip()
    if value:
        return value
    for env_key in env_keys:
        value = os.getenv(env_key, "").strip()
        if value:
            return value
    for file_key in file_keys:
        value = (saved.get(file_key) or "").strip()
        if value:
            return value
    return ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a real OpenClaw gateway smoke test.")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--provider", default="")
    parser.add_argument("--gateway-port", type=int, default=int(os.getenv("OPENCLAW_LIVE_GATEWAY_PORT", "19017")))
    parser.add_argument("--profile", default=f"ci-live-{int(time.time())}")
    parser.add_argument("--prompt", default=os.getenv("OPENCLAW_LIVE_PROMPT", "Reply with exactly OPENCLAW_LIVE_OK and nothing else."))
    parser.add_argument("--expect", default=os.getenv("OPENCLAW_LIVE_EXPECT", "OPENCLAW_LIVE_OK"))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("OPENCLAW_LIVE_TIMEOUT", "90")))
    return parser.parse_args()


def _run_checked(cmd: list[str], *, env: dict[str, str], timeout: int = 120) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            + " ".join(cmd)
            + "\nstdout:\n"
            + proc.stdout
            + "\nstderr:\n"
            + proc.stderr
        )
    return proc


def _wait_for_port(port: int, timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _build_auth_headers(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _wait_for_http_endpoint(port: int, token: str | None, timeout: int) -> tuple[int, str]:
    deadline = time.time() + timeout
    last_status = 0
    last_body = ""
    while time.time() < deadline:
        try:
            response = requests.get(
                f"http://127.0.0.1:{port}/v1/models",
                headers=_build_auth_headers(token),
                timeout=5,
            )
            last_status = response.status_code
            last_body = response.text[:400]
            if response.status_code != 404:
                return last_status, last_body
        except requests.RequestException as exc:
            last_body = str(exc)
        time.sleep(1)
    return last_status, last_body


def _extract_content(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "".join(parts).strip()
    return str(content or "").strip()


def _enable_chat_completions_in_config(config_path: Path) -> None:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    gateway = data.setdefault("gateway", {})
    http_cfg = gateway.setdefault("http", {})
    endpoints = http_cfg.setdefault("endpoints", {})
    chat_completions = endpoints.setdefault("chatCompletions", {})
    chat_completions["enabled"] = True
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _disable_gateway_auth_in_config(config_path: Path) -> None:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    gateway = data.setdefault("gateway", {})
    auth_cfg = gateway.setdefault("auth", {})
    auth_cfg["mode"] = "none"
    auth_cfg.pop("token", None)
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    saved = _read_env_file()

    api_key = _pick(args.api_key, ["OPENCLAW_LIVE_API_KEY", "LLM_LIVE_API_KEY"], ["LLM_API_KEY"], saved)
    base_url = _pick(args.base_url, ["OPENCLAW_LIVE_BASE_URL", "LLM_LIVE_BASE_URL"], ["LLM_BASE_URL"], saved)
    model = _pick(args.model, ["OPENCLAW_LIVE_MODEL", "LLM_LIVE_MODEL"], ["LLM_MODEL"], saved)
    provider = _pick(args.provider, ["OPENCLAW_LIVE_PROVIDER", "LLM_LIVE_PROVIDER"], ["LLM_PROVIDER"], saved)

    missing = [
        name
        for name, value in (
            ("api_key", api_key),
            ("base_url", base_url),
            ("model", model),
        )
        if not value
    ]
    if missing:
        print("OpenClaw live smoke test failed: missing " + ", ".join(missing))
        return 1

    openclaw_bin = shutil.which("openclaw")
    if not openclaw_bin:
        print("OpenClaw live smoke test failed: `openclaw` not found in PATH.")
        return 1

    profile = args.profile
    openclaw_home = Path.home() / f".openclaw-{profile}"
    workspace = Path(tempfile.mkdtemp(prefix=f"{profile}-ws-"))
    token = secrets.token_hex(24)
    cli_env = os.environ.copy()

    shutil.rmtree(openclaw_home, ignore_errors=True)

    onboard_cmd = [
        openclaw_bin,
        "--profile",
        profile,
        "onboard",
        "--non-interactive",
        "--accept-risk",
        "--mode",
        "local",
        "--auth-choice",
        "skip",
        "--gateway-auth",
        "token",
        "--gateway-bind",
        "loopback",
        "--gateway-port",
        str(args.gateway_port),
        "--gateway-token",
        token,
        "--skip-channels",
        "--skip-search",
        "--skip-skills",
        "--skip-ui",
        "--skip-health",
        "--skip-daemon",
        "--workspace",
        str(workspace),
        "--json",
    ]
    onboard_result = _run_checked(onboard_cmd, env=cli_env, timeout=args.timeout)
    print(onboard_result.stdout.strip())

    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    os.environ["OPENCLAW_HOME"] = str(openclaw_home)
    configure_openclaw = importlib.import_module("configure_openclaw")
    configure_openclaw = importlib.reload(configure_openclaw)
    configure_openclaw.OPENCLAW_HOME = str(openclaw_home)
    configure_openclaw.OPENCLAW_CONFIG_PATH = str(openclaw_home / "openclaw.json")
    configure_openclaw.DEFAULT_WORKSPACE_PATH = str(workspace)
    configure_openclaw.DEFAULT_SESSIONS_FILE = str(openclaw_home / "agents" / "main" / "sessions" / "sessions.json")

    configure_openclaw.init_workspace_templates(str(workspace))
    export_result = configure_openclaw.export_llm_config_to_openclaw(
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider=provider,
    )
    config_path = openclaw_home / "openclaw.json"
    _enable_chat_completions_in_config(config_path)
    _disable_gateway_auth_in_config(config_path)
    enabled_result = _run_checked(
        [
            openclaw_bin,
            "--profile",
            profile,
            "config",
            "get",
            "gateway.http.endpoints.chatCompletions.enabled",
        ],
        env=cli_env,
        timeout=30,
    )
    print(f"chat_completions_enabled={enabled_result.stdout.strip()}")

    gateway_cmd = [
        openclaw_bin,
        "--profile",
        profile,
        "gateway",
        "run",
        "--force",
        "--port",
        str(args.gateway_port),
        "--bind",
        "loopback",
        "--auth",
        "none",
    ]

    proc = subprocess.Popen(
        gateway_cmd,
        cwd=PROJECT_ROOT,
        env=cli_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    logs: deque[str] = deque(maxlen=200)

    def _pump_output() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            logs.append(line.rstrip())

    reader = threading.Thread(target=_pump_output, daemon=True)
    reader.start()

    try:
        if not _wait_for_port(args.gateway_port, args.timeout):
            raise RuntimeError("Gateway did not start in time.\n" + "\n".join(logs))

        models_status, models_body = _wait_for_http_endpoint(args.gateway_port, None, args.timeout)
        print(f"models_status={models_status}")
        if models_status == 404:
            raise RuntimeError("OpenClaw HTTP endpoint stayed at 404.\n" + models_body + "\n" + "\n".join(logs))

        endpoint = f"http://127.0.0.1:{args.gateway_port}/v1/chat/completions"
        candidates = ["openclaw/default", "openclaw", "openclaw/main"]
        response_payload = None
        used_model = None
        last_error = ""

        for candidate in candidates:
            if not candidate:
                continue
            headers = {
                "Content-Type": "application/json",
                "x-openclaw-agent-id": "main",
                "x-openclaw-model": export_result.get("model_ref") or model,
            }
            headers.update(_build_auth_headers(None))

            response = requests.post(
                endpoint,
                headers=headers,
                json={
                    "model": candidate,
                    "messages": [{"role": "user", "content": args.prompt}],
                    "stream": False,
                },
                timeout=args.timeout,
            )
            if response.ok:
                response_payload = response.json()
                used_model = candidate
                break
            last_error = f"status={response.status_code} body={response.text[:400]}"

        if response_payload is None:
            raise RuntimeError("OpenClaw chat completion failed. " + last_error)

        text = _extract_content(response_payload)
        print(f"model={used_model}")
        print(f"response={text[:200]}")
        if not text:
            raise RuntimeError("OpenClaw returned an empty response.")
        expected = (args.expect or "").strip()
        if expected and expected.upper() not in text.upper():
            raise RuntimeError(f"Expected marker {expected!r} not found in OpenClaw response.")
        return 0
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(openclaw_home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
