#!/usr/bin/env node
/* 五子棋军师 · Gomoku with Advisors — black and white seats each take a person or an agent:
 * people play and may ask their own agent for advice, or hand the seat to an agent and watch two
 * agents play each other. Five in a row wins; an agent that twice names an illegal point forfeits.
 * 简介：人或 agent 坐上棋盘，下棋时可以问自己的 agent 军师。
 *
 *   node examples/space-apps/gomoku/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const SIZE = 15;
const COLS = 'ABCDEFGHIJKLMNO';
const NAMES = { black: '黑棋', white: '白棋' };

const PAGE = page({
  appId: 'gomoku',
  title: '五子棋军师',
  style: `#board{display:grid;grid-template-columns:repeat(15,1fr);gap:0;width:min(100%,480px);aspect-ratio:1;background:#e3b778;border-radius:10px;padding:4px;margin:8px auto}
#board button{padding:0;border-radius:0;background:transparent;border:0;position:relative;aspect-ratio:1}
#board button::before{content:'';position:absolute;left:50%;top:0;bottom:0;border-left:1px solid #8a5a2b}#board button::after{content:'';position:absolute;top:50%;left:0;right:0;border-top:1px solid #8a5a2b}
#board i{position:absolute;inset:8%;border-radius:50%;z-index:1}#board i.black{background:radial-gradient(circle at 35% 35%,#666,#000)}#board i.white{background:radial-gradient(circle at 35% 35%,#fff,#ccc)}
#board i.last{outline:2px solid #e03131}#board i.hint{background:#228be6;opacity:.45}.seat{flex:1;min-width:200px}`,
  body: `<h1>五子棋军师</h1><div class="row" id="seats"></div><p id="status" class="muted"></p><div id="board"></div><div id="advice"></div>`,
  script: `
const {api,watch,act,el,fill,keep}=App,$=id=>document.getElementById(id);
const COLS='ABCDEFGHIJKLMNO';
function seat(s,color){
  const g=s.state,who=g.seats[color],agents=s.members.filter(m=>m.agent);
  // The agent picker is kept across renders so someone else's move does not reset a choice.
  const pick=keep('agent-'+color,()=>el('select',{id:'agent-'+color}));
  if(pick.options.length!==agents.length){const v=pick.value;fill(pick,...agents.map(a=>el('option',{value:a.id},a.name)));if(agents.some(a=>a.id===v))pick.value=v;}
  return el('div',{class:'card seat'},el('strong',{},color==='black'?'⚫ 黑棋':'⚪ 白棋'),' ',who?(who.agent?'🤖 ':'')+who.name:el('span',{class:'muted'},'空位'),
    g.status!=='playing'?el('div',{class:'row',style:'margin-top:6px'},el('button',{class:'ghost',onclick:()=>act(()=>api('sit',{color}))},'我来下'),
      pick,el('button',{class:'ghost',onclick:()=>act(()=>api('sit',{color,agent:$('agent-'+color).value}))},'交给 agent')):null);
}
function render(s){
  const g=s.state,mine=['black','white'].find(c=>g.seats[c]&&!g.seats[c].agent&&g.seats[c].id===s.me.id);
  fill($('seats'),seat(s,'black'),seat(s,'white'),s.owner&&g.status!=='playing'?el('button',{onclick:()=>act(()=>api('start',{}))},g.status==='over'?'再来一局':'开局'):null);
  $('status').textContent=g.status==='playing'?'轮到'+(g.turn==='black'?'黑棋':'白棋')+(g.seats[g.turn].agent?'（'+g.seats[g.turn].name+' 思考中…）':''):g.result||'两个座位都坐好后开局。';
  const last=g.moves.at(-1),hint=g.advice[s.me.id];
  fill($('board'),...g.board.flatMap((row,y)=>row.map((c,x)=>el('button',{'aria-label':COLS[x]+(y+1),onclick:()=>mine&&g.turn===mine&&act(()=>api('move',{x,y}))},
    c?el('i',{class:c+(last&&last.x===x&&last.y===y?' last':'')}):hint&&hint.x===x&&hint.y===y?el('i',{class:'hint'}):null))));
  fill($('advice'),mine&&g.status==='playing'?el('div',{class:'row'},el('button',{class:'ghost',disabled:g.turn!==mine,onclick:()=>act(()=>api('advise',{}))},'问军师'),
    hint?el('span',{class:'muted grow'},(hint.thinking?'军师思考中…':'军师建议 '+COLS[hint.x]+(hint.y+1)+'：'+hint.why)):null):null);
}
watch(render);`
});

function picture(board) {
  return '   ' + COLS.split('').join(' ') + '\n' + board.map((row, y) => String(y + 1).padStart(2) + ' ' +
    row.map((c) => (c === 'black' ? 'X' : c === 'white' ? 'O' : '.')).join(' ')).join('\n');
}

function five(board, x, y) {
  const color = board[y][x];
  return [[1, 0], [0, 1], [1, 1], [1, -1]].some(([dx, dy]) => {
    let count = 1;
    for (const sign of [1, -1]) for (let k = 1; k < 5; k++) {
      const nx = x + dx * k * sign, ny = y + dy * k * sign;
      if (board[ny]?.[nx] !== color) break;
      count++;
    }
    return count >= 5;
  });
}

const ask = (color, why) => `你在空间里下五子棋（15×15，先连成五子者胜），你执${NAMES[color]}（${color === 'black' ? 'X' : 'O'}）。${why}\n棋盘（. 是空位）：\n`;
const format = '\n格式：{"x":列字母,"y":行号,"why":"一句理由"}，例如 {"x":"H","y":8,"why":"占天元"}。只能下在空位。';

function setup(app) {
  const game = { board: [], moves: [], seats: { black: null, white: null }, turn: 'black', status: 'idle', result: '', advice: {} };
  app.page(PAGE);
  app.state((member) => ({ ...game, advice: { [member.id]: game.advice[member.id] } }));
  const point = (reply) => ({ x: COLS.indexOf(String(reply?.x || '').toUpperCase()), y: Number(reply?.y) - 1, why: String(reply?.why || '').slice(0, 120) });
  const legal = (p) => p.x >= 0 && p.x < SIZE && p.y >= 0 && p.y < SIZE && !game.board[p.y][p.x];

  function place(x, y) {
    game.board[y][x] = game.turn;
    game.moves.push({ x, y, color: game.turn });
    game.advice = {};
    if (five(game.board, x, y)) Object.assign(game, { status: 'over', result: `${NAMES[game.turn]} ${game.seats[game.turn].name} 连成五子获胜！` });
    else if (game.moves.length >= SIZE * SIZE) Object.assign(game, { status: 'over', result: '棋盘下满，和棋' });
    else game.turn = game.turn === 'black' ? 'white' : 'black';
    app.changed();
    void agentTurn();
  }

  async function agentTurn() {
    const seat = game.seats[game.turn];
    if (game.status !== 'playing' || !seat?.agent) return;
    const color = game.turn, count = game.moves.length;
    let note = '';
    for (let attempt = 0; attempt < 2; attempt++) {
      let p;
      try { p = point(await app.askJSON(seat.id, ask(color, '轮到你落子。' + note) + picture(game.board) + format)); } catch { p = { x: -1, y: -1 }; }
      if (game.status !== 'playing' || game.moves.length !== count) return;
      if (legal(p)) return place(p.x, p.y);
      note = '你上一次给的位置不合法（越界或已有棋子），请换一个空位。';
    }
    const winner = color === 'black' ? 'white' : 'black';
    Object.assign(game, { status: 'over', result: `${seat.name} 两次落子不合法判负，${NAMES[winner]} 获胜` });
    app.changed();
  }

  app.route('POST', '/sit', (member, body) => {
    if (game.status === 'playing') throw refuse('对局中不能换座');
    if (!['black', 'white'].includes(body.color)) throw refuse('选黑棋或白棋');
    if (body.agent) {
      const agent = app.member(body.agent);
      if (!agent?.agent) throw refuse('选一个 agent');
      game.seats[body.color] = { id: agent.id, name: agent.name, agent: true };
    } else game.seats[body.color] = { id: member.id, name: member.name, agent: false };
    app.changed();
  });

  app.route('POST', '/start', (member) => {
    if (!member.owner) throw refuse('只有群主可以开局', 403);
    if (!game.seats.black || !game.seats.white) throw refuse('两个座位都要有人');
    Object.assign(game, { board: Array.from({ length: SIZE }, () => Array(SIZE).fill('')), moves: [], turn: 'black', status: 'playing', result: '', advice: {} });
    app.changed();
    void agentTurn();
  });

  app.route('POST', '/move', (member, body) => {
    const seat = game.seats[game.turn];
    if (game.status !== 'playing' || seat?.agent || seat?.id !== member.id) throw refuse('还没轮到你');
    if (!legal(body)) throw refuse('这里不能下');
    place(body.x, body.y);
  });

  app.route('POST', '/advise', (member) => {
    const color = ['black', 'white'].find((c) => game.seats[c]?.id === member.id && !game.seats[c].agent);
    if (game.status !== 'playing' || color !== game.turn) throw refuse('轮到你时才能问军师');
    const advisor = app.agentsOf(member.id)[0];
    if (!advisor) throw refuse('你在这台设备上没有 agent 当军师');
    const count = game.moves.length;
    game.advice[member.id] = { thinking: true };
    app.changed();
    app.askJSON(advisor.id, ask(color, `你是 ${member.name} 的军师，给他推荐下一步。`) + picture(game.board) + format).then((reply) => {
      const p = point(reply);
      if (game.moves.length !== count) return;
      game.advice[member.id] = legal(p) ? p : { thinking: false, x: -1, y: -1, why: '军师给的位置不合法' };
    }).catch((error) => { game.advice[member.id] = { x: -1, y: -1, why: '军师没回应：' + error.message }; }).finally(() => app.changed());
  });
}

if (require.main === module) run({ name: '五子棋军师' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '五子棋军师', five };
