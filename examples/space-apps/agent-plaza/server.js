#!/usr/bin/env node
/* Agent 广场 · Agent Plaza — built for very large spaces. The owner posts a question; anyone can
 * send their own agents to answer it with one tap, or answer themselves. Everyone upvotes; the
 * best answers rise. The server keeps only answers and votes; each agent answers each question once.
 * 简介：群主提问，大家派自己的 agent 回答，投票选出好答案；适合很多人。
 *
 *   node examples/space-apps/agent-plaza/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const SHOWN = 60;
const PROMPTS = ['用一句话说服我明天早起', '如果你能给人类一条忠告，会是什么？', '推荐一本冷门但改变你想法的书', '下一个十年最被低估的技能是什么？',
  '给一个刚开始写代码的人最好的建议', '用三个词形容 2026 年'];

const PAGE = page({
  appId: 'agent-plaza',
  title: 'Agent 广场',
  style: `.answer{display:flex;gap:10px;align-items:flex-start;padding:10px 0;border-bottom:1px solid var(--line)}.rank{font-weight:700;color:var(--muted);width:26px}
.vote{padding:4px 12px}.vote.on{background:var(--good)}`,
  body: `<h1>Agent 广场</h1><p class="muted">一个问题，所有人的 agent 一起回答，大家投出最好的那个。</p>
<div id="ask" class="card"></div><div id="plaza"></div>`,
  script: `
const {api,watch,act,el,fill,keep}=App,$=id=>document.getElementById(id);
function render(s){
  const p=s.state;
  fill($('ask'),s.owner?el('div',{},el('div',{class:'row'},keep('question',()=>el('input',{id:'question',class:'grow',placeholder:'抛出一个问题'})),
      el('button',{onclick:()=>act(()=>{const v=$('question').value;$('question').value='';return api('post',{question:v});})},'抛出')),
    el('div',{class:'row',style:'margin-top:6px'},...p.prompts.map(x=>el('button',{class:'ghost',onclick:()=>{$('question').value=x;}},x)))):null,
    !p.question?el('p',{class:'muted'},'等待群主抛出问题。'):el('div',{},el('h2',{},p.question),el('p',{class:'muted'},p.total+' 个回答'),
      el('div',{class:'row'},p.myAgents.length?el('button',{disabled:!p.myAgents.some(a=>!a.sent),onclick:()=>act(()=>api('send',{}))},'派我的 agent 回答（'+p.myAgents.map(a=>a.name).join('、')+'）'):null),
      el('div',{class:'row',style:'margin-top:6px'},keep('mine',()=>el('input',{id:'mine',class:'grow',placeholder:'或者自己答一句'})),
        el('button',{class:'ghost',onclick:()=>act(()=>{const v=$('mine').value;$('mine').value='';return api('answer',{text:v});})},'回答'))));
  if(!p.question){fill($('plaza'));return;}
  fill($('plaza'),...p.answers.map((a,i)=>el('div',{class:'answer'},el('span',{class:'rank'},String(i+1)),
    el('div',{class:'grow'},el('div',{class:a.agent?'agent':''},(a.agent?'🤖 ':'')+a.name+(a.owner?' · '+a.owner:'')),el('div',{class:'pre'},a.text||'思考中…')),
    a.text?el('button',{class:'ghost vote'+(a.mine?' on':''),onclick:()=>act(()=>api('vote',{id:a.id}))},'▲ '+a.votes):null)));
}
watch(render);`
});

function setup(app) {
  let round = { id: 0, question: '', answers: [] };
  app.page(PAGE);
  app.state((member) => {
    const ranked = round.answers.filter((a) => a.text || a.ownerId === member.id || a.agentOwner === member.id)
      .sort((a, b) => b.votes.size - a.votes.size || a.at - b.at);
    return { prompts: PROMPTS, question: round.question, total: round.answers.filter((a) => a.text).length,
      myAgents: app.agentsOf(member.id).map((a) => ({ name: a.name, sent: round.answers.some((x) => x.agentId === a.id) })),
      // Only the top answers travel to every page: the plaza stays light however many people join.
      answers: ranked.slice(0, SHOWN).map((a) => ({ id: a.id, name: a.name, owner: a.owner, agent: !!a.agentId, text: a.text,
        votes: a.votes.size, mine: a.votes.has(member.id) })) };
  });

  app.route('POST', '/post', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以抛出问题', 403);
    const question = String(body.question || '').trim().slice(0, 200);
    if (!question) throw refuse('写下问题');
    round = { id: round.id + 1, question, answers: [] };
    app.changed();
  });

  app.route('POST', '/send', (member) => {
    if (!round.question) throw refuse('还没有问题');
    const agents = app.agentsOf(member.id).filter((a) => !round.answers.some((x) => x.agentId === a.id));
    if (!agents.length) throw refuse('你的 agent 都已经回答过了');
    const id = round.id;
    for (const agent of agents) {
      const answer = { id: `${id}-${round.answers.length + 1}`, name: agent.name, owner: member.name, agentId: agent.id, agentOwner: member.id,
        text: '', votes: new Set(), at: Date.now() };
      round.answers.push(answer);
      app.ask(agent.id, `你在空间「${app.title}」的 Agent 广场，和很多 agent 一起回答同一个问题，大家投票选最好的。\n问题：${round.question}\n` +
        '用 1–3 句话回答，要有你自己的特点。只输出回答本身。').then((text) => {
        if (round.id === id) answer.text = String(text).trim().slice(0, 400);
      }).catch(() => { if (round.id === id) round.answers.splice(round.answers.indexOf(answer), 1); }).finally(() => app.changed());
    }
    app.changed();
  });

  app.route('POST', '/answer', (member, body) => {
    const text = String(body.text || '').trim().slice(0, 300);
    if (!round.question || !text) throw refuse('写下你的回答');
    if (round.answers.some((a) => a.ownerId === member.id)) throw refuse('你已经答过了');
    round.answers.push({ id: `${round.id}-${round.answers.length + 1}`, name: member.name, ownerId: member.id, text, votes: new Set(), at: Date.now() });
    app.changed();
  });

  app.route('POST', '/vote', (member, body) => {
    const answer = round.answers.find((a) => a.id === body.id && a.text);
    if (!answer) throw refuse('没有这个回答');
    answer.votes.has(member.id) ? answer.votes.delete(member.id) : answer.votes.add(member.id);
    app.changed();
  });
}

if (require.main === module) run({ name: 'Agent 广场' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: 'Agent 广场' };
