#!/usr/bin/env python3
"""Prepare the Python runtime and explicitly requested external components.

This module intentionally uses only the standard library so it can run in a
newly created virtual environment before project dependencies are installed.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = PROJECT_ROOT / "config" / "requirements.txt"
CHANNEL_REQUIREMENTS = PROJECT_ROOT / "config" / "requirements-channels.txt"


def bin_dir() -> Path:
    home = Path(os.getenv("CLAWCROSS_HOME") or Path.home() / ".clawcross")
    return Path(os.getenv("CLAWCROSS_BIN_DIR") or home / "bin")


def venv_python() -> Path:
    home = Path(os.getenv("CLAWCROSS_HOME") or Path.home() / ".clawcross")
    venv = Path(os.getenv("CLAWCROSS_VENV_DIR") or home / "venv")
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _run(command: list[str], *, timeout: int = 600) -> None:
    print("Running:", " ".join(command), flush=True)
    subprocess.run(command, check=True, timeout=timeout, cwd=PROJECT_ROOT)


def _core_imports_available(python: Path) -> bool:
    modules = ("fastapi", "dotenv", "uvicorn", "langchain_openai", "mcp", "aiosqlite")
    code = ("import importlib.util, sys; "
            f"sys.exit(0 if all(importlib.util.find_spec(name) for name in {modules!r}) else 1)")
    result = subprocess.run([str(python), "-c", code], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, check=False)
    return result.returncode == 0


def ensure_core() -> None:
    """Install only the Python dependencies needed by the core application."""
    python = venv_python()
    if not python.is_file():
        raise RuntimeError(f"Python environment is missing: {python}")
    digest = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    stamp = python.parents[1] / ".clawcross-requirements.sha256"
    if (stamp.is_file() and stamp.read_text(encoding="ascii").strip() == digest
            and _core_imports_available(python)):
        return
    uv = os.getenv("CLAWCROSS_UV_BIN") or shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required to prepare Python dependencies")
    _run([uv, "pip", "install", "--python", str(python), "-r", str(REQUIREMENTS)])
    stamp.write_text(digest + "\n", encoding="ascii")


def component_status() -> None:
    for name, binary in (("acpx", "acpx"), ("cloudflared", "cloudflared"), ("weclaw", "weclaw")):
        suffix = ".exe" if os.name == "nt" else ""
        local = bin_dir() / f"{binary}{suffix}"
        if name == "acpx":
            local = bin_dir() / "node" / "node_modules" / ".bin" / ("acpx.cmd" if os.name == "nt" else "acpx")
        print(f"{name}: {shutil.which(binary) or (str(local) if local.is_file() else 'not installed')}")
    python = venv_python()
    if python.is_file():
        check = subprocess.run([str(python), "-c", "import nonebot"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        print(f"nonebot: {'installed' if check.returncode == 0 else 'not installed'}")
        channel_check = subprocess.run(
            [str(python), "-c", "from importlib.metadata import distribution; "
             "distribution('qq-botpy'); distribution('python-telegram-bot')"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        print(f"channels: {'installed' if channel_check.returncode == 0 else 'not installed'}")
    else:
        print("nonebot: Python environment missing")
        print("channels: Python environment missing")


def _asset_name(component: str) -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if not arch or system not in {"linux", "darwin", "windows"}:
        raise RuntimeError(f"Unsupported platform for {component}: {system}/{machine}")
    if component == "weclaw":
        return f"weclaw_{system}_{arch}" + (".exe" if system == "windows" else "")
    if system == "darwin":
        return f"cloudflared-darwin-{arch}.tgz"
    if system == "windows":
        if arch != "amd64":
            raise RuntimeError("Cloudflare does not publish a Windows arm64 binary")
        return "cloudflared-windows-amd64.exe"
    return f"cloudflared-linux-{arch}"


def _github_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "ClawCross-optional-installer",
                                                   "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def _read_release_asset(repository: str, asset: dict, *, limit: int) -> bytes:
    url = asset.get("browser_download_url") or ""
    expected_prefix = f"https://github.com/{repository}/releases/download/"
    if not url.startswith(expected_prefix):
        raise RuntimeError("Unexpected release asset URL")
    request = urllib.request.Request(url, headers={"User-Agent": "ClawCross-optional-installer"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError(f"Release asset exceeds the {limit} byte limit")
    return data


def _download_release_binary(component: str) -> Path:
    """Download an explicitly selected release asset after a SHA-256 check."""
    repository = "cloudflare/cloudflared" if component == "cloudflared" else "fastclaw-ai/weclaw"
    asset_name = _asset_name(component)
    releases = _github_json(f"https://api.github.com/repos/{repository}/releases?per_page=10")
    if not isinstance(releases, list):
        raise RuntimeError("Unexpected GitHub release response")
    selected = None
    selected_release = None
    for release in releases:
        if not isinstance(release, dict) or release.get("prerelease"):
            continue
        selected = next((a for a in release.get("assets", []) if a.get("name") == asset_name), None)
        if selected:
            selected_release = release
            break
    if not selected:
        raise RuntimeError(f"No release asset found for {asset_name}")
    digest = selected.get("digest") or ""
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        checksums = next((a for a in selected_release.get("assets", [])
                          if a.get("name") == "checksums.txt"), None)
        if not checksums:
            raise RuntimeError(f"Release asset {asset_name} has no SHA-256 digest; nothing was installed")
        manifest = _read_release_asset(repository, checksums, limit=100_000).decode("utf-8")
        hashes = [match.group(1) for line in manifest.splitlines()
                  if (match := re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line))
                  and match.group(2) == asset_name]
        if len(hashes) != 1:
            raise RuntimeError(f"Release checksum for {asset_name} is missing or ambiguous")
        digest = "sha256:" + hashes[0]
    data = _read_release_asset(repository, selected, limit=100_000_000)
    if hashlib.sha256(data).hexdigest().lower() != digest.removeprefix("sha256:").lower():
        raise RuntimeError("Release asset SHA-256 mismatch; nothing was installed")
    if asset_name.endswith(".tgz"):
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members = [m for m in archive.getmembers() if m.isfile() and Path(m.name).name == "cloudflared"]
            if len(members) != 1:
                raise RuntimeError("Cloudflare archive does not contain one cloudflared binary")
            extracted = archive.extractfile(members[0])
            if extracted is None:
                raise RuntimeError("Could not read cloudflared from archive")
            data = extracted.read()
    destination_dir = bin_dir()
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / (component + (".exe" if os.name == "nt" else ""))
    with tempfile.NamedTemporaryFile(dir=destination_dir, delete=False) as temporary:
        temporary.write(data)
        temp_path = Path(temporary.name)
    try:
        if os.name != "nt":
            temp_path.chmod(0o755)
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)
    return destination


def install_component(name: str, adapters: list[str]) -> None:
    """Run only a component named by the caller; startup never invokes this."""
    if name == "channels":
        python = venv_python()
        if not python.is_file():
            raise RuntimeError("Prepare the Python environment before installing channel packages")
        uv = os.getenv("CLAWCROSS_UV_BIN") or shutil.which("uv")
        if not uv:
            raise RuntimeError("uv is required to install optional Python components")
        command = [uv, "pip", "install", "--python", str(python)]
        _run(command + ["-r", str(CHANNEL_REQUIREMENTS)])
        return
    if name == "acpx":
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not npm:
            raise RuntimeError("npm is required to install acpx; install Node.js first")
        target = bin_dir() / "node"
        target.mkdir(parents=True, exist_ok=True)
        _run([npm, "install", "--ignore-scripts", "--prefix", str(target), "acpx@latest"])
        print(f"acpx installed under {target}")
        return
    if name == "nonebot":
        python = venv_python()
        if not python.is_file():
            raise RuntimeError("Prepare the Python environment before installing NoneBot")
        packages = ["nonebot2[fastapi,httpx,websockets]"]
        for adapter in adapters:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", adapter):
                raise ValueError(f"Invalid adapter name: {adapter!r}")
            packages.append("nonebot-adapter-" + adapter.split(".", 1)[0].replace("_", "-"))
        uv = os.getenv("CLAWCROSS_UV_BIN") or shutil.which("uv")
        if not uv:
            raise RuntimeError("uv is required to install optional Python components")
        command = [uv, "pip", "install", "--python", str(python)]
        _run(command + list(dict.fromkeys(packages)))
        return
    if name == "weclaw":
        print(f"weclaw installed: {_download_release_binary('weclaw')}")
        return
    if name == "cloudflared":
        print(f"cloudflared installed: {_download_release_binary('cloudflared')}")
        return
    raise ValueError(f"Unknown component: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ensure-core", help="Install core Python dependencies if needed")
    sub.add_parser("components", help="Report optional component availability")
    install = sub.add_parser("install", help="Explicitly install an optional component")
    install.add_argument("component", choices=("acpx", "nonebot", "channels", "weclaw", "cloudflared"))
    install.add_argument("--adapter", action="append", default=[], help="NoneBot adapter name")
    args = parser.parse_args()
    try:
        if args.command == "ensure-core":
            ensure_core()
        elif args.command == "components":
            component_status()
        else:
            install_component(args.component, args.adapter)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"Environment error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
