"""Dependencies point down only: composition (teams, group chat, OASIS) → conversations →
agent layer → transports. Above the agent layer an agent is an id: which runtime it lives
in (WeBot, codex, OpenClaw, …) is known only to the agent layer and the team file format."""

import ast
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# What the agent layer (src/agents) must never import: the layers built on it.
_ABOVE_L1 = (
    "api.group_", "api.ops_", "api.openai_", "api.session_", "api.system_",
    "routes", "mcp_servers", "core.agent", "comms", "teams",
    "oasis.engine", "oasis.server", "oasis.forum", "oasis.scheduler", "oasis.swarm_engine",
)

# What the communication layer (src/comms) must never import: the products built on it.
_ABOVE_L2 = ("api.", "routes", "mcp_servers", "teams", "groups", "oasis", "core.agent")

# The runtime an agent lives in: driver names and the driver's own config.
_DRIVER_NAMES = {"WEBOT", "ACPX", "OPENCLAW", "HTTP", "LLM", "DRIVERS", "runtime_key", "driver_for_platform"}
_DRIVER_ATTRS = {"driver", "config"}

# Talking to a transport directly instead of through agents.gateway (only the runtimes do).
_TRANSPORT = ("integrations.agent_sender", "integrations.registry", "integrations.connectors")

# The runtimes, which live in the Agent service only; other processes (OASIS, the
# scheduler, the web front, the CLI) reach agents over its entrances (agents.client).
_RUNTIMES = ("agents.gateway", "external", "webot.driver")
_OTHER_PROCESSES = ("oasis", "scripts", "chatbot", "visual", "clawcross_cli")
_OTHER_PROCESS_FILES = ("src/utils/scheduler_service.py", "src/front.py")

# Modules that still call transports directly. This list may only shrink as
# callers move onto the gateway; a new direct caller fails the test.
_LEGACY_TRANSPORT_CALLERS: set[str] = set()


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return {name.removeprefix("src.") for name in names}


def _python_files(*roots: str) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        files.extend(p for p in (PROJECT_ROOT / root).rglob("*.py") if "__pycache__" not in p.parts)
    return files


class TestLayering(unittest.TestCase):
    def test_agent_layer_does_not_import_the_layers_above_it(self):
        for path in [*_python_files("src/agents", "src/external"), PROJECT_ROOT / "src/webot/driver.py"]:
            with self.subTest(path=str(path.relative_to(PROJECT_ROOT))):
                bad = sorted(name for name in _imports(path) if name.startswith(_ABOVE_L1))
                self.assertEqual(bad, [])

    def test_comms_layer_does_not_import_the_products_above_it(self):
        for path in _python_files("src/comms"):
            with self.subTest(path=str(path.relative_to(PROJECT_ROOT))):
                bad = sorted(name for name in _imports(path) if name.startswith(_ABOVE_L2))
                self.assertEqual(bad, [])

    def test_above_the_agent_layer_nobody_knows_an_agents_runtime(self):
        paths = _python_files("src/comms", "src/groups", "src/teams", "oasis")
        for path in paths:
            rel = str(path.relative_to(PROJECT_ROOT))
            if rel == "src/teams/manifest.py":  # the team file format names runtimes
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            with self.subTest(path=rel):
                imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
                read = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
                self.assertEqual(sorted(imported & _DRIVER_NAMES), [])
                self.assertEqual(sorted(read & _DRIVER_ATTRS), [])

    def test_only_the_agent_service_holds_runtimes(self):
        paths = [*_python_files(*_OTHER_PROCESSES), *(PROJECT_ROOT / f for f in _OTHER_PROCESS_FILES)]
        for path in paths:
            with self.subTest(path=str(path.relative_to(PROJECT_ROOT))):
                bad = sorted(name for name in _imports(path) if name.startswith(_RUNTIMES))
                self.assertEqual(bad, [], "use agents.client")

    def test_no_new_direct_transport_callers(self):
        callers = set()
        for path in _python_files("src", "oasis", "scripts", "clawcross_cli", "chatbot", "visual"):
            rel = str(path.relative_to(PROJECT_ROOT))
            if rel.startswith(("src/integrations/", "src/agents/", "src/external/")) or rel == "src/webot/driver.py":
                continue
            if any(name.startswith(_TRANSPORT) for name in _imports(path)):
                callers.add(rel)
        self.assertEqual(sorted(callers - _LEGACY_TRANSPORT_CALLERS), [], "use agents.gateway instead")


if __name__ == "__main__":
    unittest.main()
