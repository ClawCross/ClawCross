"""Initial Agent capabilities, without starting tools or installing components."""

import ast
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from webot.tool_capabilities import REMOVED_TOOLS

_PRIVATE = {'companion':True,'user_shared':False,'cli':True,'teams':True,'paths':[]}
_ISOLATED = {**_PRIVATE,'cli':False,'teams':False}
_GROUP_TOOLS = ['send_to_group','join_group','leave_group','list_agent_groups','get_group_details',
                'read_session_inbox','mark_session_inbox_read','get_current_time','read_file','write_file','list_files']
_PERSONAL_TOOLS = ['list_files','read_file','write_file','delete_file','run_command','background_command_io','cancel_background_command',
                  'web_search','web_fetch','list_sessions','search_sessions','get_session_details','set_session_title','read_session_inbox','mark_session_inbox_read',
                  'send_to_group','get_current_time','add_alarm','list_alarms','delete_alarm','spawn_subagent','list_subagents','send_subagent_message',
                  'get_subagent_history','cancel_subagent','delete_subagent','write_session_plan','read_session_plan','clear_session_plan',
                  'get_configuration','request_configuration','get_clawcross_help','manage_personality','usage_status','show_ui_panel','get_publicnet_info']
_TEMPLATES = {
    'chat': {'label':{'zh':'纯聊天','en':'Chat only'},'description':{'zh':'文字交流，无工具','en':'Conversation without tools'},
             'mode':'chat','security':'strict','tools':[],'workspaces':_ISOLATED},
    'group': {'label':{'zh':'群聊伙伴','en':'Group companion'},'description':{'zh':'参与群聊，严格隔离','en':'Group participation with strict isolation'},
              'mode':'auto','security':'strict','tools':_GROUP_TOOLS,'workspaces':_ISOLATED},
    'personal': {'label':{'zh':'私人助手','en':'Personal assistant'},'description':{'zh':'文件、搜索、命令，AI 审核','en':'Files, search and commands with AI review'},
                 'mode':'auto','security':'standard','tools':_PERSONAL_TOOLS,'workspaces':_PRIVATE},
    'admin': {'label':{'zh':'管理员','en':'Administrator'},'description':{'zh':'全部管理工具，人工审核','en':'All management tools with human review'},
              'mode':'manual','security':'standard','tools':'all','workspaces':_PRIVATE},
}


@lru_cache(maxsize=1)
def builtin_tool_names():
    """Enumerate registered built-ins without importing or spawning MCP servers."""
    names = set()
    for path in (Path(__file__).parents[1] / 'webot/mcp').glob('*.py'):
        for node in ast.parse(path.read_text(encoding='utf-8')).body:
            if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                function = decorator.func if isinstance(decorator,ast.Call) else decorator
                if isinstance(function,ast.Attribute) and isinstance(function.value,ast.Name) and function.value.id == 'mcp' and function.attr == 'tool':
                    name = node.name
                    if isinstance(decorator,ast.Call):
                        name = next((kw.value.value for kw in decorator.keywords if kw.arg=='name' and isinstance(kw.value,ast.Constant)),name)
                    names.add(name)
    return sorted(names - REMOVED_TOOLS)


def creation_template(template_id):
    if template_id not in _TEMPLATES:
        raise ValueError('Unknown Agent creation template')
    row = {'id':template_id,**deepcopy(_TEMPLATES[template_id])}
    row['external_compatible'] = template_id not in {'chat','group'}
    if row['tools'] == 'all':
        row['tools'] = builtin_tool_names()
    row['approval'] = {'mode':row['mode'],'command_sandbox':'auto','sandbox_security':row['security']}
    return row


def catalog():
    return [creation_template(name) for name in _TEMPLATES]


def save_initial_settings(owner, agent_id, template_id):
    from webot.runtime_settings import save_runtime_settings
    from webot.runtime_store import save_session_mode
    template = creation_template(template_id)
    save_runtime_settings(owner,session_id=agent_id,settings={'approval':template['approval']})
    save_session_mode(owner,agent_id,mode=template['mode'])
