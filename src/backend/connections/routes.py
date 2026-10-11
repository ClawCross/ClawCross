"""Connection management uses the same authenticated user as Agent management."""
from fastapi import APIRouter, Header, HTTPException
from agents.routes import authenticate
from connections.ssh import get_connections, SSHError


def create_connections_router(*, internal_token, verify_password, agents, gateway, manager=None):
    router=APIRouter();manager=manager or get_connections()
    def owner(auth):return authenticate(auth,internal_token=internal_token,verify_password=verify_password)
    def require(user,connection_id):
        try:return manager.store.get(user,connection_id)
        except LookupError as exc:raise HTTPException(404,str(exc)) from exc
    def attached(user,connection_id):return [agent for agent in agents.list(user) if agent.config.get('connection_id')==connection_id]

    @router.get('/v1/connections')
    async def list_connections(authorization:str|None=Header(None)):
        user=owner(authorization)
        return {'data':[{**manager.card(user,row['id']),'agent_count':len(attached(user,row['id']))} for row in manager.store.list(user)]}

    @router.post('/v1/connections')
    async def create_connection(body:dict,authorization:str|None=Header(None)):
        user=owner(authorization)
        try:row=manager.store.save(user,body)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        return manager.card(user,row['id'])

    @router.patch('/v1/connections/{connection_id}')
    async def update_connection(connection_id:str,body:dict,authorization:str|None=Header(None)):
        user=owner(authorization);current=require(user,connection_id)
        bindings=attached(user,connection_id)
        if any(gateway.is_busy(agent) for agent in bindings):raise HTTPException(409,'连接上的 Agent 正在运行')
        if any(key in body and body[key]!=current[key] for key in ('host','username','port')) and bindings:
            raise HTTPException(409,'连接已有 Agent；更换设备请新建连接，避免丢失会话')
        try:value=manager.store.validate({key:val for key,val in current.items() if key!='id'}|body)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        await manager.disconnect(user,connection_id);manager.store.save(user,value,connection_id)
        return manager.card(user,connection_id)

    @router.post('/v1/connections/{connection_id}/connect')
    async def connect(connection_id:str,authorization:str|None=Header(None)):
        user=owner(authorization);current=require(user,connection_id)
        manager.store.save(user,{key:value for key,value in current.items() if key!='id'}|{'enabled':True},connection_id)
        try:return await manager.probe(user,connection_id)
        except (SSHError,TimeoutError,RuntimeError,ValueError) as exc:raise HTTPException(502,str(exc)) from exc

    @router.post('/v1/connections/{connection_id}/disconnect')
    async def disconnect(connection_id:str,authorization:str|None=Header(None)):
        user=owner(authorization);require(user,connection_id)
        if any(gateway.is_busy(agent) for agent in attached(user,connection_id)):raise HTTPException(409,'请先取消或等待 Agent 本轮结束')
        await manager.disconnect(user,connection_id)
        current=manager.store.get(user,connection_id)
        manager.store.save(user,{key:value for key,value in current.items() if key!='id'}|{'enabled':False},connection_id)
        return manager.card(user,connection_id)

    @router.delete('/v1/connections/{connection_id}')
    async def delete_connection(connection_id:str,authorization:str|None=Header(None)):
        user=owner(authorization);require(user,connection_id)
        if attached(user,connection_id):raise HTTPException(409,'请先移除使用此连接的 Agent')
        await manager.disconnect(user,connection_id);manager.store.delete(user,connection_id)
        return {'deleted':connection_id}

    return router
