"""HTTPS for remote browsers, with plain HTTP permitted on loopback."""
from ipaddress import ip_address
from urllib.parse import urlsplit

from flask import jsonify, redirect, request
from werkzeug.middleware.proxy_fix import ProxyFix


def loopback(value):
    try:
        return ip_address(value or '').is_loopback
    except ValueError:
        return False


class LocalProxyFix:
    """Only local Caddy/cloudflared may assert forwarded TLS and client identity."""
    def __init__(self, application):
        self.application = application
        self.proxy = ProxyFix(application, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    def __call__(self, environ, start_response):
        application = self.proxy if loopback(environ.get('REMOTE_ADDR')) else self.application
        return application(environ, start_response)


def register_https_access(app, public_url):
    app.wsgi_app = LocalProxyFix(app.wsgi_app)

    @app.before_request
    def require_remote_https():
        original = request.environ.get('werkzeug.proxy_fix.orig', {})
        peer = original.get('REMOTE_ADDR', request.remote_addr)
        forwarded = any(request.headers.get(key) for key in (
            'X-Forwarded-For', 'X-Forwarded-Proto', 'X-Forwarded-Host',
            'Forwarded', 'Cf-Connecting-Ip', 'Via', 'X-Real-Ip',
        ))
        if request.is_secure or (loopback(peer) and not forwarded):
            return None
        # Never send a browser to a Host supplied by the request itself.
        destination = urlsplit(public_url() or '')
        if destination.scheme == 'https' and destination.hostname:
            path = request.path
            query = '?' + request.query_string.decode('latin1') if request.query_string else ''
            return redirect('https://' + destination.netloc + path + query, code=308)
        return jsonify(error='远程前端访问需要 HTTPS，请配置 HTTPS 反向代理或公网通道。'), 426
