"""Delegation cannot discard the parent's live approval/sandbox/tool policy."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from webot import runtime_settings, runtime_store, subagents
from webot.runtime import effective_session_mode
from webot.subagent_permissions import intersect_domains, parent_sessions


class SubagentPermissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(patch.stopall)
        root = Path(self.tmp.name)
        patch.object(runtime_settings, "USER_FILES_DIR", root / "users").start()
        patch.object(subagents, "DEFAULT_DB_PATH", root / "subagents.db").start()
        patch.object(runtime_store, "DEFAULT_DB_PATH", root / "runtime.db").start()
        # Saving policy must not inspect the real model/Agent catalog.
        patch.object(runtime_settings, "runtime_settings_payload", return_value={}).start()
        self.child = self.add_child("child", "parent")

    def add_child(self, name, parent, role="general"):
        session = f"subagent__{role}__{name}"
        subagents.upsert_subagent(subagents.create_subagent_record(
            agent_id=name, user_id="alice", session_id=session,
            agent_type=role, name=name, parent_session=parent, description="test"))
        return session

    def set_approval(self, session, **values):
        runtime_settings.save_runtime_settings("alice", session_id=session,
                                               settings={"approval": values})

    def approval(self, session=None):
        return runtime_settings.get_runtime_settings("alice", session or self.child).approval

    def test_live_inheritance_and_child_cannot_disable_strict(self):
        self.set_approval("parent", mode="manual", command_sandbox="landlock",
                          sandbox_security="strict", sandbox_allowed_domains=["example.com"],
                          reviewer_model="reviewer", reviewer_policy="keep answers short")
        self.set_approval(self.child, mode="bypass", command_sandbox="off",
                          sandbox_security="standard", sandbox_allowed_domains=["example.com:443", "other.com"],
                          sandbox_grants=[{"access": "network", "target": "other.com"}])
        approval = self.approval()
        self.assertEqual(approval.mode, "manual")
        self.assertEqual(approval.command_sandbox, "landlock")
        self.assertEqual(approval.sandbox_security, "strict")
        self.assertEqual(approval.sandbox_allowed_domains, ["example.com:443"])
        self.assertEqual(approval.reviewer_model, "reviewer")
        self.assertEqual(approval.reviewer_policy, "keep answers short")
        self.assertEqual(approval.sandbox_grants, [])
        runtime_settings.save_runtime_settings("alice", session_id=self.child, settings={}, reset=True)
        self.assertEqual(self.approval().sandbox_security, "strict")
        self.assertEqual(self.approval().sandbox_allowed_domains, ["example.com"])

    def test_parent_tightening_applies_to_existing_child_immediately(self):
        self.set_approval("parent", command_sandbox="auto", sandbox_allowed_domains=["example.com", "other.com"])
        self.assertEqual(self.approval().sandbox_security, "standard")
        self.set_approval("parent", sandbox_security="strict", sandbox_allowed_domains=[])
        self.assertEqual(self.approval().sandbox_security, "strict")
        self.assertEqual(self.approval().sandbox_allowed_domains, [])
        with self.assertRaises(ValueError):
            runtime_settings.remember_sandbox_grant("alice", session_id=self.child,
                                                   access="network", target="example.com")

    def test_child_stricter_mode_and_sandbox_remain(self):
        self.set_approval("parent", mode="bypass", command_sandbox="off")
        self.set_approval(self.child, mode="readonly", command_sandbox="landlock", sandbox_security="strict")
        approval = self.approval()
        self.assertEqual((approval.mode, approval.command_sandbox, approval.sandbox_security),
                         ("readonly", "landlock", "strict"))

    def test_keep_y_is_not_copied_from_parent(self):
        self.set_approval("parent", sandbox_grants=[{"access": "network", "target": "example.com"}])
        self.assertEqual(self.approval().sandbox_grants, [])
        self.set_approval(self.child, sandbox_grants=[{"access": "network", "target": "child.com"}])
        self.assertEqual([g.target for g in self.approval().sandbox_grants], ["child.com"])

    def test_legacy_and_requested_bypass_cannot_change_parent_reviewer(self):
        runtime_store.save_session_mode("alice", "parent", mode="manual")
        runtime_store.save_session_mode("alice", self.child, mode="bypass")
        self.assertEqual(self.approval().mode, "manual")
        self.assertEqual(effective_session_mode("alice", self.child, "bypass"), "manual")
        self.set_approval("parent", mode="chat")
        # The explicit legacy parent mode still wins until changed.
        runtime_store.save_session_mode("alice", "parent", mode="chat")
        self.assertEqual(effective_session_mode("alice", self.child, "review"), "chat")
        self.assertEqual(effective_session_mode("alice", "ordinary", "execute"), "execute")

    def test_nested_inheritance_does_not_overwrite_ancestor_restrictions(self):
        grandchild = self.add_child("grandchild", self.child)
        self.set_approval("parent", mode="manual", sandbox_security="strict", sandbox_allowed_domains=["example.com:443"])
        runtime_store.save_session_mode("alice", self.child, mode="bypass")
        self.assertEqual(parent_sessions("alice", grandchild), [self.child, "parent"])
        self.assertEqual(self.approval(grandchild).mode, "manual")
        self.assertEqual(self.approval(grandchild).sandbox_security, "strict")

    def test_cycle_fails_closed_and_another_user_cannot_supply_parent(self):
        subagents.update_subagent_metadata("child", "alice", parent_session=self.child)
        with self.assertRaisesRegex(ValueError, "inheritance chain"):
            self.approval()
        self.assertEqual(parent_sessions("bob", self.child), [])

    def test_domain_intersection_preserves_port_limits(self):
        self.assertEqual(intersect_domains(["a.com:443", "b.com"], ["a.com", "b.com:80", "c.com"]),
                         ["a.com:443", "b.com:80"])
        self.assertEqual(intersect_domains(["a.com:443"], ["a.com:80"]), [])

    def test_strict_child_has_separate_workspace_and_web_hard_gate(self):
        from webot import workspace
        from webot.web_security import web_access_violation
        self.set_approval("parent", mode="bypass", sandbox_security="strict",
                          sandbox_allowed_domains=["example.com:443"])
        with patch.object(workspace, "WORKSPACE_DIR", Path(self.tmp.name) / "workspaces"):
            parent_root = workspace.resolve_session_workspace("alice", "parent").root
            child = workspace.resolve_session_workspace("alice", self.child)
        self.assertEqual(child.mode, "strict")
        self.assertNotEqual(parent_root, child.root)
        self.assertTrue(child.root.is_dir())
        self.assertEqual(web_access_violation("web_fetch", {"url": "https://example.com"}, "alice", self.child), "")
        self.assertTrue(web_access_violation("web_fetch", {"url": "https://other.com"}, "alice", self.child))
        self.assertTrue(web_access_violation("web_fetch", {"url": "http://example.com"}, "alice", self.child))

    def available(self, session, metadata, state=None):
        from webot.engine.agent import available_internal_tool_names
        tools = [SimpleNamespace(name=name) for name in
                 ("read_file", "write_file", "run_command", "web_fetch", "spawn_subagent")]
        return available_internal_tool_names(tools, user_id="alice", session_id=session,
                                             state=state or {}, find_session_meta=lambda user, sid: metadata.get(sid))

    def test_parent_tool_scope_role_and_turn_scope_intersect(self):
        metadata = {"parent": {"tools": ["read_file", "run_command", "spawn_subagent"]}}
        self.assertEqual(self.available(self.child, metadata), {"read_file", "run_command"})
        research = self.add_child("research", "parent", role="research")
        self.assertEqual(self.available(research, metadata), {"read_file"})
        self.assertEqual(self.available(self.child, metadata, {"enabled_tools": ["read_file"]}), {"read_file"})
        self.set_approval("parent", mode="readonly")
        self.assertEqual(self.available(self.child, metadata), {"read_file"})
        self.set_approval("parent", mode="chat")
        self.assertEqual(self.available(self.child, metadata), set())

    def test_parent_planning_mode_cannot_be_escaped_by_coder(self):
        runtime_store.save_session_mode("alice", "parent", mode="plan")
        self.assertEqual(self.available(self.child, {}), {"read_file", "web_fetch"})

    def test_spawn_continue_and_followup_all_inherit_before_backend_call(self):
        from webot.mcp import webot
        self.set_approval("strict-parent", mode="manual", sandbox_security="strict", command_sandbox="landlock")
        seen = []

        async def backend(**kwargs):
            approval = self.approval(kwargs["session_id"])
            seen.append((kwargs["session_id"], approval.mode, approval.sandbox_security, approval.command_sandbox))
            return "completed"

        with patch.object(webot, "_recover_background_runs", AsyncMock()), \
             patch.object(webot, "_peek_session_busy", AsyncMock(return_value=False)), \
             patch.object(webot, "_call_internal_subagent", side_effect=backend), \
             patch.object(webot, "describe_session_workspace", return_value="test workspace"):
            created = asyncio.run(webot.spawn_subagent("alice", "test", name="new", parent_session="strict-parent", wait=True))
            continued = asyncio.run(webot.spawn_subagent("alice", "test again", name="new", parent_session="strict-parent", wait=True))
            followed = asyncio.run(webot.send_subagent_message("alice", "child", "continue", source_session="strict-parent", wait=True))
        self.assertIn("✅", created)
        self.assertIn("继续已有", continued)
        self.assertIn("✅", followed)
        self.assertEqual(len(seen), 3)
        self.assertEqual(seen[0][0], seen[1][0])
        self.assertTrue(all(entry[1:] == ("manual", "strict", "landlock") for entry in seen))


if __name__ == "__main__":
    unittest.main()
