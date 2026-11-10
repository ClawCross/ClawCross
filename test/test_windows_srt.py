import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from webot import command_sandbox as sandbox


def install_fixture(tmp_path, version='0.0.78'):
    package = tmp_path / 'node/node_modules/@anthropic-ai/sandbox-runtime'
    (package / 'dist').mkdir(parents=True)
    (package / 'dist/index.js').write_text('')
    (package / 'package.json').write_text(json.dumps({'name': '@anthropic-ai/sandbox-runtime', 'version': version}))
    helper = package / 'vendor/srt-win/x64/srt-win.exe'
    helper.parent.mkdir(parents=True)
    helper.write_bytes(b'fake')
    shim = tmp_path / 'node/node_modules/.bin/srt.cmd'
    shim.parent.mkdir(parents=True)
    shim.write_text('shim must never be executed')
    return shim, package


def test_windows_runtime_resolves_local_npm_shim_without_executing_it(tmp_path):
    shim, package = install_fixture(tmp_path)
    with patch.object(sandbox.platform, 'machine', return_value='AMD64'), \
            patch.object(sandbox.shutil, 'which', return_value='node.exe'):
        assert sandbox._windows_srt_runtime(str(shim)) == ('node.exe', package / 'dist/index.js')


def test_windows_rejects_packages_without_native_support(tmp_path):
    shim, _ = install_fixture(tmp_path, '0.0.77')
    with pytest.raises(sandbox.SandboxUnavailable, match='0.0.78'):
        sandbox._windows_srt_runtime(str(shim))


def test_windows_command_uses_shared_policy_and_opaque_workload(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    controls = tmp_path / 'controls'; controls.mkdir()
    shim, _ = install_fixture(tmp_path)
    command = 'echo "中文 & spaces" & echo %PATH%'
    def private_file(prefix):
        fd, path = tempfile.mkstemp(prefix=prefix, suffix='.json', dir=controls)
        return fd, Path(path)
    with patch.object(sandbox.sys, 'platform', 'win32'), \
            patch.object(sandbox.platform, 'machine', return_value='AMD64'), \
            patch.object(sandbox.shutil, 'which', return_value='node.exe'), \
            patch.object(sandbox, '_srt_binary', return_value=str(shim)), \
            patch.object(sandbox, 'validate_workspace_root'), \
            patch.object(sandbox, 'protected_control_paths', return_value=[controls]), \
            patch.object(sandbox, '_command_settings_file', side_effect=private_file):
        call = sandbox.build_srt_command(root=workspace, cwd=workspace, command=command,
            language='shell', python_executable=sys.executable, allowed_domains=['example.com:443'])
    try:
        assert call.argv[0] == 'node.exe'
        assert call.argv[3] == 'run'
        assert command not in call.argv
        payload = json.loads(base64.b64decode(call.argv[-1]))
        assert payload['argv'][-1] == command
        assert payload['argv'][1:4] == ['/d', '/s', '/c']
        policy = json.loads(call.settings_path.read_text())
        assert policy['network']['allowedDomains'] == ['example.com:443']
        assert str(workspace) in policy['filesystem']['allowWrite']
        assert str(controls) in policy['filesystem']['denyWrite']
        assert not call.settings_path.is_relative_to(workspace)
    finally:
        call.settings_path.unlink()
        shutil.rmtree(call.temporary_dir)


@pytest.mark.parametrize('response', ['', '[]', '{"ready":true,"errors":"wrong"}'])
def test_status_failure_never_claims_ready_or_runs_install(response):
    with patch.object(sandbox, 'windows_srt_operation', return_value=('node', 'bridge', 'status')) as operation, \
            patch.object(sandbox.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, response, '')):
        result = sandbox.windows_srt_status()
    assert not result['ready']
    assert result['can_initialize']
    operation.assert_called_once_with('status')


@pytest.mark.skipif(not shutil.which('node'), reason='Node required for SRT bridge contract')
@pytest.mark.parametrize('fail_initialize', [False, True])
def test_bridge_initializes_before_spawn_and_always_resets(tmp_path, fail_initialize):
    entry = tmp_path / 'fake-srt.mjs'
    trace = tmp_path / 'trace.txt'
    entry.write_text('''import fs from 'node:fs';
const record=(value)=>fs.appendFileSync(%s,value+'\\n');
export const VENDORED_SRT_WIN_EXE='fake-native-helper';
export const resolveSrtWin=(value)=>value;
export const SandboxRuntimeConfigSchema={parse:value=>value};
export const SandboxManager={
 initialize:async()=>{record('initialize');if(%s)throw Error('not provisioned');},
 wrapWithSandboxArgv:async(payload,shell)=>{
  record('wrap');if(shell.args[0]!=='-I'||shell.args[1]!=='-c')throw Error('wrong entry');
  return {argv:[process.execPath,'-e',"process.stdout.write('sandbox-child-ok')"],env:{}};
 },
 reset:async()=>record('reset')
};
''' % (json.dumps(str(trace)), str(fail_initialize).lower()), encoding='utf-8')
    policy = tmp_path / 'policy.json'; policy.write_text('{}')
    bridge = Path(sandbox.__file__).with_name('windows_srt_bridge.mjs')
    limits = Path(sandbox.__file__).with_name('windows_resource_limits.py')
    result = subprocess.run([shutil.which('node'), str(bridge), str(entry), 'run', str(policy),
                             sys.executable, str(limits), 'opaque-data'], capture_output=True, text=True, timeout=10)
    assert trace.read_text().splitlines() == (['initialize', 'reset'] if fail_initialize else ['initialize', 'wrap', 'reset'])
    if fail_initialize:
        assert result.returncode == 125 and not result.stdout
        assert 'initialization failed' in result.stderr
    else:
        assert result.returncode == 0 and result.stdout == 'sandbox-child-ok'


@pytest.mark.skipif(sys.platform != 'win32' or os.getenv('CLAWCROSS_WINDOWS_SRT_INTEGRATION') != '1',
                    reason='Opt in on a Windows host with initialized SRT')
def test_real_windows_srt_workspace_and_outside_file(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    outside = tmp_path / 'outside.txt'; outside.write_text('SYNTHETIC PRIVATE DATA')
    script = workspace / 'check.py'
    script.write_text('import pathlib,sys\n'
        'pathlib.Path("inside.txt").write_text("workspace-ok")\n'
        f'try:\n pathlib.Path({str(outside)!r}).read_text()\n'
        'except PermissionError:\n print("OUTSIDE_DENIED")\n'
        'else:\n sys.exit(99)\n', encoding='utf-8')
    call = sandbox.build_srt_command(root=workspace, cwd=workspace, command='', language='python',
                                    python_executable=sys.executable, script_path=script, wall_timeout=30)
    try:
        result = subprocess.run(call.argv, cwd=workspace, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        assert 'OUTSIDE_DENIED' in result.stdout
        assert (workspace / 'inside.txt').read_text() == 'workspace-ok'
    finally:
        call.settings_path.unlink(missing_ok=True)
        shutil.rmtree(call.temporary_dir, ignore_errors=True)
