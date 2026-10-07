"""Configured reverse proxies and Quick Tunnels share public URL discovery."""

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "backend"))

from common.public_access import read_public_domain, write_tunnel_domain


def test_fixed_domain_is_available_without_tunnel_and_reloads(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("PUBLIC_DOMAIN=clawcross.example/\n")
    assert read_public_domain(env_file, tunnel_running=False) == "https://clawcross.example"
    env_file.write_text("PUBLIC_DOMAIN=https://changed.example\n")
    assert read_public_domain(env_file, tunnel_running=False) == "https://changed.example"


def test_tunnel_start_and_stop_preserve_fixed_proxy_configuration(tmp_path):
    env_file = tmp_path / ".env"
    original = "PUBLIC_DOMAIN=https://clawcross.example\nOTHER_SETTING=preserve\n"
    env_file.write_text(original)
    for url in ("", "https://random.trycloudflare.com", ""):
        assert write_tunnel_domain(url, env_file) is False
        assert env_file.read_text() == original


def test_quick_tunnel_publish_and_cleanup_preserve_other_settings_and_permissions(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER_SETTING=preserve\nPUBLIC_DOMAIN=\n")
    env_file.chmod(0o600)
    assert write_tunnel_domain("https://random.trycloudflare.com", env_file)
    assert read_public_domain(env_file, tunnel_running=True) == "https://random.trycloudflare.com"
    assert read_public_domain(env_file, tunnel_running=False) == ""
    assert not write_tunnel_domain("", env_file, expected="https://different.trycloudflare.com")
    assert write_tunnel_domain("", env_file, expected="https://random.trycloudflare.com")
    assert env_file.read_text() == "OTHER_SETTING=preserve\nPUBLIC_DOMAIN=\n"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_cleared_configuration_does_not_reuse_stale_process_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setenv("PUBLIC_DOMAIN", "https://stale.trycloudflare.com")
    env_file.write_text("PUBLIC_DOMAIN=\n")
    assert read_public_domain(env_file, tunnel_running=True) == ""


def test_placeholder_is_not_a_public_address(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("PUBLIC_DOMAIN=wait to set\n")
    assert read_public_domain(env_file, tunnel_running=True) == ""
