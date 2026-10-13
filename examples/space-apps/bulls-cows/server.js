#!/usr/bin/env node
/* 猜数字对决 · Bulls and Cows — each side keeps a secret of four different digits; they take turns
 * guessing the other's and the server answers "xAyB" (A: right digit, right place; B: right digit,
 * wrong place). Play your own secret against an agent, or seat two agents and watch them reason.
 * The server keeps both secrets and scores every guess; no agent ever sees the other's number.
 * 简介：1A2B 猜数字，人对 agent，或两个 agent 对战。
 *
 *   node examples/space-apps/bulls-cows/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const TURNS = 12;
const valid = (x) => /^\d{4}$/.test(x) && new Set(x).size === 4;
const randomSecret = () => [...'0123456789'].sort(() => Math.random() - 0.5).slice(0, 4).join('');
function score(secret, guess) {
  let a = 0, b = 0;
  for (let i = 0; i < 4; i++) if (guess[i] === secret[i]) a++; else if (secret.includes(guess[i])) b++;
  return `${a}A${b}B`;
}

const PAGE = page({
  title: '猜数字对决',
  style: `.side{flex:1;min-width:200px}.log{font:15px ui-monospace,monospace;padding:2px 0}.win{color:var(--good);font-weight:700}.secret{letter-spacing:4px;font:700 18px ui-monospace,monospace}`,
  body: `<h1>猜数字对决</h1><p class="muted">双方各藏一个四位数（数字不重复），轮流猜对方的。A = 数字和位置都对，B = 数字对位置不对。</p>
<div id="setup" class="card" hidden><div class="row"><label>我的秘密数字 <input id="secret" maxlength="4" inputmode="numeric" placeholder="1234" style="width:6em"></label>
<label>对手 <select id="rival"></select></label><button id="me">我来挑战</button></div>
<div class="row" id="owner-row" style="margin-top:8px" hidden><span class="muted">或者看两个 agent 对战：</span><select id="a"></select><select id="b"></select><button id="duel" class="ghost">开战</button></div></div>
<div id="status" class="card"></div>
<div id="guess-box" class="card" hidden><div class="row"><input id="guess" maxlength="4" inputmode="numeric" placeholder="猜四位数" class="grow"><button id="send">猜</button></div></div>
<div id="sides" class="row"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
$('me').onclick=()=>act(()=>api('challenge',{secret:$('secret').value,rival:$('rival').value}));
$('duel').onclick=()=>act(()=>api('duel',{a:$('a').value,b:$('b').value}));
$('send').onclick=()=>act(()=>{const v=$('guess').value;$('guess').value='';return api('guess',{guess:v});});
$('guess').onkeydown=e=>{if(e.key==='Enter')$('send').click();};
function render(s){
  const g=s.state,agents=s.members.filter(m=>m.agent);
  $('setup').hidden=g.status==='playing';$('owner-row').hidden=!s.owner;
  for(const [id,i] of [['rival',0],['a',0],['b',1]])if($(id).options.length!==agents.length)fill($(id),...agents.map((a,j)=>el('option',{value:a.id,selected:j===i},a.name)));
  $('guess-box').hidden=!(g.status==='playing'&&g.myTurn);
  fill($('status'),g.status==='idle'?el('span',{class:'muted'},'挑战一个 agent，或者让两个 agent 对战。')
    :g.status==='playing'?el('span',{},'轮到 ',el('strong',{},g.turn),g.thinking?' · 正在想…':'')
    :el('span',{class:'win'},g.winner?g.winner+' 赢了！':'平局：都没猜中'));
  fill($('sides'),...g.sides.map(x=>el('div',{class:'card side'},el('h2',{style:'margin-top:0'},x.name+' 猜'),
    x.secret?el('div',{class:'muted'},'自己的数字：',el('span',{class:'secret'},x.secret)):null,
    ...x.guesses.map((r,i)=>el('div',{class:'log'+(r.result==='4A0B'?' win':'')},(i+1)+'. '+r.guess+'  →  '+r.result)),
    x.note?el('div',{class:'muted'},x.note):null)));
}
watch(render);`
});

function setup(app) {
  // sides: {id, name, agent, secret, guesses: [{guess, result}], note}
  const game = { round: 0, status: 'idle', sides: [], turn: 0, winner: '', thinking: false };
  app.page(PAGE);

  app.state((member) => ({
    status: game.status, winner: game.winner, thinking: game.thinking, turn: game.sides[game.turn]?.name || '',
    myTurn: game.sides[game.turn]?.id === member.id && !game.sides[game.turn]?.agent,
    // A secret shows only to its own player, and to everyone when the game is over.
    sides: game.sides.map((x) => ({ name: x.name, guesses: x.guesses, note: x.note, secret: game.status === 'over' || (!x.agent && x.id === member.id) ? x.secret : '' })),
  }));

  function begin(sides) {
    Object.assign(game, { round: game.round + 1, status: 'playing', sides, turn: 0, winner: '', thinking: false });
    app.changed();
    void start(game.round);
  }

  async function secretOf(side) {
    try {
      const reply = await app.askJSON(side.id, `你在空间「${app.title}」玩猜数字对决。想一个四位数，四个数字互不相同（可以以 0 开头），不要告诉任何人。\n格式：{"secret":"四位数"}`);
      if (valid(String(reply?.secret))) return String(reply.secret);
    } catch { /* below */ }
    side.note = '没想好数字，系统替它选了一个';
    return randomSecret();
  }

  app.route('POST', '/challenge', (member, body) => {
    if (game.status === 'playing') throw refuse('正在对决');
    const secret = String(body.secret || '');
    if (!valid(secret)) throw refuse('秘密数字要四位、互不相同');
    const rival = app.member(body.rival);
    if (!rival?.agent) throw refuse('选一个 agent 当对手');
    begin([{ id: member.id, name: member.name, agent: false, secret, guesses: [], note: '' }, { id: rival.id, name: rival.name, agent: true, secret: '', guesses: [], note: '' }]);
  });

  app.route('POST', '/duel', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以安排 agent 对战', 403);
    if (game.status === 'playing') throw refuse('正在对决');
    const a = app.member(body.a), b = app.member(body.b);
    if (!a?.agent || !b?.agent || a.id === b.id) throw refuse('选两个不同的 agent');
    begin([a, b].map((x) => ({ id: x.id, name: x.name, agent: true, secret: '', guesses: [], note: '' })));
  });

  /** Records a guess against the other side's secret; ends the game on 4A or when both are out of turns. */
  function record(side, guess) {
    const other = game.sides.find((x) => x !== side);
    const result = score(other.secret, guess);
    side.guesses.push({ guess, result });
    if (result === '4A0B') Object.assign(game, { status: 'over', winner: side.name });
    else if (game.sides.every((x) => x.guesses.length >= TURNS)) Object.assign(game, { status: 'over', winner: '' });
    else game.turn = 1 - game.turn;
    app.changed();
  }

  /** Agents pick their secrets once, then play their turns. */
  async function start(round) {
    game.thinking = true;
    app.changed();
    for (const side of game.sides) if (side.agent) side.secret = await secretOf(side);
    if (game.round !== round) return;
    game.thinking = false;
    app.changed();
    await agentTurns(round);
  }

  /** Plays turns while it is an agent's turn; a person's turn waits for /guess. */
  async function agentTurns(round) {
    const live = () => game.round === round && game.status === 'playing';
    while (live() && game.sides[game.turn].agent) {
      const side = game.sides[game.turn];
      game.thinking = true;
      app.changed();
      const history = side.guesses.map((r) => `${r.guess} → ${r.result}`).join('\n') || '（还没猜过）';
      let guess = '';
      try {
        const reply = await app.askJSON(side.id, `你在空间「${app.title}」玩猜数字对决，正在猜对手的四位数（数字互不相同，可以以 0 开头）。` +
          `结果 xAyB：A 是数字和位置都对，B 是数字对但位置不对。你之前的猜测：\n${history}\n还剩 ${TURNS - side.guesses.length} 次。仔细推理，给出下一次猜测。\n格式：{"guess":"四位数"}`);
        guess = String(reply?.guess || '');
      } catch { /* a wasted turn */ }
      if (!live()) return;
      game.thinking = false;
      if (valid(guess)) record(side, guess);
      else { side.guesses.push({ guess: guess.slice(0, 8) || '----', result: '无效' }); game.turn = 1 - game.turn; app.changed(); }
      if (live() && game.sides.every((x) => x.guesses.length >= TURNS)) { Object.assign(game, { status: 'over', winner: '' }); app.changed(); }
    }
  }

  app.route('POST', '/guess', (member, body) => {
    const side = game.sides[game.turn];
    if (game.status !== 'playing' || game.thinking || side?.id !== member.id || side.agent) throw refuse('还没轮到你', 403);
    const guess = String(body.guess || '');
    if (!valid(guess)) throw refuse('猜一个四位、互不相同的数字');
    record(side, guess);
    void agentTurns(game.round);
  });
}

if (require.main === module) run({ name: '猜数字对决' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '猜数字对决' };
