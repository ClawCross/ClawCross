"""Import owned transport metadata without invoking the retired acpx CLI."""
import json
import os
from pathlib import Path

from external import session
from external.acpx import AcpxError


def snapshot(agent, *, root=None, check_owner=True):
    root = Path(root) if root else Path.home()/'.acpx'
    name, cwd = session.runtime_session(agent), session.native_workspace_cwd(agent)
    candidates = []
    for path in (root/'sessions').glob('*.json'):
        try:
            if path.stat().st_size > 16*1024*1024: continue
            row = json.loads(path.read_text())
            if row.get('name') != name or Path(row.get('cwd','')).resolve() != Path(cwd).resolve(): continue
            candidates.append((path.stat().st_mtime,row))
        except (OSError,ValueError): pass
    if not candidates: return None
    record = max(candidates,key=lambda item:item[0])[1]
    native = record.get('acp_session_id') or record.get('agent_session_id') or record.get('agentSessionId')
    if not native:
        raise AcpxError('旧会话没有可恢复的原生编号，历史已保留，请检查原生会话目录。')
    rid = record.get('acpx_record_id')
    # Never start a second controller over a still-running old queue owner.
    if rid and check_owner:
        import hashlib
        lock = root/'queues'/(hashlib.sha256(str(rid).encode()).hexdigest()[:24]+'.lock')
        try:
            owner = json.loads(lock.read_text())
            pid = owner.get('pid')
            if type(pid) is int and pid>1:
                try: os.kill(pid,0)
                except ProcessLookupError: pass
                except PermissionError: raise AcpxError('无法确认旧 ACP 进程已停止，原生会话已保留。') from None
                else: raise AcpxError('旧 ACP 进程仍持有会话，请先停止旧连接；原生记忆和历史已保留。')
        except (OSError,ValueError): pass
    return {'native_resume_id':str(native),'acp_cwd':str(record['cwd']),
            'direct_config_options':(record.get('acpx') or {}).get('config_options') or [],
            'acp_transport':'direct','legacy_record_id':rid}


def migrate(store, agent):
    if agent.runtime.get('acp_transport') == 'direct': return agent
    patch = {}
    if not agent.config.get('connection_id') and not agent.runtime.get('native_resume_id'):
        patch = snapshot(agent) or {}
        if not patch and agent.runtime.get('negotiation_sent'):
            raise AcpxError('找不到旧会话的原生编号，已保留历史，未创建替代会话。')
    if not agent.config.get('connection_id'):
        patch['acp_transport'] = 'direct'
        store.patch_runtime(agent.owner,agent.agent_id,patch)
    return store.require(agent.owner,agent.agent_id)
