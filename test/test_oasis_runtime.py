"""Real OASIS HTTP/process smoke tests with isolated data and a local Agent reply stub.

No external model requests or live user data are involved.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from agents.store import AgentStore
from teams.manifest import import_entries
from teams.store import TeamStore

ROOT = Path(__file__).resolve().parents[1]


class _AgentStub(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.calls.append((self.path, self.headers.get("Authorization"), payload))
        agent_id = self.path.split("/")[-2]
        body = json.dumps({"ok": True, "content": f"AGENT_OK:{agent_id}", "error": ""}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class OasisRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="clawcross-oasis-runtime-")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.home = Path(cls.tmp.name)
        cls.stub = ThreadingHTTPServer(("127.0.0.1", 0), _AgentStub)
        cls.addClassCleanup(cls.stub.server_close)
        cls.addClassCleanup(cls.stub.shutdown)
        threading.Thread(target=cls.stub.serve_forever, daemon=True).start()
        _AgentStub.calls = []
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cls.env = {k: v for k, v in os.environ.items() if not k.startswith("CLAWCROSS_")}
        cls.env.update({
            "CLAWCROSS_HOME": str(cls.home), "CLAWCROSS_SERVER_HOST": "127.0.0.1",
            "CLAWCROSS_VENV_DIR": str(Path(sys.executable).parent.parent),
            "PORT_AGENT": str(cls.stub.server_port), "OASIS_BASE_URL": f"http://127.0.0.1:{port}",
            "INTERNAL_TOKEN": "runtime-test", "LLM_MODEL": "",
            "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT / "src/backend")]),
        })
        cls.agents = AgentStore(cls.home / "data/agents.db")
        cls.teams = TeamStore(cls.agents, cls.home / "data/user_files")
        import_entries(cls.teams, "tester", "runtime", [
            {"name": "Writer", "session": "writer", "persona": "write"},
            {"name": "Reviewer", "session": "reviewer", "persona": "review"},
        ], [])
        cls.log = (cls.home / "oasis.log").open("w+")
        cls.addClassCleanup(cls.log.close)
        cls.process = subprocess.Popen([
            sys.executable, "-m", "uvicorn", "oasis.server:app", "--host", "127.0.0.1",
            "--port", str(port), "--log-level", "warning",
        ], cwd=ROOT, env=cls.env, stdout=cls.log, stderr=subprocess.STDOUT)

        def stop():
            cls.process.terminate()
            try:
                cls.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=5)

        cls.addClassCleanup(stop)
        cls.client = httpx.Client(base_url=cls.env["OASIS_BASE_URL"], timeout=3)
        cls.addClassCleanup(cls.client.close)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                break
            try:
                if cls.client.get("/experts", params={"user_id": "tester"}).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        cls.log.flush()
        cls.log.seek(0)
        raise RuntimeError(f"OASIS startup failed: {cls.log.read()}")

    def detail(self, topic_id):
        response = self.client.get(f"/topics/{topic_id}", params={"user_id": "tester"})
        response.raise_for_status()
        return response.json()

    def wait(self, topic_id, predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            detail = self.detail(topic_id)
            if predicate(detail):
                return detail
            time.sleep(0.05)
        self.fail(f"OASIS did not reach expected state: {detail}")

    def test_saved_yaml_parallel_agents_and_human_resume(self):
        schedule = """version: 2
repeat: false
discussion: false
plan:
  - id: start
    manual:
      author: host
      content: RUNTIME_START
  - id: pair
    parallel:
      - agent: Writer
      - agent: Reviewer
  - id: confirm
    human:
      author: host
      prompt: continue?
  - id: finish
    manual:
      author: host
      content: RUNTIME_FINISHED
edges:
  - [start, pair]
  - [pair, confirm]
  - [confirm, finish]
"""
        saved = self.client.post("/workflows", json={
            "user_id": "tester", "team": "runtime", "name": "smoke", "schedule_yaml": schedule,
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        workflow = self.teams.folder("tester", "runtime") / "oasis/yaml/smoke.yaml"
        self.assertTrue(workflow.is_file())
        response = self.client.post("/topics", json={
            "question": "runtime test", "user_id": "tester", "team": "runtime",
            "schedule_file": str(workflow), "discussion": False, "max_rounds": 1,
        })
        self.assertEqual(response.status_code, 200, response.text)
        topic = response.json()["topic_id"]
        pending = self.wait(topic, lambda d: bool(d["pending_human"]))
        self.assertEqual(pending["status"], "discussing")
        content = [p["content"] for p in pending["posts"]]
        self.assertIn("AGENT_OK:writer", content)
        self.assertIn("AGENT_OK:reviewer", content)
        human = pending["pending_human"]
        replied = self.client.post(f"/topics/{topic}/human-reply", json={
            "user_id": "tester", "node_id": human["node_id"], "round_num": human["round_num"],
            "content": "HUMAN_CONTINUE",
        })
        self.assertEqual(replied.status_code, 200, replied.text)
        finished = self.wait(topic, lambda d: d["status"] in ("concluded", "error"))
        self.assertEqual(finished["status"], "concluded")
        self.assertIn("RUNTIME_FINISHED", finished["conclusion"])
        self.assertIn("HUMAN_CONTINUE", [p["content"] for p in finished["posts"]])
        self.assertTrue(all(auth == "Bearer runtime-test:tester" for _, auth, _ in _AgentStub.calls))

    def test_saved_python_workflow_agent_reply_and_forum_mirroring(self):
        workflow = self.teams.folder("tester", "runtime") / "oasis/python/python_smoke.py"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text("""from oasis.workflow import workflow
@workflow
async def main(ctx):
    reply = await ctx.send_agent('Writer', 'PYTHON_AGENT_REQUEST')
    if not reply.ok:
        raise RuntimeError(reply.error)
    await ctx.publish(reply.content, author='workflowpy')
    ctx.set_conclusion('PYTHON_WORKFLOW_OK')
    ctx.set_result({'reply': reply.content, 'team': ctx.team})
""")
        result_file = self.home / "python-result.json"
        process = subprocess.run([
            sys.executable, str(ROOT / "src/backend/oasis/run_python_workflow.py"), "python_smoke",
            "--user-id", "tester", "--team", "runtime", "--question", "python test",
            "--result-file", str(result_file),
        ], cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        result = json.loads(result_file.read_text())
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"], {"reply": "AGENT_OK:writer", "team": "runtime"})
        detail = self.detail(result["topic_id"])
        self.assertEqual(detail["status"], "concluded")
        self.assertEqual(detail["conclusion"], "PYTHON_WORKFLOW_OK")
        self.assertIn("AGENT_OK:writer", [p["content"] for p in detail["posts"]])
