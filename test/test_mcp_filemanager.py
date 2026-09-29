import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import mcp_servers.filemanager as filemanager
from webot.approval_actions import bind_file_target
from webot.approval_review import ApprovalResult
from webot import runtime_store as store
from webot.workspace import SessionWorkspace


class FileManagerTests(unittest.TestCase):
    def test_read_file_supports_offset_pagination(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "notes.txt").write_text("abcdefghij", encoding="utf-8")
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            with patch.object(filemanager, "resolve_session_workspace", return_value=workspace):
                result = asyncio.run(
                    filemanager.read_file("alice", "notes.txt", offset=2, limit=4)
                )
            self.assertIn("cdef", result)
            self.assertIn("offset: 2", result)
            self.assertIn("offset=6", result)

    def test_read_file_supports_line_windows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "lines.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            with patch.object(filemanager, "resolve_session_workspace", return_value=workspace):
                result = asyncio.run(
                    filemanager.read_file("alice", "lines.txt", start_line=2, line_count=2)
                )
            self.assertIn("行 2-3", result)
            self.assertIn("b\nc\n", result)
            self.assertIn("start_line=4", result)

    def test_read_file_treats_utf8_text_as_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "notes.md").write_text("你好，普通文本。\n第二行 😄\n", encoding="utf-8")
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            with patch.object(filemanager, "resolve_session_workspace", return_value=workspace):
                result = asyncio.run(filemanager.read_file("alice", "notes.md"))
            self.assertIn("你好，普通文本。", result)
            self.assertNotIn("二进制文件", result)

    def test_write_file_supports_replace_range_and_sha_guard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "draft.txt"
            path.write_text("hello world", encoding="utf-8")
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            with patch.object(filemanager, "resolve_session_workspace", return_value=workspace):
                sha = filemanager._file_sha256(str(path))
                result = asyncio.run(
                    filemanager.write_file(
                        "alice",
                        "draft.txt",
                        "Claw",
                        mode="replace_range",
                        start=6,
                        end=11,
                        expected_sha256=sha,
                    )
                )
            self.assertIn("已范围替换", result)
            self.assertEqual(path.read_text(encoding="utf-8"), "hello Claw")

    def test_write_file_rejects_mismatched_sha(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "draft.txt"
            path.write_text("hello", encoding="utf-8")
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            with patch.object(filemanager, "resolve_session_workspace", return_value=workspace):
                result = asyncio.run(
                    filemanager.write_file(
                        "alice",
                        "draft.txt",
                        "x",
                        expected_sha256="bad",
                    )
                )
            self.assertIn("sha256 不匹配", result)
            self.assertEqual(path.read_text(encoding="utf-8"), "hello")

    def test_read_file_outside_workspace_requires_approval(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "workspace"
            root.mkdir()
            outside = Path(tmpdir) / "outside.txt"
            outside.write_text("outside content", encoding="utf-8")
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            with patch.object(filemanager, "resolve_session_workspace", return_value=workspace), \
                 patch.object(filemanager, "consume_execution_permit", return_value=False), \
                 patch.object(filemanager, "policy_binding", return_value="binding"), \
                 patch.object(filemanager, "authorize_action", new=AsyncMock(return_value=ApprovalResult(False, "未获批准"))) as review:
                result = asyncio.run(filemanager.read_file("alice", str(outside)))
            self.assertIn("未获批准", result)
            self.assertEqual(review.await_count, 1)
            self.assertEqual(review.call_args.kwargs["args"]["_resolved_path"], str(outside))

    def test_read_file_allows_relative_traversal_from_cwd(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "workspace"
            root.mkdir()
            outside = Path(tmpdir) / "outside.txt"
            outside.write_text("relative traversal", encoding="utf-8")
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            with patch.object(filemanager, "resolve_session_workspace", return_value=workspace), \
                 patch.object(filemanager, "consume_execution_permit", return_value=False), \
                 patch.object(filemanager, "policy_binding", return_value="binding"), \
                 patch.object(filemanager, "authorize_action", new=AsyncMock(return_value=ApprovalResult(True))):
                result = asyncio.run(filemanager.read_file("alice", "../outside.txt"))
            self.assertIn("relative traversal", result)

    def test_list_files_accepts_absolute_folder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "workspace"
            folder = Path(tmpdir) / "other"
            root.mkdir()
            folder.mkdir()
            (folder / "notes.txt").write_text("hello", encoding="utf-8")
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            with patch.object(filemanager, "resolve_session_workspace", return_value=workspace), \
                 patch.object(filemanager, "consume_execution_permit", return_value=False), \
                 patch.object(filemanager, "policy_binding", return_value="binding"), \
                 patch.object(filemanager, "authorize_action", new=AsyncMock(return_value=ApprovalResult(True))):
                result = asyncio.run(filemanager.list_files("alice", folder=str(folder)))
            self.assertIn(str(folder), result)
            self.assertIn("notes.txt", result)

    def test_outside_file_permit_is_bound_to_symlink_target(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "workspace"
            root.mkdir()
            first = base / "first.txt"
            second = base / "second.txt"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")
            alias = root / "alias.txt"
            alias.symlink_to(first)
            workspace = SessionWorkspace(root=root, cwd=root, mode="shared", remote="")
            args = bind_file_target("read_file", {"username": "alice", "session_id": "default", "filename": "alias.txt"},
                                    "alice", "default", workspace=workspace)
            with patch.object(store, "DEFAULT_DB_PATH", base / "permits.db"), \
                 patch.object(filemanager, "resolve_session_workspace", return_value=workspace), \
                 patch.object(filemanager, "policy_binding", return_value="binding"), \
                 patch.object(filemanager, "authorize_action", new=AsyncMock(return_value=ApprovalResult(False, "需重新批准"))) as review:
                store.issue_execution_permit("alice", "default", "read_file", args, "binding")
                alias.unlink()
                alias.symlink_to(second)
                result = asyncio.run(filemanager.read_file("alice", "alias.txt"))
            self.assertIn("需重新批准", result)
            self.assertNotIn("second", result)
            review.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
