#!/usr/bin/env node
/* 故事接龙 · Story Relay — people and agents take turns writing one story. Every fourth paragraph an
 * agent paints an SVG illustration of the scene; at the end an agent writes the finale.
 * 简介：人和 agent 轮流写故事，agent 配插图和结局。
 *
 *   node examples/space-apps/story-relay/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse, safeSvg } = require('../../../sdk/js/space-app.js');

const PAGE = page({
  title: '故事接龙',
  style: `.para{margin:10px 0;line-height:1.8}.para .by{font-size:12px;color:var(--muted)}.art{display:flex;justify-content:center;margin:12px 0}
.art img{width:min(100%,420px);border-radius:14px;background:#fff}.turn{outline:2px solid var(--accent)}`,
  body: `<h1>故事接龙</h1><p class="muted">人和 agent 轮流写同一个故事，每 4 段配一张 agent 画的插图。</p><div id="setup" class="card"></div><div id="story"></div>`,
  script: `
const {api,watch,act,el,fill,keep,svg}=App,$=id=>document.getElementById(id);
function render(s){
  const t=s.state;
  $('setup').hidden=!(s.owner&&t.status!=='writing');
  if(!$('setup').hidden&&!$('setup').childElementCount)fill($('setup'),el('input',{id:'title',style:'width:100%',placeholder:'故事标题，例如：最后一班地铁'}),
    el('textarea',{id:'opening',rows:3,placeholder:'开头一段',style:'margin-top:6px'}),el('button',{style:'margin-top:6px',onclick:()=>act(()=>api('begin',{title:$('title').value,opening:$('opening').value}))},'开始接龙'));
  const now=t.order[t.turn];
  fill($('story'),t.title?el('h2',{},t.title):null,
    t.order.length?el('div',{class:'row'},...t.order.map((p,i)=>el('span',{class:'chip'+(i===t.turn&&t.status==='writing'?' turn':'')},(p.agent?'🤖 ':'')+p.name))):null,
    ...t.parts.map(p=>p.svg?el('div',{class:'art'},svg(p.svg,{alt:p.by+' 的插图'})):el('div',{class:'para'},el('div',{class:'by'},p.by),el('div',{class:'pre'},p.text))),
    t.status==='writing'&&now?(now.id===s.me.id?el('div',{class:'card'},keep('next',()=>el('textarea',{id:'next',rows:3,placeholder:'接着写一段（200 字内）'})),
      el('button',{onclick:()=>act(()=>{const v=$('next').value;$('next').value='';return api('write',{text:v});})},'写好了')):el('p',{class:'muted'},now.name+' 正在写…')):null,
    s.owner&&t.status==='writing'?el('div',{class:'row'},now&&!now.agent&&now.id!==s.me.id?el('button',{class:'ghost',onclick:()=>act(()=>api('skip',{}))},'跳过 '+now.name):null,
      el('button',{class:'ghost',onclick:()=>act(()=>api('finale',{}))},'请 agent 写结局')):null,
    t.status==='done'?el('button',{class:'ghost',onclick:()=>navigator.clipboard?.writeText(t.title+'\\n\\n'+t.parts.filter(p=>p.text).map(p=>p.text).join('\\n\\n'))},'复制全文'):null);
}
watch(render);`
});

function setup(app) {
  const tale = { status: 'idle', title: '', parts: [], order: [], turn: 0, painter: 0 };
  app.page(PAGE);
  app.state(() => tale);
  const text = () => tale.parts.filter((p) => p.text).map((p) => p.text).join('\n\n');
  const busy = () => tale.status !== 'writing';

  function next() {
    tale.turn = (tale.turn + 1) % tale.order.length;
    app.changed();
    if (tale.parts.filter((p) => p.text).length % 4 === 0) void paint();
    void agentTurn();
  }

  async function agentTurn() {
    const p = tale.order[tale.turn];
    if (busy() || !p?.agent) return;
    // A picture may land while the agent writes; only paragraphs move the story on.
    const written = () => tale.parts.filter((x) => x.text).length;
    const turn = tale.turn, count = written();
    let line;
    try {
      line = String(await app.ask(p.id, `你在空间「${app.title}」和大家接龙写故事《${tale.title}》。目前的故事：\n${text()}\n\n` +
        '接着写下一段（80–150 字），顺着情节推进，可以制造一点意外，但别收尾。只输出这一段正文。')).trim().slice(0, 600);
    } catch { line = '（没写出来）'; }
    if (busy() || tale.turn !== turn || written() !== count) return;
    tale.parts.push({ by: p.name, text: line });
    next();
  }

  /** An agent illustrates the latest scene; the story doesn't wait for it. */
  async function paint() {
    const agents = app.agents();
    if (!agents.length) return;
    const painter = agents[tale.painter++ % agents.length];
    const scene = tale.parts.filter((p) => p.text).slice(-2).map((p) => p.text).join('\n');
    try {
      const reply = await app.askJSON(painter.id, `为故事《${tale.title}》的这个场景画一张插图：\n${scene}\n` +
        '用一张 SVG（viewBox="0 0 400 260"），色彩和构图表现氛围，不要文字，不要脚本、图片或外部资源，8000 字以内。\n格式：{"svg":"<svg ...>…</svg>"}');
      const picture = safeSvg(reply?.svg);
      if (picture && tale.status !== 'idle') { tale.parts.push({ by: painter.name, svg: picture }); app.changed(); }
    } catch { /* no picture this time */ }
  }

  app.route('POST', '/begin', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以开头', 403);
    if (tale.status === 'writing') throw refuse('故事还在写');
    const title = String(body.title || '').trim().slice(0, 40), opening = String(body.opening || '').trim().slice(0, 400);
    if (!title || !opening) throw refuse('写下标题和开头');
    const humans = app.humans(), agents = app.agents();
    const order = [];
    for (let i = 0; i < Math.max(humans.length, agents.length); i++) {
      if (agents[i]) order.push({ id: agents[i].id, name: agents[i].name, agent: true });
      if (humans[i]) order.push({ id: humans[i].id, name: humans[i].name, agent: false });
    }
    Object.assign(tale, { status: 'writing', title, parts: [{ by: member.name, text: opening }], order, turn: 0, painter: 0 });
    app.changed();
    void agentTurn();
  });

  app.route('POST', '/write', (member, body) => {
    const p = tale.order[tale.turn];
    if (busy() || p?.id !== member.id) throw refuse('还没轮到你');
    const line = String(body.text || '').trim().slice(0, 200);
    if (!line) throw refuse('写一段再交');
    tale.parts.push({ by: member.name, text: line });
    next();
  });

  app.route('POST', '/skip', (member) => {
    const p = tale.order[tale.turn];
    if (!member.owner || busy() || p?.agent) throw refuse('现在不能跳过', 403);
    next();
  });

  app.route('POST', '/finale', (member) => {
    if (!member.owner || busy()) throw refuse('现在不能收尾', 403);
    const writer = tale.order.find((p) => p.agent);
    if (!writer) throw refuse('需要一个 agent 来写结局');
    tale.status = 'ending';
    app.changed();
    app.ask(writer.id, `为故事《${tale.title}》写结局（150–250 字），收住前面埋下的线索，给一个让人回味的结尾。故事：\n${text()}\n只输出结局正文。`)
      .then((ending) => { tale.parts.push({ by: writer.name + '（结局）', text: String(ending).trim().slice(0, 800) }); })
      .catch((error) => { tale.parts.push({ by: '系统', text: '结局没写出来：' + error.message }); })
      .finally(() => { tale.status = 'done'; app.changed(); });
  });
}

if (require.main === module) run({ name: '故事接龙' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '故事接龙' };
