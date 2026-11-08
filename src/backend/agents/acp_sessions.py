"""Owned ACP session tracking. Listing is passive and needs no external client."""
from fastapi import HTTPException


async def list_all_sessions(user_id, acpx_bin=None):
    from agents.store import ACPX, get_store
    from external.session import runtime_session, native_workspace_cwd
    from external.acp_migration import snapshot
    rows = []
    for agent in get_store().list(user_id):
        if agent.driver != ACPX: continue
        legacy = snapshot(agent,check_owner=False) if not agent.runtime.get('native_resume_id') and not agent.config.get('connection_id') else None
        rows.append({'platform':agent.platform,'name':runtime_session(agent),'agent_id':agent.agent_id,
                     'agent_name':agent.name,'session_id':agent.runtime.get('native_resume_id') or (legacy or {}).get('native_resume_id'),
                     'cwd':native_workspace_cwd(agent),'closed':agent.runtime.get('acp_connection_closed',False),
                     'connection_id':agent.config.get('connection_id',''),'transport':'direct'})
    # Keep the old response field for existing frontends; values are ACP records.
    return {'status':'success','acpx_sessions':rows,'sessions':rows}


async def close_acp_session(platform,session_name,cwd='',*,user_id,acpx_bin=None):
    from agents.store import ACPX, canonical_platform, get_store
    from external.session import runtime_session, control_lock
    from external.acp_direct import DirectAdapter
    store = get_store()
    owned = next((agent for agent in store.list(user_id) if agent.driver == ACPX
                  and canonical_platform(agent.platform) == canonical_platform(platform)
                  and runtime_session(agent) == session_name),None)
    if owned is None: raise HTTPException(404,'No owned ACP session')
    async with control_lock(store,owned):
        await DirectAdapter(owned,store=store).close_session()
        store.patch_runtime(owned.owner,owned.agent_id,{'acp_connection_closed':True})
    return {'status':'success','platform':owned.platform,'session_name':session_name}
