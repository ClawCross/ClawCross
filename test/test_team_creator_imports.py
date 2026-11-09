import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import front
import services.skill_import_tools as skill_import_tools
import services.team_creator_service as svc


class ClawcrossCreatorImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        front.app.config.update(TESTING=True)

    def setUp(self):
        self.client = front.app.test_client()

    def test_creator_page_renders_local_path_import_fields(self):
        response = self.client.get("/creator")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="feishu-app-id"', html)
        self.assertIn('id="generate-colleague-btn"', html)
        self.assertIn('id="import-colleague-dir"', html)
        self.assertIn('id="mentor-arxiv-name"', html)
        self.assertIn('id="generate-mentor-btn"', html)
        self.assertIn('id="import-mentor-json-path"', html)
        self.assertIn('id="import-mentor-skill-path"', html)

    def test_distill_colleague_skill_artifacts_uses_llm_payload(self):
        meta_json = {
            "name": "张三",
            "profile": {"company": "Clawcross", "role": "后端工程师"},
            "tags": {"personality": ["直接"], "culture": ["结果导向"]},
        }
        llm_payload = {
            "persona_md": "# 张三 — Persona\n\n## Layer 0：核心性格\n- 先给结论\n\n## Layer 1：沟通风格\n- 信息密度高\n\n## Layer 2：决策偏好\n- 看证据\n\n## Layer 3：协作模式\n- 明确输入输出\n\n## Layer 4：压力与边界\n- 不喜欢重复背景",
            "work_md": "# 张三 — Work Skill\n\n## 职责范围\n- 负责核心后端交付\n\n## 关键上下文\n- 熟悉用户链路\n\n## 工作流程\n1. 先确认边界\n\n## 交付偏好\n- 结论先行",
            "personality_tags": ["直接", "数据驱动"],
            "culture_tags": ["结果导向"],
            "impression": "说话短，判断快。",
            "evidence_summary": "Based on Feishu messages.",
        }

        class FakeLlm:
            def invoke(self, _prompt):
                return SimpleNamespace(content=json.dumps(llm_payload, ensure_ascii=False))

        with mock.patch("services.llm_factory.create_chat_model", return_value=FakeLlm()):
            distilled = svc.distill_colleague_skill_artifacts(
                meta_json=meta_json,
                messages_text="## 日常消息（风格参考）\n\n[10:00] 先确认边界，再推进方案。",
            )

        self.assertEqual(distilled["persona_md"], llm_payload["persona_md"])
        self.assertEqual(distilled["work_md"], llm_payload["work_md"])
        self.assertEqual(distilled["personality_tags"], ["直接", "数据驱动"])
        self.assertEqual(distilled["culture_tags"], ["结果导向"])
        self.assertEqual(distilled["impression"], "说话短，判断快。")

    def test_extract_colleague_responsibilities_prefers_duty_and_process_sections(self):
        work_md = """# 张三 — Work Skill

## 职责范围

- 用户中台服务（user-center）：用户注册、登录、权限管理
- 内部 BI 数据导出接口

## 技术规范

- 统一返回结构：{ code, message, data }

## 工作流程

1. 先看 PRD 里的边界条件，把模糊的地方列出来问产品
2. 有止血方案先止血（回滚/降级），再查根因
"""

        responsibilities = svc._extract_colleague_responsibilities(work_md)

        self.assertIn("用户中台服务（user-center）：用户注册、登录、权限管理", responsibilities)
        self.assertIn("内部 BI 数据导出接口", responsibilities)
        self.assertIn("先看 PRD 里的边界条件，把模糊的地方列出来问产品", responsibilities)
        self.assertNotIn("统一返回结构：{ code, message, data }", responsibilities)

    @mock.patch.object(svc, "enhance_workflow_graph_via_llm", return_value=None)
    def test_import_colleague_skill_preserves_full_persona_and_metadata(self, _mock_llm):
        meta_json = {
            "name": "张三",
            "slug": "zhangsan",
            "version": "v3",
            "profile": {
                "company": "字节跳动",
                "level": "2-1",
                "role": "后端工程师",
                "gender": "男",
                "mbti": "INTJ",
            },
            "tags": {
                "personality": ["话少", "数据驱动"],
                "culture": ["字节范"],
            },
            "impression": "评审会上经常一针见血。",
            "knowledge_sources": ["knowledge/docs/design.pdf"],
            "corrections_count": 2,
        }
        persona_md = "## Layer 0：核心性格\n\n- impact 优先"
        work_md = """## 职责范围

- 用户中台服务（user-center）：用户注册、登录、权限管理

## 工作流程

1. 先看 PRD 里的边界条件
"""

        team_config = svc.import_colleague_skill(
            meta_json=meta_json,
            persona_md=persona_md,
            work_md=work_md,
            team_name="同事团队",
            task_description="模拟同事协作",
        )

        self.assertEqual(team_config["summary"]["import_source"], "colleague-skill")
        self.assertEqual(team_config["summary"]["colleague_meta"]["slug"], "zhangsan")
        self.assertEqual(team_config["summary"]["colleague_meta"]["knowledge_sources"], ["knowledge/docs/design.pdf"])
        self.assertEqual(team_config["summary"]["colleague_meta"]["corrections_count"], 2)
        persona = team_config["oasis_experts"][0]["persona"]
        self.assertIn("## PART A：工作能力", persona)
        self.assertIn("## PART B：人物性格", persona)
        self.assertIn("## Layer 0：核心性格", persona)
        self.assertEqual(team_config["oasis_experts"][0]["source"], "colleague-skill")

    @mock.patch.object(svc, "enhance_workflow_graph_via_llm", return_value=None)
    def test_import_mentor_skill_prefers_generated_skill_and_summary_source(self, _mock_llm):
        mentor_json = {
            "meta": {
                "mentor_name": "Geoffrey Hinton",
                "affiliation": "University of Toronto",
            },
            "profile": {
                "name_en": "Geoffrey Hinton",
                "institution": "University of Toronto",
                "position": "Professor",
                "website": "https://example.edu/hinton",
                "languages": "en",
            },
            "research": {
                "primary_fields": ["representation learning", "large language models"],
                "research_summary": "Focused on representation learning and neural networks.",
                "key_publications": [
                    {
                        "title": "Capsules",
                        "summary": "Introduced capsule networks.",
                        "venue": "ArXiv",
                        "year": 2017,
                    }
                ],
            },
            "style": {
                "research_style": {
                    "type": "理论驱动型",
                    "keywords": ["representation", "backprop"],
                    "description": "强调基础理论与直觉。",
                },
                "communication_style": {
                    "tone": "直接",
                    "language": "English",
                    "characteristics": "Short and opinionated",
                },
                "academic_values": ["学术严谨"],
                "expertise_areas": ["深度学习"],
            },
            "achievements": {
                "academic_service": ["NeurIPS keynote"],
                "honors": ["Turing Award"],
            },
            "source_materials": {
                "websites_visited": ["https://example.edu/hinton"],
            },
        }
        skill_md = """---
name: geoffrey-hinton
description: auto generated
---

# Geoffrey Hinton AI Mentor

Always question shallow reasoning.
"""

        team_config = svc.import_mentor_skill(
            mentor_json=mentor_json,
            skill_md=skill_md,
            team_name="导师团队",
            task_description="模拟导师指导",
        )

        self.assertEqual(team_config["summary"]["import_source"], "supervisor-mentor")
        self.assertEqual(team_config["summary"]["mentor_meta"]["website"], "https://example.edu/hinton")
        self.assertEqual(team_config["summary"]["mentor_meta"]["languages"], "en")
        self.assertEqual(team_config["summary"]["mentor_meta"]["source_materials"]["websites_visited"], ["https://example.edu/hinton"])
        persona = team_config["oasis_experts"][0]["persona"]
        self.assertTrue(persona.startswith("# Geoffrey Hinton AI Mentor"))
        self.assertNotIn("---", persona)
        self.assertEqual(team_config["oasis_experts"][0]["source"], "supervisor-mentor")

    @mock.patch.object(svc, "enhance_workflow_graph_via_llm", return_value=None)
    def test_import_colleague_route_accepts_directory_path(self, _mock_llm):
        with TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "meta.json").write_text(json.dumps({
                "name": "李四",
                "slug": "lisi",
                "profile": {"company": "Clawcross", "level": "L3", "role": "产品经理"},
                "tags": {"personality": ["直接"], "culture": ["效率优先"]},
            }, ensure_ascii=False), encoding="utf-8")
            (base / "persona.md").write_text("## Layer 0：核心性格\n\n- 直接给结论", encoding="utf-8")
            (base / "work.md").write_text("## 职责范围\n\n- 产品规划", encoding="utf-8")

            response = self.client.post(
                "/api/team-creator/import-colleague",
                json={"colleague_dir_path": str(base), "team_name": "李四团队"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["import_source"], "colleague-skill")
        self.assertEqual(payload["summary"]["colleague_meta"]["name"], "李四")
        self.assertIn("Layer 0", payload["team_config"]["oasis_experts"][0]["persona"])

    @mock.patch.object(svc, "enhance_workflow_graph_via_llm", return_value=None)
    def test_import_mentor_route_accepts_local_paths(self, _mock_llm):
        with TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            mentor_json_path = base / "Geoffrey_Hinton.json"
            mentor_json_path.write_text(json.dumps({
                "meta": {"mentor_name": "Geoffrey Hinton"},
                "profile": {"name_en": "Geoffrey Hinton", "institution": "University of Toronto"},
                "research": {"primary_fields": ["representation learning"], "key_publications": []},
                "style": {"research_style": {"type": "理论驱动型", "keywords": []}, "communication_style": {}},
            }, ensure_ascii=False), encoding="utf-8")
            skill_path = base / "SKILL.md"
            skill_path.write_text("---\nname: mentor\n---\n\n# Geoffrey Hinton AI Mentor\n", encoding="utf-8")

            response = self.client.post(
                "/api/team-creator/import-mentor",
                json={
                    "mentor_json_path": str(mentor_json_path),
                    "skill_md_path": str(skill_path),
                    "team_name": "导师团队",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["import_source"], "supervisor-mentor")
        self.assertEqual(payload["summary"]["mentor_meta"]["name"], "Geoffrey Hinton")
        self.assertIn("# Geoffrey Hinton AI Mentor", payload["team_config"]["oasis_experts"][0]["persona"])

    @mock.patch.object(svc, "enhance_workflow_graph_via_llm", return_value=None)
    def test_arxiv_search_route_can_auto_import(self, _mock_llm):
        papers = [
            skill_import_tools.ArxivPaper(
                title="Representation learning at scale",
                summary="Representation learning methods for large neural networks.",
                authors=["Geoffrey Hinton"],
                published="2026-01-01T00:00:00Z",
                arxiv_id="2601.00001",
                year=2026,
            )
        ]

        with mock.patch.object(skill_import_tools, "search_arxiv", return_value=papers):
            response = self.client.post(
                "/api/team-creator/arxiv-search",
                json={
                    "author_name": "Geoffrey Hinton",
                    "affiliation": "University of Toronto",
                    "auto_import": True,
                    "team_name": "导师团队",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["papers_count"], 1)
        self.assertTrue(payload["auto_imported"])
        self.assertEqual(payload["summary"]["import_source"], "supervisor-mentor")

    def test_feishu_collect_route_returns_meta_and_messages(self):
        with mock.patch.object(
            skill_import_tools,
            "feishu_collect_user_messages",
            return_value="## 日常消息（风格参考）\n\n[10:00] 先对齐一下背景",
        ):
            response = self.client.post(
                "/api/team-creator/feishu-collect",
                json={
                    "app_id": "app-id",
                    "app_secret": "app-secret",
                    "target_name": "张三",
                    "company": "字节跳动",
                    "role": "后端工程师",
                    "personality_tags": ["话少"],
                    "culture_tags": ["字节范"],
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["meta_json"]["name"], "张三")
        self.assertEqual(payload["meta_json"]["profile"]["role"], "后端工程师")
        self.assertIn("feishu_auto_collect", payload["meta_json"]["knowledge_sources"])
        self.assertIn("风格参考", payload["messages_text"])

    @mock.patch.object(svc, "enhance_workflow_graph_via_llm", return_value=None)
    def test_feishu_collect_route_can_auto_distill_and_import(self, _mock_llm):
        distilled_payload = {
            "persona_md": "# 张三 — Persona\n\n## Layer 0：核心性格\n- 先给结论\n\n## Layer 1：沟通风格\n- 信息密度高\n\n## Layer 2：决策偏好\n- 看证据\n\n## Layer 3：协作模式\n- 明确输入输出\n\n## Layer 4：压力与边界\n- 不喜欢重复背景",
            "work_md": "# 张三 — Work Skill\n\n## 职责范围\n- 负责核心后端交付\n\n## 关键上下文\n- 熟悉用户链路\n\n## 工作流程\n1. 先确认边界\n\n## 交付偏好\n- 结论先行",
            "personality_tags": ["直接", "数据驱动"],
            "culture_tags": ["结果导向"],
            "impression": "说话短，判断快。",
            "evidence_summary": "Based on Feishu messages.",
        }

        with mock.patch.object(
            skill_import_tools,
            "feishu_collect_user_messages",
            return_value="## 日常消息（风格参考）\n\n[10:00] 先确认边界，再推进方案。",
        ), mock.patch.object(front, "distill_colleague_skill_artifacts", return_value=distilled_payload):
            response = self.client.post(
                "/api/team-creator/feishu-collect",
                json={
                    "app_id": "app-id",
                    "app_secret": "app-secret",
                    "target_name": "张三",
                    "company": "Clawcross",
                    "role": "后端工程师",
                    "auto_distill": True,
                    "auto_import": True,
                    "team_name": "张三团队",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["auto_imported"])
        self.assertEqual(payload["summary"]["import_source"], "colleague-skill")
        self.assertEqual(payload["distillation"]["personality_tags"], ["直接", "数据驱动"])
        self.assertEqual(payload["meta_json"]["tags"]["culture"], ["结果导向"])
        self.assertIn("## PART A：工作能力", payload["team_config"]["oasis_experts"][0]["persona"])
        self.assertIn("张三", payload["team_config"]["oasis_experts"][0]["name"])


if __name__ == "__main__":
    unittest.main()
