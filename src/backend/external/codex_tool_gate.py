"""Codex ACP launcher with a model-transport gate for native-tool-free chat.

The native harness cannot execute a model tool call it never receives. Remove
native definitions before inference and validate complete SSE frames before
forwarding them to Codex. Only ClawCross's two centrally authorized MCP wrappers
may cross this boundary. ACP permission flags alone do not provide this boundary.
"""
from __future__ import annotations

import http.server
import base64
import ctypes
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import threading
import tomllib
from urllib.parse import urlsplit

import httpx
import zstandard

MANAGED = frozenset({'mcp__ClawCross__tool_search','mcp__ClawCross__tool_call'})
MAX_BODY = 4 * 1024 * 1024
SAFE_ITEMS = frozenset({'message','reasoning','compaction'})
SAFE_EVENTS = frozenset({'response.created','response.in_progress','response.completed','response.metadata',
    'response.incomplete','response.failed','error','response.output_item.added','response.output_item.done',
    'response.content_part.added','response.content_part.done','response.output_text.delta',
    'response.output_text.done','response.refusal.delta','response.refusal.done',
    'response.reasoning_summary_part.added','response.reasoning_summary_part.done',
    'response.reasoning_summary_text.delta','response.reasoning_summary_text.done',
    'response.reasoning_text.delta','response.reasoning_text.done'})


class BlockedTool(RuntimeError):
    pass


def filter_request(body):
    if not isinstance(body, dict):
        raise ValueError('Invalid response request')
    allowed = [tool for tool in body.get('tools', [])
               if isinstance(tool, dict) and tool.get('type') == 'function' and tool.get('name') in MANAGED]
    return {**body, 'tools':allowed, 'tool_choice':'auto' if allowed else 'none',
            'parallel_tool_calls':False, 'stream':True}


def decode_request(raw, encoding=''):
    if encoding == 'zstd':
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw)) as reader:
            raw = reader.read(MAX_BODY+1)
    elif encoding == 'gzip':
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as reader:
            raw = reader.read(MAX_BODY+1)
    elif encoding not in ('','identity'):
        raise ValueError('Unsupported request encoding')
    if len(raw) > MAX_BODY:
        raise ValueError('Decoded request too large')
    return filter_request(json.loads(raw))


def refresh_request(raw, expected_token):
    body = json.loads(raw)
    if (not isinstance(body,dict) or set(body) != {'grant_type','refresh_token','client_id'}
            or body.get('grant_type') != 'refresh_token' or not isinstance(body.get('refresh_token'),str)
            or not expected_token or not secrets.compare_digest(body['refresh_token'],expected_token)
            or not isinstance(body.get('client_id'),str) or not 1 <= len(body['client_id']) <= 256):
        raise ValueError('Only renewal of the existing local login is permitted')
    return body


class ResponseGate:
    def __init__(self):
        self.calls = set()

    def item(self, item):
        kind = item.get('type')
        if kind in SAFE_ITEMS:
            return
        if kind == 'function_call' and item.get('name') in MANAGED:
            if item.get('id'):
                self.calls.add(item['id'])
            return
        raise BlockedTool('Native model tool output blocked')

    def validate(self, packet):
        kind = packet.get('type', '')
        if kind in {'response.function_call_arguments.delta','response.function_call_arguments.done'}:
            if packet.get('item_id') not in self.calls:
                raise BlockedTool('Unregistered tool arguments blocked')
        elif kind not in SAFE_EVENTS:
            safe_kind = kind if kind.replace('.','').replace('_','').isalnum() else 'unknown'
            raise BlockedTool('Unsupported model event blocked: '+safe_kind[:100])
        if isinstance(packet.get('item'), dict):
            self.item(packet['item'])
        for item in (packet.get('response') or {}).get('output', []):
            self.item(item)


def adapter_command():
    installed = shutil.which('codex-acp')
    if installed:
        return [installed]
    # Read package metadata only, never execute npm or download on message receipt.
    root = Path(os.environ.get('npm_config_cache') or Path.home()/'.npm')/'_npx'
    candidates = list(root.glob('*/node_modules/@agentclientprotocol/codex-acp/package.json'))
    for manifest in sorted(candidates, key=lambda p:p.stat().st_mtime, reverse=True):
        package = json.loads(manifest.read_text())
        if package.get('name') != '@agentclientprotocol/codex-acp':
            continue
        entry = package.get('bin', {})
        entry = entry.get('codex-acp') if isinstance(entry, dict) else entry
        if not entry:
            continue
        target = (manifest.parent/entry).resolve()
        if target.is_relative_to(manifest.parent.resolve()) and target.is_file():
            node = shutil.which('node')
            if not node:
                break
            return [node, str(target)]
    raise RuntimeError('Install the official Codex ACP adapter before enabling text-only chat')


def fence_network(ports):
    """Require model traffic through our gate, including all descendant processes.

    If Codex ignores the configured model URL, a direct provider connection must
    fail rather than silently bypass tool filtering. The Agent API port permits
    only the normal scoped ClawCross MCP connector.
    """
    if sys.platform != 'linux':
        raise RuntimeError('Text-only Codex requires the Linux network fence')
    libc = ctypes.CDLL(None, use_errno=True)
    class Ruleset(ctypes.Structure):
        _fields_ = [('fs',ctypes.c_uint64),('net',ctypes.c_uint64)]
    class Rule(ctypes.Structure):
        _fields_ = [('access',ctypes.c_uint64),('port',ctypes.c_uint64)]
    if libc.syscall(444,0,0,1) < 4:
        raise RuntimeError('Landlock network isolation is unavailable')
    attr = Ruleset(0,2)
    fd = libc.syscall(444,ctypes.byref(attr),ctypes.sizeof(attr),0)
    if fd < 0:
        raise RuntimeError('Cannot create native network fence')
    try:
        for port in ports:
            rule = Rule(2,int(port))
            if libc.syscall(445,fd,2,ctypes.byref(rule),0) < 0:
                raise RuntimeError('Cannot configure native network fence')
        if libc.prctl(38,1,0,0,0) < 0 or libc.syscall(446,fd,0) < 0:
            raise RuntimeError('Cannot enforce native network fence')
    finally:
        os.close(fd)


def codex_command(command):
    """Prefer the user's installed Codex; fall back to the adapter's bundle."""
    installed = shutil.which('codex')
    if installed:
        return [installed]
    for part in command:
        for parent in Path(part).resolve().parents:
            if parent.name == 'node_modules':
                cli = parent/'@openai/codex/bin/codex.js'
                if cli.is_file():
                    return [shutil.which('node'), str(cli)]
    raise RuntimeError('Codex executable is unavailable')


def toml_value(value):
    if isinstance(value, dict):
        return '{'+', '.join(json.dumps(k)+'='+toml_value(v) for k,v in value.items())+'}'
    return json.dumps(value)


def start_codex():
    # ACP's CODEX_CONFIG only configures threads. Account discovery runs before
    # a thread exists, so apply the same restrictions to the app-server startup.
    command = json.loads(os.environ['CLAWCROSS_CODEX_COMMAND'])
    for key,value in json.loads(os.environ['CODEX_CONFIG']).items():
        command.extend(['-c',key+'='+toml_value(value)])
    command.extend(sys.argv[2:])
    os.execvpe(command[0],command,os.environ)


def local_certificate(directory):
    """Trust one ephemeral loopback certificate only in this native process."""
    directory = Path(directory)
    cert,key = directory/'cert.pem',directory/'key.pem'
    ca,ca_key,csr,ext = (directory/name for name in ('ca.pem','ca-key.pem','leaf.csr','extensions'))
    ext.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n'
                   'extendedKeyUsage=serverAuth\nsubjectAltName=DNS:chatgpt.com,DNS:auth.openai.com\n')
    commands = [
        ['openssl','req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256',
         '-nodes','-days','1','-subj','/CN=ClawCross process CA',
         '-addext','basicConstraints=critical,CA:TRUE','-keyout',str(ca_key),'-out',str(ca)],
        ['openssl','req','-new','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256',
         '-nodes','-subj','/CN=chatgpt.com','-keyout',str(key),'-out',str(csr)],
        ['openssl','x509','-req','-in',str(csr),'-CA',str(ca),'-CAkey',str(ca_key),
         '-set_serial','1','-days','1','-extfile',str(ext),'-out',str(cert)],
    ]
    for command in commands:
        subprocess.run(command,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
    key.chmod(0o600);ca_key.chmod(0o600)
    bundle = Path(directory)/'trust.pem'
    data = ca.read_bytes()
    for name in ('CODEX_CA_CERTIFICATE','SSL_CERT_FILE'):
        existing = os.environ.get(name)
        if existing:
            data += b'\n'+Path(existing).read_bytes()
    bundle.write_bytes(data)
    return cert,key,bundle


def main():
    home = Path(os.getenv('CODEX_HOME') or Path.home()/'.codex')
    config_path = home/'config.toml'
    config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
    if config.get('model_provider', 'openai') != 'openai' or 'openai' in config.get('model_providers', {}):
        raise RuntimeError('Text-only Codex currently requires the standard OpenAI provider')
    login = json.loads((home/'auth.json').read_text())
    if login.get('auth_mode') != 'chatgpt' or not login.get('tokens'):
        raise RuntimeError('Text-only Codex currently requires the existing ChatGPT login')
    nonce = secrets.token_urlsafe(32)
    proxy_auth = 'Basic '+base64.b64encode(('gate:'+nonce).encode()).decode()
    launcher = tempfile.TemporaryDirectory(prefix='clawcross-codex-')
    cert,key,bundle = local_certificate(launcher.name)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert,key)
    rejected, rejection_lock = set(),threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        upstream_host = 'chatgpt.com'

        def log_message(self, *_):
            pass  # Never log request URLs, authorization headers or prompts.

        def do_GET(self):
            self.proxy(False)

        def do_POST(self):
            self.proxy(True)

        def do_CONNECT(self):
            # An explicit, process-local HTTPS proxy keeps Codex's original
            # workspace routing and persisted session URLs intact. It may only
            # terminate this one official host, never arbitrary destinations.
            if self.path not in {'chatgpt.com:443','auth.openai.com:443'} or not secrets.compare_digest(
                    self.headers.get('Proxy-Authorization',''),proxy_auth):
                self.send_error(403)
                return
            self.send_response(200,'Connection established');self.end_headers()
            self.close_connection = True
            host = self.path.rsplit(':',1)[0]
            class TunnelHandler(Handler):
                upstream_host = host
            with tls.wrap_socket(self.connection,server_side=True) as connection:
                TunnelHandler(connection,self.client_address,self.server)

        def proxy(self, post):
            parsed = urlsplit(self.path)
            path = parsed.path.removeprefix('/backend-api')
            renewal = self.upstream_host == 'auth.openai.com'
            model = post and not renewal
            allowed = (post and path == '/oauth/token' and not parsed.query) if renewal else (
                path in {'/codex/responses','/models','/codex/models','/wham/accounts/check'}
                and post == (path == '/codex/responses'))
            if not allowed:
                self.send_error(403, 'Unsupported Codex route')
                return
            try:
                fingerprint = None
                current_login = json.loads((home/'auth.json').read_text())['tokens']
                if not renewal and not secrets.compare_digest(self.headers.get('Authorization',''),
                        'Bearer '+current_login['access_token']):
                    self.send_error(401)
                    return
                size = int(self.headers.get('Content-Length', '0'))
                if size < 0 or size > MAX_BODY:
                    self.send_error(413)
                    return
                raw = self.rfile.read(size)
                body = refresh_request(raw,current_login.get('refresh_token','')) if renewal else (
                    decode_request(raw,self.headers.get('Content-Encoding','').lower()) if model else None)
                if model:
                    fingerprint = hashlib.sha256(json.dumps(body.get('input'),sort_keys=True).encode()).digest()
                    with rejection_lock:
                        if fingerprint in rejected:
                            self.send_error(400,'This model turn was blocked; automatic retry is disabled')
                            return
                # This is not a general HTTP proxy. Credentials can only reach
                # the original, fixed ChatGPT service over verified HTTPS.
                url = 'https://auth.openai.com/oauth/token' if renewal else 'https://chatgpt.com/backend-api'+path
                if parsed.query:
                    url += '?'+parsed.query
                headers = {k:v for k,v in self.headers.items() if k.lower() not in {
                    'host','connection','proxy-authorization','content-length','content-encoding','accept-encoding','transfer-encoding'}}
                with httpx.Client(timeout=httpx.Timeout(240, connect=15)) as client:
                    with client.stream('POST' if post else 'GET', url, headers=headers, json=body) as response:
                        if response.status_code != 200:
                            data = b'{"error":{"message":"Codex upstream request failed"}}'
                            self.send_response(response.status_code)
                            self.send_header('Content-Type','application/json')
                            self.send_header('Content-Length',str(len(data)))
                            self.end_headers(); self.wfile.write(data)
                            return
                        if not model:
                            raw = response.read()
                            self.send_response(200)
                            self.send_header('Content-Type','application/json')
                            self.send_header('Content-Length',str(len(raw)))
                            self.send_header('Connection','close');self.end_headers()
                            self.wfile.write(raw)
                            self.close_connection = True
                            return
                        self.send_response(200)
                        self.send_header('Content-Type','text/event-stream')
                        self.send_header('Transfer-Encoding','chunked')
                        self.send_header('Connection','close'); self.end_headers()
                        gate, frame = ResponseGate(), []
                        for line in response.iter_lines():
                            if line:
                                frame.append(line)
                                continue
                            data = '\n'.join(part[5:].lstrip() for part in frame if part.startswith('data:'))
                            if data and data != '[DONE]':
                                gate.validate(json.loads(data))
                            if frame:
                                chunk = ('\n'.join(frame)+'\n\n').encode()
                                self.wfile.write(format(len(chunk),'x').encode()+b'\r\n'+chunk+b'\r\n')
                                self.wfile.flush()
                            frame = []
                        if frame:
                            raise BlockedTool('Incomplete model stream blocked')
                        self.wfile.write(b'0\r\n\r\n');self.wfile.flush()
                        self.close_connection = True
            except BlockedTool as exc:
                if fingerprint is not None:
                    with rejection_lock:
                        if len(rejected) >= 64:
                            rejected.pop()
                        rejected.add(fingerprint)
                print('Codex gate: '+str(exc),file=sys.stderr)
                self.close_connection = True
            except (OSError, ValueError, httpx.HTTPError) as exc:
                print('Codex gate: upstream '+type(exc).__name__,file=sys.stderr)
                # No unvalidated frame or arbitrary provider error text escapes.
                self.close_connection = True

    server = http.server.ThreadingHTTPServer(('127.0.0.1',0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    values = dict(os.environ)
    values['CODEX_CONFIG'] = json.dumps({
        'approval_policy':'never','sandbox_mode':'read-only',
        'features':{'shell_tool':False,'shell_snapshot':False,'responses_websockets':False,
                    'responses_websockets_v2':False,'multi_agent':False,'multi_agent_v2':False,
                    'hooks':False,'plugins':False,'apps':False,'enable_mcp_apps':False},
        'mcp_servers':{name:{'enabled':False} for name in config.get('mcp_servers', {})}})
    values['INITIAL_AGENT_MODE'] = 'read-only'
    values.pop('MODEL_PROVIDER', None)
    values['CODEX_CA_CERTIFICATE'] = str(bundle)
    values['SSL_CERT_FILE'] = str(bundle)
    for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
        values[name] = f'http://gate:{nonce}@127.0.0.1:{server.server_port}'
    values['NO_PROXY'] = values['no_proxy'] = '127.0.0.1,localhost,::1'
    command = adapter_command()
    values['CLAWCROSS_CODEX_COMMAND'] = json.dumps(codex_command(command))
    wrapper = Path(launcher.name)/'codex'
    wrapper.write_text('#!'+sys.executable+'\nimport os,sys\nos.execv(sys.executable,'+
                       repr([sys.executable,'-P',str(Path(__file__).resolve()),'--codex'])+'+sys.argv[1:])\n')
    wrapper.chmod(0o700)
    values['CODEX_PATH'] = str(wrapper)
    process = subprocess.Popen([sys.executable,'-P',str(Path(__file__).resolve()),'--child',
        str(server.server_port),os.getenv('PORT_AGENT','51200'),*command],env=values)
    def stop(_signum, _frame):
        process.terminate()
    for sig in (signal.SIGTERM,signal.SIGINT):
        signal.signal(sig, stop)
    try:
        return process.wait()
    finally:
        server.shutdown(); server.server_close()
        launcher.cleanup()


if __name__ == '__main__':
    try:
        if sys.argv[1:2] == ['--codex']:
            start_codex()
        if sys.argv[1:2] == ['--child']:
            fence_network(sys.argv[2:4])
            os.execvpe(sys.argv[4],sys.argv[4:],os.environ)
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError):
        print('Codex text-only initialization failed; check the local login and adapter.', file=sys.stderr)
        raise SystemExit(1)
