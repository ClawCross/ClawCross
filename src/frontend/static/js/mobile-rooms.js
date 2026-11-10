/* A space whose card names an app shows the app's site in place of the chat log and input.
 * The site is the group server's tunnel to the app server the space's owner runs; it opens
 * sandboxed, so it never acts as this page. The last few app spaces stay loaded while hidden,
 * so switching back is instant, and each space's entrance is reused for most of its day. */
(function (root) {
  'use strict';
  const KEEP = 3;                    // app pages kept loaded, most recent first
  const ENTRANCE_MS = 23 * 3600e3;   // an entrance is valid for a day
  const frames = new Map();          // gid -> {frame, name}
  const entrances = new Map();       // gid -> {url, until}
  let gid = '';
  const surface = () => document.getElementById('room-app-surface');
  function notice(text) {
    let status = surface().querySelector('.room-app-notice');
    if (!status) {
      status = document.createElement('p');
      status.className = 'room-app-notice'; status.setAttribute('role', 'status');
      surface().append(status);
    }
    status.textContent = text; status.hidden = !text;
  }
  function drop(id) {
    frames.get(id)?.frame.remove();
    frames.delete(id);
  }
  async function entrance(id) {
    const known = entrances.get(id);
    if (known && known.until > Date.now()) return known.url;
    const { url } = await api('POST', '/proxy_groups/' + encodeURIComponent(id) + '/app-session');
    entrances.set(id, { url, until: Date.now() + ENTRANCE_MS });
    return url;
  }
  function show(group) {
    document.getElementById('chat-page').classList.toggle('room-app-view', !!group.app);
    // A metadata poll must not hide the active iframe: doing so blurs its
    // focused field even when the same iframe is shown again immediately.
    for (const [id, entry] of frames) if (id !== gid && !entry.frame.hidden) entry.frame.hidden = true;
    notice('');
    if (!group.app) { root.closeAppFullscreen?.(); drop(gid); return; }   // the app was stopped: plain chat again
    const name = group.app.name || '小程序';
    if (!group.app.online) { drop(gid); notice('「' + name + '」暂时不在线'); return; }
    const kept = frames.get(gid);
    if (kept && kept.name === name) {
      frames.delete(gid); frames.set(gid, kept);   // most recent last
      if (kept.frame.hidden) kept.frame.hidden = false;
      return;
    }
    drop(gid);   // another app now runs in this space
    const selected = gid, frame = document.createElement('iframe');
    frame.title = name;
    frame.setAttribute('sandbox', 'allow-scripts allow-forms allow-popups allow-modals allow-downloads allow-pointer-lock');
    frame.referrerPolicy = 'no-referrer';
    frames.set(selected, { frame, name });
    surface().append(frame);
    while (frames.size > KEEP) drop(frames.keys().next().value);
    entrance(selected).then(url => {
      if (frames.get(selected)?.frame === frame) frame.src = url;
    }, error => {
      drop(selected);
      if (gid === selected) notice('小程序打开失败：' + error.message);
    });
  }
  root.MobileRooms = {
    update(group) {
      if (!group || group.group_id !== currentGroupId) return;
      gid = group.group_id;
      show(group);
    },
    metadata(group) {
      if (group && group.group_id === gid) show(group);
    },
    close() {
      root.closeAppFullscreen?.();
      gid = '';
      for (const entry of frames.values()) entry.frame.hidden = true;
      if (surface()) notice('');
      document.getElementById('chat-page')?.classList.remove('room-app-view');
    }
  };
})(window);
