"""Public Web connections and the strict Agent's immutable network boundary.

This is a boundary for trusted Web tools, not a process sandbox. Reuse the
command proxy's DNS validation and numeric connect so redirects and DNS
rebinding cannot turn a public URL into access to backend services.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import secrets
import threading
from urllib.parse import urlsplit

from webot.landlock_network import HttpProxy, ProxyPolicy, TcpServer, normalize_destination

_domains = ContextVar('web_network_domains', default=None)


def strict_network_active():
    return _domains.get() is not None


def with_agent_network(function):
    signature = inspect.signature(function)

    @wraps(function)
    async def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        if not bound.arguments.get('username') or not bound.arguments.get('session_id'):
            # Let the tool return its normal missing-identity error; never
            # load controls for an unauthenticated empty user ID.
            return await function(*args, **kwargs)
        from webot.runtime_settings import get_runtime_settings
        approval = get_runtime_settings(bound.arguments.get('username', ''),
                                        bound.arguments.get('session_id', '')).approval
        domains = tuple(approval.sandbox_allowed_domains) if approval.sandbox_security == 'strict' else None
        token = _domains.set(domains)
        try:
            return await function(*args, **kwargs)
        finally:
            _domains.reset(token)
    return wrapped


def web_access_violation(tool_name, args, user_id, session_id):
    if tool_name not in {'web_fetch', 'web_search'}:
        return ''
    from webot.runtime_settings import get_runtime_settings
    approval = get_runtime_settings(user_id, session_id).approval
    if approval.sandbox_security != 'strict':
        return ''
    domains = approval.sandbox_allowed_domains
    if not domains:
        return '严格安全模式的网络许可列表为空；Web 工具不联网，审批和 Bypass 不能扩大范围。'
    if tool_name == 'web_fetch':
        try:
            parsed = urlsplit(args.get('url', '').strip())
            if parsed.scheme not in {'http', 'https'} or parsed.username is not None or parsed.password is not None:
                raise ValueError('URL must be public HTTP(S) without credentials')
            host, port = normalize_destination(parsed.hostname or '', parsed.port if parsed.port is not None else (443 if parsed.scheme == 'https' else 80))
            if not ProxyPolicy(domains, '').allows_destination(host, port):
                return '严格安全模式拒绝未在网络许可列表中的网站；审批和 Bypass 不能扩大范围。'
        except (ValueError, OSError):
            return '严格安全模式拒绝无效的 Web 目标。'
    return ''


class PublicWebPolicy(ProxyPolicy):
    def __init__(self, domains, token):
        super().__init__(domains or [], token)
        self.public_only = domains is None

    def allows_destination(self, host, port):
        return self.public_only or super().allows_destination(host, port)


@contextmanager
def public_web_proxy():
    policy = PublicWebPolicy(_domains.get(), secrets.token_hex(24))
    server = TcpServer(('127.0.0.1', 0), HttpProxy)
    server.policy = policy
    server.slots = threading.BoundedSemaphore(32)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        yield f'http://{policy.token}:x@127.0.0.1:{server.server_address[1]}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
