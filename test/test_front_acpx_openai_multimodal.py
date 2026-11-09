"""Main-chat ACP: OpenAI-style messages → prompt + acpx attachments."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import front  # noqa: E402


def test_acpx_openai_text_and_image_data_uri():
    png_b64 = "iVBORw0KGgo="
    data_uri = f"data:image/png;base64,{png_b64}"
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe"},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        }
    ]
    prompt, attachments = front._acpx_prompt_and_attachments_from_openai_messages(messages)
    assert "[user]" in prompt
    assert "describe" in prompt
    assert len(attachments) == 1
    assert attachments[0]["type"] == "image"
    assert attachments[0]["mime_type"] == "image/png"
    assert attachments[0]["data"] == png_b64


def test_acpx_openai_image_only_still_yields_prompt_and_attachment():
    png_b64 = "iVBORw0KGgo="
    data_uri = f"data:image/png;base64,{png_b64}"
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": data_uri}}]}]
    prompt, attachments = front._acpx_prompt_and_attachments_from_openai_messages(messages)
    assert attachments
    assert "多模态" in prompt or "附件" in prompt


def test_acpx_openai_audio_raw_base64():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "input_audio", "input_audio": {"data": "ZmFrZQ==", "format": "wav"}},
            ],
        }
    ]
    prompt, attachments = front._acpx_prompt_and_attachments_from_openai_messages(messages)
    assert len(attachments) == 1
    assert attachments[0]["type"] == "audio"
    assert attachments[0]["data"] == "ZmFrZQ=="
    assert "audio" in prompt.lower() or "附件" in prompt


def test_acpx_openai_text_file_inlined():
    raw = "hello file"
    b64 = "aGVsbG8gZmlsZQ=="  # base64 of "hello file"
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "file",
                    "file": {
                        "filename": "note.txt",
                        "file_data": f"data:text/plain;base64,{b64}",
                    },
                }
            ],
        }
    ]
    prompt, attachments = front._acpx_prompt_and_attachments_from_openai_messages(messages)
    assert raw in prompt
    assert attachments == []
