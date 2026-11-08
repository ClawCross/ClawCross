"""Native session/list and session/load through ACP, without creating or prompting."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from acp import RequestError
from external.acp_direct import Lease, Peer, manager
from external.acpx import AcpxError
from external.native_replay import normalize_updates
from common.runtime_paths import STATE_DIR


async def inspect_native(owner, config, *, cursor='', cwd=None, session_id=None, runtime=None, agent=None):
    runtime = runtime or manager()
    key = ('native-catalog',owner,config['platform'])
    directory = STATE_DIR/'acp-catalogs'
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    async def inspect():
        async with runtime.inspections, runtime.lock(key):
            selected = SimpleNamespace(agent=agent or SimpleNamespace(owner=owner,agent_id='native-catalog',config=config,runtime={}),
                                       cwd=str(cwd or directory.resolve()),store=None,manager=runtime,key=key)
            peer = Peer(selected,None,Lease(key))
            try:
                async with asyncio.timeout(45):
                    await peer.open([])
                    if session_id:
                        if not peer.capabilities.load_session:
                            return {'status':'unavailable','detail':'Adapter cannot load existing sessions','messages':[]}
                        peer.sid, peer.replay = session_id, []
                        await peer.connection.load_session(cwd=str(cwd),session_id=session_id,mcp_servers=[])
                        await peer.connection._session_updates.wait(session_id)
                        if peer.replay_bytes>64*1024*1024:
                            return {'status':'error','detail':'Native history exceeds 64 MiB','messages':[]}
                        return {'status':'loaded','messages':normalize_updates(peer.replay)}
                    result = await peer.connection.list_sessions(cursor=cursor or None)
                    return {'sessions':[{'session_id':row.session_id,'cwd':row.cwd,'title':(row.title or '')[:160],
                                        'updated_at':row.updated_at} for row in result.sessions],
                            'next_cursor':result.next_cursor}
            except RequestError as exc:
                if exc.code == -32601:
                    return {'sessions':[],'next_cursor':None,'detail':'This adapter does not expose a native session list'}
                raise AcpxError(f'ACP native session query failed (code {exc.code}); no model prompt was sent') from None
            except (OSError,RuntimeError,TimeoutError) as exc:
                raise AcpxError(f'ACP native session query failed ({type(exc).__name__}); no model prompt was sent') from None
            finally:
                await peer.close()
    return await runtime.submit(inspect,key=key)


class NativeCatalog:
    def __init__(self, owner): self.owner = owner

    async def list_native_sessions(self, *, tool, cursor='', allow_install=False):
        # Preparation belongs to explicit CLI install-component, never listing.
        return await inspect_native(self.owner,{'platform':tool},cursor=cursor)
