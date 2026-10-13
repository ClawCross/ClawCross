#!/usr/bin/env node
/* 知识擂台 · Quiz Arena — one agent is the quizmaster and writes five multiple-choice questions on
 * a topic; people and the other agents answer each question; the owner reveals the answer and
 * the scoreboard moves.
 * 简介：一个 agent 出题，人和其他 agent 答题计分。
 *
 *   node examples/space-apps/quiz/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const PAGE = page({
  title: '知识擂台',
  style: `.option{display:block;width:100%;text-align:left;margin:6px 0;border-radius:12px}.option.right{background:var(--good)}.option.chosen{outline:3px solid var(--accent)}
.board span{margin-right:10px}`,
  body: `<h1>知识擂台</h1><div id="setup" class="card"></div><div id="arena"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
function render(s){
  const q=s.state,agents=s.members.filter(m=>m.agent);
  $('setup').hidden=!(s.owner&&(q.status==='idle'||q.status==='over'));
  if(!$('setup').hidden&&!$('setup').childElementCount)fill($('setup'),el('div',{class:'row'},el('input',{id:'topic',class:'grow',placeholder:'题目范围，例如：宋朝历史、量子计算、宝可梦'}),
    el('label',{},'出题人 ',el('select',{id:'master'},...agents.map(a=>el('option',{value:a.id},a.name)))),el('button',{onclick:()=>act(()=>api('start',{topic:$('topic').value,master:$('master').value}))},'开擂')));
  const cur=q.questions[q.index];
  fill($('arena'),el('div',{class:'row board'},...Object.entries(q.scores).sort((a,b)=>b[1]-a[1]).map(([n,p])=>el('span',{class:'chip'},n+' '+p))),
    q.status==='writing'?el('p',{class:'muted'},q.master+' 正在出题…'):null,
    q.status==='idle'?el('p',{class:'muted'},s.owner?'选个范围和出题人。':'等待群主开擂。'):null,
    cur&&q.status!=='writing'?el('div',{class:'card'},el('div',{class:'muted'},q.topic+' · 第 '+(q.index+1)+' / '+q.questions.length+' 题 · 出题人 '+q.master),
      el('h2',{},cur.question),...cur.options.map((o,i)=>el('button',{class:'option ghost'+(cur.revealed&&cur.answer===i?' right':'')+(q.mine===i?' chosen':''),
        disabled:cur.revealed||q.isMaster,onclick:()=>act(()=>api('answer',{choice:i}))},'ABCD'[i]+'. '+o)),
      el('div',{class:'muted'},'已作答：'+cur.answered.join('、')),
      cur.revealed?el('div',{class:'pre'},'解析：'+cur.explain):null,
      s.owner&&q.status==='playing'?el('button',{style:'margin-top:8px',onclick:()=>act(()=>api('next',{}))},cur.revealed?(q.index+1<q.questions.length?'下一题':'结束'):'揭晓答案'):null):null,
    q.status==='over'?el('h2',{},'冠军：'+(Object.entries(q.scores).sort((a,b)=>b[1]-a[1])[0]||['无'])[0]):null);
}
watch(render);`
});

function setup(app) {
  const quiz = { status: 'idle', topic: '', master: '', masterId: '', questions: [], index: 0, scores: {} };
  const answers = [];   // per question: Map(name -> choice)
  app.page(PAGE);
  app.state((member) => {
    const cur = quiz.questions[quiz.index];
    return { ...quiz, masterId: undefined, isMaster: false, mine: answers[quiz.index]?.get(member.name) ?? -1,
      questions: quiz.questions.map((q, i) => ({ ...q, answer: q.revealed ? q.answer : -1, explain: q.revealed ? q.explain : '',
        answered: [...(answers[i]?.keys() || [])] })), current: cur ? quiz.index : -1 };
  });

  function agentAnswers(index) {
    const q = quiz.questions[index];
    for (const agent of app.agents().filter((a) => a.id !== quiz.masterId)) {
      app.askJSON(agent.id, `你在空间「${app.title}」的知识擂台答题。题目：${q.question}\n选项：${q.options.map((o, i) => 'ABCD'[i] + '. ' + o).join('  ')}\n` +
        '格式：{"choice":"A|B|C|D"}').then((reply) => {
        const choice = 'ABCD'.indexOf(String(reply?.choice || '').trim().toUpperCase()[0]);
        if (choice >= 0 && quiz.index === index && !q.revealed && !answers[index].has(agent.name)) { answers[index].set(agent.name, choice); app.changed(); }
      }).catch(() => {});
    }
  }

  app.route('POST', '/start', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以开擂', 403);
    if (['writing', 'playing'].includes(quiz.status)) throw refuse('擂台正在进行');
    const master = app.member(body.master);
    const topic = String(body.topic || '').trim().slice(0, 60);
    if (!master?.agent) throw refuse('选一个 agent 出题');
    if (!topic) throw refuse('写下题目范围');
    Object.assign(quiz, { status: 'writing', topic, master: master.name, masterId: master.id, questions: [], index: 0, scores: {} });
    answers.length = 0;
    app.changed();
    app.askJSON(master.id, `你是空间「${app.title}」知识擂台的出题人。围绕「${topic}」出 5 道有区分度的单选题，由易到难，每题 4 个选项且只有一个正确。\n` +
      '格式：{"questions":[{"question":"题干","options":["A","B","C","D"],"answer":正确选项序号0到3,"explain":"一句解析"}]}').then((reply) => {
      const questions = (reply?.questions || []).filter((q) => q?.question && Array.isArray(q.options) && q.options.length === 4 && [0, 1, 2, 3].includes(q.answer))
        .slice(0, 5).map((q) => ({ question: String(q.question).slice(0, 300), options: q.options.map((o) => String(o).slice(0, 120)), answer: q.answer,
          explain: String(q.explain || '').slice(0, 300), revealed: false }));
      if (!questions.length) throw new Error('出题人没出好题');
      Object.assign(quiz, { status: 'playing', questions });
      questions.forEach(() => answers.push(new Map()));
      agentAnswers(0);
    }).catch(() => Object.assign(quiz, { status: 'idle' })).finally(() => app.changed());
  });

  app.route('POST', '/answer', (member, body) => {
    const q = quiz.questions[quiz.index];
    if (quiz.status !== 'playing' || !q || q.revealed) throw refuse('现在不能作答');
    if (![0, 1, 2, 3].includes(body.choice)) throw refuse('选一个选项');
    answers[quiz.index].set(member.name, body.choice);
    app.changed();
  });

  app.route('POST', '/next', (member) => {
    const q = quiz.questions[quiz.index];
    if (!member.owner || quiz.status !== 'playing' || !q) throw refuse('现在不能操作', 403);
    if (!q.revealed) {
      q.revealed = true;
      for (const [name, choice] of answers[quiz.index]) quiz.scores[name] = (quiz.scores[name] || 0) + (choice === q.answer ? 1 : 0);
    } else if (quiz.index + 1 < quiz.questions.length) { quiz.index++; agentAnswers(quiz.index); }
    else quiz.status = 'over';
    app.changed();
  });
}

if (require.main === module) run({ name: '知识擂台' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '知识擂台' };
