import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src" / "backend"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import webot.subagents as store
import webot.workspace as webot_workspace
from webot.subagents import create_subagent_record, upsert_subagent
from webot.workspace import resolve_session_workspace


class WeBotWorkspaceTests(unittest.TestCase):
    def test_agent_launch_directory_is_effective_but_strict_ignores_it(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / 'project'; project.mkdir()
            config = {'workspace_root': str(project)}
            with patch.object(webot_workspace, 'WORKSPACE_DIR', base / 'workspace'):
                normal = resolve_session_workspace('alice', 'cli-agent', agent_config=config)
                self.assertEqual(normal.root, project)
                self.assertEqual(normal.cwd, project)
                with self.assertRaises(ValueError):
                    resolve_session_workspace('alice', 'cli-agent', explicit_cwd='..', agent_config=config)
                with patch('webot.runtime_settings.get_runtime_settings', return_value=SimpleNamespace(
                        approval=SimpleNamespace(sandbox_security='strict'))):
                    strict = resolve_session_workspace('alice', 'cli-agent', agent_config=config)
                self.assertEqual(strict.mode, 'strict')
                self.assertFalse(strict.root.is_relative_to(project))

    def test_workspace_cannot_encompass_backend_controls(self):
        from common.runtime_paths import CONFIG_DIR
        with self.assertRaises(ValueError):
            webot_workspace.configured_workspace_root(str(CONFIG_DIR))
        with self.assertRaises(ValueError):
            webot_workspace.configured_workspace_root('relative/path')

    def test_security_levels_use_clean_roots_without_moving_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            existing = base / 'user_files' / 'alice' / 'existing.txt'
            existing.parent.mkdir(parents=True)
            existing.write_text('KEEP_EXISTING_DATA')
            with patch.object(webot_workspace, 'WORKSPACE_DIR', base / 'workspace'), \
                 patch.object(webot_workspace, 'USER_FILES_DIR', base / 'user_files'):
                normal = resolve_session_workspace('alice', 'agent-one')
                with patch('webot.runtime_settings.get_runtime_settings', return_value=SimpleNamespace(
                        approval=SimpleNamespace(sandbox_security='strict'))):
                    strict = resolve_session_workspace('alice', 'agent-one')
                    second = resolve_session_workspace('alice', 'agent-two')
                self.assertNotEqual(normal.root, strict.root)
                self.assertNotEqual(strict.root, second.root)
                self.assertEqual(list(normal.root.iterdir()), [])
                self.assertEqual(list(strict.root.iterdir()), [])
                self.assertEqual(existing.read_text(), 'KEEP_EXISTING_DATA')

    def test_shared_workspace_is_separate_from_runtime_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            original_workspace_dir = webot_workspace.WORKSPACE_DIR
            original_user_files = webot_workspace.USER_FILES_DIR
            self.addCleanup(setattr, webot_workspace, "WORKSPACE_DIR", original_workspace_dir)
            self.addCleanup(setattr, webot_workspace, "USER_FILES_DIR", original_user_files)
            webot_workspace.WORKSPACE_DIR = root / "workspace"
            webot_workspace.USER_FILES_DIR = root / "user_files"

            workspace = resolve_session_workspace("alice", "")
            self.assertEqual(workspace.mode, "shared")
            self.assertEqual(workspace.root, root / "workspace" / "users" / "alice")
            self.assertFalse(workspace.root.is_relative_to(root / "user_files"))
            self.assertEqual(list(workspace.root.iterdir()), [])

    def test_isolated_workspace_uses_subagent_root_and_cwd(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_db = store.DEFAULT_DB_PATH
            original_workspace_dir = webot_workspace.WORKSPACE_DIR
            original_user_files = webot_workspace.USER_FILES_DIR
            self.addCleanup(setattr, store, "DEFAULT_DB_PATH", original_db)
            self.addCleanup(setattr, webot_workspace, "WORKSPACE_DIR", original_workspace_dir)
            self.addCleanup(setattr, webot_workspace, "USER_FILES_DIR", original_user_files)
            store.DEFAULT_DB_PATH = Path(tmpdir) / "subagents.db"
            webot_workspace.WORKSPACE_DIR = Path(tmpdir) / "workspace"
            webot_workspace.USER_FILES_DIR = Path(tmpdir) / "user_files"
            record = create_subagent_record(
                agent_id="agent1",
                user_id="alice",
                session_id="subagent__coder__agent1",
                agent_type="coder",
                name="agent1",
                description="",
                parent_session="default",
                workspace_mode="isolated",
                cwd="repo/src",
            )
            upsert_subagent(record)
            workspace = resolve_session_workspace("alice", "subagent__coder__agent1")
            self.assertEqual(workspace.mode, "isolated")
            self.assertTrue(str(workspace.cwd).endswith("repo/src"))

    def test_worktree_workspace_creates_git_worktree(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_root = Path(tmpdir) / "workspace"
            user_files = Path(tmpdir) / "user_files"
            original_db = store.DEFAULT_DB_PATH
            original_workspace_dir = webot_workspace.WORKSPACE_DIR
            original_user_files = webot_workspace.USER_FILES_DIR
            self.addCleanup(setattr, store, "DEFAULT_DB_PATH", original_db)
            self.addCleanup(setattr, webot_workspace, "WORKSPACE_DIR", original_workspace_dir)
            self.addCleanup(setattr, webot_workspace, "USER_FILES_DIR", original_user_files)
            webot_workspace.WORKSPACE_DIR = workspace_root
            webot_workspace.USER_FILES_DIR = user_files
            store.DEFAULT_DB_PATH = Path(tmpdir) / "subagents.db"
            repo_root = workspace_root / "users" / "alice" / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
            (repo_root / "README.md").write_text("hello", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_root, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )
            record = create_subagent_record(
                agent_id="agent2",
                user_id="alice",
                session_id="subagent__coder__agent2",
                agent_type="coder",
                name="agent2",
                description="",
                parent_session="default",
                workspace_mode="worktree",
                workspace_root="repo",
            )
            upsert_subagent(record)
            workspace = resolve_session_workspace("alice", "subagent__coder__agent2")
            self.assertEqual(workspace.mode, "worktree")
            self.assertTrue((workspace.root / ".git").exists())


if __name__ == "__main__":
    unittest.main()
