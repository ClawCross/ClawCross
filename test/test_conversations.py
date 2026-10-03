"""L2 conversations and the group chat built on them: members and senders are agent
ids or people; who a message wakes, what a woken agent is told, and who may do what."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src" / "backend"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agents.messages import DeliveryReceipt  # noqa: E402
from agents.store import ACPX, WEBOT, AgentStore  # noqa: E402
from groups.conversations import Conversations, NotAMember, resolve_text_mentions  # noqa: E402
from groups.delivery import StormGuard, WakeRequest, mentions_everyone, select_wake_targets  # noqa: E402
from groups.store import DIRECT, ConversationStore, human  # noqa: E402
from groups.routes import create_groups_router  # noqa: E402
from groups.service import Forbidden, GroupError, GroupService  # noqa: E402
from teams.store import TeamStore  # noqa: E402

TOKEN = "tok"


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
        self.assertEqual(self.wake(sender_id="a", primary_id="a", mention_all=True), ["b", "c"])

    def test_direct_chat(self):
        self.assertEqual(select_wake_targets(WakeRequest(agent_ids=["a"], direct=True)), ["a"])
        self.assertEqual(select_wake_targets(WakeRequest(agent_ids=["a"], sender_id="a", direct=True)), [])

    def test_mention_tokens(self):
        for text, expected in {"@所有人 看一下": True, "@all hi": True, "@allison": False, "no mention": False}.items():
            with self.subTest(text=text):
                self.assertEqual(mentions_everyone(text), expected)
        members = [("Code", "c"), ("Code Reviewer", "r"), ("研发", "y")]
        self.assertEqual(resolve_text_mentions("@Code Reviewer 看看", members), ["r"])
        self.assertEqual(resolve_text_mentions("@Codex and @Code.", members), ["c"])
        self.assertEqual(resolve_text_mentions("mail a@Code.io @研发看下", members), ["y"])

    def test_storm_guard_resets_when_a_human_speaks(self):
        guard = StormGuard(limit=3)
        self.assertTrue(guard.allow("g", 2))
        self.assertFalse(guard.allow("g", 2))
        guard.human_spoke("g")
        self.assertTrue(guard.allow("g", 3))


class _Gateway:
    """Records deliveries; ``fail`` makes them fail."""

    def __init__(self):
        self.deliveries = []
        self.fail = False

    async def inbox(self, agent, msg, **kwargs):
        self.deliveries.append({"agent": agent, "msg": msg, **kwargs})
        return DeliveryReceipt(accepted=not self.fail, error="down" if self.fail else "")


class GroupCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.agents = AgentStore(root / "agents.db")
        self.teams = TeamStore(self.agents, root / "user_files")
        self.gateway = _Gateway()
        self.conversations = Conversations(ConversationStore(root / "conversations.db"), self.agents, self.gateway)
        self.service = GroupService(self.conversations, names=self.teams.address)
        self.planner = self.agents.create("alice", driver=WEBOT, name="Planner", agent_id="planner")
        self.coder = self.agents.create("alice", driver=WEBOT, name="Coder", agent_id="coder")
        self.codex = self.agents.create("alice", driver=ACPX, config={"platform": "codex"}, name="Codex", agent_id="codex")
        self.group = self.service.create("alice", title="Dev", agents=["planner", "coder", "codex"])["group_id"]

    def woken(self):
        found = sorted(d["agent"].name for d in self.gateway.deliveries)
        self.gateway.deliveries.clear()
        return found

    async def say(self, text, sender=None, **kw):
        return await self.service.post("alice", self.group, sender or human("alice"), text, **kw)


class TestGroupChat(GroupCase):
    async def test_live_memberships_change_and_are_scoped_by_owner_and_agent(self):
        meta = self.service.memberships('alice', 'coder')
        self.assertEqual(meta[0]['group_id'], self.group)
        self.assertNotIn('messages', meta[0])
        self.service.update('alice', self.group, title='Renamed')
        self.assertEqual(self.service.memberships('alice', 'coder')[0]['title'], 'Renamed')
        self.agents.create('bob', driver=WEBOT, name='Other Coder', agent_id='coder')
        self.assertEqual(self.service.memberships('bob', 'coder'), [])
        self.service.remove_member('alice', self.group, 'coder')
        self.assertEqual(self.service.memberships('alice', 'coder'), [])
        with self.assertRaises(NotAMember):
            await self.say('cannot send after leaving', sender='coder')

    async def test_wrong_group_title_does_not_post(self):
        before = self.conversations.store.message_count(self.group)
        with self.assertRaises(GroupError):
            await self.say('wrong target', sender='coder', expected_title='Other group')
        self.assertEqual(self.conversations.store.message_count(self.group), before)
        await self.say('right target', sender='coder', expected_title='Dev')

    async def test_members_are_agents_and_people_with_names(self):
        detail = self.service.detail("alice", self.group)
        self.assertEqual([m["name"] for m in detail["members"]], ["alice", "Planner", "Coder", "Codex"])
        self.assertEqual(detail["members"][3]["agent"]["platform"], "codex")
        self.assertEqual(self.service.list("alice")[0]["member_count"], 4)

    async def test_human_message_wakes_by_the_rules_and_says_how_to_reply(self):
        await self.say("大家好")
        self.assertEqual(self.woken(), ["Coder", "Codex", "Planner"])

        self.service.set_primary("alice", self.group, "planner")
        await self.say("有人吗")
        self.assertEqual(self.woken(), ["Planner"])

        await self.say("@Codex 看一下 @Coder")
        text = {d["agent"].name: d["msg"].text for d in self.gateway.deliveries}
        self.assertEqual(sorted(text), ["Coder", "Codex"])
        self.assertIn("@你 说:", text["Codex"])
        meta = {d["agent"].name: d["context"]["groups"][0] for d in self.gateway.deliveries}
        self.assertIn('ClawCross MCP', meta["Codex"]["reply_channel"])
        self.assertIn('send_to_group', meta["Codex"]["reply_channel"])
        self.assertIn(self.group, meta["Codex"]["reply_channel"])
        self.assertIn(f'send_to_group(group_id="{self.group}"', meta["Coder"]["reply_channel"])
        self.assertEqual(meta["Codex"]["role"], "sub_agent")
        self.assertNotIn("reply_channel", text["Codex"])
        summary = {d["agent"].name: d["msg"].summary for d in self.gateway.deliveries}
        self.assertEqual(summary["Codex"], "群聊「Dev」 alice @你: @Codex 看一下 @Coder")  # its inbox notice

    async def test_agents_wake_only_whom_they_mention_and_are_capped(self):
        await self.say("@Coder 你好", sender=self.planner.agent_id)
        self.assertEqual(self.woken(), ["Coder"])
        await self.say("收到", sender=self.coder.agent_id)
        self.assertEqual(self.woken(), [])

        self.conversations.storm_guard = StormGuard(limit=2)
        for _ in range(3):
            await self.say("@Codex @Coder 再来", sender=self.planner.agent_id)
        self.assertEqual(len(self.woken()), 2)
        await self.say("停")  # a person speaks: a new budget
        self.woken()
        await self.say("@Codex 继续", sender=self.planner.agent_id)
        self.assertEqual(self.woken(), ["Codex"])

    async def test_woken_agent_gets_what_it_missed(self):
        await self.say("@Coder 第一条")
        self.woken()
        await self.say("@Planner 第二条")
        self.woken()
        await self.say("@Coder 第三条")
        digest = self.gateway.deliveries[0]["msg"].text
        self.assertIn("第二条", digest.split("[群聊")[0])
        self.assertNotIn("第一条", digest.split("[群聊")[0])

    async def test_repeat_posts_are_stored_once_and_senders_mentions_kept(self):
        first = await self.say("@Coder hi", client_msg_id="m1")
        again = await self.say("@Coder hi", client_msg_id="m1")
        self.assertTrue(first["created"])
        self.assertFalse(again["created"])
        self.assertEqual(len(self.woken()), 1)
        message = self.service.messages("alice", self.group)[0]
        self.assertEqual((message["sender"], message["sender_name"]), ("u:alice", "alice"))
        self.assertEqual(message["mentions"], [self.coder.agent_id])

    async def test_dnd_and_muted_members_are_not_woken(self):
        self.service.update("alice", self.group, dnd=True)
        await self.say("大家好")
        self.assertEqual(self.woken(), [])
        self.service.update("alice", self.group, dnd=False)
        self.service.update_member("alice", self.group, self.codex.agent_id, muted=True)
        await self.say("大家好")
        self.assertEqual(self.woken(), ["Coder", "Planner"])
        self.service.mute_agents("alice", self.group, True)
        await self.say("大家好")
        self.assertEqual(self.woken(), [])

    async def test_removing_the_primary_or_deleting_an_agent_leaves_no_dangling_reference(self):
        self.service.set_primary("alice", self.group, "planner")
        self.service.remove_member("alice", self.group, self.planner.agent_id)
        self.assertIsNone(self.service.detail("alice", self.group)["primary_agent"])
        self.service.set_primary("alice", self.group, "codex")
        self.agents.delete("alice", "codex")
        self.conversations.store.forget("alice", "codex")  # what deleting through the API does
        detail = self.service.detail("alice", self.group)
        self.assertIsNone(detail["primary_agent"])
        self.assertNotIn("codex", [m["principal"] for m in detail["members"]])
        await self.say("大家好")
        self.assertEqual(self.woken(), ["Coder"])

    async def test_failed_delivery_does_not_leave_the_agent_typing(self):
        self.gateway.fail = True
        await self.say("@Coder hi")
        self.assertEqual(self.service.typing("alice", self.group)["typing"], [])

    async def test_direct_chat_is_one_agent_and_reused(self):
        direct = self.service.create("alice", title="", kind=DIRECT, agents=["codex"])
        again = self.service.create("alice", title="", kind=DIRECT, agents=["codex"])
        self.assertEqual(direct["group_id"], again["group_id"])
        self.assertEqual(direct["title"], "Codex")
        await self.service.post("alice", direct["group_id"], human("alice"), "hi")
        self.assertIn("[私聊", self.gateway.deliveries[0]["msg"].text)
        with self.assertRaises(GroupError):
            self.service.add_member("alice", direct["group_id"], "coder")

    async def test_others_cannot_read_post_or_manage(self):
        with self.assertRaises(Forbidden):
            self.service.detail("bob", self.group)
        with self.assertRaises(Forbidden):
            await self.service.post("bob", self.group, human("bob"), "hi")
        outsider = self.agents.create("alice", driver=WEBOT, name="Outsider")
        with self.assertRaises(NotAMember):
            await self.say("hi", sender=outsider.agent_id)


class TestGroupsAndTeams(GroupCase):
    async def test_a_group_has_nothing_to_do_with_teams_but_takes_team_names(self):
        self.teams.create("alice", "dev")
        self.teams.add("alice", "dev", "planner", role="Builder")
        created = self.service.create("alice", title="Mixed", agents=["dev.Builder", "codex"])
        self.assertEqual(sorted(m["principal"] for m in created["members"]), ["codex", "planner", "u:alice"])
        self.assertNotIn("team", created)
        self.teams.remove("alice", "dev", "planner")  # the team changes; the group stays as it was made
        self.assertEqual(len(self.service.detail("alice", created["group_id"])["members"]), 3)
        await self.service.post("alice", created["group_id"], human("alice"), "hi")
        for delivery in self.gateway.deliveries:
            self.assertEqual(delivery["context"]["conversation_id"], created["group_id"])
            meta = delivery["context"]["groups"][0]
            self.assertEqual(meta["group_id"], created["group_id"])
            self.assertEqual(len(meta["members"]), 3)
            self.assertNotIn("content", meta)


class TestGroupsApi(GroupCase):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(create_groups_router(internal_token=TOKEN, verify_password=lambda u, p: False,
                                                service=self.service))
        self.client = TestClient(app)

    def post(self, body, *, token=None, user="alice"):
        headers = {"Authorization": f"Bearer {TOKEN}:{user}"}
        if token:
            headers["X-Internal-Token"] = token
        return self.client.post(f"/groups/{self.group}/messages", json=body, headers=headers)

    def test_query_agent_memberships_and_verify_expected_target(self):
        headers = {'Authorization': f'Bearer {TOKEN}:alice'}
        response = self.client.get('/groups?agent_id=coder', headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['groups'][0]['group_id'], self.group)
        self.assertNotIn('messages', response.json()['groups'][0])
        rejected = self.post({'content': 'wrong', 'agent': 'coder', 'expected_title': 'Other'}, token=TOKEN)
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.conversations.store.message_count(self.group), 0)

    def test_only_local_services_post_for_an_agent_and_only_the_owners(self):
        self.assertEqual(self.post({"content": "hi", "agent": "coder"}).status_code, 403)
        self.assertEqual(self.post({"content": "hi", "agent": "coder"}, token="wrong").status_code, 403)
        sent = self.post({"content": "hi", "agent": "coder"}, token=TOKEN).json()
        self.assertEqual(sent["message"]["sender"], self.coder.agent_id)
        self.assertEqual(self.post({"content": "hi", "agent": "coder"}, token=TOKEN, user="bob").status_code, 404)
        self.assertEqual(self.post({"content": "hi"}, user="bob").status_code, 403)

    def test_group_endpoints(self):
        headers = {"Authorization": f"Bearer {TOKEN}:alice"}
        created = self.client.post("/groups", json={"title": "新群", "agents": ["coder"]}, headers=headers).json()
        gid = created["group_id"]
        self.assertTrue(gid.startswith("g_"))
        self.client.post(f"/groups/{gid}/members", json={"agent": "codex"}, headers=headers)
        available = self.client.get(f"/groups/{gid}/available_agents", headers=headers).json()["agents"]
        self.assertEqual([a["name"] for a in available], ["Planner"])
        self.client.post(f"/groups/{gid}/messages", json={"content": "hi"}, headers=headers)
        self.client.patch(f"/groups/{gid}", json={"dnd": True}, headers=headers)
        listed = {g["group_id"]: g for g in self.client.get("/groups", headers=headers).json()["groups"]}
        self.assertEqual((listed[gid]["message_count"], listed[gid]["dnd"], listed[gid]["last_message"]["content"]),
                         (1, True, "hi"))
        self.client.put(f"/groups/{gid}/primary", json={"agent": "codex"}, headers=headers)
        self.assertEqual(self.client.get(f"/groups/{gid}", headers=headers).json()["primary_agent"], self.codex.agent_id)
        self.assertEqual(self.client.delete(f"/groups/{gid}", headers=headers).status_code, 200)
        self.assertEqual(self.client.get(f"/groups/{gid}", headers=headers).status_code, 404)


if __name__ == "__main__":
    unittest.main()
