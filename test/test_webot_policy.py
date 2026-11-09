import json
import tempfile
import unittest
from pathlib import Path

from src.webot.policy import (
    WeBotToolPolicy,
    ToolPolicyDecision,
    ToolPolicyRule,
    evaluate_tool_policy,
    get_tool_policy,
    run_tool_policy_hooks,
    save_tool_policy_config,
)


class WeBotToolPolicyTests(unittest.TestCase):
    def test_manual_rule_requires_approval(self):
        policy = WeBotToolPolicy(
            default_approval="allow",
            tools={"run_command": ToolPolicyRule(approval="manual")},
        )
        decision = evaluate_tool_policy(policy, "run_command", {"command": "ls"})
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_approval)

    def test_evaluate_manual_and_pattern_rules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_tool_policy_config(
                "alice",
                {
                    "default_approval": "allow",
                    "tools": {
                        "run_command": {
                            "approval": "manual",
                            "content_allow_patterns": ["^ls\\b"],
                        },
                        "delete_file": {
                            "approval": "deny",
                        },
                    },
                },
                project_root=tmpdir,
            )

            policy = get_tool_policy("alice", project_root=tmpdir)
            self.assertEqual(str(path), policy.definition_path)

            manual = evaluate_tool_policy(policy, "run_command", {"command": "ls -la"})
            self.assertFalse(manual.allowed)
            self.assertTrue(manual.requires_approval)

            blocked = evaluate_tool_policy(policy, "run_command", {"command": "cat notes.txt"})
            self.assertFalse(blocked.allowed)
            self.assertFalse(blocked.requires_approval)

            denied = evaluate_tool_policy(policy, "delete_file", {"filename": "notes.txt"})
            self.assertFalse(denied.allowed)
            self.assertIn("禁用", denied.reason)

    def test_hooks_write_jsonl_event_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            save_tool_policy_config(
                "alice",
                {
                    "hooks": [
                        {
                            "event": "after",
                            "type": "write_jsonl",
                            "path": "audit/tool-events.jsonl",
                            "include_result": True,
                        }
                    ]
                },
                project_root=tmpdir,
            )
            policy = get_tool_policy("alice", project_root=tmpdir)
            decision = ToolPolicyDecision(allowed=True)
            run_tool_policy_hooks(
                policy,
                event="after",
                user_id="alice",
                session_id="default",
                tool_name="read_file",
                args={"filename": "notes.txt"},
                decision=decision,
                result="hello world",
                project_root=tmpdir,
            )

            log_path = Path(tmpdir) / "data" / "user_files" / "alice" / "audit" / "tool-events.jsonl"
            self.assertTrue(log_path.is_file())
            payload = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertEqual(payload["event"], "after")
            self.assertEqual(payload["tool_name"], "read_file")
            self.assertEqual(payload["result"], "hello world")

    def test_shell_hook_can_override_decision(self):
        save_hook_policy = {
            "hooks": [
                {
                    "event": "before",
                    "type": "shell_command",
                    "command": "python3 -c \"import json,sys; json.dump({'decision': {'action': 'deny', 'reason': 'hook deny'}}, sys.stdout)\"",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            save_tool_policy_config("alice", save_hook_policy, project_root=tmpdir)
            loaded = get_tool_policy("alice", project_root=tmpdir)
            outcome = run_tool_policy_hooks(
                loaded,
                event="before",
                user_id="alice",
                session_id="default",
                tool_name="run_command",
                args={"command": "ls"},
                decision=ToolPolicyDecision(allowed=True),
                project_root=tmpdir,
            )
            self.assertFalse(outcome.decision.allowed)
            self.assertEqual(outcome.decision.reason, "hook deny")


if __name__ == "__main__":
    unittest.main()
