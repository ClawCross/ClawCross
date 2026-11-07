// The API supplies category metadata; permissions remain on individual tools.
const TOOL_CATEGORY_LABELS = {
    groups: ['群聊与团队', 'Groups and teams'],
    files: ['文件与记忆', 'Files and memory'], commands: ['命令执行', 'Commands'], web: ['搜索与模型', 'Search and models'],
    sessions: ['会话与消息', 'Sessions and messages'], agents: ['Agent 与计划', 'Agents and plans'],
    workflows: ['OASIS 工作流', 'OASIS workflows'], skills: ['个性', 'Personality'],
    notifications: ['定时与通知', 'Schedules and notifications'], usage: ['用量与报告', 'Usage and reports'], other: ['其他', 'Other'],
};

const TOOL_DISPLAY_LABELS = {
    read_file:['读取文件','Read file'],write_file:['写入文件','Write file'],list_files:['浏览文件','Browse files'],delete_file:['删除文件','Delete file'],
    run_command:['运行命令','Run command'],background_command_io:['后台进程','Background processes'],cancel_background_command:['停止后台进程','Stop process'],
    web_search:['搜索网页','Search web'],web_fetch:['读取网页','Read webpage'],get_publicnet_info:['公网连接','Public access'],
    manage_team:['管理团队','Manage teams'],manage_group:['管理群聊','Manage groups'],join_group:['加入群聊','Join group'],leave_group:['退出群聊','Leave group'],
    get_team_details:['团队详情','Team details'],get_group_details:['群聊详情','Group details'],list_agent_groups:['我的群聊','My groups'],send_to_group:['发送群消息','Send group message'],
    read_session_inbox:['阅读收件箱','Read inbox'],mark_session_inbox_read:['标记已读','Mark read'],send_to_session:['联系 Agent','Contact Agent'],
    list_sessions:['列出 Agent','List Agents'],search_sessions:['查找 Agent','Find Agent'],get_session_details:['Agent 详情','Agent details'],set_session_title:['设置对话标题','Set chat title'],
    spawn_subagent:['创建子 Agent','Create subagent'],list_subagents:['查看子 Agent','List subagents'],send_subagent_message:['联系子 Agent','Message subagent'],
    get_subagent_history:['子 Agent 历史','Subagent history'],cancel_subagent:['停止子 Agent','Stop subagent'],delete_subagent:['删除子 Agent','Delete subagent'],
    add_alarm:['创建提醒','Create reminder'],list_alarms:['查看提醒','List reminders'],delete_alarm:['删除提醒','Delete reminder'],get_current_time:['当前时间','Current time'],
    get_configuration:['查看设置','View settings'],request_configuration:['填写设置表单','Configuration form'],get_clawcross_help:['ClawCross 帮助','ClawCross help'],
    show_ui_panel:['动态 UI','Dynamic UI'],usage_status:['用量统计','Usage'],manage_personality:['管理人设','Manage personas'],
    write_session_plan:['更新计划','Update plan'],read_session_plan:['读取计划','Read plan'],clear_session_plan:['清除计划','Clear plan'],
};
function toolDisplayName(name) {
    const label=TOOL_DISPLAY_LABELS[name];
    if(!label)return String(name || '').replaceAll('_',' ');
    return window.ClawCrossLocale?.text(...label) || label[document.documentElement.lang.startsWith('zh')?0:1];
}

function groupToolsByCategory(tools) {
    const grouped = new Map();
    for (const tool of tools) {
        const category = tool.category in TOOL_CATEGORY_LABELS ? tool.category : 'other';
        if (!grouped.has(category)) grouped.set(category, []);
        grouped.get(category).push(tool);
    }
    return Object.keys(TOOL_CATEGORY_LABELS).filter(key => grouped.has(key)).map(key => ({
        key, label: TOOL_CATEGORY_LABELS[key][typeof currentLang === 'undefined' ? (document.documentElement.lang.startsWith('en') ? 1 : 0) : currentLang === 'zh-CN' ? 0 : 1],
        tools: grouped.get(key),
    }));
}

function renderGroupedToolPicker(container, tools, enabled, toggle) {
    container.replaceChildren();
    for (const group of groupToolsByCategory(tools)) {
        const section = document.createElement('details');
        section.className = 'tool-category';
        section.dataset.toolCategory=group.key;
        section.open = true;
        const summary = document.createElement('summary');
        summary.className = 'tool-category-heading';
        const title = document.createElement('span');
        title.textContent = group.label;
        const count = document.createElement('small');
        const refreshCount = () => { count.textContent = `${group.tools.filter(tool => enabled.has(tool.name)).length} / ${group.tools.length}`; };
        refreshCount();
        summary.append(title, count);
        const list = document.createElement('div');
        list.className = 'tool-category-items';
        for (const tool of group.tools) {
            const tag = document.createElement('button');
            tag.type = 'button';
            tag.className = 'tool-tag ' + (enabled.has(tool.name) ? 'enabled' : 'disabled');
            tag.setAttribute('aria-pressed', String(enabled.has(tool.name)));
            tag.title = tool.name;
            tag.textContent = toolDisplayName(tool.name);
            tag.dataset.toolName = tool.name;
            const code=document.createElement('span');code.className='tool-code';code.textContent=tool.name;tag.append(code);
            tag.onclick = () => { toggle(tool.name, tag); tag.setAttribute('aria-pressed', String(enabled.has(tool.name))); refreshCount(); };
            list.append(tag);
        }
        section.append(summary, list);
        container.append(section);
    }
}


document.addEventListener('clawcross:language',()=>{
    document.querySelectorAll('.tool-tag[data-tool-name]').forEach(button=>{
        if(button.firstChild?.nodeType===Node.TEXT_NODE)button.firstChild.nodeValue=toolDisplayName(button.dataset.toolName);
    });
    document.querySelectorAll('.tool-category[data-tool-category]').forEach(section=>{
        const labels=TOOL_CATEGORY_LABELS[section.dataset.toolCategory];
        const title=section.querySelector('.tool-category-heading>span');
        if(labels&&title)title.textContent=window.ClawCrossLocale?.text(...labels) || labels[document.documentElement.lang.startsWith('zh')?0:1];
    });
});
