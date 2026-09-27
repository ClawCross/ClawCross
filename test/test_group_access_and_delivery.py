"""Group chat: who may read or post, who a message reaches, and state that must
not outlive its group."""

import asyncio
import shlex
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fastapi import HTTPException

import api.group_service as group_service
from api.group_models import (
    GroupAddMemberRequest,
    GroupCreateRequest,
    GroupMessageRequest,
    GroupMuteAllRequest,
    GroupSetPrimaryRequest,
)
from api.group_service import GroupService, init_group_db, resolve_text_mentions

TOKEN = "test-token"


def bearer(user: str) -> str:
    """How front.py and scripts/cli.py authenticate: internal token + user id."""
    return f"Bearer {TOKEN}:{user}"


class _IdleAgent:
    def is_thread_busy(self, _thread_id):
        return False


class _RecordingClient:
    """Stands in for httpx.AsyncClient; records internal-agent triggers."""

    posts: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        _RecordingClient.posts.append(json)
        return mock.Mock(status_code=200)


class GroupChatTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = TemporaryDirectory()
        db = str(Path(self.tmpdir.name) / "group_chat.db")
        await init_group_db(db)
        self.service = GroupService(
            internal_token=TOKEN,
            verify_password=lambda _u, _p: False,
            checkpoint_db_path=str(Path(self.tmpdir.name) / "checkpoints.db"),
            group_db_path=db,
            agent=_IdleAgent(),
        )
        _RecordingClient.posts = []
        patcher = mock.patch.object(group_service.httpx, "AsyncClient", _RecordingClient)
        patcher.start()
        self.addCleanup(patcher.stop)

        created = await self.service.create_group(GroupCreateRequest(name="Dev Team"), bearer("alice"))
        self.group_id = created["group_id"]
        for short_name, global_id in [("Code", "s_code"), ("Code Reviewer", "s_rev"), ("Planner", "s_plan")]:
            await self.service.add_single_member(
                self.group_id,
                GroupAddMemberRequest(short_name=short_name, global_id=global_id),
                bearer("alice"),
            )

    async def asyncTearDown(self):
        self.tmpdir.cleanup()

    async def settle(self):
        """Wait for the fire-and-forget broadcast and the tasks it spawns."""
        for _ in range(10):
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)

    def woken(self) -> list[str]:
        return sorted(post["session_id"] for post in _RecordingClient.posts)

    async def post(self, content: str, user: str = "alice", **fields):
        result = await self.service.post_group_message(
            self.group_id, GroupMessageRequest(content=content, **fields), bearer(user), None,
        )
        await self.settle()
        return result


class TestPrimaryAgent(GroupChatTestCase):
    async def test_removing_the_primary_clears_it_so_messages_still_arrive(self):
        await self.service.set_primary_agent(self.group_id, GroupSetPrimaryRequest(global_id="s_plan"), bearer("alice"))
        await self.service.remove_single_member(self.group_id, "s_plan", bearer("alice"))

        await self.post("大家好")

        self.assertEqual(self.woken(), ["s_code", "s_rev"])
        detail = await self.service.get_group(self.group_id, bearer("alice"))
        self.assertIsNone(detail["primary_agent_global_id"])

    async def test_a_primary_that_is_no_longer_a_member_is_ignored(self):
        # State written before removal cleared the primary.
        await group_service.set_group_primary_agent(
            self.service.group_db_path, group_id=self.group_id, global_id="gone",
        )

        await self.post("大家好")

        self.assertEqual(self.woken(), ["s_code", "s_plan", "s_rev"])


class TestMentions(GroupChatTestCase):
    async def test_longer_name_does_not_also_wake_its_prefix(self):
        await self.post("@Code Reviewer 看下这个 PR")

        self.assertEqual(self.woken(), ["s_rev"])

    def test_resolve_text_mentions(self):
        members = [("Code", "a"), ("Code Reviewer", "b"), ("小明", "c")]
        cases = {
            "@Code Reviewer please": ["b"],
            "@Code and @Code Reviewer": ["b", "a"],
            "@Codex is not a member": [],
            "@code, lower case": ["a"],
            "@小明你好": ["c"],
            "email a@Code.io": [],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(resolve_text_mentions(text, members), expected)


class TestAccess(GroupChatTestCase):
    async def test_other_user_cannot_read_post_or_mute(self):
        calls = [
            self.service.get_group(self.group_id, bearer("bob")),
            self.service.get_group_messages(self.group_id, 0, bearer("bob")),
            self.service.get_typing_status(self.group_id, bearer("bob")),
            self.service.post_group_message(self.group_id, GroupMessageRequest(content="hi"), bearer("bob"), None),
            self.service.mute_group(self.group_id, bearer("bob")),
        ]
        for call in calls:
            with self.subTest(call=call.__qualname__):
                with self.assertRaises(HTTPException) as ctx:
                    await call
                self.assertEqual(ctx.exception.status_code, 403)
        await self.settle()
        self.assertEqual(_RecordingClient.posts, [])

    async def test_owner_can_read_and_post(self):
        await self.post("hello")
        messages = await self.service.get_group_messages(self.group_id, 0, bearer("alice"))
        self.assertEqual([m["content"] for m in messages["messages"]], ["hello"])

    async def test_mcp_agent_may_only_post_into_its_own_users_groups(self):
        request = GroupMessageRequest(content="hi", sender="bob#s_x", sender_display="#s_x")
        with self.assertRaises(HTTPException) as ctx:
            await self.service.post_group_message(self.group_id, request, None, TOKEN)
        self.assertEqual(ctx.exception.status_code, 403)

        own = GroupMessageRequest(content="hi", sender="alice#s_code", sender_display="#s_code")
        result = await self.service.post_group_message(self.group_id, own, None, TOKEN)
        await self.settle()
        self.assertEqual(result["sender_display"], "#oasis#Code#s_code")

    async def test_cli_agent_reply_is_broadcast_as_the_group_owner(self):
        seen = {}

        async def record(*_args, **kwargs):
            seen.update(kwargs)

        self.service.broadcast_to_group = record
        display = "claude#ext#Code#s_code"
        # Without -u the CLI used to default to "admin".
        await self.post("done", user="admin", sender=display, sender_display=display)

        self.assertEqual(seen["user_id"], "alice")


class TestDeletedGroupState(GroupChatTestCase):
    async def test_recreated_group_does_not_inherit_mute_all(self):
        await self.service.mute_all_group_agents(self.group_id, GroupMuteAllRequest(muted=True), bearer("alice"))
        await self.service.delete_group(self.group_id, bearer("alice"))

        recreated = await self.service.create_group(GroupCreateRequest(name="Dev Team"), bearer("alice"))

        self.assertEqual(recreated["group_id"], self.group_id)
        detail = await self.service.get_group(self.group_id, bearer("alice"))
        self.assertFalse(detail["mute_all_agents"])


class TestCliHint(unittest.TestCase):
    def test_group_id_with_spaces_stays_one_argument(self):
        hint = group_service._cli_hint(
            "send", owner="alice", group_id="alice::Dev Team", sender_display="tag#ext#O'Neil#g1",
        )
        argv = shlex.split(hint.split("&&", 1)[1])
        self.assertEqual(argv[argv.index("-u") + 1], "alice")
        self.assertEqual(argv[argv.index("--group-id") + 1], "alice::Dev Team")
        self.assertEqual(argv[argv.index("--sender") + 1], "tag#ext#O'Neil#g1")


if __name__ == "__main__":
    unittest.main()
