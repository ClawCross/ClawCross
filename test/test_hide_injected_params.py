"""The bound tool schemas must not advertise arguments the runtime fills in.

UserAwareToolNode injects ``username`` and a per-tool session argument on every
call. Advertising them costs tokens in the tool array — which renders before the
system prompt, the most expensive part of the stable prefix — and invites the
model to guess an identity that is then overwritten.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src" / "backend"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from langchain_core.tools import StructuredTool

from webot.engine.agent import hide_injected_params


def _tool(name: str, **fields):
    """A StructuredTool whose signature carries the given arguments."""
    def run(**kwargs) -> str:
        return ""

    properties = {k: {"type": "string"} for k in fields}
    return StructuredTool(
        name=name,
        description=f"{name} for tests",
        args_schema={
            "type": "object",
            "properties": properties,
            "required": list(fields),
        },
        func=run,
    )


def _properties(bound):
    return bound["function"]["parameters"]["properties"]


class InjectedParamsAreHidden(unittest.TestCase):
    def test_username_is_dropped_for_an_injected_tool(self):
        # read_file is in USER_INJECTED_TOOLS.
        bound = hide_injected_params(_tool("read_file", username="", path=""))
        self.assertIsInstance(bound, dict)
        self.assertNotIn("username", _properties(bound))
        self.assertIn("path", _properties(bound))

    def test_session_argument_is_dropped_under_its_own_name(self):
        # read_session_plan takes source_session, not session_id.
        bound = hide_injected_params(_tool("read_session_plan", username="", source_session=""))
        self.assertNotIn("source_session", _properties(bound))
        self.assertNotIn("username", _properties(bound))

    def test_dropped_arguments_leave_required(self):
        bound = hide_injected_params(_tool("read_file", username="", path=""))
        self.assertEqual(bound["function"]["parameters"]["required"], ["path"])

    def test_a_tool_outside_the_injection_tables_keeps_the_argument(self):
        # Only arguments the runtime actually fills in are hidden; a tool the
        # tables do not name keeps its username for the model to supply.
        tool = _tool("some_other_tool", username="", note="")
        self.assertIs(hide_injected_params(tool), tool)

    def test_probe_claude_code_is_injected_not_model_supplied(self):
        # probe_claude_code used to take username from the model, which let a
        # call act as another user. It is injected like its siblings now.
        bound = hide_injected_params(_tool("probe_claude_code", username="", source_session="", prompt=""))
        self.assertEqual(set(_properties(bound)), {"prompt"})

    def test_unknown_tools_pass_through_untouched(self):
        tool = _tool("not_a_real_tool", username="", x="")
        self.assertIs(hide_injected_params(tool), tool)

    def test_a_tool_without_the_argument_is_returned_as_is(self):
        # In USER_INJECTED_TOOLS but no username in its signature: nothing to do,
        # and the tool object must survive so binding never loses it.
        tool = _tool("read_file", path="")
        self.assertIs(hide_injected_params(tool), tool)

    def test_unreadable_schema_falls_back_to_the_tool(self):
        class Broken:
            name = "read_file"

        broken = Broken()
        self.assertIs(hide_injected_params(broken), broken)


if __name__ == "__main__":
    unittest.main()
