"""Native calls must be blocked before Codex can receive or execute them."""
import json
import gzip
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest

import zstandard
from external.codex_tool_gate import filter_request, decode_request, refresh_request, ResponseGate, BlockedTool, MAX_BODY
from external.native_tool_policy import runtime_allowed, policy_card, clean_transport_warning


class GateTests(unittest.TestCase):
    def test_expected_http_fallback_is_not_sent_as_a_group_reply(self):
        warning='Warning: Falling back from WebSockets to HTTPS transport. unavailable\n\n'
        self.assertEqual(clean_transport_warning(warning+'hello'),'hello')
        self.assertEqual(clean_transport_warning('hello'),'hello')
        self.assertEqual(clean_transport_warning('Model request failed'),'Model request failed')

    def test_request_removes_all_native_surfaces_and_keeps_scoped_mcp_only(self):
        request={'tools':[{'type':'function','name':'exec_command'}, {'type':'web_search'},
                          {'type':'function','name':'mcp__ClawCross__tool_call'},
                          {'type':'function','name':'mcp__Other__read_secrets'}],
                 'tool_choice':{'type':'function','name':'exec_command'}, 'parallel_tool_calls':True}
        filtered=filter_request(request)
        self.assertEqual([tool['name'] for tool in filtered['tools']],['mcp__ClawCross__tool_call'])
        self.assertEqual(filtered['tool_choice'],'auto')
        self.assertFalse(filtered['parallel_tool_calls'])
        self.assertEqual(filter_request({'tools':[{'type':'web_search'}]})['tool_choice'],'none')

    def test_model_cannot_hallucinate_a_native_call_or_tool_argument_stream(self):
        for packet in [
            {'type':'response.output_item.added','item':{'type':'function_call','name':'exec_command','id':'native'}},
            {'type':'response.completed','response':{'output':[{'type':'custom_tool_call','name':'apply_patch'}]}},
            {'type':'response.function_call_arguments.delta','item_id':'unregistered','delta':'{}'},
            {'type':'response.web_search_call.in_progress'},
        ]:
            with self.subTest(packet=packet),self.assertRaises(BlockedTool):ResponseGate().validate(packet)
        gate=ResponseGate()
        gate.validate({'type':'response.output_item.added','item':{'type':'function_call','name':'mcp__ClawCross__tool_call','id':'managed'}})
        gate.validate({'type':'response.function_call_arguments.delta','item_id':'managed','delta':'{}'})
        gate.validate({'type':'response.output_text.delta','delta':'hello'})
        gate.validate({'type':'response.metadata','metadata':{'runtime_ms':100}})

    def test_compressed_native_requests_are_filtered_and_bounded(self):
        raw=json.dumps({'tools':[{'type':'function','name':'exec_command'}]}).encode()
        for encoding,encoded in [('gzip',gzip.compress(raw)),('zstd',zstandard.ZstdCompressor().compress(raw))]:
            with self.subTest(encoding=encoding):
                self.assertEqual(decode_request(encoded,encoding)['tools'],[])
        with self.assertRaises(ValueError):
            decode_request(gzip.compress(b' '*(MAX_BODY+1)),'gzip')

    def test_auth_proxy_only_renews_the_existing_local_login(self):
        body={'grant_type':'refresh_token','refresh_token':'local-login','client_id':'official-client'}
        self.assertEqual(refresh_request(json.dumps(body).encode(),'local-login'),body)
        for changes in [{'refresh_token':'another-account'},{'grant_type':'authorization_code'},{'redirect_uri':'https://example.invalid'}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                refresh_request(json.dumps({**body,**changes}).encode(),'local-login')

    def test_unsupported_and_custom_adapters_still_fail_closed(self):
        config={'platform':'codex','meta':{'acp':{'native_tools':False,'text_only':True}}}
        self.assertEqual(runtime_allowed(config),sys.platform=='linux')
        if sys.platform=='linux':self.assertEqual(policy_card(config)['mechanism'],'model-tool-gate')
        for extra in ({'connection_id':'remote'}, {'platform':'claude'},
                      {'meta':{'acp':{'native_tools':False,'text_only':True,'agent_command':'anything'}}}):
            self.assertFalse(runtime_allowed({**config,**extra}))

    @unittest.skipUnless(sys.platform=='linux','Linux kernel fence')
    def test_native_process_cannot_bypass_gate_with_direct_tcp(self):
        sockets=[socket.socket(),socket.socket()]
        try:
            for item in sockets:item.bind(('127.0.0.1',0));item.listen()
            ports=[s.getsockname()[1] for s in sockets]
            code='''
import json,socket,sys
from external.codex_tool_gate import fence_network
fence_network([int(sys.argv[1])])
out=[]
for port in map(int,sys.argv[1:]):
 try:
  s=socket.create_connection(('127.0.0.1',port),timeout=1);s.close();out.append(True)
 except OSError:out.append(False)
print(json.dumps(out))
'''
            root=Path(__file__).resolve().parents[1]
            result=subprocess.run([sys.executable,'-c',code,*map(str,ports)],
                env={**os.environ,'PYTHONPATH':str(root/'src/backend')},capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),[True,False])
        finally:
            for item in sockets:item.close()
