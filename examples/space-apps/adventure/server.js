#!/usr/bin/env node
/* 冒险团 · Adventure Party — a tabletop adventure. One agent is the game master; every other agent
 * is an adventurer that acts on its own person's orders. Each round the adventurers declare
 * actions, the server rolls a d20 for each in the open, and the master narrates what happens.
 *
 *   node examples/space-apps/adventure/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const SETTINGS = ['赛博朋克夜市的一桩失窃案', '沙漠古墓深处的回声', '失联的空间站「白鲸号」', '江南客栈里的一封血书'];
const HP = 10;

const PAGE = page({
  title: '冒险团',
  style: `.scene{font-size:16px;line-height:1.8}.hero{flex:1;min-width:180px}.hp{color:var(--bad);font-weight:700}.roll{font-weight:700}.roll.hi{color:var(--good)}.roll.lo{color:var(--bad)}`,
  body: `<h1>冒险团</h1><div id="setup" class="card"></div><div id="party" class="row"></div><div id="tale"></div>`,
  script: `
const {api,watch,act,el,fill,keep}=App,$=id=>document.getElementById(id);
function render(s){
  const a=s.state,agents=s.members.filter(m=>m.agent);
  $('setup').hidden=!(s.owner&&['idle','over'].includes(a.status));
  if(!$('setup').hidden&&!$('setup').childElementCount)fill($('setup'),el('div',{class:'row'},el('label',{},'主持人 ',el('select',{id:'master'},...agents.map(x=>el('option',{value:x.id},x.name)))),
    el('select',{id:'setting',class:'grow'},...a.settings.map(x=>el('option',{},x))),el('button',{onclick:()=>act(()=>api('begin',{master:$('master').value,setting:$('setting').value}))},'出发')));
  fill($('party'),...a.heroes.map(h=>el('div',{class:'card hero'},el('strong',{class:'agent'},h.name),' ',el('span',{class:'hp'},'♥ '+h.hp),el('div',{class:'muted'},'由 '+h.coachName+' 指挥'),
    h.coach===s.me.id&&a.status==='waiting'?el('div',{class:'row'},keep('order-'+h.id,()=>el('input',{id:'order-'+h.id,class:'grow',placeholder:'下一步让它做什么'})),
      el('button',{class:'ghost',onclick:()=>act(()=>{const v=$('order-'+h.id).value;$('order-'+h.id).value='';return api('order',{hero:h.id,text:v});})},'下令')):null,
    h.order?el('div',{class:'muted'},'已下令：'+h.order):null)));
  fill($('tale'),a.status==='idle'?el('p',{class:'muted'},s.owner?'选一位主持人和冒险。':'等待群主组队出发。'):null,
    ...a.rounds.map(r=>el('div',{class:'card'},el('div',{class:'scene pre'},r.scene),...r.actions.map(x=>el('div',{},el('span',{class:'agent'},x.name),'：'+x.action+' ',
      el('span',{class:'roll '+(x.roll>=15?'hi':x.roll<=5?'lo':'')},'🎲'+x.roll))))),
    a.status==='narrating'?el('p',{class:'muted'},a.master+' 正在讲述…'):null,a.status==='acting'?el('p',{class:'muted'},'冒险者们正在行动…'):null,
    s.owner&&a.status==='waiting'?el('button',{onclick:()=>act(()=>api('round',{}))},'进入下一回合'):null,
    a.status==='over'?el('h2',{},'冒险结束'):null);
}
watch(render);`
});

const roll = () => 1 + Math.floor(Math.random() * 20);

function setup(app) {
  const party = { status: 'idle', master: '', masterId: '', setting: '', rounds: [], heroes: [] };
  app.page(PAGE);
  app.state(() => ({ ...party, masterId: undefined, settings: SETTINGS }));

  function history() {
    return party.rounds.map((r, i) => `第${i}幕：${r.scene}${r.actions.length ? '\n行动：' + r.actions.map((x) => `${x.name} ${x.action}（d20=${x.roll}）`).join('；') : ''}`).join('\n');
  }

  async function narrate(first) {
    party.status = 'narrating';
    app.changed();
    const heroes = party.heroes.map((h) => `${h.name}（生命 ${h.hp}）`).join('、');
    try {
      const reply = await app.askJSON(party.masterId, `你是空间「${app.title}」桌游冒险的主持人「${party.master}」。冒险：${party.setting}。冒险者：${heroes}。\n` +
        (first ? '开场：描述起始场景（150 字内），给出一个悬念。' : `之前：\n${history()}\n根据上一回合的行动和骰点（d20：15 以上大成功，5 以下出岔子）讲述后果（200 字内），推进剧情；可以让冒险者掉血。`) +
        '\n格式：{"scene":"讲述","hp":{"冒险者名字":生命变化整数},"ended":true或false}');
      for (const h of party.heroes) h.hp = Math.max(0, Math.min(HP, h.hp + (Number.isInteger(reply?.hp?.[h.name]) ? reply.hp[h.name] : 0)));
      party.rounds.push({ scene: String(reply?.scene || '（主持人沉默了）').slice(0, 800), actions: [] });
      party.status = reply?.ended === true || party.heroes.every((h) => h.hp === 0) || party.rounds.length > 12 ? 'over' : 'waiting';
    } catch (error) {
      party.rounds.push({ scene: '主持人没有回应：' + error.message, actions: [] });
      party.status = 'waiting';
    }
    app.changed();
  }

  app.route('POST', '/begin', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以组队', 403);
    if (!['idle', 'over'].includes(party.status)) throw refuse('冒险正在进行');
    const master = app.member(body.master);
    if (!master?.agent) throw refuse('选一个 agent 当主持人');
    const heroes = app.agents().filter((a) => a.id !== master.id);
    if (!heroes.length) throw refuse('除了主持人，至少还要一个 agent 当冒险者');
    Object.assign(party, { master: master.name, masterId: master.id, setting: SETTINGS.includes(body.setting) ? body.setting : SETTINGS[0], rounds: [],
      heroes: heroes.map((h) => { const coach = app.ownerOf(h.id); return { id: h.id, name: h.name, hp: HP, coach: coach?.id || '', coachName: coach?.name || '自己', order: '' }; }) });
    void narrate(true);
  });

  app.route('POST', '/order', (member, body) => {
    const hero = party.heroes.find((h) => h.id === body.hero);
    if (!hero || hero.coach !== member.id || party.status !== 'waiting') throw refuse('只能在回合之间指挥自己的冒险者', 403);
    hero.order = String(body.text || '').trim().slice(0, 120);
    app.changed();
  });

  app.route('POST', '/round', (member) => {
    if (!member.owner || party.status !== 'waiting') throw refuse('现在不能推进', 403);
    party.status = 'acting';
    app.changed();
    void act();
  });

  /** Every adventurer acts at once; the dice are the server's and everyone sees them. */
  async function act() {
    const scene = party.rounds.at(-1);
    await Promise.all(party.heroes.filter((h) => h.hp > 0).map(async (h) => {
      let action;
      try {
        const reply = await app.askJSON(h.id, `你是桌游冒险「${party.setting}」里的冒险者「${h.name}」，生命 ${h.hp}/${HP}。当前场景：\n${scene.scene}\n` +
          `${h.coachName} 的指令：${h.order || '（没有指令，自己判断）'}\n说出你这一回合的行动（一句话，具体可执行）。格式：{"action":"行动"}`);
        action = String(reply?.action || '原地观察').slice(0, 120);
      } catch { action = '愣在原地'; }
      scene.actions.push({ name: h.name, action, roll: roll() });
      h.order = '';
      app.changed();
    }));
    await narrate(false);
  }
}

if (require.main === module) run({ name: '冒险团' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '冒险团' };
