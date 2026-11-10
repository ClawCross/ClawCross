"""Public tunnels preserve remote identity, fixed configuration and per-device state."""
import pytest

from ops.hub_tunnel import forwarded_headers, hub_url
from common.public_access import read_public_domain


def test_remote_forwarding_cannot_claim_local_or_internal_identity():
    incoming=[('X-Forwarded-For','127.0.0.1'),('Forwarded','for=127.0.0.1'),('Host','localhost'),
              ('X-User-Id','alice'),('X-Internal-Token','fake'),('Cookie','session=kept'),('Authorization','Bearer user-key')]
    headers={k.lower():v for k,v in forwarded_headers(incoming,'d-test.clawcross.net','198.51.100.8')}
    assert headers['x-forwarded-for']=='198.51.100.8' and headers['x-forwarded-proto']=='https'
    assert headers['host']=='d-test.clawcross.net'
    assert not {'forwarded','x-user-id','x-internal-token'} & headers.keys()
    assert headers['cookie']=='session=kept' and headers['authorization']=='Bearer user-key'


def test_remote_hubs_require_https_but_local_protocol_tests_can_use_http():
    assert hub_url('https://clawcross.net/')=='https://clawcross.net'
    assert hub_url('http://127.0.0.1:1234')=='http://127.0.0.1:1234'
    for value in ('http://remote.example','https://user:pass@host.example','file:///tmp','https://host.example/path'):
        with pytest.raises(ValueError):hub_url(value)


def test_connected_hub_url_has_runtime_priority_without_overwriting_fixed_domain(tmp_path,monkeypatch):
    from common import hub_access, runtime_paths
    env=tmp_path/'.env';env.write_text('PUBLIC_DOMAIN=https://fixed.example\n')
    monkeypatch.setattr(runtime_paths,'ENV_FILE',env)
    monkeypatch.setattr(hub_access,'tunnel_state',lambda:{'provider':'hub','phase':'connected','public_domain':'https://d-test.clawcross.net'})
    assert read_public_domain()=='https://d-test.clawcross.net'
    assert env.read_text()=='PUBLIC_DOMAIN=https://fixed.example\n'
    monkeypatch.setattr(hub_access,'tunnel_state',lambda:{})
    assert read_public_domain()=='https://fixed.example'
