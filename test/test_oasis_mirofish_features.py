import asyncio
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from oasis import server
from oasis.forum import DiscussionForum


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def invoke(self, _messages):
        return _FakeResponse("我是 Analyst，我根据帖子和时间线解释了自己的行动。")


def _seed_topic() -> DiscussionForum:
    server.discussions.clear()
    server.engines.clear()
    server.tasks.clear()

    forum = DiscussionForum(
        topic_id="topic-mirofish",
        question="Project Alpha 下一步该如何验证？",
        user_id="alice",
        max_rounds=3,
    )
    forum.status = "discussing"
    forum.current_round = 1
    forum.start_clock()

    p1 = asyncio.run(forum.publish("Analyst", "先确认数据管线和 metrics schema。"))
    p1.upvotes = 3
    p1.downvotes = 1
    p2 = asyncio.run(forum.publish("Analyst", "我会复查最近一次 run 的日志。", reply_to=p1.id))
    p2.upvotes = 1
    asyncio.run(forum.publish("Builder", "我负责补 verifier 和 artifact contract。"))

    forum.log_event("agent_call", agent="Analyst", detail="inspect metrics")
    forum.log_event("agent_done", agent="Analyst", detail="metrics parsed")
    forum.log_event("agent_error", agent="Builder", detail="missing artifact")

    server.discussions[forum.topic_id] = forum
    return forum


class OasisMiroFishFeatureTests(unittest.TestCase):
    def tearDown(self):
        server.discussions.clear()
        server.engines.clear()
        server.tasks.clear()

    def test_agent_stats_summarize_posts_votes_and_timeline(self):
        forum = _seed_topic()
        with TestClient(server.app) as client:
            response = client.get(f"/topics/{forum.topic_id}/agent-stats", params={"user_id": "alice"})

        self.assertEqual(response.status_code, 200)
        agents = response.json()["agents"]
        analyst = next(item for item in agents if item["agent_name"] == "Analyst")
        self.assertEqual(analyst["posts"], 2)
        self.assertEqual(analyst["replies"], 1)
        self.assertEqual(analyst["net_votes"], 3)
        self.assertEqual(analyst["calls"], 1)
        self.assertEqual(analyst["done"], 1)

        builder = next(item for item in agents if item["agent_name"] == "Builder")
        self.assertEqual(builder["errors"], 1)

    def test_actions_expose_post_and_timeline_feed_with_agent_filter(self):
        forum = _seed_topic()
        with TestClient(server.app) as client:
            response = client.get(
                f"/topics/{forum.topic_id}/actions",
                params={"user_id": "alice", "agent": "agent:Analyst"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        action_types = {item["action_type"] for item in payload["actions"]}
        self.assertIn("CREATE_POST", action_types)
        self.assertIn("REPLY_POST", action_types)
        self.assertIn("AGENT_CALL", action_types)
        self.assertTrue(all(item["agent_name"] == "Analyst" for item in payload["actions"]))

    @mock.patch.object(server, "create_chat_model", return_value=_FakeLLM())
    def test_interview_uses_agent_context_and_returns_evidence(self, _mock_llm):
        forum = _seed_topic()
        with TestClient(server.app) as client:
            response = client.post(
                f"/topics/{forum.topic_id}/interview",
                json={
                    "user_id": "alice",
                    "agent_name": "Analyst",
                    "prompt": "你刚才做了什么？",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agent"]["agent_name"], "Analyst")
        self.assertIn("Analyst", payload["answer"])
        self.assertGreaterEqual(len(payload["evidence_posts"]), 2)


if __name__ == "__main__":
    unittest.main()
