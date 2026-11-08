"""Exercise the installed command contract against a local protocol fixture."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
ENTRY = os.environ.get("CLAWCROSS_TEST_ENTRY", str(ROOT / "bin/clawcross.js"))
pytestmark = pytest.mark.skipif(not NODE, reason="Node is required for the npm entrypoint")


def env_for(home):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAWCROSS_", "PORT_")) and k not in {"INTERNAL_TOKEN", "CLAW_USER", "CLI_USER", "PUBLIC_DOMAIN"}}
    return {**env, "CLAWCROSS_HOME": str(home), "CLAWCROSS_USE_LEGACY_PATHS": "0", "CLAW_USER": "alice", "PYTHONDONTWRITEBYTECODE": "1"}


def call(args, home, cwd=ROOT, **kwargs):
    return subprocess.run([NODE, ENTRY, *args], env=env_for(home),
        cwd=cwd, text=True, encoding="utf-8", capture_output=True, timeout=20, **kwargs)


@pytest.fixture
def backend(tmp_path):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            body = {"data": [{"agent_id": "fixture", "name": "伙伴", "platform": "webot"}]} if self.path == "/v1/agents" else {"messages": []}
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            requests.append((self.path, body, self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if self.path.endswith("completions") else "application/json")
            self.end_headers()
            if self.path.endswith("/control"):
                self.wfile.write(b'{"cancelled":true}')
            elif body["messages"][0]["content"] == "error-case":
                self.wfile.write(b'data: {"error":{"message":"fixture API error"}}\n\n')
            else:
                self.wfile.write(b'data: {"choices":[],"usage":{"total_tokens":5}}\n\n')
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"CLI_OK"},"finish_reason":null}]}\n\n')
                self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    home = tmp_path / "home"; config = home / "config"; config.mkdir(parents=True)
    (config / ".env").write_text(f"INTERNAL_TOKEN='cli-fixture-token'\nPORT_AGENT='{server.server_port}'\n", encoding="utf-8")
    try:
        yield home, requests
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_metadata_does_not_bootstrap_or_create_user_data(tmp_path):
    home = tmp_path / "untouched"
    for args in (["--help"], ["--version"], []):
        result = call(args, home)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip()
    assert not home.exists()


def test_welcome_screen_does_not_require_backend_dependencies(tmp_path):
    program = '''
import sys
sys.modules['dotenv'] = None
from src.cli import clawcross as shell
assert shell._public_front_url().startswith('http://127.0.0.1:')
assert 'ClawCross' in '\\n'.join(shell._welcome_lines(shell._default_state()))
'''
    result = subprocess.run([sys.executable, "-c", program], env=env_for(tmp_path / "clean"),
        cwd=ROOT, text=True, encoding="utf-8", capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_shell_api_cli_and_gateway_cancel_share_the_packaged_entry(backend, tmp_path):
    home, requests = backend
    caller = tmp_path / "project with spaces"; caller.mkdir()
    for args in (["agents", "list"], ["-u", "alice", "agents", "list"], ["model", "list"]):
        result = call(args, home, caller)
        assert result.returncode == 0, result.stderr
    for platform in ("internal", "codex"):
        result = call(["run", "hello", "-u", "alice", "-s", "fixture", "-p", platform], home, caller)
        assert result.returncode == 0, result.stderr
        assert "CLI_OK" in result.stdout
        _, body, auth = requests[-1]
        assert body["cli_workspace"] == str(caller.resolve())
        assert body["messages"] == [{"role": "user", "content": "hello"}]
        assert auth == "Bearer cli-fixture-token:alice"
    result = call(["cancel", "-s", "fixture", "-u", "alice"], home, caller)
    assert result.returncode == 0, result.stderr
    assert requests[-1][:2] == ("/v1/agents/fixture/control", {"action": "cancel"})
    assert not (home / "venv").exists()


def test_stream_errors_are_not_reported_as_success_and_stdin_is_supported(backend):
    home, requests = backend
    error = call(["chat", "error-case", "-s", "fixture"], home)
    assert error.returncode == 1
    assert "fixture API error" in error.stderr
    success = call(["chat", "-", "-s", "fixture"], home, input="来自 stdin：你好\nsecond line")
    assert success.returncode == 0, success.stderr
    assert "CLI_OK" in success.stdout
    assert requests[-1][1]['messages'] == [{'role':'user','content':'来自 stdin：你好\nsecond line'}]


@pytest.mark.parametrize('message', ['中文伙伴 😀', 'with spaces', 'a"quoted" value', "$env:NAME; $(echo unsafe) & <x>", 'line one\nline two'])
def test_cli_arguments_reach_python_as_literal_values(backend, message):
    home, requests = backend
    result = call(['chat', message, '-s', 'fixture'], home)
    assert result.returncode == 0, result.stderr
    assert requests[-1][1]['messages'] == [{'role':'user','content':message}]


def test_interactive_turns_keep_agent_and_apply_mode_changes(backend):
    home, _ = backend
    program = '''
import sys
from src.cli import clawcross as shell
lines = iter(['first', '/mode readonly', 'second', '/exit'])
shell._read_interactive_line = lambda _: next(lines)
shell.print_welcome = lambda _: None
shell._replay_current_session_history = lambda _: None
sys.stdin.isatty = lambda: True
seen = []
shell._stream_turn = lambda payload, state, **kw: seen.append(payload)
assert shell.main(['-u','alice','-s','fixture']) == 0
assert [p['messages'][0]['content'] for p in seen] == ['first','second']
assert all(p['session_id'] == 'fixture' for p in seen)
assert seen[1]['session_mode'] == 'readonly'
'''
    result = subprocess.run([sys.executable, "-c", program], env=env_for(home), cwd=ROOT,
        text=True, encoding="utf-8", capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_terminal_keep_y_resumes_original_call_and_does_not_override_auto_review(backend):
    home, _ = backend
    program = '''
import builtins, json, sys, threading
from src.cli import clawcross as shell
sys.stdin.isatty = lambda: True
sys.stdout.isatty = lambda: True
builtins.input = lambda _: 'KEEP Y'
gate = threading.Event()
resolutions = []
def lines(*args, **kw):
 yield 'data: '+json.dumps({'choices':[{'delta':{'meta':{'type':'tool_start','name':'read_file'}}}]})
 assert gate.wait(5)
 yield 'data: '+json.dumps({'choices':[{'delta':{'content':'APPROVED'},'finish_reason':'stop'}]})
 yield 'data: [DONE]'
def request(method,url,**kw):
 if method == 'GET':
  return {'approvals':[
   {'approval_id':'auto','review':{'reviewer':'auto_review'}},
   {'approval_id':'human','tool_name':'read_file','args':{},'review':{}},
  ]}
 resolutions.append(kw['data']); gate.set(); return {'status':'success'}
shell._post_stream = lines
shell._request_json = request
state = shell._default_state()
shell._stream_turn({'session_id':'fixture'}, state)
assert len(resolutions) == 1
assert resolutions[0]['approval_id'] == 'human'
assert resolutions[0]['remember'] is True
assert resolutions[0]['session_id'] == 'fixture'
'''
    result = subprocess.run([sys.executable, "-c", program], env=env_for(home), cwd=ROOT,
        text=True, encoding="utf-8", capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "APPROVED" in result.stdout
