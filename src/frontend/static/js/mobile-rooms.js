/* Presentation adapter only: existing /proxy_groups endpoints remain unchanged. */
(function (root) {
  'use strict';
  let handle = null, detail = null, messages = new Map(), gid = '';
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
    update(group) {
      if (!group || group.group_id !== currentGroupId) return;
      if (gid !== group.group_id) {
        apiUI.close();
        gid = group.group_id;
        const selected = gid;
        handle = ClawCrossRooms.mountRoom(document.getElementById('room-app-surface'), {
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
      const surface = document.getElementById('room-app-surface');
      if (surface) surface.hidden = true;
    }
  };
  root.MobileRooms = apiUI;
})(window);
