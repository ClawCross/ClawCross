"""Independent group process: relay protocol and compatibility for existing local groups."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
from contextlib import asynccontextmanager
import os

from dotenv import load_dotenv
from common.runtime_paths import ENV_FILE, DATA_DIR
load_dotenv(ENV_FILE)

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from groups.config import service_key
from groups.relay_api import local_control, relay_router
from groups.relay_store import RelayStore


class LocalAgentClient:
    def __init__(self):
        self.busy = set()
        self.tasks = set()

    def is_busy(self, agent):
        return (agent.owner, agent.agent_id) in self.busy

    async def inbox(self, agent, msg, *, context, mode=None, on_complete=None):
        import httpx
        from agents.client import AgentClient
        from agents.messages import DeliveryReceipt
        client = AgentClient(agent.owner)
        async with httpx.AsyncClient(timeout=20, trust_env=False) as http:
            response = await http.post(client.base_url + '/v1/agents/' + agent.agent_id + '/inbox',
                headers=client._auth(), json={'text': msg.text, 'attachments': msg.attachments,
                                             'context': context, 'mode': mode, 'inbox_sender': msg.sender,
                                             'inbox_summary': msg.summary[:256]})
        if response.status_code >= 400 or not response.json().get('accepted'):
            return DeliveryReceipt(accepted=False, error='本地 agent 暂未接受群消息')
        key = (agent.owner, agent.agent_id)
        self.busy.add(key)
        async def watch():
            try:
                for _ in range(120):
                    await asyncio.sleep(1)
                    async with httpx.AsyncClient(timeout=5, trust_env=False) as http:
                        status = await http.get(client.base_url + '/v1/agents/' + agent.agent_id, headers=client._auth())
                    if status.status_code != 200 or status.json().get('status', {}).get('state') != 'running':
                        break
            except httpx.HTTPError:
                pass
            finally:
                self.busy.discard(key)
                if on_complete:
                    on_complete(None)
        task = asyncio.create_task(watch())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return DeliveryReceipt(accepted=True)


def create_app(*, data_dir=None, control_key=None, legacy=True):
    root = Path(data_dir or DATA_DIR)
    key = control_key or service_key()
    store = RelayStore(root / 'group-relay.db')
    adapter = LocalAgentClient()

    @asynccontextmanager
    async def lifespan(app):
        yield
        for task in adapter.tasks:
            task.cancel()
        await asyncio.gather(*adapter.tasks, return_exceptions=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.relay_store = store

    @app.middleware('http')
    async def limits(request: Request, call_next):
        if request.url.path.startswith('/local') and not local_control(request, key):
            return JSONResponse({'detail': '本机接口需要机器凭证'}, status_code=403)
        size, chunks = 0, []
        async for chunk in request.stream():
            size += len(chunk)
            if size > 2 * 1024 * 1024:
                return JSONResponse({'detail': '消息大小超过 2 MiB'}, status_code=413)
            chunks.append(chunk)
        request._body = b''.join(chunks)
        return await call_next(request)

    app.include_router(relay_router(store, key))
    if legacy:
        from agents.store import get_store
        from groups.conversations import Conversations
        from groups.routes import create_groups_router
        from groups.service import GroupService
        from groups.store import ConversationStore, default_db_path
        from teams.store import get_team_store
        agents = get_store()
        teams = get_team_store(agents)
        service = GroupService(Conversations(ConversationStore(default_db_path()), agents, adapter), names=teams.address)
        router = create_groups_router(internal_token=os.getenv('INTERNAL_TOKEN', ''), verify_password=lambda u, p: False, service=service)
        app.include_router(router, prefix='/local')
        @app.post('/local/groups/_forget')
        async def forget(body: dict, request: Request):
            from agents.routes import authenticate
            user = authenticate(request.headers.get('authorization'), internal_token=os.getenv('INTERNAL_TOKEN', ''), verify_password=lambda u, p: False)
            service.store.forget(user, str(body.get('agent_id', '')))
            return {'ok': True}
    return app


if __name__ == '__main__':
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description='ClawCross 群聊服务器（无需 LLM 或 agent runtime）')
    parser.add_argument('--host', default=os.getenv('GROUP_SERVER_HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.getenv('PORT_GROUPS', '51203')))
    parser.add_argument("--relay-only", action="store_true", help="只运行网络群服务，不挂载旧本机群兼容接口")
    parser.add_argument("--data-dir", help="指定群服务器数据目录")
    args = parser.parse_args()
    uvicorn.run(create_app(data_dir=args.data_dir, legacy=not args.relay_only), host=args.host, port=args.port, ws_max_size=2 * 1024 * 1024, limit_concurrency=256)
