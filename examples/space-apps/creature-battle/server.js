#!/usr/bin/env node
/* 精灵对战 · Creature Battle — two agents each design a creature in SVG, with its own animated
 * skill effects, and fight on their people's commands; @-mention an agent in the chat to talk to it.
 * 简介：Agent 设计精灵、四个技能与属性点；玩家点技能或发指令对战，精灵气泡展示对话。
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

const PAGE_SCRIPTS = [path.join(__dirname, 'room-sdk.js'), path.join(__dirname, 'battle.js')];

/** The page members see: the battle's own view, its chat and its input. Every URL is relative. */
function page() {
  const scripts = PAGE_SCRIPTS.map((file) => '<script>' + fs.readFileSync(file, 'utf8').replace(/<\/script/gi, '<\\/script') + '</script>').join('\n');
  return `<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>精灵对战</title><style>
${fs.readFileSync(path.join(__dirname, 'arena.css'), 'utf8')}
</style></head><body><main id="room-root"></main><section id="chat" aria-label="聊天"></section>
<div id="bar"><div id="command-context" hidden><span>给我的精灵下指令</span><button type="button" id="cancel-command" aria-label="取消出招指令">×</button></div>
<textarea id="text" rows="1" maxlength="2000" aria-label="消息"></textarea>
<button type="button" data-send aria-label="发送" title="发送"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6"/></svg></button><span id="hint" role="status"></span></div>
${scripts}
<script>
(function(){
  const root=document.getElementById('room-root'),chat=document.getElementById('chat'),text=document.getElementById('text');
  const hint=document.getElementById('hint'),send=document.querySelector('[data-send]'),context=document.getElementById('command-context');
  const seen=new Set();let composer=null,after=0,revision=-1,sending=false,command=false;
  async function api(address,body){
    // text/plain keeps a POST "simple": the sandboxed page skips the CORS preflight round trip.
    const response=await fetch(address,body===undefined?{}:{method:'POST',headers:{'Content-Type':'text/plain;charset=UTF-8'},body:JSON.stringify(body)});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.error||response.statusText);
    return data;
  }
  // Plain messages are the room's chat; protocol messages drive the battle.
  function show(rows){
    const following=chat.scrollHeight-chat.scrollTop-chat.clientHeight<32;
    for(const row of rows||[]){
      if(seen.has(row.id)||ClawCrossRooms.parseEvent(row.content))continue;
      seen.add(row.id);const line=document.createElement('p'),who=document.createElement('b');
      line.className=(client.snapshot.members||[]).some(m=>m.key===row.sender&&m.is_agent)?'agent':'';
      who.textContent=row.sender_name;line.append(who,document.createTextNode(row.content));chat.append(line);
    }
    while(chat.children.length>50)chat.firstElementChild.remove();
    if(following)chat.scrollTop=chat.scrollHeight;
  }
  function paint(){
    send.disabled=sending||command&&(!composer||!composer.canSubmit);
    context.hidden=!command;
    text.placeholder=command?'告诉我的精灵怎么行动…':'聊聊，或 @ Agent';
  }
  function commandInput(){command=true;hint.textContent='';paint();text.focus();}
  const client=new ClawCrossRooms.RoomClient({
    send:(content,mentions)=>api('send',{content,mentions}).then(refresh),
    configure:announcement=>api('configure',{announcement}).then(refresh),
    loadHistory:async()=>{const data=await api('history');show(data.messages);return data.messages;},
    onComposer:value=>{composer=value;paint();},
    onSuggest:value=>{commandInput();text.value=value;},onCommand:commandInput
  });
  const game=ClawCrossBattle.mount(root,client);
  game.chatSlot.append(chat,document.getElementById('bar'));
  document.getElementById('cancel-command').onclick=()=>{command=false;paint();text.focus();};
  function receive(data){
    if(data.revision<revision)return;
    revision=data.revision;after=Math.max(after,data.cursor);
    client.update(data.snapshot);show(data.snapshot.messages);game.update(client.snapshot);
  }
  async function refresh(){receive(await api('state?after='+after));}
  async function run(button,task){
    const value=text.value.trim();if(!value)return;
    if(sending)return;sending=true;hint.textContent='';paint();
    let failure='';
    try{await task(value);text.value='';command=false;}
    catch(error){failure=error.message||String(error);}
    finally{sending=false;paint();if(failure)hint.textContent=failure;}
  }
  // The page is sandboxed without forms, so the buttons act on click.
  send.addEventListener('click',()=>run(send,value=>command?game.submit(value):api('send',{content:value,mentions:[]}).then(refresh)));
  text.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!send.disabled)send.click();}});
  paint();
  // Network events update game state; local animations never wait for a polling timer.
  if(window.EventSource){
    const stream=new EventSource('events?after='+after);
    stream.onmessage=event=>receive(JSON.parse(event.data));
    stream.onopen=()=>{if(hint.dataset.offline){hint.textContent='';delete hint.dataset.offline;}};
    stream.onerror=()=>{hint.dataset.offline='1';hint.textContent='连接中断，正在重连…';};
    window.addEventListener('pagehide',event=>{if(!event.persisted)stream.close();});
  }else (async()=>{for(;;){await refresh().catch(()=>{});await new Promise(done=>setTimeout(done,1500));}})();

})();
</script></body></html>`;
}

/** The battle server; `space` is a connected ClawCross space. */
function battle(space) {
  const rows = [];      // the match: people's commands and chat, agents' replies
  let config = null;    // the players and rules the owner set
  let roster = { title: '', members: [] };
  const viewers = new Set();
  let revision = 0;

  const view = () => roster.members.map((m) => ({ principal: m.id, name: m.name, is_agent: m.agent, device: m.device, platform: m.platform }));
  const member = (id) => roster.members.find((m) => m.id === id);
  const refreshRoster = async () => {
    const next = await space.members();
    if(JSON.stringify(next)!==JSON.stringify(roster)){roster=next;changed();}
  };
  const append = (sender, name, content) => { const row = { id: rows.length + 1, sender, sender_name: name, content, created_at: Date.now() / 1000 }; rows.push(row); changed(); return row; };

  function snapshot(person, after=0) {
    return {cursor:rows.length, revision, snapshot:{roomId:'space',title:roster.title,
      announcement:config?Rooms.encodeConfig(config):'',members:view(),messages:rows.slice(after),
      actor:{key:person.id,name:person.name},isOwner:!!member(person.id)?.owner}};
  }
  function push(viewer) {
    if(viewer.res.writableNeedDrain || viewer.revision===revision)return;
    if(!member(viewer.person.id)){viewer.res.end();return;}
    viewer.res.write('data: '+JSON.stringify(snapshot(viewer.person,viewer.after))+'\n\n');
    viewer.after=rows.length;viewer.revision=revision;
  }
  function changed(){revision++;for(const viewer of viewers)push(viewer);}

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
    if(event.kind==='skill')return after.turn===before.turn+1 && after.log.at(-1)?.id===event.id || after.winner!==null && before.winner===null && after.log.at(-1)?.id===event.id;
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
    rows.push(row);changed();
    if(event.kind!=='skill')for (const agent of (mentions || []).filter((id) => member(id)?.agent)) await space.ask(agent, content);
    return row;
  }

  space.on('closing',()=>{for(const viewer of viewers)viewer.res.end();viewers.clear();});
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
      if (req.method === 'GET' && url.pathname === '/') return reply(200, page(), 'text/html; charset=utf-8');
      if (req.method === 'GET' && url.pathname === '/state')
        return reply(200,snapshot(person,Math.max(0,Number(url.searchParams.get('after'))||0)));
      if (req.method === 'GET' && url.pathname === '/events') {
        res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-store','X-Accel-Buffering':'no'});
        const viewer={res,person,after:Math.max(0,Number(url.searchParams.get('after'))||0),revision:-1};
        viewers.add(viewer);res.write('retry: 1000\n\n');push(viewer);
        const heartbeat=setInterval(()=>{if(!res.writableNeedDrain)res.write(': keepalive\n\n');},15000);
        heartbeat.unref();res.on('drain',()=>push(viewer));
        res.on('close',()=>{clearInterval(heartbeat);viewers.delete(viewer);});
        return;
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
        config = next;changed();
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
