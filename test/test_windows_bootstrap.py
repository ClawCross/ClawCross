"""The Windows entrypoint must stop after preparing Python."""

from pathlib import Path


def test_windows_bootstrap_dispatches_to_python():
    text = Path("launch/run.ps1").read_text(encoding="utf-8-sig")
    assert "winget" in text
    assert "& $uv venv" in text
    assert "launch\\runtime_control.py" in text
    assert "npm install" not in text
    assert "cloudflared" not in text
