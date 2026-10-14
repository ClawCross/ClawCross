#!/usr/bin/env node
/* 谈判桌 · Negotiation Table — a buyer agent and a seller agent bargain over a deal. Each agent's
 * own person privately sets its limit and priorities (the other side never sees them until the
 * end) and may slip it a hint. Agents take turns with a message and an offer until one accepts or
 * ten turns pass; then both limits are revealed and the surplus scored.
 * 简介：买卖双方的 agent 讨价还价，底线只有各自主人知道。
 *
 *   node examples/space-apps/negotiation/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const TURNS = 10;
const SCENES = ['一台用了两年的索尼 A7M4 相机，带两个镜头', '上海静安区一套一居室的月租', '一个电商小程序的外包开发报价',
  '一幅新锐画家的油画原作', '一张周杰伦演唱会内场门票'];
const ROLE = { buyer: '买方', seller: '卖方' };

const PAGE = page({
  appId: 'negotiation',
  title: '谈判桌',
  style: `.msg{border-radius:14px;padding:9px 12px;margin:7px 0;max-width:86%}.msg.buyer{background:#e7f1ff}.msg.seller{background:#e9f7ef;margin-left:auto}
@media(prefers-color-scheme:dark){.msg.buyer{background:#0b2545}.msg.seller{background:#0d2b1a}}.offer{font-weight:700;font-size:18px}`,
  body: `<h1>谈判桌</h1><div id="setup" class="card"></div><div id="mandate"></div><div id="table"></div>`,
  script: `
const {api,watch,act,el,fill,keep}=App,$=id=>document.getElementById(id);
function render(s){
  const n=s.state.deal,agents=s.members.filter(m=>m.agent);
  $('setup').hidden=!(s.owner&&(!n||n.status==='done'));
  if(!$('setup').hidden&&!$('setup').childElementCount)fill($('setup'),el('div',{class:'muted'},'谈什么'),
    el('input',{id:'scene',style:'width:100%',placeholder:'交易内容'}),el('div',{class:'row',style:'margin:6px 0'},...s.state.scenes.map(x=>el('button',{class:'ghost',onclick:()=>$('scene').value=x},x))),
    el('div',{class:'row'},...['buyer','seller'].map((r,i)=>el('label',{class:'grow'},r==='buyer'?'买方 ':'卖方 ',el('select',{id:r},...agents.map((a,j)=>el('option',{value:a.id,selected:j===i},a.name)))))),
    el('button',{style:'margin-top:8px',onclick:()=>act(()=>api('open',{scene:$('scene').value,buyer:$('buyer').value,seller:$('seller').value}))},'摆上谈判桌'));
  if(!n){fill($('table'),el('p',{class:'muted'},s.owner?'选好交易内容和双方 agent。':'等待群主摆上谈判桌。'));fill($('mandate'));return;}
  const mine=['buyer','seller'].find(r=>n[r].coach===s.me.id);
  fill($('mandate'),mine&&n.status!=='done'?el('div',{class:'card'},el('strong',{},'你是'+(mine==='buyer'?'买方':'卖方')+' '+n[mine].name+' 的主人（只有你和它看得到）'),
    n.status==='waiting'?el('div',{},el('div',{class:'row',style:'margin-top:6px'},keep('limit',()=>el('input',{id:'limit',type:'number',placeholder:mine==='buyer'?'最高愿付':'最低接受',value:n[mine].limit??''})),
      keep('wants',()=>el('input',{id:'wants',class:'grow',placeholder:'优先事项，例如：最好包邮、能开发票',value:n[mine].wants||''}))),
      el('button',{style:'margin-top:6px',onclick:()=>act(()=>api('mandate',{limit:+$('limit').value,wants:$('wants').value}))},n[mine].ready?'更新授权':'交给 agent')):
    el('div',{class:'row'},keep('hint',()=>el('input',{id:'hint',class:'grow',placeholder:'悄悄提醒你的 agent'})),el('button',{onclick:()=>act(()=>{const v=$('hint').value;$('hint').value='';return api('hint',{hint:v});})},'提醒')),
    el('div',{class:'muted'},'你的底线：'+(n[mine].limit??'未设')+'　'+(n[mine].wants||''))):null);
  fill($('table'),el('h2',{},n.scene),
    el('p',{class:'muted'},'买方 '+n.buyer.name+(n.buyer.ready?' ✓':'（等待授权）')+'　·　卖方 '+n.seller.name+(n.seller.ready?' ✓':'（等待授权）')),
    s.owner&&n.status==='waiting'?el('button',{disabled:!(n.buyer.ready&&n.seller.ready),onclick:()=>act(()=>api('start',{}))},'开始谈判'):null,
    ...n.turns.map(t=>el('div',{class:'msg '+t.role},el('strong',{},ROLE[t.role]+' '+n[t.role].name),el('div',{class:'pre'},t.say),
      t.offer!=null?el('div',{class:'offer'},'出价 '+t.offer):null,t.accept?el('div',{class:'agent'},'✔ 接受成交'):null)),
    n.status==='talking'?el('p',{class:'muted'},n[n.next].name+' 正在考虑…'):null,
    n.status==='done'?el('div',{class:'card'},el('strong',{},n.price!=null?'成交价 '+n.price:'谈崩了'),
      el('div',{},'买方底线 '+n.buyer.limit+'，卖方底线 '+n.seller.limit),
      n.price!=null?el('div',{},'买方省下 '+(n.buyer.limit-n.price)+'，卖方多赚 '+(n.price-n.seller.limit)):null,
      n.breach?el('div',{class:'error'},n.breach):null):null);
}
const ROLE={buyer:'买方',seller:'卖方'};
watch(render);`
});

function turnPrompt(app, n, role) {
  const me = n[role], other = n[role === 'buyer' ? 'seller' : 'buyer'];
  const history = n.turns.map((t) => `【${ROLE[t.role]}】${t.say}${t.offer != null ? `（出价 ${t.offer}）` : ''}${t.accept ? '（接受）' : ''}`).join('\n') || '（还没有人开口）';
  const last = [...n.turns].reverse().find((t) => t.role !== role && t.offer != null);
  return `你是空间「${app.title}」谈判桌上的${ROLE[role]}「${me.name}」，代表 ${me.coachName}，对手是${ROLE[role === 'buyer' ? 'seller' : 'buyer']}「${other.name}」。\n` +
    `交易：${n.scene}\n主人给你的授权（对方不知道）：${role === 'buyer' ? '最高愿付' : '最低接受'} ${me.limit}；${me.wants || '没有额外要求'}。\n` +
    (me.hint ? `主人刚刚悄悄提醒你：${me.hint}\n` : '') +
    `目前对话：\n${history}\n这是第 ${n.turns.length + 1} / ${TURNS} 轮。` + (last ? `对方最新出价 ${last.offer}。` : '') +
    '\n争取对主人最有利的价格，但不要超出授权；觉得对方最新出价可以接受就 accept。\n' +
    '格式：{"say":"你对对方说的话（一两句）","offer":你的出价数字或null,"accept":true或false}';
}

function setup(app) {
  let deal = null;
  app.page(PAGE);
  app.state((member) => {
    if (!deal) return { scenes: SCENES, deal: null };
    const done = deal.status === 'done';
    // Each side's limit and wishes are private to its own person until the deal ends.
    const view = (role) => ({ ...deal[role], hint: undefined,
      limit: done || deal[role].coach === member.id ? deal[role].limit : undefined, wants: done || deal[role].coach === member.id ? deal[role].wants : undefined });
    return { scenes: SCENES, deal: { ...deal, buyer: view('buyer'), seller: view('seller') } };
  });

  const side = (id) => {
    const agent = app.member(id);
    if (!agent?.agent) throw refuse('请选择空间里的 agent');
    const coach = app.ownerOf(id);
    return { id, name: agent.name, coach: coach?.id || '', coachName: coach?.name || '无人', limit: null, wants: '', hint: '', ready: false };
  };

  app.route('POST', '/open', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以摆谈判桌', 403);
    if (deal?.status === 'talking') throw refuse('谈判正在进行');
    const scene = String(body.scene || '').trim().slice(0, 160);
    if (!scene) throw refuse('写下交易内容');
    if (body.buyer === body.seller) throw refuse('买卖双方要选两个不同的 agent');
    deal = { scene, buyer: side(body.buyer), seller: side(body.seller), turns: [], status: 'waiting', next: 'seller', price: null, breach: '' };
    app.changed();
  });

  app.route('POST', '/mandate', (member, body) => {
    const role = ['buyer', 'seller'].find((r) => deal?.[r].coach === member.id);
    if (!role || deal.status !== 'waiting') throw refuse('只有代表方的主人能在开始前授权', 403);
    if (!Number.isFinite(body.limit) || body.limit <= 0) throw refuse('写下一个底线价格');
    Object.assign(deal[role], { limit: body.limit, wants: String(body.wants || '').slice(0, 200), ready: true });
    app.changed();
  });

  app.route('POST', '/hint', (member, body) => {
    const role = ['buyer', 'seller'].find((r) => deal?.[r].coach === member.id);
    if (!role || deal.status !== 'talking') throw refuse('谈判进行中才能提醒自己的 agent', 403);
    deal[role].hint = String(body.hint || '').slice(0, 200);
  });

  app.route('POST', '/start', (member) => {
    if (!member.owner || deal?.status !== 'waiting') throw refuse('现在不能开始', 403);
    if (!deal.buyer.ready || !deal.seller.ready) throw refuse('双方的主人都要先授权');
    deal.status = 'talking';
    app.changed();
    void bargain(deal);
  });

  async function bargain(n) {
    try {
      while (n.turns.length < TURNS) {
        const role = n.next;
        const prompt = turnPrompt(app, n, role);
        n[role].hint = '';
        const answer = (await app.askJSON(n[role].id, prompt)) || {};
        const offer = Number.isFinite(Number(answer.offer)) && answer.offer !== null ? Number(answer.offer) : null;
        const last = [...n.turns].reverse().find((t) => t.role !== role && t.offer != null);
        const accept = answer.accept === true && !!last;
        n.turns.push({ role, say: String(answer.say || '……').slice(0, 300), offer, accept });
        n.next = role === 'buyer' ? 'seller' : 'buyer';
        app.changed();
        if (accept) {
          n.price = last.offer;
          if (role === 'buyer' && n.price > n.buyer.limit) n.breach = `买方 ${n.buyer.name} 接受了超出授权的价格`;
          if (role === 'seller' && n.price < n.seller.limit) n.breach = `卖方 ${n.seller.name} 接受了低于授权的价格`;
          break;
        }
      }
    } catch (error) {
      n.breach = '谈判中断：' + error.message;
    }
    n.status = 'done';
    app.changed();
  }
}

if (require.main === module) run({ name: '谈判桌' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '谈判桌' };
