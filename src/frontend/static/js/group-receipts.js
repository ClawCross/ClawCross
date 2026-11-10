/* Visible conversations advance read positions; background clients only receive messages. */
(() => {
  'use strict';
  let current = {}, timer;
  const read = new Map();
  const sending = new Map(), flushing = new Set();
  const scope = gid => GroupChatState.scope(location.origin, gid, selfPrincipal());
  const retryable = error => !error.status || error.status >= 500 || [408,425,429].includes(error.status);
  async function send(gid, body) {
    const key = scope(gid), saved = await GroupChatState.load(key);
    const same = saved.outbox.find(row => {
      const {client_msg_id, failed, ...payload} = row;
      return JSON.stringify(payload) === JSON.stringify(body);
    });
    const message = {...body, client_msg_id:same?.client_msg_id || crypto.randomUUID()};
    await GroupChatState.queue(key, message);
    return transmit(gid, key, message);
  }
  function transmit(gid, key, message) {
    const id = key + ':' + message.client_msg_id;
    if (sending.has(id)) return sending.get(id);
    const task = (async () => { try {
      const result = await api('POST', `/proxy_groups/${encodeURIComponent(gid)}/messages`, message);
      if (!result.app) GroupChatState.confirm(result.message?.id);
      await GroupChatState.sent(key, message.client_msg_id);
      return result;
    } catch (error) {
      if (!retryable(error)) await GroupChatState.failed(key, message.client_msg_id);
      throw error;
    } })();
    sending.set(id, task);
    task.then(() => sending.delete(id), () => sending.delete(id));
    return task;
  }
  async function flush(gid) {
    const key = scope(gid);
    if (flushing.has(key)) return;
    flushing.add(key);
    try {
      for (const message of (await GroupChatState.load(key)).outbox.filter(row => !row.failed).slice(0,4)) {
        await transmit(gid, key, message);
      }
    } catch (_) { /* Keep transient failures queued across refreshes. */ }
    finally { flushing.delete(key); }
  }
  function paint() {
    const body = document.getElementById('chat-body');
    if (!body || current.group_id !== currentGroupId) return;
    for (const bubble of body.querySelectorAll('[data-group-self="true"]')) {
      let label = bubble.parentElement.querySelector('.message-receipt');
      if (!label) { label = document.createElement('small'); label.className = 'message-receipt'; bubble.parentElement.append(label); }
      label.textContent = current.capabilities?.receipts === 'device-v1'
        ? GroupChatState.label(current.receipts, selfPrincipal(), Number(bubble.dataset.groupMessageId)) : '';
    }
    schedule();
  }
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const body = document.getElementById('chat-body'), gid = current.group_id;
      if (document.hidden || !body?.clientHeight || gid !== currentGroupId || current.capabilities?.receipts !== 'device-v1') return;
      const bounds = body.getBoundingClientRect();
      if (bounds.width <= 0 || bounds.bottom <= 0 || bounds.top >= innerHeight) return;
      const id = Math.max(0, ...[...body.querySelectorAll('[data-group-message-id]')].filter(row => {
        const rect = row.getBoundingClientRect(); return rect.bottom > bounds.top && rect.top < bounds.bottom;
      }).map(row => Number(row.dataset.groupMessageId)));
      if (id <= (read.get(gid) || 0)) return;
      try { await api('POST', `/proxy_groups/${encodeURIComponent(gid)}/receipts`, {read_cursor:id}); read.set(gid, Math.max(read.get(gid) || 0, id)); }
      catch (_) { /* The background receive cursor may still be catching up. */ }
    }, 200);
  }
  window.GroupReceipts = {send, update(detail) {
    current = {...detail}; paint();
    if (detail.group_id === currentGroupId && detail.capabilities?.message_idempotency) void flush(detail.group_id);
  }};
  document.addEventListener('DOMContentLoaded', () => {
    const body = document.getElementById('chat-body');
    if (body) { body.addEventListener('scroll', schedule); new MutationObserver(paint).observe(body, {childList:true}); }
    document.addEventListener('visibilitychange', schedule);
  });
})();
