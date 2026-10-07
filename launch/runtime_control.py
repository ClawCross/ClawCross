#!/usr/bin/env python3
"""Cross-platform service lifecycle after the thin OS bootstrap has Python."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

from environment import bin_dir, component_status, ensure_core, install_component


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TRUE_VALUES = {"1", "true", "yes", "on"}
LEGACY_PATHS = os.getenv("CLAWCROSS_USE_LEGACY_PATHS", "").lower() in TRUE_VALUES
HOME = ROOT if LEGACY_PATHS else Path(os.getenv("CLAWCROSS_HOME") or Path.home() / ".clawcross")
CONFIG_DIR = HOME / "config" if LEGACY_PATHS else Path(os.getenv("CLAWCROSS_CONFIG_DIR") or HOME / "config")
RUN_DIR = HOME if LEGACY_PATHS else Path(os.getenv("CLAWCROSS_RUN_DIR") or HOME / "run")
LOG_DIR = HOME / "logs" if LEGACY_PATHS else Path(os.getenv("CLAWCROSS_LOG_DIR") or HOME / "logs")
WORKSPACE_DIR = HOME if LEGACY_PATHS else Path(os.getenv("CLAWCROSS_WORKSPACE_DIR") or HOME / "workspace")
ENV_FILE = CONFIG_DIR / ".env"
LAUNCHER_PID = RUN_DIR / "clawcross.pid"
TUNNEL_PID = RUN_DIR / "tunnel.pid"


def _initialize_paths() -> None:
    """Share one runtime layout with scripts launched from this controller."""
    legacy = LEGACY_PATHS
    home = HOME
    if legacy:
        os.environ["CLAWCROSS_HOME"] = str(home)
    else:
        os.environ.setdefault("CLAWCROSS_HOME", str(home))
    paths = {
        "CLAWCROSS_VENV_DIR": home / (".venv" if legacy else "venv"),
        "CLAWCROSS_DATA_DIR": home / "data",
        "CLAWCROSS_LOG_DIR": home / "logs",
        "CLAWCROSS_CONFIG_DIR": home / "config",
        "CLAWCROSS_RUN_DIR": home if legacy else home / "run",
        "CLAWCROSS_BIN_DIR": home / "bin",
        "CLAWCROSS_WORKSPACE_DIR": home if legacy else home / "workspace",
        "CLAWCROSS_STATE_DIR": home,
    }
    for key, path in paths.items():
        if legacy:
            os.environ[key] = str(path)
        else:
            os.environ.setdefault(key, str(path))
    os.environ.setdefault("PYTHONPYCACHEPREFIX", str(home / "pycache"))


def _is_windows() -> bool:
    return os.name == "nt"


def _read_env() -> dict[str, str]:
    result: dict[str, str] = {}
    if ENV_FILE.is_file():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip("'\"")
    return result


def _process_env(*, no_channel: bool = False) -> dict[str, str]:
    env = dict(_read_env())
    env.update(os.environ)
    env.setdefault("CLAWCROSS_HOME", str(HOME))
    env.setdefault("CLAWCROSS_WORKSPACE_DIR", str(WORKSPACE_DIR))
    local_node_bin = bin_dir() / "node" / "node_modules" / ".bin"
    env["PATH"] = os.pathsep.join((str(bin_dir()), str(local_node_bin), env.get("PATH", "")))
    env["WEBOT_HEADLESS"] = "1"
    if no_channel:
        env["CLAWCROSS_NO_CHANNEL"] = "1"
    return env


def _pid(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="utf-8-sig").strip())
        if value <= 0:
            return None
        if _is_windows():
            result = subprocess.run(["tasklist", "/FI", f"PID eq {value}", "/FO", "CSV", "/NH"],
                                    capture_output=True, text=True, check=False, timeout=5)
            if result.returncode != 0:
                return None
            return value if any(len(row) > 1 and row[1] == str(value)
                                for row in csv.reader(result.stdout.splitlines())) else None
        os.kill(value, 0)
        return value
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError, OSError,
            subprocess.TimeoutExpired):
        return None


def _stop_pid(path: Path, *, timeout: float = 8.0) -> None:
    pid = _pid(path)
    if pid is None:
        path.unlink(missing_ok=True)
        return
    if _is_windows():
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _pid(path) is None:
            break
        time.sleep(0.2)
    path.unlink(missing_ok=True)


def _clear_public_domain() -> None:
    from src.backend.common.public_access import write_tunnel_domain
    write_tunnel_domain("", ENV_FILE)


def _remove_owned_pid(path: Path, pid: int) -> None:
    try:
        if path.read_text(encoding="ascii").strip() == str(pid):
            path.unlink()
    except FileNotFoundError:
        pass


def _probe(url: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=1.5) as response:
            return response.status < 500
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except (OSError, urllib.error.URLError):
        return False


def _port(env: dict[str, str], name: str, default: int) -> int:
    value = int(env.get(name, default))
    if not 1 <= value <= 65535:
        raise ValueError(f"{name} must be a port between 1 and 65535")
    return value


def _ensure_config() -> None:
    if ENV_FILE.is_file():
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(ROOT / "src/backend/ops/setup/configure.py"), "--init"],
                   cwd=ROOT, check=True)


def _migrate_if_needed() -> None:
    if (os.getenv("CLAWCROSS_USE_LEGACY_PATHS") or "").lower() in TRUE_VALUES:
        return
    if (HOME / ".migration_done").is_file():
        return
    subprocess.run([sys.executable, str(ROOT / "launch/migrate_to_user_home.py")],
                   cwd=ROOT, check=True)


def _maybe_import_openclaw(env: dict[str, str]) -> None:
    """Read OpenClaw's LLM settings into ClawCross while ClawCross has no key of its own."""
    if (env.get("LLM_API_KEY") or "") not in {"", "your_api_key_here"}:
        return
    subprocess.run([sys.executable, str(ROOT / "src/backend/ops/setup/configure_openclaw.py"),
                    "--import-clawcross-llm-from-openclaw"], cwd=ROOT, check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _check_model(env: dict[str, str]) -> None:
    model = (env.get("LLM_MODEL") or "").strip()
    if model and model != "wait to set":
        return
    if ((env.get("CLAWCROSS_REQUIRE_LLM_MODEL") or "").lower() in TRUE_VALUES
            and (env.get("CLAWCROSS_ALLOW_EMPTY_LLM_MODEL") or "").lower() not in TRUE_VALUES):
        raise RuntimeError("LLM_MODEL is required by CLAWCROSS_REQUIRE_LLM_MODEL=1")
    print("LLM_MODEL is unset; the web UI can start, but LLM requests will fail until configured.")


def _magic_links(env: dict[str, str], *, tunnel: bool) -> None:
    user = env.get("CLAWCROSS_MAGIC_LINK_USER") or "default"
    result = subprocess.run([sys.executable, str(ROOT / "src/cli/cli.py"), "token", "generate",
                             "-u", user, "--valid-hours", "24"], cwd=ROOT, env=env,
                            text=True, capture_output=True, check=False)
    match = re.search(r"Token:\s*(\S+)", result.stdout)
    if not match:
        print("Magic link unavailable; set INTERNAL_TOKEN in config/.env.")
        return
    port = _port(env, "PORT_FRONTEND", 51209)
    suffix = f"/login-link/{match.group(1)}?user={user}"
    print(f"🔗 Magic link 本机: http://127.0.0.1:{port}{suffix}")
    from src.backend.common.public_access import read_public_domain
    domain = read_public_domain(ENV_FILE, tunnel_running=bool(_pid(TUNNEL_PID)))
    if domain:
        print(f"🔗 Magic link 远程: {domain}{suffix}")


def _start_tunnel(env: dict[str, str]) -> bool:
    check = subprocess.run([sys.executable, str(ROOT / "launch/tunnel.py"), "--check"],
                           cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    if check.returncode:
        print("Cloudflare Tunnel skipped: cloudflared is unavailable. Install it explicitly if needed.")
        return False
    _stop_pid(TUNNEL_PID)
    _clear_public_domain()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "tunnel.log").open("a", encoding="utf-8") as log:
        kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                  else {"start_new_session": True})
        process = subprocess.Popen([sys.executable, str(ROOT / "launch/tunnel.py")], cwd=WORKSPACE_DIR,
                                   env=env, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, **kwargs)
    print("Starting Cloudflare Tunnel from the installed cloudflared binary...", flush=True)
    deadline = time.monotonic() + 65
    while time.monotonic() < deadline:
        domain = (_read_env().get("PUBLIC_DOMAIN") or "").strip()
        if domain and domain != "wait to set" and process.poll() is None:
            print(f"Mobile access: {domain}/mobile_group_chat")
            return True
        if process.poll() is not None:
            break
        time.sleep(1)
    print(f"Tunnel is still starting; see {LOG_DIR / 'tunnel.log'}")
    return process.poll() is None


def start(args: argparse.Namespace) -> int:
    use_openclaw = bool(getattr(args, "with_openclaw", False) and not args.no_openclaw)
    use_tunnel = bool(getattr(args, "tunnel", False) and not args.no_tunnel)
    _migrate_if_needed()
    ensure_core()
    _ensure_config()
    env = _process_env(no_channel=args.no_channel)
    if use_openclaw:
        _maybe_import_openclaw(env)
        env = _process_env(no_channel=args.no_channel)
    _check_model(env)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    _stop_pid(TUNNEL_PID)
    _clear_public_domain()
    _stop_pid(LAUNCHER_PID)
    command = [sys.executable, str(ROOT / "launch/launcher.py")]
    if args.foreground:
        process = None
        try:
            process = subprocess.Popen(command, cwd=WORKSPACE_DIR, env=env)
            LAUNCHER_PID.write_text(str(process.pid) + "\n", encoding="ascii")
            deadline = time.monotonic() + 120
            agent = _port(env, "PORT_AGENT", 51200)
            oasis = _port(env, "PORT_OASIS", 51202)
            frontend = _port(env, "PORT_FRONTEND", 51209)
            groups = _port(env, "PORT_GROUPS", 51203)
            while process.poll() is None and time.monotonic() < deadline:
                if (_probe(f"http://127.0.0.1:{groups}/relay/health") and
                _probe(f"http://127.0.0.1:{agent}/v1/models") and
                        _probe(f"http://127.0.0.1:{oasis}/experts") and
                        _probe(f"http://127.0.0.1:{frontend}/")):
                    print(f"Local web UI: http://127.0.0.1:{frontend}", flush=True)
                    _magic_links(_process_env(no_channel=args.no_channel), tunnel=False)
                    break
                time.sleep(0.5)
            return process.wait()
        except KeyboardInterrupt:
            return 130
        finally:
            if process is not None:
                _remove_owned_pid(LAUNCHER_PID, process.pid)
    with (LOG_DIR / "launcher.log").open("a", encoding="utf-8") as log:
        kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                  else {"start_new_session": True})
        process = subprocess.Popen(command, cwd=WORKSPACE_DIR, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, **kwargs)
    LAUNCHER_PID.write_text(str(process.pid) + "\n", encoding="ascii")
    agent = _port(env, "PORT_AGENT", 51200)
    oasis = _port(env, "PORT_OASIS", 51202)
    frontend = _port(env, "PORT_FRONTEND", 51209)
    groups = _port(env, "PORT_GROUPS", 51203)
    deadline = time.monotonic() + 120
    ready = False
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        if (_probe(f"http://127.0.0.1:{groups}/relay/health") and
                _probe(f"http://127.0.0.1:{agent}/v1/models") and
                _probe(f"http://127.0.0.1:{oasis}/experts") and
                _probe(f"http://127.0.0.1:{frontend}/")):
            ready = True
            break
        time.sleep(0.5)
    if not ready:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        _remove_owned_pid(LAUNCHER_PID, process.pid)
        print(f"Services did not become ready; see {LOG_DIR / 'launcher.log'}", file=sys.stderr)
        return 1
    print(f"Local web UI: http://127.0.0.1:{frontend}")
    tunnel = _start_tunnel(env) if use_tunnel else False
    _magic_links(env, tunnel=tunnel)
    return 0


def status() -> int:
    pid = _pid(LAUNCHER_PID)
    env = _process_env()
    if pid is None:
        print("ClawCross is not running.")
        return 1
    print(f"ClawCross launcher PID: {pid}")
    for name, default in (("PORT_AGENT", 51200), ("PORT_SCHEDULER", 51201),
                          ("PORT_OASIS", 51202), ("PORT_GROUPS", 51203), ("PORT_FRONTEND", 51209)):
        print(f"{name}: {_port(env, name, default)}")
    return 0


def _run_python(relative_path: str, arguments: list[str]) -> int:
    return subprocess.run([sys.executable, str(ROOT / relative_path), *arguments],
                          cwd=ROOT, env=_process_env(), check=False).returncode


def _logs(arguments: list[str]) -> int:
    names = {"launcher": "launcher.log", "main": "launcher.log", "error": "error.log",
             "errors": "error.log", "tunnel": "tunnel.log"}
    if len(arguments) > 1 or (arguments and arguments[0] not in names):
        raise ValueError("Usage: logs [launcher|error|tunnel]")
    path = LOG_DIR / names[arguments[0] if arguments else "launcher"]
    if not path.is_file():
        raise FileNotFoundError(path)
    lines = int(os.getenv("CLAWCROSS_LOG_LINES", "200"))
    if lines < 1:
        raise ValueError("CLAWCROSS_LOG_LINES must be positive")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]:
        print(line)
    return 0


def _legacy_command(command: str, arguments: list[str]) -> int | None:
    scripts = {
        "add-user": ("src/backend/ops/setup/adduser.py", []),
        "configure": ("src/backend/ops/setup/configure.py", []),
        "auto-model": ("src/backend/ops/setup/configure.py", ["--auto-model"]),
        "import-openclaw-llm": ("src/backend/ops/setup/configure_openclaw.py", ["--import-clawcross-llm-from-openclaw"]),
        "evolve-skill": ("tools/maintenance/evolve_skill.py", []),
        "cli": ("src/cli/cli.py", []),
        "clawcross": ("src/cli/clawcross.py", []),
    }
    if command in scripts:
        script, prefix = scripts[command]
        if command == "add-user" and len(arguments) != 2:
            raise ValueError("Usage: add-user <username> <password>")
        if command in {"cli", "clawcross", "evolve-skill"}:
            ensure_core()
        return _run_python(script, prefix + arguments)
    if command == "logs":
        return _logs(arguments)
    if command == "doctor":
        result = status()
        component_status()
        return result
    if command == "restart":
        _stop_pid(TUNNEL_PID)
        _clear_public_domain()
        _stop_pid(LAUNCHER_PID)
        options = argparse.ArgumentParser(prog="restart")
        options.add_argument("--tunnel", action="store_true")
        options.add_argument("--with-openclaw", action="store_true")
        options.add_argument("--no-tunnel", action="store_true")
        options.add_argument("--no-openclaw", action="store_true")
        options.add_argument("--no-channel", action="store_true")
        parsed = options.parse_args(arguments)
        parsed.foreground = False
        return start(parsed)
    return None


def main() -> int:
    _initialize_paths()
    if len(sys.argv) < 2 or sys.argv[1] == "help":
        print("ClawCross commands: start, start-foreground, restart, setup, stop, status, "
              "configure, add-user, auto-model, import-openclaw-llm, components, "
              "install-component, start-tunnel, stop-tunnel, tunnel-status, logs, "
              "doctor, cli, clawcross, evolve-skill")
        return 0
    if len(sys.argv) > 1:
        try:
            legacy = _legacy_command(sys.argv[1], sys.argv[2:])
            if legacy is not None:
                return legacy
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            print(f"ClawCross: {exc}", file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "start-foreground", "start-fg"):
        command = sub.add_parser(name)
        command.add_argument("--tunnel", action="store_true")
        command.add_argument("--with-openclaw", action="store_true")
        command.add_argument("--no-tunnel", action="store_true")
        command.add_argument("--no-openclaw", action="store_true")
        command.add_argument("--no-channel", action="store_true")
    sub.add_parser("setup")
    sub.add_parser("status")
    sub.add_parser("stop")
    sub.add_parser("components")
    install = sub.add_parser("install-component")
    install.add_argument("component", choices=("acpx", "nonebot", "channels", "weclaw", "cloudflared", "srt"))
    install.add_argument("--adapter", action="append", default=[])
    sub.add_parser("start-tunnel")
    sub.add_parser("stop-tunnel")
    sub.add_parser("tunnel-status")
    args = parser.parse_args()
    try:
        if args.command in {"start", "start-foreground", "start-fg"}:
            args.foreground = args.command != "start"
            return start(args)
        if args.command == "setup":
            _migrate_if_needed()
            ensure_core()
            component_status()
            return 0
        if args.command == "status":
            return status()
        if args.command == "stop":
            _stop_pid(TUNNEL_PID)
            _clear_public_domain()
            _stop_pid(LAUNCHER_PID)
            print("ClawCross stopped.")
            return 0
        if args.command == "components":
            component_status()
            return 0
        if args.command == "install-component":
            install_component(args.component, args.adapter)
            return 0
        if args.command == "start-tunnel":
            ensure_core()
            env = _process_env()
            if not _start_tunnel(env):
                return 1
            _magic_links(env, tunnel=True)
            return 0
        if args.command == "stop-tunnel":
            _stop_pid(TUNNEL_PID)
            _clear_public_domain()
            return 0
        if args.command == "tunnel-status":
            pid = _pid(TUNNEL_PID)
            print(f"Tunnel PID: {pid}" if pid else "Tunnel is not running.")
            return 0 if pid else 1
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"ClawCross: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
