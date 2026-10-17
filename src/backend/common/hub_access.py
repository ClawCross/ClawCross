"""Private device credentials and the public, process-owned tunnel status."""
import json
import os
from pathlib import Path
import tempfile

from .runtime_paths import CONFIG_DIR, PID_DIR

CREDENTIALS = CONFIG_DIR / 'hub-tunnel.json'
STATUS = PID_DIR / 'hub-tunnel-status.json'


def read_json(path):
    try:
        value=json.loads(Path(path).read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(dir=path.parent,prefix='.'+path.name+'-')
    try:
        with os.fdopen(fd,'w') as stream:
            os.chmod(temporary,0o600)
            json.dump(value,stream,ensure_ascii=False)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def tunnel_state():
    state=read_json(STATUS)
    try:
        pid=int((PID_DIR/'tunnel.pid').read_text())
        if pid!=state.get('pid'):return {}
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=kernel.OpenProcess(0x1000,False,pid)
            if not handle:return {}
            try:
                code=wintypes.DWORD()
                if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value!=259:return {}
            finally:kernel.CloseHandle(handle)
        else:os.kill(pid,0)
    except (ValueError,OSError):return {}
    return state
