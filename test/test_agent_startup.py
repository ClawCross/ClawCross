"""Exercise actual MCP conversion and the service lifespan with isolated data."""
import asyncio
from functools import wraps
from inspect import signature
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'backend'))


def test_builtin_tool_loading_preserves_the_installed_adapters_error_policy():
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from webot.engine import builtin_tools
    converter = builtin_tools.convert_mcp_tool_to_langchain_tool
    supports_errors = 'handle_tool_errors' in signature(converter).parameters
    connection = {'command':sys.executable,'args':['filemanager.py'],'transport':'stdio','env':{'INSTANCE':'isolated'}}
    client = MultiServerMCPClient({'files':connection}, **({'handle_tool_errors':False} if supports_errors else {}))
    calls = []

    @wraps(converter)
    def convert(*args, **kwargs):
        calls.append(kwargs)
        return converter(*args, **kwargs)

    with patch.object(builtin_tools,'convert_mcp_tool_to_langchain_tool',convert):
        tools = asyncio.run(builtin_tools.load_builtin_tools(client))
    assert tools and len(tools) == len(calls)
    assert all(call['connection'] is connection and call['callbacks'] is client.callbacks for call in calls)
    assert all(call.get('handle_tool_errors') is False if supports_errors else 'handle_tool_errors' not in call for call in calls)


def test_agent_service_reaches_health_without_models_or_existing_user_data(tmp_path):
    # A real Uvicorn process must finish server.py's entire lifespan, including
    # TeamAgent.startup and conversion of every bundled MCP tool.
    env = {key:value for key,value in os.environ.items()
           if not key.startswith(('CLAWCROSS_','PORT_','LLM_','OPENAI_','ANTHROPIC_','GOOGLE_'))
           and key not in {'INTERNAL_TOKEN','PUBLIC_DOMAIN','CLAW_USER','CLI_USER'}}
    env.update(CLAWCROSS_HOME=str(tmp_path/'启动 test home'),CLAWCROSS_USE_LEGACY_PATHS='0',
               INTERNAL_TOKEN='startup-test-only',PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    env['PYTHONPATH'] = str(ROOT/'src'/'backend') + (os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    env['PORT_AGENT']=str(port)
    program = '''
from unittest.mock import patch
import os,uvicorn
with patch('common.llm_factory.create_chat_model',side_effect=AssertionError('startup must not call a model')), \\
     patch('external.acp_direct.DirectAdapter',side_effect=AssertionError('startup must not launch a native Agent')):
    import server
    uvicorn.run(server.app,host='127.0.0.1',port=int(os.environ['PORT_AGENT']),access_log=False)
'''
    log_path=tmp_path/'startup.log'
    with log_path.open('w',encoding='utf-8') as log:
        process=subprocess.Popen([sys.executable,'-u','-c',program],cwd=ROOT,env=env,
                                 stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                assert process.poll() is None, log_path.read_text(encoding='utf-8',errors='replace')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as response:
                        assert json.load(response)=={'status':'ok'}
                    return
                except (urllib.error.URLError,TimeoutError):
                    time.sleep(.1)
            raise AssertionError(log_path.read_text(encoding='utf-8',errors='replace'))
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=5)
