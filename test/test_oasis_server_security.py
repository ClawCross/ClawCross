"""OASIS: what a topic request may point the server at, and topic lifecycle
states that must reach a terminal value."""

import asyncio
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from oasis import engine as engine_module
from oasis import experts as experts_module
from oasis import forum as forum_module
from oasis import server
from oasis.forum import DiscussionForum

MANUAL_YAML = """version: 2
repeat: false
plan:
  - id: m1
    manual:
      author: host
      content: hello
"""

HUMAN_YAML = MANUAL_YAML + """  - id: h1
    human:
      prompt: "continue?"
      author: host
edges:
  - [m1, h1]
"""


class _RecordingClient:
    """Stands in for httpx.AsyncClient inside oasis.server (completion callbacks)."""

    posts: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        _RecordingClient.posts.append({"url": url, "headers": headers or {}})
        return mock.Mock(status_code=200)


class OasisServerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        _RecordingClient.posts = []
        for target, attr, value in [
            (forum_module, "DISCUSSIONS_DIR", os.path.join(self.tmp.name, "discussions")),
            (server, "USER_FILES_DIR", os.path.join(self.tmp.name, "user_files")),
            (server.httpx, "AsyncClient", _RecordingClient),
            (engine_module, "create_chat_model", mock.MagicMock()),
        ]:
            patcher = mock.patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Test clients are not loopback; keep the listening host fixed so the
        # network-exposure rule doesn't depend on the machine (e.g. WSL).
        env = mock.patch.dict(os.environ, {"CLAWCROSS_SERVER_HOST": "127.0.0.1"})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(server.discussions.clear)
        self.addCleanup(server.engines.clear)
        self.addCleanup(server.tasks.clear)

    def wait_status(self, client, topic_id, done=("concluded", "error", "cancelled"), timeout=10.0):
        deadline = time.time() + timeout
        status = ""
        while time.time() < deadline:
            status = client.get(f"/topics/{topic_id}", params={"user_id": "alice"}).json()["status"]
            if status in done:
                return status
            time.sleep(0.05)
        return status

    def wait_for(self, predicate, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return predicate()


class TestCallbackUrl(OasisServerTestCase):
    def test_only_the_local_agent_service_is_trusted(self):
        port = os.getenv("PORT_AGENT", "51200")
        self.assertEqual(
            server._trusted_callback_url(f"http://127.0.0.1:{port}/system_trigger"),
            f"http://127.0.0.1:{port}/system_trigger",
        )
        for url in [
            "http://evil.example/steal",
            f"http://127.0.0.1:{port}/other",
            "http://127.0.0.1:1/system_trigger",
            f"https://127.0.0.1:{port}/system_trigger",
            None,
            "",
        ]:
            with self.subTest(url=url):
                self.assertIsNone(server._trusted_callback_url(url))

    def test_foreign_callback_never_receives_the_internal_token(self):
        with TestClient(server.app) as client:
            topic = client.post("/topics", json={
                "question": "q", "user_id": "alice", "schedule_yaml": MANUAL_YAML,
                "discussion": False, "callback_url": "http://evil.example/steal",
            }).json()
            self.assertEqual(self.wait_status(client, topic["topic_id"]), "concluded")
            time.sleep(0.2)
        self.assertEqual(_RecordingClient.posts, [])

    def test_local_callback_is_still_sent(self):
        url = f"http://127.0.0.1:{os.getenv('PORT_AGENT', '51200')}/system_trigger"
        with TestClient(server.app) as client:
            topic = client.post("/topics", json={
                "question": "q", "user_id": "alice", "schedule_yaml": MANUAL_YAML,
                "discussion": False, "callback_url": url, "callback_session_id": "s1",
            }).json()
            self.wait_status(client, topic["topic_id"])
            self.assertTrue(self.wait_for(lambda: _RecordingClient.posts))
        self.assertEqual(_RecordingClient.posts[0]["url"], url)


class TestPaths(OasisServerTestCase):
    def test_workflow_names_and_users_cannot_leave_their_directory(self):
        with TestClient(server.app) as client:
            for body in [
                {"user_id": "alice", "name": "../../../bob_pwn", "schedule_yaml": "plan: []"},
                {"user_id": "../bob", "name": "x", "schedule_yaml": "plan: []"},
                {"user_id": "alice", "team": "../bob", "name": "x", "schedule_yaml": "plan: []"},
            ]:
                with self.subTest(body=body):
                    self.assertEqual(client.post("/workflows", json=body).status_code, 400)
            ok = client.post("/workflows", json={"user_id": "alice", "name": "flow", "schedule_yaml": "plan: []"})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(client.get("/workflows", params={"user_id": "../x"}).status_code, 400)
            self.assertEqual(client.post("/topics", json={
                "question": "q", "user_id": "../x", "schedule_yaml": MANUAL_YAML,
            }).status_code, 400)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "bob_pwn.yaml")))

    def test_legacy_python_file_must_be_inside_the_users_files(self):
        outside = os.path.join(self.tmp.name, "outside.py")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("async def main(ctx):\n    return 'ran'\n")
        with TestClient(server.app) as client:
            response = client.post("/topics", json={"question": "q", "user_id": "alice", "python_file": outside})
        self.assertEqual(response.status_code, 500)
        self.assertIn("python_file", response.json()["detail"])


class TestCancellation(OasisServerTestCase):
    def test_cancelled_topic_ends_as_cancelled_and_conclusion_returns_at_once(self):
        with TestClient(server.app) as client:
            topic = client.post("/topics", json={
                "question": "q", "user_id": "alice", "schedule_yaml": HUMAN_YAML,
                "discussion": False, "bot_timeout": 60,
            }).json()
            topic_id = topic["topic_id"]
            self.assertTrue(self.wait_for(
                lambda: client.get(f"/topics/{topic_id}", params={"user_id": "alice"}).json()["pending_human"]
            ))
            client.delete(f"/topics/{topic_id}", params={"user_id": "alice"})
            self.assertEqual(self.wait_status(client, topic_id), "cancelled")

            started = time.time()
            result = client.get(f"/topics/{topic_id}/conclusion", params={"user_id": "alice", "timeout": 30}).json()
        self.assertEqual(result["status"], "cancelled")
        self.assertLess(time.time() - started, 5)


class TestSwarmRefresh(OasisServerTestCase):
    def test_refresh_does_not_block_the_event_loop(self):
        forum = DiscussionForum("t1", "q", "alice")
        forum.status = "concluded"
        server.discussions["t1"] = forum

        def slow_blueprint(*_args, **_kwargs):
            time.sleep(0.5)
            return {"summary": "ok"}

        async def run():
            ticks = 0

            async def heartbeat():
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.02)
                    ticks += 1

            beat = asyncio.create_task(heartbeat())
            await server.refresh_swarm("t1", user_id="alice")
            beat.cancel()
            return ticks

        with mock.patch.object(server, "generate_swarm_blueprint", slow_blueprint):
            ticks = asyncio.run(run())
        self.assertGreater(ticks, 10)
        self.assertEqual(forum.swarm, {"summary": "ok"})


class TestExpertModelOverride(OasisServerTestCase):
    def test_editing_a_team_expert_keeps_its_model_override(self):
        user_files = os.path.join(self.tmp.name, "user_files")
        override = {"model": "gpt-5.4", "api_key": "sk-x", "base_url": "https://api.example", "provider": "openai"}
        with mock.patch.object(experts_module, "USER_FILES_DIR", user_files):
            experts_module._save_team_experts("alice", "t1", [
                {"name": "GPT 顾问", "tag": "gpt", "persona": "p", "temperature": 0.5, **override},
            ])
            experts_module.update_team_expert("alice", "t1", "gpt", {"persona": "new persona"})
            saved = experts_module.load_team_experts("alice", "t1")[0]
        self.assertEqual(saved["persona"], "new persona")
        for key, value in override.items():
            self.assertEqual(saved[key], value)


class TestNetworkExposure(OasisServerTestCase):
    def test_other_hosts_need_the_token_when_listening_beyond_loopback(self):
        exposed = {"CLAWCROSS_SERVER_HOST": "0.0.0.0", "INTERNAL_TOKEN": "tok"}
        with mock.patch.dict(os.environ, exposed):
            remote = TestClient(server.app, client=("192.168.1.50", 5000))
            self.assertEqual(remote.get("/experts").status_code, 401)
            self.assertEqual(remote.get("/experts", headers={"X-Internal-Token": "bad"}).status_code, 401)
            self.assertEqual(remote.get("/experts", headers={"X-Internal-Token": "tok"}).status_code, 200)

            local = TestClient(server.app, client=("127.0.0.1", 5000))
            self.assertEqual(local.get("/experts").status_code, 200)  # launcher / run.sh health probe

    def test_loopback_only_server_is_unchanged(self):
        with mock.patch.dict(os.environ, {"CLAWCROSS_SERVER_HOST": "127.0.0.1", "INTERNAL_TOKEN": "tok"}):
            client = TestClient(server.app, client=("192.168.1.50", 5000))
            self.assertEqual(client.get("/experts").status_code, 200)


class TestStartNewOasisDiscussionFlag(unittest.IsolatedAsyncioTestCase):
    async def test_yaml_decides_unless_discussion_is_forced(self):
        import webot.tools.oasis as oasis_mcp

        bodies = []

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, json=None, **kwargs):
                bodies.append(json)
                return mock.Mock(status_code=200, json=lambda: {"topic_id": "x"})

        yaml = "version: 2\ndiscussion: true\nplan: []\n"
        with mock.patch.object(oasis_mcp.httpx, "AsyncClient", Client):
            await oasis_mcp.start_new_oasis(question="q", schedule_yaml=yaml, username="alice")
            await oasis_mcp.start_new_oasis(question="q", schedule_yaml=yaml, username="alice", discussion=True)

        self.assertNotIn("discussion", bodies[0])
        self.assertIs(bodies[1]["discussion"], True)


if __name__ == "__main__":
    unittest.main()
