import unittest
from ops.project_help import lookup, TOPICS
from common.runtime_paths import PROJECT_ROOT


class ProjectHelpTests(unittest.TestCase):
    def test_topic_sources_exist_and_index_does_not_load_content(self):
        index=lookup();self.assertEqual(len(index['topics']),len(TOPICS))
        self.assertNotIn('content',str(index))
        for title,filename,summary in TOPICS.values():self.assertTrue((PROJECT_ROOT/'docs'/filename).is_file(),filename)

    def test_content_is_bounded_and_can_read_a_specific_section(self):
        result=lookup('configuration')['results'][0]
        self.assertLessEqual(len(result['content']),6000)
        self.assertIn('私密填写',result['sections'])
        excerpt=lookup('configuration',section='私密填写')['results'][0]
        self.assertIn('密钥不进入聊天',excerpt['content'])
        self.assertNotIn('## 审批登记',excerpt['content'])

    def test_unknown_path_is_rejected_and_search_is_local(self):
        with self.assertRaises(ValueError):lookup('../../config/.env')
        self.assertEqual(lookup(query='no-such-feature-unlikely')['results'],[])
        self.assertTrue(lookup('configuration',query='KEEP Y')['results'])

    def test_mini_app_development_is_discoverable_and_sections_are_complete(self):
        overview = lookup('apps')['results'][0]
        self.assertIn('SDK 选择', overview['sections'])
        self.assertIn('状态与存档', overview['sections'])
        self.assertIn('实时页面与输入焦点', overview['sections'])
        example = lookup('apps', section='最小示例')['results'][0]
        self.assertIn('module.exports', example['content'])
        self.assertFalse(example['truncated'])
        self.assertNotIn('## 安全边界', example['content'])
        state = lookup('apps', section='状态与存档')['results'][0]
        self.assertIn('CLAWCROSS_SPACE_DATA_DIR', state['content'])
        self.assertTrue(lookup(query='小程序 SDK')['results'])
        self.assertIn('SDK 选择', lookup('apps', section='missing-section')['sections'])

    def test_teams_help_uses_main_agent_tools(self):
        result = lookup('teams')['results'][0]
        self.assertEqual(result['source'], 'docs/team-management.md')
        self.assertIn('manage_team', result['content'])
        self.assertIn('不再有独立的 Team Creator 页面', result['content'])
