#!/usr/bin/env node
/* 预言家 · Forecast Board — yes/no questions about the future ("will it rain on Friday?"). Everyone
 * gives a probability with one tap; the agents research with their own tools and give theirs with
 * a reason. The page shows the people's and the agents' medians. When the owner settles a question,
 * Brier scores rank who forecasts best, people and agents alike. One tap per person: fine for crowds.
 *
 *   node examples/space-apps/forecast/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const CHOICES = [5, 20, 35, 50, 65, 80, 95];
const QUESTIONS = 20;
const AGENTS = 6;
const median = (list) => { if (!list.length) return null; const s = [...list].sort((a, b) => a - b), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

const PAGE = page({
  title: '预言家',
  style: `.choices button{padding:4px 10px;font-size:13px}.choices button.on{background:var(--accent);color:var(--on-accent)}
.stat{font-size:13px}.why{font-size:13px;color:var(--muted)}.settled{font-weight:700}table{border-collapse:collapse;width:100%;font-size:14px}td{padding:3px 6px;border-bottom:1px solid var(--line)}`,
  body: `<h1>预言家</h1><p class="muted">对未来的事给出概率。agent 会自己查资料并说明理由；结果揭晓后按准确度排名，人和 agent 一起比。</p>
<div id="ask-box" class="card" hidden><div class="row"><input id="question" class="grow" placeholder="一个会有明确结果的是非题，例如：本周五上海会下雨吗？"><button id="post">提问</button></div></div>
<div id="board"></div><div id="questions"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
$('post').onclick=()=>act(()=>{const v=$('question').value;$('question').value='';return api('post',{text:v});});
const pct=x=>x==null?'–':Math.round(x)+'%';
function render(s){
  const g=s.state;
  $('ask-box').hidden=!s.owner;
  fill($('board'),g.board.length?el('div',{class:'card'},el('strong',{},'准确度排行（已揭晓 '+g.settled+' 题）'),el('table',{},
    ...g.board.map((x,i)=>el('tr',{},el('td',{},i+1),el('td',{class:x.agent?'agent':''},x.name),el('td',{},x.score+' 分'),el('td',{class:'muted'},x.count+' 题'))))):null);
  fill($('questions'),...g.questions.map(q=>el('div',{class:'card'},el('div',{class:'row'},el('strong',{class:'grow'},q.text),
      q.outcome!=null?el('span',{class:'settled'},q.outcome?'结果：是':'结果：否'):q.locked?el('span',{class:'muted'},'已停止预测'):null),
    el('div',{class:'stat'},'人：'+q.people+' 人 · 中位数 '+pct(q.peopleMedian)+'　agent：'+q.agents.filter(a=>a.p!=null).length+' 个 · 中位数 '+pct(q.agentMedian)),
    el('div',{class:'row choices',style:'margin-top:6px'},el('span',{class:'muted'},'你：'),...g.choices.map(c=>el('button',{class:q.mine===c?'on':'ghost',disabled:q.locked,
      onclick:()=>act(()=>api('predict',{id:q.id,p:c}))},c+'%'))),
    ...q.agents.map(a=>el('div',{},el('span',{class:'agent'},a.name),' ',a.p==null?el('span',{class:'muted'},a.error||'在查资料…'):pct(a.p),a.why?el('div',{class:'why'},a.why):null)),
    s.owner?el('div',{class:'row',style:'margin-top:6px'},q.outcome==null?[
      el('button',{class:'ghost',onclick:()=>act(()=>api('agents',{id:q.id}))},'让 agent 预测'),
      q.locked?null:el('button',{class:'ghost',onclick:()=>act(()=>api('lock',{id:q.id}))},'停止预测'),
      el('button',{class:'ghost',onclick:()=>act(()=>api('settle',{id:q.id,outcome:true}))},'揭晓：是'),
      el('button',{class:'ghost',onclick:()=>act(()=>api('settle',{id:q.id,outcome:false}))},'揭晓：否')]:null):null)));
}
watch(render);`
});

function setup(app) {
  // questions: {id, text, locked, outcome, people: Map(person -> p), agents: Map(agent -> {name, p, why, error})}
  const questions = [];
  const record = new Map();   // id -> {name, agent, brier: [..]}
  let next = 1;
  app.page(PAGE);

  app.state((member) => ({
    choices: CHOICES, settled: questions.filter((q) => q.outcome != null).length,
    board: [...record.values()].map((x) => ({ name: x.name, agent: x.agent, count: x.brier.length,
      score: Math.round((1 - x.brier.reduce((t, b) => t + b, 0) / x.brier.length) * 100) })).sort((a, b) => b.score - a.score).slice(0, 30),
    // Individual people's forecasts stay private: each sees its own and the median.
    questions: questions.slice().reverse().map((q) => ({ id: q.id, text: q.text, locked: q.locked || q.outcome != null, outcome: q.outcome,
      mine: q.people.get(member.id) ?? null, people: q.people.size, peopleMedian: median([...q.people.values()]),
      agents: [...q.agents.values()], agentMedian: median([...q.agents.values()].map((a) => a.p).filter((p) => p != null)) })),
  }));

  app.route('POST', '/post', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以提问', 403);
    const text = String(body.text || '').trim().slice(0, 200);
    if (!text) throw refuse('写一个问题');
    questions.push({ id: String(next++), text, locked: false, outcome: null, people: new Map(), agents: new Map() });
    while (questions.length > QUESTIONS) questions.shift();
    app.changed();
  });

  const find = (id) => { const q = questions.find((x) => x.id === id); if (!q) throw refuse('没有这个问题'); return q; };

  app.route('POST', '/predict', (member, body) => {
    const q = find(body.id);
    if (q.locked || q.outcome != null) throw refuse('已经停止预测');
    if (!CHOICES.includes(body.p)) throw refuse('选一个概率');
    if (member.agent) throw refuse('agent 由群主来问', 403);
    q.people.set(member.id, body.p);
    app.changed();
  });

  app.route('POST', '/agents', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以请 agent 预测', 403);
    const q = find(body.id);
    if (q.locked || q.outcome != null) throw refuse('已经停止预测');
    for (const agent of app.agents().filter((a) => a.online !== false).slice(0, AGENTS)) {
      if (q.agents.get(agent.id)?.p === null && !q.agents.get(agent.id)?.error) continue;   // still researching
      const entry = { name: agent.name, p: null, why: '', error: '' };
      q.agents.set(agent.id, entry);
      app.askJSON(agent.id, `你在空间「${app.title}」的预测板上预测：${q.text}\n今天是 ${new Date().toLocaleDateString('zh-CN')}。能联网就先查资料，` +
        '然后给出这件事发生（答案为「是」）的概率。之后会按 Brier 分数和其他人、agent 比准确度。\n格式：{"p":0到100的整数,"why":"一两句理由"}')
        .then((reply) => {
          const p = Math.round(Number(reply?.p));
          if (!(p >= 0 && p <= 100)) throw new Error('没有给出概率');
          if (q.outcome == null && !q.locked) Object.assign(entry, { p, why: String(reply?.why || '').slice(0, 300) });
          else entry.error = '答得太晚了';
        })
        .catch((error) => { entry.error = error.message; })
        .finally(() => app.changed());
    }
    app.changed();
  });

  app.route('POST', '/lock', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以停止预测', 403);
    find(body.id).locked = true;
    app.changed();
  });

  app.route('POST', '/settle', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以揭晓', 403);
    const q = find(body.id);
    if (q.outcome != null) throw refuse('已经揭晓了');
    if (typeof body.outcome !== 'boolean') throw refuse('是还是否？');
    q.outcome = body.outcome;
    const add = (id, name, agent, p) => {
      if (!record.has(id)) record.set(id, { name, agent, brier: [] });
      record.get(id).brier.push((p / 100 - (q.outcome ? 1 : 0)) ** 2);
    };
    for (const [id, p] of q.people) add(id, app.member(id)?.name || '有人', false, p);
    for (const [id, a] of q.agents) if (a.p != null) add(id, a.name, true, a.p);
    app.changed();
  });
}

if (require.main === module) run({ name: '预言家' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '预言家' };
