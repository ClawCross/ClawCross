/* A group whose card names an app shows that app in place of the chat log and input.
 * The app reaches the group only through the existing /proxy_groups endpoints. */
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
    document.getElementById('chat-page').classList.toggle('room-app-view', !!detail.app);
    handle.update({app: detail.app || null, roomId: gid, title: detail.title || '', announcement: detail.announcement || '',
      members: detail.members || currentMembers || [], messages: [...messages.values()], actor: actorOf(detail),
      isOwner: !String(detail.owner || '').startsWith('remote:')});
  }
  root.MobileRooms = {
    update(group) {
      if (!group || group.group_id !== currentGroupId) return;
      if (gid !== group.group_id) {
        root.MobileRooms.close();
        gid = group.group_id;
        const path = '/proxy_groups/' + encodeURIComponent(gid);
        handle = ClawCrossRoomHost.mountApp(document.getElementById('room-app-surface'), {
          loadApp: () => api('GET', path + '/app'),
          send: async (content, mentions) => {
            await api('POST', path + '/messages', {content: content, mentions: mentions, client_msg_id: ClawCrossRooms.uid()});
            if (currentGroupId === group.group_id) await pollNewMessages();
          },
          configure: async announcement => {
            const updated = await api('PATCH', path, {announcement: announcement});
            if (currentGroupId === group.group_id) applyGroupDetail(updated);
          },
          loadHistory: async () => (await api('GET', path + '/messages?after_id=0')).messages || []
        }, '/static/room-app-frame.html');
      }
      detail = group;
      for (const message of group.messages || []) messages.set(Number(message.id), message);
      draw();
    },
    metadata(group) {
      if (detail && group && group.group_id === gid) root.MobileRooms.update({...detail, ...group, members: group.members || detail.members});
    },
    observe(rows) {
      if (!handle || !detail) return;
      for (const row of rows || []) if (Number.isSafeInteger(Number(row.id))) messages.set(Number(row.id), row);
      draw();
    },
    close() {
      if (handle) handle.destroy();
      handle = null; detail = null; gid = ''; messages.clear();
      document.getElementById('chat-page')?.classList.remove('room-app-view');
    }
  };
})(window);
