/* Presentation adapter only: existing /proxy_groups endpoints remain unchanged. */
(function (root) {
  'use strict';
  let handle = null, detail = null, messages = new Map(), gid = '', composer = null, mode = 'command';
  function paintComposer() {
    const row = document.getElementById('room-composer');
    if (!row) return;
    row.hidden = !composer;
    row.querySelector('[data-room-hint]').textContent = mode === 'command' && composer ? composer.label : '消息发送到房间';
    row.querySelectorAll('[data-room-mode]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.roomMode === mode)));
    const input = document.getElementById('msg-input');
    if (input && !selectedWorkflow) input.placeholder = composer && mode === 'command' ? composer.placeholder : t('input_placeholder');
  }
  function actorOf(group) {
    const person = (group.members || []).find(member => !member.is_agent && !member.agent &&
      (member.remote === false || member.principal === selfPrincipal()));
    return {key: person ? person.principal : selfPrincipal(), name: person ? person.name : window.__currentUser || ''};
  }
  function draw() {
    if (!handle || !detail) return;
    handle.update({roomId: gid, announcement: detail.announcement || '', members: detail.members || currentMembers || [],
      messages: [...messages.values()], actor: actorOf(detail), isOwner: !String(detail.owner || '').startsWith('remote:')});
  }
  const apiUI = {
    setMode(value) {mode = value === 'chat' ? 'chat' : 'command'; paintComposer();},
    commandSelected() {return !!composer && mode === 'command';},
    async submit(text) {
      if (!apiUI.commandSelected()) return false;
      await handle.submit(text);
      return true;
    },
    update(group) {
      if (!group || group.group_id !== currentGroupId) return;
      if (gid !== group.group_id) {
        apiUI.close();
        gid = group.group_id;
        const selected = gid;
        handle = ClawCrossRooms.mountRoom(document.getElementById('room-app-surface'), {
          onComposer: value => {composer = value; paintComposer();},
          onSuggest: text => {
            apiUI.setMode('command');
            const input=document.getElementById('msg-input');input.value=text;input.focus();input.dispatchEvent(new Event('input',{bubbles:true}));
          },
          send: async (content, mentions) => {
            await api('POST', '/proxy_groups/' + encodeURIComponent(selected) + '/messages',
              {content: content, mentions: mentions, client_msg_id: ClawCrossRooms.uid()});
            if (currentGroupId === selected) await pollNewMessages();
          },
          configure: async announcement => {
            const updated = await api('PATCH', '/proxy_groups/' + encodeURIComponent(selected), {announcement: announcement});
            if (currentGroupId === selected) applyGroupDetail(updated);
          },
          loadHistory: async () => {
            const data = await api('GET', '/proxy_groups/' + encodeURIComponent(selected) + '/messages?after_id=0');
            return data.messages || [];
          }
        });
      }
      detail = group;
      for (const message of group.messages || []) messages.set(Number(message.id), message);
      draw();
    },
    metadata(group) {
      if (detail && group && group.group_id === gid) apiUI.update({...detail, ...group, members: group.members || detail.members});
    },
    observe(rows) {
      if (!handle || !detail) return;
      for (const row of rows || []) if (Number.isSafeInteger(Number(row.id))) messages.set(Number(row.id), row);
      draw();
    },
    close() {
      if (handle) handle.destroy();
      handle = null; detail = null; gid = ''; messages.clear();
      composer = null; mode = 'command'; paintComposer();
      const surface = document.getElementById('room-app-surface');
      if (surface) surface.hidden = true;
    }
  };
  root.MobileRooms = apiUI;
  // A running Flask frontend can retain the HTML template in Jinja's cache.
  // Static assets are refreshed independently. Upgrade the old presentation
  // here so publishing a room app never requires restarting Agent services.
  root.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('room-composer')) return;
    const wrap = document.querySelector('#chat-page .input-bar-wrap');
    if (!wrap) return;
    const row = document.createElement('div');
    row.id = 'room-composer'; row.className = 'room-composer'; row.hidden = true;
    row.innerHTML = '<div class="room-composer-modes" role="group" aria-label="消息用途"><button type="button" data-room-mode="chat" aria-pressed="false">聊天</button><button type="button" data-room-mode="command" aria-pressed="true">出招</button></div><span data-room-hint role="status"></span>';
    row.querySelectorAll('button').forEach(button => button.onclick = () => apiUI.setMode(button.dataset.roomMode));
    wrap.prepend(row);
    const style = document.createElement('style');
    style.textContent = '.room-composer[hidden]{display:none!important}.room-composer{display:flex;align-items:center;gap:10px;padding:6px 12px 2px;min-width:0}.room-composer-modes{display:flex;flex-shrink:0;padding:3px;border-radius:99px;background:var(--ui-surface-3)}.room-composer-modes button{border:0;border-radius:99px;padding:5px 13px;font:inherit;font-size:12px;color:var(--text-secondary);background:transparent;cursor:pointer}.room-composer-modes button[aria-pressed="true"]{background:var(--bg-card);color:var(--primary);box-shadow:0 1px 5px #00000010;font-weight:600}.room-composer [data-room-hint]{min-width:0;font-size:11px;color:var(--text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}';
    document.head.append(style);
    const normalSend = root.sendMessage;
    root.sendMessage = async function (...args) {
      if (!apiUI.commandSelected() || selectedWorkflow) return normalSend.apply(this, args);
      const input = document.getElementById('msg-input'), text = input.value.trim();
      if (!text) return;
      if (pendingAttachments.length || mcPendingAudios.length || selectedOasisContexts.length) {
        toast('附件和工作流信息请切换到聊天后发送'); return;
      }
      const button = document.getElementById('send-btn');
      if (button.disabled) return;
      button.disabled = true;
      try {
        const sessions = (currentMembers || []).filter(m => m.agent).map(m => String(m.agent.agent_id));
        const approval = await ClawcrossApproval.reply(text, sessions);
        if (approval) {
          input.value = ''; toast(t('approval_done')); await refreshPendingApprovalsForCurrentGroup(); return;
        }
        await apiUI.submit(text);
        input.value = ''; mentionState.picked.clear(); clearGroupReply(); closeInputActions(); scrollToBottom();
      } catch (error) { toast(error.message); }
      finally { button.disabled = false; }
    };
  });
})(window);
