"""Guards on the cacheable prefix of every request sent to the model.

Prompt/KV caching is a prefix match, so two things must hold no matter which
branch assembles the turn: the system message is exactly ``base_prompt``, and
per-turn runtime state rides at the tail (and only when it changed).
"""

import sys
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from langchain_core.tools import StructuredTool

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src" / "backend"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from webot.engine.agent import TeamAgent, discovery_tool_schemas, should_inject_new_inbox_notice
from webot.engine.lazy_tool_discovery import LazyToolRegistry
from webot.context import (
    RUNTIME_DELTA_KEY,
    RUNTIME_STATE_KEY,
    assemble_input_messages,
    render_runtime_context_block,
    render_team_skill_context,
    strip_legacy_skills_from_system_prompt,
)

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

    def test_old_multimodal_message_keeps_transition_metadata_after_stripping(self):
        original = HumanMessage(
            content=[{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "data:"}}],
            id="turn-with-image",
        )
        original.additional_kwargs[RUNTIME_STATE_KEY] = STATE
        original.additional_kwargs[RUNTIME_DELTA_KEY] = STATE
        stripped = TeamAgent._strip_multimodal_parts([original])[0]
        self.assertEqual(stripped.id, original.id)
        self.assertEqual(stripped.additional_kwargs[RUNTIME_DELTA_KEY], STATE)
        messages, injected = assemble_input_messages(
            base_prompt=BASE,
            history=[stripped, AIMessage(content="done"), HumanMessage(content="next")],
            runtime_state=STATE,
        )
        self.assertEqual(injected, "")
        self.assertIn(STATE, messages[1].content)

    def test_tool_round_attaches_state_to_last_tool_result(self):
        history = _tool_round_history()
        messages, injected = assemble_input_messages(
            base_prompt=BASE, history=history, runtime_state=STATE
        )
        self.assertEqual(injected, STATE)
        # The tool call/result pairing and result identity are preserved;
        # runtime state never starts a synthetic user turn.
        self.assertEqual(len(messages), len(history) + 1)
        self.assertIsInstance(messages[-1], ToolMessage)
        self.assertEqual(messages[-1].tool_call_id, "call_1")
        self.assertIn(STATE, messages[-1].content)

    def test_history_is_never_mutated(self):
        history = _tool_round_history()
        before = [m.content for m in history]
        assemble_input_messages(base_prompt=BASE, history=history, runtime_state=STATE)
        self.assertEqual([m.content for m in history], before)


class UnchangedStateIsNotResent(unittest.TestCase):
    def test_first_retained_patch_rebases_after_compaction(self):
        changed = STATE.replace("pending", "done")
        retained = HumanMessage(content="retained turn")
        self._record(retained, state=changed, delta="【Runtime Context Update】\n- pending\n+ done")
        messages, injected = assemble_input_messages(
            base_prompt=BASE, history=[retained, AIMessage(content="done"), HumanMessage(content="next")],
            runtime_state=changed,
        )
        self.assertIn(changed, messages[1].content)
        self.assertEqual(injected, "")

    @staticmethod
    def _record(message, state=STATE, delta=STATE):
        message.additional_kwargs[RUNTIME_STATE_KEY] = state
        message.additional_kwargs[RUNTIME_DELTA_KEY] = delta

    def test_tool_round_ends_on_a_stored_message_when_state_is_unchanged(self):
        history = _tool_round_history()
        self._record(history[0])
        messages, injected = assemble_input_messages(
            base_prompt=BASE,
            history=history,
            runtime_state=STATE,
        )
        self.assertEqual(injected, "")
        self.assertIn(STATE, messages[1].content)
        # The previous injection is replayed from metadata; the tool result
        # itself stays unchanged because there was no transition.
        self.assertEqual(len(messages), len(history) + 1)
        self.assertIsInstance(messages[-1], ToolMessage)
        self.assertEqual(messages[-1].content, "ok")

    def test_tool_round_sends_only_changed_lines(self):
        history = _tool_round_history()
        self._record(history[0])
        messages, injected = assemble_input_messages(
            base_prompt=BASE,
            history=history,
            runtime_state=STATE.replace("pending", "done"),
        )
        self.assertIn("- todo::pending::ship it", injected)
        self.assertIn("+ todo::done::ship it", injected)
        self.assertNotIn("workspace: /tmp/ws", injected)
        self.assertIsInstance(messages[-1], ToolMessage)
        self.assertEqual(messages[-1].tool_call_id, "call_1")

    def test_next_user_turn_does_not_resend_unchanged_state(self):
        first = HumanMessage(content="first question")
        self._record(first)
        messages, injected = assemble_input_messages(
            base_prompt=BASE,
            history=[first, AIMessage(content="done"), HumanMessage(content="next question")],
            runtime_state=STATE,
        )
        self.assertEqual(injected, "")
        self.assertIn(STATE, messages[1].content)
        self.assertNotIn(RUNTIME_STATE_KEY, messages[1].additional_kwargs)
        self.assertEqual(messages[-1].content, "next question")

    def test_removed_state_is_sent_as_a_transition(self):
        first = HumanMessage(content="first question")
        self._record(first)
        messages, injected = assemble_input_messages(
            base_prompt=BASE,
            history=[first, AIMessage(content="done"), HumanMessage(content="next")],
            runtime_state="",
        )
        self.assertIn("- todo::pending::ship it", injected)
        self.assertIn(injected, messages[-1].content)

    def test_missing_snapshot_in_visible_history_restarts_from_full_state(self):
        messages, injected = assemble_input_messages(
            base_prompt=BASE,
            history=[HumanMessage(content="[earlier turns compacted]"), HumanMessage(content="next")],
            runtime_state=STATE,
        )
        self.assertEqual(injected, STATE)

    def test_successive_changes_advance_the_snapshot(self):
        first = HumanMessage(content="start")
        self._record(first)
        tool = ToolMessage(content="updated", tool_call_id="call-2")
        changed = STATE.replace("pending", "done")
        first_history = [first, AIMessage(content=""), tool]
        _, delta = assemble_input_messages(
            base_prompt=BASE, history=first_history, runtime_state=changed,
        )
        self._record(tool, state=changed, delta=delta)
        next_turn = first_history + [AIMessage(content="done"), HumanMessage(content="continue")]
        messages, injected = assemble_input_messages(
            base_prompt=BASE, history=next_turn, runtime_state=changed,
        )
        self.assertEqual(injected, "")
        self.assertIn(delta, messages[3].content)
        self.assertEqual(messages[-1].content, "continue")


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

    def test_runtime_inbox_uses_count_and_summary_without_body(self):
        block = render_runtime_context_block(
            inbox=[{"message_id": "inbox-1", "source_label": "planner", "summary": "Review build", "status": "queued", "body": "SECRET BODY"}],
            inbox_unread_count=7,
            inbox_new_count=2,
        )
        self.assertIn("inbox_unread: 7", block)
        self.assertIn("inbox_new: 2", block)
        self.assertIn("inbox::new::inbox-1::planner::Review build", block)
        self.assertNotIn("SECRET BODY", block)

    def test_previously_notified_unread_inbox_is_absent_from_dynamic_block(self):
        block = render_runtime_context_block(
            inbox=[{"message_id": "inbox-old", "source_label": "planner", "summary": "Earlier notice"}],
            inbox_unread_count=7,
            inbox_new_count=0,
        )
        self.assertNotIn("inbox_", block)
        self.assertNotIn("inbox::", block)

    def test_inbox_notice_only_on_first_model_call_without_existing_delivery(self):
        user_turn = {"trigger_source": "user", "messages": [HumanMessage(content="continue")]}
        digest_turn = {"trigger_source": "system", "messages": [HumanMessage(content="[收件箱通知] 2 条新消息")]}
        self.assertTrue(should_inject_new_inbox_notice(user_turn, 0))
        self.assertFalse(should_inject_new_inbox_notice(user_turn, 1))
        self.assertFalse(should_inject_new_inbox_notice(digest_turn, 0))


class DynamicTeamAndSkills(unittest.TestCase):
    def test_first_call_contains_full_team_and_skill_catalog(self):
        catalog = render_team_skill_context(["ops"], "【用户技能 / Memory 条目】\n  - deploy")
        messages, injected = assemble_input_messages(
            base_prompt=BASE, history=[HumanMessage(content="start")], runtime_state=catalog,
        )
        self.assertEqual(injected, catalog)
        self.assertIn("team: ops", messages[-1].content)
        self.assertIn("deploy", messages[-1].content)
        self.assertEqual(messages[0].content, BASE)

    def test_later_team_and_skill_changes_are_delta_only(self):
        old = render_team_skill_context(["ops"], "【用户技能 / Memory 条目】\n  - deploy")
        first = HumanMessage(content="start")
        first.additional_kwargs[RUNTIME_STATE_KEY] = old
        first.additional_kwargs[RUNTIME_DELTA_KEY] = old
        current = render_team_skill_context(["research"], "【用户技能 / Memory 条目】\n  - review")
        _, injected = assemble_input_messages(
            base_prompt=BASE,
            history=[first, AIMessage(content="done"), HumanMessage(content="continue")],
            runtime_state=current,
        )
        self.assertIn("- team: ops", injected)
        self.assertIn("+ team: research", injected)
        self.assertIn("-   - deploy", injected)
        self.assertIn("+   - review", injected)

    def test_new_session_starts_with_full_snapshot_even_with_forked_history(self):
        old = render_team_skill_context(["ops"], "【用户技能 / Memory 条目】\n  - deploy")
        inherited = HumanMessage(content="parent turn")
        inherited.additional_kwargs[RUNTIME_STATE_KEY] = old
        inherited.additional_kwargs[RUNTIME_DELTA_KEY] = old
        messages, injected = assemble_input_messages(
            base_prompt=BASE,
            history=[inherited, AIMessage(content="done"), HumanMessage(content="child turn")],
            runtime_state=old,
            force_snapshot=True,
        )
        self.assertEqual(injected, old)
        self.assertIn(old, messages[-1].content)

    def test_legacy_frozen_skill_catalog_is_removed_but_soul_remains(self):
        old = BASE + "\n【用户画像】\nowner\n【用户技能 / Memory 条目】\n可用技能：\n  - old\n【Personality (SOUL.md)】\nsteady"
        cleaned = strip_legacy_skills_from_system_prompt(old)
        self.assertIn("【用户画像】", cleaned)
        self.assertIn("【Personality (SOUL.md)】", cleaned)
        self.assertNotIn("【用户技能 / Memory 条目】", cleaned)
        self.assertEqual(
            strip_legacy_skills_from_system_prompt(BASE + "\n【用户技能 / Memory 条目】\n当前暂无已注册条目。"),
            BASE,
        )
        self.assertEqual(strip_legacy_skills_from_system_prompt(BASE), BASE)


class ToolSearchKeepsToolDefinitionsStable(unittest.TestCase):
    def test_forbidden_caller_tools_pass_through_execution_checks(self):
        from webot.engine.agent import UserAwareToolNode
        from webot import runtime_store
        from tempfile import TemporaryDirectory
        import asyncio
        engine=TeamAgent.__new__(TeamAgent)
        engine._internal_tool_names={'tool_call','tool_search'}
        caller={'type':'function','function':{'name':'caller_tool','parameters':{'type':'object','properties':{}}}}
        with TemporaryDirectory() as temp, patch.object(runtime_store,'AGENT_RUNTIME_DB_DIR',Path(temp)):
            for mode in ('chat','readonly'):
                state={'user_id':'alice','session_id':'guard','session_mode':mode,'tools':[caller],
                       'messages':[AIMessage(content='',tool_calls=[{'name':'caller_tool','args':{},'id':'external-1'}])]}
                self.assertTrue(engine._should_continue(state))
                output=asyncio.run(UserAwareToolNode([])(state,{}))
                self.assertTrue(any('不允许' in str(message.content) for message in output['messages']))

    def test_api_definitions_follow_intrinsic_table_across_modes_and_temporary_selection(self):
        from webot import runtime_store
        from tempfile import TemporaryDirectory
        def tool(name):
            return StructuredTool(name=name,description=name+' tool',args_schema={'type':'object','properties':{'value':{'type':'string'}},'required':['value']},func=lambda **_: 'ok')
        engine=TeamAgent.__new__(TeamAgent)
        engine._mcp_tools=[tool(name) for name in ('read_file','run_command','get_session_details','manage_group','manage_team')]
        engine._tool_registry=LazyToolRegistry();engine._tool_registry.register_tools(engine._mcp_tools)
        engine._tool_registry.set_always_loaded({'read_file','run_command'})
        innate=['read_file','run_command','get_session_details','manage_group']
        engine._find_internal_session_meta=lambda *_:{'tools':innate}
        external=[{'name':'caller_tool','description':'Caller function','parameters':{'type':'object','properties':{},'required':[]}}]
        snapshots=[]
        with TemporaryDirectory() as temp, patch.object(runtime_store,'AGENT_RUNTIME_DB_DIR',Path(temp)):
            for mode in ('auto','manual','bypass','readonly','chat'):
                for selected in (None,[],['read_file'],['manage_group']):
                    turn=SimpleNamespace(user_id='alice',session_id='stable',mode=mode)
                    schemas=engine._turn_tool_schemas({'session_mode':mode,'enabled_tools':selected},turn,external,strict=True)
                    snapshots.append(json.dumps(schemas,sort_keys=True,ensure_ascii=False))
        self.assertEqual(len(set(snapshots)),1)
        self.assertIn('manage_group',snapshots[0])
        self.assertNotIn('manage_team',snapshots[0])
        innate.remove('manage_group')
        with TemporaryDirectory() as temp, patch.object(runtime_store,'AGENT_RUNTIME_DB_DIR',Path(temp)):
            updated=engine._turn_tool_schemas({},turn,external,strict=True)
        self.assertNotIn('manage_group',json.dumps(updated))

    def test_search_only_returns_results(self):
        registry = LazyToolRegistry()
        registry.register_tools([SimpleNamespace(name="search_archive", description="Search archived records")])
        names = {"search_archive"}
        before = discovery_tool_schemas(registry, names, strict=True)
        found = registry.search_tools("archive", enabled_names=names)
        after = discovery_tool_schemas(registry, names, strict=True)
        self.assertEqual([item["name"] for item in found], ["search_archive"])
        self.assertEqual(before, after)
        self.assertFalse(registry._entries["search_archive"].schema_loaded)


if __name__ == "__main__":
    unittest.main()
