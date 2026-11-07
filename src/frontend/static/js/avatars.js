/* One visual identity across Studio, contacts and group messages. SVG stays in <img>. */
window.Avatars = (() => {
  'use strict';
  const text = (cn, en) => window.ClawCrossLocale?.text(cn, en) || (document.documentElement.lang.startsWith('zh') ? cn : en);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const cache = new Map();
  let guestLoader;
  async function request(path, method = 'GET', body, signal) {
    const response = await fetch(path, {method, signal, headers:{'Content-Type':'application/json'}, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.error || text('头像暂时无法加载','Avatar unavailable'));
    return data;
  }
  function direct(value) {
    if (value.startsWith('<svg') && value.length < 14000) return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(value);
    return value.length <= 100000 && /^data:image\/(png|jpeg|webp|svg\+xml)[;,]/.test(value) ? value : '';
  }
  function markup(value, name, options = {}) {
    const attrs = Object.entries({avatar:value || '', avatarName:name || '', avatarAgent:options.agentId || '', avatarPrincipal:options.principal || '', avatarGroup:options.groupId || ''})
      .map(([key,val]) => `data-${key.replace(/[A-Z]/g, c => '-' + c.toLowerCase())}="${esc(val)}"`).join(' ');
    return `<span class="cc-avatar" ${attrs} aria-hidden="true">${esc(Array.from(name || '·')[0].toLocaleUpperCase())}</span>`;
  }
  async function resolve(value, options = {}) {
    const raw = direct(String(value || ''));
    if (raw || !value) return raw;
    if (!/^av_[a-f0-9]{64}$/.test(value)) return '';
    const key = [window.__currentUser || '', options.groupId || '', options.principal || '', options.agentId || '', value].join(':');
    if (cache.has(key) && cache.get(key).expires > Date.now()) return cache.get(key).work;
    const work = (async () => {
      let data;
      if (options.load) data = await options.load();
      else if (options.principal && guestLoader) data = await guestLoader(options.principal, value);
      else if (options.principal && options.groupId) data = await request(`/proxy_groups/${encodeURIComponent(options.groupId)}/members/${encodeURIComponent(options.principal)}/avatar/${value}`);
      else if (options.agentId) data = await request(`/v1/agents/${encodeURIComponent(options.agentId)}/avatar`);
      else if (options.principal) return '';
      else data = await request('/v1/agents/avatars/me');
      return direct(String(data?.url || ''));
    })().catch(() => '');
    cache.set(key, {work, expires:Date.now()+60000});
    if (cache.size > 500) cache.delete(cache.keys().next().value);
    return work;
  }
  async function fill(element, value, name, options = {}) {
    const token = (Number(element.dataset.avatarVersion) || 0) + 1;
    element.dataset.avatarVersion = token;
    element.classList.add('cc-avatar');
    element.classList.remove('has-image');
    element.textContent = Array.from(name || '·')[0].toLocaleUpperCase();
    const url = await resolve(value, options);
    if (!url || !element.isConnected || String(token) !== element.dataset.avatarVersion) return;
    const image = document.createElement('img'); image.alt = ''; image.decoding = 'async';
    image.onload = () => { if (element.isConnected && String(token) === element.dataset.avatarVersion) { element.replaceChildren(image); element.classList.add('has-image'); } };
    image.src = url;
  }
  function hydrate(root = document) {
    const nodes = [...root.querySelectorAll('[data-avatar]:not([data-avatar-ready])')];
    if (root.matches?.('[data-avatar]:not([data-avatar-ready])')) nodes.unshift(root);
    for (const element of nodes) {
      element.dataset.avatarReady = 'true';
      void fill(element, element.dataset.avatar, element.dataset.avatarName, {agentId:element.dataset.avatarAgent, principal:element.dataset.avatarPrincipal, groupId:element.dataset.avatarGroup});
    }
  }
  async function prepare(file) {
    if (file.size > 8 * 1024 * 1024) throw new Error(text('图片不能超过 8 MB','Choose an image smaller than 8 MB'));
    if (file.type === 'image/svg+xml' || /\.svg$/i.test(file.name)) {
      if (file.size > 12000) throw new Error(text('SVG 头像最多 12 KB','SVG avatars can be up to 12 KB'));
      const svg = (await file.text()).trim();
      if (!svg.startsWith('<svg')) throw new Error(text('请选择完整的 SVG 图片','Choose a complete SVG image'));
      return svg;
    }
    const object = URL.createObjectURL(file);
    try {
      const image = new Image(); image.src = object; await image.decode();
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 256;
      const context = canvas.getContext('2d'), side = Math.min(image.naturalWidth, image.naturalHeight);
      context.drawImage(image, (image.naturalWidth-side)/2, (image.naturalHeight-side)/2, side, side, 0, 0, 256, 256);
      let url = canvas.toDataURL('image/png');
      if (url.length > 64*1024*4/3) url = canvas.toDataURL('image/webp', .85);
      if (url.length > 64*1024*4/3) url = canvas.toDataURL('image/jpeg', .8);
      if (url.length > 64*1024*4/3) throw new Error(text('图片过于复杂，请换一张','Choose a simpler image'));
      return url;
    } finally { URL.revokeObjectURL(object); }
  }
  function editor({value = '', name = '', kind = 'agent', load, save, onSaved, onClose, canGenerate = true}) {
    const dialog = document.createElement('dialog'); dialog.className = 'cc-avatar-dialog';
    dialog.setAttribute('aria-label', text('设置头像','Choose an avatar'));
    dialog.innerHTML = `<header><h2>${text('设置头像','Choose an avatar')}</h2><button type="button" data-close aria-label="${text('关闭','Close')}">×</button></header>
      <div class="cc-avatar-body"><div class="cc-avatar-preview"></div>
      <div class="cc-avatar-upload-row"><button type="button" data-upload>${text('上传图片','Upload image')}</button><button type="button" data-clear>${text('恢复默认','Use default')}</button><input hidden type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml"></div>
      ${canGenerate ? `<label class="cc-avatar-prompt">${text('想要什么样的形象？','Describe your avatar')}<textarea maxlength="500" rows="3" placeholder="${text('戴耳机的蓝色猫咪，简洁圆润','A friendly blue cat with headphones, simple rounded shapes')}"></textarea></label><button type="button" data-generate class="cc-avatar-generate">✦ ${text('生成头像','Generate avatar')}</button>` : ''}
      <p class="cc-avatar-status" role="status"></p></div><footer><button type="button" data-cancel>${text('取消','Cancel')}</button><button type="button" data-save class="cc-avatar-primary" disabled>${text('保存头像','Save avatar')}</button></footer>`;
    const preview = dialog.querySelector('.cc-avatar-preview'), status = dialog.querySelector('[role=status]');
    const saveButton = dialog.querySelector('[data-save]'), controller = new AbortController();
    let draft = value, busy = false;
    const draw = () => { void fill(preview, draft, name, {load}); saveButton.disabled = busy || draft === value; };
    const setBusy = flag => { busy = flag; dialog.querySelectorAll('button:not([data-close]):not([data-cancel]),textarea,input').forEach(el => { el.disabled = flag; }); saveButton.disabled = flag || draft === value; dialog.setAttribute('aria-busy', String(flag)); };
    const fail = error => { if (dialog.isConnected && error.name !== 'AbortError') { status.textContent = error.message; status.classList.add('is-error'); } };
    dialog.querySelectorAll('[data-close],[data-cancel]').forEach(button => { button.onclick = () => dialog.close(); });
    dialog.onclick = event => { if (event.target === dialog) dialog.close(); };
    dialog.addEventListener('close', () => { controller.abort(); dialog.remove(); onClose?.(); }, {once:true});
    dialog.querySelector('[data-clear]').onclick = () => { draft = ''; status.textContent = ''; draw(); };
    const input = dialog.querySelector('input[type=file]');
    dialog.querySelector('[data-upload]').onclick = () => input.click();
    input.onchange = async () => {
      if (!input.files[0]) return;
      setBusy(true); status.textContent = ''; status.classList.remove('is-error');
      try { draft = await prepare(input.files[0]); if (dialog.isConnected) draw(); } catch (error) { fail(error); }
      finally { if (dialog.isConnected) setBusy(false); input.value = ''; }
    };
    const generate = dialog.querySelector('[data-generate]');
    if (generate) generate.onclick = async () => {
      const description = dialog.querySelector('textarea').value.trim();
      if (!description) { dialog.querySelector('textarea').focus(); return; }
      setBusy(true); status.classList.remove('is-error'); status.textContent = text('正在设计你的头像…','Designing your avatar…');
      try {
        const data = await request('/v1/agents/avatars/generate', 'POST', {description, name, kind}, controller.signal);
        if (!dialog.isConnected) return;
        draft = data.url; draw(); status.textContent = text('喜欢的话，保存就好。','Save it when you like it.');
      } catch (error) { fail(error); }
      finally { if (dialog.isConnected) setBusy(false); }
    };
    saveButton.onclick = async () => {
      if (busy || draft === value) return;
      setBusy(true); status.classList.remove('is-error'); status.textContent = text('正在保存…','Saving…');
      try { const result = await save(draft); if (!dialog.isConnected) return; onSaved?.(result); dialog.close(); }
      catch (error) { fail(error); }
      finally { if (dialog.isConnected) setBusy(false); }
    };
    document.body.append(dialog); dialog.showModal(); draw(); return dialog;
  }
  async function editUser(onSaved) {
    try {
      const current = await request('/v1/agents/avatars/me');
      return editor({value:current.avatar, name:window.__currentUser || '', kind:'human', load:() => Promise.resolve(current),
        save:avatar => request('/v1/agents/avatars/me','PUT',{avatar}),
        onSaved:result => { cache.clear(); onSaved?.(result); window.dispatchEvent(new Event('avatars-changed')); }});
    } catch (error) { if (typeof toast === 'function') toast(error.message); }
  }
  function editAgent(agent, {onSaved, onClose, ensure} = {}) {
    return editor({value:agent.settings?.avatar || '', name:agent.name || '', kind:'agent',
      load:() => request(`/v1/agents/${encodeURIComponent(agent.agent_id)}/avatar`),
      save:async avatar => { if (ensure) await ensure(); return request(`/v1/agents/${encodeURIComponent(agent.agent_id)}`,'PATCH',{settings:{avatar}}); },
      onSaved:result => { cache.clear(); onSaved?.(result); window.dispatchEvent(new Event('avatars-changed')); }, onClose});
  }
  function start() {
    hydrate(); new MutationObserver(records => { for (const record of records) for (const node of record.addedNodes) if (node.nodeType === 1) hydrate(node); })
      .observe(document.body, {childList:true, subtree:true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',start,{once:true}); else start();
  return {markup, fill, hydrate, prepare, editor, editUser, editAgent, request, clear:() => cache.clear(), setGuestLoader:callback => {guestLoader=callback;}};
})();
