"""An OpenAI chat message becomes an agent message: text plus attachments, whatever the runtime."""

import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "backend"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agents.messages import build_openai_content, compose_text_prompt, parse_openai_content  # noqa: E402

PNG = "iVBORw0KGgo="


def test_text_image_audio_and_files_become_attachments():
    note = base64.b64encode("第一行\nsecond".encode()).decode()
    text, attachments = parse_openai_content([
        {"type": "text", "text": "describe"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG}"}},
        {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},  # not inline: left out
        {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "webm"}},
        {"type": "file", "file": {"filename": "notes.txt", "file_data": f"data:text/plain;base64,{note}"}},
        {"type": "file", "file": {"filename": "blob.bin", "file_data": "AAECAw=="}},
    ])
    assert text == "describe"
    assert [(a["type"], a["mime_type"]) for a in attachments] == [
        ("image", "image/png"), ("audio", "audio/webm"), ("file", "text/plain"), ("file", "application/octet-stream")]
    prompt = compose_text_prompt(text, attachments)  # what a text-only runtime (acpx) is sent
    assert "第一行" in prompt and "图片已随多模态附件发送" in prompt and "blob.bin" in prompt


def test_plain_text_and_the_round_trip():
    assert parse_openai_content("hi") == ("hi", [])
    image = {"type": "image", "name": "image", "mime_type": "image/png", "data": PNG}
    assert parse_openai_content(build_openai_content("look", [image])) == ("look", [image])
