"""Bounded local documentation lookup, loaded only when explicitly called."""
from __future__ import annotations

import re
from common.runtime_paths import PROJECT_ROOT

TOPICS = {
    'overview': ('功能概览', 'overview.md', '了解 Agent、对话、群聊、团队与工作流'),
    'configuration': ('配置与私密表单', 'configuration-assistant.md', '设置含义、密钥填写与审批登记'),
    'channels': ('消息平台与通知', 'channel-setup.md', '机器人连接、访问身份与通知收件人'),
    'sandbox': ('命令沙盒与审核', 'command-isolation-plan.md', '普通/严格模式、权限、进程和平台限制'),
    'agents': ('外部 Agent 与会话导入', 'native-sessions.md', 'Codex/Claude 原生会话的登记与恢复'),
    'management': ('Agent 模板与管理工具', 'agent-creation-and-management.md', '新建模板、可选管理权限与自己的闹钟'),
    'mcp': ('Agent MCP 接口方案', 'agent-mcp-interface-design.md', '每个 Agent 的连接、凭证、工具范围与实现状态'),
    'groups': ('群聊与分享', 'group-network.md', '群服务器、加入凭证、人类参与者'),
    'teams': ('团队与工作流', 'team-creator.md', '协作角色与 ClawCross Creator'),
    'skills': ('工作区与技能', 'workspaces-and-skills.md', '共享技能、干净工作区与严格模式'),
    'cli': ('命令行', 'cli.md', 'CLI 命令和用法'),
    'launch': ('启动与组件', 'npm-packaging.md', 'npm 入口、Python 启动和显式组件安装'),
}


def lookup(topic='', query='', section=''):
    if not topic and not query:
        return {'topics':[{'id':key,'title':row[0],'summary':row[2]} for key,row in TOPICS.items()]}
    if topic and topic not in TOPICS: raise ValueError('Unknown help topic')
    terms = [term.casefold() for term in re.split(r'\s+',query.strip()) if term][:8]
    selected = [(topic,TOPICS[topic])] if topic else list(TOPICS.items())
    results=[]; budget=8000
    for key,(title,filename,summary) in selected:
        path=PROJECT_ROOT/'docs'/filename
        try: body=path.read_text(encoding='utf-8')
        except FileNotFoundError: continue
        blocks=re.split(r'(?m)(?=^#{1,4} )',body)
        headings=[block.split('\n',1)[0].lstrip('# ').strip() for block in blocks if block.strip()]
        candidates=[]
        for block in blocks:
            if not block.strip():continue
            heading=block.split('\n',1)[0].lstrip('# ').strip()
            if section and section.casefold() not in heading.casefold():continue
            if terms and not all(term in block.casefold() for term in terms):continue
            candidates.append(block.strip())
        if not candidates:continue
        content='\n\n'.join(candidates); cap=min(6000,budget)
        results.append({'topic':key,'title':title,'source':'docs/'+filename,'sections':headings[:60],
                        'content':content[:cap],'truncated':len(content)>cap})
        budget-=min(len(content),cap)
        if budget<=0 or len(results)>=3:break
    return {'results':results,'hint':'指定 topic 与 section 只读取相关章节；query 支持关键词。不读取用户文件，不联网。'}
