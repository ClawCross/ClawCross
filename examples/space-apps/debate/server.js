#!/usr/bin/env node
/* 辩论擂台 · Debate Arena — two agents argue a motion over three rounds (opening, rebuttal, closing).
 * Each agent's own person can pass it a coaching note before its next speech; an optional third
 * agent judges, and everyone votes.
 *
 *   node examples/space-apps/debate/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const PHASES = ['立论', '反驳', '总结'];
const MOTIONS = ['AI 写的代码应该署名 AI', '远程办公比坐班更高效', '大学应该取消期末考试', '城市应该全面禁止燃油车',
  '短视频让人变笨了', '人类应该移民火星', '加班文化弊大于利', '未来十年 AI 会取代大多数程序员'];

const PAGE = page({
  title: '辩论擂台',
  style: `.speech{border-radius:14px;padding:10px 12px;margin:8px 0;max-width:88%}.speech.pro{background:#e7f1ff;color:#0b3a7a}
.speech.con{background:#fff0e6;color:#7a3300;margin-left:auto}@media(prefers-color-scheme:dark){.speech.pro{background:#0b2545;color:#cfe3ff}.speech.con{background:#3d1f0a;color:#ffd8b8}}
.bar{height:10px;border-radius:99px;background:#ffd8b8;overflow:hidden}.bar span{display:block;height:100%;background:#4c8dff}`,
  body: `<h1>辩论擂台</h1><div id="setup" class="card"></div><div id="stage"></div>`,
  script: `
const {api,watch,act,el,keep}=App,$=id=>document.getElementById(id);
let draft=null;
function setupForm(s){
  const agents=s.members.filter(m=>m.agent),pick=(id,label)=>el('label',{class:'grow'},label,el('select',{id},...agents.map((a,i)=>el('option',{value:a.id,selected:(id==='pro'&&i===0)||(id==='con'&&i===1)},a.name))));
  return [el('div',{class:'muted'},'辩题'),el('input',{id:'motion',style:'width:100%',placeholder:'输入辩题，或点下面的热门辩题',value:draft||''}),
    el('div',{class:'row',style:'margin:6px 0'},...s.state.motions.map(m=>el('button',{class:'ghost',onclick:()=>{draft=m;$('motion').value=m;}},m))),
    el('div',{class:'row'},pick('pro','正方 '),pick('con','反方 '),
      el('label',{class:'grow'},'评委 ',el('select',{id:'judge'},el('option',{value:''},'不设评委'),...agents.map(a=>el('option',{value:a.id},a.name))))),
    el('button',{style:'margin-top:8px',onclick:()=>act(()=>api('start',{motion:$('motion').value,pro:$('pro').value,con:$('con').value,judge:$('judge').value}))},'开始辩论')];
}
function render(s){
  const d=s.state.debate;
  $('setup').hidden=!(s.owner&&(!d||d.status==='done'));
  if(!$('setup').hidden&&!$('setup').childElementCount)$('setup').replaceChildren(...setupForm(s));
  if(!d){App.fill($('stage'),el('p',{class:'muted'},s.owner?'设置辩题和双方 agent 后开始。':'等待群主开启一场辩论。'));return;}
  const mine=['pro','con'].find(side=>d[side].coach===s.me.id);
  const votes=d.votes.pro+d.votes.con;
  App.fill($('stage'),
    el('h2',{},d.motion),el('p',{class:'muted'},'正方 '+d.pro.name+'（'+d.pro.coachName+'）　对阵　反方 '+d.con.name+'（'+d.con.coachName+'）'),
    ...d.speeches.map(x=>el('div',{class:'speech '+x.side},el('strong',{},(x.side==='pro'?'正方':'反方')+' · '+x.phase+' · '+d[x.side].name),el('div',{class:'pre'},x.text))),
    d.status==='running'?el('p',{class:'muted'},'正在发言：'+(d.next?d[d.next.side].name+'（'+d.next.phase+'）':'…')):null,
    mine&&d.status==='running'?el('div',{class:'card'},el('div',{class:'muted'},'场外指导（只有你的 agent 会在下一次发言前看到）'),
      el('div',{class:'row'},keep('coach',()=>el('input',{id:'coach',class:'grow',placeholder:'例如：抓住对方第二点里的数据漏洞'})),
        el('button',{onclick:()=>act(()=>{const v=$('coach').value;$('coach').value='';return api('coach',{note:v});})},'递纸条')),
      d[mine].note?el('div',{class:'muted'},'待用纸条：'+d[mine].note):null):null,
    d.status==='done'?el('div',{class:'card'},el('strong',{},'投票'),el('div',{class:'bar'},el('span',{style:'width:'+(votes?d.votes.pro*100/votes:50)+'%'})),
      el('div',{class:'row'},el('button',{onclick:()=>act(()=>api('vote',{side:'pro'}))},'支持正方 '+d.votes.pro),el('span',{class:'grow'}),
        el('button',{onclick:()=>act(()=>api('vote',{side:'con'}))},'支持反方 '+d.votes.con)),
      d.verdict?el('p',{class:'pre'},'评委 '+d.verdict.name+'：'+(d.verdict.winner==='pro'?'正方胜':d.verdict.winner==='con'?'反方胜':'')+'。'+d.verdict.reason):null):null);
}
watch(render);`
});

function speechPrompt(app, d, side, phase) {
  const me = d[side], other = d[side === 'pro' ? 'con' : 'pro'];
  const transcript = d.speeches.map((x) => `【${x.side === 'pro' ? '正方' : '反方'}·${x.phase}】${x.text}`).join('\n') || '（还没有人发言）';
  return `你是空间「${app.title}」辩论擂台上的${side === 'pro' ? '正方' : '反方'}辩手「${me.name}」，对手是「${other.name}」。\n` +
    `辩题：${d.motion}（正方支持，反方反对）\n现在是你的「${phase}」发言。\n已有发言：\n${transcript}\n` +
    (me.note ? `你的队友 ${me.coachName} 递来纸条：${me.note}\n` : '') +
    '用 150–250 字发言，观点鲜明、有理有据，必要时直接回应对方。只输出发言内容本身。';
}

function setup(app) {
  let debate = null;
  app.page(PAGE);
  app.state(() => ({ motions: MOTIONS, debate: debate && { ...debate, votes: { pro: debate.votes.pro.size, con: debate.votes.con.size } } }));

  const side = (id) => {
    const agent = app.member(id);
    if (!agent?.agent) throw refuse('请选择空间里的 agent');
    const coach = app.ownerOf(id);
    return { id, name: agent.name, coach: coach?.id || '', coachName: coach?.name || '无人指导', note: '' };
  };

  app.route('POST', '/start', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以开启辩论', 403);
    if (debate?.status === 'running') throw refuse('辩论正在进行');
    const motion = String(body.motion || '').trim().slice(0, 120);
    if (!motion) throw refuse('写下辩题');
    if (body.pro === body.con) throw refuse('正反方要选两个不同的 agent');
    const judge = body.judge ? app.member(body.judge) : null;
    debate = { motion, pro: side(body.pro), con: side(body.con), judge: judge?.agent ? { id: judge.id, name: judge.name } : null,
      speeches: [], status: 'running', next: null, verdict: null, votes: { pro: new Set(), con: new Set() } };
    app.changed();
    void hold(debate);
  });

  /** The debate itself, in the background: three rounds, then the judge. */
  async function hold(d) {
    try {
      for (const phase of PHASES) {
        for (const s of ['pro', 'con']) {
          d.next = { side: s, phase };
          app.changed();
          const prompt = speechPrompt(app, d, s, phase);
          d[s].note = '';   // a note is used once, by the speech it was passed for
          const text = await app.ask(d[s].id, prompt);
          d.speeches.push({ side: s, phase, text: String(text).trim().slice(0, 1200) });
        }
      }
      d.next = null;
      d.status = 'done';
      app.changed();
      if (d.judge) {
        const verdict = await app.askJSON(d.judge.id, `你是空间「${app.title}」辩论擂台的评委「${d.judge.name}」。辩题：${d.motion}\n` +
          d.speeches.map((x) => `【${x.side === 'pro' ? '正方' : '反方'}·${x.phase}】${x.text}`).join('\n') +
          '\n根据论证质量判定胜方。格式：{"winner":"pro或con","reason":"两三句理由"}');
        d.verdict = { name: d.judge.name, winner: verdict?.winner === 'con' ? 'con' : verdict?.winner === 'pro' ? 'pro' : '', reason: String(verdict?.reason || '').slice(0, 400) };
      }
    } catch (error) {
      d.status = 'done';
      d.speeches.push({ side: d.next?.side || 'pro', phase: '中断', text: '辩论中断：' + error.message });
    }
    app.changed();
  }

  app.route('POST', '/coach', (member, body) => {
    const s = ['pro', 'con'].find((k) => debate?.[k].coach === member.id);
    if (!s || debate.status !== 'running') throw refuse('只有辩手的队友能在辩论中递纸条', 403);
    debate[s].note = String(body.note || '').trim().slice(0, 300);
    app.changed();
  });

  app.route('POST', '/vote', (member, body) => {
    if (debate?.status !== 'done' || !['pro', 'con'].includes(body.side)) throw refuse('辩论结束后才能投票');
    debate.votes.pro.delete(member.id); debate.votes.con.delete(member.id);
    debate.votes[body.side].add(member.id);
    app.changed();
  });
}

if (require.main === module) run({ name: '辩论擂台' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '辩论擂台' };
