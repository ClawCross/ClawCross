#!/usr/bin/env node
/* Emoji 猜谜 · Emoji Riddles — each round an agent secretly picks a film, song, idiom or city and
 * describes it in emoji only. People guess as often as they like; the other agents get one guess
 * each. Right guesses stay hidden from the others (only "got it" shows), wrong ones are public.
 * First right guess scores 3, later ones 1, and the riddler 1 for everyone who got it.
 *
 *   node examples/space-apps/emoji-riddle/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const CATEGORIES = ['电影', '歌曲', '成语', '城市', '动画'];
const norm = (x) => String(x || '').toLowerCase().replace(/[\s\p{P}\p{S}]/gu, '');

const PAGE = page({
  title: 'Emoji 猜谜',
  style: `.riddle{font-size:44px;letter-spacing:4px;text-align:center;margin:14px 0;line-height:1.3}.guess{font-size:14px;padding:2px 0}.right{color:var(--good);font-weight:600}
.score{display:flex;gap:8px}.score b{width:2em;text-align:right}`,
  body: `<h1>Emoji 猜谜</h1><p class="muted">agent 只用 emoji 描述一部电影、一首歌或一个成语，大家来猜。猜对了别人看不到你的答案。</p>
<div id="setup" class="card" hidden></div><div id="round"></div>
<div id="guess-box" class="card" hidden><div class="row"><input id="guess" class="grow" placeholder="你的答案"><button id="send">猜</button></div></div>
<div id="log"></div><div id="board"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
$('send').onclick=()=>act(()=>{const v=$('guess').value;$('guess').value='';return api('guess',{text:v});});
$('guess').onkeydown=e=>{if(e.key==='Enter'&&!e.isComposing)$('send').click();};
function render(s){
  const g=s.state;
  $('setup').hidden=!(s.owner&&['idle','revealed'].includes(g.status));
  if(!$('setup').hidden&&!$('setup').childElementCount)fill($('setup'),el('div',{class:'row'},
    el('select',{id:'category',class:'grow'},...g.categories.map(c=>el('option',{},c))),el('button',{onclick:()=>act(()=>api('start',{category:$('category').value}))},'下一题')),
    el('div',{class:'muted',style:'margin-top:6px'},'出题的 agent 轮流来。'));
  $('guess-box').hidden=!(g.status==='guessing'&&!g.solved);
  fill($('round'),g.status==='idle'?el('p',{class:'muted'},g.error||(s.owner?'选一个类别出题。':'等群主出题。'))
    :g.status==='writing'?el('p',{class:'muted'},g.riddler+' 正在想…')
    :el('div',{class:'card'},el('div',{class:'muted'},'第 '+g.n+' 题 · '+g.category+' · 出题：',el('span',{class:'agent'},g.riddler)),
      el('div',{class:'riddle'},g.emoji),g.hint?el('div',{class:'muted'},'提示：'+g.hint):null,
      g.status==='revealed'?el('h2',{},'答案：'+g.secret):null,g.solved&&g.status==='guessing'?el('p',{class:'right'},'你猜对了！'):null,
      s.owner&&g.status==='guessing'?el('div',{class:'row'},g.hint?null:el('button',{class:'ghost',onclick:()=>act(()=>api('hint',{}))},'给个提示'),
        el('button',{onclick:()=>act(()=>api('reveal',{}))},'揭晓')):null));
  fill($('log'),...g.guesses.slice().reverse().map(x=>el('div',{class:'guess'+(x.right?' right':'')},(x.agent?'🤖 ':'')+x.name+(x.right?' 猜对了 ✓'+(x.text?'：'+x.text:''):' 猜：'+x.text+' ✗'))));
  fill($('board'),g.scores.length?el('h2',{},'积分'):null,...g.scores.map(x=>el('div',{class:'score'},el('b',{},x.points),el('span',{class:x.agent?'agent':''},x.name))));
}
watch(render);`
});

function setup(app) {
  // guesses: {id, name, agent, text, right}
  const game = { n: 0, status: 'idle', category: '', riddler: '', riddlerId: '', secret: '', aliases: [], emoji: '', hint: '', hintShown: false,
    guesses: [], error: '', turn: 0 };
  const scores = new Map();   // id -> {name, agent, points}
  app.page(PAGE);

  const solved = (id) => game.guesses.some((x) => x.id === id && x.right);
  app.state((member) => {
    const revealed = game.status === 'revealed';
    return {
      categories: CATEGORIES, n: game.n, status: game.status, category: game.category, riddler: game.riddler, error: game.error,
      emoji: game.emoji, hint: game.hintShown ? game.hint : '', secret: revealed ? game.secret : '', solved: solved(member.id),
      // A right answer stays hidden from everyone but its guesser until the reveal.
      guesses: game.guesses.slice(-50).map((x) => ({ name: x.name, agent: x.agent, right: x.right, text: x.right && !revealed && x.id !== member.id ? '' : x.text })),
      scores: [...scores.values()].sort((a, b) => b.points - a.points).slice(0, 30),
    };
  });

  const score = (id, name, agent, points) => {
    if (!scores.has(id)) scores.set(id, { name, agent, points: 0 });
    scores.get(id).points += points;
  };
  function judge(id, name, agent, text) {
    const right = [game.secret, ...game.aliases].some((answer) => norm(answer) && norm(answer) === norm(text));
    game.guesses.push({ id, name, agent, text, right });
    if (right) {
      score(id, name, agent, game.guesses.filter((x) => x.right).length === 1 ? 3 : 1);
      score(game.riddlerId, game.riddler, true, 1);
    }
    app.changed();
    return right;
  }

  app.route('POST', '/start', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以出题', 403);
    if (['writing', 'guessing'].includes(game.status)) throw refuse('这一题还没结束');
    const agents = app.agents().filter((a) => a.online !== false);
    if (!agents.length) throw refuse('空间里没有在线的 agent');
    const riddler = agents[game.turn++ % agents.length];
    const category = CATEGORIES.includes(body.category) ? body.category : CATEGORIES[0];
    Object.assign(game, { n: game.n + 1, status: 'writing', category, riddler: riddler.name, riddlerId: riddler.id, secret: '', aliases: [], emoji: '',
      hint: '', hintShown: false, guesses: [], error: '' });
    app.changed();
    const n = game.n;
    app.askJSON(riddler.id, `你在空间「${app.title}」玩 Emoji 猜谜，这一题你来出。类别：${category}。选一个大家都熟悉的${category}，只用 emoji 描述它（3 到 8 个，不要文字、字母或数字）。` +
      `再给出它的常见别名（用来判对）和一句文字提示。\n格式：{"secret":"答案","aliases":["别名"],"emoji":"🙂🙃","hint":"提示"}`)
      .then((reply) => {
        if (game.n !== n) return;
        const emoji = String(reply?.emoji || '').replace(/[\p{L}\p{N}\s]/gu, '').slice(0, 40);
        const secret = String(reply?.secret || '').trim().slice(0, 40);
        if (!emoji || !secret) throw new Error('出的题不是纯 emoji');
        Object.assign(game, { status: 'guessing', secret, emoji, hint: String(reply?.hint || '').slice(0, 80),
          aliases: (Array.isArray(reply?.aliases) ? reply.aliases : []).map((x) => String(x).slice(0, 40)).slice(0, 6) });
        app.changed();
        // Every other agent gets one guess.
        for (const agent of agents.filter((a) => a.id !== riddler.id).slice(0, 6)) {
          app.askJSON(agent.id, `你在空间「${app.title}」玩 Emoji 猜谜。类别：${category}。谜面：${emoji}\n猜猜是什么，只有一次机会。\n格式：{"guess":"你的答案"}`)
            .then((r) => { if (game.n === n && game.status === 'guessing' && r?.guess) judge(agent.id, agent.name, true, String(r.guess).slice(0, 40)); })
            .catch(() => {});
        }
      })
      .catch((error) => { if (game.n === n) { Object.assign(game, { status: 'idle', error: '没出成题：' + error.message }); app.changed(); } });
  });

  app.route('POST', '/guess', (member, body) => {
    const text = String(body.text || '').trim().slice(0, 40);
    if (game.status !== 'guessing') throw refuse('现在不能猜');
    if (member.agent) throw refuse('agent 由空间来问', 403);
    if (solved(member.id)) throw refuse('你已经猜对了');
    if (!text) throw refuse('写下你的答案');
    judge(member.id, member.name, false, text);
  });

  app.route('POST', '/hint', (member) => {
    if (!member.owner || game.status !== 'guessing') throw refuse('现在不能给提示', 403);
    game.hintShown = true;
    app.changed();
  });

  app.route('POST', '/reveal', (member) => {
    if (!member.owner || game.status !== 'guessing') throw refuse('现在不能揭晓', 403);
    game.status = 'revealed';
    app.changed();
  });
}

if (require.main === module) run({ name: 'Emoji 猜谜' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: 'Emoji 猜谜' };
