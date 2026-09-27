// The API supplies category metadata; permissions remain on individual tools.
const TOOL_CATEGORY_LABELS = {
    files: ['文件与记忆', 'Files and memory'], commands: ['命令执行', 'Commands'], web: ['搜索与模型', 'Search and models'],
    sessions: ['会话与消息', 'Sessions and messages'], agents: ['Agent 与计划', 'Agents and plans'],
    workflows: ['OASIS 工作流', 'OASIS workflows'], skills: ['个性', 'Personality'],
    notifications: ['定时与通知', 'Schedules and notifications'], usage: ['用量与报告', 'Usage and reports'], other: ['其他', 'Other'],
};

function groupToolsByCategory(tools) {
    const grouped = new Map();
    for (const tool of tools) {
        const category = tool.category in TOOL_CATEGORY_LABELS ? tool.category : 'other';
        if (!grouped.has(category)) grouped.set(category, []);
        grouped.get(category).push(tool);
    }
    return Object.keys(TOOL_CATEGORY_LABELS).filter(key => grouped.has(key)).map(key => ({
        key, label: TOOL_CATEGORY_LABELS[key][typeof currentLang === 'undefined' || currentLang === 'zh-CN' ? 0 : 1],
        tools: grouped.get(key),
    }));
}

function renderGroupedToolPicker(container, tools, enabled, toggle) {
    container.replaceChildren();
    for (const group of groupToolsByCategory(tools)) {
        const section = document.createElement('details');
        section.className = 'tool-category';
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
            tag.title = tool.description || '';
            tag.textContent = tool.name;
            tag.onclick = () => { toggle(tool.name, tag); tag.setAttribute('aria-pressed', String(enabled.has(tool.name))); refreshCount(); };
            list.append(tag);
        }
        section.append(summary, list);
        container.append(section);
    }
}
