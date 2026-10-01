/* Shared, explicit group joins. Passwords stay in the form until submitted. */
(() => {
  'use strict';
  async function request(method, path, body) {
    const response = await fetch('/proxy_groups' + path, {
      method, headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || data.error || '群聊请求失败');
    return data;
  }
  function dialog(title, fields, action, submit) {
    const root = document.createElement('dialog');
    root.className = 'group-network-dialog';
    root.innerHTML = `<form><header><strong></strong><button type="button" aria-label="关闭">×</button></header><div class="group-network-fields"></div><p role="status"></p><button type="submit" class="group-network-submit"></button></form>`;
    root.querySelector('strong').textContent = title;
    root.querySelector('button[type=submit]').textContent = action;
    const form = root.querySelector('form');
    root.querySelector('.group-network-fields').append(...fields);
    const status = root.querySelector('[role=status]');
    const close = () => { root.close(); root.remove(); };
    root.querySelector('button[type=button]').onclick = close;
    root.addEventListener('cancel', () => root.remove());
    form.onsubmit = async (event) => {
      event.preventDefault();
      const button = root.querySelector('button[type=submit]');
      button.disabled = true; status.textContent = '正在连接…';
      try { await submit(); close(); } catch (error) { status.textContent = error.message; }
      finally { button.disabled = false; }
    };
    document.body.append(root); root.showModal();
    return status;
  }
  function field(label, name, options = {}) {
    const wrapper = document.createElement('label');
    wrapper.textContent = label;
    const input = document.createElement(options.multiline ? 'textarea' : 'input');
    input.name = name;
    Object.assign(input, { type: 'text', autocomplete: 'off', ...options });
    wrapper.append(input);
    return { wrapper, input };
  }
  function note(text) { const node = document.createElement('p'); node.textContent = text; return node; }
  async function changed(group) {
    window.dispatchEvent(new CustomEvent('group-network-changed', { detail: group }));
  }
  window.GroupNetworkUI = {
    join() {
      const server = field('服务器地址（留空为本机）', 'server', { placeholder: 'http://192.168.1.10:51203', maxLength: 512 });
      const gid = field('群号', 'group', { required: true, maxLength: 100 });
      const password = field('群密码（本机默认不需要）', 'password', { type: 'password', maxLength: 256 });
      dialog('加入群聊', [server.wrapper, gid.wrapper, password.wrapper,
        note('加入后，你可以发言；你的 agent 只有在你手动添加后才会收到群消息。')], '加入', async () => {
        const group = await request('POST', '/join', { server_url: server.input.value.trim(), group_id: gid.input.value.trim(), password: password.input.value, agents: [] });
        password.input.value = '';
        await changed(group);
      });
    },
    async sharing(gid) {
      try {
        const [invite, group] = await Promise.all([request('GET', `/${encodeURIComponent(gid)}/invite`), request('GET', `/${encodeURIComponent(gid)}`)]);
        const address = field('给朋友的服务器地址', 'address', { value: invite.server_url, readOnly: true });
        const id = field('群号', 'group', { value: invite.group_id, readOnly: true });
        const password = field('设置新密码（留空关闭远程加入）', 'password', { type: 'password', maxLength: 256 });
        const revoke = field('同时断开其他成员现有连接', 'revoke', { type: 'checkbox' });
        const hint = note('更改密码不会默认踢出已加入的成员。服务器需要开放该地址的端口才能跨设备加入。');
        const fields = [address.wrapper, id.wrapper, note(invite.password_enabled ? '当前已开放密码加入。' : '当前未开放远程加入。')];
        const localOwner = !String(group.owner || '').startsWith('remote:');
        if (localOwner) fields.push(password.wrapper, revoke.wrapper, hint);
        dialog(localOwner ? '邀请朋友加入' : '群聊地址', fields, localOwner ? '保存' : '完成', async () => {
          if (localOwner) {
            await request('POST', `/${encodeURIComponent(gid)}/sharing`, { password: password.input.value, local_join: group.local_join, revoke_connections: revoke.input.checked });
            password.input.value = '';
            await changed();
          }
        });
      } catch (error) { if (window.toast) window.toast(error.message); else window.alert(error.message); }
    },
    async leave(gid) {
      if (!window.confirm('退出此群？你引入的 agent 也会退出。')) return;
      try { await request('POST', `/${encodeURIComponent(gid)}/leave`, {}); await changed(); }
      catch (error) { if (window.toast) window.toast(error.message); else window.alert(error.message); }
    },
  };
})();
