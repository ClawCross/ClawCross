import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from integrations.acpx_cli_tools import acpx_agent_command_names, acpx_agent_tags_with_legacy  # noqa: E402


def test_acpx_agent_command_names_nonempty():
    names = acpx_agent_command_names()
    assert isinstance(names, frozenset)
    assert len(names) >= 4
    assert "codex" in names or "claude" in names


def test_legacy_aliases_included():
    merged = acpx_agent_tags_with_legacy()
    assert "aider" in merged
