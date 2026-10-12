#!/usr/bin/env node
/* 拆弹专家 · Bomb Defusal — one person sees a bomb (wires, a button, a keypad, a serial number…);
 * one agent holds the manual but cannot see the bomb. They talk until it is defused, against the
 * clock; three mistakes and it goes off. Everyone else watches. The server makes the bomb, keeps
 * the manual's answers and checks every move; the agent only ever sees the manual and the talk.
 *
 *   node examples/space-apps/bomb-defusal/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const COLORS = { red: '红', blue: '蓝', yellow: '黄', white: '白', black: '黑' };
const BUTTONS = { blue: '蓝', white: '白', yellow: '黄', red: '红' };
const LABELS = ['中止', '引爆', '按住', '按下'];
const STRIP = { blue: 4, white: 1, yellow: 5, red: 1 };
const COLUMNS = [
  ['☂', '★', '♠', '⚓', '☎', '✈', '♫'], ['☃', '☂', '♫', '✂', '☆', '⚓', '⌛'], ['☯', '✿', '✂', '♛', '☘', '★', '☆'],
  ['⚡', '♣', '⌛', '☘', '☀', '♛', '☃'], ['♥', '☀', '✈', '♦', '⚡', '☯', '♠'], ['✿', '♦', '☎', '♣', '♥', '⚓', '☂']];
const NAMES = { '☂': '雨伞', '★': '实心星', '♠': '黑桃', '⚓': '锚', '☎': '电话', '✈': '飞机', '♫': '音符', '☃': '雪人', '✂': '剪刀', '☆': '空心星',
  '⌛': '沙漏', '☯': '太极', '✿': '花', '♛': '皇冠', '☘': '三叶草', '⚡': '闪电', '♣': '梅花', '☀': '太阳', '♥': '红心', '♦': '方块' };
const MINUTES = [5, 8, 12];

const MANUAL = `【拆弹手册】炸弹有三个模块：线路、按钮、键盘。全部解除就拆除成功；做错一次记一次失误，三次失误就爆炸。
边框上有序列号、电池数量和指示灯（CAR、FRK，亮或灭），规则会用到。

一、线路（3 到 6 根线，从上往下数）只剪一根：
3 根：没有红线→剪第 2 根；否则最后一根是白线→剪最后一根；否则蓝线多于 1 根→剪最后一根蓝线；否则剪最后一根。
4 根：红线多于 1 根且序列号末位是奇数→剪最后一根红线；否则最后一根是黄线且没有红线→剪第 1 根；否则恰好 1 根蓝线→剪第 1 根；否则黄线多于 1 根→剪最后一根；否则剪第 2 根。
5 根：最后一根是黑线且序列号末位是奇数→剪第 4 根；否则恰好 1 根红线且黄线多于 1 根→剪第 1 根；否则没有黑线→剪第 2 根；否则剪第 1 根。
6 根：没有黄线且序列号末位是奇数→剪第 3 根；否则恰好 1 根黄线且白线多于 1 根→剪第 4 根；否则没有红线→剪最后一根；否则剪第 4 根。

二、按钮：依次检查，用第一条符合的：
1. 蓝色且写着「中止」→按住。2. 写着「引爆」且电池多于 1 节→按一下马上松开。3. 白色且 CAR 灯亮→按住。
4. 电池多于 2 节且 FRK 灯亮→按一下马上松开。5. 黄色→按住。6. 红色且写着「按住」→按一下马上松开。7. 其他情况→按住。
按住后按钮旁会亮起一条色带：蓝色→在倒计时出现数字 4 时松开；白色→1；黄色→5；其他颜色→1。

三、键盘：有四个符号。下面六列里只有一列同时包含这四个符号，按它们在那一列中从上到下的顺序依次按下。
${COLUMNS.map((column, i) => `第 ${i + 1} 列：` + column.map((s) => `${s}${NAMES[s]}`).join(' ')).join('\n')}`;

const pick = (list) => list[Math.floor(Math.random() * list.length)];

/** The right moves for a bomb: the wire to cut (0-based), press|hold, the release digit, the key order. */
function solve(bomb) {
  const w = bomb.wires, n = w.length, odd = Number(bomb.serial.at(-1)) % 2 === 1;
  const count = (c) => w.filter((x) => x === c).length, last = (c) => w.lastIndexOf(c);
  let wire;
  if (n === 3) wire = !count('red') ? 1 : w[n - 1] === 'white' ? n - 1 : count('blue') > 1 ? last('blue') : n - 1;
  else if (n === 4) wire = count('red') > 1 && odd ? last('red') : w[n - 1] === 'yellow' && !count('red') ? 0 : count('blue') === 1 ? 0 : count('yellow') > 1 ? n - 1 : 1;
  else if (n === 5) wire = w[n - 1] === 'black' && odd ? 3 : count('red') === 1 && count('yellow') > 1 ? 0 : !count('black') ? 1 : 0;
  else wire = !count('yellow') && odd ? 2 : count('yellow') === 1 && count('white') > 1 ? 3 : !count('red') ? n - 1 : 3;
  const b = bomb.button, lit = (x) => bomb.indicators.some((i) => i.name === x && i.lit);
  const button = [   // the manual's button rules, first match wins
    [b.color === 'blue' && b.label === '中止', 'hold'], [b.label === '引爆' && bomb.batteries > 1, 'press'], [b.color === 'white' && lit('CAR'), 'hold'],
    [bomb.batteries > 2 && lit('FRK'), 'press'], [b.color === 'yellow', 'hold'], [b.color === 'red' && b.label === '按住', 'press'], [true, 'hold'],
  ].find(([when]) => when)[1];
  const column = COLUMNS.find((c) => bomb.keys.every((k) => c.includes(k)));
  return { wire, button, release: STRIP[b.strip], keys: column.filter((k) => bomb.keys.includes(k)) };
}

function makeBomb() {
  const wires = Array.from({ length: 3 + Math.floor(Math.random() * 4) }, () => pick(Object.keys(COLORS)));
  let keys;
  do {
    const column = [...pick(COLUMNS)].sort(() => Math.random() - 0.5);
    keys = column.slice(0, 4);
  } while (COLUMNS.filter((c) => keys.every((k) => c.includes(k))).length !== 1);
  const serial = Array.from({ length: 5 }, () => pick('ABCDEFGHJKLMNPQRSTUVWXZ0123456789'.split(''))).join('') + Math.floor(Math.random() * 10);
  return { wires, serial, batteries: Math.floor(Math.random() * 4), indicators: ['CAR', 'FRK'].map((name) => ({ name, lit: Math.random() < 0.5 })),
    button: { color: pick(Object.keys(BUTTONS)), label: pick(LABELS), strip: pick(Object.keys(STRIP)) }, keys: keys.sort(() => Math.random() - 0.5) };
}

const PAGE = page({
  title: '拆弹专家',
  style: `.bomb{background:#2b2b2e;color:#eee;border-radius:16px;padding:14px;display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(200px,1fr))}
.module{background:#3a3a3e;border-radius:12px;padding:10px}.module.solved{outline:2px solid #3fb950}.module h3{margin:0 0 8px;font-size:13px;color:#bbb}
.wire{display:flex;align-items:center;gap:8px;margin:5px 0}.wire .line{flex:1;height:7px;border-radius:4px}.wire .cut{background:repeating-linear-gradient(90deg,#666 0 10px,transparent 10px 20px)!important}
.wire button,.key,.bigbutton{padding:3px 10px}.key{font-size:24px;width:52px;height:52px;padding:0;border-radius:10px;background:#eee;color:#111;margin:3px}.key.done{background:#3fb950}
.bigbutton{width:96px;height:96px;border-radius:50%;font-weight:700;font-size:15px;color:#111;border:4px solid #222}.strip{height:10px;border-radius:5px;margin-top:6px}
.edge{grid-column:1/-1;display:flex;gap:14px;flex-wrap:wrap;font-size:13px;color:#ccc}.lit{color:#ffd43b}.timer{font:700 26px ui-monospace,monospace;color:#ff6b6b}
.chat{max-height:300px;overflow:auto}.line-say{margin:4px 0}.line-event{color:var(--muted);font-size:13px}.result{font-size:20px;font-weight:700}`,
  body: `<h1>拆弹专家</h1><p class="muted">拆弹员看得到炸弹，没有手册；agent 专家有手册，看不到炸弹。靠对话在时间内拆掉它。</p>
<div id="setup" class="card" hidden></div><div id="head" class="row"></div><div id="bomb"></div>
<div class="card"><div id="chat" class="chat"></div><div id="say-box" class="row" hidden style="margin-top:8px"><input id="say" class="grow" placeholder="告诉专家你看到了什么"><button id="send">发送</button></div></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
const CSS={red:'#e03131',blue:'#1c7ed6',yellow:'#fcc419',white:'#f1f3f5',black:'#111'};
$('send').onclick=()=>act(()=>{const v=$('say').value;$('say').value='';return api('say',{text:v});});
$('say').onkeydown=e=>{if(e.key==='Enter')$('send').click();};
let deadline=0,status='';
setInterval(()=>{const t=$('timer');if(t&&status==='armed'){const s=Math.max(0,Math.round((deadline-Date.now())/1000));t.textContent=Math.floor(s/60)+':'+String(s%60).padStart(2,'0');}},500);
function render(s){
  const g=s.state,b=g.bomb,me=g.defuser===s.me.id&&g.status==='armed';status=g.status;deadline=Date.now()+g.left*1000;
  $('setup').hidden=!(s.owner&&g.status!=='armed');
  if(!$('setup').hidden&&!$('setup').childElementCount)fill($('setup'),el('div',{class:'row'},
    el('label',{},'专家 ',el('select',{id:'expert'},...s.members.filter(m=>m.agent).map(a=>el('option',{value:a.id},a.name)))),
    el('label',{},'拆弹员 ',el('select',{id:'defuser'},...s.members.filter(m=>!m.agent).map(p=>el('option',{value:p.id,selected:p.id===s.me.id},p.name)))),
    el('select',{id:'minutes'},...g.minutes.map(m=>el('option',{value:m},m+' 分钟'))),
    el('button',{onclick:()=>act(()=>api('start',{expert:$('expert').value,defuser:$('defuser').value,minutes:+$('minutes').value}))},'开始')));
  fill($('head'),g.status==='idle'?el('span',{class:'muted'},s.owner?'选专家和拆弹员。':'等群主开局。')
    :[el('span',{class:'grow'},'专家 ',el('span',{class:'agent'},g.expertName),' · 拆弹员 '+g.defuserName+' · 失误 '+'✗'.repeat(g.strikes)+'○'.repeat(3-g.strikes)),
      g.status==='armed'?el('span',{class:'timer',id:'timer'}):el('span',{class:'result'},g.status==='defused'?'💚 炸弹已拆除':'💥 爆炸了'+(g.reason?'（'+g.reason+'）':''))]);
  $('say-box').hidden=!me;
  if(!b){fill($('bomb'));fill($('chat'));return;}
  const act2=(path,body)=>()=>act(()=>api(path,body));
  fill($('bomb'),el('div',{class:'bomb'},
    el('div',{class:'edge'},el('span',{},'序列号 '+b.serial),el('span',{},'电池 '+'🔋'.repeat(b.batteries)+(b.batteries?'':'无')),
      ...b.indicators.map(i=>el('span',{class:i.lit?'lit':''},(i.lit?'● ':'○ ')+i.name))),
    el('div',{class:'module'+(g.solved.wires?' solved':'')},el('h3',{},'线路'),...b.wires.map((c,i)=>el('div',{class:'wire'},el('span',{},i+1),
      el('span',{class:'line'+(g.cut.includes(i)?' cut':''),style:'background:'+CSS[c]}),me&&!g.solved.wires&&!g.cut.includes(i)?el('button',{class:'ghost',onclick:act2('cut',{wire:i})},'剪'):null))),
    el('div',{class:'module'+(g.solved.button?' solved':'')},el('h3',{},'按钮'),el('div',{class:'row'},
      el('button',{class:'bigbutton',style:'background:'+CSS[b.button.color],disabled:!me||!!g.solved.button||!!g.holding,onclick:act2('button',{action:'press'})},b.button.label),
      me&&!g.solved.button&&!g.holding?el('button',{class:'ghost',onclick:act2('button',{action:'hold'})},'按住'):null),
      g.holding?[el('div',{class:'strip',style:'background:'+CSS[g.holding]}),me?el('div',{class:'row'},el('span',{class:'muted'},'倒计时出现数字几时松开？'),
        ...[1,2,3,4,5,6,7,8,9].map(d=>el('button',{class:'ghost',onclick:act2('release',{digit:d})},d))):null]:null),
    el('div',{class:'module'+(g.solved.keys?' solved':'')},el('h3',{},'键盘'),el('div',{},...b.keys.map(k=>el('button',{class:'key'+(g.pressed.includes(k)?' done':''),
      disabled:!me||!!g.solved.keys||g.pressed.includes(k),onclick:act2('key',{key:k})},k))))));
  fill($('chat'),...g.talk.map(t=>t.kind==='event'?el('div',{class:'line-event'},t.text):el('div',{class:'line-say'},el('strong',{class:t.kind==='expert'?'agent':''},t.who+'：'),t.text)),
    g.thinking?el('div',{class:'muted'},g.expertName+' 正在翻手册…'):null);
  $('chat').scrollTop=$('chat').scrollHeight;
}
watch(render);`
});

function setup(app) {
  const game = { round: 0, status: 'idle', bomb: null, answer: null, expert: '', expertName: '', defuser: '', defuserName: '', deadline: 0,
    strikes: 0, cut: [], pressed: [], holding: '', solved: {}, talk: [], thinking: false, reason: '' };
  app.page(PAGE);

  app.state(() => ({
    status: game.status, minutes: MINUTES, expertName: game.expertName, defuser: game.defuser, defuserName: game.defuserName,
    left: Math.max(0, Math.round((game.deadline - Date.now()) / 1000)), strikes: game.strikes, cut: game.cut, pressed: game.pressed,
    holding: game.holding, solved: game.solved, talk: game.talk.slice(-60), thinking: game.thinking, reason: game.reason,
    bomb: game.bomb && { ...game.bomb, button: { color: game.bomb.button.color, label: game.bomb.button.label } },
  }));

  const event = (text) => game.talk.push({ kind: 'event', text });
  function end(status, reason = '') {
    Object.assign(game, { status, reason, holding: '' });
    event(status === 'defused' ? '炸弹已拆除！' : '💥 ' + reason);
  }
  /** Every move goes through here: the clock first, then the move, then strikes and the win. */
  function move(member, apply) {
    if (game.status !== 'armed' || member.id !== game.defuser) throw refuse('只有拆弹员能动炸弹', 403);
    if (Date.now() > game.deadline) { end('exploded', '时间到了'); app.changed(); return; }
    const ok = apply();
    if (!ok && ++game.strikes >= 3) end('exploded', '三次失误');
    else if (game.solved.wires && game.solved.button && game.solved.keys) end('defused');
    app.changed();
  }

  app.route('POST', '/start', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以开局', 403);
    if (game.status === 'armed') throw refuse('炸弹还在倒计时');
    const expert = app.member(body.expert), defuser = app.member(body.defuser);
    if (!expert?.agent) throw refuse('选一个 agent 当专家');
    if (!defuser || defuser.agent) throw refuse('选一个人当拆弹员');
    const bomb = makeBomb();
    const minutes = MINUTES.includes(body.minutes) ? body.minutes : 8;
    Object.assign(game, { round: game.round + 1, status: 'armed', bomb, answer: solve(bomb), expert: expert.id, expertName: expert.name,
      defuser: defuser.id, defuserName: defuser.name, deadline: Date.now() + minutes * 60_000, strikes: 0, cut: [], pressed: [], holding: '',
      solved: {}, talk: [], thinking: false, reason: '' });
    event(`炸弹已启动，${minutes} 分钟。${defuser.name} 描述炸弹，${expert.name} 查手册。`);
    const round = game.round;
    setTimeout(() => { if (game.round === round && game.status === 'armed') { end('exploded', '时间到了'); app.changed(); } }, minutes * 60_000 + 500).unref();
    app.changed();
  });

  app.route('POST', '/say', (member, body) => {
    const text = String(body.text || '').trim().slice(0, 300);
    if (game.status !== 'armed' || member.id !== game.defuser) throw refuse('只有拆弹员能和专家说话', 403);
    if (!text) throw refuse('说点什么');
    game.talk.push({ kind: 'defuser', who: member.name, text });
    game.thinking = true;
    app.changed();
    const round = game.round;
    const history = game.talk.slice(-30).map((t) => t.kind === 'event' ? `（${t.text}）` : `${t.who}：${t.text}`).join('\n');
    app.ask(game.expert, `你是空间「${app.title}」里的拆弹专家，正在指导 ${member.name} 拆弹。你手里只有手册，看不到炸弹；${member.name} 看得到炸弹，但没有手册。` +
      `问清楚你需要的信息（线的颜色和顺序、序列号末位、电池数、指示灯、按钮颜色和文字、色带颜色、键盘符号），再给出明确的操作。` +
      `回答简短（100 字以内），一次只说一两件事，直接说话，不要格式。\n\n${MANUAL}\n\n到目前为止：\n${history}`)
      .then((reply) => { if (game.round === round) game.talk.push({ kind: 'expert', who: game.expertName, text: String(reply || '').trim().slice(0, 600) }); })
      .catch((error) => { if (game.round === round) event('专家没有回应：' + error.message); })
      .finally(() => { if (game.round === round) { game.thinking = false; app.changed(); } });
  });

  app.route('POST', '/cut', (member, body) => move(member, () => {
    const wire = Number(body.wire);
    if (game.solved.wires || game.cut.includes(wire) || !(wire >= 0 && wire < game.bomb.wires.length)) throw refuse('这根线不能剪');
    game.cut.push(wire);
    const ok = wire === game.answer.wire;
    if (ok) game.solved.wires = true;
    event(`${member.name} 剪断了第 ${wire + 1} 根线（${COLORS[game.bomb.wires[wire]]}）：${ok ? '线路解除' : '错了，记一次失误'}`);
    return ok;
  }));

  app.route('POST', '/button', (member, body) => move(member, () => {
    if (game.solved.button || game.holding || !['press', 'hold'].includes(body.action)) throw refuse('现在不能按');
    if (body.action === 'hold') {
      game.holding = game.bomb.button.strip;
      event(`${member.name} 按住了按钮，亮起${BUTTONS[game.holding]}色色带`);
      return true;
    }
    const ok = game.answer.button === 'press';
    if (ok) game.solved.button = true;
    event(`${member.name} 按了一下按钮：${ok ? '按钮解除' : '错了，记一次失误'}`);
    return ok;
  }));

  app.route('POST', '/release', (member, body) => move(member, () => {
    if (!game.holding) throw refuse('没有按住按钮');
    const ok = game.answer.button === 'hold' && Number(body.digit) === game.answer.release;
    game.holding = '';
    if (ok) game.solved.button = true;
    event(`${member.name} 在数字 ${Number(body.digit)} 时松开：${ok ? '按钮解除' : '错了，记一次失误'}`);
    return ok;
  }));

  app.route('POST', '/key', (member, body) => move(member, () => {
    if (game.solved.keys || !game.bomb.keys.includes(body.key) || game.pressed.includes(body.key)) throw refuse('这个键不能按');
    const ok = body.key === game.answer.keys[game.pressed.length];
    if (ok) game.pressed.push(body.key);
    if (game.pressed.length === 4) game.solved.keys = true;
    event(`${member.name} 按下 ${body.key}${NAMES[body.key]}：${ok ? (game.solved.keys ? '键盘解除' : '对') : '错了，记一次失误'}`);
    return ok;
  }));
}

if (require.main === module) run({ name: '拆弹专家' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '拆弹专家', solve };
