"""Network policy tests; public HTTPS integration is explicitly opt-in."""
import json
import os
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'/'backend'))
from webot.landlock_network import ProxyPolicy, NetworkDenied, normalize_destination


class NetworkPolicyTests(unittest.TestCase):
    def test_denied_host_is_not_resolved(self):
        policy=ProxyPolicy([], 'token')
        with patch('socket.getaddrinfo') as dns:
            with self.assertRaises(NetworkDenied): policy.connect('unapproved.example',443)
        dns.assert_not_called()
        self.assertEqual(policy.denied[0][0], 'unapproved.example:443')

    def test_public_name_resolving_to_private_address_is_blocked(self):
        policy=ProxyPolicy(['approved.example:443'], 'token')
        records=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
        with patch('socket.getaddrinfo',return_value=records), patch('socket.socket') as connect:
            with self.assertRaises(NetworkDenied): policy.connect('approved.example',443)
        connect.assert_not_called()
        self.assertEqual(policy.denied[0][1], 'private/local address blocked')

    def test_resolved_numeric_address_is_used_without_second_lookup(self):
        policy=ProxyPolicy(['approved.example:443'], 'token')
        record=(socket.AF_INET,socket.SOCK_STREAM,6,'',('1.1.1.1',443))
        with patch('socket.getaddrinfo',return_value=[record]) as dns, patch('socket.socket') as create:
            policy.connect('approved.example',443)
        dns.assert_called_once()
        create.return_value.connect.assert_called_once_with(('1.1.1.1',443))

    def test_port_grant_does_not_allow_other_port(self):
        policy=ProxyPolicy(['approved.example:443'], 'token')
        with patch('socket.getaddrinfo') as dns:
            with self.assertRaises(NetworkDenied): policy.connect('approved.example',22)
        dns.assert_not_called()

    def test_invalid_destinations_rejected(self):
        for host,port in [('host\nforged',443),('user@host',80),('host',0),('host',65536)]:
            with self.subTest(host=host,port=port),self.assertRaises(NetworkDenied):
                normalize_destination(host,port)


@unittest.skipUnless(os.environ.get('CLAWCROSS_NETWORK_INTEGRATION') == '1', 'explicit kernel fence test')
class NetworkFenceFailureTests(unittest.TestCase):
    def test_missing_kernel_fence_prevents_user_command_execution(self):
        import subprocess, tempfile
        from webot.landlock_network import start_server, FenceProbe, host_address
        from webot.command_sandbox import network_fence_available
        if not network_fence_available(): self.skipTest('needs systemd host')
        probe=start_server(FenceProbe,None,'0.0.0.0')
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); marker=root/'must-not-exist'
                policy=root/'policy.json'
                policy.write_text(json.dumps({'root':str(root),'network_ports':[probe.server_address[1]],'network_probe':[host_address(),probe.server_address[1]]}))
                launcher=Path(__file__).resolve().parents[1]/'src/backend/webot/landlock_launcher.py'
                result=subprocess.run([sys.executable,str(launcher),str(policy),sys.executable,'-c',f'open({str(marker)!r},"w").write("bad")'],capture_output=True,text=True,timeout=3)
                self.assertEqual(result.returncode,125)
                self.assertIn('Network fence is not enforced',result.stderr)
                self.assertFalse(marker.exists())
        finally:
            probe.shutdown(); probe.server_close()
