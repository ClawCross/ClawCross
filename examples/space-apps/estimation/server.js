#!/usr/bin/env node
/* 猜数大会 · Wisdom of Crowds — a number question with a true answer (the owner's, kept secret).
 * People and agents each estimate; at the reveal the page shows every guess, the crowd's median,
 * who came closest, and whether the people or the agents guessed better. Light enough for a crowd.
 * 简介：人和 agent 一起估一个数，看谁最接近、人和 agent 谁更准；适合很多人。
 *
 *   node examples/space-apps/estimation/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const median = (values) => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted.length ? (sorted.length % 2 ? sorted[(sorted.length - 1) / 2] : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2) : null;
};

const PAGE = page({
  title: '猜数大会',
  style: `.dots{position:relative;height:70px;border-bottom:2px solid var(--line);margin:16px 6px}.dot{position:absolute;bottom:0;width:10px;height:10px;border-radius:50%;transform:translateX(-50%)}
.dot.human{background:#e8590c}.dot.agent{background:var(--good)}.truth{position:absolute;bottom:0;top:0;border-left:3px solid var(--bad)}.big{font-size:22px;font-weight:700}`,
  body: `<h1>猜数大会</h1><p class="muted">人和 agent 一起估一个数，看群体智慧有多准。</p><div id="host" class="card"></div><div id="game"></div>`,
  script: `
const {api,watch,act,el,fill,keep}=App,$=id=>document.getElementById(id);
function render(s){
  const g=s.state;
  fill($('host'),s.owner&&g.status!=='open'?el('div',{},el('input',{id:'q',style:'width:100%',placeholder:'问题，例如：上海一共有多少个地铁站？'}),
    el('div',{class:'row',style:'margin-top:6px'},el('input',{id:'truth',type:'number',class:'grow',placeholder:'标准答案（只有你知道）'}),el('input',{id:'unit',placeholder:'单位，例如 个'}),
      el('button',{onclick:()=>act(()=>api('open',{question:$('q').value,truth:+$('truth').value,unit:$('unit').value}))},'出题'))):
    s.owner&&g.status==='open'?el('button',{onclick:()=>act(()=>api('reveal',{}))},'揭晓答案（已有 '+g.count+' 个估计）'):null);
  if(!g.question){fill($('game'),el('p',{class:'muted'},'等待群主出题。'));return;}
  fill($('game'),el('h2',{},g.question),
    g.status==='open'?el('div',{class:'card'},g.mine!=null?el('p',{},'你估了 '+g.mine+' '+g.unit):el('div',{class:'row'},
      keep('guess',()=>el('input',{id:'guess',type:'number',class:'grow',placeholder:'你的估计'})),el('button',{onclick:()=>act(()=>api('guess',{value:+$('guess').value}))},'提交')),
      g.myAgents.length?el('button',{class:'ghost',style:'margin-top:6px',disabled:g.myAgents.every(a=>a.sent),onclick:()=>act(()=>api('agents',{}))},'让我的 agent 也估（'+g.myAgents.map(a=>a.name).join('、')+'）'):null,
      el('p',{class:'muted'},'已有 '+g.count+' 个估计，揭晓前看不到别人的数。')):null,
    g.status==='revealed'?el('div',{},el('div',{class:'big'},'答案：'+g.truth+' '+g.unit),
      el('div',{class:'dots'},...(()=>{const all=[...g.guesses.map(x=>x.value),g.truth],lo=Math.min(...all),hi=Math.max(...all),at=v=>hi===lo?50:(v-lo)/(hi-lo)*100;
        return [el('div',{class:'truth',style:'left:'+at(g.truth)+'%'}),...g.guesses.map((x,i)=>el('div',{class:'dot '+(x.agent?'agent':'human'),title:x.name+' '+x.value,style:'left:'+at(x.value)+'%;bottom:'+(i%5)*12+'px'}))];})()),
      el('p',{},'所有人的中位数 '+g.crowd+'，人类中位数 '+g.people+'，agent 中位数 '+g.agents),
      el('p',{},'最接近：',el('strong',{},g.best)),
      ...g.guesses.slice().sort((a,b)=>Math.abs(a.value-g.truth)-Math.abs(b.value-g.truth)).map(x=>el('div',{},(x.agent?'🤖 ':'')+x.name+'：'+x.value+(x.why?'　'+x.why:'')))):null);
}
watch(render);`
});

function setup(app) {
  let game = { status: 'idle', question: '', unit: '', truth: null, guesses: [] };
  app.page(PAGE);
  app.state((member) => {
    const revealed = game.status === 'revealed';
    const values = (pick) => game.guesses.filter(pick).map((x) => x.value);
    const best = revealed && [...game.guesses].sort((a, b) => Math.abs(a.value - game.truth) - Math.abs(b.value - game.truth))[0];
    return { status: game.status, question: game.question, unit: game.unit, count: game.guesses.length,
      mine: game.guesses.find((x) => x.id === member.id)?.value ?? null,
      myAgents: app.agentsOf(member.id).map((a) => ({ name: a.name, sent: game.guesses.some((x) => x.id === a.id) || game.asked?.has(a.id) })),
      // Before the reveal nobody sees the answer or other guesses.
      truth: revealed ? game.truth : null, guesses: revealed ? game.guesses : [],
      crowd: revealed ? median(values(() => true)) : null, people: revealed ? median(values((x) => !x.agent)) : null,
      agents: revealed ? median(values((x) => x.agent)) : null, best: best ? `${best.name}（${best.value}）` : '' };
  });

  app.route('POST', '/open', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以出题', 403);
    const question = String(body.question || '').trim().slice(0, 200);
    if (!question || !Number.isFinite(body.truth)) throw refuse('写下问题和标准答案');
    game = { status: 'open', question, unit: String(body.unit || '').slice(0, 10), truth: body.truth, guesses: [], asked: new Set() };
    app.changed();
  });

  app.route('POST', '/guess', (member, body) => {
    if (game.status !== 'open') throw refuse('现在不能估');
    if (!Number.isFinite(body.value)) throw refuse('写一个数字');
    if (game.guesses.some((x) => x.id === member.id)) throw refuse('你已经估过了');
    game.guesses.push({ id: member.id, name: member.name, value: body.value, agent: false });
    app.changed();
  });

  app.route('POST', '/agents', (member) => {
    if (game.status !== 'open') throw refuse('现在不能估');
    const current = game;
    for (const agent of app.agentsOf(member.id).filter((a) => !current.asked.has(a.id))) {
      current.asked.add(agent.id);
      app.askJSON(agent.id, `你在空间「${app.title}」的猜数大会，人和 agent 一起估一个数。问题：${current.question}\n` +
        `可以查资料或推算，给出你最好的估计${current.unit ? '（单位：' + current.unit + '）' : ''}。\n格式：{"value":数字,"why":"一句推算思路"}`).then((reply) => {
        const value = Number(reply?.value);
        if (current.status === 'open' && Number.isFinite(value)) current.guesses.push({ id: agent.id, name: agent.name, value, agent: true, why: String(reply?.why || '').slice(0, 120) });
      }).catch(() => {}).finally(() => app.changed());
    }
    app.changed();
  });

  app.route('POST', '/reveal', (member) => {
    if (!member.owner || game.status !== 'open') throw refuse('现在不能揭晓', 403);
    game.status = 'revealed';
    app.changed();
  });
}

if (require.main === module) run({ name: '猜数大会' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '猜数大会', median };
