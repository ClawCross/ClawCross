/* Private ten-floor runs. Agents author bounded content; the server owns combat and rewards. */
'use strict';
const crypto=require('node:crypto'),Battle=require('./battle'),Rooms=require('./room-sdk'),{SPECIES,art,gain}=require('./garden');
const fail=message=>{throw Object.assign(new Error(message),{status:409});};
const copy=value=>structuredClone(value),bounded=(value,size)=>String(value||'').trim().slice(0,size);
function defaults(){return Array.from({length:10},(_,i)=>{const floor=i+1,species=Object.keys(SPECIES)[i%3],s=SPECIES[species];return{floor,title:floor%5===0?'星门守望者':'第 '+floor+' 层 · 星光回廊',story:floor%5===0?'星门缓缓打开。守望者等待着能够团结伙伴的旅人。':'沿着悬浮的石阶向上，你和伙伴遇到了守护这一层的精灵。',enemy:{id:'tower-'+floor,name:(floor%5===0?'首领 · ':'巡星者 · ')+s.name,description:'星塔的原创守护精灵。',svg:art(species,floor),elements:[s.element],base:{hp:100,attack:100,defense:100,speed:100},skills:[s.moves[0],'撞击','守住','自我再生'],actions:{}}};});}
function frozenPet(c){return{...copy(c),battle:{hp:c.stats.hp,pp:undefined,cooldowns:{}}};}
class Tower{
 constructor(store,garden,changed){Object.assign(this,{store,garden,changed});if(!store.data.towers||!store.data.towerCampaigns)store.update(d=>{d.towers||={};d.towerCampaigns||={};});}
 state(owner){return this.store.data.towers[owner]||null;}
 range(owner){const run=this.state(owner),start=run?(run.status==='fighting'?run.floor+1:run.cleared+1):1;if(start>10)fail('本次星塔已完成，开始新旅程后再设计楼层。');return{start,end:Math.min(10,start+2)};}
 install(owner,value,range,agent){
  if(!value||!Array.isArray(value.floors)||!value.floors.length||value.floors.length>3)fail('一次可以设计 1–3 个后续楼层。');
  const now=this.range(owner),ids=new Set(),floors=value.floors.map(row=>{
   if(!row||typeof row!=='object')fail('楼层格式无效。');
   if(!Number.isInteger(row.floor)||row.floor<Math.max(now.start,range.start)||row.floor>range.end||ids.has(row.floor))fail('只能设计本次请求指定且尚未开始的楼层。');ids.add(row.floor);
   const raw=row.enemy,svg=Battle.safeSvg(raw?.svg),skills=Battle.skillsFor(raw||{}),base=raw?.stats===undefined?{hp:100,attack:100,defense:100,speed:100}:Battle.statsFor(raw.stats),elements=raw?.elements;
   if(!svg||!skills||!skills.some(s=>!['guard','heal'].includes(s.type))||!base||!bounded(row.title,64)||!bounded(row.story,600)||!bounded(raw?.name,40)||!Array.isArray(elements)||elements.length<1||elements.length>2||new Set(elements).size!==elements.length||elements.some(type=>!Object.hasOwn(Battle.TYPES,type)))fail('楼层剧情、精灵形象、属性或技能没有通过校验；原关卡保留。');
   return{floor:row.floor,title:bounded(row.title,64),story:bounded(row.story,600),designer:agent,enemy:{id:'tower-'+row.floor,name:bounded(raw.name,40),description:bounded(raw.description,600),svg,elements,base,skills:skills.map(s=>s.name),customSkills:skills,actions:Battle.actionsFor(raw.actions,skills.map(s=>s.name),skills)}};
  });
  this.store.update(d=>{const campaign=d.towerCampaigns[owner]||defaults();for(const floor of floors)campaign[floor.floor-1]=floor;d.towerCampaigns[owner]=campaign;const run=d.towers[owner];if(run&&run.status!=='complete'&&run.status!=='lost')for(const floor of floors)run.floors[floor.floor-1]=copy(floor);});this.changed();
 }
 build(run){return new Battle.BattleModel().rebuild(run.config,run.members,run.rows);}
 nextConfig(run){
  const content=run.floors[run.floor-1],level=Math.min(30,Math.max(1,run.level+Math.floor((run.floor-1)/2))),enemy=copy(content.enemy);
  enemy.level=level;enemy.stats=Object.fromEntries(Object.entries(enemy.base).map(([key,value])=>[key,Math.round(value*(1+.04*(level-1))*(key==='hp'&&run.floor%5===0?1.2:1))]));
  run.config={v:1,app:'battle',id:run.id+':'+run.floor,host:{key:run.owner,name:run.name},players:[{key:run.owner,name:run.name,controller:run.name},{key:'tower-foe',name:'星塔守护者',controller:'星塔守护者'}],companion_battle:true,battle_rules:4,manual_start:false,player_first:true,companions:[run.team[0],enemy],squads:[run.team,[enemy]]};run.rows=[];run.status='fighting';
 }
 start(person,ids){
  const prior=this.state(person.id);if(prior&&!['lost','complete','abandoned'].includes(prior.status))fail('先完成当前旅程，或选择结束本次旅程。');
  const garden=this.garden.view(person.id),keys=ids||garden.team;
  if(!Array.isArray(keys)||keys.length<1||keys.length>3||new Set(keys).size!==keys.length||keys.some(id=>!garden.creatures.some(c=>c.id===id)))fail('先在背包组建 1–3 只精灵的队伍。');
  const team=keys.map(id=>frozenPet(garden.creatures.find(c=>c.id===id))),run={id:crypto.randomUUID(),owner:person.id,name:person.name,floor:1,cleared:0,rested:0,level:Math.round(team.reduce((n,c)=>n+c.level,0)/team.length),team,floors:copy(this.store.data.towerCampaigns?.[person.id]||defaults()),members:[{principal:person.id,name:person.name,is_agent:false},{principal:'tower-foe',name:'星塔守护者',is_agent:false}],rows:[],requests:[]};this.nextConfig(run);this.store.update(d=>{d.towers[person.id]=run;});this.changed();return this.view(person.id);
 }
 intent(state){const foe=state.fighters[1],skills=foe.skills.filter(skill=>!Battle.Combat.unavailable(foe,skill));if(foe.hp<foe.maxHp*.35){const heal=skills.find(s=>s.type==='heal');if(heal)return heal;}const attacks=skills.filter(s=>!['guard','heal'].includes(s.type));if((state.active===0?state.turn+1:state.turn)%6===0){const guard=skills.find(s=>s.type==='guard');if(guard)return guard;}return attacks.sort((a,b)=>b.power-a.power)[0]||skills[0];}
 act(owner,body){
  const saved=this.state(owner);if(!saved)fail('先开始星塔旅程。');const key=typeof body.requestId==='string'?body.requestId.slice(0,100):'';if(key&&saved.requests.includes(key))return this.view(owner);
  this.store.update(d=>{
   const run=d.towers[owner];
   if(body.kind==='abandon'){run.status='abandoned';}
   else if(body.kind==='next'){
    if(run.status!=='cleared'||run.cleared>=10)fail('先击败当前楼层的守护者。');run.floor++;this.nextConfig(run);
   }else if(body.kind==='rest'){
    if(run.status!=='cleared'||run.cleared%3!==0||run.rested===run.cleared)fail('每三层有一次休整机会。');run.rested=run.cleared;
    for(const pet of run.team){pet.battle.hp=Math.min(pet.stats.hp,pet.battle.hp+Math.round(pet.stats.hp*.3));for(const name of pet.skills){const skill=pet.customSkills?.find(s=>s.name===name)||Battle.skillFor(name);pet.battle.pp[name]=Math.min(Battle.Combat.limit(skill.type),(pet.battle.pp[name]||0)+1);}}
   }else{
    if(run.status!=='fighting')fail('当前没有进行中的星塔战斗。');let state=this.build(run);if(state.active!==0)fail('等待守护者行动。');if(body.room&&body.room!==run.config.id||body.turn!==undefined&&body.turn!==state.turn)fail('战斗已更新，请使用当前回合。');
    const apply=(side,kind,fields)=>{const event={type:'command',kind,room:run.config.id,id:crypto.randomUUID(),fighter:run.config.players[side].key,turn:state.turn,...fields},row={id:run.rows.length+1,sender:run.config.players[side].key,content:Rooms.encodeEvent(event)};const next=new Battle.BattleModel().rebuild(run.config,run.members,[...run.rows,row]);if(next.log.at(-1)?.id!==event.id)fail(next.invalid||'本次操作不可用。');run.rows.push(row);state=next;};
    if(!['skill','switch'].includes(body.kind))fail('请选择技能或替补精灵。');apply(0,body.kind,{move:body.move,creature:body.creature});
    if(state.winner===null&&state.active===1){const move=this.intent(state);if(!move)apply(1,'forfeit',{});else apply(1,'skill',{move:move.name});}
    if(state.winner===0){run.cleared=run.floor;run.status=run.floor===10?'complete':'cleared';const p=d.garden.players[owner];p.coins+=10+run.floor*3;for(const c of p.creatures.filter(c=>run.team.some(pet=>pet.id===c.id)))gain(c,15+run.floor*2);p.notice='星塔第 '+run.floor+' 层完成，奖励已保存。';}
    else if(state.winner===1)run.status='lost';
    run.team=[state.fighters[0],...state.teams[0].filter(c=>c!==state.fighters[0])].map(fighter=>{const pet=run.team.find(c=>c.id===fighter.creature);return{...pet,battle:{hp:fighter.hp,pp:copy(fighter.pp),cooldowns:copy(fighter.cooldowns)}};});
   }
   if(key){run.requests.push(key);run.requests=run.requests.slice(-50);}
  });this.changed();return this.view(owner);
 }
 view(owner){
  const run=this.state(owner);if(!run)return{run:null,garden:this.garden.view(owner),preview:(this.store.data.towerCampaigns?.[owner]||defaults()).map(({floor,title,story,designer})=>({floor,title,story,designer}))};
  const state=this.build(run),next=state.winner===null?this.intent(state):null;
  return{garden:this.garden.view(owner),preview:run.floors.map(({floor,title,story,designer})=>({floor,title,story,designer})),run:{id:run.id,floor:run.floor,cleared:run.cleared,status:run.status,rested:run.rested,story:run.floors[run.floor-1].story,title:run.floors[run.floor-1].title,team:copy(run.team),intent:next?.name||'',boss:run.floor%5===0},battle:{title:run.floors[run.floor-1].title,roomId:run.id,members:run.members,actor:{key:owner,name:run.name},announcement:Rooms.encodeConfig({...run.config,companions:undefined,squads:undefined}),arena:{active:true,ready:[],fighters:run.config.companions,squads:run.config.squads},messages:run.rows,garden:this.garden.view(owner)}};
 }
}
module.exports={Tower,defaults};
