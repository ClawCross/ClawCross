"""Optional user administration, distinct from an Agent's own group participation."""

import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

import json
import os
from typing import Literal
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

from common.runtime_paths import ENV_FILE
from agents.store import get_store
from teams.store import DEFAULT_TEAM, get_team_store, valid_team_name
from webot.mcp_tool_docs import DocumentedFastMCP

load_dotenv(ENV_FILE)
mcp = DocumentedFastMCP('User Administration')


class TeamFields(BaseModel):
    model_config=ConfigDict(extra='forbid')
    agent:str|None=None
    name:str|None=None
    platform:str|None=None
    creation_template:Literal['chat','group','personal','admin']|None=None
    role:str|None=None
    is_lead:bool|None=None
    tag:str|None=None
    persona:str|None=None
    emoji:str|None=None
    name_zh:str|None=None
    name_en:str|None=None
    tools:list[str]|None=None
    kind:Literal['yaml','python','all']|None=None
    content:str|None=None
    description:str|None=None
    text:str|None=None
    cron:str|None=None
    schedule_type:Literal['cron','once']|None=None
    run_at:str|None=None
    task_id:str|None=None


class GroupFields(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str|None=None
    agents:list[str]|None=None
    agent:str|None=None
    principal:str|None=None
    muted:bool|None=None
    nickname:str|None=None
    enabled:bool|None=None


class AlarmFields(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text:str|None=None
    cron:str|None=None
    schedule_type:Literal['cron','once']|None=None
    run_at:str|None=None
    team:str|None=None
    task_id:str|None=None


def operation_data(data):
    if isinstance(data,BaseModel):
        return {key:value for key,value in data.model_dump(exclude_unset=True).items() if value is not None or key=='tools'}
    if data is None:return {}
    if not isinstance(data,dict):raise ValueError('data 必须是一个对象')
    return data


def public_data(value):
    if isinstance(value,dict):
        return {key:public_data(item) for key,item in value.items() if key.lower() not in
                {'api_key','api_url','base_url','headers','authorization','meta','token','password','secret','password_hash','salt'}
                and not key.upper().endswith(('_API_KEY','_TOKEN','_PASSWORD','_SECRET'))}
    if isinstance(value,list):
        return [public_data(item) for item in value]
    return value


def result(data=None,error=''):
    return json.dumps({'ok':not bool(error),**({'error':error} if error else {'data':public_data(data)})},ensure_ascii=False,default=str)


def calling_agent(owner,source):
    if not source:
        raise ValueError('缺少运行时 Agent 身份')
    get_store().require(owner,source)


def fields(data,allowed):
    if not isinstance(data,dict):
        raise ValueError('data 必须是一个对象')
    unexpected=set(data)-set(allowed)
    if unexpected:
        raise ValueError('不支持的参数：'+', '.join(sorted(unexpected)))
    for key,value in data.items():
        if key in {'enabled','is_lead','muted'}:
            if not isinstance(value,bool):raise ValueError(key+' 必须是布尔值')
        elif key in {'tools','agents'}:
            if key=='tools' and value is None:continue
            if not isinstance(value,list) or not all(isinstance(item,str) and item.strip() for item in value):
                raise ValueError(key+' 必须是字符串列表')
        elif not isinstance(value,str):
            raise ValueError(key+' 必须是字符串')
    return data


def required(data,key):
    value=data.get(key,'').strip()
    if not value:raise ValueError('缺少 '+key)
    return value


def team_workflow_file(store,owner,team,name,kind):
    if kind not in {'yaml','python'}:raise ValueError('kind 必须是 yaml 或 python')
    if not name or name in {'.','..'} or Path(name).name!=name or '/' in name or '\\' in name:
        raise ValueError('name 只能是工作流文件名')
    extensions=('.yaml','.yml') if kind=='yaml' else ('.py',)
    if not name.endswith(extensions):
        if Path(name).suffix:raise ValueError('工作流扩展名与 kind 不符')
        name+=extensions[0]
    root=(store.folder(owner,team)/'oasis'/('yaml' if kind=='yaml' else 'python')).resolve()
    path=(root/name).resolve()
    if path.parent!=root:raise ValueError('工作流必须位于选定 Team 的目录中')
    return path


def team_workflows(store,owner,team,action,data):
    fields(data,['kind'] if action=='list_workflows' else ['name','kind','content','description'] if action=='save_workflow' else ['name','kind'])
    kind=data.get('kind','all' if action=='list_workflows' else 'yaml')
    if action=='list_workflows':
        if kind not in {'yaml','python','all'}:raise ValueError('kind 必须是 yaml、python 或 all')
        rows=[]
        for language in (('yaml','python') if kind=='all' else (kind,)):
            folder=store.folder(owner,team)/'oasis'/language
            if not folder.is_dir():continue
            for file in sorted(folder.iterdir()):
                if file.suffix not in ({'.yaml','.yml'} if language=='yaml' else {'.py'}):continue
                path=team_workflow_file(store,owner,team,file.name,language)
                if path.is_file():rows.append({'name':path.name,'kind':language,'team':team})
        return {'team':team,'workflows':rows}
    path=team_workflow_file(store,owner,team,required(data,'name'),kind)
    if action=='get_workflow':return {'team':team,'name':path.name,'kind':kind,'content':path.read_text(encoding='utf-8')}
    if action=='delete_workflow':
        path.unlink();return {'team':team,'deleted':path.name}
    required(data,'content');content=data['content']
    if kind=='yaml':
        import yaml
        schedule=yaml.safe_load(content)
        if not isinstance(schedule,dict) or 'plan' not in schedule:raise ValueError('YAML 工作流必须包含 plan；先查询 get_workflow_rules')
        if data.get('description'):content='# '+data['description'].replace('\n',' ')+'\n'+content
    else:
        import ast,textwrap
        ast.parse('async def __workflowpy_main__():\n'+textwrap.indent(content,'    '))
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(content if content.endswith('\n') else content+'\n',encoding='utf-8')
    return {'team':team,'name':path.name,'kind':kind,'path':str(path),'saved':True}


async def local_request(owner,method,path,body=None,*,scheduler=False):
    token=os.getenv('INTERNAL_TOKEN','')
    port=os.getenv('PORT_SCHEDULER' if scheduler else 'PORT_AGENT','51201' if scheduler else '51200')
    async with httpx.AsyncClient(timeout=20,trust_env=False) as client:
        response=await client.request(method,f'http://127.0.0.1:{port}'+path,
            headers={'Authorization':f'Bearer {token}:{owner}','X-Internal-Token':token},json=body)
    payload=response.json()
    if not response.is_success:
        raise ValueError(str(payload.get('detail') or payload.get('error') or '管理请求失败'))
    return payload


@mcp.tool()
async def manage_team(username:str,action:Literal['list','get','create','rename','delete','create_agent','add_member','update_member','remove_member','list_personas','save_persona','delete_persona','update_agent','list_workflows','get_workflow','save_workflow','delete_workflow','list_alarms','create_alarm','update_alarm','delete_alarm'],
                      team:str='',data:TeamFields|None=None,source_session:str='')->str:
    """管理 Team：成员、人设、工作流、闹钟和 Agent 配置；创建、改名、删除；不必加入。
    Manage the user's Teams without requiring this Agent to be a member.
    Uses the Agent's ordinary tool table; mutations require review.
    Group participation uses join_group/leave_group, not this tool.
    create uses team; rename data={name}; member operations data={agent,role?,is_lead?,tag?}.
    create_agent data={name,platform?,persona?,creation_template?,tools?,role?}; default personal template.
    save_persona data={tag,name,persona,emoji?}; delete_persona data={tag}.
    update_agent data={agent,name?,persona?,tools?} edits a Team member's non-secret settings.
    list_workflows data={kind?:yaml|python|all}; get/delete_workflow data={name,kind?:yaml|python}.
    save_workflow data={name,content,kind?:yaml|python,description?}; query get_workflow_rules first.
    Workflow files use this Team's existing OASIS directory; saving does not start a workflow.
    list_alarms data={agent?}; create_alarm data={agent,text,cron?,schedule_type?,run_at?}; delete_alarm data={task_id}.
    update_alarm data={task_id,agent?,text?,cron?,schedule_type?,run_at?} changes an existing Team clock without changing its ID.
    Alarms are scoped to this Team; creation targets a member. Use manage_agent_alarms for another owned Agent outside it.
    Never supplies API keys or secret connector settings; use private configuration forms.

    :param username: Runtime user identity; injected.
    :param action: Team operation.
    :param team: Actual Team name; user space is a virtual list and cannot be modified.
    :param data: Operation fields described above; other user IDs and file paths are rejected.
    :param source_session: Calling Agent; injected.
    """
    try:
        calling_agent(username,source_session);data=operation_data(data)
        store=get_team_store(get_store())
        from teams.routes import team_card,member_card
        if action=='list':return result([team_card(store,username,name) for name in store.teams(username) if name!=DEFAULT_TEAM])
        if not valid_team_name(team) or team==DEFAULT_TEAM:raise ValueError('请指定真实 Team；用户空间仅用于查看')
        if action=='create':
            fields(data,[])
            if store.exists(username,team):raise ValueError('Team 已存在')
            store.create(username,team);return result(team_card(store,username,team))
        store.require(username,team)
        if action=='get':return result(team_card(store,username,team))
        if action in {'list_workflows','get_workflow','save_workflow','delete_workflow'}:
            return result(team_workflows(store,username,team,action,data))
        if action=='create_alarm':
            fields(data,['agent','text','cron','schedule_type','run_at'])
            target=get_store().require(username,required(data,'agent'));store.member(username,team,target.agent_id)
            required(data,'text')
            return result(await local_request(username,'POST','/tasks',{'user_id':username,'agent':target.agent_id,'team':team,
                **{key:value for key,value in data.items() if key!='agent'}},scheduler=True))
        if action in {'list_alarms','delete_alarm','update_alarm'}:
            fields(data,['agent'] if action=='list_alarms' else ['task_id','agent','text','cron','schedule_type','run_at'] if action=='update_alarm' else ['task_id'])
            tasks=await local_request(username,'GET','/tasks',scheduler=True)
            scoped=[task for task in tasks if task.get('user_id')==username and task.get('team')==team]
            if action=='list_alarms':return result([task for task in scoped if not data.get('agent') or task.get('agent')==data['agent']])
            task_id=required(data,'task_id')
            existing=next((task for task in scoped if task.get('task_id')==task_id),None)
            if existing is None:raise ValueError('闹钟不属于当前用户的这个 Team')
            if action=='update_alarm':
                body={key:existing.get(key,'') for key in ('agent','text','cron','schedule_type','run_at')}
                body.update({key:value for key,value in data.items() if key!='task_id'})
                target=get_store().require(username,body['agent']);store.member(username,team,target.agent_id)
                return result(await local_request(username,'PATCH','/tasks/'+quote(task_id,safe=''),{'user_id':username,'team':team,**body},scheduler=True))
            return result(await local_request(username,'DELETE','/tasks/'+quote(task_id,safe=''),scheduler=True))
        if action=='rename':
            fields(data,['name']);store.rename(username,team,required(data,'name'));return result({'team':data['name']})
        if action=='delete':
            fields(data,[]);store.delete(username,team);return result({'deleted':team,'agents_preserved':True})
        if action=='create_agent':
            fields(data,['name','platform','persona','creation_template','tools','role']);required(data,'name')
            card=await local_request(username,'POST','/v1/agents',{'creation_template':'personal',**{key:value for key,value in data.items() if key!='role'}})
            return result(member_card(store.add(username,team,card['agent_id'],role=data.get('role',''))))
        if action in {'add_member','update_member','remove_member','update_agent'}:
            fields(data,['agent','role','is_lead','tag'] if action!='update_agent' else ['agent','name','persona','tools'])
            target=get_store().require(username,required(data,'agent'))
            if action=='add_member':return result(member_card(store.add(username,team,target.agent_id,role=data.get('role',''),is_lead=data.get('is_lead',False),extra={'tag':data['tag']} if data.get('tag') else None)))
            store.member(username,team,target.agent_id)
            if action=='update_member':return result(member_card(store.update(username,team,target.agent_id,role=data.get('role'),is_lead=data.get('is_lead'),tag=data.get('tag'))))
            if action=='remove_member':store.remove(username,team,target.agent_id);return result(team_card(store,username,team))
            return result(await local_request(username,'PATCH','/v1/agents/'+quote(target.agent_id,safe=''),{'name':data.get('name'),'settings':{key:data[key] for key in ('persona','tools') if key in data}}))
        from oasis.experts import load_team_experts,add_team_expert,update_team_expert,delete_team_expert
        if action=='list_personas':return result(load_team_experts(username,team))
        if action=='delete_persona':fields(data,['tag']);return result(delete_team_expert(username,team,required(data,'tag')))
        if action!='save_persona':raise ValueError('不支持的 Team 操作')
        fields(data,['tag','name','persona','emoji','name_zh','name_en'])
        tag=required(data,'tag');existing=any(row.get('tag')==tag for row in load_team_experts(username,team))
        return result(update_team_expert(username,team,tag,data) if existing else add_team_expert(username,team,data))
    except Exception as error:return result(error=str(error))


@mcp.tool()
async def manage_group(username:str,action:Literal['list','get','create','rename','delete','add_member','remove_member','member_settings','set_primary','external_access'],
                       group_id:str='',data:GroupFields|None=None,source_session:str='')->str:
    """管理群聊：建群、改名、拉入 Agent、踢成员、暂停/恢复联网；以本用户群主权限操作。
    Administer groups as the owning user; this Agent need not join the group.
    Uses the Agent's ordinary tool table and review. Does not grant administration of other users' groups.
    create data={title,agents?}; rename data={title}; members data={agent} or {principal}.
    member_settings data={principal,muted?,nickname?}; set_primary data={agent}.
    external_access data={enabled}: pause/resume networking while preserving members and history.
    Joining/leaving yourself remains in join_group/leave_group.
    “拉人”在此只添加本用户已有 Agent；人类使用邀请链接主动加入。
    “踢人”可移除 Agent 或人类成员，不能撤掉群主本人。

    :param username: Runtime user identity; injected.
    :param action: Group administration operation.
    :param group_id: Local group ID returned by list or create.
    :param data: Fields for the chosen operation; arbitrary URLs and user IDs are rejected.
    :param source_session: Calling Agent; injected.
    """
    try:
        calling_agent(username,source_session);data=operation_data(data)
        if action=='list':return result(await local_request(username,'GET','/groups'))
        if action=='create':
            fields(data,['title','agents']);required(data,'title');return result(await local_request(username,'POST','/groups',data))
        if not group_id:raise ValueError('缺少 group_id，请先 list 查询')
        path='/groups/'+quote(group_id,safe='')
        card=await local_request(username,'GET',path)
        if action=='get':return result(card)
        if card.get('owner')!=username:raise ValueError('当前用户不是群主，不能管理此群')
        if action=='rename':fields(data,['title']);required(data,'title');return result(await local_request(username,'PATCH',path,data))
        if action=='delete':fields(data,[]);return result(await local_request(username,'DELETE',path))
        if action=='add_member':fields(data,['agent']);required(data,'agent');return result(await local_request(username,'POST',path+'/members',data))
        if action in {'remove_member','member_settings'}:
            fields(data,['principal'] if action=='remove_member' else ['principal','muted','nickname']);principal=required(data,'principal')
            return result(await local_request(username,'DELETE' if action=='remove_member' else 'PATCH',path+'/members/'+quote(principal,safe=''),{key:value for key,value in data.items() if key!='principal'} if action=='member_settings' else None))
        if action=='set_primary':fields(data,['agent']);required(data,'agent');return result(await local_request(username,'PUT',path+'/primary',data))
        if action!='external_access':raise ValueError('不支持的群管理操作')
        if 'enabled' not in data:raise ValueError('缺少 enabled')
        fields(data,['enabled']);return result(await local_request(username,'POST',path+'/external-access',data))
    except Exception as error:return result(error=str(error))


@mcp.tool()
async def manage_agent_alarms(username:str,action:Literal['list','create','delete'],target_agent:str='',data:AlarmFields|None=None,source_session:str='')->str:
    """管理本用户其他 Agent 的闹钟/提醒：创建、查看、删除；自己的闹钟使用 add_alarm。
    Manage alarms for other Agents owned by this user, using the ordinary tool table and review.
    For your own reminders use add_alarm/list_alarms/delete_alarm instead.
    create data={text,cron?,schedule_type?,run_at?,team?}; delete data={task_id}.

    :param username: Runtime user identity; injected.
    :param action: Alarm operation.
    :param target_agent: Owned Agent ID; required for every operation.
    :param data: Schedule fields or a task ID.
    :param source_session: Calling Agent; injected.
    """
    try:
        calling_agent(username,source_session);data=operation_data(data)
        target=get_store().require(username,target_agent)
        if action=='create':
            fields(data,['text','cron','schedule_type','run_at','team'])
            required(data,'text')
            return result(await local_request(username,'POST','/tasks',{'user_id':username,'agent':target.agent_id,**data},scheduler=True))
        tasks=await local_request(username,'GET','/tasks',scheduler=True)
        owned=[task for task in tasks if task.get('user_id')==username and task.get('agent')==target.agent_id]
        if action=='list':return result(owned)
        if action!='delete':raise ValueError('不支持的闹钟管理操作')
        fields(data,['task_id']);task_id=required(data,'task_id')
        if not any(task.get('task_id')==task_id for task in owned):raise ValueError('闹钟不属于这个用户和目标 Agent')
        return result(await local_request(username,'DELETE','/tasks/'+quote(task_id,safe=''),scheduler=True))
    except Exception as error:return result(error=str(error))


if __name__=='__main__':mcp.run(transport='stdio')
