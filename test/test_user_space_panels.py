import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'src' / 'backend'))

import frontend.server as front
import oasis.experts as experts
import webot.skills as skills
import webot.workspace as workspace
from agents.store import AgentStore
from teams.routes import team_card
from teams.store import TeamStore


class UserSpacePanelsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TeamStore(AgentStore(self.root / 'agents.db'), self.root / 'user_files')
        self.store.create('tester', 'real-team')
        for module, name, value in (
            (front, '_teams', lambda: self.store),
            (front, 'USER_FILES_DIR', self.root / 'user_files'),
            (skills, 'USER_FILES_DIR', self.root / 'user_files'),
            (skills, 'WORKSPACE_DIR', self.root / 'workspace'),
            (workspace, 'WORKSPACE_DIR', self.root / 'workspace'),
            (experts, '_USER_EXPERTS_DIR', str(self.root / 'personas')),
        ):
            self.enterContext(patch.object(module, name, value))
        front.app.config.update(TESTING=True)
        self.client = front.app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = 'tester'

    def content(self, name, body='instructions'):
        return f'---\nname: {name}\ndescription: test skill\n---\n\n{body}'

    def test_virtual_team_metadata_has_no_member_file(self):
        payload = self.client.get('/teams').get_json()
        self.assertEqual(payload['teams'], ['__default__', 'real-team'])
        self.assertEqual(payload['team_info'][0]['kind'], 'user_space')
        self.assertTrue(payload['team_info'][0]['virtual'])
        self.assertFalse(payload['team_info'][1]['virtual'])
        card = team_card(self.store, 'tester', '__default__')
        self.assertTrue(card['virtual'])
        self.assertEqual(card['title'], '用户空间')
        self.assertFalse(self.store.folder('tester', '__default__').exists())

    def test_default_skills_use_user_workspace_and_directory_identity(self):
        created = skills.create_skill('tester', name='folder-key', content=self.content('中文显示名'))
        self.assertTrue(created['success'], created)
        response = self.client.get('/teams/__default__/skills')
        self.assertEqual(response.status_code, 200)
        sections = response.get_json()['skills']
        self.assertEqual(sections['team'], [])
        self.assertEqual(sections['personal'][0]['name'], '中文显示名')
        self.assertEqual(sections['personal'][0]['id'], 'folder-key')
        path = Path(sections['personal'][0]['path'])
        self.assertEqual(path, self.root / 'workspace/users/tester/skills/folder-key/SKILL.md')
        updated = self.content('新的显示名', 'new body')
        response = self.client.put('/teams/__default__/skills/folder-key', json={'content': updated})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(path.read_text(), updated)
        response = self.client.get('/skills/folder-key')
        self.assertEqual(response.get_json()['skill']['content'], updated)
        self.assertEqual(self.client.delete('/teams/__default__/skills/folder-key').status_code, 200)
        self.assertFalse(path.exists())
        self.assertFalse(self.store.folder('tester', '__default__').exists())

    def test_default_skill_zip_import_does_not_create_a_team(self):
        package = io.BytesIO()
        with zipfile.ZipFile(package, 'w') as archive:
            archive.writestr('demo/SKILL.md', self.content('demo'))
        package.seek(0)
        response = self.client.post('/teams/__default__/skills/import-zip', data={'file': (package, 'demo.zip')})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIsNotNone(skills.get_skill('tester', name='demo'))
        self.assertFalse(self.store.folder('tester', '__default__').exists())

    def test_default_personas_include_presets_and_store_edits_in_user_pool(self):
        persona = {'name': '我的人设', 'tag': 'mine', 'persona': 'help the user', 'temperature': 0.5}
        response = self.client.post('/teams/__default__/experts', json=persona)
        self.assertEqual(response.status_code, 200)
        response = self.client.get('/teams/__default__/experts')
        self.assertEqual(response.status_code, 200)
        rows = response.get_json()['experts']
        self.assertTrue(any(row['source'] == 'public' and not row['deletable'] for row in rows))
        self.assertTrue(any(row['tag'] == 'mine' and row['deletable'] for row in rows))
        self.assertEqual(len(experts.load_user_experts('tester')), 1)
        self.assertEqual(self.client.put('/teams/__default__/experts/mine', json={'persona': 'updated'}).status_code, 200)
        self.assertEqual(experts.load_user_experts('tester')[0]['persona'], 'updated')
        self.assertEqual(self.client.delete('/teams/__default__/experts/mine').status_code, 200)
        self.assertFalse(self.store.folder('tester', '__default__').exists())

    def test_all_bundled_preset_persona_pools_load_as_real_teams(self):
        for pool in sorted((ROOT / 'data/team_presets').glob('*/oasis_experts.json')):
            with self.subTest(preset=pool.parent.name):
                name = pool.parent.name
                self.store.create('tester', name)
                (self.store.folder('tester', name) / 'oasis_experts.json').write_text(pool.read_text())
                response = self.client.get(f'/teams/{name}/experts')
                self.assertEqual(response.status_code, 200)
                rows = response.get_json()['experts']
                self.assertEqual(len(rows), len(json.loads(pool.read_text())))
                self.assertTrue(all(row['source'] == 'team' and row['deletable'] for row in rows))


if __name__ == '__main__':
    unittest.main()
