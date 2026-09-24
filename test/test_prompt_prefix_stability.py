"""Guards on the cacheable prefix of every request sent to the model.

Prompt/KV caching is a prefix match, so two things must hold no matter which
branch assembles the turn: the system message is exactly ``base_prompt``, and
per-turn runtime state rides at the tail (and only when it changed).
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from webot.context import assemble_input_messages, render_runtime_context_block

BASE = "stable system prompt"
STATE = "【Runtime Context】\nworkspace: /tmp/ws\ntodo::pending::ship it"


def _tool_round_history():
    """History as the loop sees it right after a tool batch came back."""
    return [
        HumanMessage(content="build the thing"),
        AIMessage(content="", tool_calls=[{"name": "bash", "args": {}, "id": "call_1"}]),
        ToolMessage(content="ok", tool_call_id="call_1"),
    ]


class SystemMessageStaysStable(unittest.TestCase):
    def test_system_is_exactly_base_prompt_on_first_call(self):
        messages, _ = assemble_input_messages(
            base_prompt=BASE,
            history=[HumanMessage(content="hi")],
            runtime_state=STATE,
        )
        self.assertIsInstance(messages[0], SystemMessage)
        self.assertEqual(messages[0].content, BASE)

    def test_system_is_exactly_base_prompt_on_tool_rounds(self):
        messages, _ = assemble_input_messages(
            base_prompt=BASE,
            history=_tool_round_history(),
            runtime_state=STATE,
        )
        self.assertEqual(messages[0].content, BASE)

    def test_system_is_identical_across_both_branches(self):
        first, _ = assemble_input_messages(
            base_prompt=BASE, history=[HumanMessage(content="hi")], runtime_state=STATE
        )
        during, _ = assemble_input_messages(
            base_prompt=BASE, history=_tool_round_history(), runtime_state=STATE
        )
        self.assertEqual(first[0].content, during[0].content)


class RuntimeStateRidesAtTheTail(unittest.TestCase):
    def test_first_call_merges_state_into_the_user_message(self):
        messages, injected = assemble_input_messages(
            base_prompt=BASE,
            history=[HumanMessage(content="build the thing")],
            runtime_state=STATE,
        )
        self.assertEqual(injected, STATE)
        self.assertIsInstance(messages[-1], HumanMessage)
        self.assertIn(STATE, messages[-1].content)
        self.assertIn("build the thing", messages[-1].content)

    def test_state_follows_the_user_text_not_precedes_it(self):
        # The stored message has no state block, so every later request sees the
        # bare text. State first would put the divergence at the start of the
        # message and force the whole (possibly huge) input to be re-processed;
        # state last keeps the input inside the shared prefix.
        long_input = "x" * 20000
        messages, _ = assemble_input_messages(
            base_prompt=BASE,
            history=[HumanMessage(content=long_input)],
            runtime_state=STATE,
        )
        sent = messages[-1].content
        self.assertTrue(sent.startswith(long_input))
        self.assertLess(sent.index(long_input), sent.index(STATE))

    def test_multimodal_user_message_keeps_its_blocks(self):
        blocks = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]
        messages, _ = assemble_input_messages(
            base_prompt=BASE,
            history=[HumanMessage(content=list(blocks))],
            runtime_state=STATE,
        )
        content = messages[-1].content
        self.assertEqual(content[:-1], blocks)
        self.assertEqual(content[-1]["type"], "text")
        self.assertIn(STATE, content[-1]["text"])

    def test_tool_round_appends_after_every_tool_result(self):
        history = _tool_round_history()
        messages, injected = assemble_input_messages(
            base_prompt=BASE, history=history, runtime_state=STATE
        )
        self.assertEqual(injected, STATE)
        # tool_calls -> ToolMessage pairing must survive: the state message is
        # appended after the tool results, never substituted for one.
        self.assertIsInstance(messages[-2], ToolMessage)
        self.assertIsInstance(messages[-1], HumanMessage)
        self.assertIn(STATE, messages[-1].content)

    def test_history_is_never_mutated(self):
        history = _tool_round_history()
        before = [m.content for m in history]
        assemble_input_messages(base_prompt=BASE, history=history, runtime_state=STATE)
        self.assertEqual([m.content for m in history], before)


class UnchangedStateIsNotResent(unittest.TestCase):
    def test_tool_round_ends_on_a_stored_message_when_state_is_unchanged(self):
        history = _tool_round_history()
        messages, injected = assemble_input_messages(
            base_prompt=BASE,
            history=history,
            runtime_state=STATE,
            last_sent_state=STATE,
        )
        self.assertEqual(injected, "")
        # Nothing ephemeral at the tail, so this request's cache entry ends on
        # content the next request still has.
        self.assertEqual(len(messages), len(history) + 1)
        self.assertIsInstance(messages[-1], ToolMessage)

    def test_changed_state_is_resent(self):
        messages, injected = assemble_input_messages(
            base_prompt=BASE,
            history=_tool_round_history(),
            runtime_state=STATE + "\ntodo::done::ship it",
            last_sent_state=STATE,
        )
        self.assertTrue(injected)
        self.assertIsInstance(messages[-1], HumanMessage)

    def test_first_call_resends_even_when_unchanged(self):
        _, injected = assemble_input_messages(
            base_prompt=BASE,
            history=[HumanMessage(content="next question")],
            runtime_state=STATE,
            last_sent_state=STATE,
        )
        self.assertEqual(injected, STATE)


class DegenerateInputs(unittest.TestCase):
    def test_empty_state_injects_nothing(self):
        history = [HumanMessage(content="hi")]
        messages, injected = assemble_input_messages(
            base_prompt=BASE, history=history, runtime_state=""
        )
        self.assertEqual(injected, "")
        self.assertEqual(messages[1:], history)

    def test_empty_history_injects_nothing(self):
        messages, injected = assemble_input_messages(
            base_prompt=BASE, history=[], runtime_state=STATE
        )
        self.assertEqual(injected, "")
        self.assertEqual(len(messages), 1)

    def test_ai_message_tail_never_reaches_the_system_message(self):
        history = [HumanMessage(content="hi"), AIMessage(content="done")]
        messages, injected = assemble_input_messages(
            base_prompt=BASE, history=history, runtime_state=STATE
        )
        self.assertEqual(injected, "")
        self.assertEqual(messages[0].content, BASE)
        self.assertNotIn(STATE, messages[0].content)


class WorkspaceLivesInTheSystemPrompt(unittest.TestCase):
    """Workspace is fixed per session, so it is carried by the stable system
    prompt instead of being re-sent in the per-turn block."""

    def test_runtime_block_omits_workspace_by_default(self):
        block = render_runtime_context_block(todos={"items": [{"status": "pending", "step": "go"}]})
        self.assertNotIn("workspace:", block)
        self.assertIn("todo::pending::go", block)

    def test_runtime_block_still_renders_workspace_when_asked(self):
        block = render_runtime_context_block(workspace="mode=shared cwd=/tmp")
        self.assertIn("workspace: mode=shared cwd=/tmp", block)


if __name__ == "__main__":
    unittest.main()
