// Agent workspace sources are switches; only explicitly added folders are saved.
(() => {
    const text = (zh, en) => document.documentElement.lang.startsWith('en') ? en : zh;
    const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    const sources = [
        ['companion', '专属工作区', 'Private workspace', '这个 Agent 的伴生目录', 'This Agent’s companion folder'],
        ['user_shared', '用户共享', 'User shared', '与同一用户的其他 Agent 共享文件', 'Share files with this user’s other Agents'],
        ['cli', 'CLI 工作区', 'CLI workspace', '使用 CLI 本次启动的目录；服务重启后重新取得', 'Use the CLI launch directory; reacquired after a service restart'],
        ['teams', '团队工作区', 'Team workspaces', '随加入或退出团队自动更新', 'Update automatically when joining or leaving Teams'],
    ];
    window.workspaceTeamLabel = team => team === '__default__' ? text('默认项目','Default project') : team;
    window.openWorkspaceSettings = async target => {
        const agent = target || (typeof runtimeSettingsCurrentAgent === 'function' ? runtimeSettingsCurrentAgent() : '');
        if (!agent) return;
        let overlay = document.getElementById('workspace-settings-modal');
        if (overlay) overlay.remove();
        overlay = document.createElement('div');
        overlay.id = 'workspace-settings-modal'; overlay.className = 'settings-modal-overlay';
        overlay.setAttribute('role','dialog'); overlay.setAttribute('aria-modal','true');
        overlay.setAttribute('aria-labelledby','workspace-settings-title'); overlay.style.display = 'flex';
        const previousFocus = document.activeElement;
        const close = () => { overlay.remove(); previousFocus?.focus(); };
        overlay.innerHTML = `<div class="settings-modal runtime-settings-dialog workspace-settings-dialog">
            <div class="runtime-settings-header"><div><h2 id="workspace-settings-title">${text('工作区','Workspaces')}</h2><p>${text('这个 Agent 可以使用的目录集合','Folders available to this Agent')}</p></div><button type="button" class="runtime-settings-close" aria-label="${text('关闭','Close')}">×</button></div>
            <form class="workspace-settings-form"><div class="runtime-settings-body"><p class="workspace-settings-help">${text('自动来源按当前用户、Agent、团队和 CLI 运行状态生成目录。配置仅保存开关和你添加的路径。Skills 位于各工作区的 skills 文件夹中。','Automatic sources resolve from the current user, Agent, Teams and CLI runtime. Settings save switches and custom paths only. Skills live in each workspace’s skills folder.')}</p>
            <div class="workspace-source-list">${sources.map(([key, zh, en, hintZh, hintEn]) => `<label class="workspace-source"><input type="checkbox" name="${key}"><span><strong>${text(zh,en)}</strong><small>${text(hintZh,hintEn)}</small></span></label>`).join('')}</div>
            <label class="runtime-settings-field"><span>${text('添加目录','Additional folders')}</span><textarea name="paths" class="runtime-settings-input" rows="3" placeholder="${text('每行一个已存在的绝对目录路径','One existing absolute folder path per line')}"></textarea></label>
            <details class="runtime-settings-advanced" open><summary>${text('当前可用目录','Available folders')}</summary><div class="workspace-folder-list"></div></details></div>
            <div class="runtime-settings-footer"><div class="workspace-settings-status" role="status"></div><button type="submit" class="runtime-settings-btn runtime-settings-btn-primary" disabled>${text('保存','Save')}</button></div></form></div>`;
        document.body.appendChild(overlay);
        overlay.querySelector('.runtime-settings-close').onclick = close;
        overlay.onclick = event => { if (event.target === overlay) close(); };
        overlay.addEventListener('keydown', event => {
            if (event.key === 'Escape') { event.stopPropagation(); close(); }
            if (event.key === 'Tab') {
                const controls = [...overlay.querySelectorAll('button,input,textarea,summary')].filter(el=>!el.disabled && el.getClientRects().length);
                if (!controls.length) return;
                const first=controls[0],last=controls[controls.length-1];
                if (event.shiftKey && event.target===first) { event.preventDefault(); last.focus(); }
                else if (!event.shiftKey && event.target===last) { event.preventDefault(); first.focus(); }
            }
        });
        const form = overlay.querySelector('form'), status = overlay.querySelector('[role="status"]'), save = form.querySelector('[type="submit"]');
        const url = '/v1/agents/' + encodeURIComponent(agent);
        const render = payload => {
            const list = overlay.querySelector('.workspace-folder-list');
            const labels = Object.fromEntries(sources.map(([key,zh,en])=>[key,text(zh,en)]));
            labels.user=labels.user_shared; labels.team=labels.teams; labels.custom=text('添加的目录','Added folder');
            list.innerHTML = (payload.folders || []).map(folder => `<div class="workspace-folder"><span>${escape(labels[folder.source] || folder.source)}${folder.team && folder.team !== '__default__' ? ' · ' + escape(folder.team) : ''}</span><code>${escape(folder.path)}</code></div>`).join('') || `<p>${escape(payload.error || text('暂无可用目录','No folders available'))}</p>`;
        };
        status.textContent=text('加载中…','Loading…');
        try {
            const response=await fetch(url+'/workspaces'), payload=await response.json();
            if (!response.ok) throw new Error(payload.detail || payload.error || response.statusText);
            if (!overlay.isConnected) return;
            for (const [key] of sources) form.elements[key].checked=!!payload.settings[key];
            form.elements.paths.value=(payload.settings.paths || []).join('\n');
            render(payload); status.textContent=payload.error || ''; save.disabled=false;
        } catch(error) { status.textContent=error.message; }
        form.onsubmit=async event=>{
            event.preventDefault(); save.disabled=true;
            const workspaces={paths:form.elements.paths.value.split('\n').map(value=>value.trim()).filter(Boolean)};
            for (const [key] of sources) workspaces[key]=form.elements[key].checked;
            try {
                const response=await fetch(url,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:{workspaces}})}), payload=await response.json();
                if (!response.ok) throw new Error(typeof payload.detail==='string' ? payload.detail : JSON.stringify(payload.detail || payload.error));
                const refreshed=await fetch(url+'/workspaces');
                if (!refreshed.ok) throw new Error(refreshed.statusText);
                render(await refreshed.json()); status.textContent=text('已保存，下次调用会使用最新目录。','Saved. The next call uses the latest folders.');
            } catch(error) { status.textContent=error.message; }
            finally { save.disabled=false; }
        };
        overlay.querySelector('.runtime-settings-close').focus();
    };
})();
