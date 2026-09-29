"""Messages sent to a single agent, and how each transport receives them."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

# Per-message permission mode, shared by CLI, PC web and mobile group chat.
VALID_RUN_MODES = ("chat", "readonly", "bypass", "auto")

# The same modes expressed as acpx run options.
ACPX_OVERRIDES_BY_MODE: dict[str, dict[str, Any]] = {
    "chat": {
        # All three together: tools hidden, and even if an old agent ignores
        # --allowed-tools, approve-all is moot because no tool calls happen.
        "permission_policy": "approve-all",
        "non_interactive_permissions": "",
        "allowed_tools": "",
    },
    "readonly": {
        "permission_policy": "approve-reads",
        # Writes must error out, not hang waiting for a human approval.
        "non_interactive_permissions": "deny",
    },
    "auto": {"permission_policy": "approve-reads", "non_interactive_permissions": "deny"},
    "bypass": {
        "permission_policy": "approve-all",
        "non_interactive_permissions": "",
    },
}


def normalize_run_mode(mode: str | None) -> str | None:
    """Return one of VALID_RUN_MODES, or None (no override) for empty/invalid input.

    The earlier names manual / plan / yolo still arrive from old clients.
    """
    raw = (mode or "").strip().lower()
    raw = {"manual": "chat", "plan": "readonly", "yolo": "bypass"}.get(raw, raw)
    return raw if raw in VALID_RUN_MODES else None


_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_EXACT = frozenset({
    "application/json", "application/xml", "application/javascript",
    "application/typescript", "application/x-yaml", "application/yaml",
    "application/toml", "application/x-toml",
    "application/sql", "application/graphql",
    "application/x-sh", "application/x-python",
    "application/csv", "application/x-csv",
    "application/ld+json", "application/manifest+json",
    "application/x-httpd-php",
})


def is_text_mime(mime_type: str) -> bool:
    """Whether an attachment of this MIME type can be shown to an agent as text."""
    mime = (mime_type or "").lower().strip()
    if any(mime.startswith(p) for p in _TEXT_MIME_PREFIXES):
        return True
    if mime in _TEXT_MIME_EXACT:
        return True
    return mime.endswith("+json") or mime.endswith("+xml")


def decode_text_attachment(data: str, max_chars: int = 50000) -> str | None:
    """Decode base64 attachment data as UTF-8 text, truncated; None if it isn't text."""
    try:
        raw = base64.b64decode(data)
        text = raw.decode("utf-8")
    except Exception:
        return None
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n... (文件过长，已截断，共 {len(raw)} 字节)"
    return text


def _field(att: Any, name: str) -> str:
    value = att.get(name) if isinstance(att, dict) else getattr(att, name, "")
    return str(value or "")


def _text_attachment_note(att: Any) -> str:
    name, mime = _field(att, "name"), _field(att, "mime_type")
    if is_text_mime(mime):
        decoded = decode_text_attachment(_field(att, "data"))
        if decoded is not None:
            return f"\n📄 附件「{name}」内容:\n```\n{decoded}\n```"
        return f"[附件: {name} ({mime}), 解码失败]"
    return f"[附件: {name} ({mime}), 二进制文件无法展示]"


def compose_text_prompt(text: str, attachments: list[Any] | None = None) -> str:
    """One plain-text prompt for transports that take text only (acpx).

    Images and audio travel separately as multimodal attachments; text files
    are inlined; other binaries are named.
    """
    parts: list[str] = [text]
    for att in attachments or []:
        kind = _field(att, "type")
        if kind == "image":
            parts.append(f"[附件: {_field(att, 'name')} ({_field(att, 'mime_type')}), 图片已随多模态附件发送]")
        elif kind == "audio":
            parts.append(f"[附件: {_field(att, 'name')} ({_field(att, 'mime_type')}), 音频已随多模态附件发送]")
        else:
            parts.append(_text_attachment_note(att))
    return "\n\n".join(p for p in parts if p)


def build_openai_content(text: str, attachments: list[Any] | None = None) -> str | list[dict]:
    """OpenAI chat ``content`` for a user message: plain text, or multimodal parts."""
    if not attachments:
        return text
    parts: list[dict] = [{"type": "text", "text": text}]
    for att in attachments:
        kind, mime, data = _field(att, "type"), _field(att, "mime_type"), _field(att, "data")
        if kind == "image":
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
        elif kind == "audio":
            parts.append({"type": "input_audio", "input_audio": {"data": data, "format": mime.split("/")[-1]}})
        else:
            parts.append({"type": "text", "text": _text_attachment_note(att)})
    return parts


def _data_uri(value: str) -> tuple[str, str]:
    """``data:<mime>;base64,<payload>`` → (mime, payload); ("", "") for anything else."""
    if not value.startswith("data:") or "," not in value:
        return "", ""
    header, payload = value.split(",", 1)
    return header[5:].split(";", 1)[0].strip(), payload


def parse_openai_content(content: Any) -> tuple[str, list[dict]]:
    """The text and attachments of an OpenAI chat ``content`` — the inverse of
    ``build_openai_content``: images, audio and files become attachments."""
    if not isinstance(content, list):
        return str(content or ""), []
    texts: list[str] = []
    attachments: list[dict] = []
    for part in content:
        part = part if isinstance(part, dict) else part.model_dump()
        kind = part.get("type")
        if kind == "text":
            texts.append(str(part.get("text") or ""))
        elif kind == "image_url":
            mime, data = _data_uri(str((part.get("image_url") or {}).get("url") or ""))
            if data:
                attachments.append({"type": "image", "name": "image", "mime_type": mime or "image/png", "data": data})
        elif kind == "input_audio":
            audio = part.get("input_audio") or {}
            data, fmt = str(audio.get("data") or ""), str(audio.get("format") or "wav")
            if data.startswith("data:"):
                data = _data_uri(data)[1]
            if data:
                attachments.append({"type": "audio", "name": "audio",
                                    "mime_type": fmt if "/" in fmt else f"audio/{fmt}", "data": data})
        elif kind == "file":
            file = part.get("file") or {}
            raw = str(file.get("file_data") or "")
            mime, data = _data_uri(raw) if raw.startswith("data:") else ("", raw)
            if data:
                attachments.append({"type": "file", "name": str(file.get("filename") or "file"),
                                    "mime_type": mime or "application/octet-stream", "data": data})
    return "\n".join(t for t in texts if t), attachments


@dataclass(slots=True)
class AgentMessage:
    """What a caller says to one agent.

    ``instructions`` is caller-supplied system text (for example the rules of
    the group chat the message comes from); the agent layer only forwards it.
    ``summary`` is one line for an inbox notice.
    """

    text: str
    attachments: list[dict] = field(default_factory=list)
    sender: str = ""
    instructions: str = ""
    summary: str = ""


@dataclass(slots=True)
class AgentReply:
    ok: bool
    content: str = ""
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeliveryReceipt:
    accepted: bool
    error: str = ""
