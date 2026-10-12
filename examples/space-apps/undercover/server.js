#!/usr/bin/env node
/* 谁是卧底 · Who's the Undercover — people and agents play together. Everyone gets a word; one
 * player's word is slightly different. In turn each describes theirs in one sentence (agents on
 * their own, people by typing), then everyone votes. Find the undercover before it outlasts you.
 *
 *   node examples/space-apps/undercover/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const PAIRS = [['苹果', '梨'], ['咖啡', '奶茶'], ['地铁', '公交'], ['饺子', '包子'], ['钢琴', '吉他'], ['猫', '狐狸'],
  ['雨伞', '雨衣'], ['火锅', '麻辣烫'], ['西瓜', '哈密瓜'], ['牛奶', '豆浆'], ['电影院', '剧院'], ['口罩', '围巾'], ['高铁', '飞机'], ['蜡烛', '台灯']];

const PAGE = page({
  title: '谁是卧底',
  style: `.player{display:inline-flex;gap:4px;align-items:center;padding:4px 10px;border-radius:99px;background:var(--card);margin:3px}
.player.out{opacity:.4;text-decoration:line-through}.player.now{outline:2px solid var(--accent)}.word{font-size:22px;font-weight:700}`,
  body: `<h1>谁是卧底</h1><p class="muted">人和 agent 一起玩：每人一个词，只有卧底的词不一样。轮流描述，再投票。</p><div id="game"></div>`,
  script: `
const {api,watch,act,el,fill,keep}=App,$=id=>document.getElementById(id);
function render(s){
  const g=s.state,me=g.players.find(p=>p.id===s.me.id),speaker=g.players[g.order[g.turn]];
  fill($('game'),
    s.owner&&g.status!=='describing'&&g.status!=='voting'?el('button',{onclick:()=>act(()=>api('start',{}))},g.status==='over'?'再来一局':'开始一局'):null,
    g.status==='idle'?el('p',{class:'muted'},'空间里的人和 agent 都会参加，至少 3 位。'):null,
    me&&g.status!=='idle'?el('div',{class:'card'},'你的词：',el('span',{class:'word'},me.word),me.out?el('span',{class:'muted'},'（你已出局）'):null):null,
    el('div',{},...g.players.map((p,i)=>el('span',{class:'player'+(p.out?' out':'')+(speaker&&speaker.id===p.id&&g.status==='describing'?' now':'')},(p.agent?'🤖 ':'')+p.name,
      g.status==='over'?el('span',{class:'muted'},'（'+p.word+'）'):null,
      g.status==='voting'&&!p.out&&me&&!me.out&&p.id!==me.id?el('button',{class:'ghost',onclick:()=>act(()=>api('vote',{target:p.id}))},g.myVote===p.id?'已投':'投他'):null))),
    g.status==='describing'?el('p',{class:'muted'},'第 '+g.round+' 轮描述 · 轮到 '+(speaker?speaker.name:'')):null,
    g.status==='voting'?el('p',{class:'muted'},'投票中：已投 '+g.voted+' / '+g.players.filter(p=>!p.out).length):null,
    g.status==='describing'&&speaker&&speaker.id===s.me.id?el('div',{class:'row'},keep('say',()=>el('input',{id:'say',class:'grow',placeholder:'用一句话描述你的词，别说出它'})),
      el('button',{onclick:()=>act(()=>{const v=$('say').value;$('say').value='';return api('describe',{text:v});})},'说')):null,
    s.owner&&g.status==='describing'&&speaker&&!speaker.agent&&speaker.id!==s.me.id?el('button',{class:'ghost',onclick:()=>act(()=>api('skip',{}))},'跳过 '+speaker.name):null,
    s.owner&&g.status==='voting'?el('button',{class:'ghost',onclick:()=>act(()=>api('tally',{}))},'结束投票'):null,
    ...g.log.map(x=>el('div',{class:'card'},el('strong',{class:x.agent?'agent':''},x.by),' ',el('span',{class:'pre'},x.text))),
    g.result?el('h2',{},g.result):null);
}
watch(render);`
});

function setup(app) {
  const game = { status: 'idle', round: 0, players: [], order: [], turn: 0, log: [], votes: {}, result: '', undercover: '' };
  app.page(PAGE);
  app.state((member) => ({ ...game, undercover: undefined, votes: undefined, myVote: game.votes[member.id] || '',
    voted: Object.keys(game.votes).length,
    // Only your own word, until the game ends.
    players: game.players.map((p) => ({ ...p, word: game.status === 'over' || p.id === member.id ? p.word : '' })) }));
  const alive = () => game.players.filter((p) => !p.out);
  const said = () => game.log.filter((x) => x.round).map((x) => `${x.by}：${x.text}`).join('\n') || '（还没有人描述）';

  function advance() {
    game.turn++;
    if (game.turn >= game.order.length) { game.status = 'voting'; game.votes = {}; app.changed(); return void agentVotes(); }
    app.changed();
    void agentTurn();
  }

  async function agentTurn() {
    const p = game.players[game.order[game.turn]];
    if (!p?.agent || game.status !== 'describing') return;
    const turn = game.turn, round = game.round;
    let text;
    try {
      const reply = await app.askJSON(p.id, `你在空间「${app.title}」玩谁是卧底，你是玩家「${p.name}」。你的词是「${p.word}」（你不知道自己是不是卧底）。\n` +
        `这一轮之前的描述：\n${said()}\n用一句话描述你的词：不能说出这个词或其中的字，太直白容易暴露，太模糊会被怀疑。\n格式：{"say":"你的描述"}`);
      text = String(reply?.say || '……').replace(new RegExp(p.word, 'g'), '**').slice(0, 80);
    } catch { text = '（没有回应）'; }
    if (game.turn !== turn || game.round !== round) return;
    game.log.push({ round, by: p.name, text, agent: true });
    advance();
  }

  function agentVotes() {
    for (const p of alive().filter((x) => x.agent)) {
      const others = alive().filter((x) => x.id !== p.id);
      app.askJSON(p.id, `你在空间「${app.title}」玩谁是卧底，你是「${p.name}」，你的词是「${p.word}」。描述如下：\n${said()}\n` +
        `投票找出卧底（词和大多数人不同的人），从这些玩家里选一个：${others.map((x) => x.name).join('、')}。\n格式：{"vote":"玩家名字","reason":"一句理由"}`)
        .then((reply) => {
          const target = others.find((x) => x.name === reply?.vote);
          if (game.status !== 'voting' || !target) return;
          game.votes[p.id] = target.id;
          game.log.push({ by: p.name, text: `投给 ${target.name}：${String(reply.reason || '').slice(0, 60)}`, agent: true });
          app.changed();
          if (Object.keys(game.votes).length >= alive().length) tally();
        }).catch(() => {});
    }
  }

  function tally() {
    if (game.status !== 'voting') return;
    const count = {};
    for (const target of Object.values(game.votes)) count[target] = (count[target] || 0) + 1;
    const top = Math.max(0, ...Object.values(count));
    const leaders = Object.keys(count).filter((id) => count[id] === top);
    if (leaders.length === 1) {
      const out = game.players.find((p) => p.id === leaders[0]);
      out.out = true;
      game.log.push({ by: '裁判', text: `${out.name} 出局（${top} 票）` });
    } else game.log.push({ by: '裁判', text: '平票，无人出局' });
    const spy = game.players.find((p) => p.id === game.undercover);
    if (spy.out) Object.assign(game, { status: 'over', result: `平民获胜！卧底是 ${spy.name}` });
    else if (alive().length <= 2 || game.round >= 4) Object.assign(game, { status: 'over', result: `卧底 ${spy.name} 获胜！` });
    else { game.round++; game.order = shuffle(alive().map((p) => game.players.indexOf(p))); game.turn = -1; game.status = 'describing'; advance(); return; }
    app.changed();
  }

  const shuffle = (list) => list.map((x) => [Math.random(), x]).sort((a, b) => a[0] - b[0]).map((x) => x[1]);

  app.route('POST', '/start', (member) => {
    if (!member.owner) throw refuse('只有群主可以开局', 403);
    if (['describing', 'voting'].includes(game.status)) throw refuse('这一局还没结束');
    const people = [...app.humans(), ...app.agents()];
    if (people.length < 3) throw refuse('至少要 3 位玩家（人和 agent 都算）');
    const [common, odd] = shuffle(PAIRS[Math.floor(Math.random() * PAIRS.length)]);
    const spy = people[Math.floor(Math.random() * people.length)];
    Object.assign(game, { status: 'describing', round: 1, log: [], votes: {}, result: '', undercover: spy.id, turn: -1,
      players: people.map((p) => ({ id: p.id, name: p.name, agent: p.agent, word: p.id === spy.id ? odd : common, out: false })) });
    game.order = shuffle(game.players.map((_, i) => i));
    advance();
  });

  app.route('POST', '/describe', (member, body) => {
    const p = game.players[game.order[game.turn]];
    if (game.status !== 'describing' || p?.id !== member.id) throw refuse('还没轮到你');
    const text = String(body.text || '').trim().slice(0, 80);
    if (!text) throw refuse('说一句描述');
    if (text.includes(p.word)) throw refuse('不能直接说出你的词');
    game.log.push({ round: game.round, by: p.name, text });
    advance();
  });

  app.route('POST', '/skip', (member) => {
    const p = game.players[game.order[game.turn]];
    if (!member.owner || game.status !== 'describing' || p?.agent) throw refuse('现在不能跳过', 403);
    game.log.push({ round: game.round, by: p.name, text: '（跳过）' });
    advance();
  });

  app.route('POST', '/vote', (member, body) => {
    const me = alive().find((p) => p.id === member.id);
    if (game.status !== 'voting' || !me) throw refuse('现在不能投票');
    if (!alive().some((p) => p.id === body.target && p.id !== me.id)) throw refuse('选一位在场的其他玩家');
    game.votes[me.id] = body.target;
    app.changed();
    if (Object.keys(game.votes).length >= alive().length) tally();
  });

  app.route('POST', '/tally', (member) => {
    if (!member.owner || game.status !== 'voting') throw refuse('现在不能结束投票', 403);
    tally();
  });
}

if (require.main === module) run({ name: '谁是卧底' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '谁是卧底' };
