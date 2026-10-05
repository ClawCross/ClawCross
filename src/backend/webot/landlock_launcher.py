"""Linux command launcher: inherited Landlock + seccomp, without new namespaces.

No host fallback. Optional supervisor supplies a loopback proxy fence and task cgroup limits.
The policy is read before restrictions; exec happens only after all filters apply.
"""
import ctypes
import errno
import json
import os
from pathlib import Path
import platform
import resource
import socket
import sys

LIBC = ctypes.CDLL(None, use_errno=True)


class Ruleset(ctypes.Structure):
    _fields_ = [('fs', ctypes.c_uint64), ('net', ctypes.c_uint64), ('scoped', ctypes.c_uint64)]


class PathRule(ctypes.Structure):
    _pack_ = 1
    _layout_ = 'ms'
    _fields_ = [('access', ctypes.c_uint64), ('fd', ctypes.c_int32)]


def check(value, label):
    if value < 0:
        raise OSError(ctypes.get_errno(), label + ': ' + os.strerror(ctypes.get_errno()))
    return value


def process_budget():
    # RLIMIT_NPROC counts all threads of the real UID, including the service.
    # Reserve a bounded margin above its current usage, rather than preventing
    # every fork on a busy service account. This is not a per-job tree quota.
    count = 0
    for status in Path('/proc').glob('[0-9]*/status'):
        try:
            values = dict(line.split(':', 1) for line in status.read_text().splitlines() if ':' in line)
            if int(values['Uid'].split()[0]) == os.getuid():
                count += int(values['Threads'].strip())
        except (OSError, ValueError, KeyError):
            continue
    return max(64, count + 64)


def process_read_paths():
    """Read resource statistics and existing process metadata, never memory/FDs.

    Refresh for each invocation. Landlock grants refer to inodes, so granting
    all of /proc would also expose other processes' environment and open files.
    Processes created later can still be managed by the background runner.
    """
    yield '/proc', 1 << 3
    for name in ('stat', 'meminfo', 'cpuinfo', 'loadavg', 'uptime', 'version',
                 'vmstat', 'diskstats', 'partitions', 'swaps'):
        yield '/proc/' + name, 1 << 2
    for directory in Path('/proc').glob('[0-9]*'):
        for name in ('stat', 'statm', 'status', 'comm', 'wchan'):
            yield str(directory / name), 1 << 2


def restrict(workspace, extra_read, extra_write, network_ports=(), *, strict=False, workspace_roots=None):
    nproc = process_budget()
    if sys.platform != 'linux' or platform.machine() not in {'x86_64', 'aarch64'}:
        raise RuntimeError('Landlock requires Linux x86_64 / aarch64')
    if os.geteuid() == 0:
        raise RuntimeError('Landlock command launcher refuses root execution')
    abi = check(LIBC.syscall(444, 0, 0, 1), 'query Landlock')
    if abi < 6:
        raise RuntimeError('Require ABI >= 6 for signal / abstract UNIX scopes')
    # ABI 5 adds IOCTL_DEV (bit 15). ABI 8 has no RESOLVE_UNIX (ABI 9).
    fs = (1 << 16) - 1
    attr = Ruleset(fs, 3, 3)
    fd = check(LIBC.syscall(444, ctypes.byref(attr), ctypes.sizeof(attr), 0), 'create ruleset')
    class NetRule(ctypes.Structure):
        _fields_ = [('access', ctypes.c_uint64), ('port', ctypes.c_uint64)]
    for port in network_ports:
        rule = NetRule(2, port)  # CONNECT_TCP, never BIND_TCP.
        check(LIBC.syscall(445, fd, 2, ctypes.byref(rule), 0), 'allow proxy port')
    read = (1 << 0) | (1 << 2) | (1 << 3)
    # Workspace permits ordinary files / directories, never device creation.
    rw = fs & ~((1 << 6) | (1 << 11) | (1 << 15))
    paths = [(path, rw) for path in dict.fromkeys(workspace_roots or [workspace])]
    paths += [(p, read) for p in ('/usr', '/bin', '/lib', '/lib64', sys.prefix, sys.base_prefix) if Path(p).exists()]
    paths += [(p, (1 << 2) | (1 << 1)) for p in ('/dev/null',) if Path(p).exists()]
    paths += [(p, 1 << 2) for p in ('/dev/urandom',) if Path(p).exists()]
    paths += [(p, (1 << 2) | (1 << 3)) for p in ('/etc/ssl/certs', '/etc/pki/tls/certs') if Path(p).exists()]
    # Standard mode follows the usual CLI sandbox read model: process and
    # system statistics are visible, but signals, writes and ptrace remain
    # restricted. This includes OS-readable process environments; it is not
    # an isolation boundary between tenants sharing the same Unix account.
    # Strict mode must not expose service credentials through /proc/environ.
    paths += list(process_read_paths()) if strict else [('/proc', (1 << 2) | (1 << 3))]
    paths += [(p, read if Path(p).is_dir() else 1 << 2) for p in extra_read]
    paths += [(p, rw if Path(p).is_dir() else (1 << 1) | (1 << 2) | (1 << 14)) for p in extra_write]
    try:
        for path, rights in paths:
            try:
                path_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                if path.startswith('/proc/'):
                    continue  # A process may exit while its metadata is pinned.
                raise
            try:
                rule = PathRule(rights, path_fd)
                check(LIBC.syscall(445, fd, 1, ctypes.byref(rule), 0), 'allow ' + path)
            finally:
                os.close(path_fd)
        check(LIBC.prctl(38, 1, 0, 0, 0), 'no_new_privs')
        check(LIBC.syscall(446, fd, 0), 'restrict self')
    finally:
        os.close(fd)
    # seccomp is additional to inherited filters; it does not remove them.
    sec = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    sec.seccomp_init.argtypes = [ctypes.c_uint32]
    sec.seccomp_init.restype = ctypes.c_void_p
    sec.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    sec.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    sec.seccomp_load.argtypes = [ctypes.c_void_p]
    sec.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = sec.seccomp_init(0x7fff0000)
    if not ctx:
        raise RuntimeError('seccomp_init failed')
    try:
        denied_calls = (() if network_ports else ('socket',)) + ('ptrace', 'process_vm_readv', 'process_vm_writev',
                     'setsid', 'setpgid', 'mount', 'umount2', 'pivot_root', 'chroot', 'setns', 'unshare', 'bpf',
                     'perf_event_open', 'open_by_handle_at', 'io_uring_setup',
                     'shmget', 'shmat', 'shmctl', 'shmdt', 'semget', 'semop', 'semtimedop', 'semctl',
                     'msgget', 'msgsnd', 'msgrcv', 'msgctl', 'chmod', 'fchmod', 'fchmodat', 'fchmodat2', 'chown', 'fchown', 'lchown', 'fchownat')
        for name in denied_calls:
            number = sec.seccomp_syscall_resolve_name(name.encode())
            if number >= 0:
                result = sec.seccomp_rule_add(ctx, 0x50000 | errno.EPERM, number, 0)
                if result != 0:
                    raise RuntimeError('seccomp rule ' + name + ': ' + str(result))
        # A command must not change resource limits of other processes of this UID.
        class ArgCompare(ctypes.Structure):
            _fields_ = [('arg', ctypes.c_uint), ('op', ctypes.c_int),
                        ('a', ctypes.c_uint64), ('b', ctypes.c_uint64)]
        sec.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(ArgCompare)]
        # An anonymous local socket pair is IPC within a command's process
        # tree, needed by asyncio and multiprocessing; it grants no host socket.
        number = sec.seccomp_syscall_resolve_name(b'socketpair')
        if number >= 0 and sec.seccomp_rule_add_array(ctx, 0x50000 | errno.EPERM, number, 1, ctypes.byref(ArgCompare(0, 1, socket.AF_UNIX, 0))) != 0:
            raise RuntimeError('seccomp socketpair rule failed')
        if network_ports:
            number = sec.seccomp_syscall_resolve_name(b'socket')
            filters = [ArgCompare(0, 1, socket.AF_INET, 0), ArgCompare(2, 6, 6, 0)]
            # Only IPv4 TCP sockets to the local proxy. The proxy can resolve
            # IPv4/IPv6 upstreams; UDP, UNIX, raw/SCTP sockets are never allowed.
            filters += [ArgCompare(1, 7, 15, kind) for kind in range(16) if kind != socket.SOCK_STREAM]
            filters += [ArgCompare(2, 4, proto, 0) for proto in range(1,6)]
            for comparison in filters:
                if sec.seccomp_rule_add_array(ctx, 0x50000 | errno.EPERM, number, 1, ctypes.byref(comparison)) != 0:
                    raise RuntimeError('seccomp network rule failed')
        number = sec.seccomp_syscall_resolve_name(b'prlimit64')
        if number >= 0 and sec.seccomp_rule_add_array(ctx, 0x50000 | errno.EPERM, number, 1, ctypes.byref(ArgCompare(0, 1, 0, 0))) != 0:
            raise RuntimeError('seccomp prlimit64 rule failed')
        if sec.seccomp_load(ctx) != 0:
            raise RuntimeError('seccomp_load failed')
    finally:
        sec.seccomp_release(ctx)
    # Hard limits cannot be raised by the command. They are per process/file,
    # not an aggregate cgroup quota for a process tree.
    for kind, cap in ((resource.RLIMIT_CPU, 120), (resource.RLIMIT_AS, 2 * 1024**3),
                      (resource.RLIMIT_FSIZE, 128 * 1024**2), (resource.RLIMIT_NOFILE, 256),
                      (resource.RLIMIT_NPROC, nproc)):
        soft, hard = resource.getrlimit(kind)
        cap = min(cap, soft) if soft != resource.RLIM_INFINITY else cap
        cap = min(cap, hard) if hard != resource.RLIM_INFINITY else cap
        resource.setrlimit(kind, (cap, cap))



def main():
    settings = json.loads(Path(sys.argv[1]).read_text())
    ports = settings.get('network_ports', [])
    if ports:
        # A kernel that silently skips systemd's BPF policy must never run the
        # command. The supervisor verified this endpoint is reachable outside
        # the fence; this unit must reach loopback and fail non-loopback access.
        host, port = settings['network_probe']
        with socket.create_connection(('127.0.0.1', port), timeout=.7) as probe:
            if probe.recv(64) != b'clawcross-fence-probe':
                raise RuntimeError('Loopback proxy fence probe failed')
        try:
            probe = socket.create_connection((host, port), timeout=.3)
        except OSError:
            pass
        else:
            probe.close()
            raise RuntimeError('Network fence is not enforced')
    restrict(settings['root'], settings.get('read_paths', []), settings.get('write_paths', []), ports,
             strict=settings.get('strict', False), workspace_roots=settings.get('workspace_roots'))
    os.execv(sys.argv[2], sys.argv[2:])


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('ClawCross Landlock 初始化失败: ' + str(exc), file=sys.stderr)
        sys.exit(125)
