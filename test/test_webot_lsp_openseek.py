import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from src.webot.lsp import format_diagnostics, parse_tsc_output, probe_diagnostics


class WebotLspOpenSeekTests(unittest.TestCase):
    def test_parse_tsc_output_supports_paren_and_colon_formats(self):
        payload = "\n".join(
            [
                "src/a.ts(3,7): error TS2304: Cannot find name 'x'.",
                "src/b.ts:4:2 - warning TS6133: 'y' is declared but never used.",
            ]
        )

        diagnostics = parse_tsc_output(payload, "")

        self.assertEqual(len(diagnostics), 2)
        self.assertEqual(diagnostics[0].source, "tsc TS2304")
        self.assertEqual(diagnostics[0].line, 3)
        self.assertEqual(diagnostics[1].severity, "warning")

    def test_json_diagnostics_report_decode_location(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_json = Path(tmpdir) / "bad.json"
            bad_json.write_text('{"x": }', encoding="utf-8")

            payload = probe_diagnostics(username="alice", file=str(bad_json))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["language"], "json")
        self.assertEqual(payload["diagnostics"][0]["source"], "json")
        self.assertGreaterEqual(payload["diagnostics"][0]["line"], 1)

    def test_python_diagnostics_report_syntax_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_py = Path(tmpdir) / "bad.py"
            bad_py.write_text("def broken(:\n    pass\n", encoding="utf-8")

            payload = probe_diagnostics(username="alice", file=str(bad_py))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["language"], "py")
        self.assertEqual(payload["diagnostics"][0]["source"], "py_compile")
        self.assertIn("SyntaxError", payload["diagnostics"][0]["message"])

    def test_format_diagnostics_handles_empty_payload(self):
        text = format_diagnostics(
            {
                "ok": True,
                "file": "x.go",
                "diagnostics": [],
                "meta": {"runner": "reserved"},
            }
        )

        self.assertIn("no diagnostics", text)

    def test_json_output_is_serializable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ok_json = Path(tmpdir) / "ok.json"
            ok_json.write_text('{"x": 1}', encoding="utf-8")

            payload = probe_diagnostics(username="alice", file=str(ok_json))

        json.dumps(payload)
        self.assertEqual(payload["diagnostics"], [])


if __name__ == "__main__":
    unittest.main()
