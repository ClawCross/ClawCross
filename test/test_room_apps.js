const test = require('node:test');
const assert = require('node:assert/strict');
const Rooms = require('../examples/space-apps/creature-battle/room-sdk.js');
const Battle = require('../examples/space-apps/creature-battle/battle.js');
const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="30" fill="#ff9900"/></svg>';
const members = [
  {principal:'owner',name:'Alice',is_agent:false,device:0},
  {principal:'alpha',name:'Alpha',is_agent:true,device:0},
  {principal:'bob',name:'Bob',is_agent:false,device:1},
  {principal:'beta',name:'Beta',is_agent:true,device:1},
  {principal:'viewer',name:'Viewer',is_agent:false,device:2}
];
const config = {v:1,app:'battle',id:'match',host:{key:'owner',name:'Alice'},players:[
  {key:'alpha',name:'Alpha',controller:'Alice'}, {key:'beta',name:'Beta',controller:'Bob'}]};
let serial;
function message(sender, event) {return {id:++serial,sender,content:Rooms.encodeEvent({...event,room:'match'})};}
function prepared() {
  serial=0;
  return [
    message('owner',{type:'command',kind:'avatar',id:'a1',fighter:'alpha'}),
    message('owner',{type:'command',kind:'avatar',id:'a2',fighter:'beta'}),
    message('alpha',{type:'avatar',id:'a1',name:'Amber',element:'fire',svg}),
    message('beta',{type:'avatar',id:'a2',name:'Ripple',element:'normal',svg})
  ];
}
function command(id,turn,fighter,sender='owner') {return message(sender,{type:'command',kind:'turn',id,turn,fighter,request:'attack'});}
function move(id,turn,name,sender='alpha',extra={}) {return message(sender,{type:'move',id,turn,move:name,...extra});}

test('room configuration preserves a human announcement and packet SVG escaping',()=>{
  const encoded=Rooms.encodeConfig(config,'Hello room');
  assert.equal(Rooms.parseConfig(encoded).id,'match');
  assert.equal(Rooms.humanAnnouncement(encoded),'Hello room');
  const fence=String.fromCharCode(96).repeat(3);
  const content=fence+'json\n'+JSON.stringify({protocol:'clawcross.room/1',type:'avatar',svg},null,2)+'\n'+fence;
  assert.equal(Rooms.parseEvent(content).svg,svg);
});
test('damage follows the fixed skill table, ignoring supplied HP and power',()=>{
  const rows=prepared();rows.push(command('turn1',1,'alpha'),move('turn1',1,'喷射火焰','alpha',{power:99999,hp:0}));
  const state=new Battle.BattleModel().rebuild(config,members,rows);
  assert.equal(state.fighters[1].hp,76);
  assert.equal(state.turn,2);
  assert.equal(state.active,1);
});
test('unknown moves, human impersonation, stale turns and duplicate replies do not advance combat',()=>{
  const rows=prepared();
  rows.push(command('turn1',1,'alpha'),move('turn1',1,'destroy everything'));
  const model=new Battle.BattleModel();
  assert.equal(model.rebuild(config,members,rows).turn,1);
  rows.push(move('turn1',1,'喷射火焰','viewer'),move('turn1',2,'喷射火焰'),move('turn1',1,'喷射火焰'));
  rows.push(move('turn1',1,'喷射火焰'),command('bad',2,'beta','viewer'),move('bad',2,'十万伏特','beta'));
  const state=model.rebuild(config,members,rows);
  assert.equal(state.fighters[1].hp,76);assert.equal(state.fighters[0].hp,100);assert.equal(state.turn,2);
});
test('guard and healing are rule-driven and HP stays bounded',()=>{
  const rows=prepared();
  rows.push(command('1',1,'alpha'),move('1',1,'守住'),
    command('2',2,'beta','bob'),move('2',2,'喷射火焰','beta'),
    command('3',3,'alpha'),move('3',3,'自我再生'));
  const state=new Battle.BattleModel().rebuild(config,members,rows);
  assert.equal(state.fighters[0].hp,100);assert.equal(state.log[1].text.includes('8 点伤害'),true);
});
test('local principal translation and remote browser views settle the same combat',()=>{
  const rows=prepared();rows.push(command('1',1,'alpha'),move('1',1,'撞击'));
  const localMembers=members.map(row=>({...row,principal:row.principal==='alpha'?'ag_local':row.principal==='owner'?'u:alice':row.principal}));
  const localRows=rows.map(row=>({...row,sender:row.sender==='alpha'?'ag_local':row.sender==='owner'?'u:alice':row.sender}));
  const remote=new Battle.BattleModel().rebuild(config,members,rows);
  const local=new Battle.BattleModel().rebuild(config,localMembers,localRows);
  assert.deepEqual(local.fighters.map(f=>f.hp),remote.fighters.map(f=>f.hp));
  assert.equal(local.turn,remote.turn);
});
test('active SVG content and external resources are rejected',()=>{
  assert.ok(Battle.safeSvg(svg));
  for(const unsafe of [
    '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/secret"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><path style="fill:url(https://example.com)"/></svg>'
  ])assert.equal(Battle.safeSvg(unsafe),'');
});
test('explicit start and controller identity govern alternating turns',()=>{
  const cfg={...config,manual_start:true,owner_only:true};
  const rows=prepared(),model=new Battle.BattleModel();
  rows.push(command('early',1,'alpha'),move('early',1,'撞击'));
  assert.equal(model.rebuild(cfg,members,rows).turn,1);
  rows.push(message('viewer',{type:'start'}));
  assert.equal(model.rebuild(cfg,members,rows).started,false);
  rows.push(message('owner',{type:'start'}),command('1',1,'alpha'),move('1',1,'撞击'));
  rows.push(command('stolen',2,'beta','owner'),move('stolen',2,'撞击','beta'));
  assert.equal(model.rebuild(cfg,members,rows).turn,2);
  rows.push(command('2',2,'beta','bob'),move('2',2,'撞击','beta'));
  const state=model.rebuild(cfg,members,rows);
  assert.equal(state.turn,3);
  assert.deepEqual(state.fighters.map(f=>f.hp),[86,86]);
});

function preparedV2(attacker=['fire'], defender=['grass'], moves=['喷射火焰','撞击','守住','自我再生']) {
  const rows=prepared();
  rows[2].content=Rooms.encodeEvent({type:'avatar',room:'match',id:'a1',name:'Amber',elements:attacker,moves,svg});
  rows[3].content=Rooms.encodeEvent({type:'avatar',room:'match',id:'a2',name:'Ripple',elements:defender,moves:['撞击','水枪','守住','自我再生'],svg});
  rows.push(message('owner',{type:'start'}));
  return rows;
}
test('new creatures select exactly four unique catalog moves and valid attributes',()=>{
  assert.equal(Battle.SKILLS.length,44);
  assert.equal(Object.keys(Battle.TYPES).length,18);
  const cfg={...config,battle_rules:2,manual_start:true};
  const rows=preparedV2();
  const state=new Battle.BattleModel().rebuild(cfg,members,rows);
  assert.deepEqual(state.fighters[0].moves,['喷射火焰','撞击','守住','自我再生']);
  assert.deepEqual(state.fighters[0].elements,['fire']);
  for (const moves of [[],['撞击','撞击','守住','自我再生'],['撞击','destroy','守住','自我再生']]) {
    assert.equal(new Battle.BattleModel().rebuild(cfg,members,preparedV2(['fire'],['grass'],moves)).fighters[0].svg,'');
  }
  assert.equal(new Battle.BattleModel().rebuild(cfg,members,preparedV2(['unknown'])).fighters[0].svg,'');
});
test('unselected moves do not settle and new type rules apply STAB, resistance and immunity',()=>{
  const cfg={...config,battle_rules:2,manual_start:true},model=new Battle.BattleModel();
  const rows=preparedV2();rows.push(command('1',1,'alpha'),move('1',1,'十万伏特'));
  assert.equal(model.rebuild(cfg,members,rows).turn,1);
  rows.push(move('1',1,'喷射火焰'));
  assert.equal(model.rebuild(cfg,members,rows).fighters[1].hp,28); // 24 × 1.5 × 2
  const resisted=preparedV2(['fire'],['water']);resisted.push(command('r',1,'alpha'),move('r',1,'喷射火焰'));
  assert.equal(model.rebuild(cfg,members,resisted).fighters[1].hp,82);
  const immune=preparedV2(['electric'],['ground'],['十万伏特','撞击','守住','自我再生']);immune.push(command('i',1,'alpha'),move('i',1,'十万伏特'));
  assert.equal(model.rebuild(cfg,members,immune).fighters[1].hp,100);
  assert.equal(model.state.turn,2);
  assert.equal(Battle.effectiveness('fire',['grass','steel']),4);
  assert.equal(Battle.effectiveness('ground',['flying','steel']),0);
});
test('skill effects are self-contained animated SVGs for both attack directions and status moves',()=>{
  for (const skill of Battle.SKILLS) for (const side of [0,1]) {
    const svg=Battle.effectSvg(skill,side);
    assert.ok(svg.startsWith('<svg'));
    assert.match(svg,/<animate/);
    assert.doesNotMatch(svg,/<script|onload=|foreignObject|<image|href=/i);
    if(!['guard','heal'].includes(skill.type)) assert.ok(svg.includes(side?'M490 110 L110 110':'M110 110 L490 110'));
  }
});
