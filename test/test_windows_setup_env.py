from pathlib import Path


def test_setup_env_ps1_is_utf8_bom_for_windows_powershell_51():
    script = Path("scripts/setup_env.ps1")
    data = script.read_bytes()

    assert data.startswith(b"\xef\xbb\xbf")


def test_setup_env_ps1_forwards_to_shared_python_bootstrap():
    text = Path("scripts/setup_env.ps1").read_text(encoding="utf-8-sig")
    assert '"selfskill\\scripts\\run.ps1") setup @args' in text
    assert "npm install" not in text
