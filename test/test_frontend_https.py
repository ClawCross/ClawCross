"""Remote access cannot claim TLS or local identity through forged proxy headers."""
from flask import Flask, jsonify, request
from frontend.https_access import register_https_access


def client(domain='https://clawcross.example'):
    app = Flask(__name__)
    register_https_access(app, lambda: domain)
    @app.route('/studio')
    def studio():
        return jsonify(peer=request.remote_addr, secure=request.is_secure)
    return app.test_client()


def test_direct_local_http_and_https_remain_available():
    browser = client()
    assert browser.get('/studio').status_code == 200
    assert browser.get('/studio', base_url='https://localhost').json['secure'] is True


def test_remote_http_redirects_to_configured_https_not_untrusted_host():
    result = client().get('/studio?tab=chat', base_url='http://attacker.example', environ_overrides={'REMOTE_ADDR':'198.51.100.8'},
                          headers={'X-Forwarded-Proto':'https','X-Forwarded-For':'127.0.0.1','X-Forwarded-Host':'attacker.example'})
    assert result.status_code == 308
    assert result.headers['Location'] == 'https://clawcross.example/studio?tab=chat'


def test_local_https_proxy_and_http_proxy_are_distinguished():
    browser = client()
    forwarded = {'X-Forwarded-Proto':'https','X-Forwarded-For':'198.51.100.8'}
    result = browser.get('/studio', headers=forwarded)
    assert result.status_code == 200
    assert result.json == {'peer':'198.51.100.8','secure':True}
    forwarded['X-Forwarded-Proto'] = 'http'
    assert browser.get('/studio', headers=forwarded).status_code == 308


def test_remote_http_without_https_entry_is_rejected():
    assert client('').get('/studio', environ_overrides={'REMOTE_ADDR':'198.51.100.8'}).status_code == 426
