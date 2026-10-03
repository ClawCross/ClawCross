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
  function changed(group) {
    window.dispatchEvent(new CustomEvent('group-network-changed', { detail: group }));
  }

  window.GroupNetworkUI = {
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
        const data = await request('POST', `/${encodeURIComponent(gid)}/guest-link`, {});
        link.value = data.url;
        remember(gid, data.url);
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
      function refresh() {
        const has = Boolean(link.value);
        copy.hidden = !has;
        stop.hidden = !has;
        create.textContent = has ? '换一个新链接' : '生成邀请链接';
        create.className = 'gn-btn ' + (has ? 'gn-btn-secondary' : 'gn-btn-primary');
      }
      refresh();
      const actions = el('div', 'gn-actions');
      actions.append(copy, create, stop);
      const steps = el('ul', 'gn-steps');
      for (const text of [
        '朋友用浏览器打开链接，取个名字就能聊天，不需要账号。',
        '装了 ClawCross 的朋友在「加入群聊」粘贴链接，加入后还能带上自己的 agent。',
        '链接 30 天内可用来加入；换新链接或停止邀请后旧链接失效，已加入的人不受影响。',
      ]) steps.append(el('li', '', text));
      view.body.append(link, actions, steps);
    },

    async leave(gid) {
      if (!window.confirm('退出此群？你引入的 agent 也会退出。')) return;
      try { await request('POST', `/${encodeURIComponent(gid)}/leave`, {}); changed(); }
      catch (error) { notify(error.message); }
    },
  };
})();
