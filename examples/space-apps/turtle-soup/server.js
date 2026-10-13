#!/usr/bin/env node
/* 海龟汤 · Lateral-thinking Soup — one agent hosts: it invents a puzzle and keeps the truth to
 * itself. People ask yes/no questions, or let their own agent think up a question for them, and
 * finally guess what really happened; the host judges.
 * 简介：agent 出题主持，大家用是非题追问真相。
 *
 *   node examples/space-apps/turtle-soup/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const PAGE = page({
  title: '海龟汤',
  style: `.surface{font-size:17px;line-height:1.7}.qa{padding:6px 0;border-bottom:1px solid var(--line)}.ans{font-weight:700;margin-left:6px}
.ans.是{color:var(--good)}.ans.否{color:var(--bad)}.ans.无关{color:var(--muted)}.ans.接近了{color:#e8590c}`,
  body: `<h1>海龟汤</h1><div id="setup" class="card"></div><div id="soup"></div>`,
  script: `
const {api,watch,act,el,fill,keep}=App,$=id=>document.getElementById(id);
function render(s){
  const g=s.state.game,agents=s.members.filter(m=>m.agent);
  $('setup').hidden=!(s.owner&&(!g||g.status!=='playing'));
  if(!$('setup').hidden&&!$('setup').childElementCount)fill($('setup'),el('div',{class:'row'},el('label',{},'主持 agent ',el('select',{id:'host'},...agents.map(a=>el('option',{value:a.id},a.name)))),
    el('select',{id:'theme'},...['悬疑','温情','搞笑','恐怖（不血腥）'].map(t=>el('option',{},t))),el('button',{onclick:()=>act(()=>api('brew',{host:$('host').value,theme:$('theme').value}))},'熬一锅汤')));
  if(!g){fill($('soup'),el('p',{class:'muted'},s.owner?'选一个 agent 当主持人。':'等待群主请出主持人。'));return;}
  if(g.status==='brewing'){fill($('soup'),el('p',{class:'muted'},g.host.name+' 正在熬汤…'));return;}
  const helper=s.state.helpers[s.me.id];
  fill($('soup'),el('h2',{},g.title),el('div',{class:'card surface pre'},g.surface),
    ...g.log.map(x=>el('div',{class:'qa'},el('span',{class:'muted'},x.by+'：'),x.text,x.answer?el('span',{class:'ans '+x.answer},x.answer):el('span',{class:'muted'},' …'),x.note?el('div',{class:'muted'},x.note):null)),
    g.status==='playing'?el('div',{class:'card'},
      el('div',{class:'row'},keep('q',()=>el('input',{id:'q',class:'grow',placeholder:'问一个能用“是/否”回答的问题'})),el('button',{onclick:()=>act(()=>{const v=$('q').value;$('q').value='';return api('ask',{text:v});})},'提问'),
        helper?el('button',{class:'ghost',onclick:()=>act(()=>api('help',{}))},'让 '+helper+' 帮我问'):null),
      el('div',{class:'row',style:'margin-top:8px'},keep('guess',()=>el('input',{id:'guess',class:'grow',placeholder:'我知道了！真相是……'})),el('button',{class:'ghost',onclick:()=>act(()=>{const v=$('guess').value;$('guess').value='';return api('guess',{text:v});})},'揭晓我的推理')),
      s.owner?el('button',{class:'ghost',style:'margin-top:8px',onclick:()=>act(()=>api('reveal',{}))},'公布汤底'):null):null,
    g.bottom?el('div',{class:'card'},el('strong',{},g.winner?g.winner+' 猜中了！汤底：':'汤底：'),el('div',{class:'pre'},g.bottom)):null);
}
watch(render);`
});

function setup(app) {
  let game = null;
  app.page(PAGE);
  app.state(() => ({
    game: game && { ...game, host: { name: game.host.name }, bottom: game.status === 'over' ? game.bottom : '' },
    // Who may ask their own agent for a question: the agent on their device that isn't hosting.
    helpers: Object.fromEntries(app.humans().map((h) => [h.id, app.agentsOf(h.id).find((a) => a.id !== game?.host.id)?.name || '']))
  }));

  const playing = () => { if (game?.status !== 'playing') throw refuse('现在没有在进行的汤'); };
  const context = () => `汤面：${game.surface}\n汤底（只有主持人知道）：${game.bottom}\n已经问过：\n` +
    (game.log.filter((x) => x.answer).map((x) => `- ${x.text} → ${x.answer}`).join('\n') || '（无）');

  /** The host answers one question; questions queue at the host. */
  async function answer(entry) {
    try {
      const reply = await app.askJSON(game.host.id, `你是空间「${app.title}」海龟汤的主持人「${game.host.name}」。\n${context()}\n` +
        `${entry.by} 问：${entry.text}\n只能回答 是、否、无关 或 接近了（方向对但不完全）。note 可选，不能泄露汤底。\n格式：{"answer":"是|否|无关|接近了","note":""}`);
      entry.answer = ['是', '否', '无关', '接近了'].includes(reply?.answer) ? reply.answer : '无关';
      entry.note = String(reply?.note || '').slice(0, 120);
    } catch (error) { entry.answer = '无关'; entry.note = '主持人没回应：' + error.message; }
    app.changed();
  }

  app.route('POST', '/brew', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以开汤', 403);
    const host = app.member(body.host);
    if (!host?.agent) throw refuse('选一个 agent 当主持人');
    game = { host: { id: host.id, name: host.name }, status: 'brewing', title: '', surface: '', bottom: '', log: [], winner: '' };
    app.changed();
    const theme = String(body.theme || '悬疑').slice(0, 20);
    app.askJSON(host.id, `你是空间「${app.title}」海龟汤的主持人「${host.name}」。原创一道${theme}风格的海龟汤：汤面是一段离奇但合理的情境（80 字内），` +
      '汤底是完整真相（200 字内），玩家要通过是非题推理出来。\n格式：{"title":"标题","surface":"汤面","bottom":"汤底"}').then((soup) => {
      if (!soup?.surface || !soup?.bottom) throw new Error('主持人没给出完整的汤');
      Object.assign(game, { status: 'playing', title: String(soup.title || '海龟汤').slice(0, 40), surface: String(soup.surface).slice(0, 400), bottom: String(soup.bottom).slice(0, 1200) });
    }).catch((error) => { game = null; console.error(error.message); }).finally(() => app.changed());
  });

  app.route('POST', '/ask', (member, body) => {
    playing();
    const text = String(body.text || '').trim().slice(0, 200);
    if (!text) throw refuse('写下你的问题');
    const entry = { by: member.name, text, answer: '' };
    game.log.push(entry);
    app.changed();
    void answer(entry);
  });

  // Routes return at once; agents answer in the background and the page follows the state.
  app.route('POST', '/help', (member) => {
    playing();
    const helper = app.agentsOf(member.id).find((a) => a.id !== game.host.id);
    if (!helper) throw refuse('你在这台设备上没有可以帮忙的 agent');
    const entry = { by: `${helper.name}（替 ${member.name}）`, text: '正在想问题…', answer: '' };
    game.log.push(entry);
    app.changed();
    app.askJSON(helper.id, `你在帮 ${member.name} 玩海龟汤。汤面：${game.surface}\n已经问过：\n` +
      (game.log.filter((x) => x.answer).map((x) => `- ${x.text} → ${x.answer}`).join('\n') || '（无）') +
      '\n想一个最能缩小范围的是非题。格式：{"question":"问题"}').then((proposal) => {
      entry.text = String(proposal?.question || '').trim().slice(0, 200);
      if (!entry.text) throw new Error(helper.name + ' 没想出问题');
      app.changed();
      return answer(entry);
    }).catch((error) => { entry.text = '（没想出来）'; entry.answer = '无关'; entry.note = error.message; app.changed(); });
  });

  app.route('POST', '/guess', (member, body) => {
    playing();
    const text = String(body.text || '').trim().slice(0, 500);
    if (!text) throw refuse('写下你的推理');
    const entry = { by: member.name, text: '【推理】' + text, answer: '' };
    game.log.push(entry);
    app.changed();
    app.askJSON(game.host.id, `你是海龟汤主持人「${game.host.name}」。${context()}\n${member.name} 给出推理：${text}\n` +
      '核心真相都对了才算猜中。格式：{"correct":true或false,"comment":"一句点评，不要泄露没猜到的部分"}').then((verdict) => {
      entry.answer = verdict?.correct === true ? '是' : '否';
      entry.note = String(verdict?.comment || '').slice(0, 200);
      if (verdict?.correct === true && game.status === 'playing') Object.assign(game, { status: 'over', winner: member.name });
    }).catch((error) => { entry.answer = '无关'; entry.note = '主持人没回应：' + error.message; }).finally(() => app.changed());
  });

  app.route('POST', '/reveal', (member) => {
    if (!member.owner) throw refuse('只有群主可以公布汤底', 403);
    playing();
    game.status = 'over';
    app.changed();
  });
}

if (require.main === module) run({ name: '海龟汤' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '海龟汤' };
