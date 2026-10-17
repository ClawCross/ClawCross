const assert=require('node:assert/strict');
const {Store}=require('../sdk/js/space-store'),{Garden}=require('../examples/space-apps/creature-battle/garden'),{Arena}=require('../examples/space-apps/creature-battle/arena'),Battle=require('../examples/space-apps/creature-battle/battle'),Rooms=require('../examples/space-apps/creature-battle/room-sdk');
const store=new Store(null,{rows:[],config:null}),garden=new Garden(store),members=[{id:'alice',name:'Alice',device:'a'},{id:'bob',name:'Bob',device:'b'}];let config=null;
const view=members.map(m=>({principal:m.id,name:m.name,device:m.device})),changed=()=>store.update(d=>{d.config=config;}),arena=new Arena(store,garden,()=>({members}),()=>config,next=>{config=next;},changed);
const alice={id:'alice',name:'Alice'},bob={id:'bob',name:'Bob'},a=garden.act('alice','adopt',{species:'spark'}).selected,b=garden.act('bob','adopt',{species:'ripple'}).selected;
assert.throws(()=>arena.choose(alice,b));arena.choose(alice,a);assert.throws(()=>arena.start(alice));arena.choose(bob,b);arena.start(alice,'bob');
assert.equal(config.companion_battle,true);assert.deepEqual(config.players.map(p=>p.key),['alice','bob']);assert.equal(arena.active(),true);assert.throws(()=>arena.start(alice,'bob'));
const build=()=>new Battle.BattleModel().rebuild(config,view,store.data.rows);
assert.equal(build().fighters[0].svg,garden.view('alice').creatures[0].svg);
const old=build().fighters[0].stats.attack;garden.act('alice','train',{focus:'attack'});assert.equal(build().fighters[0].stats.attack,old);
const play=(person,side,move)=>{const s=build();store.update(d=>{d.rows.push({id:d.rows.length+1,sender:person,content:Rooms.encodeEvent({type:'command',kind:'skill',room:config.id,id:'turn-'+d.rows.length,fighter:config.players[side].key,turn:s.turn,move})});});};
play('bob',0,'火花');assert.equal(build().turn,1);
let count=0;while(build().winner===null&&count++<25){const s=build(),side=s.active;play(config.players[side].key,side,s.fighters[side].moves[0]);}
assert.notEqual(build().winner,null);assert.equal(arena.active(),false);garden.reward(config.id,build(),['alice','bob']);const rewarded=structuredClone(store.data);garden.reward(config.id,build(),['alice','bob']);assert.deepEqual(store.data,rewarded);
arena.choose(alice,a);arena.choose(bob,b);assert.equal(arena.display().id,config.id);arena.start(alice,'bob');assert.equal(arena.active(),true);
console.log('PASS: player-owned companions, no Agent required, frozen match designs, turn authorization, battle completion and reward deduplication');
