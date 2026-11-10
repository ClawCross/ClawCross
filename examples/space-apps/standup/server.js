#!/usr/bin/env node
/* 站会 · Standup — each person asks their own agent to write today's update from what happened on
 * its device (recent commits, changes, to-dos), adding a note of their own; people without an agent
 * write it by hand. The owner has one agent pull the updates together: the team's progress, the
 * risks, and who should talk to whom. Only summaries are shared; the agent decides what to read.
 * 简介：每个人的 agent 根据自己设备上的工作写日报，再汇总。
 *
 *   node examples/space-apps/standup/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const DAYS = 7;
const lines = (list, max = 6) => (Array.isArray(list) ? list : []).map((x) => String(x || '').trim().slice(0, 200)).filter(Boolean).slice(0, max);
const today = () => new Date().toLocaleDateString('sv-SE');   // YYYY-MM-DD, server time

const PAGE = page({
  appId: 'standup',
  title: '站会',
  style: `.report h2{margin:0 0 4px;font-size:15px}.report ul{margin:2px 0 6px;padding-left:20px}.report .tag{font-size:12px;color:var(--muted)}
.blocker{color:var(--bad)}.summary{border:1px solid var(--accent)}`,
  body: `<h1>站会</h1><p class="muted">让自己的 agent 根据它电脑上最近的工作写日报，或者自己写。只分享摘要。</p>
<div class="row"><select id="day"></select><span class="grow"></span><span id="count" class="muted"></span></div>
<div class="card"><textarea id="note" rows="3" placeholder="给 agent 的补充（例如：卡在登录接口），或者直接写你的日报，一行一条"></textarea>
<div class="row" style="margin-top:8px"><span id="mine" class="row"></span><button id="manual" class="ghost">直接发布</button></div></div>
<div id="summary"></div><div id="reports" class="grid"></div>`,
  script: `
const {api,watch,act,el,fill,keep}=App,$=id=>document.getElementById(id);
let day='',last=null;
$('day').onchange=()=>{day=$('day').value;last&&render(last);};
$('manual').onclick=()=>act(()=>{const v=$('note').value;$('note').value='';return api('manual',{text:v});});
const list=(title,items,cls)=>items.length?[el('div',{class:'tag'},title),el('ul',{},...items.map(x=>el('li',{class:cls||''},x)))]:[];
function render(s){
  last=s;const w=s.state;
  if($('day').options.length!==w.days.length)fill($('day'),...w.days.map(d=>el('option',{value:d.day},d.day===w.today?'今天':d.day)));
  const shown=w.days.find(d=>d.day===(day||w.today))||w.days[0];
  $('day').value=shown.day;
  fill($('mine'),...w.mine.map(a=>el('button',{onclick:()=>act(()=>{const v=$('note').value;$('note').value='';return api('write',{agent:a.id,note:v});})},'让 '+a.name+' 写')));
  $('count').textContent=shown.reports.length+' 份日报';
  fill($('summary'),shown.summary?el('div',{class:'card summary'},el('h2',{},'汇总 · '+shown.summary.by),el('div',{class:'pre'},shown.summary.text),
      ...list('风险',shown.summary.risks,'blocker'),...list('建议对一下',shown.summary.pairs)):null,
    s.owner&&shown.day===w.today?el('div',{class:'row'},keep('writer',()=>el('select',{id:'writer'},...s.members.filter(m=>m.agent).map(a=>el('option',{value:a.id},a.name)))),
      el('button',{class:'ghost',disabled:!shown.reports.some(r=>r.status==='done'),onclick:()=>act(()=>api('summarize',{agent:$('writer').value}))},shown.summarizing?'正在汇总…':'汇总')):null);
  fill($('reports'),...shown.reports.map(r=>el('div',{class:'card report'},el('h2',{},r.person),el('div',{class:'tag'},r.by+' · '+r.at),
    r.status==='writing'?el('p',{class:'muted'},'正在写…'):r.status==='failed'?el('p',{class:'error'},'没写成：'+r.error):null,
    ...list('完成',r.done),...list('接下来',r.next),...list('卡点',r.blockers,'blocker'),
    r.personId===s.me.id&&r.status!=='writing'?el('button',{class:'ghost',onclick:()=>act(()=>api('remove',{day:shown.day}))},'撤回'):null)));
}
watch(render);`
});

function setup(app) {
  // days: [{day, reports: [{personId, person, by, at, status, error, done, next, blockers}], summary, summarizing}]
  const days = [];
  app.page(PAGE);

  const current = () => {
    const day = today();
    if (days.at(-1)?.day !== day) { days.push({ day, reports: [], summary: null, summarizing: false }); while (days.length > DAYS) days.shift(); }
    return days.at(-1);
  };
  const report = (member) => {
    const board = current();
    let entry = board.reports.find((r) => r.personId === member.id);
    if (!entry) board.reports.push(entry = { personId: member.id, person: member.name });
    return entry;
  };
  const time = () => new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });

  app.state((member) => {
    current();
    return { today: today(), days: days.slice().reverse(), mine: app.agentsOf(member.id).map(({ id, name }) => ({ id, name })) };
  });

  app.route('POST', '/write', (member, body) => {
    const agent = app.agentsOf(member.id).find((a) => a.id === body.agent);
    if (!agent) throw refuse('只能让自己的 agent 写', 403);
    const entry = report(member);
    if (entry.status === 'writing') throw refuse('还在写');
    const note = String(body.note || '').trim().slice(0, 500);
    Object.assign(entry, { by: agent.name, at: time(), status: 'writing', error: '', done: [], next: [], blockers: [] });
    app.changed();
    app.askJSON(agent.id, `你在空间「${app.title}」的站会上替「${member.name}」写今天的日报。看看你所在设备工作目录最近 24 小时的 git 提交、改动和待办（如果有），` +
      `总结做了什么、接下来做什么、卡在哪。只写摘要：不要贴代码、密钥、内部地址或别人的隐私。` + (note ? `\n${member.name} 的补充：${note}` : '') +
      '\n格式：{"done":["完成的事"],"next":["接下来"],"blockers":["卡点，没有就空"]}')
      .then((reply) => {
        if (!reply) throw new Error('回复不是日报格式');
        Object.assign(entry, { status: 'done', done: lines(reply.done), next: lines(reply.next), blockers: lines(reply.blockers, 4) });
      })
      .catch((error) => Object.assign(entry, { status: 'failed', error: error.message }))
      .finally(() => app.changed());
  });

  app.route('POST', '/manual', (member, body) => {
    const done = lines(String(body.text || '').split('\n'), 10);
    if (!done.length) throw refuse('写点什么');
    const entry = report(member);
    if (entry.status === 'writing') throw refuse('你的 agent 还在写');
    Object.assign(entry, { by: '自己写', at: time(), status: 'done', error: '', done, next: [], blockers: [] });
    app.changed();
  });

  app.route('POST', '/remove', (member, body) => {
    const board = days.find((d) => d.day === body.day);
    const index = board ? board.reports.findIndex((r) => r.personId === member.id && r.status !== 'writing') : -1;
    if (index < 0) throw refuse('没有你的日报');
    board.reports.splice(index, 1);
    app.changed();
  });

  app.route('POST', '/summarize', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以汇总', 403);
    const agent = app.member(body.agent);
    const board = current();
    const ready = board.reports.filter((r) => r.status === 'done');
    if (!agent?.agent) throw refuse('选一个 agent');
    if (!ready.length) throw refuse('还没有日报');
    if (board.summarizing) throw refuse('正在汇总');
    board.summarizing = true;
    app.changed();
    const text = ready.map((r) => `【${r.person}】完成：${r.done.join('；') || '无'}｜接下来：${r.next.join('；') || '无'}｜卡点：${r.blockers.join('；') || '无'}`).join('\n');
    app.askJSON(agent.id, `你在空间「${app.title}」主持站会。今天大家的日报：\n${text}\n用几句话汇总团队进展，指出风险，并建议哪些人应该对一下（谁能帮谁解决卡点、谁的工作有重叠）。` +
      '\n格式：{"summary":"三五句话","risks":["风险"],"pairs":["A 和 B：原因"]}')
      .then((reply) => {
        board.summary = { by: agent.name, text: String(reply?.summary || '（没有汇总）').slice(0, 1200), risks: lines(reply?.risks), pairs: lines(reply?.pairs) };
      })
      .catch((error) => { board.summary = { by: agent.name, text: '没有汇总成：' + error.message, risks: [], pairs: [] }; })
      .finally(() => { board.summarizing = false; app.changed(); });
  });
}

if (require.main === module) run({ name: '站会' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '站会' };
