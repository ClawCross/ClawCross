import sys as _sys
import os as _os
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

import os
import base64
import hashlib
import json
import tempfile
from contextlib import ExitStack
from typing import Literal
from mcp.types import CallToolResult, ImageContent, TextContent
from utils.mcp_tool_docs import DocumentedFastMCP as FastMCP

from webot.workspace import resolve_session_workspace
from webot.approval_actions import bind_file_target, file_target_outside_workspace
from webot.approval_review import authorize_action, policy_binding
from webot.policy import evaluate_tool_policy, get_tool_policy
from webot.runtime_store import consume_execution_permit

mcp = FastMCP("FileManager")

DEFAULT_PREVIEW_CHARS = 4000
DEFAULT_READ_CHARS = 12000
MAX_READ_CHARS = 50000
DEFAULT_LINE_COUNT = 200
MAX_LINE_COUNT = 2000

# Images come back as native image content, so the next model call sees them.
ATTACHMENT_MARKER = "__clawcross_multimodal_attachment__"
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _detect_image_mime(path: str) -> str:
    with open(path, "rb") as handle:
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
    # Only the file's own header counts: a text file named *.png is text.
    return ""


def _image_result(path: str, mime_type: str) -> CallToolResult:
    size = os.path.getsize(path)
    if size > MAX_IMAGE_BYTES:
        return CallToolResult(
            isError=True,
            content=[TextContent(type="text", text=f"❌ 图片过大: {_format_size(size)}，上限 {_format_size(MAX_IMAGE_BYTES)}")],
        )
    metadata = {
        "ok": True,
        "type": ATTACHMENT_MARKER,
        "attachments": [{
            "type": "image",
            "name": os.path.basename(path),
            "path": path,
            "mime_type": mime_type,
            "size": size,
            "sha256": _file_sha256(path),
        }],
    }
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    return CallToolResult(content=[
        TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)),
        ImageContent(type="image", data=encoded, mimeType=mime_type),
    ])


def _limit_value(value: int, default: int, maximum: int) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        parsed = 0
    if parsed <= 0:
        parsed = default
    return min(parsed, maximum)

async def _file_access_gate(username: str, session_id: str, tool_name: str, args: dict) -> tuple[str | None, str]:
    """Require one exact approval when a file tool crosses the workspace root."""
    normalized_session = session_id or "default"
    from webot.runtime import effective_session_mode, mode_allows_tool, PLAN_MODE_BLOCKED_TOOLS, REVIEW_MODE_BLOCKED_TOOLS
    mode = effective_session_mode(username, normalized_session)
    if not mode_allows_tool(mode, tool_name, args) or (mode == "plan" and tool_name in PLAN_MODE_BLOCKED_TOOLS) or (mode == "review" and tool_name in REVIEW_MODE_BLOCKED_TOOLS):
        return "❌ 当前模式不允许该文件操作。", ""
    workspace = resolve_session_workspace(username, session_id)
    bound = bind_file_target(tool_name, {**args, "username": username, "session_id": normalized_session},
                             username, normalized_session, workspace=workspace)
    path = bound["_resolved_path"]
    if consume_execution_permit(username, normalized_session, tool_name, bound,
                                policy_binding(username, normalized_session)):
        return None, path
    if not file_target_outside_workspace(bound):
        policy_decision = evaluate_tool_policy(get_tool_policy(username), tool_name, bound)
        if policy_decision.allowed:
            return None, path
        if not policy_decision.requires_approval:
            return "❌ " + policy_decision.reason, path
    result = await authorize_action(user_id=username, session_id=normalized_session,
                                    tool_name=tool_name, args=bound)
    return (None if result.allowed else "❌ " + result.reason), path


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
    for trim in range(4):
        candidate = blob[: len(blob) - trim] if trim else blob
        if not candidate:
            break
        try:
            candidate.decode("utf-8")
            return False
        except UnicodeDecodeError:
            continue
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
async def list_files(username: str, session_id: str = "", folder: str = ".", storage: Literal["file", "memory"] = "file", team: str | None = None) -> str:
    """
    列出目录文件，或用 storage="memory" 列出 Skill/记忆条目（编号、名称、说明，无路径）。

    :param username: 用户名（由系统自动注入，无需手动传递）
    :param folder: 要列出的目录；支持绝对路径，相对路径以当前 session cwd 为基准
    :param storage: file 为普通目录；memory 为 Skill 正文条目，此时 folder 保持默认值
    :param team: memory 范围；null 使用当前团队，空字符串为个人，列表包含团队与共享个人条目
    :return: 文件列表的描述
    """
    team = team or ""
    try:
        if storage == "memory":
            from webot.skill_memory import list_memory
            if folder not in {"", "."}:
                return "❌ memory 模式按条目管理，不接受目录路径。"
            return json.dumps({"storage": "memory", "items": list_memory(username, team)}, ensure_ascii=False)
        if storage != "file":
            return "❌ 不支持的 storage。"
        reject, user_path = await _file_access_gate(username, session_id, "list_files",
            {"folder": folder, "storage": storage, "team": team})
        if reject:
            return reject
        if not os.path.exists(user_path):
            return f"❌ 目录 '{folder}' 不存在。"
        if not os.path.isdir(user_path):
            return f"❌ '{folder}' 不是目录。"
        files = os.listdir(user_path)
        if not files:
            return f"📂 目录 '{folder}' 没有任何文件。"
        result = f"📂 目录 '{user_path}' 的文件列表：\n"
        for file_name in sorted(files):
            file_path = os.path.join(user_path, file_name)
            if os.path.isdir(file_path):
                result += f"  - {file_name}/\n"
                continue
            size = os.path.getsize(file_path)
            size_str = _format_size(size)
            result += f"  - {file_name} ({size_str})\n"
        return result
    except ValueError as e:
        return f"❌ {e}"
    except Exception as e:
        if storage == "memory":
            return "⚠️ 无法读取 memory 条目。"
        return f"⚠️ 列出文件失败: {str(e)}"

@mcp.tool(structured_output=False)
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
    storage: Literal["file", "memory"] = "file",
    team: str | None = None,
):
    """
    读取文件。文本按块返回，适合大文件渐进读取；图片（png/jpg/gif/webp/bmp）
    直接作为图片交给你查看。

    :param username: 用户名（由系统自动注入，无需手动传递）
    :param filename: file 模式为文件路径；memory 模式为 list_files 返回的条目编号或名称
    :param offset: 按字符分块读取的起点；首次传 0，之后用上一次结果给出的 offset 继续
    :param limit: 按字符分块时本次最多读取的字符数；0 表示默认值（12000），上限 50000
    :param start_line: 按行读取的起始行号（从 1 开始）；与 line_count 任一大于 0 时改为按行读取
    :param line_count: 按行读取的行数；0 表示默认值（200），上限 2000
    :param encoding: 文件编码，默认 utf-8
    :param include_sha256: 是否在结果中附上文件的 sha256，可作为之后 write_file 的 expected_sha256
    :param storage: file 为普通文件；memory 只读取 Skill 正文，不读取支持文件
    :param team: memory 范围；null 使用当前团队，空字符串为个人，读取时也可查看共享个人条目
    :return: 文件内容或错误信息
    """
    team = team or ""
    try:
        if storage == "memory":
            from webot.skill_memory import memory_target
            file_path = str(memory_target(username, filename, team, shared=True)["_path"])
            encoding = "utf-8"
        elif storage == "file":
            reject, file_path = await _file_access_gate(username, session_id, "read_file", {
                "filename": filename, "offset": offset, "limit": limit,
                "start_line": start_line, "line_count": line_count, "encoding": encoding,
                "include_sha256": include_sha256, "storage": storage, "team": team,
            })
            if reject:
                return reject
        else:
            return "❌ 不支持的 storage。"
        if not os.path.exists(file_path):
            return f"❌ 文件 '{filename}' 不存在。"
        if os.path.isdir(file_path):
            return f"❌ '{filename}' 是目录，不是文件。"

        mime_type = _detect_image_mime(file_path) if storage == "file" else ""
        if mime_type:
            return _image_result(file_path, mime_type)

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
        if storage == "memory":
            return "⚠️ 无法读取 memory 条目。"
        return f"⚠️ 读取文件失败: {str(e)}"

@mcp.tool()
async def write_file(
    username: str,
    filename: str,
    content: str = "",
    session_id: str = "",
    mode: str = "overwrite",
    start: int = 0,
    end: int = 0,
    encoding: str = "utf-8",
    expected_sha256: str = "",
    old_string: str = "",
    new_string: str = "",
    replace_all: bool = False,
    storage: Literal["file", "memory"] = "file",
    team: str | None = None,
) -> str:
    """
    创建或写入文件。改已有文件的一小段时优先用 mode="str_replace"，不要整篇
    overwrite：old_string 须在文件中原样出现且唯一，不唯一就多带几行上下文或设
    replace_all。单次 content 尽量不超过约 4000 字符，长内容分多次 append 写入。

    :param username: 用户名（由系统自动注入，无需手动传递）
    :param filename: file 模式为文件路径；memory 模式为条目编号或名称，新名称创建条目
    :param content: overwrite/append/prepend/insert/replace_range 模式下要写入的内容
    :param old_string: str_replace 模式下要查找的原文片段
    :param new_string: str_replace 模式下的替换内容
    :param replace_all: str_replace 模式下是否替换全部匹配（默认只允许唯一匹配）
    :param mode: 写入模式：overwrite / append / prepend / insert / replace_range / str_replace；文件不存在时只允许 overwrite 或 append
    :param start: insert / replace_range 模式下的起始字符位置（从 0 开始）
    :param end: replace_range 模式下的结束字符位置（不含）
    :param encoding: 文件编码，默认 utf-8
    :param expected_sha256: 可选的并发保护：文件当前 sha256 与之不一致时拒绝写入
    :param storage: file 为普通文件；memory 仅写入 Skill 正文，接受 Markdown 并维护元信息和索引
    :param team: memory 范围；null 使用当前团队，空字符串为个人，修改不会回退到共享个人条目
    :return: 操作结果描述
    """
    normalized_mode_check = (mode or "overwrite").strip().lower()
    if (old_string or new_string) and normalized_mode_check != "str_replace":
        return (
            "❌ 传了 old_string/new_string 但 mode 不是 'str_replace'（当前是 "
            f"'{mode}'）。为避免把文件误清空/覆盖，已阻止执行——"
            "请显式传 mode=\"str_replace\"。"
        )

    team = team or ""
    guard = ExitStack()
    try:
        entry = None
        if storage == "memory":
            from webot.skill_memory import memory_lock, memory_target, prepare_content, public_entry, refresh_index
            guard.enter_context(memory_lock(username, team))
            entry = memory_target(username, filename, team, create=normalized_mode_check in {"overwrite", "append", "create"})
            file_path = str(entry["_path"])
            encoding = "utf-8"
        elif storage == "file":
            reject, file_path = await _file_access_gate(username, session_id, "write_file", {
                "filename": filename, "content": content, "mode": mode,
                "start": start, "end": end, "encoding": encoding,
                "expected_sha256": expected_sha256, "old_string": old_string,
                "new_string": new_string, "replace_all": replace_all,
                "storage": storage, "team": team,
            })
            if reject:
                return reject
        else:
            return "❌ 不支持的 storage。"
        existing = os.path.exists(file_path)
        if entry is not None and normalized_mode_check in {"create", "update"}:
            if normalized_mode_check == "create" and existing:
                return "❌ Memory entry already exists; read it before updating."
            mode = normalized_mode_check = "overwrite"
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
        elif normalized_mode_check not in {"overwrite", "append"}:
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
        elif normalized_mode == "str_replace":
            if not old_string:
                return "❌ str_replace 模式需要提供 old_string。"
            if old_string == new_string:
                return "❌ old_string 和 new_string 不能相同。"
            occurrences = existing_text.count(old_string)
            if occurrences == 0:
                return (
                    f"❌ 在 '{filename}' 中没有找到匹配的 old_string。\n"
                    "请确认内容（含空白/缩进/换行）与文件原文逐字符一致，"
                    "必要时先用 read_file 核对原文。"
                )
            if occurrences > 1 and not replace_all:
                return (
                    f"❌ old_string 在 '{filename}' 中匹配到 {occurrences} 处，不唯一。\n"
                    "请把 old_string 写得更具体（多带几行上下文使其唯一），"
                    "或显式传 replace_all=true 替换全部匹配。"
                )
            new_content = existing_text.replace(
                old_string, new_string, -1 if replace_all else 1
            )
        else:
            return f"❌ 不支持的写入模式 '{mode}'。"

        if entry is not None:
            new_content = prepare_content(entry, new_content)
        _atomic_write_text(file_path, new_content, encoding=encoding, atomic=True)
        if entry is not None:
            refresh_index(username, team)
            saved = memory_target(username, entry["id"], team)
            return json.dumps({"success": True, "storage": "memory", **public_entry(saved),
                               "sha256": _file_sha256(file_path), "chars": len(new_content)}, ensure_ascii=False)
        action = {
            "overwrite": "已保存",
            "append": "已追加",
            "prepend": "已前置追加",
            "insert": "已插入",
            "replace_range": "已范围替换",
            "str_replace": "已替换",
        }[normalized_mode]
        return (
            f"✅ 文件 '{filename}' {action}。\n"
            f"📏 当前长度: {len(new_content)} 字符\n"
            f"🔐 sha256: {_file_sha256(file_path)}"
        )
    except ValueError as e:
        return f"❌ {str(e)}"
    except Exception as e:
        if storage == "memory":
            return "⚠️ 无法写入 memory 条目。"
        return f"⚠️ 写入文件失败: {str(e)}"
    finally:
        guard.close()

@mcp.tool()
async def delete_file(username: str, filename: str, session_id: str = "", storage: Literal["file", "memory"] = "file", team: str | None = None) -> str:
    """
    删除用户的指定文件。

    :param username: 用户名（由系统自动注入，无需手动传递）
    :param filename: file 模式为文件路径；memory 模式为条目编号或名称
    :param storage: file 删除普通文件；memory 仅删除 Skill 正文，支持文件保留
    :param team: memory 范围；null 使用当前团队，空字符串为个人
    :return: 操作结果描述
    """
    team = team or ""
    try:
        if storage == "memory":
            from webot.skill_memory import memory_lock, memory_target, public_entry, refresh_index
            with memory_lock(username, team):
                entry = memory_target(username, filename, team)
                entry["_path"].unlink()
                refresh_index(username, team)
                return json.dumps({"success": True, "deleted": public_entry(entry)}, ensure_ascii=False)
        if storage != "file":
            return "❌ 不支持的 storage。"
        reject, file_path = await _file_access_gate(username, session_id, "delete_file",
            {"filename": filename, "storage": storage, "team": team})
        if reject:
            return reject
        if not os.path.exists(file_path):
            return f"❌ 文件 '{filename}' 不存在，无法删除。"
        os.remove(file_path)
        return f"🗑️ 文件 '{filename}' 已删除。"
    except ValueError as e:
        return f"❌ {str(e)}"
    except Exception as e:
        if storage == "memory":
            return "⚠️ 无法删除 memory 条目。"
        return f"⚠️ 删除文件失败: {str(e)}"

if __name__ == "__main__":
    mcp.run()
