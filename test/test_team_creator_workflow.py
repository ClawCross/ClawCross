import sys
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import services.team_creator_service as svc


class ClawcrossCreatorWorkflowTests(unittest.TestCase):
    @mock.patch.object(svc, "enhance_workflow_graph_via_llm", return_value=None)
    def test_build_from_roles_creates_selector_review_loop(self, _mock_llm):
        roles = [
            {
                "role_name": "增长策略负责人",
                "primary_responsibilities": ["定义增长目标", "统筹获客与留存策略"],
                "depends_on": [],
                "tools_used": ["Notion", "Looker"],
            },
            {
                "role_name": "内容运营",
                "primary_responsibilities": ["产出内容与活动素材", "执行渠道分发"],
                "depends_on": ["增长策略负责人"],
                "tools_used": ["Figma", "Notion"],
            },
            {
                "role_name": "数据分析师",
                "primary_responsibilities": ["分析漏斗数据", "输出实验结论"],
                "depends_on": ["增长策略负责人"],
                "tools_used": ["SQL", "Python"],
            },
            {
                "role_name": "QA审核负责人",
                "primary_responsibilities": ["审核方案质量", "决定是否需要返工"],
                "depends_on": ["内容运营", "数据分析师"],
                "tools_used": ["Checklist"],
            },
        ]

        result = svc.build_from_roles(roles, "增长团队", "构建一个可持续的 SaaS 增长团队")

        graph = result["workflow_graph"]
        summary = result["summary"]
        layout = result["workflow_layout"]

        self.assertTrue(summary["dag_enhanced"])
        self.assertEqual(summary["workflow_nodes"], len(graph["plan"]))
        self.assertEqual(summary["selector_nodes"], 1)
        self.assertEqual(summary["review_loops"], 1)

        self.assertTrue(any(step.get("author") == "begin" for step in graph["plan"]))
        self.assertTrue(any(step.get("author") == "bend" for step in graph["plan"]))

        review_step = next(
            step for step in graph["plan"]
            if step.get("performing_role") == "QA审核负责人"
        )
        self.assertTrue(review_step.get("selector"))
        self.assertEqual(graph["selector_edges"][0]["source"], review_step["id"])
        self.assertEqual(graph["selector_edges"][0]["choices"]["1"], "end")

        self.assertIn("selector_edges:", result["yaml_workflow"])
        self.assertIn("selector: true", result["yaml_workflow"])

        self.assertEqual(len(layout["nodes"]), len(graph["plan"]))
        end_node = next(node for node in layout["nodes"] if node.get("author") == "bend")
        review_node = next(node for node in layout["nodes"] if node.get("name") == "QA审核负责人")
        self.assertGreater(end_node["x"], review_node["x"])

    @mock.patch.object(svc, "enhance_workflow_graph_via_llm", return_value=None)
    def test_build_from_roles_still_emits_boundaries_without_review_role(self, _mock_llm):
        roles = [
            {
                "role_name": "产品负责人",
                "primary_responsibilities": ["明确目标", "拆解关键里程碑"],
                "depends_on": [],
                "tools_used": ["Notion"],
            },
            {
                "role_name": "设计师",
                "primary_responsibilities": ["输出交互稿", "定义视觉方案"],
                "depends_on": ["产品负责人"],
                "tools_used": ["Figma"],
            },
            {
                "role_name": "工程师",
                "primary_responsibilities": ["实现功能", "联调交付"],
                "depends_on": ["产品负责人", "设计师"],
                "tools_used": ["Python", "Git"],
            },
        ]

        result = svc.build_from_roles(roles, "产品交付团队", "交付一个新的产品功能")

        graph = result["workflow_graph"]
        summary = result["summary"]

        self.assertEqual(summary["workflow_mode"], "heuristic")
        self.assertEqual(summary["selector_nodes"], 0)
        self.assertEqual(summary["workflow_nodes"], len(roles) + 2)
        self.assertTrue(any(step.get("author") == "begin" for step in graph["plan"]))
        self.assertTrue(any(step.get("author") == "bend" for step in graph["plan"]))
        self.assertEqual(graph["selector_edges"], [])
        self.assertIn("version: 2", result["yaml_workflow"])


if __name__ == "__main__":
    unittest.main()
