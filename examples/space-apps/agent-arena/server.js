#!/usr/bin/env node
/* Agent 竞技场 · Agent Arena — blind battles between the space's agents, like a chatbot arena for
 * the agents on members' own devices. Anyone poses a task; two agents answer it; you see them as A
 * and B and vote before you learn who was who. Every vote moves an Elo leaderboard. Votes cost one
 * click, so it suits a crowd; the page shows only the latest battles.
 * 简介：两个 agent 匿名回答同一道题，投票后才揭晓是谁，并维护 Elo 排行榜。
 *
 *   node examples/space-apps/agent-arena/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const PRESETS = ['写一首关于周一早上的四行短诗', '用一句话向小学生解释什么是区块链', '给一只橘猫取五个名字，每个附一句理由',
  '写一个 JavaScript 函数判断字符串是不是回文', '如果月亮突然消失，地球上第一周会发生什么？', '为一家只卖饺子的店写一句广告语'];
const BATTLES = 12;
const K = 24;

const PAGE = page({
  title: 'Agent 竞技场',
  style: `.duel{display:grid;grid-template-columns:1fr 1fr;gap:10px}@media(max-width:600px){.duel{grid-template-columns:1fr}}
.side{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:10px;max-height:340px;overflow:auto}
.side h3{margin:0 0 6px;font-size:14px}.won{border-color:var(--good)}.votes button{padding:5px 12px}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:4px 6px;text-align:left;border-bottom:1px solid var(--line)}td.n{text-align:right}`,
  body: `<h1>Agent 竞技场</h1><p class="muted">出一道题，随机两个 agent 匿名作答。你投票之后才揭晓谁是谁，每一票都会改变排行榜。</p>
<div class="card"><div class="row"><input id="task" class="grow" placeholder="出一道题"><button id="random" class="ghost">随机一题</button><button id="send">开战</button></div></div>
<div id="board"></div><div id="battles"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
$('send').onclick=()=>act(()=>{const v=$('task').value;$('task').value='';return api('battle',{task:v});});
$('random').onclick=()=>act(()=>api('battle',{random:true}));
const VOTES=[['a','A 更好'],['tie','差不多'],['b','B 更好'],['bad','都不好']];
function render(s){
  const w=s.state;
  fill($('board'),w.board.length?el('div',{class:'card'},el('h2',{style:'margin-top:0'},'排行榜'),el('table',{},el('tr',{},...['','agent','Elo','胜','负','平'].map(h=>el('th',{},h))),
    ...w.board.map((x,i)=>el('tr',{},el('td',{},i+1),el('td',{class:'agent'},x.name),el('td',{class:'n'},x.elo),el('td',{class:'n'},x.won),el('td',{class:'n'},x.lost),el('td',{class:'n'},x.tied))))):null);
  fill($('battles'),...w.battles.map(b=>el('div',{class:'card'},el('div',{class:'row'},el('strong',{class:'grow'},b.task),el('span',{class:'muted'},b.by+' · '+b.votes+' 票')),
    el('div',{class:'duel'},...['a','b'].map(k=>{const x=b[k];return el('div',{class:'side'+(b.mine&&b.winner===k?' won':'')},
      el('h3',{},k.toUpperCase()+(x.name?' · '+x.name:'')),x.status==='asking'?el('div',{class:'muted'},'思考中…'):x.status==='failed'?el('div',{class:'error'},'没有回答：'+x.error):el('div',{class:'pre'},x.text));})),
    b.ready&&!b.mine?el('div',{class:'row votes',style:'margin-top:8px'},...VOTES.map(([v,t])=>el('button',{class:'ghost',onclick:()=>act(()=>api('vote',{battle:b.id,vote:v}))},t))):null,
    b.mine?el('div',{class:'muted',style:'margin-top:6px'},'你投了：'+VOTES.find(v=>v[0]===b.mine)[1]):null)));
}
watch(render);`
});

function setup(app) {
  const ratings = new Map();   // agent id -> {name, elo, won, lost, tied}
  const battles = [];          // {id, task, by, byId, a: {id, name, status, text, error}, b, votes: Map(person -> vote)}
  let next = 1;
  app.page(PAGE);

  const rating = (side) => {
    if (!ratings.has(side.id)) ratings.set(side.id, { name: side.name, elo: 1000, won: 0, lost: 0, tied: 0 });
    return ratings.get(side.id);
  };

  app.state((member) => ({
    board: [...ratings.values()].sort((x, y) => y.elo - x.elo).map((x) => ({ ...x, elo: Math.round(x.elo) })),
    battles: battles.slice().reverse().map((b) => {
      const mine = b.votes.get(member.id) || '';
      const side = (x) => ({ name: mine ? x.name : '', status: x.status, text: x.text, error: x.error });
      return { id: b.id, task: b.task, by: b.by, votes: b.votes.size, ready: b.a.status !== 'asking' && b.b.status !== 'asking', mine,
        winner: mine === 'a' || mine === 'b' ? mine : '', a: side(b.a), b: side(b.b) };
    }),
  }));

  app.route('POST', '/battle', (member, body) => {
    const task = body.random ? PRESETS[Math.floor(Math.random() * PRESETS.length)] : String(body.task || '').trim().slice(0, 300);
    if (!task) throw refuse('出一道题');
    if (battles.some((b) => b.byId === member.id && [b.a, b.b].some((x) => x.status === 'asking'))) throw refuse('你的上一场还没打完');
    const agents = app.agents().filter((a) => a.online !== false);
    if (agents.length < 2) throw refuse('至少要两个在线的 agent');
    for (let i = agents.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [agents[i], agents[j]] = [agents[j], agents[i]]; }
    const battle = { id: String(next++), task, by: member.name, byId: member.id, votes: new Map(),
      a: { id: agents[0].id, name: agents[0].name, status: 'asking', text: '', error: '' }, b: { id: agents[1].id, name: agents[1].name, status: 'asking', text: '', error: '' } };
    battles.push(battle);
    while (battles.length > BATTLES) battles.shift();
    app.changed();
    for (const side of [battle.a, battle.b]) {
      app.ask(side.id, `你在空间「${app.title}」的 Agent 竞技场回答一道题。你的回答会和另一个 agent 的回答匿名放在一起，由大家投票。` +
        `直接作答，不要提到你是谁或用了什么模型，500 字以内。\n题目：${task}`)
        .then((text) => Object.assign(side, { status: 'done', text: String(text || '').trim().slice(0, 3000) }))
        .catch((error) => Object.assign(side, { status: 'failed', error: error.message }))
        .finally(() => app.changed());
    }
  });

  app.route('POST', '/vote', (member, body) => {
    const battle = battles.find((b) => b.id === body.battle);
    if (!battle || [battle.a, battle.b].some((x) => x.status === 'asking')) throw refuse('这一场还不能投票');
    if (!['a', 'b', 'tie', 'bad'].includes(body.vote)) throw refuse('选一个');
    if (battle.votes.has(member.id)) throw refuse('你已经投过了');
    battle.votes.set(member.id, body.vote);
    // Elo: a win is 1, a tie or "both bad" 0.5 each.
    const a = rating(battle.a), b = rating(battle.b);
    const score = body.vote === 'a' ? 1 : body.vote === 'b' ? 0 : 0.5;
    const expected = 1 / (1 + 10 ** ((b.elo - a.elo) / 400));
    a.elo += K * (score - expected);
    b.elo -= K * (score - expected);
    if (score === 1) { a.won++; b.lost++; } else if (score === 0) { b.won++; a.lost++; } else { a.tied++; b.tied++; }
    app.changed();
  });
}

if (require.main === module) run({ name: 'Agent 竞技场' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: 'Agent 竞技场' };
