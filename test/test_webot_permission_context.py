import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import webot.policy as webot_policy
import webot.runtime_store as webot_runtime_store
from webot.permission_context import (
    create_or_reuse_permission_request,
    resolve_permission_context,
    resolve_permission_request,
)
from webot.policy import save_tool_policy_config


class WeBotPermissionContextTests(unittest.TestCase):
    def test_manual_policy_uses_pending_and_approved_requests(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            user_files = tmp_path / "data" / "user_files"
            # _user_dir reads from webot.policy.USER_FILES_DIR when no
            # project_root is passed. Patch it so save_tool_policy_config and
            # get_tool_policy resolve to the same temp location.
            with patch.object(webot_policy, "USER_FILES_DIR", user_files), patch.object(
                webot_policy, "PROJECT_ROOT", tmp_path
            ), patch.object(
                webot_runtime_store,
                "DEFAULT_DB_PATH",
                tmp_path / "runtime.db",
            ):
                save_tool_policy_config(
                    "alice",
                    {
                        "tools": {
                            "run_command": {
                                "approval": "manual",
                                "content_allow_patterns": ["^ls\\b"],
                            }
                        }
                    },
                    project_root=tmpdir,
                )

                first = resolve_permission_context(
                    user_id="alice",
                    session_id="default",
                    tool_name="run_command",
                    args={"command": "ls -la"},
                )
                self.assertEqual(first.decision, "ask")
                self.assertTrue(first.requires_approval)

                pending = create_or_reuse_permission_request(
                    user_id="alice",
                    session_id="default",
                    tool_name="run_command",
                    args={"command": "ls -la"},
                    reason="need approval",
                )
                again = resolve_permission_context(
                    user_id="alice",
                    session_id="default",
                    tool_name="run_command",
                    args={"command": "ls -la"},
                )
                self.assertEqual(again.approval.approval_id, pending.approval_id)
                self.assertEqual(again.decision, "ask")

                updated = resolve_permission_request(
                    user_id="alice",
                    approval_id=pending.approval_id,
                    action="approved",
                    reason="approved for this command",
                    remember=False,
                )
                self.assertEqual(updated.status, "approved")

                allowed = resolve_permission_context(
                    user_id="alice",
                    session_id="default",
                    tool_name="run_command",
                    args={"command": "ls -la"},
                )
                self.assertEqual(allowed.decision, "allow")
                self.assertTrue(allowed.allowed)


if __name__ == "__main__":
    unittest.main()
