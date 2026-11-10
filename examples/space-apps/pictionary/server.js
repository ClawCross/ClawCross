#!/usr/bin/env node
/* 你画我猜 · Agent Pictionary — each round one agent draws a secret word as SVG. People guess
 * from the picture; the other agents guess from the SVG source itself. Correct guesses score for
 * the guesser and for the drawing agent.
 * 简介：agent 用 SVG 画出秘密词，人和其他 agent 来猜。
 *
 *   node examples/space-apps/pictionary/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse, safeSvg } = require('../../../sdk/js/space-app.js');

const WORDS = ['长颈鹿', '自行车', '火箭', '雨伞', '灯塔', '冰淇淋', '钢琴', '仙人掌', '帆船', '南瓜灯', '蜗牛', '热气球',
  '企鹅', '吉他', '城堡', '风车', '章鱼', '彩虹', '机器人', '月亮', '蘑菇', '鲸鱼', '咖啡杯', '眼镜', '风筝', '圣诞树'];
const same = (a, b) => String(a || '').replace(/[\s，。！？、,.!?"'“”]/g, '').toLowerCase() === String(b || '').toLowerCase();

const PAGE = page({
  appId: 'pictionary',
  title: '你画我猜',
  style: `.canvas{display:flex;justify-content:center;background:#fff;border-radius:16px;padding:10px}.canvas img{width:min(100%,360px);aspect-ratio:1}
.guess{padding:4px 0}.right{color:var(--good);font-weight:600}.scores span{margin-right:10px}`,
  body: `<h1>你画我猜</h1><p class="muted">agent 轮流作画，大家来猜；其他 agent 会读着 SVG 代码猜。</p><div id="board"></div>`,
  script: `
const {api,watch,act,el,fill,keep,svg}=App,$=id=>document.getElementById(id);
function render(s){
  const g=s.state;
  fill($('board'),
    el('div',{class:'row scores'},...Object.entries(g.scores).sort((a,b)=>b[1]-a[1]).map(([n,p])=>el('span',{class:'chip'},n+' '+p))),
    !g.round?el('p',{class:'muted'},s.owner?'点“下一轮”让第一个 agent 作画。':'等待群主开局。'):el('h2',{},'第 '+g.round+' 轮 · '+g.drawer+(g.status==='drawing'?' 正在作画…':' 的画')),
    g.svg?el('div',{class:'canvas'},svg(g.svg,{alt:'agent 的画'})):null,
    g.status==='reveal'?el('p',{},'答案：',el('strong',{},g.word)):null,
    g.status==='guessing'?el('div',{class:'row'},keep('guess',()=>el('input',{id:'guess',class:'grow',placeholder:'猜一个词'})),el('button',{onclick:()=>act(()=>{const v=$('guess').value;$('guess').value='';return api('guess',{text:v});})},'猜')):null,
    ...g.guesses.map(x=>el('div',{class:'guess'},el('span',{class:x.agent?'agent':''},(x.agent?'🤖 ':'')+x.by+'：'),x.right?el('span',{class:'right'},'猜对了！'):x.text)),
    s.owner&&g.status!=='drawing'?el('button',{style:'margin-top:10px',onclick:()=>act(()=>api('next',{}))},g.status==='guessing'?'揭晓并进入下一轮':'下一轮'):null);
}
watch(render);`
});

function setup(app) {
  const game = { round: 0, drawer: '', drawerId: '', word: '', svg: '', status: 'idle', guesses: [], scores: {}, rotation: 0 };
  app.page(PAGE);
  app.state(() => ({ ...game, word: game.status === 'reveal' ? game.word : '', drawerId: undefined }));
  const score = (name, points) => { game.scores[name] = (game.scores[name] || 0) + points; };

  function judge(by, text, agent) {
    const right = same(text, game.word);
    if (game.guesses.some((x) => x.by === by && x.right)) return;
    game.guesses.push({ by, text: String(text || '').slice(0, 30), right, agent });
    if (right) { score(by, 2); score(game.drawer, 1); }
    app.changed();
  }

  app.route('POST', '/next', (member) => {
    if (!member.owner) throw refuse('只有群主可以开下一轮', 403);
    if (game.status === 'drawing') throw refuse('还在作画');
    const agents = app.agents();
    if (!agents.length) throw refuse('空间里还没有 agent');
    if (game.status === 'guessing') { game.status = 'reveal'; app.changed(); return; }
    const drawer = agents[game.rotation++ % agents.length];
    Object.assign(game, { round: game.round + 1, drawer: drawer.name, drawerId: drawer.id, word: WORDS[Math.floor(Math.random() * WORDS.length)],
      svg: '', status: 'drawing', guesses: [] });
    app.changed();
    const round = game.round;
    app.askJSON(drawer.id, `你在空间「${app.title}」玩你画我猜，这一轮你作画。要画的词是「${game.word}」（保密，别人要猜）。\n` +
      '用一张 SVG 画出来：viewBox="0 0 300 300"，用形状、颜色和简单线条，不能写任何文字或字母，不要用脚本、图片或外部资源，6000 字以内。\n' +
      '格式：{"svg":"<svg ...>…</svg>"}').then((answer) => {
      if (game.round !== round) return;
      game.svg = safeSvg(answer?.svg) || '';
      game.status = 'guessing';
      if (!game.svg) game.guesses.push({ by: drawer.name, text: '没画出能用的图（已跳过）', right: false, agent: true });
      app.changed();
      // The other agents guess from the SVG source.
      if (game.svg) for (const agent of app.agents().filter((a) => a.id !== drawer.id)) {
        app.askJSON(agent.id, `你在空间「${app.title}」玩你画我猜。下面是 ${drawer.name} 画的 SVG，读代码想象画面，猜它画的是什么（一个中文词）。\n${game.svg}\n格式：{"guess":"词语"}`)
          .then((reply) => { if (game.round === round && game.status === 'guessing') judge(agent.name, reply?.guess, true); }).catch(() => {});
      }
    }).catch((error) => { if (game.round === round) { game.status = 'guessing'; game.guesses.push({ by: drawer.name, text: '没画完：' + error.message, right: false, agent: true }); app.changed(); } });
  });

  app.route('POST', '/guess', (member, body) => {
    if (game.status !== 'guessing') throw refuse('现在不能猜');
    if (!String(body.text || '').trim()) throw refuse('写下你的答案');
    judge(member.name, body.text, false);
  });
}

if (require.main === module) run({ name: '你画我猜' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '你画我猜' };
