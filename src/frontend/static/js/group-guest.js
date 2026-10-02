/* A human-only group client. Main-site login cookies are not used. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const ticket = location.hash.slice(1);
  const legacyStorageKey = 'group-guest:' + ticket;
  let storageKey = legacyStorageKey;
  function stored(key) {
    try { return JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch (_) { return {}; }
  }
  let saved = stored(storageKey);
  function identityKey(data) {
    if (data.identity_key) storageKey = 'group-guest-identity:' + data.identity_key;
  }
  let credential = saved.token || '', principal = '', cursor = -1, timer, polling = false;
  const seen = new Set();
  let pendingSend = null;
  let members = [], mentionRange = null, selectedMentions = [], previousDraft = '', activeOption = 0;
  function hideMentions() { el('mention-menu').hidden = true; el('text').setAttribute('aria-expanded', 'false'); mentionRange = null; }
  function updateDraft() {
    const text = el('text').value;
    let prefix = 0, suffix = 0;
    while (prefix < Math.min(previousDraft.length, text.length) && previousDraft[prefix] === text[prefix]) prefix++;
    while (suffix < Math.min(previousDraft.length, text.length) - prefix && previousDraft[previousDraft.length - 1 - suffix] === text[text.length - 1 - suffix]) suffix++;
    const end = previousDraft.length - suffix, delta = text.length - previousDraft.length;
    selectedMentions = selectedMentions.filter(m => m.end <= prefix || m.start >= end)
      .map(m => m.start >= end ? {...m,start:m.start + delta,end:m.end + delta} : m);
    previousDraft = text;
  }
  function chooseMember(member) {
    const input = el('text'), start = mentionRange ? mentionRange.start : input.selectionStart;
    const end = mentionRange ? mentionRange.end : input.selectionEnd;
    const label = '@' + member.name;
    if (input.value.length - (end - start) + label.length + 1 > input.maxLength) { status('消息已达到字数上限', true); return; }
    input.setRangeText(label + ' ', start, end, 'end'); updateDraft();
    selectedMentions.push({principal:member.principal,start,end:start + label.length,label});
    hideMentions(); input.focus();
  }
  function showMentions(query = '') {
    const options = members.filter(m => m.principal !== principal && m.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
    activeOption = 0;
    el('mention-menu').replaceChildren(...options.map(member => {
      const button = document.createElement('button'); button.type = 'button'; button.setAttribute('role', 'option');
      button.textContent = member.name; button.setAttribute('aria-selected', 'false');
      button.addEventListener('mousedown', event => event.preventDefault());
      button.addEventListener('click', () => chooseMember(member)); return button;
    }));
    if (!options.length) { hideMentions(); return; }
    el('mention-menu').hidden = false; el('text').setAttribute('aria-expanded', 'true');
    el('mention-menu').firstElementChild.setAttribute('aria-selected', 'true');
  }
  function suggestMentions() {
    const input = el('text'), before = input.value.slice(0, input.selectionStart);
    const match = /(?:^|\s)@([^@\n]*)$/.exec(before);
    if (!match) { hideMentions(); return; }
    mentionRange = {start:before.length - match[1].length - 1,end:input.selectionStart};
    showMentions(match[1]);
  }
  el('text').addEventListener('input', () => { updateDraft(); suggestMentions(); });
  el('text').addEventListener('click', suggestMentions);
  el('text').addEventListener('keydown', event => {
    if (el('mention-menu').hidden) return;
    const options = Array.from(el('mention-menu').children);
    if (event.key === 'Escape') { event.preventDefault(); hideMentions(); }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); activeOption = (activeOption + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
      options.forEach((b,i) => b.setAttribute('aria-selected', String(i === activeOption))); options[activeOption].scrollIntoView({block:'nearest'});
    }
    if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); options[activeOption].click(); }
  });
  el('mention').addEventListener('click', () => { mentionRange = null; showMentions(); });
  function status(text, error = false) { el('status').textContent = text; el('status').classList.toggle('error', error); }
  function remember() {
    try {
      const value = JSON.stringify({token: credential, name: el('name').value});
      localStorage.setItem(storageKey, value);
      // Keep old links usable while migrating existing identities to the per-group key.
      localStorage.setItem(legacyStorageKey, value);
    } catch (_) {}
  }
  async function api(action, body) {
    const response = await fetch('/group-guest-api/' + action + (action === 'state' ? '?after_id=' + cursor : ''), {
      method: body === undefined ? 'GET' : 'POST', credentials: 'omit',
      headers: {'Content-Type': 'application/json', 'X-Group-Invite': ticket, 'X-Guest-Token': credential},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) {
      let text = data.error || data.detail || '暂时无法连接群聊';
      if (typeof text !== 'string') text = '请检查名字或消息内容';
      const error = new Error(text); error.status = response.status; throw error;
    }
    return data;
  }
  function showJoin() { clearTimeout(timer); el('join').hidden = false; el('chat').hidden = true; el('rename').hidden = true; el('password-open').hidden = true; el('password-form').hidden = true; }
  function render(data) {
    identityKey(data);
    el('password-open').textContent = data.password_set ? '修改密码' : '设置密码';
    principal = data.principal; el('title').textContent = data.title; el('name').value = data.name;
    members = data.members;
    el('member-count').textContent = '群成员 · ' + data.members.length;
    el('member-list').replaceChildren(...members.map(m => {
      const li = document.createElement('li'), button = document.createElement('button');
      button.type = 'button'; button.textContent = m.name; button.setAttribute('aria-label', '提及 ' + m.name);
      button.addEventListener('click', () => { mentionRange = null; chooseMember(m); }); li.append(button); return li;
    }));
    const box = el('messages'), nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 100;
    for (const m of data.messages) {
      if (seen.has(m.id)) continue;
      seen.add(m.id);
      const article = document.createElement('article'); article.className = 'message' + (m.sender === principal ? ' own' : '');
      const by = document.createElement('div'); by.className = 'byline';
      by.textContent = m.sender_name + ' · ' + new Date(m.created_at * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
      const bubble = document.createElement('div'); bubble.className = 'bubble'; bubble.textContent = m.content;
      article.append(by, bubble); box.append(article);
      if (box.children.length > 500) box.firstElementChild.remove();
    }
    if (seen.size > 2000) { const recent = Array.from(seen).slice(-1000); seen.clear(); recent.forEach(id => seen.add(id)); }
    if (nearBottom) box.scrollTop = box.scrollHeight;
    cursor = data.cursor; remember();
  }
  async function poll() {
    if (polling || !credential) return;
    clearTimeout(timer); polling = true;
    let delay = 2000;
    try {
      const data = await api('state');
      el('join').hidden = true; el('chat').hidden = false; el('rename').hidden = false; el('password-open').hidden = false;
      render(data); status('以 ' + data.name + ' 的身份参与');
      if (data.has_more) delay = 100;
    } catch (error) {
      status(error.message, true); delay = 5000;
      if ([401,403,404].includes(error.status)) { credential = ''; remember(); showJoin(); }
    } finally { polling = false; if (credential) timer = setTimeout(poll, delay); }
  }
  el('join').addEventListener('submit', async event => {
    event.preventDefault(); const button = el('join').querySelector('button'); button.disabled = true;
    try { const data = await api('join', {name:el('name').value.trim(), password:el('password').value}); credential = data.token; el('password').value = ''; cursor = -1; seen.clear(); el('messages').replaceChildren(); remember(); await poll(); }
    catch (error) { status(error.message, true); } finally { button.disabled = false; }
  });
  el('send').addEventListener('submit', async event => {
    event.preventDefault(); const text = el('text').value.trim(); if (!text) return;
    const mentions = [...new Set(selectedMentions.filter(m => members.some(member => member.principal === m.principal) && el('text').value.slice(m.start,m.end) === m.label).map(m => m.principal))];
    const button = el('send').querySelector('button'); button.disabled = true;
    if (!pendingSend || pendingSend.content !== text || JSON.stringify(pendingSend.mentions) !== JSON.stringify(mentions)) pendingSend = {content:text, mentions, client_msg_id:crypto.randomUUID()};
    try { await api('messages', pendingSend); pendingSend = null; el('text').value = ''; selectedMentions = []; previousDraft = ''; hideMentions(); await poll(); }
    catch (error) { status(error.message, true); } finally { button.disabled = false; }
  });
  el('password-open').addEventListener('click', () => { el('password-form').hidden = false; el('new-password').focus(); });
  el('password-cancel').addEventListener('click', () => { el('password-form').hidden = true; el('new-password').value = ''; });
  el('password-form').addEventListener('submit', async event => {
    event.preventDefault(); const button = el('password-form').querySelector('button'); button.disabled = true;
    try {
      await api('password', {password:el('new-password').value});
      el('new-password').value = ''; el('password-form').hidden = true;
      el('password-open').textContent = '修改密码';
      status('密码已保存，之后可用这个名字和密码重新进入');
    } catch (error) { status(error.message, true); } finally { button.disabled = false; }
  });
  el('rename').addEventListener('click', async () => {
    const name = prompt('你的新名字', el('name').value); if (name === null) return;
    try { await api('rename', {name:name.trim()}); await poll(); } catch (error) { status(error.message, true); }
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) void poll(); });
  async function start() {
    if (!ticket) { status('请从朋友发来的分享链接进入', true); return; }
    el('name').value = saved.name || '朋友' + Math.floor(1000 + Math.random() * 9000);
    if (credential) { await poll(); return; }
    try {
      const data = await api('info', {});
      identityKey(data);
      saved = stored(storageKey);
      el('title').textContent = data.title;
      if (saved.token) {
        credential = saved.token;
        el('name').value = saved.name || el('name').value;
        await poll();
        return;
      }
      if (saved.name) el('name').value = saved.name;
      status('欢迎加入'); showJoin();
    }
    catch (error) { status(error.message, true); }
  }
  void start();
})();
