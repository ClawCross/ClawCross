import json
import tempfile
import unittest
from pathlib import Path

from src.webot.profiles import (
    build_subagent_session_id,
    get_agent_profile,
    list_agent_profiles,
    parse_subagent_session_id,
)


class WeBotProfilesTests(unittest.TestCase):
    def test_build_and_parse_subagent_session_id(self):
        session_id = build_subagent_session_id("Research", "Code Audit")
        parsed = parse_subagent_session_id(session_id)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["agent_type"], "research")
        self.assertEqual(parsed["agent_id"], "code-audit")

    def test_get_agent_profile_falls_back_to_general(self):
        profile = get_agent_profile("non-existent")
        self.assertEqual(profile.agent_type, "general")
        self.assertTrue(profile.allowed_tools)

    def test_reviewer_profile_is_read_only_by_default(self):
        profile = get_agent_profile("reviewer")
        self.assertIn("read_file", profile.allowed_tools)
        self.assertNotIn("write_file", profile.allowed_tools)
        self.assertNotIn("delete_file", profile.allowed_tools)

    def test_custom_profile_can_override_builtin_profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = (
                Path(tmpdir)
                / "data"
                / "user_files"
                / "alice"
                / "webot_agent_profiles.json"
            )
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            profile_path.write_text(
                json.dumps(
                    {
                        "reviewer": {
                            "display_name": "Strict Reviewer",
                            "description": "Custom reviewer profile",
                            "prompt": "Review carefully and stop fast.",
                            "allowed_tools": ["read_file", "delete_file"],
                            "disallowed_tools": ["delete_file"],
                            "background_default": True,
                            "max_turns": 3,
                        }
                    }
                ),
                encoding="utf-8",
            )

            profile = get_agent_profile("reviewer", user_id="alice", project_root=tmpdir)
            self.assertEqual(profile.source, "user")
            self.assertEqual(profile.display_name, "Strict Reviewer")
            self.assertEqual(profile.allowed_tools, ("read_file",))
            self.assertTrue(profile.background_default)
            self.assertEqual(profile.max_turns, 3)

    def test_list_profiles_includes_custom_agent_type(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = (
                Path(tmpdir)
                / "data"
                / "user_files"
                / "alice"
                / "webot_agent_profiles.json"
            )
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            profile_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "deep-research": {
                                "description": "Custom deep research agent",
                                "system_prompt": "Search broadly but stay read-only.",
                                "allowed_tools": ["web_search", "read_file"],
                                "max_turns": 5,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            profiles = list_agent_profiles(user_id="alice", project_root=tmpdir)
            profile_names = {profile.agent_type for profile in profiles}
            self.assertIn("deep-research", profile_names)


if __name__ == "__main__":
    unittest.main()
