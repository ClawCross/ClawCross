"""Strict file tools on Windows: pinned ancestors and no reparse traversal.

Open every ancestor without FILE_SHARE_DELETE so paths cannot be renamed or
replaced while a tool operates. Open leaves with OPEN_REPARSE_POINT and check
the handle before reading; writes replace a fresh file instead of following
an existing link. No fallback to ordinary path access on API failures.
"""
import ctypes
import os
from pathlib import Path, PureWindowsPath
import secrets

from webot.confined_files import ConfinedPath, _handles

DWORD = ctypes.c_uint32
HANDLE = ctypes.c_void_p


class FileTime(ctypes.Structure):
    _fields_ = [('low', DWORD), ('high', DWORD)]


class FileInfo(ctypes.Structure):
    _fields_ = [('attributes', DWORD), ('created', FileTime),
                ('accessed', FileTime), ('written', FileTime),
                ('volume', DWORD), ('size_high', DWORD), ('size_low', DWORD),
                ('links', DWORD), ('index_high', DWORD), ('index_low', DWORD)]


def kernel():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [ctypes.c_wchar_p, DWORD, DWORD, ctypes.c_void_p, DWORD, DWORD, HANDLE]
    api.CreateFileW.restype = HANDLE
    api.GetFileInformationByHandle.argtypes = [HANDLE, ctypes.POINTER(FileInfo)]
    api.GetFileInformationByHandle.restype = ctypes.c_int32
    api.CloseHandle.argtypes = [HANDLE]
    api.CloseHandle.restype = ctypes.c_int32
    return api


def validate_components(path):
    parsed = PureWindowsPath(path)
    if parsed.anchor.startswith('\\\\'):
        raise ValueError('严格文件工具只访问本地工作区，不访问 UNC 网络共享。')
    parts = parsed.parts
    for part in parts[1:] if parsed.anchor else parts:
        if (part in {'.', '..'} or ':' in part or part.rstrip(' .') != part
                or PureWindowsPath(part).is_reserved()):
            raise ValueError('严格文件工具拒绝 Windows 设备名、流或非规范路径。')


class PinnedHandle:
    def __init__(self, api, value):
        self.api, self.value = api, value

    def close(self):
        if self.value is not None:
            self.api.CloseHandle(self.value)
            self.value = None


def open_checked(api, path, *, read=False, directory=False):
    # BACKUP_SEMANTICS opens directory handles; OPEN_REPARSE_POINT prevents
    # traversal at the final component. Pinned parents protect intermediates.
    # Attribute-only handles do not participate in Windows sharing checks.
    # FILE_READ_DATA / FILE_LIST_DIRECTORY makes denying SHARE_DELETE pin
    # the file or directory name until this handle closes.
    value = api.CreateFileW(str(path), 0x80000000 if read else 0x80 | 0x1,
                           0x1 | 0x2, None, 3, 0x02000000 | 0x00200000, None)
    if value is None or value == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    handle = PinnedHandle(api, value)
    info = FileInfo()
    try:
        if not api.GetFileInformationByHandle(value, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400:
            raise ValueError('严格文件工具拒绝 Windows reparse point 或 junction。')
        if directory and not info.attributes & 0x10:
            raise ValueError('严格文件访问的父路径不是目录。')
        if read and (info.attributes & 0x10 or info.links != 1):
            raise ValueError('严格文件工具只读取无硬链接的普通文件。')
        return handle
    except BaseException:
        handle.close()
        raise


class WindowsConfinedPath(ConfinedPath):
    def __new__(cls, target, root, *, create_parents=False):
        handles = _handles.get()
        if handles is None:
            raise ValueError('严格文件访问缺少受控执行范围。')
        # Do not resolve here: opening each original component must detect a
        # junction introduced after the runtime resolved the approval target.
        root, target = Path(root).absolute(), Path(target).absolute()
        relative = target.relative_to(root)
        validate_components(root)
        validate_components(target)
        api = kernel()
        current = Path(root.anchor)
        handles.append(open_checked(api, current, directory=True))
        for part in root.parts[1:]:
            current /= part
            handles.append(open_checked(api, current, directory=True))
        for part in relative.parts[:-1]:
            current /= part
            if create_parents:
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    pass
            handles.append(open_checked(api, current, directory=True))
        value = str.__new__(cls, str(target))
        value.api = api
        return value

    def stat(self):
        handle = open_checked(self.api, self)
        try:
            return os.stat(self, follow_symlinks=False)
        finally:
            handle.close()

    def open(self, mode='r', **kwargs):
        import msvcrt
        if mode not in {'r', 'rb'}:
            raise ValueError('Strict writes require atomic replacement')
        handle = open_checked(self.api, self, read=True)
        try:
            fd = msvcrt.open_osfhandle(handle.value, os.O_RDONLY | (os.O_BINARY if mode == 'rb' else os.O_TEXT))
            handle.value = None  # CRT owns the HANDLE from this point.
        finally:
            handle.close()
        try:
            return os.fdopen(fd, mode, **kwargs)
        except BaseException:
            os.close(fd)
            raise

    def entries(self):
        handle = open_checked(self.api, self, directory=True)
        try:
            with os.scandir(self) as entries:
                return sorted((entry.name, entry.stat(follow_symlinks=False)) for entry in entries)
        finally:
            handle.close()

    def write_text(self, content, encoding):
        temporary = Path(self).parent / ('.mcp-write-' + secrets.token_hex(16))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding=encoding) as handle:
                handle.write(content)
            os.replace(temporary, self)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def unlink(self):
        os.unlink(self)
