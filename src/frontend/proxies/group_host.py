"""Host-only group administration. Machine credentials stay on the server."""
from urllib.parse import quote, urlsplit

import requests
from flask import jsonify, render_template, request


def register_group_host_routes(app, *, is_host_request):
    def allowed():
        if not is_host_request():
            return False
        if urlsplit(request.host_url).hostname not in {'localhost', '127.0.0.1', '::1'}:
            return False
        origin = request.headers.get('Origin')
        return not origin or urlsplit(origin).netloc == request.host

    def relay(method, path, body=None):
        from groups.config import service_key, service_url
        response = requests.request(method, service_url() + '/relay/admin/' + path,
                                    headers={'X-Group-Service-Key': service_key()}, json=body,
                                    timeout=10)
        return response.json(), response.status_code

    @app.get('/proxy_group_host/available')
    def group_host_available():
        return jsonify(available=allowed())

    @app.get('/group-host')
    def group_host_page():
        if not allowed():
            return '群聊服务器管理仅允许本机直接访问。请使用 http://127.0.0.1:<前端端口>/group-host', 403
        return render_template('group_host.html')

    @app.get('/proxy_group_host')
    def group_host_status():
        if not allowed():
            return jsonify(error='服务器管理仅允许本机直接访问'), 403
        try:
            network, status = relay('GET', 'network')
            if status != 200:
                return jsonify(network), status
            groups, status = relay('GET', 'groups')
            return jsonify(**groups, network=network), status
        except (requests.RequestException, ValueError):
            return jsonify(error='暂时无法连接本机群聊服务器'), 503

    @app.post('/proxy_group_host/network')
    def group_host_network():
        if not allowed():
            return jsonify(error='服务器管理仅允许本机直接访问'), 403
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict) or not isinstance(body.get('enabled'), bool):
            return jsonify(error='需要明确的联网开关'), 400
        try:
            payload, status = relay('POST', 'network', {'enabled': body['enabled']})
            return jsonify(payload), status
        except (requests.RequestException, ValueError):
            return jsonify(error='暂时无法连接本机群聊服务器'), 503

    @app.post('/proxy_group_host/groups/<gid>/<action>')
    def group_host_manage(gid, action):
        if not allowed():
            return jsonify(error='服务器管理仅允许本机直接访问'), 403
        if action not in {'patch', 'remove_member', 'member_patch', 'external_access'}:
            return jsonify(error='不支持的管理操作'), 400
        try:
            payload, status = relay('POST', 'groups/' + quote(gid, safe='') + '/' + action,
                                    request.get_json(silent=True) or {})
            return jsonify(payload), status
        except (requests.RequestException, ValueError):
            return jsonify(error='暂时无法连接本机群聊服务器'), 503
