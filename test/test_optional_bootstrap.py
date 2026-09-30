"""The setup path must not fetch optional services during ordinary commands."""

from pathlib import Path
import importlib.util
import hashlib
import os
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _load_environment():
    spec = importlib.util.spec_from_file_location("clawcross_environment", ROOT / "scripts/environment.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_runtime_control():
    spec = importlib.util.spec_from_file_location("clawcross_runtime_control", ROOT / "scripts/runtime_control.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_core_setup_only_installs_requirements_when_manifest_changes(tmp_path, monkeypatch):
    environment = _load_environment()
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("fastapi\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(environment, "venv_python", lambda: python)
    monkeypatch.setattr(environment, "REQUIREMENTS", requirements)
    monkeypatch.setattr(environment.shutil, "which", lambda name: "/fake/uv" if name == "uv" else None)
    monkeypatch.setattr(environment, "_run", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(environment, "_core_imports_available", lambda python: True)

    environment.ensure_core()
    environment.ensure_core()

    assert len(calls) == 1
    assert calls[0][1:3] == ["pip", "install"]
    assert "acpx" not in " ".join(calls[0])


def test_read_only_status_does_not_prepare_python_or_download(tmp_path):
    home = tmp_path / "home"
    result = subprocess.run(
        ["bash", str(ROOT / "selfskill/scripts/run.sh"), "status"],
        cwd=ROOT,
        env={**os.environ, "CLAWCROSS_HOME": str(home)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "not running" in result.stdout
    assert not (home / "venv").exists()


def test_setup_uses_uv_even_with_system_python_present(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".migration_done").touch()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "uv.log"
    uv = fake_bin / "uv"
    uv.write_text(
        '#!/bin/sh\n'
        'printf "%s\\n" "$*" >> "$FAKE_UV_LOG"\n'
        'if [ "$1" = venv ]; then\n'
        '  mkdir -p "$2/bin"\n'
        '  ln -s "$FAKE_PYTHON" "$2/bin/python"\n'
        'fi\n', encoding="utf-8")
    uv.chmod(0o755)
    result = subprocess.run(
        ["bash", str(ROOT / "selfskill/scripts/run.sh"), "setup"], cwd=ROOT,
        env={**os.environ, "CLAWCROSS_HOME": str(home), "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
             "FAKE_UV_LOG": str(log), "FAKE_PYTHON": sys.executable},
        text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    commands = log.read_text(encoding="utf-8")
    assert "venv " in commands
    assert "pip install" in commands
    assert "acpx" not in commands


def test_optional_download_requires_release_digest(tmp_path, monkeypatch):
    environment = _load_environment()
    monkeypatch.setattr(environment, "bin_dir", lambda: tmp_path)
    monkeypatch.setattr(environment, "_asset_name", lambda component: "cloudflared-linux-amd64")
    monkeypatch.setattr(environment, "_github_json", lambda url: [{
        "prerelease": False,
        "assets": [{"name": "cloudflared-linux-amd64", "digest": "", "browser_download_url": "https://example.com/file"}],
    }])
    try:
        environment._download_release_binary("cloudflared")
    except RuntimeError as exc:
        assert "SHA-256 digest" in str(exc)
    else:
        raise AssertionError("An unverified release asset was installed")
    assert list(tmp_path.iterdir()) == []


def test_optional_weclaw_download_accepts_release_checksum_manifest(tmp_path, monkeypatch):
    environment = _load_environment()
    name = "weclaw_linux_amd64"
    payload = b"verified weclaw binary"
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(environment, "bin_dir", lambda: tmp_path)
    monkeypatch.setattr(environment, "_asset_name", lambda component: name)
    monkeypatch.setattr(environment, "_github_json", lambda url: [{
        "prerelease": False,
        "assets": [
            {"name": name, "browser_download_url": f"https://github.com/fastclaw-ai/weclaw/releases/download/v1/{name}"},
            {"name": "checksums.txt", "browser_download_url": "https://github.com/fastclaw-ai/weclaw/releases/download/v1/checksums.txt"},
        ],
    }])
    monkeypatch.setattr(environment, "_read_release_asset",
                        lambda repo, asset, limit: (f"{digest}  {name}\n".encode()
                                                    if asset["name"] == "checksums.txt" else payload))
    installed = environment._download_release_binary("weclaw")
    assert installed.read_bytes() == payload


def test_channel_packages_are_opt_in():
    core = (ROOT / "config/requirements.txt").read_text(encoding="utf-8")
    optional = (ROOT / "config/requirements-channels.txt").read_text(encoding="utf-8")
    assert "qq-botpy" not in core
    assert "python-telegram-bot" not in core
    assert "agent-client-protocol" not in core
    assert "qq-botpy" in optional


def test_stale_tunnel_domain_is_cleared_before_start(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    control = _load_runtime_control()
    env_file = tmp_path / ".env"
    env_file.write_text("PORT_FRONTEND=51209\nPUBLIC_DOMAIN=https://old.trycloudflare.com\n", encoding="utf-8")
    monkeypatch.setattr(control, "ENV_FILE", env_file)
    monkeypatch.setattr(control, "CONFIG_DIR", tmp_path)
    control._clear_public_domain()
    assert env_file.read_text(encoding="utf-8") == "PORT_FRONTEND=51209\nPUBLIC_DOMAIN=\n"
