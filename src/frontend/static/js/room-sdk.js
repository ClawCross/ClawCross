/* ClawCross Rooms SDK: application-level adapters over the existing group APIs.
 * This file is also vendored by ClawCrossHub/lib/rooms. No Agent runtime changes.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.ClawCrossRooms = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const CONFIG = '[ClawCrossRoomConfig:v1]';
  const EVENT = '[ClawCrossRoom:v1]';
  const apps = new Map();
  function uid() {
    return typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2);
  }
  function parseConfig(announcement) {
    const text = String(announcement || '');
    const line = text.split('\n').find(row => row.startsWith(CONFIG));
    if (!line) return null;
    try {
      const value = JSON.parse(line.slice(CONFIG.length));
      return value && value.v === 1 && typeof value.app === 'string' && typeof value.id === 'string' ? value : null;
    } catch (_) { return null; }
  }
  function humanAnnouncement(announcement) {
    return String(announcement || '').split('\n').filter(row => !row.startsWith(CONFIG)).join('\n').trim();
  }
  function encodeConfig(config, announcement) {
    const text = humanAnnouncement(announcement);
    const result = CONFIG + JSON.stringify(config) + (text ? '\n' + text : '');
    if (result.length > 4000) throw new Error('房间配置和公告超过 4000 字符');
    return result;
  }
  function createConfig(app, actor) {
    return {v: 1, app: app, id: uid(), host: actor || {key: '', name: ''}, players: []};
  }
  function encodeEvent(event, instructions) {
    return EVENT + JSON.stringify(Object.assign({protocol: 'clawcross.room/1'}, event)) +
      (instructions ? '\n' + instructions : '');
  }
  function parseEvent(content) {
    const text = String(content || '');
    const marker = text.indexOf(EVENT);
    const start = text.indexOf('{', marker < 0 ? 0 : marker + EVENT.length);
    if (start < 0 || (marker < 0 && text.indexOf('"clawcross.room/1"') < 0)) return null;
    let depth = 0, quoted = false, escape = false;
    for (let i = start; i < Math.min(text.length, start + 16000); i++) {
      const c = text[i];
      if (quoted) {
        if (escape) escape = false;
        else if (c === '\\') escape = true;
        else if (c === '"') quoted = false;
      } else if (c === '"') quoted = true;
      else if (c === '{') depth++;
      else if (c === '}' && --depth === 0) {
        try {
          const value = JSON.parse(text.slice(start, i + 1));
          return value.protocol === 'clawcross.room/1' && typeof value.type === 'string' ? value : null;
        } catch (_) { return null; }
      }
    }
    return null;
  }
  function describeMessage(content) {
    const event = parseEvent(content);
    if (!event) return content;
    if (event.type === 'command') return event.kind === 'avatar' ? '请求生成精灵形象' : event.request || '发出对战指令';
    if (event.type === 'avatar') return '生成精灵形象' + (event.name ? ' · ' + event.name : '');
    if (event.type === 'move') return '出招：' + String(event.move || '') + (event.narration ? '\n' + event.narration : '');
    return content;
  }
  function normalizeMembers(rows) {
    const members = (rows || []).map(row => Object.assign({}, row, {
      key: String(row.principal || row.agent_id || (row.agent && row.agent.agent_id) || ''),
      name: String(row.name || (row.agent && row.agent.name) || ''),
      is_agent: !!(row.is_agent || row.agent),
      device_key: row.device !== undefined ? 'device:' + row.device :
        row.connection_id || (row.node_id ? row.node_id + ':' + row.user_id : 'local')
    }));
    for (const member of members) {
      const person = members.find(other => !other.is_agent && other.device_key === member.device_key);
      member.controller = member.controller || (person && person.name) || '';
    }
    return members;
  }
  function binding(member) {
    return {key: member.key, name: member.name, controller: member.controller || ''};
  }
  function resolveMember(ref, members) {
    if (!ref) return null;
    const exact = members.find(member => member.key === ref.key);
    if (exact) return exact;
    // Local clients translate their own principals; the unique name/controller
    // pair bridges that presentation without changing the relay protocol.
    const matches = members.filter(member => member.name === ref.name &&
      (!ref.controller || member.controller === ref.controller));
    return matches.length === 1 ? matches[0] : null;
  }
  class RoomClient {
    constructor(transport) { this.transport = transport; this.snapshot = {}; this.listeners = new Set(); }
    update(snapshot) {
      this.snapshot = Object.assign({}, snapshot, {members: normalizeMembers(snapshot.members)});
      for (const listener of this.listeners) listener(this.snapshot);
    }
    subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
    async publish(content, mentions) { return this.transport.send(content, mentions || []); }
    async sendToAgent(member, content) {
      const target = typeof member === 'string' ? this.snapshot.members.find(row => row.key === member) : member;
      if (!target || !target.is_agent) throw new Error('请选择房间中的 Agent');
      return this.publish('@' + target.name + ' ' + content, [target.key]);
    }
    async configure(config) {
      if (!this.snapshot.isOwner) throw new Error('只有房主可以设置房间玩法');
      return this.transport.configure(encodeConfig(config, this.snapshot.announcement));
    }
  }
  function registerApp(id, app) { apps.set(id, app); }
  function mountRoom(container, transport) {
    const client = new RoomClient(transport);
    let handle = null, kind = '', last = {};
    function update(snapshot) {
      last = snapshot;
      client.update(snapshot);
      const config = parseConfig(snapshot.announcement);
      const next = config ? config.app : 'chat';
      if (next !== kind) {
        if (handle) handle.destroy();
        container.replaceChildren();
        kind = next;
        handle = apps.has(next) ? apps.get(next).mount(container, client) : null;
      }
      container.hidden = !handle;
      if (handle) handle.update(client.snapshot);
    }
    return {
      update: update, client: client,
      refresh: function () { update(last); },
      destroy: function () { if (handle) handle.destroy(); container.replaceChildren(); }
    };
  }
  return {CONFIG: CONFIG, EVENT: EVENT, uid: uid, parseConfig: parseConfig, encodeConfig: encodeConfig,
    createConfig: createConfig, humanAnnouncement: humanAnnouncement, encodeEvent: encodeEvent,
    parseEvent: parseEvent, describeMessage: describeMessage, normalizeMembers: normalizeMembers,
    binding: binding, resolveMember: resolveMember, RoomClient: RoomClient, registerApp: registerApp, mountRoom: mountRoom};
});
