"""Synthetic strict-tool attacks; never probe live users or internal services."""
import asyncio
from contextlib import contextmanager
import json
import http.server
import socket
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from webot import runtime_settings
from webot.approval_review import authorize_action
from webot.mcp import search
from webot.permission_context import resolve_permission_context
from webot.policy import WeBotToolPolicy
from webot import web_security as security


def settings(domains=(), mode='bypass', strict=True):
    return SimpleNamespace(approval=SimpleNamespace(
        sandbox_security='strict' if strict else 'standard',
        sandbox_allowed_domains=list(domains), mode=mode))


@pytest.mark.parametrize('mode', ['auto', 'manual', 'bypass'])
def test_strict_web_denial_precedes_approval_and_permits(mode):
    with patch.object(runtime_settings, 'get_runtime_settings', return_value=settings(mode=mode)), \
            patch.object(search, 'authorize_action', new=AsyncMock()) as reviewer, \
            patch.object(search, 'consume_execution_permit', return_value=True) as permit:
        for tool, args in [('web_fetch', {'url': 'https://example.com'}), ('web_search', {'query': 'hello'})]:
            reject = asyncio.run(search._web_access_gate('alice', 's', tool, args))
            assert '严格' in reject
            decision = resolve_permission_context(user_id='alice', session_id='s', tool_name=tool,
                args=args, policy=WeBotToolPolicy(default_approval='manual'))
            assert decision.decision == 'deny'
            assert not decision.requires_approval
            result = asyncio.run(authorize_action(user_id='alice', session_id='s', tool_name=tool,
                args=args, policy=WeBotToolPolicy(default_approval='manual')))
            assert not result.allowed
        reviewer.assert_not_awaited()
        permit.assert_not_called()


@pytest.mark.parametrize('url', ['https://other.example', 'http://example.com',
    'https://example.com:8443', 'https://example.com:0',
    'https://user:password@example.com', 'file:///etc/hosts'])
def test_strict_web_cannot_change_destination_or_port(url):
    with patch.object(runtime_settings, 'get_runtime_settings', return_value=settings(['example.com:443'])):
        assert security.web_access_violation('web_fetch', {'url': url}, 'alice', 's')
        assert not security.web_access_violation('web_fetch', {'url': 'https://example.com/path'}, 'alice', 's')


@pytest.mark.parametrize('records', [
    [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))],
    [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('1.1.1.1', 443)),
     (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.169.254', 443))],
    [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::ffff:127.0.0.1', 443, 0, 0))],
])
def test_dns_to_private_address_denied_before_connect(records):
    policy = security.PublicWebPolicy(['example.com:443'], 'token')
    with patch('socket.getaddrinfo', return_value=records), patch('socket.socket') as create:
        with pytest.raises(OSError, match='private/local'):
            policy.connect('example.com', 443)
        create.assert_not_called()


def test_dns_lookup_is_pinned_to_numeric_connect():
    policy = security.PublicWebPolicy(None, 'token')
    records = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('1.1.1.1', 443))]
    with patch('socket.getaddrinfo', return_value=records) as dns, patch('socket.socket') as create:
        policy.connect('example.com', 443)
        dns.assert_called_once()
        create.return_value.connect.assert_called_once_with(('1.1.1.1', 443))


def test_concurrent_agents_keep_separate_network_scopes():
    @security.with_agent_network
    async def run(username, session_id):
        await asyncio.sleep(0)
        return security._domains.get()
    def current(user, session):
        return settings([user + '.example']) if user != 'ordinary' else settings(strict=False)
    async def execute():
        return await asyncio.gather(run('alice', 'a'), run('bob', 'b'), run('ordinary', 'c'))
    with patch.object(runtime_settings, 'get_runtime_settings', side_effect=current):
        assert asyncio.run(execute()) == [('alice.example',), ('bob.example',), None]
    assert not security.strict_network_active()


def test_strict_search_proxy_is_used_and_browser_fallback_never_launches():
    proxy_url = 'http://test-token:x@127.0.0.1:12345'
    @contextmanager
    def proxy():
        assert security._domains.get() == ('duckduckgo.com:443',)
        yield proxy_url
    with patch.object(runtime_settings, 'get_runtime_settings', return_value=settings(['duckduckgo.com:443'])), \
            patch.object(search, 'public_web_proxy', proxy), \
            patch.object(search, 'DDGS') as ddgs, \
            patch.object(search, '_web_access_gate', new=AsyncMock(return_value=None)), \
            patch.object(search.asyncio, 'create_subprocess_exec', new=AsyncMock()) as spawn:
        ddgs.return_value.__enter__.return_value.text.return_value = []
        result = json.loads(asyncio.run(search.web_search('hello', format='json', username='alice', session_id='s')))
        assert '严格' in result['browser_fallback']['error']
        ddgs.assert_called_once_with(timeout=search.DEFAULT_TIMEOUT, proxy=proxy_url)
        spawn.assert_not_awaited()


def test_http_redirects_use_same_proxy_and_ignore_host_environment():
    real_client = httpx.AsyncClient
    visits = []
    def handler(request):
        visits.append(str(request.url))
        if request.url.host == 'example.com':
            return httpx.Response(302, headers={'location': 'http://127.0.0.1/internal'})
        # Synthetic proxy transport refuses the redirect; no internal request.
        raise httpx.ProxyError('private/local address blocked')
    @contextmanager
    def proxy():
        yield 'http://test-token:x@127.0.0.1:12345'
    def client(**kwargs):
        assert kwargs['proxy'] == 'http://test-token:x@127.0.0.1:12345'
        assert kwargs['trust_env'] is False
        return real_client(transport=httpx.MockTransport(handler), follow_redirects=kwargs['follow_redirects'])
    with patch.object(search, 'public_web_proxy', proxy), patch.object(search.httpx, 'AsyncClient', side_effect=client):
        result = asyncio.run(search._fetch_url_payload('https://example.com'))
    assert not result['ok']
    assert 'private/local' in result['error']
    assert visits == ['https://example.com', 'http://127.0.0.1/internal']


def test_http_response_has_bounded_host_memory():
    real_client = httpx.AsyncClient
    @contextmanager
    def proxy():
        yield 'http://test-token:x@127.0.0.1:12345'
    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(lambda request:
            httpx.Response(200, content=b'x' * (search.MAX_FETCH_BYTES + 1))))
    with patch.object(search, 'public_web_proxy', proxy), patch.object(search.httpx, 'AsyncClient', side_effect=client):
        result = asyncio.run(search._fetch_url_payload('https://example.com'))
    assert not result['ok']
    assert '2 MiB' in result['error']


def test_missing_identity_returns_tool_error_without_loading_controls():
    with patch.object(runtime_settings, 'get_runtime_settings') as controls:
        result = json.loads(asyncio.run(search.web_fetch('https://example.com')))
    assert not result['ok']
    assert '身份' in result['error']
    controls.assert_not_called()


@pytest.mark.parametrize('domains', [None, ('example.com:80',)])
def test_real_http_proxy_stops_redirect_before_local_endpoint(domains):
    visits = []
    class Origin(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            visits.append(self.path)
            if self.path == '/private':
                self.send_response(200); self.end_headers()
                self.wfile.write(b'SYNTHETIC_SECRET')
            else:
                self.send_response(302)
                self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/private')
                self.end_headers()
        def log_message(self, *args):
            pass
    origin = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Origin)
    thread = threading.Thread(target=lambda: origin.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    original_connect = security.ProxyPolicy.connect
    def synthetic_origin(policy, host, port):
        if host == 'example.com' and port == 80:
            assert policy.allows_destination(host, port)
            return socket.create_connection(('127.0.0.1', origin.server_port))
        return original_connect(policy, host, port)
    token = security._domains.set(domains)
    try:
        with patch.object(security.ProxyPolicy, 'connect', synthetic_origin):
            result = asyncio.run(search._fetch_url_payload('http://example.com/'))
        assert not result['ok']
        assert result['status_code'] == 403
        assert 'SYNTHETIC_SECRET' not in result['text']
        assert visits == ['/']
    finally:
        security._domains.reset(token)
        origin.shutdown(); origin.server_close(); thread.join(timeout=1)
