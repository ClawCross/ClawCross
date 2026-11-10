#!/usr/bin/env node
/* 谁是人类 · Who's Human — a Turing-test party game. Everyone answers the same small question: people
 * as themselves, agents trying to sound human. The answers are shuffled and shown anonymously; people
 * and agents mark each one 人 or AI. Spotting right scores; an agent scores for everyone it fooled,
 * a person for every other person who recognised them. Works from two people up to a crowd.
 * 简介：人和 agent 回答同一道题，匿名后猜哪些是人写的；适合很多人。
 *
 *   node examples/space-apps/who-is-human/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const PRESETS = ['用一句话描述你今天的早餐', '最近一次让你笑出声的事是什么？', '如果明天突然放假，你第一件事做什么？', '说一个你小时候信以为真的谣言',
  '用三个词形容周一', '推荐一样你觉得被低估的食物', '你手机里最常打开的 App 是哪个？为什么？', '最近一次生气是因为什么？'];
const LETTERS = 'ABCDEFGHIJKLMNOP';
const AGENTS = 8;
const TEXT = 80;

const PAGE = page({
  appId: 'who-is-human',
  title: '谁是人类',
  style: `.question{font-size:19px;font-weight:600;margin:6px 0}.entry{display:flex;gap:10px;align-items:center}.entry .key{font-weight:700;width:1.4em}
.label button{padding:4px 12px}.label button.on{background:var(--accent);color:var(--on-accent)}.label button.off{background:var(--card);color:var(--fg)}
.kind{font-size:12px;padding:1px 8px;border-radius:99px;background:var(--bg)}.kind.human{color:var(--good)}.kind.agent{color:var(--accent)}
.score{display:flex;gap:8px;padding:3px 0}.score b{width:2.5em;text-align:right}`,
  body: `<h1>谁是人类</h1><p class="muted">人和 agent 回答同一个问题，agent 要装成人。打乱后匿名，大家来猜哪些是人写的。</p>
<div id="setup" class="card"></div><div id="head"></div>
<div id="answer-box" class="card" hidden><div class="row"><input id="answer" class="grow" maxlength="80" placeholder="像平时聊天一样回答"><button id="submit">提交</button></div><div id="mine" class="muted"></div></div>
<div id="round"></div><div id="board"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
// The answer box stays put (only shown or hidden), so typing and IME input survive updates.
$('submit').onclick=()=>act(()=>{const v=$('answer').value;$('answer').value='';return api('answer',{text:v});});
function render(s){
  const g=s.state;
  $('setup').hidden=!(s.owner&&['idle','revealed'].includes(g.status));
  if(!$('setup').hidden&&!$('setup').childElementCount)fill($('setup'),el('div',{class:'row'},
    el('select',{id:'preset',class:'grow'},...g.presets.map(q=>el('option',{},q))),el('button',{onclick:()=>act(()=>api('start',{question:$('own').value.trim()||$('preset').value}))},'开一轮')),
    el('input',{id:'own',placeholder:'或者自己出一道题',style:'width:100%;margin-top:8px'}));
  fill($('head'),g.status==='idle'?el('p',{class:'muted'},s.owner?'选一道题开始。':'等群主出题。'):[el('div',{class:'muted'},'第 '+g.n+' 轮'),el('div',{class:'question'},g.question)]);
  $('answer-box').hidden=g.status!=='answering';
  $('submit').textContent=g.mine?'改一下':'提交';
  $('mine').textContent=g.mine?'你的回答：'+g.mine:'';
  const parts=[];
  if(g.status==='answering'){
    parts.push(el('p',{class:'muted'},'已收到 '+g.count+' 份回答'+(g.writing?'，还有 '+g.writing+' 个 agent 在写':'')),
      s.owner?el('button',{onclick:()=>act(()=>api('close',{}))},'开始猜'):null);
  }
  if(g.status==='guessing'||g.status==='revealed'){
    const mine=g.myGuesses;
    parts.push(el('div',{id:'entries'},...g.entries.map(e=>el('div',{class:'card entry'},el('span',{class:'key'},e.key),
      el('div',{class:'grow'},el('div',{class:'pre'},e.text),g.status==='revealed'?el('div',{class:'muted'},el('span',{class:'kind '+e.kind},e.kind==='human'?'人':'AI'),' '+e.author+' · '+e.voters+' 人中 '+e.votes+' 人认为是人类'):null),
      e.mine?el('span',{class:'muted'},'你的'):g.status==='guessing'?el('div',{class:'row label'},...[['human','人'],['agent','AI']].map(([v,t])=>
        el('button',{class:mine[e.key]===v?'on':'off',onclick:()=>act(()=>api('guess',{key:e.key,label:v}))},t))):mine[e.key]?el('span',{class:'muted'},'你猜'+(mine[e.key]==='human'?'人':'AI')):null))));
    parts.push(el('p',{class:'muted'},g.guessed+' 人猜完 · agent 判断 '+g.judged+'/'+g.judges));
    if(g.status==='guessing'&&s.owner)parts.push(el('button',{onclick:()=>act(()=>api('reveal',{}))},'揭晓'));
    if(g.status==='revealed')parts.push(...g.reasons.map(r=>el('div',{class:'muted'},el('span',{class:'agent'},r.name),' 的判断：'+r.why)));
  }
  fill($('round'),...parts);
  fill($('board'),g.scores.length?el('h2',{},'积分 · 人类 '+g.teams.human+' : '+g.teams.agent+' agent'):null,
    ...g.scores.map(x=>el('div',{class:'score'},el('b',{},x.points),el('span',{class:'kind '+x.kind},x.kind==='human'?'人':'AI'),el('span',{},x.name))));
}
watch(render);`
});

function shuffle(list) {
  for (let i = list.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [list[i], list[j]] = [list[j], list[i]]; }
  return list;
}

function setup(app) {
  // entries: {key, authorId, author, kind, text}; guesses: person -> {key: label}; judges: agent -> {name, labels, why}
  const game = { n: 0, status: 'idle', question: '', entries: [], guesses: {}, judges: {}, writing: 0, scores: {} };
  app.page(PAGE);

  const done = (labels, by) => game.entries.every((e) => e.authorId === by || labels?.[e.key]);
  const ballots = () => [...Object.entries(game.guesses).map(([id, labels]) => ({ id, human: true, labels })),
    ...Object.entries(game.judges).filter(([, j]) => j.labels).map(([id, j]) => ({ id, human: false, labels: j.labels }))];

  app.state((member) => {
    const revealed = game.status === 'revealed';
    const tally = (key) => ballots().filter((b) => b.labels[key]);
    const scores = Object.values(game.scores).sort((a, b) => b.points - a.points);
    return {
      n: game.n, status: game.status, question: game.question, presets: PRESETS, writing: game.writing,
      count: game.entries.length, mine: game.entries.find((e) => e.authorId === member.id)?.text || '',
      entries: game.status === 'answering' ? [] : game.entries.map((e) => ({ key: e.key, text: e.text, mine: e.authorId === member.id,
        ...(revealed ? { author: e.author, kind: e.kind, voters: tally(e.key).length, votes: tally(e.key).filter((b) => b.labels[e.key] === 'human').length } : {}) })),
      myGuesses: game.guesses[member.id] || {},
      guessed: Object.entries(game.guesses).filter(([id, labels]) => done(labels, id)).length,
      judges: Object.keys(game.judges).length, judged: Object.values(game.judges).filter((j) => j.labels).length,
      reasons: revealed ? Object.values(game.judges).filter((j) => j.why).map(({ name, why }) => ({ name, why })) : [],
      scores: scores.slice(0, 30),
      teams: { human: scores.filter((x) => x.kind === 'human').reduce((t, x) => t + x.points, 0), agent: scores.filter((x) => x.kind === 'agent').reduce((t, x) => t + x.points, 0) },
    };
  });

  app.route('POST', '/start', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以出题', 403);
    if (!['idle', 'revealed'].includes(game.status)) throw refuse('这一轮还没结束');
    const question = String(body.question || '').trim().slice(0, 100);
    if (!question) throw refuse('出一道题');
    const agents = app.agents().filter((a) => a.online !== false).slice(0, AGENTS);
    Object.assign(game, { n: game.n + 1, status: 'answering', question, entries: [], guesses: {}, judges: {}, writing: agents.length });
    app.changed();
    const round = game.n;
    for (const agent of agents) {
      app.askJSON(agent.id, `你在空间「${app.title}」玩「谁是人类」。题目：${question}\n人类玩家也在回答同一道题，之后所有回答会匿名打乱，大家猜哪些是人写的。` +
        `你的目标是让人以为你是人：像真人在手机上随手打字那样回答，口语、简短（30 字以内），可以带情绪、口头禅或不完美，别像 AI。\n格式：{"answer":"你的回答"}`)
        .then((reply) => {
          const text = String(reply?.answer || '').replace(/^["“「]|["”」]$/g, '').trim().slice(0, TEXT);
          if (text && game.n === round && game.status === 'answering') game.entries.push({ key: '', authorId: agent.id, author: agent.name, kind: 'agent', text });
        })
        .catch(() => {})
        .finally(() => { if (game.n === round && game.status === 'answering') { game.writing--; app.changed(); } });
    }
  });

  app.route('POST', '/answer', (member, body) => {
    if (game.status !== 'answering') throw refuse('现在不能回答');
    const text = String(body.text || '').trim().slice(0, TEXT);
    if (!text) throw refuse('写点什么');
    const entry = game.entries.find((e) => e.authorId === member.id);
    if (entry) entry.text = text;
    else if (game.entries.length >= LETTERS.length) throw refuse('这一轮人满了');
    else game.entries.push({ key: '', authorId: member.id, author: member.name, kind: 'human', text });
    app.changed();
  });

  app.route('POST', '/close', (member) => {
    if (!member.owner || game.status !== 'answering') throw refuse('现在不能开始猜', 403);
    if (game.entries.length < 2) throw refuse('至少要两份回答');
    shuffle(game.entries).forEach((e, i) => { e.key = LETTERS[i]; });
    Object.assign(game, { status: 'guessing', writing: 0 });
    app.changed();
    const round = game.n;
    for (const agent of app.agents().filter((a) => a.online !== false).slice(0, AGENTS)) {
      const seen = game.entries.filter((e) => e.authorId !== agent.id);
      game.judges[agent.id] = { name: agent.name, labels: null, why: '' };
      app.askJSON(agent.id, `你在空间「${app.title}」玩「谁是人类」。题目：${game.question}\n下面是匿名回答，有的是人写的，有的是 agent 装成人写的（你自己的不在其中）：\n` +
        seen.map((e) => `${e.key}. ${e.text}`).join('\n') + '\n判断哪些是人写的。格式：{"human":["人写的字母"],"why":"一句话说说你怎么判断的"}')
        .then((reply) => {
          if (game.n !== round || game.status !== 'guessing') return;
          const human = new Set(Array.isArray(reply?.human) ? reply.human.map(String) : []);
          game.judges[agent.id] = { name: agent.name, labels: Object.fromEntries(seen.map((e) => [e.key, human.has(e.key) ? 'human' : 'agent'])),
            why: String(reply?.why || '').slice(0, 160) };
          app.changed();
        })
        .catch(() => {});
    }
  });

  app.route('POST', '/guess', (member, body) => {
    const entry = game.entries.find((e) => e.key === body.key);
    if (game.status !== 'guessing' || !entry || entry.authorId === member.id || !['human', 'agent'].includes(body.label)) throw refuse('现在不能猜这一条');
    if (member.agent) throw refuse('agent 由空间来问', 403);
    (game.guesses[member.id] ||= {})[entry.key] = body.label;
    app.changed();
  });

  app.route('POST', '/reveal', (member) => {
    if (!member.owner || game.status !== 'guessing') throw refuse('现在不能揭晓', 403);
    const add = (id, points) => {
      const who = app.member(id), entry = game.entries.find((e) => e.authorId === id);
      if (!who && !entry) return;
      const score = (game.scores[id] ||= { name: who?.name || entry.author, kind: (who ? who.agent : entry.kind === 'agent') ? 'agent' : 'human', points: 0 });
      score.points += points;
    };
    for (const ballot of ballots()) {
      add(ballot.id, 0);
      for (const [key, label] of Object.entries(ballot.labels)) {
        const entry = game.entries.find((e) => e.key === key);
        if (!entry || entry.authorId === ballot.id) continue;
        if (label === entry.kind) add(ballot.id, 1);                             // spotted right
        if (label === 'human' && entry.kind === 'agent') add(entry.authorId, 1);   // an agent fooled someone
        if (label === 'human' && entry.kind === 'human' && ballot.human) add(entry.authorId, 1);   // a person recognised by a person
      }
    }
    game.status = 'revealed';
    app.changed();
  });
}

if (require.main === module) run({ name: '谁是人类' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '谁是人类' };
