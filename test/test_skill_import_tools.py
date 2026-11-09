import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import services.skill_import_tools as tools


class SkillImportToolsTests(unittest.TestCase):
    def test_arxiv_papers_to_mentor_json_builds_importable_profile(self):
        papers = [
            tools.ArxivPaper(
                title="Large language models for representation learning",
                summary="A paper about language models and representation learning.",
                authors=["Geoffrey Hinton"],
                published="2026-01-01T00:00:00Z",
                arxiv_id="2601.00001",
                year=2026,
            ),
            tools.ArxivPaper(
                title="Graph neural networks for recommendation",
                summary="A paper about graph neural networks and recommendation.",
                authors=["Geoffrey Hinton"],
                published="2025-01-01T00:00:00Z",
                arxiv_id="2501.00001",
                year=2025,
            ),
        ]

        mentor_json = tools.arxiv_papers_to_mentor_json(
            papers,
            mentor_name="Geoffrey Hinton",
            affiliation="University of Toronto",
        )

        self.assertEqual(mentor_json["meta"]["mentor_name"], "Geoffrey Hinton")
        self.assertEqual(mentor_json["profile"]["institution"], "University of Toronto")
        self.assertTrue(mentor_json["research"]["primary_fields"])
        self.assertEqual(len(mentor_json["research"]["key_publications"]), 2)
        self.assertTrue(mentor_json["style"]["research_style"]["type"])

    def test_feishu_messages_to_colleague_meta_marks_collected_source(self):
        meta_json = tools.feishu_messages_to_colleague_meta(
            target_name="张三",
            messages_text="[10:00] 先对齐一下背景",
            company="字节跳动",
            role="后端工程师",
            level="2-1",
            personality_tags=["话少"],
            culture_tags=["字节范"],
            impression="结论先行",
        )

        self.assertEqual(meta_json["name"], "张三")
        self.assertEqual(meta_json["profile"]["role"], "后端工程师")
        self.assertEqual(meta_json["tags"]["personality"], ["话少"])
        self.assertEqual(meta_json["tags"]["culture"], ["字节范"])
        self.assertEqual(meta_json["knowledge_sources"], ["feishu_auto_collect"])


if __name__ == "__main__":
    unittest.main()
