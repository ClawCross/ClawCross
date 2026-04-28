import sys as _sys
import os as _os
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

import os
import hashlib
import tempfile
from mcp.server.fastmcp import FastMCP

from webot.workspace import resolve_session_workspace

mcp = FastMCP("FileManager")

DEFAULT_PREVIEW_CHARS = 4000
DEFAULT_READ_CHARS = 12000
MAX_READ_CHARS = 50000
DEFAULT_LINE_COUNT = 200
MAX_LINE_COUNT = 2000


def _limit_value(value: int, default: int, maximum: int) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        parsed = 0
    if parsed <= 0:
        parsed = default
    return min(parsed, maximum)

def _user_dir(username: str, session_id: str = "") -> str:
    """获取用户目录路径，自动创建。

    :param username: 用户名
    :return: 用户目录的绝对路径
    """

    return str(resolve_session_workspace(username, session_id).root)

def _safe_path(username: str, filename: str, session_id: str = "") -> str:
    """拼接安全路径，防止路径穿越。

    :param username: 用户名
    :param filename: 文件名
    :return: 文件的完整绝对路径
    :raises ValueError: 如果路径穿越尝试被检测到
    """
    user_path = _user_dir(username, session_id)
    full_path = os.path.abspath(os.path.normpath(os.path.join(user_path, filename)))
    # 确保路径在用户目录内
    if os.path.commonpath([user_path, full_path]) != user_path:
        raise ValueError(f"非法路径: {filename}")
    return full_path


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_binary_preview(path: str, preview_bytes: int = 256) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(preview_bytes)


def _is_binary_preview(blob: bytes) -> bool:
    if not blob:
        return False
    if b"\x00" in blob:
        return True
    text_like = sum(1 for b in blob if 32 <= b <= 126 or b in (9, 10, 13))
    return (text_like / len(blob)) < 0.7


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def _read_text_chunk(path: str, *, offset: int = 0, limit: int = DEFAULT_READ_CHARS, encoding: str = "utf-8") -> tuple[str, int, int]:
    safe_offset = max(0, int(offset or 0))
    safe_limit = _limit_value(limit, DEFAULT_READ_CHARS, MAX_READ_CHARS)
    with open(path, "r", encoding=encoding, errors="replace") as handle:
        handle.seek(safe_offset)
        content = handle.read(safe_limit)
        next_offset = handle.tell()
    return content, safe_offset, next_offset


def _read_text_lines(path: str, *, start_line: int = 1, line_count: int = DEFAULT_LINE_COUNT, encoding: str = "utf-8") -> tuple[str, int, int, bool]:
    safe_start = max(1, int(start_line or 1))
    safe_count = _limit_value(line_count, DEFAULT_LINE_COUNT, MAX_LINE_COUNT)
    end_line = safe_start + safe_count - 1
    collected: list[str] = []
    has_more = False
    with open(path, "r", encoding=encoding, errors="replace") as handle:
        for idx, line in enumerate(handle, start=1):
            if idx < safe_start:
                continue
            if idx > end_line:
                has_more = True
                break
            collected.append(line)
    return "".join(collected), safe_start, safe_start + len(collected) - 1, has_more


def _atomic_write_text(path: str, content: str, *, encoding: str = "utf-8", atomic: bool = True) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    if not atomic:
        with open(path, "w", encoding=encoding) as handle:
            handle.write(content)
        return

    fd, tmp_path = tempfile.mkstemp(prefix=".mcp-write-", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

@mcp.tool()
async def list_files(username: str, session_id: str = "") -> str:
    """
    列出当前用户的所有文件。

    :param username: 用户名（由系统自动注入，无需手动传递）
    :return: 用户文件列表的描述
    """
    user_path = _user_dir(username, session_id)
    try:
        files = os.listdir(user_path)
        if not files:
            return "📂 你还没有任何文件。"
        result = "📂 你的文件列表：\n"
        for file_name in sorted(files):
            file_path = os.path.join(user_path, file_name)
            if os.path.isdir(file_path):
                result += f"  - {file_name}/\n"
                continue
            size = os.path.getsize(file_path)
            size_str = _format_size(size)
            result += f"  - {file_name} ({size_str})\n"
        return result
    except Exception as e:
        return f"⚠️ 列出文件失败: {str(e)}"

@mcp.tool()
async def read_file(
    username: str,
    filename: str,
    session_id: str = "",
    offset: int = 0,
    limit: int = 0,
    start_line: int = 0,
    line_count: int = 0,
    encoding: str = "utf-8",
    include_sha256: bool = False,
) -> str:
    """
    读取用户的指定文件内容。
    默认返回受限长度的文本分块，适合大文件渐进读取。

    :param username: 用户名（由系统自动注入，无需手动传递）
    :param filename: 要读取的文件名
    :return: 文件内容或错误信息
    """
    try:
        file_path = _safe_path(username, filename, session_id)
        if not os.path.exists(file_path):
            return f"❌ 文件 '{filename}' 不存在。"
        if os.path.isdir(file_path):
            return f"❌ '{filename}' 是目录，不是文件。"

        size = os.path.getsize(file_path)
        preview = _read_binary_preview(file_path)
        sha_text = f"\n🔐 sha256: {_file_sha256(file_path)}" if include_sha256 else ""
        if _is_binary_preview(preview):
            return (
                f"📄 文件 '{filename}' 是二进制文件。\n"
                f"📦 大小: {_format_size(size)}{sha_text}\n"
                "建议只读取元信息，或改用专门的二进制处理工具。"
            )

        if size == 0:
            return f"📄 文件 '{filename}' 是空的。{sha_text}"

        if start_line > 0 or line_count > 0:
            content, actual_start, actual_end, has_more = _read_text_lines(
                file_path,
                start_line=start_line or 1,
                line_count=line_count or DEFAULT_LINE_COUNT,
                encoding=encoding,
            )
            next_hint = f"\n➡️ 下一段可用 `start_line={actual_end + 1}` 继续读取。" if has_more else ""
            return (
                f"📄 文件 '{filename}' 行 {actual_start}-{actual_end}：\n"
                f"📦 大小: {_format_size(size)}{sha_text}\n\n"
                f"{content}{next_hint}"
            )

        content, used_offset, next_offset = _read_text_chunk(
            file_path,
            offset=offset,
            limit=limit or DEFAULT_READ_CHARS,
            encoding=encoding,
        )
        if not content:
            return (
                f"📄 文件 '{filename}' 已读到末尾。\n"
                f"📦 大小: {_format_size(size)}\n"
                f"📍 offset: {max(0, int(offset or 0))}{sha_text}"
            )

        truncated = next_offset < size
        suffix = f"\n➡️ 下一段可用 `offset={next_offset}` 继续读取。" if truncated else ""
        return (
            f"📄 文件 '{filename}' 的内容片段：\n"
            f"📦 大小: {_format_size(size)}\n"
            f"📍 offset: {used_offset}\n"
            f"📏 returned_chars: {len(content)}{sha_text}\n\n"
            f"{content}{suffix}"
        )
    except ValueError as e:
        return f"❌ {str(e)}"
    except Exception as e:
        return f"⚠️ 读取文件失败: {str(e)}"

@mcp.tool()
async def write_file(
    username: str,
    filename: str,
    content: str,
    session_id: str = "",
    mode: str = "overwrite",
    start: int = 0,
    end: int = 0,
    encoding: str = "utf-8",
    expected_sha256: str = "",
    atomic: bool = True,
) -> str:
    """
    创建或写入用户的指定文件。
    支持 overwrite / append / prepend / insert / replace_range。
    为了降低 tool 参数过长导致的截断风险，单次 content 建议尽量控制在约 4000 字符以内；
    长内容更适合先建文件，再多次 append，或多次 replace_range 分段写入同一文件。

    :param username: 用户名（由系统自动注入，无需手动传递）
    :param filename: 要写入的文件名
    :param content: 要写入的内容
    :return: 操作结果描述
    """
    try:
        file_path = _safe_path(username, filename, session_id)
        existing = os.path.exists(file_path)
        existing_text = ""
        if existing:
            if os.path.isdir(file_path):
                return f"❌ '{filename}' 是目录，不能直接写入。"
            if expected_sha256:
                actual_sha = _file_sha256(file_path)
                if actual_sha != expected_sha256:
                    return (
                        f"❌ 文件 '{filename}' 已变化，sha256 不匹配。\n"
                        f"当前: {actual_sha}\n"
                        f"期望: {expected_sha256}"
                    )
            with open(file_path, "r", encoding=encoding, errors="replace") as handle:
                existing_text = handle.read()
        elif mode not in {"overwrite", "append"}:
            return f"❌ 文件 '{filename}' 不存在，模式 '{mode}' 需要已有文件。"

        normalized_mode = (mode or "overwrite").strip().lower()
        if normalized_mode == "overwrite":
            new_content = content
        elif normalized_mode == "append":
            new_content = existing_text + content
        elif normalized_mode == "prepend":
            new_content = content + existing_text
        elif normalized_mode == "insert":
            safe_start = max(0, min(int(start or 0), len(existing_text)))
            new_content = existing_text[:safe_start] + content + existing_text[safe_start:]
        elif normalized_mode == "replace_range":
            safe_start = max(0, min(int(start or 0), len(existing_text)))
            safe_end = max(safe_start, min(int(end or safe_start), len(existing_text)))
            new_content = existing_text[:safe_start] + content + existing_text[safe_end:]
        else:
            return f"❌ 不支持的写入模式 '{mode}'。"

        _atomic_write_text(file_path, new_content, encoding=encoding, atomic=bool(atomic))
        action = {
            "overwrite": "已保存",
            "append": "已追加",
            "prepend": "已前置追加",
            "insert": "已插入",
            "replace_range": "已范围替换",
        }[normalized_mode]
        return (
            f"✅ 文件 '{filename}' {action}。\n"
            f"📏 当前长度: {len(new_content)} 字符\n"
            f"🔐 sha256: {_file_sha256(file_path)}"
        )
    except ValueError as e:
        return f"❌ {str(e)}"
    except Exception as e:
        return f"⚠️ 写入文件失败: {str(e)}"

@mcp.tool()
async def append_file(username: str, filename: str, content: str, session_id: str = "") -> str:
    """
    向用户的指定文件末尾追加内容。

    :param username: 用户名（由系统自动注入，无需手动传递）
    :param filename: 要追加内容的文件名
    :param content: 要追加的内容
    :return: 操作结果描述
    """
    return await write_file(
        username=username,
        filename=filename,
        content=content,
        session_id=session_id,
        mode="append",
    )

@mcp.tool()
async def delete_file(username: str, filename: str, session_id: str = "") -> str:
    """
    删除用户的指定文件。

    :param username: 用户名（由系统自动注入，无需手动传递）
    :param filename: 要删除的文件名
    :return: 操作结果描述
    """
    try:
        file_path = _safe_path(username, filename, session_id)
        if not os.path.exists(file_path):
            return f"❌ 文件 '{filename}' 不存在，无法删除。"
        os.remove(file_path)
        return f"🗑️ 文件 '{filename}' 已删除。"
    except ValueError as e:
        return f"❌ {str(e)}"
    except Exception as e:
        return f"⚠️ 删除文件失败: {str(e)}"

if __name__ == "__main__":
    mcp.run()
