'use strict';
// ---------------------------------------------------------------------------
// Persistence: IndexedDB, falling back to localStorage, then to memory
// ---------------------------------------------------------------------------
const LS = {
  ok: (() => { try { const k = '__mcjs_test'; localStorage.setItem(k, '1'); localStorage.removeItem(k); return true; } catch (e) { return false; } })(),
  get(k) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : null; } catch (e) { return null; } },
  set(k, v) { localStorage.setItem(k, JSON.stringify(v)); },
  keys(prefix) { const out = []; for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); if (k.startsWith(prefix)) out.push(k); } return out; },
};

const DB = {
  db: null, mem: { worlds: new Map(), chunks: new Map() }, ok: false, backend: 'memory',
  open() {
    return new Promise((res) => {
      const fallback = () => { this.backend = LS.ok ? 'local' : 'memory'; res(false); };
      let req;
      try { req = indexedDB.open('mc-js-clone', 1); } catch (e) { fallback(); return; }
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains('worlds')) db.createObjectStore('worlds', { keyPath: 'id' });
        if (!db.objectStoreNames.contains('chunks')) db.createObjectStore('chunks');
      };
      req.onsuccess = () => { this.db = req.result; this.ok = true; this.backend = 'idb'; res(true); };
      req.onerror = fallback;
      req.onblocked = fallback;
    });
  },
  tx(store, mode, fn) {
    return new Promise((res, rej) => {
      const t = this.db.transaction(store, mode), s = t.objectStore(store);
      let out;
      const r = fn(s);
      if (r && 'onsuccess' in r) r.onsuccess = () => { out = r.result; };
      t.oncomplete = () => res(out);
      t.onerror = () => rej(t.error);
    });
  },
  async listWorlds() {
    let all;
    if (this.backend === 'idb') all = await this.tx('worlds', 'readonly', s => s.getAll());
    else if (this.backend === 'local') all = LS.keys('mcjs:w:').map(k => LS.get(k)).filter(Boolean);
    else all = [...this.mem.worlds.values()];
    return (all || []).sort((a, b) => b.lastPlayed - a.lastPlayed);
  },
  async getWorld(id) {
    if (this.backend === 'idb') return this.tx('worlds', 'readonly', s => s.get(id));
    if (this.backend === 'local') return LS.get('mcjs:w:' + id);
    return this.mem.worlds.get(id);
  },
  async putWorld(w) {
    if (this.backend === 'idb') return this.tx('worlds', 'readwrite', s => s.put(w));
    if (this.backend === 'local') { LS.set('mcjs:w:' + w.id, w); return; }
    this.mem.worlds.set(w.id, w);
  },
  async deleteWorld(id) {
    if (this.backend === 'idb') {
      await this.tx('worlds', 'readwrite', s => s.delete(id));
      await this.tx('chunks', 'readwrite', s => s.delete(IDBKeyRange.bound(id + ':', id + ':￿')));
    } else if (this.backend === 'local') {
      localStorage.removeItem('mcjs:w:' + id);
      for (const k of LS.keys('mcjs:c:' + id + ':')) localStorage.removeItem(k);
    } else {
      this.mem.worlds.delete(id);
      for (const k of [...this.mem.chunks.keys()]) if (k.startsWith(id + ':')) this.mem.chunks.delete(k);
    }
  },
  async loadChunks(id) {
    const out = new Map();
    if (this.backend === 'idb') {
      await new Promise((res) => {
        const t = this.db.transaction('chunks', 'readonly'), s = t.objectStore('chunks');
        const req = s.openCursor(IDBKeyRange.bound(id + ':', id + ':￿'));
        req.onsuccess = () => {
          const c = req.result;
          if (c) { const [, cx, cz] = c.key.split(':'); out.set(ckey(+cx, +cz), c.value); c.continue(); }
        };
        t.oncomplete = res; t.onerror = res;
      });
    } else if (this.backend === 'local') {
      for (const k of LS.keys('mcjs:c:' + id + ':')) {
        const [, , , cx, cz] = k.split(':'); const v = LS.get(k);
        if (v) out.set(ckey(+cx, +cz), { b: new Uint16Array(v.b), m: new Uint16Array(v.m) });
      }
    } else {
      for (const [k, v] of this.mem.chunks) if (k.startsWith(id + ':')) { const [, cx, cz] = k.split(':'); out.set(ckey(+cx, +cz), v); }
    }
    return out;
  },
  async putChunks(id, list) {
    if (!list.length) return;
    if (this.backend === 'idb') return this.tx('chunks', 'readwrite', s => { for (const [cx, cz, v] of list) s.put(v, id + ':' + cx + ':' + cz); });
    if (this.backend === 'local') { for (const [cx, cz, v] of list) LS.set('mcjs:c:' + id + ':' + cx + ':' + cz, { b: Array.from(v.b), m: Array.from(v.m) }); return; }
    for (const [cx, cz, v] of list) this.mem.chunks.set(id + ':' + cx + ':' + cz, v);
  },
};

function encodeChunk(ch) {
  return { b: new Uint16Array(rleEncode(ch.blocks)), m: new Uint16Array(rleEncode(ch.meta)) };
}
function decodeChunk(rec, ch) {
  ch.blocks.set(rleDecode(rec.b, ch.blocks.length));
  ch.meta.set(rleDecode(rec.m, ch.meta.length));
}
