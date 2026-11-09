import unittest

from src.webot.runtime import (
    build_turn_limit_message,
    filter_tools_for_mode,
    normalize_session_mode,
    resolve_max_turns,
    should_stop_for_turn_limit,
)


class WeBotRuntimeTests(unittest.TestCase):
    def test_openseek_style_agent_and_yolo_modes_are_valid(self):
        self.assertEqual(normalize_session_mode("agent"), "agent")
        self.assertEqual(normalize_session_mode("yolo"), "yolo")
        self.assertEqual(normalize_session_mode("unknown"), "execute")

    def test_plan_filters_mutating_tools_but_agent_and_yolo_do_not(self):
        tools = ["read_file", "write_file", "run_command", "lsp"]
        self.assertEqual(filter_tools_for_mode(tools, "plan"), ["read_file", "lsp"])
        self.assertEqual(filter_tools_for_mode(tools, "agent"), tools)
        self.assertEqual(filter_tools_for_mode(tools, "yolo"), tools)

    def test_resolve_max_turns_prefers_request_override(self):
        self.assertEqual(resolve_max_turns(4, 10), 4)
        self.assertEqual(resolve_max_turns(None, 10), 10)
        self.assertIsNone(resolve_max_turns(None, None))

    def test_should_stop_for_turn_limit_only_for_internal_tool_calls(self):
        internal_tools = {"read_file", "run_command"}
        self.assertTrue(
            should_stop_for_turn_limit(
                5,
                5,
                [{"name": "read_file"}],
                internal_tools,
            )
        )
        self.assertFalse(
            should_stop_for_turn_limit(
                5,
                5,
                [{"name": "external_tool"}],
                internal_tools,
            )
        )
        self.assertFalse(
            should_stop_for_turn_limit(
                4,
                5,
                [{"name": "read_file"}],
                internal_tools,
            )
        )

    def test_build_turn_limit_message_preserves_existing_text(self):
        message = build_turn_limit_message("Done so far.", 6)
        self.assertIn("Done so far.", message)
        self.assertIn("max_turns=6", message)


if __name__ == "__main__":
    unittest.main()
