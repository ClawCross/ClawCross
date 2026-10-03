"""Descriptor-relative file tools for strict workspaces, including rename races."""

from contextvars import ContextVar
from functools import wraps
import os
from pathlib import Path
import secrets
import stat

_handles = ContextVar('confined_file_handles', default=None)


def confined_operation(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        handles = []
        token = _handles.set(handles)
        try:
            return await function(*args, **kwargs)
        finally:
            for fd in reversed(handles):
                os.close(fd)
            _handles.reset(token)
    return wrapped


class ConfinedPath(str):
    def __new__(cls, target, root, *, create_parents=False):
        if os.name != 'posix' or os.open not in os.supports_dir_fd or not hasattr(os, 'O_NOFOLLOW'):
            raise ValueError('当前平台不支持严格文件工具所需的安全目录句柄；未访问文件。')
        handles = _handles.get()
        if handles is None:
            raise ValueError('严格文件访问缺少受控执行范围。')
        root = Path(root).resolve()
        parts = Path(target).relative_to(root).parts
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(root, flags)
        handles.append(fd)
        for part in parts[:-1]:
            if create_parents:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            fd = os.open(part, flags, dir_fd=fd)
            handles.append(fd)
        value = super().__new__(cls, str(target))
        value.parent_fd, value.name = fd, parts[-1] if parts else '.'
        return value

    def stat(self):
        info = os.stat(self.name, dir_fd=self.parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('严格文件访问拒绝操作期间出现的符号链接。')
        return info

    def open(self, mode='r', **kwargs):
        if mode not in {'r', 'rb'}:
            raise ValueError('Strict writes require atomic replacement')
        fd = os.open(self.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.parent_fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise ValueError('严格文件工具只读取普通文件。')
        return os.fdopen(fd, mode, **kwargs)

    def entries(self):
        fd = os.open(self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent_fd)
        try:
            return [(name, os.stat(name, dir_fd=fd, follow_symlinks=False)) for name in sorted(os.listdir(fd))]
        finally:
            os.close(fd)

    def write_text(self, content, encoding):
        name = '.mcp-write-' + secrets.token_hex(16)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.parent_fd)
        try:
            with os.fdopen(fd, 'w', encoding=encoding) as handle:
                handle.write(content)
            os.replace(name, self.name, src_dir_fd=self.parent_fd, dst_dir_fd=self.parent_fd)
        finally:
            try:
                os.unlink(name, dir_fd=self.parent_fd)
            except FileNotFoundError:
                pass

    def unlink(self):
        os.unlink(self.name, dir_fd=self.parent_fd)


def file_open(path, mode='r', **kwargs):
    return path.open(mode, **kwargs) if isinstance(path, ConfinedPath) else open(path, mode, **kwargs)


def file_exists(path):
    if not isinstance(path, ConfinedPath):
        return os.path.exists(path)
    try:
        path.stat()
        return True
    except FileNotFoundError:
        return False


def file_isdir(path):
    return stat.S_ISDIR(path.stat().st_mode) if isinstance(path, ConfinedPath) else os.path.isdir(path)


def file_size(path):
    return path.stat().st_size if isinstance(path, ConfinedPath) else os.path.getsize(path)
