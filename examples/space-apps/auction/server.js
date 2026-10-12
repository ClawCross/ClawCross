#!/usr/bin/env node
/* 拍卖行 · Auction House — an auctioneer agent invents curious lots; every bidding agent gets a
 * private valuation for each lot (only its own person sees it) and bids sealed, following the
 * strategy its person wrote. Highest bid wins and pays it; profit is valuation minus price.
 *
 *   node examples/space-apps/auction/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const BUDGET = 100;
const LOTS = 4;

const PAGE = page({
  title: '拍卖行',
  style: `.lot h3{margin:0}.bid{display:flex;justify-content:space-between;padding:3px 0}.win{color:var(--good);font-weight:700}.mine{background:#fff3bf;color:#000;border-radius:8px;padding:2px 8px}`,
  body: `<h1>拍卖行</h1><p class="muted">每个 agent 对拍品有自己的心理价，只有它的主人知道。密封出价，价高者得。</p>
<div id="setup" class="card"></div><div id="house"></div>`,
  script: `
const {api,watch,act,el,fill,keep}=App,$=id=>document.getElementById(id);
function render(s){
  const h=s.state,agents=s.members.filter(m=>m.agent);
  $('setup').hidden=!(s.owner&&['idle','over'].includes(h.status));
  if(!$('setup').hidden&&!$('setup').childElementCount)fill($('setup'),el('div',{class:'row'},el('label',{},'拍卖师 ',el('select',{id:'host'},...agents.map(a=>el('option',{value:a.id},a.name)))),
    el('input',{id:'theme',class:'grow',placeholder:'拍品主题，例如：赛博朋克古董'}),el('button',{onclick:()=>act(()=>api('open',{host:$('host').value,theme:$('theme').value}))},'开拍')));
  fill($('house'),
    h.status==='idle'?el('p',{class:'muted'},s.owner?'选拍卖师和主题。':'等待群主开拍。'):null,
    h.status==='preparing'?el('p',{class:'muted'},h.host+' 正在准备拍品…'):null,
    h.mine.length&&h.status!=='over'?el('div',{class:'card'},el('strong',{},'给你的 agent 定策略（只有它看得到）'),
      el('div',{class:'row'},keep('strategy',()=>el('input',{id:'strategy',class:'grow',placeholder:'例如：只抢心理价最高的那件，其余捡漏',value:h.strategy})),
        el('button',{onclick:()=>act(()=>api('strategy',{text:$('strategy').value}))},'交代'))):null,
    el('div',{class:'row'},...Object.entries(h.wallets).map(([n,w])=>el('span',{class:'chip'},n+' 余额 '+w.budget+' · 盈亏 '+(w.profit>=0?'+':'')+w.profit))),
    ...h.lots.map((l,i)=>el('div',{class:'card lot'},el('h3',{},(i===h.index&&h.status==='bidding'?'🔨 ':'')+l.name),el('div',{class:'muted pre'},l.story),
      ...h.mine.map(a=>el('div',{class:'mine'},a.name+' 的心理价：'+a.values[i])),
      ...l.bids.map(b=>el('div',{class:'bid'+(b.win?' win':'')},el('span',{},b.agent+(b.win?' 🏆':'')+'：'+b.line),el('span',{},b.bid))),
      l.reveal?el('div',{class:'muted'},'各家心理价：'+l.reveal):null)),
    s.owner&&h.status==='ready'?el('button',{onclick:()=>act(()=>api('next',{}))},h.index<0?'第一件开拍':'下一件开拍'):null,
    h.status==='over'?el('h2',{},'拍卖结束，赢家：'+h.winner):null);
}
watch(render);`
});

function setup(app) {
  const house = { status: 'idle', host: '', lots: [], index: -1, bidders: [], winner: '' };
  app.page(PAGE);
  app.state((member) => ({
    ...house, bidders: undefined,
    wallets: Object.fromEntries(house.bidders.map((b) => [b.name, { budget: b.budget, profit: b.profit }])),
    // A bidder's valuations and strategy belong to its own person.
    mine: house.bidders.filter((b) => b.coach === member.id).map((b) => ({ name: b.name, values: b.values })),
    strategy: house.bidders.find((b) => b.coach === member.id)?.strategy || ''
  }));

  app.route('POST', '/open', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以开拍', 403);
    if (!['idle', 'over'].includes(house.status)) throw refuse('拍卖正在进行');
    const host = app.member(body.host);
    if (!host?.agent) throw refuse('选一个 agent 当拍卖师');
    const bidders = app.agents().filter((a) => a.id !== host.id);
    if (!bidders.length) throw refuse('除了拍卖师，至少还要一个 agent 来竞拍');
    Object.assign(house, { status: 'preparing', host: host.name, lots: [], index: -1, winner: '',
      bidders: bidders.map((a) => ({ id: a.id, name: a.name, coach: app.ownerOf(a.id)?.id || '', budget: BUDGET, profit: 0, strategy: '',
        values: Array.from({ length: LOTS }, () => 10 + Math.floor(Math.random() * 61)) })) });
    app.changed();
    const theme = String(body.theme || '奇怪的收藏品').slice(0, 40);
    app.askJSON(host.id, `你是空间「${app.title}」拍卖行的拍卖师「${host.name}」。围绕「${theme}」编 ${LOTS} 件有趣的拍品，每件一个名字和两句来历。\n` +
      '格式：{"lots":[{"name":"拍品名","story":"两句来历"}]}').then((reply) => {
      const lots = (reply?.lots || []).slice(0, LOTS).map((l) => ({ name: String(l?.name || '无名拍品').slice(0, 40), story: String(l?.story || '').slice(0, 200), bids: [], reveal: '' }));
      if (lots.length < LOTS) throw new Error('拍品不够');
      Object.assign(house, { status: 'ready', lots });
    }).catch(() => Object.assign(house, { status: 'idle' })).finally(() => app.changed());
  });

  app.route('POST', '/strategy', (member, body) => {
    const mine = house.bidders.filter((b) => b.coach === member.id);
    if (!mine.length || house.status === 'over') throw refuse('你没有在竞拍的 agent');
    for (const b of mine) b.strategy = String(body.text || '').slice(0, 200);
    app.changed();
  });

  app.route('POST', '/next', (member) => {
    if (!member.owner || house.status !== 'ready') throw refuse('现在不能开拍下一件', 403);
    house.index++;
    house.status = 'bidding';
    app.changed();
    void hammer(house.index);
  });

  /** One sealed round: every bidder bids at once, then the hammer falls. */
  async function hammer(i) {
    const lot = house.lots[i];
    const history = house.lots.slice(0, i).map((l) => `${l.name} 成交价 ${l.bids.find((b) => b.win)?.bid ?? '流拍'}`).join('；') || '（这是第一件）';
    await Promise.all(house.bidders.map(async (b) => {
      let bid = 0, line = '（没出价）';
      try {
        const reply = await app.askJSON(b.id, `你在空间「${app.title}」的拍卖行替 ${b.coach ? app.member(b.coach)?.name : '自己'} 竞拍。` +
          `拍品「${lot.name}」：${lot.story}\n你心里认为它值 ${b.values[i]}（别人不知道），你还剩 ${b.budget}。之前：${history}\n` +
          `主人的策略：${b.strategy || '没有交代，自己判断'}\n密封出价，价高者得并付出自己的出价；赚的是心理价减出价，花超了会亏。\n` +
          '格式：{"bid":出价整数（0 表示不要）,"line":"一句喊价台词"}');
        bid = Math.max(0, Math.min(b.budget, Math.floor(Number(reply?.bid) || 0)));
        line = String(reply?.line || '').slice(0, 80);
      } catch { /* no bid */ }
      lot.bids.push({ agent: b.name, bid, line, win: false });
      app.changed();
    }));
    const top = [...lot.bids].sort((a, b) => b.bid - a.bid)[0];
    if (top?.bid > 0) {
      top.win = true;
      const b = house.bidders.find((x) => x.name === top.agent);
      b.budget -= top.bid;
      b.profit += b.values[i] - top.bid;
    }
    lot.reveal = house.bidders.map((b) => `${b.name} ${b.values[i]}`).join('，');
    if (i + 1 >= house.lots.length) {
      house.status = 'over';
      house.winner = [...house.bidders].sort((a, b) => b.profit - a.profit)[0].name;
    } else house.status = 'ready';
    app.changed();
  }
}

if (require.main === module) run({ name: '拍卖行' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '拍卖行' };
