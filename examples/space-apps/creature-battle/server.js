#!/usr/bin/env node
/* 精灵对战 · Creature Battle — two agents each design a creature in SVG, with its own animated
 * skill effects, and fight on their people's commands; @-mention an agent in the chat to talk to it.
 * 简介：两个 agent 各画一只带技能特效的精灵，听主人指挥对战；聊天里 @ agent 可以召唤它说话。
 *
 *   node examples/space-apps/creature-battle/server.js --server <group server> --key <app key>
 *
 * It keeps the match (the log of people's commands and agents' replies), decides who may act,
 * asks the agents through the space and serves the page members see. The match lives in memory;
 * restarting the server starts a new one.
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { connect } = require('../../../sdk/js/clawcross-space.js');
const Rooms = require('./room-sdk.js');
const Battle = require('./battle.js');
const { THEME } = require('../../../sdk/js/space-app-theme.js');

const PAGE_SCRIPTS = [path.join(__dirname, 'room-sdk.js'), path.join(__dirname, 'battle.js')];

/** The page members see: the battle's own view, its chat and its input. Every URL is relative. */
function page() {
  const scripts = PAGE_SCRIPTS.map((file) => '<script>' + fs.readFileSync(file, 'utf8').replace(/<\/script/gi, '<\\/script') + '</script>').join('\n');
  return `<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>精灵对战</title><style>
${THEME}
html,body{height:100%}body{height:100dvh;display:flex;flex-direction:column}
main#room-root{flex:1;min-height:0;overflow:auto;width:100%;padding:24px 24px 16px}.cc-room-battle{min-height:100%;box-sizing:border-box}
#chat{max-height:24%;overflow:auto;padding:12px max(24px,calc((100vw - 952px)/2));font-size:13px;line-height:1.8;border-top:1px solid var(--line);background:var(--card)}#chat:empty{display:none}
#chat p{margin:6px 0;overflow-wrap:anywhere}#chat b{font-weight:650;margin-right:8px}#chat p.agent b{color:var(--good)}
#summon{display:flex;flex-wrap:wrap;gap:6px;grid-column:1/-1}#summon:empty{display:none}#summon button{min-height:32px;padding:5px 10px;font-size:12px;border-radius:9px}
#bar{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:10px;padding:14px max(24px,calc((100vw - 952px)/2));padding-bottom:max(14px,env(safe-area-inset-bottom));border-top:1px solid var(--line);background:var(--card)}
#hint{grid-column:1/-1;font-size:12px;color:var(--muted);overflow-wrap:anywhere}
#text{font:inherit;resize:none;border-radius:14px;padding:12px 14px;min-width:0;background:var(--bg);color:var(--fg)}
#bar button{font-size:13px;padding:10px 16px;background:var(--surface);color:var(--fg)}#bar button[data-command]{background:var(--accent);color:var(--on-accent)}
@media(max-width:600px){main#room-root{padding:16px 12px 12px}#chat{padding:10px 16px}#bar{padding:12px 12px max(12px,env(safe-area-inset-bottom));gap:8px}#bar button{padding:10px 12px}#text{font-size:16px}}
</style></head><body><main id="room-root"></main><section id="chat" aria-label="聊天"></section>
<div id="bar"><span id="hint" role="status"></span><div id="summon" aria-label="召唤 agent"></div><textarea id="text" rows="1" maxlength="2000" aria-label="消息"></textarea>
<button type="button" data-command disabled>出招</button><button type="button" data-chat>聊天</button></div>
${scripts}
<script>
(function(){
  const root=document.getElementById('room-root'),chat=document.getElementById('chat'),text=document.getElementById('text');
  const hint=document.getElementById('hint'),command=document.querySelector('[data-command]'),talk=document.querySelector('[data-chat]');
  const seen=new Set(),summon=document.getElementById('summon');let composer=null,after=0,agents='';
  async function api(address,body){
    // text/plain keeps a POST "simple": the sandboxed page skips the CORS preflight round trip.
    const response=await fetch(address,body===undefined?{}:{method:'POST',headers:{'Content-Type':'text/plain;charset=UTF-8'},body:JSON.stringify(body)});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.error||response.statusText);
    return data;
  }
  // Plain messages are the room's chat; protocol messages drive the battle.
  function show(rows){
    for(const row of rows||[]){
      if(seen.has(row.id)||ClawCrossRooms.parseEvent(row.content))continue;
      seen.add(row.id);const line=document.createElement('p'),who=document.createElement('b');
      line.className=(client.snapshot.members||[]).some(m=>m.key===row.sender&&m.is_agent)?'agent':'';
      who.textContent=(line.className?'🤖 ':'')+row.sender_name;line.append(who,document.createTextNode(row.content));chat.append(line);
    }
    while(chat.children.length>50)chat.firstElementChild.remove();
    chat.scrollTop=chat.scrollHeight;
  }
  function paint(){
    command.disabled=!composer||!composer.canSubmit;
    hint.textContent=composer?composer.label:'';
    text.placeholder=composer&&composer.canSubmit?composer.placeholder:'和大家聊聊';
  }
  const client=new ClawCrossRooms.RoomClient({
    send:(content,mentions)=>api('send',{content,mentions}).then(refresh),
    configure:announcement=>api('configure',{announcement}).then(refresh),
    loadHistory:async()=>{const data=await api('history');show(data.messages);return data.messages;},
    onComposer:value=>{composer=value;paint();},
    onSuggest:value=>{text.value=value;text.focus();}
  });
  const game=ClawCrossBattle.mount(root,client);
  // The summoner chat: @name (or @所有人) brings that agent into the conversation.
  function chips(){
    const names=(client.snapshot.members||[]).filter(m=>m.is_agent).map(m=>m.name);
    if(names.join('|')===agents)return;agents=names.join('|');
    summon.replaceChildren(...[...names,'所有人'].map(name=>{const b=document.createElement('button');b.type='button';b.textContent='@'+name;
      b.onclick=()=>{text.value=(text.value.trim()?text.value.trim()+' ':'')+'@'+name+' ';text.focus();};return b;}));
  }
  async function refresh(){
    const data=await api('state?after='+after);after=data.cursor;
    client.update(data.snapshot);show(data.snapshot.messages);chips();game.update(client.snapshot);
  }
  async function run(button,task){
    const value=text.value.trim();if(!value)return;
    button.disabled=true;
    try{await task(value);text.value='';button.disabled=false;paint();}
    catch(error){button.disabled=false;paint();hint.textContent=error.message||String(error);}
  }
  // The page is sandboxed without forms, so the buttons act on click.
  command.addEventListener('click',()=>run(command,value=>game.submit(value)));
  talk.addEventListener('click',()=>run(talk,value=>api('send',{content:value,mentions:[]}).then(refresh)));
  paint();
  // One refresh at a time: the next starts 1.5 s after the last one came back, so slow replies never pile up.
  (async()=>{try{await refresh();}catch(error){hint.textContent=error.message;}
    for(;;){await new Promise(done=>setTimeout(done,1500));await refresh().catch(()=>{});}})();
})();
</script></body></html>`;
}

/** The battle server; `space` is a connected ClawCross space. */
function battle(space) {
  const rows = [];      // the match: people's commands and chat, agents' replies
  let config = null;    // the players and rules the owner set
  let roster = { title: '', members: [] };
  const html = page();

  const view = () => roster.members.map((m) => ({ principal: m.id, name: m.name, is_agent: m.agent, device: m.device, platform: m.platform }));
  const member = (id) => roster.members.find((m) => m.id === id);
  const refreshRoster = async () => { roster = await space.members(); };
  const append = (sender, name, content) => { const row = { id: rows.length + 1, sender, sender_name: name, content, created_at: Date.now() / 1000 }; rows.push(row); return row; };

  /** The summoner chat: a message naming @agent (or @所有人) asks those agents to answer in the chat. */
  async function summon(person, content) {
    const agents = roster.members.filter((m) => m.agent && (/@(所有人|all)(?![\w-])/i.test(content) || content.includes('@' + m.name)));
    if (!agents.length) return;
    const chat = rows.filter((r) => !Rooms.parseEvent(r.content)).slice(-15).map((r) => r.sender_name + '：' + r.content).join('\n');
    for (const agent of agents) {
      await space.ask(agent.id, `你是「${roster.title}」精灵对战里的参战 agent「${agent.name}」，正在召唤师聊天里和大家说话。\n` +
        `最近的聊天：\n${chat}\n\n${person.name} 提到了你。直接回复一两句话，口吻符合你的身份；你的回复会原样显示在聊天区，不要加名字前缀，也不要使用任何协议格式。`);
    }
  }

  /** Whether the rules accept this person's command now, judged by the same model the page uses. */
  function accepted(event, row) {
    const before = new Battle.BattleModel().rebuild(config, view(), rows);
    const after = new Battle.BattleModel().rebuild(config, view(), [...rows, row]);
    if (event.type === 'start') return !before.started && after.started;
    if (event.type !== 'command') return false;
    return event.kind === 'avatar' ? after.awaiting[event.fighter] === event.id : after.pending?.id === event.id && before.pending?.id !== event.id;
  }

  async function send(person, { content, mentions }) {
    if (typeof content !== 'string' || !content.trim() || content.length > 65536) throw Object.assign(new Error('消息无效'), { status: 400 });
    const event = Rooms.parseEvent(content);
    if (!event) {
      const row = append(person.id, person.name, content);
      await summon(person, content);
      return row;
    }
    if (!config) throw Object.assign(new Error('群主还没有设置对局'), { status: 409 });
    const row = { id: rows.length + 1, sender: person.id, sender_name: person.name, content, created_at: Date.now() / 1000 };
    if (!accepted(event, row)) throw Object.assign(new Error('现在不能这样操作'), { status: 403 });
    rows.push(row);
    for (const agent of (mentions || []).filter((id) => member(id)?.agent)) await space.ask(agent, content);
    return row;
  }

  space.on('agent', ({ agent, name, content }) => append(agent, name, content));
  space.on('member', () => refreshRoster().catch(() => {}));

  async function handle(req, res) {
    // The page itself may be cached; the match and actions never are.
    const reply = (status, body, type = 'application/json; charset=utf-8') =>
      res.writeHead(status, { 'Content-Type': type, 'Cache-Control': type.startsWith('text/html') ? 'private, max-age=600' : 'no-store' })
        .end(type.startsWith('application/json') ? JSON.stringify(body) : body);
    try {
      const url = new URL(req.url, 'http://space');
      const person = { id: req.member.id, name: req.member.name };
      const owner = !!member(person.id)?.owner;
      if (req.method === 'GET' && url.pathname === '/') return reply(200, html, 'text/html; charset=utf-8');
      if (req.method === 'GET' && url.pathname === '/state') {
        const after = Number(url.searchParams.get('after')) || 0;
        return reply(200, { cursor: rows.length, snapshot: {
          roomId: 'space', title: roster.title, announcement: config ? Rooms.encodeConfig(config) : '', members: view(),
          messages: rows.slice(after), actor: { key: person.id, name: person.name }, isOwner: owner } });
      }
      if (req.method === 'GET' && url.pathname === '/history') return reply(200, { messages: rows });
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const body = JSON.parse(Buffer.concat(chunks).toString() || '{}');
      if (req.method === 'POST' && url.pathname === '/send') return reply(200, await send(person, body));
      if (req.method === 'POST' && url.pathname === '/configure') {
        if (!owner) return reply(403, { error: '只有群主可以设置对局' });
        const next = Rooms.parseConfig(body.announcement);
        if (!next) return reply(400, { error: '对局设置无效' });
        config = next;
        return reply(200, { ok: true });
      }
      return reply(404, { error: '没有这个页面' });
    } catch (error) {
      reply(error.status || 500, { error: error.message || String(error) });
    }
  }
  return { handle, refreshRoster, rows };
}

async function main() {
  const args = Object.fromEntries(process.argv.slice(2).flatMap((value, index, all) => value.startsWith('--') ? [[value.slice(2), all[index + 1]]] : []));
  const space = await connect({ server: args.server || process.env.CLAWCROSS_SPACE_SERVER,
    key: args.key || process.env.CLAWCROSS_SPACE_KEY, name: '精灵对战' });
  const app = battle(space);
  await app.refreshRoster();
  await space.serve(app.handle);
  space.on('offline', (error) => console.error('暂时连不上群聊服务器：' + error.message));
  space.on('closed', (error) => { console.error(error.message); process.exit(1); });
  console.log('精灵对战已连接到空间「' + (await space.members()).title + '」');
}

if (require.main === module) main().catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { battle, page };
