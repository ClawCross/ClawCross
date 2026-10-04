import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from webot import skills, workspace, runtime_settings
from webot.skill_memory import list_memory, memory_target


class WorkspaceSkillsTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        for module,name,value in [(skills,'USER_FILES_DIR',self.root/'data'),(skills,'WORKSPACE_DIR',self.root/'workspace'),
            (workspace,'WORKSPACE_DIR',self.root/'workspace'),(workspace,'USER_FILES_DIR',self.root/'data'),
            (runtime_settings,'USER_FILES_DIR',self.root/'data')]:
            p=patch.object(module,name,value);p.start();self.addCleanup(p.stop)

    def test_same_user_agents_share_skills_inside_clean_workspace(self):
        result=skills.create_skill('alice',name='shared',content='---\nname: shared\ndescription: shared skill\n---\nReusable instructions')
        first=workspace.resolve_session_workspace('alice','one');second=workspace.resolve_session_workspace('alice','two')
        self.assertEqual(first.root,second.root)
        self.assertTrue(Path(result['path']).is_relative_to(first.root))
        self.assertEqual(len(list_memory('alice')),1);self.assertEqual(list_memory('bob'),[])
        self.assertFalse(runtime_settings.settings_path('alice').is_relative_to(first.root))

    def test_legacy_files_migrate_once_and_keep_memory_ids(self):
        legacy=self.root/'data/alice/skills/old';legacy.mkdir(parents=True)
        (legacy/'SKILL.md').write_text('---\nname: old\ndescription: old skill\n---\nKeep this')
        (legacy/'helper.py').write_text('print("hello")')
        entries=list_memory('alice');entry=memory_target('alice',entries[0]['id'])
        self.assertEqual(entry['_path'],self.root/'workspace/users/alice/skills/old/SKILL.md')
        self.assertEqual((entry['_path'].parent/'helper.py').read_text(),'print("hello")')
        self.assertEqual(list_memory('alice')[0]['id'],entries[0]['id'])
        self.assertFalse(legacy.exists())

    def test_new_workspace_content_wins_without_deleting_legacy_conflicts(self):
        new=self.root/'workspace/users/alice/skills/old';new.mkdir(parents=True);(new/'SKILL.md').write_text('current')
        old=self.root/'data/alice/skills/old';old.mkdir(parents=True);(old/'SKILL.md').write_text('legacy')
        (old/'notes.md').write_text('retain notes')
        skills._skills_dir('alice')
        self.assertEqual((new/'SKILL.md').read_text(),'current');self.assertEqual((old/'SKILL.md').read_text(),'legacy')
        self.assertEqual((new/'notes.md').read_text(),'retain notes')

    def test_strict_workspace_stays_separate_without_adding_sandbox_grants(self):
        skills._skills_dir('alice')
        runtime_settings.save_runtime_settings('alice',session_id='one',settings={'approval':{'sandbox_security':'strict'}})
        strict=workspace.resolve_session_workspace('alice','one')
        self.assertFalse(skills._skills_dir('alice').is_relative_to(strict.root))
        self.assertEqual(runtime_settings.get_runtime_settings('alice','one').approval.sandbox_grants,[])
