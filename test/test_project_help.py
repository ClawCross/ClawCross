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
