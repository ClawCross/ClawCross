"""Group history access, stable identity, reference validation and private QR payloads."""
import asyncio
import base64
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/backend'))
sys.path.insert(0,str(ROOT/'src'))
from groups.relay_store import RelayStore, RelayError
from groups.server import create_app
from groups.client import ClientStore, GroupClient, ClientError
from groups.store import ConversationStore, human
from groups.conversations import Conversations, message_card
from groups.service import GroupService, Forbidden
from frontend.proxies.group_guests import register_guest_routes
from frontend.invite_qr import invitation_qr
from fastapi.testclient import TestClient
from flask import Flask
import qrcode


class GroupChatFeatures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = RelayStore(Path(self.temp.name)/'group-relay.db')
        self.host = self.store.create(title='Friends',node_id='host',user_id='alice',display_name='Alice')
        self.gid = self.host['group']['group_id']
        self.guest = self.store.guest_join(self.store.guest_invite(self.host['token'])['invite'],'Bob','password123')

    def test_rename_keeps_id_history_members_and_notifies_clients(self):
        original = self.store.post(self.host['token'],content='Before rename')['message']
        with self.assertRaises(RelayError): self.store.manage(self.guest['token'],'patch',{'title':'Hijacked'})
        self.store.manage(self.host['token'],'patch',{'title':'新群名'})
        changed = self.store.detail(self.host['token'])
        self.assertEqual(changed['group_id'],self.gid)
        self.assertEqual(len(changed['members']),2)
        self.assertEqual(self.store.messages(self.guest['token'])[0]['id'],original['id'])
        self.assertEqual(self.store.guest_state(self.guest['token'])['title'],'新群名')
        self.assertTrue(any(event['kind']!='message' for event in self.store.events(self.host['token'],original['id'])['events']))

    def test_history_search_pagination_literals_and_group_isolation(self):
        first = self.store.post(self.host['token'],content='old marker %_\\')['message']
        # More than the UI's recent 100 messages: searching is a server operation.
        for i in range(105): self.store.post(self.host['token'],content=f'marker {i}')
        found = self.store.search_messages(self.guest['token'],'%_\\')
        self.assertEqual([m['id'] for m in found['messages']],[first['id']])
        ids=[]; before=0
        while True:
            page = self.store.search_messages(self.guest['token'],'marker',before,50)
            ids.extend(m['id'] for m in page['messages']); before=page['next_before_id']
            if not before: break
        self.assertEqual(len(ids),106); self.assertEqual(len(set(ids)),106)
        self.assertEqual(ids,sorted(ids,reverse=True))
        other = self.store.create(title='Other',node_id='host',user_id='alice',display_name='Alice')
        self.store.post(other['token'],content='secret marker')
        self.assertEqual(len(self.store.search_messages(other['token'],'marker')['messages']),1)
        self.assertEqual(len(self.store.search_messages(self.host['token'],'Alice')['messages']),50)
        for query in ('', 'x'*121):
            with self.assertRaises(RelayError): self.store.search_messages(self.host['token'],query)

    def test_reply_snapshot_is_authoritative_and_scoped(self):
        source=self.store.post(self.host['token'],content='<script>original</script>'+'x'*600)['message']
        reply=self.store.post(self.guest['token'],content='Answer',reply_to=source['id'])['message']
        self.assertEqual(reply['reply'],{'id':source['id'],'sender_name':'Alice','content':source['content'][:500]})
        self.assertEqual(self.store.guest_state(self.guest['token'])['messages'][-1]['reply'],reply['reply'])
        other=self.store.create(title='Other',node_id='host',user_id='alice',display_name='Alice')
        for invalid in (source['id'],0,True):
            with self.assertRaises(RelayError): self.store.post(other['token'],content='cross group',reply_to=invalid)

    def test_search_api_authentication_guest_privacy_and_revoke(self):
        self.store.post(self.host['token'],content='find me')
        with TestClient(create_app(data_dir=self.temp.name,control_key='key',legacy=False)) as api:
            self.assertEqual(api.get('/relay/search',params={'query':'find'}).status_code,401)
            headers={'Authorization':'Bearer '+self.guest['token']}
            response=api.get('/relay/guest/search',params={'query':'find'},headers=headers)
            self.assertEqual(response.status_code,200)
            self.assertNotIn('sender_connection',response.text)
            self.assertNotIn('sender_agent_id',response.text)
            self.assertEqual(api.get('/relay/guest/search',params={'query':'find'},headers={'Authorization':'Bearer '+self.host['token']}).status_code,403)
            self.store.manage(self.host['token'],'remove_member',{'principal':self.store.guest_identity(self.guest['token'])['principal']})
            self.assertEqual(api.get('/relay/guest/search',params={'query':'find'},headers=headers).status_code,401)

    def test_client_search_uses_server_not_cache_and_requires_owner(self):
        cache=ClientStore(Path(self.temp.name)/'client.db'); alias=cache.save('alice','http://127.0.0.1:51203',self.host)
        client=GroupClient(cache,Mock(),Mock())
        with patch.object(client,'request',return_value={'messages':[]}) as request:
            client.search_messages('alice',alias,'older',12,10)
            self.assertEqual(request.call_args.args[1:3],('GET','/search'))
            self.assertEqual(request.call_args.kwargs['params'],{'query':'older','before_id':12,'limit':10})
            with self.assertRaises(ClientError): client.search_messages('bob',alias,'older')

    def test_legacy_group_reference_and_search(self):
        store=ConversationStore(Path(self.temp.name)/'old.db'); conversations=Conversations(store,Mock(),Mock()); service=GroupService(conversations)
        one=store.create(kind='group',owner='alice',title='Old',members=[human('alice')]); other=store.create(kind='group',owner='bob',title='Other',members=[human('bob')])
        source,_=store.add_message(one.conv_id,human('alice'),'quoted keyword %_')
        reply,_=asyncio.run(conversations.post(one.conv_id,human('alice'),'reply',reply_to=source.id))
        self.assertEqual(message_card(conversations,reply)['reply']['content'],source.content)
        self.assertEqual(service.search_messages('alice',one.conv_id,'%_')['messages'][0]['id'],source.id)
        with self.assertRaises(Forbidden): service.search_messages('bob',one.conv_id,'keyword')
        with self.assertRaises(ValueError): asyncio.run(conversations.post(other.conv_id,human('bob'),'reply',reply_to=source.id))


class InvitationQRTests(unittest.TestCase):
    def test_svg_modules_encode_full_link_including_fragment(self):
        link='https://chat.example/group-guest#signed-invite-ticket_测试'
        svg=base64.b64decode(invitation_qr(link).split(',',1)[1]).decode()
        root=ElementTree.fromstring(svg)
        path=root.find('{http://www.w3.org/2000/svg}path').attrib['d']
        actual={(int(x),int(y)) for x,y in re.findall(r'M(\d+),(\d+)h1v1h-1z',path)}
        qr=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,border=4); qr.add_data(link); qr.make(fit=True)
        self.assertEqual(b''.join(chunk.data for chunk in qr.data_list),link.encode())
        self.assertEqual(actual,{(x,y) for y,row in enumerate(qr.get_matrix()) for x,value in enumerate(row) if value})
        self.assertNotIn('signed-invite',svg); self.assertNotIn('<script',svg)

    def test_existing_qr_reuses_link_without_rotating_invite(self):
        app=Flask(__name__);app.secret_key='test-key'
        register_guest_routes(app,port_agent=51200,internal_token='internal',public_base=lambda:'https://chat.example')
        browser=app.test_client()
        self.assertEqual(browser.post('/proxy_groups/rg_x/guest-qr',json={'url':'bad'}).status_code,401)
        with browser.session_transaction() as session: session['user_id']='alice'
        upstream=Mock(status_code=200);upstream.json.return_value={'server_url':'http://127.0.0.1:51203','invite':'a'*43}
        with patch('frontend.proxies.group_guests.requests.post',return_value=upstream) as post:
            data=browser.post('/proxy_groups/rg_x/guest-link',json={}).json
            response=browser.post('/proxy_groups/rg_x/guest-qr',json={'url':data['url']})
            self.assertEqual(response.status_code,200);self.assertEqual(response.json['qr'],data['qr']);self.assertEqual(post.call_count,1)
            self.assertEqual(browser.post('/proxy_groups/rg_x/guest-qr',json={'url':data['url'].replace('chat.example','evil.example')}).status_code,400)
            self.assertEqual(browser.post('/proxy_groups/rg_x/guest-qr',json=[]).status_code,400)
            ticket=data['url'].split('#',1)[1]
            transport=Mock();transport.request.return_value=Mock(status_code=200);transport.request.return_value.json.return_value={'messages':[]}
            with patch('frontend.proxies.group_guests.requests.Session') as factory:
                factory.return_value.__enter__.return_value=transport
                reply=browser.get('/group-guest-api/search?query=old&before_id=12&limit=10',headers={'X-Group-Invite':ticket,'X-Guest-Token':'guest-token'})
                self.assertEqual(reply.status_code,200);self.assertEqual(transport.request.call_args.kwargs['params'],{'query':'old','before_id':'12','limit':'10'})

if __name__=='__main__': unittest.main()
