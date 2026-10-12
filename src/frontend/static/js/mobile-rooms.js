/* A space whose card names an app shows the app's site in place of the chat log and input.
 * The site is the group server's tunnel to the app server the space's owner runs; it opens
 * sandboxed, so it never acts as this page. */
(function (root) {
  'use strict';
  let gid = '', frame = null;
  function notice(text) {
    const status = document.createElement('p');
    status.className = 'room-app-notice'; status.setAttribute('role', 'status'); status.textContent = text;
    document.getElementById('room-app-surface').replaceChildren(status);
    frame = null;
  }
  function show(group) {
    document.getElementById('chat-page').classList.toggle('room-app-view', !!group.app);
    if (!group.app) { document.getElementById('room-app-surface').replaceChildren(); frame = null; return; }
    if (!group.app.online) { notice('「' + (group.app.name || '小程序') + '」暂时不在线'); return; }
    if (frame) return;
    const selected = gid;
    frame = document.createElement('iframe');
    api('POST', '/proxy_groups/' + encodeURIComponent(selected) + '/app-session').then(({url}) => {
      if (gid !== selected || !frame) return;
      frame.title = group.app.name || '小程序';
      frame.setAttribute('sandbox', 'allow-scripts allow-forms allow-popups allow-modals allow-downloads');
      frame.referrerPolicy = 'no-referrer';
      frame.src = url;
      document.getElementById('room-app-surface').replaceChildren(frame);
    }, error => { if (gid === selected) notice('小程序打开失败：' + error.message); });
  }
  root.MobileRooms = {
    update(group) {
      if (!group || group.group_id !== currentGroupId) return;
      if (gid !== group.group_id) { root.MobileRooms.close(); gid = group.group_id; }
      show(group);
    },
    metadata(group) {
      if (group && group.group_id === gid) show(group);
    },
    close() {
      gid = ''; frame = null;
      document.getElementById('room-app-surface')?.replaceChildren();
      document.getElementById('chat-page')?.classList.remove('room-app-view');
    }
  };
})(window);
