#!/usr/bin/env node
/* 模拟面试 · Mock Interview — the space's agents form an interview panel, each from its own angle
 * (technical, business, HR). They take turns asking; the candidate answers — you yourself, or one
 * of your agents sent in your place. Then every interviewer scores and writes feedback. Others can
 * watch; the résumé is shown only to the panel.
 *
 *   node examples/space-apps/mock-interview/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const LENSES = ['技术面：专业能力和解决问题的思路', '业务面：对业务、产品和协作的理解', 'HR 面：动机、沟通和稳定性'];
const HIRE = ['强烈推荐', '推荐', '待定', '不推荐'];
const text = (x, max) => String(x || '').trim().slice(0, max);

const PAGE = page({
  title: '模拟面试',
  style: `.q{margin:10px 0}.q .who{font-size:13px;color:var(--muted)}.a{margin:4px 0 0 14px;padding-left:10px;border-left:3px solid var(--line)}
.score{font-size:28px;font-weight:700}.hire{font-size:12px;padding:2px 8px;border-radius:99px;background:var(--bg)}`,
  body: `<h1>模拟面试</h1><p class="muted">空间里的 agent 组成面试官团轮流提问，你来回答——或者派你的 agent 去面。最后每位面试官打分和点评。</p>
<div id="setup" class="card" hidden><input id="role" placeholder="应聘岗位，例如：前端工程师，3 年经验" style="width:100%">
<textarea id="resume" rows="3" placeholder="简历要点（可选，只给面试官看）" style="margin-top:8px"></textarea>
<div class="row" style="margin-top:8px"><label>候选人 <select id="candidate"></select></label><label>每位面试官问 <select id="per"><option>1</option><option selected>2</option><option>3</option></select> 题</label><button id="start">开始面试</button></div></div>
<div id="head"></div><div id="talk"></div>
<div id="answer-box" class="card" hidden><textarea id="answer" rows="3" placeholder="你的回答"></textarea><div class="row" style="margin-top:8px"><button id="send">回答</button></div></div>
<div id="report"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
$('start').onclick=()=>act(()=>api('start',{role:$('role').value,resume:$('resume').value,candidate:$('candidate').value,per:+$('per').value}));
$('send').onclick=()=>act(()=>{const v=$('answer').value;$('answer').value='';return api('answer',{text:v});});
function render(s){
  const g=s.state;
  $('setup').hidden=['asking','answering','scoring'].includes(g.status);
  const options=[['','我自己'],...g.myAgents.map(a=>[a.id,'我的 '+a.name])];
  if($('candidate').options.length!==options.length)fill($('candidate'),...options.map(([v,t])=>el('option',{value:v},t)));
  fill($('head'),g.role?el('div',{class:'card'},el('strong',{},'岗位：'+g.role),el('div',{class:'muted'},'候选人：'+g.candidate),
    el('div',{class:'row',style:'margin-top:6px'},...g.panel.map(p=>el('span',{class:'chip'},el('span',{class:'agent'},p.name),' · '+p.lens.split('：')[0])))):null);
  fill($('talk'),...g.turns.map(t=>el('div',{class:'q'},el('div',{class:'who'},t.by),el('div',{},t.question),
    t.answer?el('div',{class:'a pre'},t.answer):el('div',{class:'a muted'},g.status==='answering'?g.candidate+' 正在回答…':''))),
    g.status==='asking'?el('p',{class:'muted'},g.asker+' 正在想问题…'):null,g.status==='scoring'?el('p',{class:'muted'},'面试官们正在打分…'):null);
  $('answer-box').hidden=!(g.status==='answering'&&g.mine);
  fill($('report'),g.status==='done'?[el('h2',{},'面试结果 · 平均 '+g.average+' 分'),el('div',{class:'grid'},...g.scores.map(x=>el('div',{class:'card'},
    el('div',{class:'row'},el('span',{class:'agent grow'},x.name),el('span',{class:'hire'},x.hire)),el('div',{class:'score'},x.score??'–'),
    x.good?el('div',{},'👍 '+x.good):null,x.improve?el('div',{},'💡 '+x.improve):null,x.error?el('div',{class:'error'},x.error):null)))]:null);
}
watch(render);`
});

function setup(app) {
  // turns: {by, byId, question, answer}; scores: {name, score, hire, good, improve, error}
  const iv = { round: 0, status: 'idle', role: '', resume: '', candidate: '', candidateId: '', person: '', agentCandidate: '', panel: [], per: 2,
    turns: [], asker: '', scores: [] };
  app.page(PAGE);

  app.state((member) => ({
    status: iv.status, role: iv.role, candidate: iv.candidate, asker: iv.asker, turns: iv.turns.map(({ byId, ...t }) => t),
    panel: iv.panel.map(({ name, lens }) => ({ name, lens })), mine: !iv.agentCandidate && iv.person === member.id,
    myAgents: app.agentsOf(member.id).map(({ id, name }) => ({ id, name })),
    scores: iv.scores, average: iv.scores.filter((x) => x.score != null).length
      ? (iv.scores.filter((x) => x.score != null).reduce((t, x) => t + x.score, 0) / iv.scores.filter((x) => x.score != null).length).toFixed(1) : '–',
  }));

  const transcript = () => iv.turns.filter((t) => t.answer).map((t) => `${t.by} 问：${t.question}\n${iv.candidate} 答：${t.answer}`).join('\n') || '（刚开始）';
  const brief = () => `岗位：${iv.role}` + (iv.resume ? `\n候选人简历要点：${iv.resume}` : '');

  app.route('POST', '/start', (member, body) => {
    if (['asking', 'answering', 'scoring'].includes(iv.status)) throw refuse('正在面试');
    const role = text(body.role, 80);
    if (!role) throw refuse('写一下应聘岗位');
    const agentCandidate = body.candidate ? app.agentsOf(member.id).find((a) => a.id === body.candidate) : null;
    if (body.candidate && !agentCandidate) throw refuse('只能派自己的 agent', 403);
    const panel = app.agents().filter((a) => a.online !== false && a.id !== agentCandidate?.id).slice(0, LENSES.length)
      .map((a, i) => ({ id: a.id, name: a.name, lens: LENSES[i] }));
    if (!panel.length) throw refuse('没有可以当面试官的 agent');
    Object.assign(iv, { round: iv.round + 1, status: 'asking', role, resume: text(body.resume, 1000), person: member.id,
      candidate: agentCandidate ? agentCandidate.name + '（' + member.name + ' 的 agent）' : member.name, agentCandidate: agentCandidate?.id || '',
      panel, per: [1, 2, 3].includes(body.per) ? body.per : 2, turns: [], scores: [] });
    app.changed();
    void next(iv.round);
  });

  /** The next interviewer asks; when everyone has asked enough, the panel scores. */
  async function next(round) {
    const live = () => iv.round === round;
    if (iv.turns.length >= iv.panel.length * iv.per) return score(round);
    const asker = iv.panel[iv.turns.length % iv.panel.length];
    Object.assign(iv, { status: 'asking', asker: asker.name });
    app.changed();
    let question;
    try {
      const reply = await app.askJSON(asker.id, `你在空间「${app.title}」的模拟面试里当面试官「${asker.name}」，负责${asker.lens}。\n${brief()}\n到目前为止：\n${transcript()}\n` +
        '从你的角度问下一个问题：具体、能看出水平，可以追问之前的回答，一次只问一个。\n格式：{"question":"问题"}');
      question = text(reply?.question, 300);
    } catch { /* below */ }
    if (!live()) return;
    const turn = { by: asker.name, byId: asker.id, question: question || '（面试官没有提出问题，请简单介绍一下自己）', answer: '' };
    iv.turns.push(turn);
    iv.status = 'answering';
    app.changed();
    if (!iv.agentCandidate) return;   // a person answers through /answer
    try {
      turn.answer = text(await app.ask(iv.agentCandidate, `你在空间「${app.title}」参加模拟面试，替你的主人去面。${brief()}\n到目前为止：\n${transcript()}\n` +
        `面试官 ${asker.name} 问：${turn.question}\n像真实候选人一样回答，200 字以内。`), 1200) || '（没有回答）';
    } catch { turn.answer = '（没有回答）'; }
    if (live()) await next(round);
  }

  app.route('POST', '/answer', (member, body) => {
    const turn = iv.turns.at(-1);
    if (iv.status !== 'answering' || iv.agentCandidate || member.id !== iv.person || !turn) throw refuse('现在不该你回答', 403);
    const answer = text(body.text, 1200);
    if (!answer) throw refuse('写下你的回答');
    turn.answer = answer;
    void next(iv.round);
  });

  async function score(round) {
    Object.assign(iv, { status: 'scoring', asker: '' });
    app.changed();
    iv.scores = await Promise.all(iv.panel.map(async (p) => {
      try {
        const reply = await app.askJSON(p.id, `你在空间「${app.title}」的模拟面试里当面试官「${p.name}」，负责${p.lens}。\n${brief()}\n完整面试记录：\n${transcript()}\n` +
          `给候选人打分（1 到 10）并点评。\n格式：{"score":分数,"hire":"${HIRE.join('|')}","good":"做得好的一点","improve":"最该改进的一点"}`);
        const value = Math.round(Number(reply?.score));
        return { name: p.name, score: value >= 1 && value <= 10 ? value : null, hire: HIRE.includes(reply?.hire) ? reply.hire : '待定',
          good: text(reply?.good, 300), improve: text(reply?.improve, 300), error: '' };
      } catch (error) { return { name: p.name, score: null, hire: '待定', good: '', improve: '', error: '没有打分：' + error.message }; }
    }));
    if (iv.round !== round) return;
    iv.status = 'done';
    app.changed();
  }
}

if (require.main === module) run({ name: '模拟面试' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '模拟面试' };
