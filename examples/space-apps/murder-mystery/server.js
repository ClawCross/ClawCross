#!/usr/bin/env node
/* 剧本杀 · Murder Mystery — one agent writes a case; the suspects are played by the other agents in
 * the space, each given only its own character sheet (the culprit knows it did it and may lie).
 * People are the detectives: they question suspects, search for hidden clues and accuse. The owner
 * closes the case and the truth comes out.
 * 简介：一个 agent 写案子，其他 agent 扮演嫌疑人，人当侦探。
 *
 *   node examples/space-apps/murder-mystery/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const SETTINGS = ['民国上海的一座公馆，寿宴当晚', '暴风雪困住的山顶旅馆', '近地轨道空间站的一次例行补给', '大学话剧社的首演后台', '江南古镇的茶楼，开张前夜'];
const QUESTIONS = 6;   // per detective
const text = (x, max) => String(x || '').trim().slice(0, max);

const PAGE = page({
  title: '剧本杀',
  style: `.suspect{flex:1;min-width:180px}.suspect.picked{outline:2px solid var(--accent)}.guilty{color:var(--bad);font-weight:700}
.qa{padding:5px 0;border-bottom:1px solid var(--line);font-size:14px}.clue{font-size:14px}.story{line-height:1.8}`,
  body: `<h1>剧本杀</h1><p class="muted">一个 agent 写案子，其他 agent 扮演嫌疑人，各自只知道自己的秘密。你是侦探：审问、搜查、指认凶手。</p>
<div id="setup" class="card" hidden></div><div id="case"></div>
<div id="ask-box" class="card" hidden><div class="row"><select id="suspect"></select><input id="question" class="grow" placeholder="你昨晚十点在哪里？"><button id="ask">审问</button></div>
<div class="row" style="margin-top:8px"><span id="left" class="muted grow"></span><button id="search" class="ghost">搜查一次</button></div></div>
<div id="log"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
$('ask').onclick=()=>act(()=>{const v=$('question').value;$('question').value='';return api('ask',{suspect:$('suspect').value,question:v});});
$('search').onclick=()=>act(()=>api('search',{}));
function render(s){
  const g=s.state,c=g.case;
  $('setup').hidden=!(s.owner&&['idle','closed'].includes(g.status));
  if(!$('setup').hidden&&!$('setup').childElementCount)fill($('setup'),el('div',{class:'row'},
    el('label',{},'编剧 ',el('select',{id:'writer'},...s.members.filter(m=>m.agent).map(a=>el('option',{value:a.id},a.name)))),
    el('select',{id:'setting',class:'grow'},...g.settings.map(x=>el('option',{},x))),el('button',{onclick:()=>act(()=>api('begin',{writer:$('writer').value,setting:$('setting').value}))},'开案')));
  $('ask-box').hidden=g.status!=='open';
  if(c&&$('suspect').options.length!==c.suspects.length)fill($('suspect'),...c.suspects.map(x=>el('option',{value:x.name},x.name)));
  $('left').textContent='你还能问 '+g.left+' 个问题 · 待搜查线索 '+g.hiddenLeft+' 条';
  if(g.status==='idle'||g.status==='writing'||!c){fill($('case'),el('p',{class:'muted'},g.status==='writing'?g.writer+' 正在写案子…':g.error||(s.owner?'选编剧和场景，开案。':'等群主开案。')));fill($('log'));return;}
  fill($('case'),el('div',{class:'card'},el('h2',{style:'margin-top:0'},c.title),el('div',{class:'story pre'},c.story),
      el('h2',{},'线索'),...c.clues.map(x=>el('div',{class:'clue'},'🔎 '+x.text+(x.by?'（'+x.by+' 搜到）':'')))),
    el('div',{class:'row'},...c.suspects.map(x=>el('div',{class:'card suspect'+(g.myPick===x.name?' picked':'')},el('strong',{},x.name),' ',el('span',{class:'muted'},'由 ',el('span',{class:'agent'},x.agent),' 扮演'),
      el('div',{class:'muted'},x.intro),x.secret?el('div',{class:x.guilty?'guilty':''},(x.guilty?'凶手 · ':'秘密 · ')+x.secret):null,
      g.status==='open'?el('button',{class:g.myPick===x.name?'':'ghost',style:'margin-top:6px',onclick:()=>act(()=>api('accuse',{suspect:x.name}))},g.myPick===x.name?'已指认':'指认'):null))),
    g.status==='open'?el('div',{class:'row'},el('span',{class:'muted grow'},g.accusers+' 位侦探已指认'),s.owner?el('button',{onclick:()=>act(()=>api('close',{}))},'结案，揭晓真相'):null):null,
    g.status==='closed'?el('div',{class:'card'},el('h2',{style:'margin-top:0'},'真相'),el('div',{class:'pre'},c.truth),el('p',{},'猜对的侦探：'+(g.winners.join('、')||'没有人'))):null);
  fill($('log'),...g.log.slice().reverse().map(q=>el('div',{class:'qa'},el('div',{class:'muted'},q.by+' 问 '+q.suspect+'：'+q.question),
    el('div',{},q.answer?q.suspect+'：'+q.answer:q.suspect+' 正在想…'))));
}
watch(render);`
});

function setup(app) {
  // case: {title, story, suspects: [{name, intro, secret, guilty, agent, agentName}], clues: [{text, by}], hidden: [text], truth}
  const game = { round: 0, status: 'idle', writer: '', error: '', case: null, log: [], asked: new Map(), picks: new Map() };
  app.page(PAGE);

  app.state((member) => {
    const c = game.case, closed = game.status === 'closed';
    return {
      status: game.status, settings: SETTINGS, writer: game.writer, error: game.error,
      left: QUESTIONS - (game.asked.get(member.id) || 0), hiddenLeft: c?.hidden.length || 0,
      myPick: game.picks.get(member.id) || '', accusers: game.picks.size, log: game.log.slice(-40),
      winners: closed ? [...game.picks].filter(([, name]) => c.suspects.find((x) => x.name === name)?.guilty).map(([id]) => app.member(id)?.name || '侦探') : [],
      // Secrets, the culprit and the truth stay on the server until the case is closed.
      case: c && { title: c.title, story: c.story, clues: c.clues, truth: closed ? c.truth : '',
        suspects: c.suspects.map((x) => ({ name: x.name, intro: x.intro, agent: x.agentName, secret: closed ? x.secret : '', guilty: closed && x.guilty })) },
    };
  });

  app.route('POST', '/begin', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以开案', 403);
    if (['writing', 'open'].includes(game.status)) throw refuse('案子还没结');
    const writer = app.member(body.writer);
    if (!writer?.agent) throw refuse('选一个 agent 当编剧');
    const setting = SETTINGS.includes(body.setting) ? body.setting : SETTINGS[0];
    Object.assign(game, { round: game.round + 1, status: 'writing', writer: writer.name, error: '', case: null, log: [], asked: new Map(), picks: new Map() });
    app.changed();
    const round = game.round;
    app.askJSON(writer.id, `你是空间「${app.title}」的剧本杀编剧。写一个短篇推理案件，场景：${setting}。\n要求：3 到 4 个嫌疑人，恰好 1 个凶手；` +
      `每个嫌疑人有公开介绍和只有自己知道的秘密（凶手的秘密写清作案经过；其他人也各有隐瞒的小事，制造干扰）；公开案情 150 字内；` +
      `3 条公开线索、3 条要搜查才能发现的线索；凶手必须能从线索推理出来；真相解释 150 字内。\n` +
      '格式：{"title":"案名","story":"公开案情","suspects":[{"name":"称呼","intro":"公开介绍","secret":"秘密","guilty":true或false}],"clues":["公开线索"],"hidden":["搜查线索"],"truth":"真相"}', { timeout: 8 * 60_000 })
      .then((reply) => {
        if (game.round !== round) return;
        const suspects = (Array.isArray(reply?.suspects) ? reply.suspects : []).slice(0, 5)
          .map((x) => ({ name: text(x?.name, 20), intro: text(x?.intro, 200), secret: text(x?.secret, 400), guilty: x?.guilty === true }))
          .filter((x, i, all) => x.name && all.findIndex((y) => y.name === x.name) === i);
        if (suspects.length < 2 || suspects.filter((x) => x.guilty).length !== 1) throw new Error('编剧写的案子不成立（要有恰好一个凶手）');
        // Suspects go to the other agents in turn; with no other agent the writer plays them too.
        const players = app.agents().filter((a) => a.id !== writer.id && a.online !== false);
        const cast = players.length ? players : [writer];
        suspects.forEach((x, i) => Object.assign(x, { agent: cast[i % cast.length].id, agentName: cast[i % cast.length].name }));
        const list = (v, n) => (Array.isArray(v) ? v : []).map((x) => text(x, 200)).filter(Boolean).slice(0, n);
        game.case = { title: text(reply.title, 40) || '无名案', story: text(reply.story, 600), suspects, truth: text(reply.truth, 800),
          clues: list(reply.clues, 5).map((t) => ({ text: t, by: '' })), hidden: list(reply.hidden, 5) };
        game.status = 'open';
      })
      .catch((error) => { if (game.round === round) Object.assign(game, { status: 'idle', error: '没写成：' + error.message }); })
      .finally(() => app.changed());
  });

  app.route('POST', '/ask', (member, body) => {
    const suspect = game.case?.suspects.find((x) => x.name === body.suspect);
    const question = text(body.question, 150);
    if (game.status !== 'open' || !suspect) throw refuse('现在不能审问');
    if (!question) throw refuse('问点什么');
    if ((game.asked.get(member.id) || 0) >= QUESTIONS) throw refuse('你的问题用完了');
    game.asked.set(member.id, (game.asked.get(member.id) || 0) + 1);
    const entry = { by: member.name, suspect: suspect.name, question, answer: '' };
    const record = game.log.filter((q) => q.answer).slice(-12).map((q) => `${q.by} 问 ${q.suspect}：${q.question}\n${q.suspect}：${q.answer}`).join('\n') || '（还没有）';
    game.log.push(entry);
    app.changed();
    const c = game.case, round = game.round;
    app.ask(suspect.agent, `你在空间「${app.title}」的剧本杀里扮演「${suspect.name}」。案情（公开）：${c.story}\n公开线索：${c.clues.map((x) => x.text).join('；')}\n` +
      `你的公开身份：${suspect.intro}\n只有你知道的秘密：${suspect.secret}\n` +
      (suspect.guilty ? '你就是凶手：可以撒谎、回避，但不要和公开线索矛盾，也不要直接承认。\n' : '你不是凶手：可以隐瞒你的小秘密，但关于案发的关键事实要说真话。\n') +
      `之前的审问：\n${record}\n侦探 ${member.name} 问你：${question}\n用「${suspect.name}」的口吻回答，60 字以内，只说台词。`)
      .then((answer) => { entry.answer = text(answer, 300) || '……'; })
      .catch(() => { entry.answer = '（没有回应）'; })
      .finally(() => { if (game.round === round) app.changed(); });
  });

  app.route('POST', '/search', (member) => {
    if (game.status !== 'open') throw refuse('现在不能搜查');
    if (!game.case.hidden.length) throw refuse('能搜的都搜过了');
    game.case.clues.push({ text: game.case.hidden.shift(), by: member.name });
    app.changed();
  });

  app.route('POST', '/accuse', (member, body) => {
    if (game.status !== 'open' || !game.case.suspects.some((x) => x.name === body.suspect)) throw refuse('现在不能指认');
    if (member.agent) throw refuse('侦探是人', 403);
    game.picks.set(member.id, body.suspect);
    app.changed();
  });

  app.route('POST', '/close', (member) => {
    if (!member.owner || game.status !== 'open') throw refuse('现在不能结案', 403);
    game.status = 'closed';
    app.changed();
  });
}

if (require.main === module) run({ name: '剧本杀' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '剧本杀' };
