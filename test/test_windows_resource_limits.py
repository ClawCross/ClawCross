import ctypes
import json
import os
import subprocess
import sys

import pytest

from webot import windows_resource_limits as limits


class Kernel:
    def __init__(self, failure='', wait=0):
        self.failure, self.wait, self.calls = failure, wait, []

    def __getattr__(self, name):
        def call(*args):
            self.calls.append(name)
            if name == self.failure:
                return 0
            if name == 'CreateJobObjectW': return 10
            if name == 'GetStdHandle': return 20
            if name == 'CreateProcessW':
                assert args[5] & 4  # no workload runs before assignment
                process = ctypes.cast(args[-1], ctypes.POINTER(limits.ProcessInfo)).contents
                process.hProcess, process.hThread = 30, 40
            if name == 'ResumeThread': return 1
            if name == 'WaitForSingleObject': return self.wait
            if name == 'GetExitCodeProcess':
                ctypes.cast(args[1], ctypes.POINTER(limits.DWORD)).contents.value = 7
            return 1
        return call


def test_job_resource_limits_cover_descendants_and_cannot_break_away():
    policy = limits.job_limits()
    assert policy.JobMemoryLimit == policy.ProcessMemoryLimit == 2 * 1024**3
    assert policy.BasicLimitInformation.PerJobUserTimeLimit == 120 * 10_000_000
    assert policy.BasicLimitInformation.ActiveProcessLimit == 128
    assert policy.BasicLimitInformation.LimitFlags & 0x2000
    assert not policy.BasicLimitInformation.LimitFlags & (0x800 | 0x1000)
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(limits.ExtendedLimits) == 144
        assert ctypes.sizeof(limits.StartupInfo) == 104


@pytest.mark.parametrize('failure', ['SetInformationJobObject', 'AssignProcessToJobObject'])
def test_workload_is_never_resumed_if_limits_cannot_be_enforced(failure):
    kernel = Kernel(failure=failure)
    with pytest.raises(OSError):
        limits.run_limited([sys.executable, '-c', 'print("must not run")'], 3, kernel=kernel)
    assert 'ResumeThread' not in kernel.calls
    assert 'CloseHandle' in kernel.calls
    if failure == 'SetInformationJobObject':
        assert 'CreateProcessW' not in kernel.calls
    else:
        assert 'TerminateProcess' in kernel.calls


def test_assignment_precedes_execution_and_handles_are_closed():
    kernel = Kernel()
    assert limits.run_limited([sys.executable, '-c', 'pass'], 3, kernel=kernel) == 7
    assert kernel.calls.index('AssignProcessToJobObject') < kernel.calls.index('ResumeThread')
    assert kernel.calls.count('CloseHandle') == 3


def test_wall_timeout_terminates_the_whole_job():
    kernel = Kernel(wait=258)
    assert limits.run_limited([sys.executable, '-c', 'pass'], 1, kernel=kernel) == 124
    assert 'TerminateJobObject' in kernel.calls


@pytest.mark.skipif(os.name != 'nt', reason='Windows kernel integration')
def test_native_argument_passing_and_exit_code():
    args = ['中文 空格', 'quote"value', 'literal&value']
    code = 'import sys,json;print(json.dumps(sys.argv[1:]));sys.exit(7)'
    import base64
    payload = base64.b64encode(json.dumps({'argv': [sys.executable, '-c', code, *args], 'timeout': 5}).encode()).decode()
    result = subprocess.run([sys.executable, limits.__file__, payload], capture_output=True, text=True, timeout=10)
    assert result.returncode == 7, result.stderr
    assert json.loads(result.stdout) == args


@pytest.mark.skipif(os.name != 'nt', reason='Windows kernel integration')
def test_native_job_memory_limit(monkeypatch):
    original = limits.job_limits
    def smaller():
        policy = original()
        policy.ProcessMemoryLimit = policy.JobMemoryLimit = 128 * 1024**2
        return policy
    monkeypatch.setattr(limits, 'job_limits', smaller)
    assert limits.run_limited([sys.executable, '-c', 'x=bytearray(512*1024**2)'], 5) != 0


@pytest.mark.skipif(os.name != 'nt', reason='Windows kernel integration')
def test_native_timeout_and_child_cleanup(tmp_path):
    marker = tmp_path / 'child.pid'
    child_code = 'import time;time.sleep(60)'
    code = ('import subprocess,sys,pathlib,time;'
            f'p=subprocess.Popen([sys.executable,"-c",{child_code!r}]);'
            f'pathlib.Path({str(marker)!r}).write_text(str(p.pid));time.sleep(60)')
    assert limits.run_limited([sys.executable, '-c', code], 1) == 124
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, int(marker.read_text()))
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle, 5000) == 0
        finally:
            kernel.CloseHandle(handle)
