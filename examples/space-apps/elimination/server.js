#!/usr/bin/env node
/* 百人淘汰赛 · Elimination Trivia — a live quiz show for a crowd. A host agent writes the questions;
 * each one is open for a few seconds; everyone still in — people and the other agents — taps an
 * answer; a wrong or missing answer knocks you out. The last ones standing win. Each answer is one
 * tap and the server only counts, so it scales to a big space.
 *
 *   node examples/space-apps/elimination/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const SECONDS = [30, 15, 10];   // real agents often need 10–30 s, so the default is the longest
const COUNTS = [5, 8, 12];
const BREAK_MS = 6000;
const AGENTS = 8;

const PAGE = page({
  title: '百人淘汰赛',
  style: `.question{font-size:20px;font-weight:600;margin:10px 0}.option{display:block;width:100%;text-align:left;margin:6px 0;padding:11px 14px;position:relative;overflow:hidden}
.option .bar{position:absolute;inset:0 auto 0 0;background:rgba(127,127,127,.18)}.option span{position:relative}.option.right{outline:2px solid var(--good)}.option.mine{font-weight:700}
.clock{font:700 28px ui-monospace,monospace}.out{color:var(--bad);font-weight:600}.alive{color:var(--good);font-weight:600}`,
  body: `<h1>百人淘汰赛</h1><p class="muted">限时答题，答错或没答就出局，人和 agent 同场，坚持到最后的赢。</p>
<div id="setup" class="card" hidden><div class="row"><label>出题 <select id="host"></select></label><input id="topic" class="grow" placeholder="题目范围，例如：中国地理、电影、编程"></div>
<div class="row" style="margin-top:8px"><select id="count"></select><select id="seconds"></select><button id="begin">开赛</button></div></div>
<div id="stage"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
$('begin').onclick=()=>act(()=>api('begin',{host:$('host').value,topic:$('topic').value,count:+$('count').value,seconds:+$('seconds').value}));
let closes=0;
setInterval(()=>{const c=$('clock');if(c)c.textContent=Math.max(0,Math.ceil((closes-Date.now())/1000));},250);
function render(s){
  const g=s.state,q=g.question;
  $('setup').hidden=!(s.owner&&['idle','over'].includes(g.status));
  if($('host').options.length!==s.members.filter(m=>m.agent).length)fill($('host'),...s.members.filter(m=>m.agent).map(a=>el('option',{value:a.id},a.name)));
  if(!$('count').options.length){fill($('count'),...g.counts.map(n=>el('option',{value:n},n+' 题')));fill($('seconds'),...g.secondsList.map(n=>el('option',{value:n},'每题 '+n+' 秒')));}
  closes=Date.now()+g.left*1000;
  const head=el('div',{class:'row'},el('span',{class:'grow'},g.status==='idle'?'':'存活 '+g.alive+' / '+g.players),
    g.me==='out'?el('span',{class:'out'},'你被淘汰了，继续围观'):g.me==='alive'?el('span',{class:'alive'},'你还在场'):null);
  if(g.status==='idle'||g.status==='writing'){fill($('stage'),el('p',{class:'muted'},g.status==='writing'?g.host+' 正在出题…':g.error||(s.owner?'选出题 agent 和范围，开赛。':'等群主开赛。')));return;}
  if(g.status==='over'){fill($('stage'),head,el('h2',{},'比赛结束'),el('p',{},g.winners.length?'幸存者：'+g.winners.join('、'):'全军覆没'));return;}
  const total=q.counts?q.counts.reduce((a,b)=>a+b,0)||1:0;
  fill($('stage'),head,el('div',{class:'row'},el('span',{class:'muted grow'},'第 '+(g.index+1)+' / '+g.total+' 题 · '+g.topic),g.status==='open'?el('span',{class:'clock',id:'clock'}):null),
    el('div',{class:'question'},q.text),
    ...q.options.map((o,i)=>el('button',{class:'option ghost'+(q.answer===i?' right':'')+(g.mine===i?' mine':''),disabled:g.status!=='open'||g.me!=='alive'||g.mine!==-1,
      onclick:()=>act(()=>api('answer',{choice:i}))},q.counts?el('div',{class:'bar',style:'width:'+Math.round(q.counts[i]/total*100)+'%'}):null,el('span',{},'ABC'[i]+'. '+o+(q.counts?' · '+q.counts[i]:'')))),
    g.status==='open'?el('p',{class:'muted'},g.answered+' 人已作答'):el('p',{class:'muted'},'这一题淘汰 '+g.dropped+' 人'+(s.owner?'':'，下一题马上开始')),
    g.status==='reveal'&&s.owner?el('button',{onclick:()=>act(()=>api('next',{}))},'下一题'):null);
}
watch(render);`
});

function setup(app) {
  // players: id -> {name, agent, out}; answers: id -> choice for the open question
  const show = { round: 0, status: 'idle', host: '', hostId: '', topic: '', error: '', questions: [], index: 0, closes: 0, seconds: 15,
    players: new Map(), answers: new Map(), dropped: 0, timer: null };
  app.page(PAGE);

  const alive = () => [...show.players].filter(([, p]) => !p.out);
  app.state((member) => {
    const q = show.questions[show.index], revealed = show.status === 'reveal';
    const player = show.players.get(member.id);
    return {
      status: show.status, host: show.host, topic: show.topic, error: show.error, counts: COUNTS, secondsList: SECONDS,
      index: show.index, total: show.questions.length, players: show.players.size, alive: alive().length, answered: show.answers.size, dropped: show.dropped,
      left: show.status === 'open' ? Math.max(0, (show.closes - Date.now()) / 1000) : 0,
      me: player ? (player.out ? 'out' : 'alive') : show.status === 'idle' ? '' : 'out',
      mine: show.answers.has(member.id) ? show.answers.get(member.id) : -1,
      winners: show.status === 'over' ? alive().map(([, p]) => p.name).slice(0, 100) : [],
      // The answer and the split appear only at the reveal.
      question: q && ['open', 'reveal'].includes(show.status) && { text: q.text, options: q.options, answer: revealed ? q.answer : -1,
        counts: revealed ? q.options.map((o, i) => [...show.answers.values()].filter((c) => c === i).length) : null },
    };
  });

  const later = (fn, ms) => { clearTimeout(show.timer); show.timer = setTimeout(fn, ms); show.timer.unref?.(); };

  app.route('POST', '/begin', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以开赛', 403);
    if (!['idle', 'over'].includes(show.status)) throw refuse('比赛正在进行');
    const host = app.member(body.host);
    if (!host?.agent) throw refuse('选一个 agent 出题');
    const topic = String(body.topic || '').trim().slice(0, 40) || '综合知识';
    const count = COUNTS.includes(body.count) ? body.count : COUNTS[0];
    Object.assign(show, { round: show.round + 1, status: 'writing', host: host.name, hostId: host.id, topic, error: '', questions: [], index: 0,
      seconds: SECONDS.includes(body.seconds) ? body.seconds : SECONDS[0], players: new Map(), answers: new Map(), dropped: 0 });
    app.changed();
    const round = show.round;
    app.askJSON(host.id, `你在空间「${app.title}」为直播淘汰赛出题。范围：${topic}。出 ${count} 道单选题，每题 3 个选项、只有一个正确答案，` +
      `由易到难，几秒内能答出、但不能太显然，答案必须确定无争议。\n格式：{"questions":[{"q":"题目","options":["A","B","C"],"answer":正确选项序号0到2}]}`)
      .then((reply) => {
        if (show.round !== round) return;
        const questions = (Array.isArray(reply?.questions) ? reply.questions : []).map((x) => ({ text: String(x?.q || '').slice(0, 200),
          options: Array.isArray(x?.options) ? x.options.slice(0, 3).map((o) => String(o).slice(0, 60)) : [], answer: x?.answer }))
          .filter((x) => x.text && x.options.length === 3 && [0, 1, 2].includes(x.answer)).slice(0, count);
        if (!questions.length) throw new Error('题目格式不对');
        show.questions = questions;
        // Everyone here now plays: people, and every agent except the host.
        for (const m of app.members()) if (m.id !== host.id && (!m.agent || m.online !== false)) show.players.set(m.id, { name: m.name, agent: !!m.agent, out: false });
        open(round);
      })
      .catch((error) => { if (show.round === round) Object.assign(show, { status: 'idle', error: '没出成题：' + error.message }); app.changed(); });
  });

  function open(round) {
    const q = show.questions[show.index];
    Object.assign(show, { status: 'open', closes: Date.now() + show.seconds * 1000, answers: new Map(), dropped: 0 });
    later(() => reveal(round), show.seconds * 1000);
    app.changed();
    for (const [id, p] of alive().filter(([, p]) => p.agent).slice(0, AGENTS)) {
      const index = show.index;
      app.askJSON(id, `你在空间「${app.title}」参加直播淘汰赛，答错就出局。题目：${q.text}\n` + q.options.map((o, i) => `${i}. ${o}`).join('\n') +
        '\n马上回答。格式：{"answer":选项序号}', { timeout: show.seconds * 1000 })
        .then((reply) => { if (show.round === round && show.index === index && show.status === 'open' && [0, 1, 2].includes(reply?.answer)) answer(id, reply.answer); })
        .catch(() => {});
    }
  }

  function answer(id, choice) {
    if (show.answers.has(id)) return;
    show.answers.set(id, choice);
    // Everyone still in has answered: reveal now.
    if (alive().every(([pid]) => show.answers.has(pid))) reveal(show.round);
    else app.changed();
  }

  function reveal(round) {
    if (show.round !== round || show.status !== 'open') return;
    const q = show.questions[show.index];
    for (const [id, p] of alive()) if (show.answers.get(id) !== q.answer) { p.out = true; show.dropped++; }
    show.status = 'reveal';
    later(() => next(round), BREAK_MS);
    app.changed();
  }

  function next(round) {
    if (show.round !== round || show.status !== 'reveal') return;
    if (show.index + 1 >= show.questions.length || !alive().length) { show.status = 'over'; clearTimeout(show.timer); app.changed(); return; }
    show.index++;
    open(round);
  }

  app.route('POST', '/answer', (member, body) => {
    const player = show.players.get(member.id);
    if (show.status !== 'open' || !player || player.out) throw refuse('现在不能作答');
    if (![0, 1, 2].includes(body.choice)) throw refuse('选一个');
    if (show.answers.has(member.id)) throw refuse('已经答过了');
    answer(member.id, body.choice);
  });

  app.route('POST', '/next', (member) => {
    if (!member.owner || show.status !== 'reveal') throw refuse('现在不能跳到下一题', 403);
    next(show.round);
  });
}

if (require.main === module) run({ name: '百人淘汰赛' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '百人淘汰赛' };
