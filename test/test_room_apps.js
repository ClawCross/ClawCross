const test = require('node:test');
const assert = require('node:assert/strict');
const Rooms = require('../src/frontend/static/js/room-sdk.js');
const Battle = require('../src/frontend/static/js/room-battle.js');
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
