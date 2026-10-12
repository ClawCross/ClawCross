/* An example room application. Its only transport is ordinary group messages. */
(function (root, factory) {
  const sdk = typeof module === 'object' && module.exports ? require('./room-sdk.js') : root.ClawCrossRooms;
  const app = factory(sdk);
  if (typeof module === 'object' && module.exports) module.exports = app;
  if (root) root.ClawCrossBattle = app;
  sdk.registerApp('battle', app);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (Rooms) {
  'use strict';
  const SKILLS = Object.freeze([
    Object.freeze({name: '撞击', aliases: ['tackle'], type: 'normal', power: 14}),
    Object.freeze({name: '电光一闪', aliases: ['quick attack'], type: 'normal', power: 18}),
    Object.freeze({name: '喷射火焰', aliases: ['flamethrower'], type: 'fire', power: 24}),
    Object.freeze({name: '水枪', aliases: ['water gun'], type: 'water', power: 20}),
    Object.freeze({name: '十万伏特', aliases: ['thunderbolt'], type: 'electric', power: 22}),
    Object.freeze({name: '守住', aliases: ['protect'], type: 'guard', power: 0}),
    Object.freeze({name: '自我再生', aliases: ['recover'], type: 'heal', power: 22})
  ]);
  function skillFor(name) {
    const text = String(name || '').trim().toLowerCase();
    return SKILLS.find(skill => skill.name === text || skill.aliases.includes(text)) || null;
  }
  function safeSvg(raw) {
    const text = String(raw || '').trim();
    if (text.length > 6000 || !/^<svg[\s>]/i.test(text) || !/<\/svg>\s*$/i.test(text)) return '';
    if (/<!DOCTYPE|<!ENTITY|<script\b|<foreignObject\b|<image\b|<iframe\b|<style\b|\bon\w+\s*=|javascript:|@import/i.test(text)) return '';
    for (const match of text.matchAll(/\b(?:href|src)\s*=\s*["']([^"']*)["']/gi)) if (!match[1].startsWith('#')) return '';
    for (const match of text.matchAll(/url\s*\(([^)]*)\)/gi)) if (!match[1].trim().replace(/^["']|["']$/g, '').startsWith('#')) return '';
    if (typeof DOMParser === 'undefined') return text;
    const doc = new DOMParser().parseFromString(text, 'image/svg+xml');
    const node = doc.documentElement;
    if (doc.querySelector('parsererror') || node.localName !== 'svg') return '';
    const allowed = new Set(['svg','g','path','rect','circle','ellipse','line','polyline','polygon','text','tspan',
      'title','desc','defs','lineargradient','radialgradient','stop','clippath','mask','pattern','use','filter',
      'fegaussianblur','fedropshadow','feoffset','femerge','femergenode','fecolormatrix','feblend','fecomposite','feflood']);
    for (const element of doc.querySelectorAll('*')) {
      if (element.namespaceURI !== 'http://www.w3.org/2000/svg' || !allowed.has(element.localName.toLowerCase())) return '';
      for (const attr of Array.from(element.attributes)) {
        if (/^on/i.test(attr.name)) return '';
        if (['href','src'].includes(attr.localName.toLowerCase()) && !attr.value.startsWith('#')) return '';
      }
    }
    node.setAttribute('width', '384'); node.setAttribute('height', '384');
    return new XMLSerializer().serializeToString(node);
  }
  function damage(skill, defender) {
    const strong = {fire: 'grass', grass: 'water', water: 'fire', electric: 'water'};
    const multiplier = strong[skill.type] === defender.element ? 1.25 : 1;
    return Math.max(1, Math.round(skill.power * multiplier * (defender.guard ? .35 : 1)));
  }
  function allowedController(person, fighter, config, members) {
    if (!person || person.is_agent || !fighter) return false;
    if (config.owner_only) return person.device_key === fighter.device_key;
    const host = Rooms.resolveMember(config.host, members);
    return !!(host && host.key === person.key) || person.name === fighter.controller;
  }
  const RULES = '【精灵对战规则】\n双方形象生成后，由房主点击开始，随后轮流出招。每个 Agent 只接受自己主人的出招指令；房主可为双方准备形象。主人身份以群成员身份为准，不凭消息中自称的身份判断。系统发送的出招请求会附上主人标记；按指定格式通过本群回复，伤害由固定技能表结算。';
  class BattleModel {
    rebuild(config, members, messages) {
      this.config = config;
      this.members = Rooms.normalizeMembers(members);
      const refs = Array.isArray(config.players) ? config.players.slice(0, 2) : [];
      const agents = refs.map(ref => Rooms.resolveMember(ref, this.members));
      const fighters = refs.map((ref, index) => ({ref: ref, agent: agents[index], hp: 100, guard: false,
        name: ref.name || '精灵', svg: '', element: 'normal'}));
      this.state = {fighters: fighters, started: !config.manual_start, turn: 1, active: 0, pending: null, awaiting: {}, winner: null, log: [], invalid: ''};
      if (fighters.length !== 2 || agents.some(agent => !agent || !agent.is_agent) || agents[0].key === agents[1].key) return this.state;
      const rows = [...new Map((messages || []).map(message => [Number(message.id), message])).values()]
        .sort((a, b) => Number(a.id) - Number(b.id));
      for (const message of rows) this.apply(message);
      return this.state;
    }
    apply(message) {
      const s = this.state, cfg = this.config;
      const speaker = this.members.find(member => member.key === String(message.sender));
      if (!speaker) return;
      let event = Rooms.parseEvent(message.content);
      const side = s.fighters.findIndex(fighter => fighter.agent && fighter.agent.key === speaker.key);
      if (!event && side >= 0) {
        const svg = /<svg[\s\S]*?<\/svg>/i.exec(String(message.content || ''));
        const move = /^(?:出招|招式|move|skill)\s*[:：]\s*([^\n]+)$/im.exec(String(message.content || ''));
        if (svg && s.awaiting[s.fighters[side].ref.key]) event = {type: 'avatar', room: cfg.id,
          id: s.awaiting[s.fighters[side].ref.key], svg: svg[0], name: s.fighters[side].name};
        else if (move && s.pending && s.active === side) event = {type: 'move', room: cfg.id,
          id: s.pending.id, turn: s.turn, move: move[1], narration: ''};
      }
      if (!event || event.room !== cfg.id) return;
      const host = Rooms.resolveMember(cfg.host, this.members);
      if (event.type === 'start') {
        if (!speaker.is_agent && host && host.key === speaker.key && s.fighters.every(f => f.svg)) s.started = true;
        return;
      }
      if (event.type === 'command') {
        const index = s.fighters.findIndex(fighter => fighter.ref.key === event.fighter);
        const permitted = event.kind === 'avatar' && host && host.key === speaker.key && !speaker.is_agent ||
          index >= 0 && allowedController(speaker, s.fighters[index].agent, cfg, this.members);
        if (index < 0 || typeof event.id !== 'string' || !event.id || event.id.length > 160 ||
            !permitted) return;
        if (event.kind === 'avatar') s.awaiting[event.fighter] = event.id;
        else if (event.kind === 'turn' && s.started && event.turn === s.turn && index === s.active && s.winner === null &&
                 (!s.pending || s.invalid) &&
                 s.fighters.every(fighter => fighter.svg)) {
          s.pending = {id: event.id, fighter: index, request: String(event.request || '')};
          s.invalid = '';
        }
        return;
      }
      if (!speaker.is_agent || side < 0) return;
      const fighter = s.fighters[side];
      if (event.type === 'avatar' && s.awaiting[fighter.ref.key] === event.id) {
        const svg = safeSvg(event.svg);
        if (!svg) { s.invalid = '形象需要是安全的 SVG 绘图，请重新生成。'; return; }
        fighter.svg = svg; fighter.name = String(event.name || fighter.ref.name).slice(0, 40);
        fighter.element = ['normal','fire','water','grass','electric'].includes(event.element) ? event.element : 'normal';
        delete s.awaiting[fighter.ref.key];
      } else if (event.type === 'move' && s.pending && event.id === s.pending.id && side === s.active &&
                 event.turn === s.turn && s.winner === null) {
        const skill = skillFor(event.move);
        if (!skill) { s.invalid = '未识别的招式：' + String(event.move || '').slice(0, 40) + '。请从技能表选择。'; return; }
        const foe = s.fighters[1 - side];
        let result;
        if (skill.type === 'guard') { fighter.guard = true; result = '守住：下一次受到的伤害减少 65%'; }
        else if (skill.type === 'heal') {
          const recovered = Math.min(100 - fighter.hp, skill.power); fighter.hp += recovered; result = '回复 ' + recovered + ' HP';
        } else {
          const amount = damage(skill, foe); foe.hp = Math.max(0, foe.hp - amount); foe.guard = false;
          result = '造成 ' + amount + ' 点伤害';
        }
        s.log.push({turn: s.turn, side: side, text: fighter.name + ' · ' + skill.name + ' · ' + result,
          narration: String(event.narration || '').slice(0, 300)});
        s.pending = null; s.invalid = '';
        if (foe.hp === 0) s.winner = side;
        else { s.active = 1 - side; s.turn++; }
      }
    }
  }
  function prompt(event, state, fighter) {
    const base = '这是群聊上的精灵对战房间。只通过本群的正常回复通道回应，不需要读写文件、运行命令或下载资源。\n';
    const frame = {protocol: 'clawcross.room/1', type: event.kind === 'avatar' ? 'avatar' : 'move',
      room: event.room, id: event.id};
    if (event.kind === 'avatar') return base +
      '为自己设计一个原创精灵，回复名字、属性和 SVG 形象。SVG 不超过 4000 字符，只用绘图元素、渐变和文字，不能含脚本、外部资源、image 或 foreignObject。\n' +
      '请保留 room 和 id，按以下格式发送一条群消息：\n' + Rooms.encodeEvent(Object.assign(frame,
        {name: '你的精灵名字', element: 'fire', svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">你的绘图</svg>'}));
    return base + '【主人指令】主人：' + event.controller + '；目标 Agent：' + fighter.ref.name + '。只处理自己主人的本轮指令。\n用户指令：' + event.request + '\n当前回合：' + state.turn +
      '\n战斗状态：' + JSON.stringify(state.fighters.map(row => ({name: row.name, hp: row.hp, element: row.element, guard: row.guard}))) +
      '\n从固定技能表中选择一个出招；伤害和胜负由房间规则计算，不要自己改 HP：\n' + JSON.stringify(SKILLS) +
      '\n请保留 room、id 和 turn，按以下格式发送一条群消息：\n' + Rooms.encodeEvent(Object.assign(frame,
        {turn: state.turn, move: '撞击', narration: '用一句话描述行动'}));
  }
  const CSS = '.cc-room-battle [hidden]{display:none!important}.cc-room-battle{color:var(--text,#17252d);font-family:inherit;padding:14px;min-width:0;background:var(--bg-card,#fff);border-bottom:1px solid rgba(100,116,139,.15)}' +
    '.cc-room-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}.cc-room-head strong{font-size:15px}.cc-room-tag{font-size:11px;border-radius:30px;padding:5px 9px;background:#e8f6ef;color:#16704b}' +
    '.cc-room-setup{display:grid;grid-template-columns:1fr 1fr auto;gap:7px;align-items:end;margin-bottom:12px}.cc-room-setup label{font-size:11px;display:block;min-width:0}.cc-room-setup select{width:100%;margin-top:4px;padding:8px;border:1px solid #cbd5e1;border-radius:9px;background:var(--bg-card,#fff);color:inherit;min-width:0}' +
    '.cc-room-btn{font:inherit;font-size:12px;border:0;border-radius:10px;padding:10px 12px;cursor:pointer;background:#14745b;color:white;white-space:nowrap}.cc-room-btn:disabled{opacity:.45;cursor:default}.cc-room-btn.secondary{background:#edf2f5;color:#405367}' +
    '.cc-room-stage{position:relative;border-radius:20px;background:radial-gradient(ellipse at 50% 95%,#517e65 0%,#203e4e 45%,#101b34 100%);padding:18px 10px 12px;color:white;overflow:hidden}.cc-room-stage:before{content:"";position:absolute;inset:70% -20% -100%;border:1px solid #8db9a333;border-radius:50%;background:#a7d5b012}' +
    '.cc-room-fighters{display:grid;grid-template-columns:1fr 1fr;gap:20px;position:relative}.cc-room-fighter{min-width:0;text-align:center}.cc-room-fighter.active .cc-room-image{filter:drop-shadow(0 0 18px #bdf5d844)}.cc-room-fighter small{opacity:.65;font-size:10px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
    '.cc-room-name{font-size:14px;font-weight:650;margin-top:7px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cc-room-image{height:145px;max-width:100%;width:145px;object-fit:contain;position:relative;animation:cc-room-idle 3s ease-in-out infinite}.cc-room-placeholder{height:145px;display:flex;align-items:center;justify-content:center;font-size:12px;opacity:.55}' +
    '.cc-room-health{height:6px;background:#ffffff20;border-radius:6px;overflow:hidden;margin:9px 8px 5px}.cc-room-health span{display:block;height:100%;background:linear-gradient(90deg,#56dbab,#c6facd);transition:width .45s}.cc-room-hp{font-size:10px;font-variant-numeric:tabular-nums;opacity:.85}' +
    '.cc-room-status{text-align:center;font-size:12px;position:relative;padding:12px 0 2px;color:#d2eee2;min-height:28px}.cc-room-log{font-size:11px;line-height:1.6;margin-top:10px;max-height:82px;overflow:auto;color:var(--text-secondary,#576778)}.cc-room-log p{margin:3px 0}.cc-room-log strong{color:inherit}' +
    '.cc-room-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:10px}.cc-room-footer [data-start]{padding:10px 20px;border-radius:99px;box-shadow:0 4px 12px #14745b22}.cc-room-error{font-size:12px;color:#c94444;margin-top:8px;min-height:0}' +
    '.cc-room-skills{font-size:11px;margin-top:9px;color:var(--text-secondary,#64748b)}.cc-room-skills summary{cursor:pointer}.cc-room-skills div{display:flex;flex-wrap:wrap;gap:5px;padding-top:7px}.cc-room-skills span{padding:4px 7px;border:1px solid #ccd8d6;border-radius:7px}' +
    '.dark .cc-room-battle,[data-theme="dark"] .cc-room-battle{--bg-card:#15212b;--text:#e0eaf1;--text-secondary:#a6b6c3}.dark .cc-room-btn.secondary{background:#263644;color:#d6e2ed}' +
    '@keyframes cc-room-idle{0%,100%{transform:translateY(2px)}50%{transform:translateY(-5px)}}@media(max-width:500px){.cc-room-battle{padding:10px}.cc-room-setup{grid-template-columns:1fr 1fr}.cc-room-setup button{grid-column:1/-1}.cc-room-image,.cc-room-placeholder{height:110px}.cc-room-image{width:110px}.cc-room-fighters{gap:10px}.cc-room-stage{padding-top:12px}}@media(prefers-reduced-motion:reduce){.cc-room-image{animation:none}.cc-room-health span{transition:none}}';
  function mount(container, client) {
    if (!document.getElementById('cc-room-battle-style')) {
      const style = document.createElement('style'); style.id = 'cc-room-battle-style'; style.textContent = CSS; document.head.append(style);
    }
    const root = document.createElement('section'); root.className = 'cc-room-battle';
    root.innerHTML = '<div class="cc-room-head"><strong>精灵对战</strong><div><button class="cc-room-btn secondary" data-settings>对阵</button> <span class="cc-room-tag">Agent × Agent</span></div></div>' +
      '<div class="cc-room-setup"><label>左方 Agent<select data-player="0"></select></label><label>右方 Agent<select data-player="1"></select></label><button class="cc-room-btn" data-prepare>生成双方精灵</button></div>' +
      '<div class="cc-room-stage"><div class="cc-room-fighters"></div><div class="cc-room-status" role="status"></div></div>' +
      '<div class="cc-room-footer"><button class="cc-room-btn" data-start>▶ 开始对战</button><details class="cc-room-skills"><summary>技能与规则</summary><div></div></details></div><div class="cc-room-log"></div><div class="cc-room-error" role="status"></div>';
    container.append(root);
    for (const skill of SKILLS) { const chip = document.createElement('span'); chip.textContent = skill.name + (skill.power ? ' · ' + skill.power : ' · 防守'); root.querySelector('.cc-room-skills div').append(chip); }
    const model = new BattleModel(), cache = new Map();
    let snapshot = {}, config = null, busy = false, synced = !client.transport.loadHistory, historyRoom = '', destroyed = false, rosterSignature = '', paintKey = '', settingsOpen = false;
    const errorBox = root.querySelector('.cc-room-error');
    const selects = [...root.querySelectorAll('select')];
    const prepare = root.querySelector('[data-prepare]'), start = root.querySelector('[data-start]');
    function ingest(messages) { for (const message of messages || []) cache.set(Number(message.id), message); }
    function state() { return model.rebuild(config, snapshot.members, [...cache.values()]); }
    function render() {
      if (!config || destroyed) return;
      const key = JSON.stringify([config, cache.size, snapshot.actor, snapshot.isOwner,
        (snapshot.members || []).map(m => [m.key,m.principal,m.name,m.is_agent,m.controller,m.device,m.connection_id]),
        busy,synced,settingsOpen]);
      if (key === paintKey) return;
      paintKey = key;
      const s = state(), members = model.members;
      const signature = JSON.stringify(members.filter(m => m.is_agent).map(m => [m.key,m.name,m.controller]));
      if (signature !== rosterSignature) {
        rosterSignature = signature;
        const agents = members.filter(m => m.is_agent);
        selects.forEach((select,index) => {
          const previous = select.value;
          select.replaceChildren();
          for (const member of agents) {
            const option = document.createElement('option'); option.value = member.key;
            option.textContent = member.name + (member.controller ? ' · ' + member.controller : ''); select.append(option);
          }
          const chosen = Rooms.resolveMember((config.players || [])[index], members);
          select.value = chosen ? chosen.key : agents.some(m => m.key === previous) ? previous : (agents[index] || agents[0] || {}).key || '';
        });
      }
      const ready = s.fighters.length === 2 && s.fighters.every(f => f.agent && f.svg);
      root.querySelector('.cc-room-setup').hidden = !snapshot.isOwner || (ready && !settingsOpen);
      root.querySelector('[data-settings]').hidden = !snapshot.isOwner;
      prepare.disabled = busy || members.filter(m => m.is_agent).length < 2;
      prepare.textContent = (config.players || []).length ? '重新生成 / 新对局' : '生成双方精灵';
      const field = root.querySelector('.cc-room-fighters'); field.replaceChildren();
      for (let index = 0; index < 2; index++) {
        const fighter = s.fighters[index];
        const card = document.createElement('div'); card.className = 'cc-room-fighter' + (index === s.active ? ' active' : '');
        const label = document.createElement('div'); label.className = 'cc-room-name'; label.textContent = fighter ? fighter.name : (index ? '右方精灵' : '左方精灵');
        if (fighter && fighter.svg) {
          const image = document.createElement('img'); image.className = 'cc-room-image'; image.alt = fighter.name;
          image.src = 'data:image/svg+xml;charset=UTF-8,' + encodeURIComponent(fighter.svg); card.append(image);
        } else {
          const placeholder = document.createElement('div'); placeholder.className = 'cc-room-placeholder';
          placeholder.textContent = fighter && s.awaiting[fighter.ref.key] ? 'Agent 正在生成…' : '等待精灵登场'; card.append(placeholder);
        }
        card.append(label);
        const owner = document.createElement('small'); owner.textContent = fighter ? fighter.ref.name + (fighter.ref.controller ? ' · ' + fighter.ref.controller : '') : '加入房间中的 Agent'; card.append(owner);
        const bar = document.createElement('div'); bar.className = 'cc-room-health';
        const hp = document.createElement('span'); hp.style.width = (fighter ? fighter.hp : 100) + '%'; bar.append(hp); card.append(bar);
        const number = document.createElement('div'); number.className = 'cc-room-hp'; number.textContent = (fighter ? fighter.hp : 100) + ' / 100 HP'; card.append(number); field.append(card);
      }
      const status = root.querySelector('.cc-room-status');
      status.textContent = !synced ? '正在同步对战记录…' : s.winner !== null ? s.fighters[s.winner].name + ' 获胜！' :
        !ready ? '先让双方 Agent 生成精灵形象' : !s.started ? '双方已就绪 · 等待房主开始对战' :
        '第 ' + s.turn + ' 回合 · ' + s.fighters[s.active].name + (s.pending ? ' 正在选择招式…' : ' 行动');
      const me = members.find(m => m.key === (snapshot.actor || {}).key);
      const permitted = ready && allowedController(me, s.fighters[s.active].agent, config, members);
      start.hidden = !snapshot.isOwner || !ready || s.started;
      start.disabled = busy || !synced;
      const hint = !synced ? '正在同步对战记录' : !ready ? '等待双方形象生成' : !s.started ? '等待房主点击开始对战' :
        s.winner !== null ? '对局结束，可在对阵中开启新对局' : s.pending && !s.invalid ? '等待 ' + s.fighters[s.active].name + ' 回复' :
        permitted ? '指挥 ' + s.fighters[s.active].ref.name + ' · 第 ' + s.turn + ' 回合' : '等待 ' + s.fighters[s.active].ref.controller + ' 指挥';
      client.setComposer({kind:'battle', label: hint, canSubmit: !!(synced && !busy && permitted && s.started && s.winner === null && (!s.pending || s.invalid)),
        placeholder: '告诉你的精灵怎么行动，例如：抓住机会，用喷射火焰'});
      const log = root.querySelector('.cc-room-log'); log.replaceChildren();
      for (const item of s.log.slice(-4)) {
        const row = document.createElement('p'), title = document.createElement('strong');
        title.textContent = item.text; row.append(title);
        if (item.narration) row.append(document.createTextNode(' — ' + item.narration)); log.append(row);
      }
      if (s.invalid && !errorBox.textContent) errorBox.textContent = s.invalid;
    }
    root.querySelector('[data-settings]').onclick = () => {settingsOpen = !settingsOpen; render();};
    async function act(fn) {
      if (busy) return;
      busy = true; errorBox.textContent = ''; render();
      try { await fn(); }
      catch (error) { errorBox.textContent = error.message || String(error); throw error; }
      finally { busy = false; render(); }
    }
    async function issue(index, kind, request) {
      const s = state(), target = s.fighters[index];
      if (!target || !target.agent) throw new Error('请选择房间中的两个不同 Agent');
      const event = {type:'command', kind:kind, room:config.id, id:Rooms.uid(),
        fighter:target.ref.key, turn:s.turn, request:request || '', controller:snapshot.actor.name, agent_name:target.ref.name};
      await client.sendToAgent(target.agent, Rooms.encodeEvent(event, prompt(event, s, target)));
    }
    prepare.onclick = () => act(async () => {
      const members = model.members;
      const chosen = selects.map(select => members.find(m => m.key === select.value));
      if (chosen.some(m => !m) || chosen[0].key === chosen[1].key) throw new Error('请先加入并选择两个不同 Agent');
      const next = Rooms.createConfig('battle', snapshot.actor);
      next.players = chosen.map(Rooms.binding);
      // The owner-managed announcement is already delivered to Agents by the
      // existing group runtime. Keep human text while publishing game rules.
      const existing = Rooms.humanAnnouncement(snapshot.announcement);
      if (!existing.includes('【精灵对战规则】')) snapshot.announcement = Rooms.encodeConfig(config, existing ? existing + '\n\n' + RULES : RULES);
      client.snapshot.announcement = snapshot.announcement;
      await client.configure(next);
      config = next; snapshot.announcement = Rooms.encodeConfig(next, snapshot.announcement); render();
      await Promise.all([issue(0, 'avatar'), issue(1, 'avatar')]);
    }).catch(() => {});
    start.onclick = () => act(async () => {
      const s = state();
      if (!snapshot.isOwner || !synced || s.started || !s.fighters.every(f => f.svg)) return;
      await client.publish(Rooms.encodeEvent({type:'start', room:config.id, id:Rooms.uid()}));
    }).catch(() => {});
    return {
      submit: async function (text) {
        const request = String(text || '').trim();
        if (!client.composer || !client.composer.canSubmit) throw new Error(client.composer?.label || '尚未开始对战');
        if (!request || request.length > 2000) throw new Error('出招指令需在 1–2000 字符之间');
        await act(() => issue(state().active, 'turn', request));
      },
      update: function (next) {
        snapshot = next; config = Rooms.parseConfig(next.announcement); ingest(next.messages);
        if (!config) return;
        if (client.transport.loadHistory && historyRoom !== next.roomId) {
          historyRoom = next.roomId; synced = false;
          Promise.resolve(client.transport.loadHistory()).then(messages => {
            if (destroyed) return; ingest(messages); synced = true; render();
          }).catch(error => { if (!destroyed) { errorBox.textContent = '无法同步记录：' + error.message; } });
        }
        render();
      },
      destroy: function () { destroyed = true; client.setComposer(null); root.remove(); cache.clear(); }
    };
  }
  return {label: '精灵对战', SKILLS: SKILLS, skillFor: skillFor, safeSvg: safeSvg, BattleModel: BattleModel, mount: mount};
});
