"""AcpxAdapter stdout parsing (JSON-RPC stream vs legacy JSON)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from integrations.acpx_adapter import AcpxAdapter, acpx_options_from_agent, normalize_acpx_run_options  # noqa: E402


def test_extract_text_jsonrpc_agent_message_chunks():
    sample = """
{"jsonrpc":"2.0","id":5,"method":"session/prompt","params":{"sessionId":"x","prompt":[{"type":"text","text":"hi"}]}}
{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"x","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":""}}}}
{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"x","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"OK"}}}}
{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"x","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"_TEST"}}}}
{"jsonrpc":"2.0","id":5,"result":{"stopReason":"end_turn"}}
""".strip()
    out = AcpxAdapter._extract_text(sample)
    assert out == "OK_TEST"


def test_extract_text_legacy_reply_key():
    legacy = '{"reply": "hello"}\n'
    assert AcpxAdapter._extract_text(legacy) == "hello"


def test_normalize_acpx_run_options_clamps_and_parses(monkeypatch):
    monkeypatch.setenv("ACPX_APPROVE_ALL", "0")
    monkeypatch.setenv("ACPX_NON_INTERACTIVE_PERMISSIONS", "read-only")

    opts = normalize_acpx_run_options({"timeout_sec": "99999", "ttl_sec": 1})

    assert opts["timeout_sec"] == 3600
    assert opts["ttl_sec"] == 60
    assert opts["approve_all"] is False
    assert opts["non_interactive_permissions"] == "read-only"


def test_acpx_options_from_agent_prefers_meta_acp_then_overrides():
    agent = {
        "timeout_sec": 30,
        "meta": {
            "timeout_sec": 60,
            "acp": {
                "timeout_sec": 120,
                "ttl_sec": 600,
                "approve_all": False,
                "non_interactive_permissions": "workspace-write",
            },
        },
    }

    opts = acpx_options_from_agent(agent, overrides={"timeout_sec": 240})

    assert opts["timeout_sec"] == 240
    assert opts["ttl_sec"] == 600
    assert opts["approve_all"] is False
    assert opts["non_interactive_permissions"] == "workspace-write"
