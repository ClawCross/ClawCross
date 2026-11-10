"""Portrait pictures stay out of group snapshots and become real model image parts."""
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from common.user_portrait import image_records, read_portrait
from groups.relay_store import RelayError, RelayStore


@pytest.fixture
def image():
    raw=(Path(__file__).parent/'browser/fixtures/group-invite-qr.png').read_bytes()
    return {'name':'portrait.png','url':'data:image/png;base64,'+base64.b64encode(raw).decode()}


def test_group_portraits_use_authenticated_image_references_and_preserve_old_text(tmp_path,image):
    store=RelayStore(tmp_path/'groups.db')
    host=store.create(title='Pictures',node_id='host',user_id='alice',display_name='Alice')
    token=host['token']
    store.manage(token,'profile',{'bio':'原有简介','location':'上海'})
    group=store.manage(token,'profile',{'images':[image]})
    member=group['members'][0]
    assert member['profile']['text']=='简介：原有简介\n地区：上海'
    picture=member['profile']['images'][0]
    assert 'url' not in picture and 'base64' not in json.dumps(group)
    assert store.profile_image(token,member['principal'],picture['id'])=={'url':image['url']}
    group=store.manage(token,'profile',{'text':'自由描述自己','images':[picture]})
    assert group['members'][0]['profile']['text']=='自由描述自己'
    other=store.create(title='Other',node_id='host',user_id='alice',display_name='Alice')
    with pytest.raises(RelayError):store.profile_image(other['token'],member['principal'],picture['id'])
    store.manage(token,'profile',{'images':[]})
    with pytest.raises(RelayError):store.profile_image(token,member['principal'],picture['id'])


def test_picture_validation_rejects_remote_urls_svg_and_unknown_references(image):
    assert len(image_records([image,image]))==1
    for values in [[{'url':'https://example.com/picture.png'}],[{'url':'data:image/svg+xml;base64,PHN2Zy8+'}],[{'id':[]}],[image]*5]:
        with pytest.raises(ValueError):image_records(values)


def test_private_portrait_save_read_and_prompt_block_keep_image_data_out_of_text(tmp_path,monkeypatch,image):
    from frontend import server as front
    from webot import skills
    monkeypatch.setattr(front,'USER_FILES_DIR',tmp_path)
    monkeypatch.setattr(skills,'USER_FILES_DIR',tmp_path)
    client=front.app.test_client()
    with client.session_transaction() as state:state['user_id']='alice'
    saved=client.put('/proxy_user_profile',json={'profile':'画像文字','images':[image]})
    assert saved.status_code==200
    assert client.get('/proxy_user_profile').json['images'][0]['url']==image['url']
    assert client.put('/proxy_user_profile',json={'profile':'只改文字'}).status_code==200
    block=skills.build_user_profile_block('alice')
    assert '只改文字' in block and 'portrait.png' in block and 'base64' not in block
    with client.session_transaction() as state:state['user_id']='bob'
    assert client.get('/proxy_user_profile').json['images']==[]
    with client.session_transaction() as state:state.clear()
    assert client.get('/proxy_user_profile').status_code==401


def test_internal_image_context_is_multimodal_and_respects_profile_isolation(tmp_path,monkeypatch,image):
    from webot.engine import agent as internal
    from webot import skills
    monkeypatch.setattr(skills,'USER_FILES_DIR',tmp_path)
    folder=tmp_path/'alice';folder.mkdir()
    (folder/'user_profile.images.json').write_text(json.dumps(image_records([image])))
    engine=internal.TeamAgent.__new__(internal.TeamAgent)
    monkeypatch.setattr(engine,'_find_internal_session_meta',lambda *a:{})
    monkeypatch.setattr('common.model_capabilities.model_capabilities',lambda *a:{'image_inputs':True})
    turn=SimpleNamespace(user_id='alice',session_id='session',is_subagent=False)
    context=engine._portrait_context(turn,'vision-model')
    assert context[0].content[1]['type']=='image_url'
    assert context[0].content[1]['image_url']['url']==image['url']
    monkeypatch.setattr('common.model_capabilities.model_capabilities',lambda *a:{'image_inputs':False})
    assert isinstance(engine._portrait_context(turn,'text-model')[0].content,str)
    monkeypatch.setattr(engine,'_find_internal_session_meta',lambda *a:{'creation_template':'group'})
    assert engine._portrait_context(turn,'vision-model')==[]


def test_external_portrait_images_are_sent_once_and_resent_after_reset(tmp_path,monkeypatch,image):
    from agents.messages import AgentMessage
    from agents.store import AgentStore, HTTP
    from external import session
    from webot import skills
    monkeypatch.setattr(skills,'USER_FILES_DIR',tmp_path)
    folder=tmp_path/'alice';folder.mkdir()
    (folder/'user_profile.images.json').write_text(json.dumps(image_records([image])))
    monkeypatch.setattr(session,'identity_sections',lambda agent:{'user_profile':'portrait-v1'})
    monkeypatch.setattr(session,'build_dynamic_context',lambda *a,**kw:{'identity_user_profile':kw['identity']['user_profile']})
    store=AgentStore(tmp_path/'agents.db');agent=store.create('alice',driver=HTTP,config={'api_url':'http://unused'})
    def prepare(agent):return session.prepare_turn(agent,AgentMessage(text='Hello'),context={},mode=None,enabled_tools=None,response_format=None)
    first=prepare(agent);assert len(first.profile_images)==1
    session.remember_turn(store,agent,first);agent=store.require('alice',agent.agent_id)
    assert prepare(agent).profile_images==[]
    session.forget(store,agent,new_session=True)
    assert len(prepare(store.require('alice',agent.agent_id)).profile_images)==1
