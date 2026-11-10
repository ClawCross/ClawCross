/* Game-level contracts: guard economy, owned squads, versioned matches and private tower saves. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const Battle=require('../examples/space-apps/creature-battle/battle'),Combat=require('../examples/space-apps/creature-battle/combat'),Rooms=require('../examples/space-apps/creature-battle/room-sdk');
const {Store}=require('../sdk/js/space-store'),{Garden,art}=require('../examples/space-apps/creature-battle/garden'),{Tower}=require('../examples/space-apps/creature-battle/tower'),{Arena}=require('../examples/space-apps/creature-battle/arena'),{Designer,MARKER}=require('../examples/space-apps/creature-battle/designer');
(async()=>{
 const fighter=()=>Combat.init({hp:100,maxHp:100,stats:{attack:100,defense:100},elements:['normal'],skills:['撞击','守住','自我再生'].map(Battle.skillFor)});
 const a=fighter(),b=fighter(),attack=Battle.skillFor('撞击'),guard=Battle.skillFor('守住');
 Combat.act(a,b,guard,Battle.effectiveness);const blocked=Combat.act(b,a,attack,Battle.effectiveness);assert.equal(blocked.damage,0);assert.equal(a.hp,100);assert.equal(a.riposte,true);assert.throws(()=>Combat.act(a,b,guard,Battle.effectiveness),/冷却/);
 const boosted=Combat.act(a,b,attack,Battle.effectiveness).damage,plain=Combat.act(b,a,attack,Battle.effectiveness).damage;assert.ok(boosted>plain);assert.equal(a.riposte,false);Combat.act(a,b,attack,Battle.effectiveness);assert.equal(a.cooldowns.guard,0);
 const domain=Battle.actionsFor({'撞击':{domain:{style:'void',color:'#a855f7',html:'<script>bad()</script>'}}},['撞击']);assert.deepEqual(domain['撞击'].domain,{style:'void',color:'#a855f7'});assert.ok(!Battle.domainSvg(domain['撞击'].domain).includes('script'));assert.deepEqual(Battle.actionsFor({'撞击':{domain:{style:'<script>',color:'url(secret)'}}},['撞击']),{});
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'creature-expedition-'));
 try{
  const store=new Store(dir,{rows:[],config:null}),garden=new Garden(store),tower=new Tower(store,garden,()=>{});
  for(const who of ['alice','bob'])for(const species of ['spark','ripple','moss'])garden.act(who,'adopt',{species});
  const team=garden.view('alice').creatures.map(c=>c.id),bobTeam=garden.view('bob').creatures.map(c=>c.id);garden.act('alice','team',{ids:team});
  assert.throws(()=>garden.act('alice','team',{ids:[bobTeam[0]]}));assert.throws(()=>garden.act('alice','team',{ids:[...team,team[0]]}));
  let config;const roster={members:[{id:'alice',name:'Alice',device:'a'},{id:'bob',name:'Bob',device:'b'}]},view=roster.members.map(m=>({principal:m.id,name:m.name,is_agent:false})),arena=new Arena(store,garden,()=>roster,()=>config,next=>{config=next;},()=>{});
  arena.choose(roster.members[0],team[0],team);arena.choose(roster.members[1],bobTeam[0],bobTeam);arena.start(roster.members[0]);assert.equal(config.battle_rules,4);assert.deepEqual(config.squads.map(t=>t.length),[3,3]);
  const moves=[];let model=new Battle.BattleModel().rebuild(config,view,moves);
  const issue=(side,kind,fields)=>{moves.push({id:moves.length+1,sender:config.players[side].key,content:Rooms.encodeEvent({type:'command',kind,room:config.id,id:'m'+moves.length,fighter:config.players[side].key,turn:model.turn,...fields})});model=new Battle.BattleModel().rebuild(config,view,moves);};
  issue(0,'switch',{creature:team[1]});assert.equal(model.fighters[0].creature,team[1]);assert.equal(model.active,1);assert.equal(model.turn,2);const prior=model.fighters[0].creature;issue(1,'switch',{creature:team[2]});assert.equal(model.fighters[0].creature,prior);assert.equal(model.active,1);
  let count=0;while(model.winner===null&&count++<300){const side=model.active,f=model.fighters[side];if(f.hp<=0){issue(side,'switch',{creature:model.teams[side].find(c=>c.hp>0).creature});continue;}const skill=f.skills.find(s=>!['guard','heal'].includes(s.type)&&!Combat.unavailable(f,s));if(!skill)throw Error('No usable attack');issue(side,'skill',{move:skill.name});}
  assert.notEqual(model.winner,null);assert.ok(model.teams[1-model.winner].every(c=>c.hp===0));
  const alice={id:'alice',name:'Alice'};let result=tower.start(alice);assert.equal(result.run.floor,1);assert.equal(tower.view('bob').run,null);assert.throws(()=>tower.act('bob',{kind:'skill',move:'撞击'}));assert.throws(()=>tower.start(alice));
  const raw={name:'梦境守护者',level:999,description:'来自原创星塔的守护者。',svg:art('moss'),elements:['grass'],skills:[{name:'叶刃',type:'grass',power:24},{name:'冲撞',type:'normal',power:16},{name:'光盾',type:'guard',power:0},{name:'暖流',type:'heal',power:22}]};
  const before=JSON.stringify(store.data);assert.throws(()=>tower.install('alice',{floors:[{floor:1,title:'改写当前层',story:'不能这样做',enemy:raw}]},tower.range('alice'),'alpha'));assert.equal(JSON.stringify(store.data),before);
  tower.install('alice',{floors:[{floor:2,title:'梦境花园',story:'花瓣化作悬浮的石阶。',enemy:raw}]},tower.range('alice'),'alpha');assert.equal(store.data.towers.alice.floors[1].title,'梦境花园');
  let played=0;while(result.run.status==='fighting'&&played++<80){const run=store.data.towers.alice,s=tower.build(run),f=s.fighters[0];if(f.hp<=0){result=tower.act('alice',{kind:'switch',creature:s.teams[0].find(c=>c.hp>0).creature});continue;}const available=f.skills.filter(skill=>!Combat.unavailable(f,skill));let move=f.hp<f.maxHp*.4?available.find(s=>s.type==='heal'):null;move ||= available.filter(s=>!['guard','heal'].includes(s.type)).sort((a,b)=>b.power*Battle.effectiveness(b.type,s.fighters[1].elements)-a.power*Battle.effectiveness(a.type,s.fighters[1].elements))[0];result=tower.act('alice',{kind:'skill',move:move.name,requestId:'tower-'+played});}
  assert.equal(result.run.status,'cleared');const rewarded=JSON.stringify(store.data);tower.act('alice',{kind:'skill',move:'撞击',requestId:'tower-'+played});assert.equal(JSON.stringify(store.data),rewarded);
  const carry=result.run.team.map(c=>c.battle.hp);result=tower.act('alice',{kind:'next',requestId:'next-floor'});assert.equal(result.run.floor,2);assert.deepEqual(result.run.team.map(c=>c.battle.hp),carry);assert.equal(result.run.title,'梦境花园');
  const restored=new Tower(new Store(dir,{rows:[],config:null}),garden,()=>{});assert.deepEqual(restored.view('alice').run,result.run);assert.throws(()=>tower.act('alice',{kind:'rest'}));assert.equal(result.battle.arena.fighters[1].level,1);
  // Resume a saved third-floor checkpoint: rest recovers once, with no duplicate grant.
  store.update(d=>{const run=d.towers.alice;run.status='cleared';run.floor=3;run.cleared=3;for(const pet of run.team){pet.battle.hp=40;for(const name of pet.skills)pet.battle.pp[name]=0;}});
  const rested=tower.act('alice',{kind:'rest',requestId:'camp-3'});assert.ok(rested.run.team.every(c=>c.battle.hp===70));assert.ok(rested.run.team.every(c=>Object.values(c.battle.pp).every(n=>n===1)));const restedSave=JSON.stringify(store.data);tower.act('alice',{kind:'rest',requestId:'camp-3'});assert.equal(JSON.stringify(store.data),restedSave);assert.throws(()=>tower.act('alice',{kind:'rest',requestId:'camp-3-again'}));
  let asked;const designer=new Designer(store,garden,{ask:async(id,prompt)=>{asked={id,prompt};return 1;}},()=>({members:[...roster.members,{id:'alpha',name:'Alpha',agent:true,device:'a'},{id:'beta',name:'Beta',agent:true,device:'b'}]}),()=>{},tower);
  await assert.rejects(()=>designer.ask(alice,{agent:'beta',task:'tower',content:'设计楼层'}));await designer.ask(alice,{agent:'alpha',task:'tower',content:'设计后续剧情和敌人'});assert.equal(asked.id,'alpha');const pending=store.data.designers.alice.pending;
  designer.receive({agent:'alpha',request:1,content:MARKER+JSON.stringify({request:pending.id,screen:'tower',tower:{floors:[{floor:pending.range.start,title:'星门长廊',story:'伙伴们来到星门之前。',enemy:raw}]}})});assert.equal(designer.view('alice').pending,null);assert.equal(designer.view('alice').ui.screen,'tower');
  console.log('PASS: guard/counter/cooldown, safe domain visuals, owned 3v3 squads, voluntary/forced switches, tower combat, atomic generation, private saves, carryover, idempotent rewards and Agent ownership');
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
