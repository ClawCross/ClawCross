#!/usr/bin/env node
/* 外语陪练 · Language Partner — practise a language in a scene. One agent plays the other side
 * (the barista, the landlord…) and speaks only the target language; another agent is your tutor and
 * quietly corrects each of your lines. At the end the tutor sums up mistakes and words to keep.
 * Every person's practice is their own: nobody else sees it.
 * 简介：一个 agent 扮演场景里的对方，另一个 agent 逐句纠错。
 *
 *   node examples/space-apps/language-partner/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const LANGUAGES = ['English', '日本語', 'Français', 'Español', '한국어', 'Deutsch'];
const LEVELS = ['入门', '中级', '高级'];
const SCENES = ['咖啡店点单', '机场值机', '租房看房', '面试自我介绍', '向邻居借东西', '自由聊天'];
const TURNS = 30;
const text = (x, max) => String(x || '').trim().slice(0, max);

const PAGE = page({
  title: '外语陪练',
  style: `.line{margin:8px 0;max-width:85%}.line.me{margin-left:auto;text-align:right}.bubble{display:inline-block;padding:7px 12px;border-radius:16px;background:var(--card);text-align:left}
.me .bubble{background:var(--accent);color:var(--on-accent)}.note{font-size:13px;margin-top:3px}.note.ok{color:var(--good)}.note.fix{color:#e8590c}`,
  body: `<h1>外语陪练</h1><p class="muted">一个 agent 扮演场景里的对方，另一个 agent 当老师逐句纠错。你的练习只有你自己看得到。</p>
<div id="setup" class="card" hidden><div class="row"><select id="language"></select><select id="level"></select><select id="scene" class="grow"></select></div>
<div class="row" style="margin-top:8px"><label>对方 <select id="partner"></select></label><label>老师 <select id="tutor"></select></label><button id="start">开始</button></div></div>
<div id="head"></div><div id="chat"></div>
<div id="say-box" class="card" hidden><div class="row"><input id="say" class="grow" placeholder="用外语回答"><button id="send">说</button><button id="end" class="ghost">结束并总结</button></div></div>
<div id="summary"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
$('start').onclick=()=>act(()=>api('start',{language:$('language').value,level:$('level').value,scene:$('scene').value,partner:$('partner').value,tutor:$('tutor').value}));
$('send').onclick=()=>act(()=>{const v=$('say').value;$('say').value='';return api('say',{text:v});});
$('say').onkeydown=e=>{if(e.key==='Enter'&&!e.isComposing)$('send').click();};
$('end').onclick=()=>act(()=>api('end',{}));
function options(id,list){if($(id).options.length!==list.length)fill($(id),...list.map(([v,t])=>el('option',{value:v},t)));}
function render(s){
  const p=s.state,x=p.session,agents=s.members.filter(m=>m.agent).map(a=>[a.id,a.name]);
  options('language',p.languages.map(v=>[v,v]));options('level',p.levels.map(v=>[v,v]));options('scene',p.scenes.map(v=>[v,v]));
  options('partner',agents);options('tutor',agents.slice().reverse());
  const live=x&&x.status!=='done';
  $('setup').hidden=!!live;$('say-box').hidden=!live;
  fill($('head'),x?el('div',{class:'muted'},x.language+' · '+x.level+' · '+x.scene+' · 对方 ',el('span',{class:'agent'},x.partner),' · 老师 ',el('span',{class:'agent'},x.tutor)):null);
  fill($('chat'),...(x?x.lines:[]).map(l=>el('div',{class:'line'+(l.me?' me':'')},el('div',{class:'bubble pre'},l.text||'…'),
    l.me?(l.note?el('div',{class:'note '+(l.note.ok?'ok':'fix')},l.note.ok?'✓ '+(l.note.why||'很好'):'✎ '+l.note.better+(l.note.why?' · '+l.note.why:'')):el('div',{class:'note muted'},'老师在看…')):null)));
  fill($('summary'),x&&x.status==='done'?el('div',{class:'card'},el('h2',{style:'margin-top:0'},'这次练习'),x.summary?[
    x.summary.mistakes.length?[el('strong',{},'常见错误'),el('ul',{},...x.summary.mistakes.map(m=>el('li',{},m)))]:null,
    x.summary.words.length?[el('strong',{},'记住这些'),el('ul',{},...x.summary.words.map(m=>el('li',{},m)))]:null,
    x.summary.next?el('p',{},'下次：'+x.summary.next):null]:el('p',{class:'muted'},'老师正在总结…')):null);
}
watch(render);`
});

function setup(app) {
  const sessions = new Map();   // person -> {language, level, scene, partnerId, partner, tutorId, tutor, status, lines: [{me, text, note}], summary}
  app.page(PAGE);

  app.state((member) => {
    const x = sessions.get(member.id);
    return { languages: LANGUAGES, levels: LEVELS, scenes: SCENES,
      session: x && { ...x, partnerId: undefined, tutorId: undefined, lines: x.lines.slice(-60) } };
  });

  const said = (x) => x.lines.map((l) => (l.me ? '学生：' : '对方：') + l.text).join('\n');

  function reply(x) {
    x.lines.push({ me: false, text: '' });
    const line = x.lines.at(-1);
    return app.ask(x.partnerId, `你在空间「${app.title}」陪人练习 ${x.language}（${x.level}）。场景：${x.scene}。你扮演场景里的对方，只用 ${x.language} 说话，` +
      `每次一两句，按${x.level}水平调整难度，自然地推进对话。不要纠错，另有老师负责。\n` +
      (x.lines.length > 1 ? `到目前为止：\n${said(x)}\n直接说你的下一句。` : '先开场说第一句。'))
      .then((answer) => { line.text = text(answer, 400) || '…'; })
      .catch(() => { line.text = '（对方没有回应）'; })
      .finally(() => app.changed());
  }

  app.route('POST', '/start', (member, body) => {
    const partner = app.member(body.partner), tutor = app.member(body.tutor);
    if (!partner?.agent || !tutor?.agent) throw refuse('选好对方和老师');
    if (member.agent) throw refuse('练习的是人', 403);
    const x = { language: LANGUAGES.includes(body.language) ? body.language : LANGUAGES[0], level: LEVELS.includes(body.level) ? body.level : LEVELS[1],
      scene: SCENES.includes(body.scene) ? body.scene : SCENES[0], partnerId: partner.id, partner: partner.name, tutorId: tutor.id, tutor: tutor.name,
      status: 'talking', lines: [], summary: null };
    sessions.set(member.id, x);
    app.changed();
    void reply(x);
  });

  app.route('POST', '/say', (member, body) => {
    const x = sessions.get(member.id);
    const words = text(body.text, 300);
    if (!x || x.status !== 'talking') throw refuse('先开始一次练习');
    if (!words) throw refuse('说点什么');
    if (x.lines.some((l) => !l.me && !l.text)) throw refuse('等对方说完');
    if (x.lines.filter((l) => l.me).length >= TURNS) throw refuse('这次练得够多了，结束总结一下吧');
    const previous = x.lines.filter((l) => !l.me).at(-1)?.text || '';
    const line = { me: true, text: words, note: null };
    x.lines.push(line);
    app.changed();
    app.askJSON(x.tutorId, `你是 ${x.language} 老师，正在空间「${app.title}」帮一个${x.level}学生练习，场景：${x.scene}。\n对方刚说：「${previous}」\n学生回答：「${words}」\n` +
      '如果有错误或不地道，给出更好的说法和简短中文解释；如果没问题就说好。\n格式：{"ok":true或false,"better":"更好的说法","why":"中文解释，30 字内"}')
      .then((note) => { line.note = { ok: note?.ok === true, better: text(note?.better, 300), why: text(note?.why, 120) }; })
      .catch(() => { line.note = { ok: true, better: '', why: '老师这次没看' }; })
      .finally(() => app.changed());
    void reply(x);
  });

  app.route('POST', '/end', (member) => {
    const x = sessions.get(member.id);
    if (!x || x.status !== 'talking') throw refuse('没有进行中的练习');
    x.status = 'done';
    app.changed();
    const lines = (list, n) => (Array.isArray(list) ? list : []).map((v) => text(v, 160)).filter(Boolean).slice(0, n);
    app.askJSON(x.tutorId, `你是 ${x.language} 老师。总结这次练习（${x.level}，场景：${x.scene}）：\n${said(x)}\n` +
      '指出学生最常见的错误，列出值得记住的词或短语（附中文意思），给一句下次的建议。\n格式：{"mistakes":["错误"],"words":["词或短语 - 意思"],"next":"建议"}')
      .then((s) => { x.summary = { mistakes: lines(s?.mistakes, 5), words: lines(s?.words, 8), next: text(s?.next, 200) }; })
      .catch(() => { x.summary = { mistakes: [], words: [], next: '老师没有总结成，下次再来。' }; })
      .finally(() => app.changed());
  });
}

if (require.main === module) run({ name: '外语陪练' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '外语陪练' };
