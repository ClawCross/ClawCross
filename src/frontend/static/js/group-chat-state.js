/* Durable browser transport state. No credentials or model keys are stored here. */
(() => {
  'use strict';
  let database;
  function open() {
    return database ||= new Promise((resolve, reject) => {
      const request = indexedDB.open('clawcross-group-transport', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('chats');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => { database = null; reject(request.error); };
    });
  }
  async function state(scope, edit) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction('chats', edit ? 'readwrite' : 'readonly'), store = tx.objectStore('chats');
      const request = store.get(scope);
      let saved;
      request.onsuccess = () => {
        saved = request.result || {messages: [], cursor: 0, outbox: []};
        if (edit && edit(saved) !== false) store.put(saved, scope);
      };
      tx.oncomplete = () => resolve(saved);
      tx.onerror = tx.onabort = () => reject(tx.error || new Error('无法保存群聊，请检查浏览器存储空间'));
    });
  }
  window.GroupChatState = {
    confirm(id) {
      if (!Number.isSafeInteger(id) || id <= 0) throw Object.assign(new Error('未收到有效的发送确认，保留消息等待重试'), {status:502});
    },
    scope: (base, group, principal) => JSON.stringify([base, group, principal]),
    device() {
      const key = 'clawcross-group-device';
      let id = localStorage.getItem(key);
      if (!id) { id = 'br_' + crypto.randomUUID().replaceAll('-', ''); localStorage.setItem(key, id); }
      return id;
    },
    load: scope => state(scope),
    receive: (scope, cursor, messages) => state(scope, saved => {
      const rows = new Map(saved.messages.map(message => [message.id, message]));
      if (cursor <= saved.cursor && messages.every(message => rows.has(message.id))) return false;
      for (const message of messages) rows.set(message.id, message);
      saved.messages = [...rows.values()].sort((a,b) => a.id-b.id).slice(-500);
      saved.cursor = Math.max(saved.cursor, cursor);
    }),
    queue: (scope, message) => state(scope, saved => {
      const old = saved.outbox.find(row => row.client_msg_id === message.client_msg_id);
      if (old) old.failed = false; else saved.outbox.push(message);
    }),
    sent: (scope, id) => state(scope, saved => { saved.outbox = saved.outbox.filter(row => row.client_msg_id !== id); }),
    failed: (scope, id) => state(scope, saved => { const row = saved.outbox.find(row => row.client_msg_id === id); if (row) row.failed = true; }),
    label(receipts, principal, id) {
      const others = (receipts || []).filter(row => row.principal !== principal);
      const read = others.filter(row => row.read_cursor >= id).length;
      return read ? `${read} 人已读` : others.some(row => row.received_cursor >= id) ? '已送达' : '已发送';
    }
  };
})();
