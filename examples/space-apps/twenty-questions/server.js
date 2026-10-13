#!/usr/bin/env node
/* 二十问对决 · Twenty Questions Duel — two agents each secretly think of something in a category,
 * then take turns asking yes/no questions about the other's, or guessing it. First to guess right
 * wins; each has twenty tries. Each agent's own person sees its secret and may slip it a hint for
 * its next turn; everyone else sees only the questions and answers until the end.
 * 简介：两个 agent 各想一样东西，轮流用是非题猜对方的。
 *
 *   node examples/space-apps/twenty-questions/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const LIMIT = 20;
const CATEGORIES = { 动物: ['长颈鹿', '海豚', '刺猬'], 名人: ['爱因斯坦', '李白', '居里夫人'], 日常物品: ['雨伞', '牙刷', '指甲刀'],
  城市: ['成都', '巴黎', '开罗'], 电影: ['泰坦尼克号', '千与千寻', '星际穿越'] };
const clean = (text, max = 120) => String(text || '').replace(/^["“「]|["”」]$/g, '').trim().slice(0, max);
const same = (a, b) => { const norm = (x) => x.toLowerCase().replace(/[\s\p{P}]/gu, ''); return !!norm(a) && norm(a) === norm(b); };

const PAGE = page({
  title: '二十问对决',
  style: `.log{font-size:14px;padding:3px 0;border-bottom:1px solid var(--line)}.yes{color:var(--good)}.no{color:var(--bad)}
.guess{font-weight:600}.right{color:var(--good)}.banner{font-size:17px;font-weight:600}`,
  body: `<h1>二十问对决</h1><p class="muted">两个 agent 各想一个东西，轮流用是非题猜对方的，猜中就赢，各有 20 次机会。</p>
<div id="setup" class="card" hidden></div>
<div id="secret" class="card" hidden></div>
<div id="hint-box" class="card" hidden><div class="row"><input id="hint" class="grow" placeholder="悄悄提示你的 agent，例如：先问是不是哺乳动物"><button id="send-hint" class="ghost">提示它</button></div><div id="hint-note" class="muted"></div></div>
<div class="row"><p id="status" class="banner grow"></p><button id="stop" class="ghost" hidden>结束对决</button></div><div id="duel" class="grid"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
$('stop').onclick=()=>act(()=>api('stop',{}));
$('send-hint').onclick=()=>act(()=>{const v=$('hint').value;$('hint').value='';return api('hint',{text:v});});
function render(s){
  const d=s.state,agents=s.members.filter(m=>m.agent);
  $('setup').hidden=!(s.owner&&['idle','over'].includes(d.status));
  if(!$('setup').hidden&&!$('setup').childElementCount)fill($('setup'),el('div',{class:'row'},
    ...['a','b'].map((seat,i)=>el('select',{id:'seat-'+seat},...agents.map((a,j)=>el('option',{value:a.id,selected:j===i},a.name)))),
    el('select',{id:'category'},...d.categories.map(c=>el('option',{},c))),
    el('button',{onclick:()=>act(()=>api('start',{a:$('seat-a').value,b:$('seat-b').value,category:$('category').value}))},'开始')));
  const mine=d.seats.find(x=>x.mine);
  $('secret').hidden=!(mine&&mine.secret&&d.status!=='over');
  if(mine)$('secret').textContent='你的 '+mine.name+' 想的是：'+mine.secret+'（只有你看得到）';
  $('hint-box').hidden=!d.coach;
  $('stop').hidden=!(s.owner&&['picking','playing'].includes(d.status));
  $('hint-note').textContent=d.myHint?'待用的提示：'+d.myHint:'';
  $('status').textContent=d.status==='idle'?(s.owner?'选两个 agent 和一个类别。':'等群主开局。')
    :d.status==='picking'?'两个 agent 正在想（'+d.category+'）…'
    :d.status==='playing'?'类别：'+d.category+' · 轮到 '+d.turn
    :d.winner?d.winner+' 猜中了，获胜！':'平局'+(d.reason?'：'+d.reason:'');
  fill($('duel'),...d.seats.map((x,i)=>{const other=d.seats[1-i];return el('div',{class:'card'},
    el('h2',{},x.name+' 猜 '+other.name+' 的'+(d.status==='over'&&other.secret?'「'+other.secret+'」':'')),el('div',{class:'muted'},'用了 '+x.used+'/'+d.limit+' 次'),
    ...x.log.map(l=>l.kind==='guess'?el('div',{class:'log guess'+(l.right?' right':'')},'猜：'+l.text+(l.right?' ✓':' ✗'))
      :el('div',{class:'log'},l.text+' ',el('span',{class:l.answer==='是'?'yes':l.answer==='否'?'no':'muted'},l.answer))));}));
}
watch(render);`
});

function setup(app) {
  const duel = { round: 0, status: 'idle', category: '', turn: '', winner: '', reason: '', seats: [] };   // seat: {id, name, secret, used, log}
  const hints = new Map();   // person -> hint for their agent's next turn
  app.page(PAGE);

  app.state((member) => ({
    status: duel.status, category: duel.category, turn: duel.turn, winner: duel.winner, reason: duel.reason, limit: LIMIT,
    categories: Object.keys(CATEGORIES), coach: app.agentsOf(member.id).length > 0, myHint: hints.get(member.id) || '',
    seats: duel.seats.map((x) => {
      const mine = app.ownerOf(x.id)?.id === member.id;
      return { name: x.name, used: x.used, log: x.log, mine, secret: mine || duel.status === 'over' ? x.secret : '' };
    }),
  }));

  app.route('POST', '/hint', (member, body) => {
    if (!app.agentsOf(member.id).length) throw refuse('你没有 agent 在这里', 403);
    const text = String(body.text || '').trim().slice(0, 120);
    text ? hints.set(member.id, text) : hints.delete(member.id);
    app.changed();
  });

  app.route('POST', '/start', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以开局', 403);
    if (!['idle', 'over'].includes(duel.status)) throw refuse('正在对决');
    const a = app.member(body.a), b = app.member(body.b);
    if (!a?.agent || !b?.agent || a.id === b.id) throw refuse('选两个不同的 agent');
    const category = CATEGORIES[body.category] ? body.category : '动物';
    Object.assign(duel, { round: duel.round + 1, status: 'picking', category, turn: '', winner: '', reason: '',
      seats: [a, b].map((x) => ({ id: x.id, name: x.name, secret: '', used: 0, log: [] })) });
    app.changed();
    void play(duel.round);
  });

  app.route('POST', '/stop', (member) => {
    if (!member.owner || !['picking', 'playing'].includes(duel.status)) throw refuse('现在不能结束', 403);
    Object.assign(duel, { status: 'over', round: duel.round + 1, reason: '群主结束了对决' });
    app.changed();
  });

  async function play(round) {
    const live = () => duel.round === round && ['picking', 'playing'].includes(duel.status);
    await Promise.all(duel.seats.map(async (seat) => {
      try {
        const reply = await app.askJSON(seat.id, `你在空间「${app.title}」玩二十问对决。类别：${duel.category}。在心里选一个大家都熟悉、不太冷门的${duel.category}，` +
          `对手会用是非题来猜它。不要告诉任何人。\n格式：{"secret":"你选的"}`);
        seat.secret = clean(reply?.secret, 30);
      } catch { /* a fallback below */ }
      if (!seat.secret) seat.secret = CATEGORIES[duel.category][Math.floor(Math.random() * 3)];
    }));
    if (!live()) return;
    duel.status = 'playing';
    app.changed();
    for (let turn = 0; live(); turn++) {
      const seat = duel.seats[turn % 2], other = duel.seats[1 - (turn % 2)];
      if (duel.seats.every((x) => x.used >= LIMIT)) { Object.assign(duel, { status: 'over', reason: '双方都用完了机会' }); break; }
      if (seat.used >= LIMIT) continue;
      duel.turn = seat.name;
      app.changed();
      const coach = app.ownerOf(seat.id);
      const hint = coach && hints.get(coach.id);
      if (hint) hints.delete(coach.id);
      const history = seat.log.map((l) => l.kind === 'guess' ? `猜「${l.text}」：不对` : `问：${l.text} 答：${l.answer}`).join('\n') || '（还没有）';
      let move = null;
      try {
        move = await app.askJSON(seat.id, `你在空间「${app.title}」玩二十问对决，正在猜「${other.name}」心里想的${duel.category}。你之前的问答：\n${history}\n` +
          `你还剩 ${LIMIT - seat.used} 次机会。` + (hint ? `${coach.name} 悄悄提示你：${hint}\n` : '') +
          '提出一个能用是/否回答的问题，或者有把握时直接猜。\n格式：{"question":"是非问题"} 或 {"guess":"你的猜测"}');
      } catch { /* counts as a wasted turn */ }
      if (!live()) return;
      seat.used++;
      if (move?.guess) {
        const guess = clean(move.guess, 30);
        let right = same(guess, other.secret);
        if (!right) {
          try {
            right = (await app.askJSON(other.id, `二十问对决：你心里想的是「${other.secret}」，${seat.name} 猜「${guess}」。是不是同一个东西（别名、简称也算，但不同的东西不算）？` +
              '\n格式：{"same":true 或 false}'))?.same === true;
          } catch { /* not judged: wrong */ }
          if (!live()) return;
        }
        seat.log.push({ kind: 'guess', text: guess, right });
        if (right) Object.assign(duel, { status: 'over', winner: seat.name });
      } else {
        const question = clean(move?.question) || '（没有提问）';
        let answer = '不确定';
        if (move?.question) {
          try {
            const reply = await app.askJSON(other.id, `二十问对决：你心里想的是「${other.secret}」。${seat.name} 问：${question}\n如实回答，不要透露答案本身。` +
              '\n格式：{"answer":"是|否|不确定"}');
            if (['是', '否', '不确定'].includes(reply?.answer)) answer = reply.answer;
          } catch { /* 不确定 */ }
          if (!live()) return;
        }
        seat.log.push({ kind: 'question', text: question, answer });
      }
      app.changed();
    }
    app.changed();
  }
}

if (require.main === module) run({ name: '二十问对决' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '二十问对决' };
