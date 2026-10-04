"""Run *inside* SRT: limit Windows workloads with a nested Job Object.

Uses only the standard library. The child starts suspended and cannot execute
before job assignment. A non-inheritable job handle kills descendants when the
supervisor exits, including when the surrounding SRT process is terminated.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import shutil
import subprocess
import sys

DWORD = ctypes.c_uint32
HANDLE = ctypes.c_void_p
SIZE_T = ctypes.c_size_t
LARGE_INTEGER = ctypes.c_int64


class BasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', LARGE_INTEGER), ('PerJobUserTimeLimit', LARGE_INTEGER),
                ('LimitFlags', DWORD), ('MinimumWorkingSetSize', SIZE_T),
                ('MaximumWorkingSetSize', SIZE_T), ('ActiveProcessLimit', DWORD),
                ('Affinity', SIZE_T), ('PriorityClass', DWORD), ('SchedulingClass', DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
        'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IoCounters),
                ('ProcessMemoryLimit', SIZE_T), ('JobMemoryLimit', SIZE_T),
                ('PeakProcessMemoryUsed', SIZE_T), ('PeakJobMemoryUsed', SIZE_T)]


class StartupInfo(ctypes.Structure):
    _fields_ = [('cb', DWORD), ('lpReserved', ctypes.c_wchar_p), ('lpDesktop', ctypes.c_wchar_p),
                ('lpTitle', ctypes.c_wchar_p), *[(name, DWORD) for name in (
                    'dwX', 'dwY', 'dwXSize', 'dwYSize', 'dwXCountChars', 'dwYCountChars',
                    'dwFillAttribute', 'dwFlags')], ('wShowWindow', ctypes.c_uint16),
                ('cbReserved2', ctypes.c_uint16), ('lpReserved2', ctypes.c_void_p),
                ('hStdInput', HANDLE), ('hStdOutput', HANDLE), ('hStdError', HANDLE)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [('hProcess', HANDLE), ('hThread', HANDLE), ('dwProcessId', DWORD), ('dwThreadId', DWORD)]


def job_limits() -> ExtendedLimits:
    limits = ExtendedLimits()
    # PROCESS_TIME | JOB_TIME | ACTIVE_PROCESS | PROCESS_MEMORY | JOB_MEMORY | KILL_ON_JOB_CLOSE
    limits.BasicLimitInformation.LimitFlags = 0x2 | 0x4 | 0x8 | 0x100 | 0x200 | 0x2000
    limits.BasicLimitInformation.PerProcessUserTimeLimit = 120 * 10_000_000
    limits.BasicLimitInformation.PerJobUserTimeLimit = 120 * 10_000_000
    limits.BasicLimitInformation.ActiveProcessLimit = 128
    limits.ProcessMemoryLimit = limits.JobMemoryLimit = 2 * 1024**3
    return limits


def _kernel_api():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateJobObjectW': (HANDLE, [ctypes.c_void_p, ctypes.c_wchar_p]),
        'SetInformationJobObject': (DWORD, [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD]),
        'GetStdHandle': (HANDLE, [DWORD]),
        'SetHandleInformation': (DWORD, [HANDLE, DWORD, DWORD]),
        'CreateProcessW': (DWORD, [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p,
            ctypes.c_void_p, DWORD, DWORD, ctypes.c_void_p, ctypes.c_wchar_p,
            ctypes.POINTER(StartupInfo), ctypes.POINTER(ProcessInfo)]),
        'AssignProcessToJobObject': (DWORD, [HANDLE, HANDLE]),
        'ResumeThread': (DWORD, [HANDLE]),
        'WaitForSingleObject': (DWORD, [HANDLE, DWORD]),
        'GetExitCodeProcess': (DWORD, [HANDLE, ctypes.POINTER(DWORD)]),
        'TerminateJobObject': (DWORD, [HANDLE, DWORD]),
        'TerminateProcess': (DWORD, [HANDLE, DWORD]),
        'CloseHandle': (DWORD, [HANDLE]),
    }
    for name, (restype, argtypes) in signatures.items():
        function = getattr(kernel, name)
        function.restype, function.argtypes = restype, argtypes
    return kernel


def run_limited(argv: list[str], timeout: int, *, kernel=None) -> int:
    if not argv or not 1 <= timeout <= 86400:
        raise ValueError('Invalid Windows sandbox workload')
    kernel = kernel if kernel is not None else _kernel_api()
    def check(value, operation):
        if not value:
            code = ctypes.get_last_error() if hasattr(ctypes, 'get_last_error') else 0
            raise OSError(code, operation)
        return value
    job = check(kernel.CreateJobObjectW(None, None), 'CreateJobObjectW')
    process = ProcessInfo()
    resumed = False
    null_fds = []
    try:
        limits = job_limits()
        check(kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)),
              'SetInformationJobObject')
        startup = StartupInfo(cb=ctypes.sizeof(StartupInfo), dwFlags=0x100)
        for field, number in (('hStdInput', -10), ('hStdOutput', -11), ('hStdError', -12)):
            handle = kernel.GetStdHandle(number & 0xffffffff)
            if not handle or handle == ctypes.c_void_p(-1).value:
                import msvcrt
                fd = os.open(os.devnull, os.O_RDWR)
                null_fds.append(fd)
                handle = msvcrt.get_osfhandle(fd)
            check(kernel.SetHandleInformation(handle, 1, 1), 'SetHandleInformation')
            setattr(startup, field, handle)
        executable = shutil.which(argv[0]) or argv[0]
        line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        # CREATE_SUSPENDED; inherit only the sandbox supervisor's environment.
        check(kernel.CreateProcessW(executable, line, None, None, True, 0x4, None, None,
                                   ctypes.byref(startup), ctypes.byref(process)), 'CreateProcessW')
        check(kernel.AssignProcessToJobObject(job, process.hProcess), 'AssignProcessToJobObject')
        if kernel.ResumeThread(process.hThread) == 0xffffffff:
            check(False, 'ResumeThread')
        resumed = True
        result = kernel.WaitForSingleObject(process.hProcess, timeout * 1000)
        if result == 258:  # WAIT_TIMEOUT
            check(kernel.TerminateJobObject(job, 124), 'TerminateJobObject')
            kernel.WaitForSingleObject(process.hProcess, 5000)
            print('ClawCross Windows workload timed out; process tree terminated.', file=sys.stderr)
            return 124
        if result != 0:
            check(False, 'WaitForSingleObject')
        code = DWORD()
        check(kernel.GetExitCodeProcess(process.hProcess, ctypes.byref(code)), 'GetExitCodeProcess')
        return code.value
    finally:
        if process.hProcess and not resumed:
            kernel.TerminateProcess(process.hProcess, 125)
            kernel.WaitForSingleObject(process.hProcess, 5000)
        # Closing the sole job handle also terminates any surviving children.
        kernel.CloseHandle(job)
        for handle in (process.hThread, process.hProcess):
            if handle:
                kernel.CloseHandle(handle)
        for fd in null_fds:
            os.close(fd)


def main() -> int:
    try:
        payload = json.loads(base64.b64decode(sys.argv[1], validate=True))
        return run_limited(payload['argv'], int(payload['timeout']))
    except Exception as exc:
        print(f'ClawCross Windows resource initialization failed: {exc}', file=sys.stderr)
        return 125


if __name__ == '__main__':
    raise SystemExit(main())
