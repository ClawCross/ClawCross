/* Shared group activity is metadata, never a chat message or a model request. */
(() => {
  'use strict';
  let current = [], groupId = '', started = new Map();
  const zh = () => document.documentElement.lang.toLowerCase().startsWith('zh');
  const working = n => zh() ? `${n} 位伙伴正在处理` : `${n} ${n === 1 ? 'agent' : 'agents'} working`;
  function tick() {
    const timer = document.getElementById('group-running-elapsed');
    if (!timer || !current.length || document.hidden) return;
    const since = Math.min(...current.map(a => started.get(a.principal || a.agent_id || a.name)));
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - since));
    timer.textContent = seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  }
  function update(agents, gid) {
    const now = Date.now() / 1000;
    if (groupId !== gid) started.clear();
    groupId = gid;
    current = (agents || []).filter(a => a.state === 'running');
    const next = new Map();
    for (const agent of current) {
      const key = agent.principal || agent.agent_id || agent.name;
      const supplied = Number(agent.started_at);
      next.set(key, supplied > 0 && supplied <= now ? supplied : started.get(key) || now);
    }
    started = next;
    const label = document.getElementById('typing-text');
    const bar = document.getElementById('typing-bar');
    const chip = document.getElementById('chat-running-status');
    const names = current.slice(0, 2).map(a => a.name || a.agent_id).join(zh() ? '、' : ', ');
    if (label) label.textContent = current.length ? (current.length <= 2
      ? (zh() ? `${names} 正在处理` : `${names} ${current.length === 1 ? 'is' : 'are'} working`)
      : working(current.length)) : '';
    if (bar) bar.classList.toggle('show', Boolean(current.length));
    if (chip) { chip.hidden = !current.length; chip.textContent = current.length ? working(current.length) : ''; }
    const row = [...document.querySelectorAll('.swipe-wrapper[data-gid]')].find(el => el.dataset.gid === gid);
    if (row) {
      row.dataset.remoteWorkingCount = String(current.filter(a => a.remote).length);
      const summary = row.querySelector('.group-activity-label');
      if (summary) { summary.hidden = !current.length; summary.textContent = working(current.length); }
    }
    const active = new Set(current.map(a => a.principal || a.agent_id));
    for (const member of document.querySelectorAll('[data-group-principal]')) {
      const running = active.has(member.dataset.groupPrincipal);
      member.classList.toggle('is-working', running);
      const dot = member.querySelector('.agent-activity-dot');
      if (dot) { dot.hidden = !running; dot.setAttribute('aria-label', zh() ? '正在处理' : 'Working'); }
    }
    tick();
  }
  function detail(card) {
    const members = (card.members || []).filter(m => m.is_agent &&
      (m.agent?.status?.state || m.activity?.state) === 'running');
    update(members.map(m => ({principal: m.principal, agent_id: m.agent?.agent_id || m.agent_id,
      name: m.name, state: 'running', remote: m.remote,
      started_at: m.agent?.status?.started_at || m.activity?.started_at})), card.group_id);
    const avatar = document.getElementById('chat-room-avatar');
    if (avatar) {
      const all = card.members || [];
      const agents = all.filter(m => m.is_agent);
      const single = agents.length === 1 && (card.kind === 'direct' || all.length <= 2);
      const portraits = single ? agents : all.slice(0, 2);
      avatar.dataset.single = String(single);
      const signature = JSON.stringify([single,...portraits.map(m=>[m.principal,m.name,m.avatar])]);
      if (avatar.dataset.avatarSignature !== signature) {
      avatar.dataset.avatarSignature = signature;
      avatar.replaceChildren(...portraits.map(member => {
        const cell = document.createElement('span');
        if (window.Avatars) {
          cell.innerHTML = Avatars.markup(member.avatar,member.name,{principal:member.principal,groupId:card.group_id});
          return cell;
        }
        const picture = String(member.avatar || '').trim();
        if (single && picture.startsWith('<svg')) {
          const img = document.createElement('img');
          img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(picture);
          img.alt = ''; img.className = 'chat-room-avatar-image'; cell.append(img);
        } else {
          cell.textContent = (Array.from(member.name || '')[0] || '·').toLocaleUpperCase();
        }
        return cell;
      }));
      }
    }
    if (window.Avatars) for (const box of document.querySelectorAll('[data-avatar-sender]')) {
      const person = (card.members || []).find(m=>m.principal===box.dataset.avatarSender || m.agent_id===box.dataset.avatarSender);
      if (person && box.querySelector('[data-avatar]')?.dataset.avatar !== (person.avatar || ''))
        box.innerHTML = Avatars.markup(person.avatar,person.name,{principal:person.principal,groupId:card.group_id});
    }
  }
  window.GroupPresence = {update, detail};
  setInterval(tick, 1000);
})();
