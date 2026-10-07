/* Group invitations: one link per group. Opened in a browser it is the guest chat;
   pasted into "加入群聊" on another ClawCross it joins that device as a member. */
(() => {
  'use strict';
  const LINK_KEY = 'clawcross_group_invite_';

  async function request(method, path, body) {
    const response = await fetch('/proxy_groups' + path, {
      method, headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.error || '群聊请求失败');
    return data;
  }
  function remembered(gid) {
    try { return localStorage.getItem(LINK_KEY + gid) || ''; } catch (_) { return ''; }
  }
  function remember(gid, link) {
    try { link ? localStorage.setItem(LINK_KEY + gid, link) : localStorage.removeItem(LINK_KEY + gid); } catch (_) { /* private mode */ }
  }
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }
  function button(label, kind, onClick) {
    const node = el('button', 'gn-btn gn-btn-' + kind, label);
    node.type = 'button';
    node.addEventListener('click', onClick);
    return node;
  }
  function dialog(title, subtitle) {
    const root = el('dialog', 'group-network-dialog');
    const head = el('header', 'gn-head');
    const titles = el('div');
    titles.append(el('h2', 'gn-title', title));
    if (subtitle) titles.append(el('p', 'gn-subtitle', subtitle));
    const close = el('button', 'gn-close');
    close.type = 'button';
    close.setAttribute('aria-label', '关闭');
    close.innerHTML = '<svg class="ui-icon"><use href="/static/icons.svg#x"/></svg>';
    const body = el('div', 'gn-body');
    const status = el('p', 'gn-status');
    status.setAttribute('role', 'status');
    head.append(titles, close);
    root.append(head, body, status);
    const done = () => { root.close(); root.remove(); };
    close.addEventListener('click', done);
    root.addEventListener('cancel', () => root.remove());
    root.addEventListener('click', (event) => { if (event.target === root) done(); });
    document.body.append(root);
    root.showModal();
    return { root, body, status, done };
  }
  async function busy(control, status, label, task) {
    control.disabled = true;
    status.textContent = label;
    status.classList.remove('is-error');
    try { await task(); }
    catch (error) { status.textContent = error.message; status.classList.add('is-error'); }
    finally { control.disabled = false; }
  }
  function notify(message) {
    if (window.toast) window.toast(message); else window.alert(message);
  }
  const text = (zh, en) => document.documentElement.lang === 'zh-CN' ? zh : en;
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function ensurePublicEntry(status) {
    const readStatus = async () => {
      const response = await fetch('/proxy_tunnel/status');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '公网状态查询失败');
      return data;
    };
    let state = await readStatus();
    if (state.public_domain) return state.public_domain;
    if (!window.confirm(text(
      '没有配置公网域名。生成邀请链接需要下载并启动 Cloudflare Tunnel，继续吗？',
      'No public domain is configured. Creating this invite requires downloading and starting Cloudflare Tunnel. Continue?'
    ))) throw new Error(text('已取消生成邀请链接', 'Invite creation cancelled'));

    let componentResponse = await fetch('/proxy_components/cloudflared');
    let component = await componentResponse.json();
    if (!componentResponse.ok) throw new Error(component.error || 'Cloudflare Tunnel 状态查询失败');
    if (!component.installed) {
      status.textContent = text('正在下载并安装 Cloudflare Tunnel…', 'Downloading and installing Cloudflare Tunnel…');
      componentResponse = await fetch('/proxy_components/cloudflared', {
        method: 'POST', headers: {'X-Requested-With': 'ClawCross'}
      });
      component = await componentResponse.json();
      if (!componentResponse.ok) throw new Error(component.error || 'Cloudflare Tunnel 安装失败');
      for (let i = 0; i < 120 && component.state === 'installing'; i++) {
        await wait(1000);
        componentResponse = await fetch('/proxy_components/cloudflared');
        component = await componentResponse.json();
        if (!componentResponse.ok) throw new Error(component.error || 'Cloudflare Tunnel 状态查询失败');
      }
      if (!component.installed) throw new Error(component.detail || 'Cloudflare Tunnel 安装失败');
    }

    state = await readStatus();
    if (!state.running) {
      status.textContent = text('正在启动 Cloudflare Tunnel…', 'Starting Cloudflare Tunnel…');
      const startResponse = await fetch('/proxy_tunnel/start', {method: 'POST'});
      const started = await startResponse.json().catch(() => ({}));
      if (!startResponse.ok) throw new Error(started.error || 'Cloudflare Tunnel 启动失败');
    }
    for (let i = 0; i < 60; i++) {
      await wait(1500);
      state = await readStatus();
      if (state.public_domain) return state.public_domain;
    }
    throw new Error(text('Tunnel 已启动，但公网域名仍未就绪。请稍后重试。', 'The tunnel started, but its public URL is not ready yet. Try again shortly.'));
  }
  function changed(group) {
    window.dispatchEvent(new CustomEvent('group-network-changed', { detail: group }));
  }

  window.GroupNetworkUI = {
    async rename(gid) {
      let group;
      try { group = await request('GET', '/' + encodeURIComponent(gid)); }
      catch (error) { notify(error.message); return; }
      const view = dialog('修改群名', '群号保持不变，成员和聊天记录都会保留');
      const input = el('input','gn-input');
      input.value = group.title || ''; input.maxLength = 160; input.setAttribute('aria-label','群名');
      const save = button('保存群名','primary',()=>busy(save,view.status,'正在保存…',async()=>{
        if (!input.value.trim()) throw new Error('请填写群名');
        const updated = await request('PATCH','/' + encodeURIComponent(gid),{title:input.value.trim()});
        view.done(); changed(updated);
      }));
      view.body.append(input,el('p','gn-note','群号：' + gid),save);
      input.focus();
    },

    search(gid) {
      const view = dialog('查找聊天记录','搜索本群全部历史消息');
      const form = el('form','gn-search-form');
      const input = el('input','gn-input'); input.type='search'; input.required=true; input.maxLength=120; input.placeholder='关键词或成员名字'; input.setAttribute('aria-label','搜索聊天记录');
      const submit = el('button','gn-btn gn-btn-primary','搜索'); submit.type='submit';
      form.append(input,submit);
      const results = el('div','gn-search-results');
      let before=0, query='', generation=0;
      const messages = new Map();
      GroupMessageMenu.bind(results, id => messages.get(id), message => { view.done(); if(window.setGroupReply) window.setGroupReply(message); });
      const more = button('更早的结果','secondary',()=>search(false)); more.hidden=true;
      async function search(reset) {
        const requestGeneration = reset ? ++generation : generation;
        if (reset) {query=input.value.trim();before=0;results.replaceChildren();messages.clear();more.hidden=true;}
        if (!query) return;
        await busy(reset ? submit : more, view.status,'正在查找…',async()=>{
          const data = await request('GET','/' + encodeURIComponent(gid) + '/search?' + new URLSearchParams({query,before_id:before,limit:50}));
          if (!view.root.isConnected || generation !== requestGeneration) return;
          for (const message of data.messages || []) {
            const row=el('article','gn-search-result');
            row.append(el('strong','',message.sender_name || message.sender),el('small','',new Date(message.created_at * 1000).toLocaleString()),el('p','',message.content));
            messages.set(message.id,message); row.dataset.groupMessageId=message.id;row.tabIndex=0;
            results.append(row);
          }
          before=data.next_before_id || 0;more.hidden=!before;
          view.status.textContent=results.children.length ? `已找到 ${results.children.length} 条消息` : '没有找到匹配的消息';
        });
      }
      form.addEventListener('submit',event=>{event.preventDefault();search(true);});
      view.body.append(form,results,more);input.focus();
    },

    join() {
      const view = dialog('加入群聊', '粘贴朋友发来的邀请链接');
      const input = el('textarea', 'gn-input gn-link-input');
      input.rows = 3;
      input.placeholder = 'https://…/group-guest#…';
      input.autocomplete = 'off';
      input.spellcheck = false;
      const join = button('加入', 'primary', () => busy(join, view.status, '正在加入…', async () => {
        const group = await request('POST', '/join', { invite: input.value.trim(), agents: [] });
        view.done();
        changed(group);
      }));
      join.disabled = true;
      input.addEventListener('input', () => { join.disabled = !input.value.includes('/group-guest#'); });
      view.body.append(input, el('p', 'gn-note', '加入后你可以发言；你的 agent 要在群成员里手动添加后才会收到群消息。'), join);
      setTimeout(() => input.focus(), 50);
    },

    async sharing(gid) {
      let group;
      try { group = await request('GET', `/${encodeURIComponent(gid)}`); }
      catch (error) { notify(error.message); return; }
      const owner = !String(group.owner || '').startsWith('remote:');
      const view = dialog('邀请朋友', group.title ? `「${group.title}」` : '');
      if (!owner) {
        view.body.append(el('p', 'gn-note', '只有群主可以生成邀请链接。请向群主要一个链接。'));
        return;
      }
      const link = el('input', 'gn-input gn-link');
      link.readOnly = true;
      link.placeholder = '还没有邀请链接';
      link.value = remembered(gid);
      const copy = button('复制链接', 'primary', async () => {
        try { await navigator.clipboard.writeText(link.value); view.status.textContent = '已复制'; }
        catch (_) { link.focus(); link.select(); view.status.textContent = '请手动复制'; }
      });
      const create = button('', 'secondary', () => busy(create, view.status, '正在生成…', async () => {
        await ensurePublicEntry(view.status);
        await refreshTunnelControl();
        const data = await request('POST', `/${encodeURIComponent(gid)}/guest-link`, {});
        link.value = data.url;
        remember(gid, data.url);
        if(data.qr) showQr(data.qr);
        refresh();
        view.status.textContent = '新链接已生成';
      }));
      const stop = button('停止邀请', 'quiet', () => busy(stop, view.status, '正在停止…', async () => {
        await request('POST', `/${encodeURIComponent(gid)}/guest-link`, { disable: true });
        link.value = '';
        remember(gid, '');
        refresh();
        view.status.textContent = '邀请已停止；已加入的人不受影响';
      }));
      const qr = el('img','gn-qr'); qr.alt='扫描二维码加入群聊'; qr.hidden=true;
      const qrCaption = el('p','gn-note gn-qr-caption','扫码与打开下方链接完全相同'); qrCaption.hidden=true;
      const download = el('a','gn-btn gn-btn-secondary','保存二维码'); download.download='group-invite.svg'; download.hidden=true;
      function refresh() {
        const has = Boolean(link.value);
        if(!has) qr.hidden=qrCaption.hidden=download.hidden=true;
        copy.hidden = !has;
        stop.hidden = !has;
        create.textContent = has ? '换一个新链接' : '生成邀请链接';
        create.className = 'gn-btn ' + (has ? 'gn-btn-secondary' : 'gn-btn-primary');
      }
      refresh();
      const actions = el('div', 'gn-actions');
      actions.append(copy, create, stop);
      const publicNote = el('p', 'gn-note', text(
        '若没有公网域名，生成邀请时会提示并启动 Cloudflare Tunnel。也可以先在这里单独下载 cloudflared。',
        'If no public domain is configured, invite creation will prompt before starting Cloudflare Tunnel. You can also install cloudflared here first.'
      ));
      const cloudflare = el('div', 'gn-component-control');
      if (typeof window.componentControlMarkup === 'function') {
        cloudflare.innerHTML = window.componentControlMarkup('cloudflared');
      }
      const tunnelStatus = el('p', 'gn-note');
      const closeTunnel = button(text('关闭公网通道 · Cloudflare', 'Close public tunnel · Cloudflare'), 'quiet', () => {
        if (!window.confirm(text(
          '关闭本机的 Cloudflare Tunnel？通过此通道访问的页面和群聊会断线；自行配置的反向代理仍由服务器管理。',
          'Close this device’s Cloudflare Tunnel? Pages and groups using it will disconnect. Separately configured reverse proxies remain managed by the server.'
        ))) return;
        busy(closeTunnel, view.status, text('正在关闭公网通道…', 'Closing the public tunnel…'), async () => {
          const response = await fetch('/proxy_tunnel/stop', {method:'POST'});
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || response.statusText);
          await refreshTunnelControl();
          view.status.textContent = text('Cloudflare 公网通道已关闭。', 'The Cloudflare public tunnel is closed.');
        });
      });
      closeTunnel.hidden = true;
      async function refreshTunnelControl() {
        try {
          const response = await fetch('/proxy_tunnel/status');
          const state = await response.json();
          if (!response.ok || !view.root.isConnected) return;
          closeTunnel.hidden = !state.running;
          tunnelStatus.textContent = state.running
            ? text('本机 Cloudflare 公网通道正在运行', 'This device’s Cloudflare public tunnel is running')
            : text('本机 Cloudflare 公网通道已关闭', 'This device’s Cloudflare public tunnel is closed');
        } catch (_) { tunnelStatus.textContent = text('公网通道状态暂时不可用', 'Public tunnel status is unavailable'); }
      }
      const steps = el('ul', 'gn-steps');
      for (const text of [
        '朋友扫码或打开链接，取名字、设密码即可聊天，不需要主站账号。',
        '装了 ClawCross 的朋友在「加入群聊」粘贴链接，加入后还能带上自己的 agent。',
        '链接 30 天内可用来加入；换新链接或停止邀请后旧链接失效，已加入的人不受影响。',
      ]) steps.append(el('li', '', text));
      view.body.append(qr,qrCaption,download,link, publicNote, cloudflare, tunnelStatus, closeTunnel, actions, steps);
      if (typeof window.initComponentControls === 'function') window.initComponentControls(cloudflare);
      void refreshTunnelControl();
      // Cached links retain their exact QR payload; this does not rotate invitations.
      if (link.value) {
        try {
          const response = await fetch('/proxy_groups/' + encodeURIComponent(gid) + '/guest-qr', {
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:link.value})});
          const data=await response.json();
          if(response.ok && data.qr) showQr(data.qr);
          else view.status.textContent=data.error || '二维码暂时不可用，可以复制链接加入';
        } catch (_) { view.status.textContent='二维码暂时不可用，可以复制链接加入'; }
      }
      function showQr(source) { qr.src=source;download.href=source;qr.hidden=qrCaption.hidden=download.hidden=false; }
    },

    async leave(gid) {
      if (!window.confirm('退出此群？你引入的 agent 也会退出。')) return;
      try { await request('POST', `/${encodeURIComponent(gid)}/leave`, {}); changed(); }
      catch (error) { notify(error.message); }
    },

    async toggleExternalAccess(gid) {
      try {
        const current = await request('GET', `/${encodeURIComponent(gid)}`);
        return await this.setExternalAccess(gid, current.external_access_enabled === false);
      } catch (error) { notify(error.message); }
    },

    async setExternalAccess(gid, enabled) {
      if (!enabled && !window.confirm(text(
        '暂停当前群的所有非主机连接？成员、凭证、邀请和历史全部保留，本机连接继续可用。恢复外部联网后可重新连接。',
        'Pause all non-host connections in this group? Members, credentials, invitations and history remain. Host connections continue working. External clients can reconnect when access resumes.'
      ))) return;
      try {
        const group = await request('POST', `/${encodeURIComponent(gid)}/external-access`, {enabled});
        changed(group);
        notify(enabled ? text('已恢复外部联网', 'External access resumed') : text('外部联网已暂停，成员和凭证保留', 'External access paused; members and credentials remain'));
      } catch (error) { notify(error.message); }
    },

    async disconnectExternal(gid) { return this.setExternalAccess(gid, false); },

    async removeLocal(gid, confirmRemoval = true) {
      if (confirmRemoval && !window.confirm(text(
        '从本机移除此群聊？将停止重连并清理本地凭证和聊天缓存。远端群聊及成员关系保留。',
        'Remove this group from this device? Reconnection stops and local credentials and chat cache are deleted. The server group and membership remain.'
      ))) return false;
      try {
        await request('DELETE', `/${encodeURIComponent(gid)}/local`);
        remember(gid, '');
        changed({removed_group_id: gid});
        notify(text('群聊已从本机移除', 'Group removed from this device'));
        return true;
      } catch (error) { notify(error.message); return false; }
    },
  };
})();
