"""Validate the model-generated UI boundary without starting services."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/backend"))
from webot.mcp.ui_panel import PANEL_KIND, build_ui_panel  # noqa: E402


def test_ui_panel_loads_from_its_standalone_script_directory():
    script = Path(__file__).resolve().parents[1] / "src/backend/webot/mcp/ui_panel.py"
    result = subprocess.run(
        [sys.executable, "-c", "import runpy; runpy.run_path('ui_panel.py', run_name='standalone_smoke')"],
        cwd=script.parent,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_ui_panel_payload_contains_all_code_fields():
    payload = json.loads(build_ui_panel("Preview", "<button>Click</button>", "button{color:blue}", "let count=0"))
    assert payload == {
        "kind": PANEL_KIND, "title": "Preview", "html": "<button>Click</button>",
        "css": "button{color:blue}", "javascript": "let count=0",
    }


@pytest.mark.parametrize("field,value", [
    ("title", ""), ("html", ""), ("html", "x" * 24001),
    ("css", "x" * 12001), ("javascript", "x" * 12001),
])
def test_ui_panel_rejects_empty_or_oversized_code(field, value):
    fields = {"title": "Preview", "html": "<p>ok</p>", "css": "", "javascript": ""}
    fields[field] = value
    with pytest.raises(ValueError):
        build_ui_panel(**fields)
