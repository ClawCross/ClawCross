from pathlib import Path


def test_setup_env_ps1_is_utf8_bom_for_windows_powershell_51():
    script = Path("scripts/setup_env.ps1")
    data = script.read_bytes()

    assert data.startswith(b"\xef\xbb\xbf")


def test_setup_env_ps1_uses_explicit_path_concatenation():
    text = Path("scripts/setup_env.ps1").read_text(encoding="utf-8-sig")

    assert "$env:PATH -notlike ('*' + $npmBin + '*')" in text
    assert "$env:PATH = $npmBin + ';' + $env:PATH" in text
    assert '"${npmBin};${env:PATH}"' not in text
    assert '"*${npmBin}*"' not in text
