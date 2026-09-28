import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import services.team_preset_assets as team_preset_assets
from agents.store import AgentStore
from teams.store import TeamStore


class TeamPresetAssetsTests(unittest.TestCase):
    def test_repo_ships_expected_presets(self):
        preset_ids = {item["preset_id"] for item in team_preset_assets.list_team_presets()}
        self.assertTrue({"ming-neige", "tang-sansheng-beta", "modern-ceo", "hanlin-novel-studio"}.issubset(preset_ids))

    def test_shipped_workflows_name_only_the_presets_roles_and_known_personas(self):
        sys.path.insert(0, str(PROJECT_ROOT))
        from oasis.scheduler import extract_expert_names, parse_schedule

        def entries(path):
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

        prompts = PROJECT_ROOT / "data" / "prompts"
        public = {e["tag"] for name in ("oasis_experts.json", "agency_experts.json") for e in entries(prompts / name)}
        for preset in sorted((PROJECT_ROOT / "data" / "team_presets").iterdir()):
            roles = {e["name"] for name in ("internal_agents.json", "external_agents.json") for e in entries(preset / name)}
            tags = public | {e["tag"] for e in entries(preset / "oasis_experts.json")}
            for workflow in sorted((preset / "oasis" / "yaml").glob("*.yaml")):
                with self.subTest(workflow=f"{preset.name}/{workflow.name}"):
                    names = extract_expert_names(parse_schedule(workflow.read_text(encoding="utf-8")))
                    unknown = [n for n in names
                               if (n.startswith("agent:") and n[len("agent:"):] not in roles)
                               or (n.startswith("persona:") and n.split(":")[1] not in tags)]
                    self.assertEqual(unknown, [])

    def test_list_and_install_team_preset(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            preset_root = root / "assets"
            preset_dir = preset_root / "modern-ceo"
            workflow_dir = preset_dir / "oasis" / "yaml"
            workflow_dir.mkdir(parents=True, exist_ok=True)
            (preset_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "preset_id": "modern-ceo",
                        "name": "现代企业制",
                        "default_team_name": "现代企业制",
                        "role_count": 2,
                        "workflow_files": ["modern.yaml"],
                        "tags": ["enterprise"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (preset_dir / "internal_agents.json").write_text(
                json.dumps(
                    [
                        {"name": "CEO", "tag": "ceo"},
                        {"name": "CTO", "tag": "cto"},
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (preset_dir / "oasis_experts.json").write_text(
                json.dumps(
                    [
                        {"name": "CEO", "tag": "ceo", "persona": "lead", "temperature": 0.4},
                        {"name": "CTO", "tag": "cto", "persona": "ship", "temperature": 0.4},
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (preset_dir / "source_map.json").write_text(
                json.dumps({"source": "test"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (workflow_dir / "modern.yaml").write_text("version: 2\nrepeat: false\nplan: []\nedges: []\n", encoding="utf-8")

            original_preset_root = team_preset_assets.PRESET_ROOT
            team_preset_assets.PRESET_ROOT = preset_root
            try:
                listed = team_preset_assets.list_team_presets()
                self.assertEqual(len(listed), 1)
                self.assertEqual(listed[0]["preset_id"], "modern-ceo")

                agents = AgentStore(root / "agents.db")
                teams = TeamStore(agents, root / "user_files")
                result = team_preset_assets.install_team_preset(
                    user_id="alice",
                    team_name="Modern Ops",
                    preset_id="modern-ceo",
                    teams=teams,
                )
                self.assertEqual(result["team"], "Modern Ops")
                self.assertEqual(result["internal_agents"], 2)
                self.assertEqual(result["workflow_files"], ["modern.yaml"])

                team_dir = teams.folder("alice", "Modern Ops")
                members = teams.members("alice", "Modern Ops")
                self.assertEqual([m.role for m in members], ["CEO", "CTO"])
                self.assertTrue(all(m.agent.agent_id for m in members))
                self.assertFalse((team_dir / "internal_agents.json").exists())
                self.assertTrue((team_dir / "clawcross_preset_manifest.json").exists())
                self.assertTrue((team_dir / "oasis" / "yaml" / "modern.yaml").exists())
            finally:
                team_preset_assets.PRESET_ROOT = original_preset_root
