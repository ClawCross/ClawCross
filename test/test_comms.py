"""L2 communication: who a message wakes, how agents are kept from waking each
other forever, and what a woken member missed — through the group chat that
uses it."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fastapi import HTTPException  # noqa: E402

import api.group_service as group_service  # noqa: E402
from api.group_models import GroupAddMemberRequest, GroupCreateRequest, GroupMessageRequest  # noqa: E402
from api.group_service import GroupService, init_group_db  # noqa: E402
from comms.delivery import StormGuard, WakeRequest, mentions_everyone, select_wake_targets  # noqa: E402

TOKEN = "tok"


def bearer(user: str) -> str:
    return f"Bearer {TOKEN}:{user}"


class TestWakeRule(unittest.TestCase):
    AGENTS = ["a", "b", "c"]

    def wake(self, **kw):
        return select_wake_targets(WakeRequest(agent_ids=self.AGENTS, **kw))

    def test_humans(self):
        self.assertEqual(self.wake(), ["a", "b", "c"])
        self.assertEqual(self.wake(mentions=["b"]), ["b"])
        self.assertEqual(self.wake(primary_id="a"), ["a"])
        self.assertEqual(self.wake(primary_id="a", mentions=["c"]), ["c"])
        self.assertEqual(self.wake(primary_id="a", mention_all=True), ["a", "b", "c"])

    def test_agents_wake_only_whom_they_mention(self):
        self.assertEqual(self.wake(sender_id="a"), [])
        self.assertEqual(self.wake(sender_id="a", mentions=["b"]), ["b"])
        self.assertEqual(self.wake(sender_id="a", mention_all=True), [])  # not the lead

    def test_sub_agents_reach_only_the_lead(self):
        self.assertEqual(self.wake(sender_id="b", primary_id="a", mentions=["c"]), ["a"])
        self.assertEqual(self.wake(sender_id="a", primary_id="a", mentions=["c"]), ["c"])
        self.assertEqual(self.wake(sender_id="a", primary_id="a", mention_all=True), ["b", "c"])

    def test_direct_chat(self):
        self.assertEqual(select_wake_targets(WakeRequest(agent_ids=["a"], direct=True)), ["a"])
        self.assertEqual(select_wake_targets(WakeRequest(agent_ids=["a"], sender_id="a", direct=True)), [])

    def test_mention_everyone_tokens(self):
        for text, expected in {"@所有人 看一下": True, "@all hi": True, "@Everyone": True,
                               "@allison": False, "a@all.com": True, "no mention": False}.items():
            with self.subTest(text=text):
                self.assertEqual(mentions_everyone(text), expected)

    def test_storm_guard_resets_when_a_human_speaks(self):
        guard = StormGuard(limit=3)
        self.assertTrue(guard.allow("g", 2))
        self.assertFalse(guard.allow("g", 2))
        self.assertTrue(guard.allow("g", 1))
        self.assertFalse(guard.allow("g", 1))
        guard.human_spoke("g")
        self.assertTrue(guard.allow("g", 3))


class _RecordingGateway:
    def __init__(self):
        self.deliveries: list[dict] = []

    async def deliver(self, owner, record, msg, **kwargs):
        self.deliveries.append({"record": record, "msg": msg, **kwargs})
        return mock.Mock(accepted=True, error="")


class GroupCommsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.user_files = root / "user_files"
        patcher = mock.patch.object(group_service, "USER_FILES_DIR", self.user_files)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Declared agents so that they get registry ids.
        self.write_team("dev", [
            {"name": "Planner", "tag": "plan", "session": "s_plan"},
            {"name": "Coder", "tag": "coder", "session": "s_code"},
        ], [
            {"name": "Codex", "tag": "codex", "global_name": "cx", "platform": "codex"},
        ])
        db = str(root / "group_chat.db")
        await init_group_db(db)
        self.gateway = _RecordingGateway()
        self.service = GroupService(
            internal_token=TOKEN, verify_password=lambda u, p: False,
            checkpoint_db_path=str(root / "checkpoints.db"), group_db_path=db,
            agent=mock.Mock(is_thread_busy=lambda _t: False), gateway=self.gateway,
        )
        created = await self.service.create_group(GroupCreateRequest(name="Dev", team_name="dev"), bearer("alice"))
        self.group_id = created["group_id"]

    async def asyncTearDown(self):
        # Background broadcasts may still be writing into the temp dir.
        await self.settle()

    def write_team(self, team, internal, external=None):
        base = self.user_files / "alice" / "teams" / team
        base.mkdir(parents=True, exist_ok=True)
        (base / "internal_agents.json").write_text(json.dumps(internal, ensure_ascii=False), encoding="utf-8")
        if external is not None:
            (base / "external_agents.json").write_text(json.dumps(external, ensure_ascii=False), encoding="utf-8")

    async def settle(self):
        for _ in range(10):
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)

    def woken(self):
        found = []
        for d in self.gateway.deliveries:
            b = d["record"].binding
            found.append(b.get("session") or b.get("global_name"))
        self.gateway.deliveries.clear()
        return sorted(found)

    async def human(self, text, **fields):
        result = await self.service.post_group_message(
            self.group_id, GroupMessageRequest(content=text, **fields), bearer("alice"), None,
        )
        await self.settle()
        return result

    async def agent(self, display, text, **fields):
        result = await self.service.post_group_message(
            self.group_id, GroupMessageRequest(content=text, sender=display, sender_display=display, **fields),
            bearer("alice"), None,
        )
        await self.settle()
        return result


class TestGroupWaking(GroupCommsTestCase):
    async def test_plain_agent_message_wakes_nobody_but_mentions_do(self):
        self.assertEqual(self.woken(), [])
        await self.human("大家好")
        self.assertEqual(self.woken(), ["cx", "s_code", "s_plan"])

        await self.agent("coder#oasis#Coder#s_code", "我先看看代码")
        self.assertEqual(self.woken(), [])

        await self.agent("coder#oasis#Coder#s_code", "@Planner 需要你拆一下任务")
        self.assertEqual(self.woken(), ["s_plan"])

    async def test_mention_all_only_from_a_human(self):
        await self.agent("coder#oasis#Coder#s_code", "@所有人 我改完了")
        self.assertEqual(self.woken(), [])
        await self.human("@所有人 开会")
        self.assertEqual(self.woken(), ["cx", "s_code", "s_plan"])

    async def test_woken_member_gets_a_digest_of_what_it_missed(self):
        await self.agent("coder#oasis#Coder#s_code", "进度：接口写完了")
        await self.agent("plan#oasis#Planner#s_plan", "我在排下一步")
        self.gateway.deliveries.clear()

        await self.agent("plan#oasis#Planner#s_plan", "@Coder 请补测试")

        delivery = self.gateway.deliveries[0]
        self.assertEqual(delivery["record"].binding["session"], "s_code")
        text = delivery["msg"].text
        self.assertIn("我在排下一步", text)           # missed while not woken
        self.assertNotIn("进度：接口写完了", text)     # its own message
        self.assertIn("@Coder 请补测试", text)

        # Next time it is woken, nothing is repeated.
        self.gateway.deliveries.clear()
        await self.human("@Coder 好了吗")
        self.assertNotIn("我在排下一步", self.gateway.deliveries[0]["msg"].text)

    async def test_agents_cannot_wake_each_other_forever(self):
        self.service._storm_guard = StormGuard(limit=3)
        for _ in range(3):
            await self.agent("coder#oasis#Coder#s_code", "@Planner ping")
        self.assertEqual(len(self.woken()), 3)
        await self.agent("coder#oasis#Coder#s_code", "@Planner ping")
        self.assertEqual(self.woken(), [])
        await self.human("继续")
        self.woken()  # the human woke everyone; start counting again
        await self.agent("coder#oasis#Coder#s_code", "@Planner ping")
        self.assertEqual(self.woken(), ["s_plan"])


class TestMessages(GroupCommsTestCase):
    async def test_sender_ids_mentions_and_retries(self):
        from agents.registry import get_registry

        registry = get_registry(self.user_files)
        coder_id = registry.webot_session("alice", "s_code").agent_id

        first = await self.human("@Coder hi", client_msg_id="c1")
        again = await self.human("@Coder hi", client_msg_id="c1")
        self.assertEqual(again["status"], "duplicate")
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(self.woken(), ["s_code"])  # the retry woke nobody

        mcp = GroupMessageRequest(content="done", sender="alice#s_code", sender_display="#s_code")
        await self.service.post_group_message(self.group_id, mcp, None, TOKEN)
        await self.settle()

        messages = (await self.service.get_group_messages(self.group_id, 0, bearer("alice")))["messages"]
        self.assertEqual([m["sender_id"] for m in messages], ["u:alice", coder_id])
        self.assertEqual(json.loads(messages[0]["mentions"]), ["s_code"])

    async def test_cli_agent_flag_speaks_for_a_member_only(self):
        result = await self.service.post_group_message(
            self.group_id, GroupMessageRequest(content="ok", agent="alice/codex"), bearer("admin"), None,
        )
        self.assertEqual(result["sender_display"], "codex#ext#Codex#cx")

        self.write_team("other", [{"name": "Outsider", "session": "s_out"}])
        with self.assertRaises(HTTPException) as ctx:
            await self.service.post_group_message(
                self.group_id, GroupMessageRequest(content="x", agent="alice/outsider"), bearer("admin"), None,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_do_not_disturb_is_persistent(self):
        await self.service.mute_group(self.group_id, bearer("alice"))
        fresh = GroupService(
            internal_token=TOKEN, verify_password=lambda u, p: False,
            checkpoint_db_path=self.service.checkpoint_db_path, group_db_path=self.service.group_db_path,
            agent=None, gateway=self.gateway,
        )
        self.assertTrue((await fresh.group_mute_status(self.group_id, bearer("alice")))["muted"])
        await fresh.post_group_message(self.group_id, GroupMessageRequest(content="hi"), bearer("alice"), None)
        await self.settle()
        self.assertEqual(self.woken(), [])
        # The message itself is kept.
        self.assertEqual(len((await fresh.get_group_messages(self.group_id, 0, bearer("alice")))["messages"]), 1)


class TestTeamGroup(GroupCommsTestCase):
    async def test_team_group_follows_the_team(self):
        detail = await self.service.get_group(self.group_id, bearer("alice"))
        self.assertEqual(detail["team"], "dev")
        self.assertEqual(sorted(m["global_id"] for m in detail["members"] if m["is_agent"]), ["cx", "s_code", "s_plan"])

        # The team gains a lead-reviewer and loses Coder.
        self.write_team("dev", [
            {"name": "Planner", "tag": "plan", "session": "s_plan"},
            {"name": "Reviewer", "tag": "rev", "session": "s_rev", "is_primary": True},
        ])
        detail = await self.service.get_group(self.group_id, bearer("alice"))
        self.assertEqual(sorted(m["global_id"] for m in detail["members"] if m["is_agent"]), ["cx", "s_plan", "s_rev"])
        self.assertEqual(detail["primary_agent_global_id"], "s_rev")

        await self.human("有人吗")
        self.assertEqual(self.woken(), ["s_rev"])  # the lead answers for the team

    async def test_members_added_by_hand_to_a_plain_group_stay(self):
        plain = await self.service.create_group(GroupCreateRequest(name="Plain"), bearer("alice"))
        await self.service.add_single_member(
            plain["group_id"], GroupAddMemberRequest(short_name="Main", global_id="default"), bearer("alice"),
        )
        detail = await self.service.get_group(plain["group_id"], bearer("alice"))
        self.assertEqual([m["global_id"] for m in detail["members"] if m["is_agent"]], ["default"])


if __name__ == "__main__":
    unittest.main()
