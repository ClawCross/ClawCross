import json
import unittest
from unittest import mock

from oasis import swarm_engine


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, content):
        self._content = content

    def invoke(self, _messages):
        return _FakeResponse(self._content)


class OasisSwarmEngineTests(unittest.TestCase):
    def test_build_pending_swarm_returns_scaffold_graph(self):
        swarm = swarm_engine.build_pending_swarm(
            "预测一款新 AI 产品发布后市场、开发者和竞品会怎么互动",
            schedule_yaml='\n'.join([
                'version: 1',
                'plan:',
                '  - parallel:',
                '      - "creative#temp#1"',
                '      - "critical#temp#2"',
            ]),
            mode="prediction",
        )

        self.assertEqual(swarm["status"], "pending")
        self.assertEqual(swarm["source"], "scaffold")
        self.assertEqual(swarm["mode"], "prediction")
        self.assertGreaterEqual(len(swarm["graph"]["nodes"]), 6)
        self.assertTrue(any(node["type"] == "objective" for node in swarm["graph"]["nodes"]))
        self.assertTrue(any(node["type"] == "agent" for node in swarm["graph"]["nodes"]))
        self.assertTrue(any(node["type"] == "memory" for node in swarm["graph"]["nodes"]))
        self.assertTrue(any(node["type"] == "scenario" for node in swarm["graph"]["nodes"]))

    @mock.patch.object(swarm_engine, "create_chat_model")
    def test_generate_swarm_blueprint_normalizes_llm_payload(self, mock_create_chat_model):
        mock_create_chat_model.return_value = _FakeLLM(
            json.dumps(
                {
                    "summary": "A focused blueprint.",
                    "objective": "Forecast platform adoption.",
                    "prediction": "The baseline favors gradual uptake with stress around ecosystem fragmentation.",
                    "signals": ["developer sentiment", "pricing pressure"],
                    "scenarios": [{"label": "Base Case", "summary": "Steady adoption", "probability": "high"}],
                    "graphrag": {"collections": ["world-memory"]},
                    "graph": {
                        "nodes": [
                            {"id": "brief", "label": "Launch Brief", "type": "objective", "summary": "Core brief"},
                            {"id": "market", "label": "Market", "type": "entity", "summary": "Demand and positioning"},
                        ],
                        "edges": [
                            {"source": "brief", "target": "market", "label": "tracks", "weight": 0.7},
                        ],
                    },
                }
            )
        )

        swarm = swarm_engine.generate_swarm_blueprint(
            "预测一个新平台发布后的生态演化",
            schedule_yaml='\n'.join([
                'version: 1',
                'plan:',
                '  - parallel:',
                '      - "creative#temp#1"',
                '      - "critical#temp#2"',
            ]),
            mode="prediction",
        )

        self.assertEqual(swarm["status"], "ready")
        self.assertEqual(swarm["source"], "llm")
        self.assertEqual(swarm["summary"], "A focused blueprint.")
        self.assertGreaterEqual(len(swarm["graph"]["nodes"]), 4)
        self.assertTrue(any(node["type"] == "agent" for node in swarm["graph"]["nodes"]))
        self.assertTrue(any(edge["source"] and edge["target"] for edge in swarm["graph"]["edges"]))

    @mock.patch.object(swarm_engine, "create_chat_model", side_effect=RuntimeError("boom"))
    def test_generate_swarm_blueprint_falls_back_when_llm_fails(self, _mock_create_chat_model):
        swarm = swarm_engine.generate_swarm_blueprint(
            "预测一项政策变化的链式反应",
            schedule_yaml='expert: "data#temp#1"',
        )

        self.assertEqual(swarm["status"], "ready")
        self.assertEqual(swarm["source"], "fallback")
        self.assertIn("diagnostics", swarm)


if __name__ == "__main__":
    unittest.main()
