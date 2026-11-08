/* Shared presentation and lifecycle actions for the Studio Agent rail. */
(() => {
    const icon = name => `<svg class="ui-icon" aria-hidden="true"><use href="/static/icons.svg#${name}"/></svg>`;
    const text = (zh,en) => window.ClawCrossLocale?.text(zh,en) || (document.documentElement.lang.startsWith('zh') ? zh : en);
    const platformMarks = new Set(['amp','antigravity','claude','cline','codebuddy','codex','copilot','cursor','gemini','goose','junie','kilocode','kimi','kiro','minimax','openclaw','opencode','qoder','qwen','trae','zai']);
    const platformAliases = {'claude-code':'claude','gemini-cli':'gemini','cursor-agent':'cursor','github-copilot':'copilot','qwen-code':'qwen',kilo:'kilocode',zcode:'zai'};
    const mobileLayout = window.matchMedia('(max-width: 768px)');
    let navigationOpen = false;
    function platformIcon(platform) {
        if(window.PlatformIcons) return PlatformIcons.markup(platform);
        const name = String(platform || '').trim().toLowerCase();
        const key = platformAliases[name] || name;
        if (platformMarks.has(key)) return `<svg class="studio-platform-icon" data-platform="${key}" viewBox="0 0 24 24" aria-hidden="true"><use href="/static/platform-icons.svg?v=20261107#${key}"/></svg>`;
        const internal = name === 'webot' || name === 'internal';
        return `<svg class="studio-platform-icon" data-platform="${internal ? 'internal' : 'other'}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${internal ? '<use href="/static/icons.svg#bot"/>' : '<rect x="3" y="4" width="18" height="16" rx="4"/><path d="m7 9 3 3-3 3m6 0h4"/>'}</svg>`;
    }
    function updatePlatform() {
        const platform = typeof _ocChatMode !== 'undefined' && _ocChatMode === 'acp' ? _acpTool : 'webot';
        const label = platform === 'webot' ? 'ClawCross' : _acpDisplayLabel(platform);
        const host = document.getElementById('studio-platform-current');
        if (host) { host.innerHTML = platformIcon(platform); const span=document.createElement('strong');span.textContent=label;host.append(span); }
        const internal = document.getElementById('oc-tab-internal');
        if (internal) internal.innerHTML = platformIcon('webot') + '<span>ClawCross</span>';
    }
    function updateNavigation(view) {
        const active = view || (typeof currentPage !== 'undefined' ? currentPage : 'chat');
        for (const [id, page] of Object.entries({'tab-chat':'chat','tab-agent-center':'agents','tab-group':'group','tab-orchestrate':'orchestrate'})) {
            const item = document.getElementById(id);
            if (!item) continue;
            item.classList.toggle('active', active === page);
            if (active === page) item.setAttribute('aria-current','page');
            else item.removeAttribute('aria-current');
        }
        const toggle = document.getElementById('studio-mobile-agents-toggle');
        if (toggle) toggle.hidden = active !== 'chat';
        const more = document.querySelector('[data-studio-more]');
        if (more) { more.title = text('更多','More'); more.setAttribute('aria-label',more.title); }
        const moreLabel = document.querySelector('[data-studio-more-label]');
        if (moreLabel) moreLabel.textContent = text('更多','More');
        document.getElementById('studio-mobile-menu-toggle')?.setAttribute('aria-label',text('菜单','Menu'));
        document.getElementById('studio-mobile-menu-close')?.setAttribute('aria-label',text('关闭','Close'));
    }
    function setNavigationOpen(open, restoreFocus = false) {
        navigationOpen = mobileLayout.matches && Boolean(open);
        const nav = document.getElementById('studio-activity-navigation');
        const trigger = document.getElementById('studio-mobile-menu-toggle');
        document.getElementById('chat-screen')?.classList.toggle('studio-navigation-open',navigationOpen);
        trigger?.setAttribute('aria-expanded',String(navigationOpen));
        const backdrop = document.getElementById('studio-mobile-nav-backdrop');
        if (backdrop) backdrop.hidden = !navigationOpen;
        if (nav) {
            nav.inert = mobileLayout.matches && !navigationOpen;
            if (mobileLayout.matches && !navigationOpen) nav.setAttribute('aria-hidden','true');
            else nav.removeAttribute('aria-hidden');
            if (navigationOpen) { nav.setAttribute('role','dialog'); nav.setAttribute('aria-modal','true'); }
            else { nav.removeAttribute('role'); nav.removeAttribute('aria-modal'); }
        }
        document.querySelectorAll('#chat-screen > .chat-main, #chat-screen > #oasis-panel').forEach(panel=>panel.inert=navigationOpen);
        if (navigationOpen) {
            const rail = document.querySelector('.studio-conversation-switcher');
            if (rail) rail.open = false;
            nav?.querySelector('.studio-activity-item.active')?.focus({preventScroll:true});
        } else {
            if (typeof closeHamburgerMenu === 'function') closeHamburgerMenu();
            if (restoreFocus && mobileLayout.matches) trigger?.focus({preventScroll:true});
        }
    }
    function toggleNavigation() { setNavigationOpen(!navigationOpen,true); }
    function closeNavigation() { setNavigationOpen(false,true); }
    function toggleAgents() {
        const rail = document.querySelector('.studio-conversation-switcher');
        if (rail) rail.open = !rail.open;
    }
    function actions(agentId, name) {
        const menu=document.createElement('details');menu.className='studio-agent-actions';
        const summary=document.createElement('summary');summary.innerHTML=icon('more');summary.setAttribute('aria-label',text('管理 Agent','Manage Agent'));menu.append(summary);
        const list=document.createElement('div');list.className='studio-agent-action-menu';menu.append(list);
        const add=(label,handler,danger=false)=>{const button=document.createElement('button');button.type='button';button.textContent=label;if(danger)button.className='danger';button.onclick=async event=>{event.stopPropagation();menu.open=false;await handler();};list.append(button);};
        add(text('Agent 设置','Agent settings'),()=>AgentSettings.open(agentId));
        add(text('归档','Archive'),()=>archive(agentId,true));
        add(text('删除 Agent','Delete Agent'),async()=>{if(await deleteAgent({agent_id:agentId},name)){await refresh();}},true);
        menu.addEventListener('click',event=>event.stopPropagation());
        return menu;
    }
    async function refresh() {
        document.getElementById('studio-team-agent-tree-list')?.removeAttribute('data-loaded');
        await refreshStudioConversations();await renderStudioTeamTree(true);
    }
    async function archive(id,value) {
        try {
            await agentApi('PATCH','/v1/agents/'+encodeURIComponent(id),{settings:{archived:value}});
            if(value && (currentSessionId===id || (typeof acpResolveSessionName==='function' && acpResolveSessionName()===id))) await studioNewConversation();
            await refresh();
        } catch(error) { alert(error.message); }
    }
    async function openArchived() {
        let dialog=document.getElementById('studio-archived-dialog');dialog?.remove();
        dialog=document.createElement('dialog');dialog.id='studio-archived-dialog';dialog.className='studio-archived-dialog';
        const header=document.createElement('header');const title=document.createElement('h2');title.textContent=text('已归档的 Agent','Archived Agents');const close=document.createElement('button');close.type='button';close.textContent='×';close.setAttribute('aria-label',text('关闭','Close'));close.onclick=()=>dialog.close();header.append(title,close);dialog.append(header);
        const body=document.createElement('div');body.className='studio-archived-list';dialog.append(body);document.body.append(dialog);dialog.showModal();
        dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close();});
        try {
            const response=await agentApi('GET','/v1/agents?include_archived=1');const rows=(response.data || []).filter(agent=>agent.archived || agent.settings?.archived);
            if(!rows.length)body.textContent=text('暂无归档 Agent','No archived Agents');
            for(const agent of rows){const row=document.createElement('div');const label=document.createElement('strong');label.textContent=agent.name;const button=document.createElement('button');button.type='button';button.textContent=text('恢复','Restore');button.onclick=async()=>{await archive(agent.agent_id,false);row.remove();if(!body.children.length)body.textContent=text('暂无归档 Agent','No archived Agents');};row.append(label,button);body.append(row);}
        }catch(error){body.textContent=error.message;}
    }
    document.addEventListener('click',event=>document.querySelectorAll('.studio-agent-actions[open]').forEach(menu=>{if(!menu.contains(event.target))menu.open=false;}));
    window.StudioSidebar={actions,platformIcon,updatePlatform,updateNavigation,toggleAgents,toggleNavigation,closeNavigation,archive,openArchived};
    document.addEventListener('DOMContentLoaded',()=>{
        updatePlatform();updateNavigation();setNavigationOpen(false);
        document.getElementById('studio-activity-navigation')?.addEventListener('click',event=>{
            const item = event.target.closest('a,button');
            if (navigationOpen && item && !item.matches('[data-studio-more],#studio-mobile-menu-close')) setNavigationOpen(false);
        });
    });
    mobileLayout.addEventListener('change',()=>setNavigationOpen(false));
    document.addEventListener('keydown',event=>{
        if (!navigationOpen) return;
        if (event.key === 'Escape') { event.preventDefault();closeNavigation();return; }
        if (event.key !== 'Tab') return;
        const items = [...document.querySelectorAll('#studio-activity-navigation button:not([disabled]),#studio-activity-navigation a[href]')].filter(item=>item.getClientRects().length);
        const first = items[0],last = items.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault();last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault();first?.focus(); }
    });
    document.addEventListener('clawcross:language',()=>updateNavigation(document.getElementById('tab-agent-center')?.classList.contains('active') ? 'agents' : undefined));
})();
