"""Per-command proxies supervised outside a non-root systemd/Landlock workload.

The systemd IP filter restricts the workload to loopback; Landlock restricts
TCP connections to these proxy ports. Neither proxy variables nor port rules
alone are an IP/domain boundary. No global firewall changes or new namespace.
"""
from __future__ import annotations

import base64
import hmac
import http.server
import ipaddress
import json
import os
from pathlib import Path
import secrets
import select
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit


class NetworkDenied(OSError):
    pass


def normalize_destination(host: str, port: int) -> tuple[str, int]:
    host = host.rstrip('.').lower().encode('idna').decode('ascii')
    if not host or len(host) > 253 or not 1 <= port <= 65535 or any(c in host for c in '/@\\\r\n \t'):
        raise NetworkDenied('Invalid destination')
    return host, port


class ProxyPolicy:
    def __init__(self, allowed, token):
        self.allowed = set(allowed)
        self.token = token
        self.denied = []
        self.lock = threading.Lock()

    def reject(self, host, port, reason):
        with self.lock:
            if len(self.denied) < 16:
                self.denied.append((f'{host}:{port}', reason))
        raise NetworkDenied(reason)

    def connect(self, host, port):
        host, port = normalize_destination(host, port)
        if host not in self.allowed and f'{host}:{port}' not in self.allowed:
            self.reject(host, port, 'destination not allowed')
        try:
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            raise NetworkDenied('Destination resolution failed')
        # Resolve and connect to the same vetted numeric address. No second DNS
        # lookup, local-address exception, or fallback to an unvetted address.
        if not records or any(not address.is_global or address.is_multicast or address.is_reserved or getattr(address, 'is_site_local', False) for item in records for address in [ipaddress.ip_address(item[4][0])]):
            self.reject(host, port, 'private/local address blocked')
        last = None
        for family, kind, protocol, _, address in records:
            sock = socket.socket(family, kind, protocol)
            sock.settimeout(8)
            try:
                sock.connect(address)
                sock.settimeout(None)
                return sock
            except OSError as exc:
                last = exc
                sock.close()
        raise OSError('Upstream connection failed') from last


def relay(left, right):
    """Bounded buffers, half-close propagation, no TLS interception."""
    readers = {left: right, right: left}
    while readers:
        ready, _, _ = select.select(list(readers), [], [], 30)
        if not ready:
            return
        for src in ready:
            chunk = src.recv(65536)
            dst = readers[src]
            if not chunk:
                dst.shutdown(socket.SHUT_WR)
                del readers[src]
            else:
                dst.sendall(chunk)


class HttpProxy(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    rbufsize = 0
    def log_message(self, *args):
        pass

    def authorized(self):
        expected = 'Basic ' + base64.b64encode((self.server.policy.token + ':x').encode()).decode()
        return hmac.compare_digest(self.headers.get('Proxy-Authorization', ''), expected)

    def handle_proxy(self):
        self.close_connection = True
        if not self.authorized():
            self.send_error(407, 'Proxy authentication required')
            return
        upstream = None
        try:
            if self.command == 'CONNECT':
                parsed = urlsplit('//'+self.path)
                host, port = normalize_destination(parsed.hostname or '', 443 if parsed.port is None else parsed.port)
            else:
                parsed = urlsplit(self.path)
                if parsed.scheme != 'http' or parsed.username is not None:
                    raise NetworkDenied('Use CONNECT for HTTPS')
                host, port = normalize_destination(parsed.hostname or '', 80 if parsed.port is None else parsed.port)
            upstream = self.server.policy.connect(host, port)
            if self.command == 'CONNECT':
                self.send_response(200, 'Connection Established'); self.end_headers(); self.wfile.flush()
            else:
                if self.headers.get('Transfer-Encoding'):
                    raise NetworkDenied('Chunked proxy requests are unsupported')
                length = int(self.headers.get('Content-Length', '0'))
                if length < 0 or length > 64 * 1024**2:
                    raise NetworkDenied('Invalid request length')
                target = parsed.path or '/'
                if parsed.query:
                    target += '?'+parsed.query
                headers = [f'{self.command} {target} HTTP/1.1', f'Host: {host}:{port}', 'Connection: close']
                for name, value in self.headers.items():
                    if name.lower() not in {'host','connection','proxy-connection','proxy-authorization'}:
                        headers.append(name+': '+value)
                upstream.sendall(('\r\n'.join(headers)+'\r\n\r\n').encode('latin1'))
                while length:
                    chunk = self.rfile.read(min(length,65536))
                    if not chunk:
                        raise NetworkDenied('Incomplete request body')
                    upstream.sendall(chunk); length -= len(chunk)
            relay(self.connection, upstream)
        except (OSError, ValueError):
            # Never return raw DNS/upstream details or proxy credentials.
            if upstream is None:
                self.send_error(403, 'ClawCross proxy denied or unreachable destination')
        finally:
            if upstream is not None:
                upstream.close()

    do_CONNECT = do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = do_OPTIONS = do_PATCH = handle_proxy


class SocksProxy(socketserver.BaseRequestHandler):
    def read(self, n):
        data = b''
        while len(data) < n:
            part = self.request.recv(n-len(data))
            if not part:
                raise OSError('Incomplete SOCKS request')
            data += part
        return data

    def handle(self):
        upstream = None
        try:
            version, count = self.read(2)
            if version != 5 or 2 not in self.read(count):
                self.request.sendall(b'\x05\xff'); return
            self.request.sendall(b'\x05\x02')
            if self.read(1) != b'\x01':
                return
            username = self.read(self.read(1)[0]).decode('ascii')
            password = self.read(self.read(1)[0])
            if not hmac.compare_digest(username, self.server.policy.token) or password != b'x':
                self.request.sendall(b'\x01\x01'); return
            self.request.sendall(b'\x01\x00')
            version, command, _, kind = self.read(4)
            if version != 5 or command != 1:
                raise NetworkDenied('Only TCP CONNECT is supported')
            if kind == 1:
                host = socket.inet_ntop(socket.AF_INET, self.read(4))
            elif kind == 4:
                host = socket.inet_ntop(socket.AF_INET6, self.read(16))
            elif kind == 3:
                host = self.read(self.read(1)[0]).decode('ascii')
            else:
                raise NetworkDenied('Invalid SOCKS address')
            port = int.from_bytes(self.read(2), 'big')
            upstream = self.server.policy.connect(host, port)
            self.request.sendall(b'\x05\x00\x00\x01'+b'\x00'*6)
            relay(self.request, upstream)
        except (OSError, ValueError):
            try:
                self.request.sendall(b'\x05\x02\x00\x01'+b'\x00'*6)
            except OSError:
                pass
        finally:
            if upstream:
                upstream.close()


class TcpServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False
    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close(); return
        super().process_request(request, client_address)
    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def start_server(handler, policy, host='127.0.0.1'):
    server = TcpServer((host,0),handler)
    server.policy = policy
    server.slots = threading.BoundedSemaphore(32)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    return server


class FenceProbe(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.sendall(b'clawcross-fence-probe')


def host_address():
    records = json.loads(subprocess.check_output(['ip','-j','-4','addr'], timeout=2))
    return next(a['local'] for item in records for a in item.get('addr_info', [])
                if a.get('scope') == 'global' and not ipaddress.ip_address(a['local']).is_loopback)


def main():
    path = Path(sys.argv[1])
    settings = json.loads(path.read_text())
    servers = []
    proc = None
    unit = 'clawcross-command-'+secrets.token_hex(8)
    def stop(*args):
        if proc is not None:
            subprocess.run(['sudo','-n','systemctl','stop',unit], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,timeout=5)
        if args:
            raise SystemExit(143)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        policy = ProxyPolicy(settings.get('allowed_domains', []), secrets.token_hex(24))
        http = start_server(HttpProxy, policy); servers.append(http)
        socks = start_server(SocksProxy, policy); servers.append(socks)
        probe = start_server(FenceProbe, policy, '0.0.0.0'); servers.append(probe)
        address = host_address()
        # Confirm the synthetic non-loopback endpoint is reachable before
        # relying on its denial as evidence that the workload IP fence works.
        with socket.create_connection((address,probe.server_address[1]), timeout=1) as check:
            if check.recv(64) != b'clawcross-fence-probe':
                raise RuntimeError('Network fence probe unavailable')
        settings['network_ports'] = [http.server_address[1], socks.server_address[1]]
        settings['network_probe'] = [address, probe.server_address[1]]
        path.write_text(json.dumps(settings))
        env = {name:value for name,value in os.environ.items() if name in {'PATH','HOME','USER','LANG','LC_ALL','TERM','TMPDIR','PYTHON_BASIC_REPL'}}
        env.update({'http_proxy':f'http://{policy.token}:x@127.0.0.1:{http.server_address[1]}',
                    'https_proxy':f'http://{policy.token}:x@127.0.0.1:{http.server_address[1]}',
                    'all_proxy':f'socks5h://{policy.token}:x@127.0.0.1:{socks.server_address[1]}',
                    'no_proxy':''})
        for name in ('http_proxy','https_proxy','all_proxy','no_proxy'):
            env[name.upper()] = env[name]
        argv = ['sudo','-n','systemd-run','--quiet','--collect','--wait','--pipe','--unit='+unit,
                '--uid='+str(os.getuid()),'--gid='+str(os.getgid()),'--expand-environment=no',
                '--working-directory='+os.getcwd(),'--property=NoNewPrivileges=yes',
                '--property=CapabilityBoundingSet=','--property=IPAddressDeny=any',
                '--property=IPAddressAllow=localhost','--property=SocketBindDeny=any',
                '--property=KillMode=control-group','--property=TimeoutStopSec=2',
                '--property=RuntimeMaxSec='+str(settings.get('wall_timeout',180)),
                '--property=MemoryMax=2G','--property=TasksMax=128']
        for name,value in env.items():
            argv.extend(['--setenv='+name+'='+value])
        launcher = Path(__file__).with_name('landlock_launcher.py')
        proc = subprocess.Popen([*argv,'--',sys.executable,str(launcher),str(path),*sys.argv[2:]])
        code = proc.wait()
        for destination, reason in policy.denied:
            print('ClawCross proxy denied network target: '+destination+' ('+reason+')',file=sys.stderr)
        return code
    finally:
        if proc is not None:
            stop()
        for server in servers:
            server.server_close()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('ClawCross Landlock 初始化失败: controlled network unavailable ('+type(exc).__name__+')',file=sys.stderr)
        sys.exit(125)
