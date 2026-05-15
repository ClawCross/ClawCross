import sys as _sys
import os as _os
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

import hashlib
import json
import mimetypes
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent

from webot.workspace import resolve_session_workspace


mcp = FastMCP("VisionAttachment")

ATTACHMENT_MARKER = "__clawcross_multimodal_attachment__"
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
MAX_MAX_BYTES = 50 * 1024 * 1024
IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
}


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def _resolve_path(username: str, session_id: str, filename: str) -> Path:
    workspace = resolve_session_workspace(username, session_id)
    base = workspace.cwd.resolve()
    requested = Path((filename or "").strip()).expanduser()
    candidate = requested if requested.is_absolute() else base / requested
    return candidate.resolve()


def _display_path(path: Path, cwd: Path) -> str:
    try:
        return os.path.relpath(path, cwd)
    except ValueError:
        return str(path)


def _detect_image_mime(path: Path) -> str:
    with path.open("rb") as handle:
        head = handle.read(16)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"BM"):
        return "image/bmp"

    guessed = mimetypes.guess_type(path.name)[0] or ""
    if guessed.startswith("image/"):
        return guessed
    return ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_text(payload: dict) -> TextContent:
    return TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))


def _error_result(message: str) -> CallToolResult:
    return CallToolResult(
        isError=True,
        content=[_json_text({"ok": False, "error": message})],
    )


@mcp.tool()
async def list_images(username: str, session_id: str = "", folder: str = ".", max_files: int = 50) -> str:
    """
    列出指定文件夹中的图片文件。相对路径按当前会话工作目录解析；绝对路径直接使用。

    Args:
        username: 系统自动注入的当前用户；不要手动填写。
        session_id: 系统自动注入的当前会话；不要手动填写。
        folder: 文件夹路径。相对路径按当前会话工作目录解析；绝对路径直接使用。
        max_files: 最多返回多少个图片路径。

    Returns:
        图片文件列表。后续可把其中的路径传给 attach_image_to_context。
    """
    try:
        base = _resolve_path(username, session_id, folder or ".")
        if not base.exists():
            return f"❌ 文件夹不存在: {folder}"
        if not base.is_dir():
            return f"❌ 不是文件夹: {folder}"
        limit = max(1, min(int(max_files or 50), 200))
        workspace = resolve_session_workspace(username, session_id)
        cwd = workspace.cwd.resolve()
        items: list[str] = []
        for path in sorted(base.rglob("*")):
            if len(items) >= limit:
                break
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                rel = _display_path(path, cwd)
                items.append(f"- {rel} ({_format_size(path.stat().st_size)})")
        if not items:
            return "📷 当前范围内没有找到图片文件。"
        return "📷 可附加给多模态模型的图片：\n" + "\n".join(items)
    except Exception as exc:
        return f"❌ 列出图片失败: {type(exc).__name__}: {exc}"


@mcp.tool()
async def attach_image_to_context(
    username: str,
    filename: str,
    session_id: str = "",
    prompt: str = "",
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> CallToolResult:
    """
    将本地图片附加到下一次原生多模态模型调用。

    这个工具适合在需要直接看图时使用。调用后，MCP 会返回 metadata 文本和原生
    ImageContent；LangGraph/LangChain MCP adapter 会把图片作为工具结果多模态
    content block 传给下一轮模型。

    Args:
        username: 系统自动注入的当前用户；不要手动填写。
        filename: 图片路径。相对路径按当前会话工作目录解析；绝对路径直接使用。
        session_id: 系统自动注入的当前会话；不要手动填写。
        prompt: 可选，告诉模型拿到图片后重点分析什么。
        max_bytes: 最大允许图片大小，默认 20MB，最高 50MB。

    Returns:
        成功时返回 CallToolResult(TextContent + ImageContent)；失败时返回 MCP error result。
    """
    try:
        path = _resolve_path(username, session_id, filename)
        if not path.exists():
            return _error_result(f"图片不存在: {filename}")
        if not path.is_file():
            return _error_result(f"不是文件: {filename}")

        size = path.stat().st_size
        limit = max(1, min(int(max_bytes or DEFAULT_MAX_BYTES), MAX_MAX_BYTES))
        if size > limit:
            return _error_result(f"图片过大: {_format_size(size)}，当前限制 {_format_size(limit)}")

        mime_type = _detect_image_mime(path)
        if not mime_type:
            return _error_result(f"文件不是支持的图片格式: {filename}")

        metadata = {
            "ok": True,
            "type": ATTACHMENT_MARKER,
            "message": "图片已准备好，将作为下一次模型调用的原生图片附件输入。",
            "prompt": prompt.strip(),
            "attachments": [
                {
                    "type": "image",
                    "name": path.name,
                    "path": str(path),
                    "mime_type": mime_type,
                    "size": size,
                    "sha256": _sha256(path),
                }
            ],
        }
        import base64
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return CallToolResult(
            content=[
                _json_text(metadata),
                ImageContent(type="image", data=encoded, mimeType=mime_type),
            ],
        )
    except Exception as exc:
        return _error_result(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    mcp.run()
