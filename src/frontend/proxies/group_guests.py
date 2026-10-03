"""Public, capability-scoped group access through an invitation link; never
establishes a main-site session.

The link ``<public base>/group-guest#<ticket>`` is one invitation: opened in a
browser it is the guest chat page; pasted into another ClawCross it joins that
device as a full member, whose group traffic then goes through ``/relay/*`` here.
"""
import re
from urllib.parse import quote
from hashlib import sha256

import requests
from flask import jsonify, render_template, request
from itsdangerous import BadSignature, URLSafeTimedSerializer


def register_guest_routes(app, *, port_agent, internal_token, public_base=lambda: ''):
    signer = URLSafeTimedSerializer(app.secret_key, salt='group-human-invite-v1')

    @app.post('/proxy_groups/<gid>/guest-link')
    def create_guest_link(gid):
        from flask import session
        user = session.get('user_id')
        if not user:
            return jsonify(error='请先登录'), 401
        try:
            response = requests.post(f'http://127.0.0.1:{port_agent}/groups/{quote(gid, safe="")}/guest-invite',
                headers={'Authorization': f'Bearer {internal_token}:{user}'},
                json={'disable': (request.get_json(silent=True) or {}).get('disable') is True}, timeout=15)
            data = response.json()
            if response.status_code != 200:
                return jsonify(data), response.status_code
            if data.get('disabled'):
                return jsonify(disabled=True)
            ticket = signer.dumps({'url': data['server_url'], 'invite': data['invite']})
            base = public_base().strip().rstrip('/') or request.url_root.rstrip('/')
            if not base.startswith(('http://', 'https://')):
                base = 'https://' + base
            return jsonify(url=base + '/group-guest#' + ticket)
        except (requests.RequestException, ValueError):
            return jsonify(error='群服务暂时不可用'), 503

    # A device joined by link reaches the group server only through these calls.
    device_calls = {('POST', 'join'), ('POST', 'poll'), ('GET', 'group'), ('GET', 'messages'),
                    ('POST', 'messages'), ('POST', 'agents')}

    @app.route('/relay/<path:call>', methods=['GET', 'POST'])
    def group_device_relay(call):
        allowed = (request.method, call) in device_calls or (
            request.method == 'POST' and re.fullmatch(r'manage/[a-z_]{1,40}', call))
        if not allowed:
            return jsonify(error='不支持的群操作'), 404
        ticket = request.headers.get('X-Group-Invite', '')
        if len(ticket) > 4096:
            return jsonify(error='邀请链接无效'), 403
        try:
            # Joining needs a current link; a joined device keeps working after the link expires.
            target = signer.loads(ticket, max_age=30 * 86400 if call == 'join' else None)
        except BadSignature:
            return jsonify(error='邀请链接无效或已过期'), 403
        body = request.get_json(silent=True) if request.method == 'POST' else None
        headers = {}
        if call == 'join':
            body = body if isinstance(body, dict) else {}
            body = {'invite': target['invite'], **{k: str(body.get(k, ''))[:160] for k in ('node_id', 'user_id', 'display_name')}}
        else:
            credential = request.headers.get('Authorization', '')
            if not credential.startswith('Bearer ') or len(credential) > 200:
                return jsonify(error='需要群连接凭证'), 401
            headers['Authorization'] = credential
        try:
            with requests.Session() as client:
                client.trust_env = False
                response = client.request(request.method, target['url'] + '/relay/' + call, headers=headers, json=body,
                                          params={'after_id': request.args.get('after_id', '0')} if call == 'messages' else None,
                                          timeout=20, allow_redirects=False)
            result = jsonify(response.json())
            result.status_code = response.status_code
            result.headers['Cache-Control'] = 'no-store'
            return result
        except (requests.RequestException, ValueError):
            return jsonify(error='暂时无法连接群服务器，请稍后重试'), 503

    @app.get('/group-guest')
    def group_guest_page():
        response = app.make_response(render_template('group_guest.html'))
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.route('/group-guest-api/<action>', methods=['GET', 'POST'])
    def group_guest_api(action):
        methods = {'info': 'POST', 'join': 'POST', 'state': 'GET', 'messages': 'POST', 'rename': 'POST', 'password': 'POST'}
        if methods.get(action) != request.method:
            return jsonify(error='不支持的操作'), 405
        ticket = request.headers.get('X-Group-Invite', '')
        if len(ticket) > 4096:
            return jsonify(error='分享链接无效'), 403
        try:
            target = signer.loads(ticket, max_age=30 * 86400 if action in {'info', 'join'} else None)
        except BadSignature:
            return jsonify(error='分享链接无效或已过期'), 403
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(error='请求格式无效'), 400
        if action == 'info':
            body = {'invite': target['invite']}
        elif action == 'join':
            body = {'invite': target['invite'], 'name': body.get('name', ''), 'password': body.get('password', '')}
        elif action == 'password':
            body = {'password': body.get('password', '')}
        elif action == 'rename':
            body = {'name': body.get('name', '')}
        elif action == 'messages':
            body = {'content': body.get('content', ''), 'client_msg_id': body.get('client_msg_id', ''),
                    'mentions': body.get('mentions', [])}
        headers = {}
        if action not in {'info', 'join'}:
            credential = request.headers.get('X-Guest-Token', '')
            if not credential or len(credential) > 100:
                return jsonify(error='请先加入群聊'), 401
            headers['Authorization'] = 'Bearer ' + credential
        try:
            with requests.Session() as client:
                client.trust_env = False
                response = client.request(request.method, target['url'] + '/relay/guest/' + action,
                    headers=headers, json=body if request.method == 'POST' else None,
                    params={'after_id': request.args.get('after_id', '0')} if action == 'state' else None,
                    timeout=15, allow_redirects=False)
            data = response.json()
            if response.status_code == 200 and action in {'info', 'state'} and isinstance(data, dict) and data.get('group_id'):
                # Scope browser identity to the server and group, independent of invitation rotation.
                data['identity_key'] = sha256((target['url'].rstrip('/') + '\0' + data['group_id']).encode()).hexdigest()
            result = jsonify(data)
            result.status_code = response.status_code
            result.headers['Cache-Control'] = 'no-store'
            return result
        except (requests.RequestException, ValueError):
            return jsonify(error='暂时无法连接群聊，请稍后重试'), 503
