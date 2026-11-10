/* Shared settings directory for Studio, contacts and group member profiles. */
window.AgentSettings = (() => {
  const text=(cn,en)=>window.ClawCrossLocale?.text(cn,en) || (document.documentElement.lang.startsWith('en') ? en : cn);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon = name => `<svg class="ui-icon" aria-hidden="true"><use href="/static/icons.svg#${name}"/></svg>`;
  let sequence = 0;
  async function request(path, method = 'GET', body) {
    const response = await fetch(path, {method, headers:{'Content-Type':'application/json'}, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
    const data = await response.json();
    if (!response.ok) throw Object.assign(new Error(typeof data.detail === 'string' ? data.detail : data.error || `HTTP ${response.status}`), {status:response.status});
    return data;
  }
  function attachBack(overlay, close, onBack) {
    if (!onBack) return;
    const header = overlay.querySelector('header, .runtime-settings-header');
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'agent-settings-back';
    button.innerHTML = `${icon('arrow-left')}<span>${text('Agent 设置','Agent settings')}</span>`;
    button.onclick = () => { close(); onBack(); };
    header.before(button);
  }
  async function open(id, initialSection = '') {
    if (!id) return;
    const token = ++sequence;
    let agent;
    try { agent = await request('/v1/agents/' + encodeURIComponent(id)); }
    catch (error) {
      if (error.status === 404 && typeof studioEnsureAgent === 'function') {
        const nativeDraft = window.ExternalAgentSettings?.currentTarget() === id && typeof _ocChatMode !== 'undefined' && _ocChatMode === 'acp';
        agent = {agent_id:id, name:id, platform:nativeDraft ? _acpTool : 'webot', settings:{}, uncreated:true};
      }
      else { if (typeof toast === 'function') toast(error.message); else window.alert(error.message); return; }
    }
    if (token !== sequence || agent.remote) return;
    document.getElementById('agent-settings-home')?.remove();
    const overlay = document.createElement('div');
    overlay.id = 'agent-settings-home'; overlay.className = 'external-settings-overlay';
    overlay.setAttribute('role','dialog'); overlay.setAttribute('aria-modal','true'); overlay.setAttribute('aria-labelledby','agent-settings-title');
    const previousFocus = document.activeElement;
    const close = () => { ++sequence; overlay.remove(); if (previousFocus?.isConnected) previousFocus.focus(); };
    const webot = agent.platform === 'webot', native = !['webot','http','llm'].includes(agent.platform);
    const row = (key, title, description, symbol) => `<button type="button" data-category="${key}" class="agent-settings-row">${icon(symbol)}<span><strong>${esc(title)}</strong><small>${esc(description)}</small></span>${icon('chevron-right')}</button>`;
    const shell = (title, body, section = '') => {
      overlay.innerHTML = `<section class="external-settings-dialog agent-settings-dialog"><header><div><h2 id="agent-settings-title">${esc(title)}</h2><p>${esc(agent.name || id)}</p></div><button type="button" data-close aria-label="${text('关闭','Close')}">×</button></header>${body}</section>`;
      overlay.querySelector('[data-close]').onclick = close;
      if (section) attachBack(overlay, () => {}, home);
      overlay.querySelector('[data-close]').focus();
    };
    const home = () => {
      shell(text('Agent 设置','Agent settings'), `<div class="agent-settings-directory"><h3>${text('身份与能力','Identity and capabilities')}</h3>${row('avatar',text('头像','Avatar'),text('上传图片，或描述并生成专属形象','Upload a picture or describe your own avatar'),'user')}${row('profile',text('名称与人设','Name and persona'),text('名字、角色与回复风格','Name, role and response style'),'user')}${webot ? row('tools',text('工具范围','Tool access'),text('这个 Agent 可以使用哪些工具','Tools available to this Agent'),'sliders') : ''}${webot || native ? row('model',native ? text('模型、工具与连接','Model, tools and connection') : text('模型与思考强度','Model and reasoning'),native ? text('外部程序、运行设备与原生选项','External program, device and native options') : text('模型配置与思考档位','Model profile and reasoning level'),'settings') : ''}<h3>${text('运行设置','Runtime settings')}</h3>${row('approval',text('模式、沙盒与授权','Mode, sandbox and approvals'),text('运行模式、安全等级与 KEEP Y','Run mode, security level and KEEP Y'),'shield')}${row('workspaces',text('工作区','Workspaces'),text('可访问的文件夹与 Skill 来源','Accessible folders and skill sources'),'folder')}${webot ? row('context',text('上下文与压缩','Context and compaction'),text('容量、压缩时机与摘要','Capacity, compaction and summaries'),'history') : ''}</div>`);
      overlay.querySelectorAll('[data-category]').forEach(button => { button.onclick = () => category(button.dataset.category); });
    };
    const changed = () => {
      if (typeof invalidateContactsPrefetch === 'function') invalidateContactsPrefetch();
      if (typeof loadContactsForTeam === 'function') void loadContactsForTeam();
      if (typeof reloadGroupDetail === 'function' && typeof currentGroupId !== 'undefined' && currentGroupId) void reloadGroupDetail().catch(()=>{});
      if (typeof refreshAgentCenter === 'function') void refreshAgentCenter();
      if (typeof refreshStudioConversations === 'function') void refreshStudioConversations();
    };
    async function editor(section) {
      shell(section === 'profile' ? text('名称与人设','Name and persona') : text('工具范围','Tool access'), `<form class="agent-settings-form"><div data-fields></div><footer><span role="status" data-status></span><button type="submit" data-save>${agent.uncreated ? text('创建并保存','Create and save') : text('保存','Save')}</button></footer></form>`, section);
      const form = overlay.querySelector('form'), fields = form.querySelector('[data-fields]'), status = form.querySelector('[data-status]'), save = form.querySelector('[data-save]');
      if (section === 'profile') {
        fields.innerHTML = `<label>${text('Agent 名称','Agent name')}<input name="name" required maxlength="120" value="${esc(agent.name)}"></label><label>${text('人设','Persona')}<textarea name="persona" rows="8">${esc(agent.settings?.persona || '')}</textarea></label>${webot ? `<label>${text('对话标题 · 可选','Conversation title · Optional')}<input name="title" maxlength="80" value="${esc(agent.settings?.title || '')}" placeholder="${text('留空显示第一条消息','Leave blank to use the first message')}"></label>` : ''}${!webot && !native ? `<label>${text('模型','Model')}<input name="model" value="${esc(agent.settings?.model || '')}"></label>` : ''}`;
        const library = document.createElement('details');
        library.innerHTML = `<summary>${text('从人设库填入','Use a saved persona')}</summary><select aria-label="${text('人设库','Persona library')}"><option value="">${text('选择人设','Choose a persona')}</option></select><small role="status"></small>`;
        fields.appendChild(library);
        let personas = null, loading = false;
        library.ontoggle = async () => {
          if (!library.open || personas || loading) return;
          loading = true;
          try {
            const payload = await request('/proxy_visual/experts?full=1');
            if (!library.isConnected) return;
            personas = (Array.isArray(payload) ? payload : []).filter(row => row.persona);
            const select = library.querySelector('select');
            personas.forEach((row,index) => select.add(new Option(row.name || row.tag, String(index))));
            select.onchange = () => { if (select.value !== '') form.elements.persona.value = personas[Number(select.value)].persona; };
          } catch (error) { if (library.isConnected) library.querySelector('[role=status]').textContent = error.message; }
          finally { loading = false; }
        };
      } else {
        save.disabled = true; status.textContent = text('正在加载工具…','Loading tools…');
        try {
          const payload = await request('/proxy_tools');
          if (!form.isConnected) return;
          const tools = Array.isArray(payload) ? payload : payload.tools || [];
          const selected = agent.settings?.tools;
          const names = new Map(tools.map(tool => [tool.name, tool.description || '']));
          (selected || []).forEach(name => { if (!names.has(name)) names.set(name, ''); });
          fields.innerHTML = `<div class="agent-settings-tool-actions"><button type="button" data-all>${text('全选','Select all')}</button><button type="button" data-none>${text('全不选','Clear all')}</button></div><div class="agent-settings-tool-list">${[...names].map(([name,description])=>`<label title="${esc(description)}"><input type="checkbox" name="tool" value="${esc(name)}" ${selected == null || selected.includes(name) ? 'checked' : ''}><span>${esc(name)}</span></label>`).join('')}</div>`;
          fields.querySelector('[data-all]').onclick = () => fields.querySelectorAll('input').forEach(input => { input.checked = true; });
          fields.querySelector('[data-none]').onclick = () => fields.querySelectorAll('input').forEach(input => { input.checked = false; });
          status.textContent = ''; save.disabled = false;
        } catch (error) { status.textContent = error.message; return; }
      }
      form.onsubmit = async event => {
        event.preventDefault(); save.disabled = true; status.textContent = text('保存中…','Saving…');
        const body = {settings:{}};
        if (section === 'profile') { body.name = form.elements.name.value.trim(); body.settings.persona = form.elements.persona.value.trim(); if (webot) body.settings.title = form.elements.title.value.trim(); if (!webot && !native) body.settings.model = form.elements.model.value.trim(); if (!body.name) { status.textContent = text('名称不能为空','Name is required'); save.disabled = false; return; } }
        else { const inputs = [...fields.querySelectorAll('input[name=tool]')]; body.settings.tools = inputs.length ? (inputs.every(input=>input.checked) ? null : inputs.filter(input=>input.checked).map(input=>input.value)) : agent.settings?.tools ?? null; }
        try {
          if (agent.uncreated) await studioEnsureAgent(id, {platform:agent.platform});
          agent = await request('/v1/agents/' + encodeURIComponent(id), 'PATCH', body);
          if (form.isConnected) status.textContent = text('已保存','Saved');
          changed();
        } catch (error) { if (form.isConnected) status.textContent = error.message; }
        finally { if (form.isConnected) save.disabled = false; }
      };
    }
    async function category(key) {
      if (key === 'avatar') {
        close();
        return Avatars.editAgent(agent, {onSaved:changed, onClose:() => open(id),
          ensure:agent.uncreated ? () => studioEnsureAgent(id, {platform:agent.platform}) : undefined});
      }
      if (key === 'profile' || key === 'tools') return editor(key);
      const options = {onBack:() => open(id)};
      close();
      if (key === 'model') return native ? openExternalAgentSettings(id, options) : openAgentModelSettings(id, options);
      if (key === 'workspaces') return openWorkspaceSettings(id, options);
      return openRuntimeSettings(id, key, options);
    }
    overlay.onclick = event => { if (event.target === overlay) close(); };
    overlay.onkeydown = event => {
      if (event.key === 'Escape') { event.stopPropagation(); close(); }
      if (event.key === 'Tab') {
        const elements = [...overlay.querySelectorAll('button,input,textarea,select,summary')].filter(el => !el.disabled && el.offsetParent !== null);
        if (event.shiftKey && document.activeElement === elements[0]) { event.preventDefault(); elements.at(-1)?.focus(); }
        else if (!event.shiftKey && document.activeElement === elements.at(-1)) { event.preventDefault(); elements[0]?.focus(); }
      }
    };
    document.body.appendChild(overlay); home();
    if (initialSection === 'avatar') await category('avatar');
    else if (['profile','tools'].includes(initialSection)) await editor(initialSection);
  }
  return {open, attachBack};
})();
