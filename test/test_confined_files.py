"""File-tool boundaries without requiring MCP or backend dependencies."""
import asyncio
import ctypes
import os
from pathlib import Path
import subprocess

import pytest

from webot.confined_files import ConfinedPath, confined_operation
from webot.windows_confined_files import FileInfo, open_checked, validate_components


def test_native_strict_read_write_list_and_delete(tmp_path):
    root = tmp_path / 'workspace'; root.mkdir()
    @confined_operation
    async def operate():
        path = ConfinedPath(root / 'nested' / 'note.txt', root, create_parents=True)
        path.write_text('你好\nCONTENT', 'utf-8')
        with path.open(encoding='utf-8') as handle:
            assert handle.read() == '你好\nCONTENT'
        entries = ConfinedPath(root / 'nested', root).entries()
        assert [name for name, info in entries] == ['note.txt']
        path.unlink()
    asyncio.run(operate())
    assert not (root / 'nested' / 'note.txt').exists()


def test_native_hardlink_to_outside_is_not_read(tmp_path):
    root = tmp_path / 'workspace'; root.mkdir()
    outside = tmp_path / 'outside.txt'; outside.write_text('SYNTHETIC_SECRET')
    os.link(outside, root / 'linked.txt')
    @confined_operation
    async def read():
        with ConfinedPath(root / 'linked.txt', root).open() as handle:
            return handle.read()
    with pytest.raises(ValueError, match='硬链接'):
        asyncio.run(read())


def test_native_symlink_or_junction_parent_does_not_escape(tmp_path):
    root = tmp_path / 'workspace'; root.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    (outside / 'secret.txt').write_text('SYNTHETIC_SECRET')
    alias = root / 'alias'
    if os.name == 'nt':
        # Directory junctions require no symlink developer privilege.
        subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(alias), str(outside)],
                       check=True, capture_output=True, timeout=5)
    else:
        alias.symlink_to(outside, target_is_directory=True)
    @confined_operation
    async def read():
        with ConfinedPath(alias / 'secret.txt', root).open() as handle:
            return handle.read()
    with pytest.raises((ValueError, OSError)):
        asyncio.run(read())


@pytest.mark.skipif(os.name != 'nt', reason='Windows directory sharing contract')
def test_native_windows_pins_ancestors_until_tool_finishes(tmp_path):
    root = tmp_path / 'workspace'; root.mkdir()
    nested = root / 'nested'; nested.mkdir()
    @confined_operation
    async def operate():
        ConfinedPath(nested / 'new.txt', root)
        with pytest.raises(PermissionError):
            nested.rename(root / 'moved')
    asyncio.run(operate())
    nested.rename(root / 'moved')  # Handles were actually released.


@pytest.mark.parametrize('path', [r'C:\work\NUL', r'C:\work\CON.txt',
    r'C:\work\file:stream', r'C:\work\ending.', r'C:\work\ending ', r'\\server\share\work', 'NUL', 'file:stream'])
def test_windows_path_aliases_devices_and_network_shares_are_rejected(path):
    with pytest.raises(ValueError):
        validate_components(path)


@pytest.mark.skipif(os.name != 'nt', reason='Windows approval path binding')
def test_native_windows_approval_binding_does_not_follow_junction(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from webot.approval_actions import bind_file_target
    root = tmp_path / 'workspace'; root.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    alias = root / 'alias'
    subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(alias), str(outside)],
                   check=True, capture_output=True, timeout=5)
    monkeypatch.setitem(sys.modules, 'webot.runtime_settings', SimpleNamespace(
        get_runtime_settings=lambda *args: SimpleNamespace(approval=SimpleNamespace(sandbox_security='strict'))))
    bound = bind_file_target('read_file', {'filename': 'alias/secret.txt'}, 'alice', 's',
                             workspace=SimpleNamespace(root=root, cwd=root))
    assert bound['_resolved_path'] == str(alias / 'secret.txt')
    with pytest.raises(ValueError, match='网络共享'):
        bind_file_target('read_file', {'filename': r'\\example.invalid\share\secret.txt'}, 'alice', 's',
                         workspace=SimpleNamespace(root=root, cwd=root))


class FakeKernel:
    def __init__(self, attributes=0, links=1):
        self.attributes, self.links = attributes, links
        self.closed = []
        self.open_args = None
    def CreateFileW(self, *args):
        self.open_args = args
        return 42
    def GetFileInformationByHandle(self, handle, info):
        info._obj.attributes, info._obj.links = self.attributes, self.links
        return 1
    def CloseHandle(self, handle):
        self.closed.append(handle)


@pytest.mark.parametrize('attributes,links,directory,read', [
    (0x400, 1, True, False), (0, 1, True, False),
    (0x10, 1, False, True), (0, 2, False, True)])
def test_windows_leaf_handle_cannot_follow_reparse_or_read_alias(attributes, links, directory, read):
    api = FakeKernel(attributes, links)
    with pytest.raises(ValueError):
        open_checked(api, r'C:\work\file', directory=directory, read=read)
    assert api.closed == [42]
    assert not api.open_args[2] & 0x4  # No FILE_SHARE_DELETE: pinned name.
    assert api.open_args[5] & 0x00200000  # OPEN_REPARSE_POINT.
    assert ctypes.sizeof(FileInfo) == 52
