/* Server-authoritative companions, training, wild encounters and progression. Original game content. */
'use strict';
const crypto=require('node:crypto');
const Battle=require('./battle'),Combat=require('./combat');
const SPECIES=Object.freeze({
 spark:{name:'萤尾',element:'fire',color:'#f6ae76',accent:'#f27c64',moves:['火花','火焰轮','喷射火焰','破坏光线']},
 ripple:{name:'潮团',element:'water',color:'#8dd9e6',accent:'#749de4',moves:['水枪','水之波动','冲浪','冰冻光束']},
 moss:{name:'苔角',element:'grass',color:'#b3db8e',accent:'#65b69a',moves:['藤鞭','飞叶快刀','能量球','虫鸣']}
});
const LEVEL_CAP=30,keys=['hp','attack','defense','speed'];
const fail=message=>{throw Object.assign(new Error(message),{status:409});};
const form=level=>level>=10?2:level>=5?1:0;
function art(species,level=1){
 const s=SPECIES[species]||SPECIES.spark,f=form(level),leaf=species==='moss',water=species==='ripple';
 return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><defs><linearGradient id="skin" x2=".2" y2="1"><stop stop-color="'+s.color+'"/><stop offset="1" stop-color="'+s.accent+'"/></linearGradient></defs>'+
  (f?'<circle cx="128" cy="130" r="106" fill="none" stroke="'+s.color+'" stroke-opacity=".22" stroke-width="'+(f===2?10:4)+'"/>':'')+
  '<ellipse cx="128" cy="231" rx="68" ry="10" fill="#152b45" opacity=".25"/>'+
  '<path d="M177 158Q236 106 225 171Q225 212 177 204" fill="'+s.accent+'"/>'+
  (water?'<path d="M66 137Q17 98 38 165L70 191M191 137Q240 98 219 165L186 191" fill="'+s.accent+'"/>':'<path d="M76 94L57 27Q98 30 109 75M145 76Q163 25 202 26L179 98" fill="url(#skin)"/>')+
  (leaf?'<path d="M127 74Q77 14 150 24Q185 46 127 74Z" fill="#538f70"/><path d="M128 73L148 32" fill="none" stroke="#d2f2b8" stroke-width="4"/>':'')+
  '<path d="M61 134Q60 76 128 73Q195 77 195 137L195 176Q195 221 128 222Q59 221 61 176Z" fill="url(#skin)"/>'+
  '<ellipse cx="128" cy="183" rx="40" ry="30" fill="#fff3d8" opacity=".6"/><path d="M79 205Q62 238 103 228M154 228Q195 238 178 206" fill="'+s.accent+'"/>'+
  '<ellipse cx="98" cy="133" rx="10" ry="15" fill="#254052"/><ellipse cx="159" cy="133" rx="10" ry="15" fill="#254052"/><circle cx="95" cy="128" r="4" fill="white"/><circle cx="156" cy="128" r="4" fill="white"/>'+
  '<path d="M115 157Q128 168 141 157" fill="none" stroke="#3c5262" stroke-width="5" stroke-linecap="round"/><ellipse cx="80" cy="154" rx="12" ry="6" fill="#f58b81" opacity=".5"/><ellipse cx="178" cy="154" rx="12" ry="6" fill="#f58b81" opacity=".5"/>'+
  (f===2?'<path d="M97 80L113 50L128 69L144 49L159 80" fill="#fff1b7" stroke="'+s.accent+'" stroke-width="3"/>':'')+'</svg>';
}
function creature(species,level=1){
 const s=SPECIES[species],base={id:crypto.randomUUID(),species,name:s.name,elements:[s.element],base:{hp:100,attack:100,defense:100,speed:100},level,xp:0,bond:10,energy:100,training:{hp:0,attack:0,defense:0,speed:0},skills:[s.moves[0],'撞击','守住','自我再生'],agent:''};return base;
}
function stats(c){return Object.fromEntries(keys.map(k=>[k,Math.round(c.base[k]*(1+.04*(c.level-1))+(c.training[k]||0)+form(c.level)*4)]));}
function gain(c,xp){const before=c.level;c.xp+=xp;while(c.level<LEVEL_CAP&&c.xp>=c.level*40){c.xp-=c.level*40;c.level++;}if(c.level===LEVEL_CAP)c.xp=0;return c.level-before;}
function unlocked(c){const species=SPECIES[c.species],list=species?species.moves.filter((_,i)=>c.level>=[1,3,5,10][i]):c.skills;return [...new Set([...c.skills,...list])];}
function publicCreature(c){return{...c,stats:stats(c),nextXp:c.level*40,form:form(c.level),unlocked:unlocked(c),svg:c.svg||(c.species?art(c.species,c.level):'')};}
function initial(){return{coins:60,balls:3,potions:3,creatures:[],selected:'',encounter:null,notice:'选一位伙伴，开始你的冒险。',requests:[]};}
function designFields(c,raw,agent){
 const svg=raw.svg===undefined?publicCreature(c).svg:Battle.safeSvg(raw.svg),skills=raw.skills===undefined?c.skills.map(name=>c.customSkills?.find(s=>s.name===name)||Battle.skillFor(name)):Battle.skillsFor(raw);
 const elements=raw.elements===undefined?c.elements:raw.elements,base=raw.stats===undefined?c.base:Battle.statsFor(raw.stats);
 if(!svg||!skills||skills.length!==4||skills.some(s=>!s)||!base||!Array.isArray(elements)||elements.length<1||elements.length>2||elements.some(t=>!Object.hasOwn(Battle.TYPES,t)))fail('设计没有通过形象、属性或技能校验，原设计已保留。');
 const moves=skills.map(s=>s.name),actions=Battle.actionsFor({...c.actions,...raw.actions},moves,skills);
 return{name:raw.name?String(raw.name).slice(0,40):c.name,description:raw.description===undefined?c.description||'':String(raw.description).slice(0,600),svg,elements:[...new Set(elements)],base,skills:moves,customSkills:skills,actions,designer:agent};
}
class Garden{
 constructor(store){this.store=store;if(!store.data.garden)this.store.update(d=>{d.garden={players:{},settled:[]};});}
 ensure(id){if(!Object.hasOwn(this.store.data.garden.players,id))this.store.update(d=>{d.garden.players[id]=initial();});return this.store.data.garden.players[id];}
 view(id){const p=this.ensure(id);return{coins:p.coins,balls:p.balls,potions:p.potions,creatures:p.creatures.map(publicCreature),selected:p.selected,team:(p.team||[p.selected]).filter(id=>p.creatures.some(c=>c.id===id)),encounter:p.encounter,notice:p.notice,species:Object.entries(SPECIES).map(([id,s])=>({id,...s,svg:art(id)})),levelCap:LEVEL_CAP};}
 act(id,action,body={}){
  this.ensure(id);const key=typeof body.requestId==='string'?body.requestId.slice(0,100):'';
  if(key&&this.store.data.garden.players[id].requests.includes(key))return this.view(id);
  this.store.update(d=>{
   const p=d.garden.players[id];let c=p.creatures.find(c=>c.id===(body.creature||p.selected));
   const pay=amount=>{if(p.coins<amount)fail('星币不足，去探险获得奖励吧。');p.coins-=amount;};
   if(action==='adopt'){
    if(!SPECIES[body.species])fail('请选择一位伙伴。');if(p.creatures.length>=12)fail('伙伴栏最多容纳 12 位伙伴。');
    if(p.creatures.length)pay(25);c=creature(body.species);p.creatures.push(c);p.selected=c.id;p.notice=c.name+' 加入了你的队伍！';
   }else if(action==='team'){const ids=body.ids;if(!Array.isArray(ids)||!ids.length||ids.length>3||new Set(ids).size!==ids.length||ids.some(id=>!p.creatures.some(c=>c.id===id)))fail('选择自己背包里 1–3 只不同精灵组成队伍。');p.team=[...ids];p.notice='参战队伍已保存。';
   }else if(action==='select'){if(!c)fail('没有这位伙伴。');if(p.encounter&&!p.encounter.done)fail('先完成当前探险。');p.selected=c.id;p.notice='出战伙伴：'+c.name;}
   else if(action==='supplies'){pay(12);p.balls+=2;p.potions++;p.notice='补给已装好：结契球 ×2、恢复药 ×1。';}
   else{
    if(!c)fail('先领养伙伴，或让 Agent 在竞技场设计精灵。');
    if(['train','feed','rest','learn','encounter'].includes(action)&&p.encounter&&!p.encounter.done)fail('先完成当前探险。');
    if(action==='train'){
     const focus=body.focus||'attack';if(!keys.includes(focus))fail('训练方向无效。');if(c.energy<10)fail('伙伴累了，先休息一下。');pay(8);c.energy-=10;c.training[focus]=Math.min(20,c.training[focus]+2);c.bond=Math.min(100,c.bond+2);
     const levels=gain(c,35);p.notice='训练完成，经验 +35'+(levels?'，升到了 '+c.level+' 级！':'。');
    }else if(action==='feed'){pay(6);c.energy=Math.min(100,c.energy+25);c.bond=Math.min(100,c.bond+8);p.notice=c.name+' 很喜欢点心，亲密度 +8。';}
    else if(action==='rest'){c.energy=100;p.notice='伙伴休息好了，体力已恢复。';}
    else if(action==='learn'){if(!unlocked(c).includes(body.move)||!Number.isInteger(body.slot)||body.slot<0||body.slot>3)fail('还不能学习这项技能。');if(c.skills.includes(body.move)&&c.skills[body.slot]!==body.move)fail('已经掌握这项技能。');c.skills[body.slot]=body.move;p.notice='学会了 '+body.move+'。';}
    else if(action==='encounter'){
     if(c.energy<8)fail('先让伙伴休息一下。');c.energy-=8;p.selected=c.id;
     const names=Object.keys(SPECIES),wild=creature(names[crypto.randomInt(names.length)],Math.max(1,c.level-1+crypto.randomInt(3))),maxHp=55+wild.level*9;
     p.encounter={id:crypto.randomUUID(),creature:c.id,wild:publicCreature(wild),wildHp:maxHp,wildMaxHp:maxHp,hp:stats(c).hp,maxHp:stats(c).hp,pp:c.skills.map(name=>['guard','heal'].includes((c.customSkills?.find(s=>s.name===name)||Battle.skillFor(name))?.type)?3:12),turn:1,done:false,events:[],log:['在星光草原遇到了 '+wild.name+'！']};p.encounter.rules=4;const make=(pet,hp)=>Combat.init({hp,maxHp:hp,stats:pet.stats,elements:pet.elements,skills:pet.skills.map(name=>pet.customSkills?.find(s=>s.name===name)||Battle.skillFor(name))});p.encounter.combat={player:make(publicCreature(c),stats(c).hp),wild:make(publicCreature(wild),maxHp)};p.notice='探险开始了。';
    }else{
     const e=p.encounter;if(!e||e.done||e.creature!==c.id)fail('当前没有进行中的探险。');let guard=false;e.events=[];
     if(action==='move'){
      const slot=body.slot;if(!Number.isInteger(slot)||slot<0||slot>3||e.pp[slot]<=0)fail('选择一项还有次数的技能。');
      const name=c.skills[slot],move=c.customSkills?.find(s=>s.name===name)||Battle.skillFor(name);if(!move)fail('技能无效。');e.pp[slot]--;const before=e.wildHp;
      if(e.rules>=4){const actor=e.combat.player,target=e.combat.wild;actor.hp=e.hp;target.hp=e.wildHp;const result=Combat.act(actor,target,move,Battle.effectiveness);e.hp=actor.hp;e.wildHp=target.hp;e.pp=c.skills.map(name=>actor.pp[name]);e.log.push(c.name+' 使用 '+name+'，'+result.text+'。');}
      else if(move.type==='guard'){guard=true;e.log.push(c.name+' 进入防守。');}
      else if(move.type==='heal'){e.hp=Math.min(e.maxHp,e.hp+move.power);e.log.push(c.name+' 恢复了生命。');}
      else{const hit=Math.max(0,Math.round(move.power*Battle.effectiveness(move.type,e.wild.elements)*stats(c).attack/e.wild.stats.defense));e.wildHp=Math.max(0,e.wildHp-hit);e.log.push(c.name+' 使用 '+name+'，造成 '+hit+' 点伤害。');}
      e.events.push({id:e.id+':'+e.turn+':0',side:0,move:name,type:move.type,damage:before-e.wildHp,hp:e.hp,wildHp:e.wildHp});
     }else if(action==='potion'){if(!p.potions)fail('恢复药用完了。');p.potions--;e.hp=Math.min(e.maxHp,e.hp+45);e.log.push('使用恢复药，恢复 45 点生命。');}
     else if(action==='catch'){
      if(!p.balls)fail('结契球用完了。');if(p.creatures.length>=12)fail('伙伴栏已满。');p.balls--;
      if(crypto.randomInt(100)<(e.wildHp/e.wildMaxHp<=.35?80:25)){
       const caught=creature(e.wild.species,e.wild.level);p.creatures.push(caught);e.done=true;e.result='caught';e.log.push(caught.name+' 愿意成为你的伙伴！');
      }else e.log.push('结契失败了，先削弱对方再试试。');
     }else if(action==='retreat'){e.done=true;e.result='retreated';e.log.push('安全返回基地。');}
     else fail('没有这项操作。');
     if(e.wildHp===0&&!e.done){e.done=true;e.result='won';const reward=35+e.wild.level*8,levels=gain(c,reward);p.coins+=18;e.log.push('胜利！经验 +'+reward+'，星币 +18'+(levels?'；升到 '+c.level+' 级！':'。'));}
     if(!e.done){const move=Battle.skillFor(e.wild.skills[e.turn%2]);const before=e.hp;let text;if(e.rules>=4){e.combat.player.hp=e.hp;e.combat.wild.hp=e.wildHp;const result=Combat.act(e.combat.wild,e.combat.player,move,Battle.effectiveness);e.hp=e.combat.player.hp;e.wildHp=e.combat.wild.hp;text=result.text;}else{const hit=Math.max(1,Math.round((move?.power||14)*Battle.effectiveness(move?.type||'normal',c.elements)*e.wild.stats.attack/stats(c).defense*(guard ? .35 : 1)));e.hp=Math.max(0,e.hp-hit);text='造成 '+hit+' 点伤害';}e.log.push(e.wild.name+' 使用 '+(move?.name||'撞击')+' 反击，'+text+'。');e.events.push({id:e.id+':'+e.turn+':1',side:1,move:move?.name||'撞击',type:move?.type||'normal',damage:before-e.hp,hp:e.hp,wildHp:e.wildHp});if(e.hp===0){e.done=true;e.result='lost';gain(c,12);p.coins+=5;e.log.push('暂时撤退。获得经验 +12、星币 +5。');}}
     e.turn++;e.log=e.log.slice(-8);p.notice=e.log.at(-1);
    }
   }
   if(key){p.requests.push(key);p.requests=p.requests.slice(-50);}
  });return this.view(id);
 }
 levelFor(agent){for(const p of Object.values(this.store.data.garden.players)){const c=p.creatures.find(c=>c.agent===agent);if(c)return{level:c.level,growth:c.training};}return{level:1,growth:{}};}
 design(owner,id,raw,agent){
  const p=this.ensure(owner),c=p.creatures.find(c=>c.id===id);if(!c)fail('只能设计自己背包里的精灵。');
  if(p.encounter&&!p.encounter.done)fail('先完成当前探险，再修改设计。');
  const fields=designFields(c,raw,agent);
  this.store.update(d=>{const pet=d.garden.players[owner].creatures.find(c=>c.id===id);
   if(pet.agent&&!pet.arenaSource)pet.arenaSource=pet.svg;
   Object.assign(pet,fields);
   d.garden.players[owner].notice='新设计已保存，等级和培养进度保持不变。';});
 }
 create(owner,raw,agent){
  const p=this.ensure(owner);if(p.creatures.length>=12)fail('精灵背包已满，最多保留 12 位伙伴。');
  if(typeof raw.name!=='string'||!raw.name.trim()||!raw.svg||!raw.skills||typeof raw.description!=='string'||!raw.description.trim())fail('新精灵需要名字、介绍、SVG 形象和四项技能。');
  const c={...creature('spark'),species:null};Object.assign(c,designFields(c,raw,agent));
  this.store.update(d=>{const player=d.garden.players[owner];player.creatures.push(c);player.selected=c.id;player.notice='AI Agent 创作的新精灵「'+c.name+'」已加入背包。';});return c.id;
 }
 capture(owner,agent,fighter){
  this.ensure(owner);const prior=this.store.data.garden.players[owner].creatures.find(c=>c.agent===agent);
  if((prior?.arenaSource||prior?.svg)===fighter.svg&&JSON.stringify(prior.arenaSkills||prior.customSkills)===JSON.stringify(fighter.skills))return;
  this.store.update(d=>{const p=d.garden.players[owner];let c=p.creatures.find(c=>c.agent===agent);if(!c){c={...creature('spark'),species:null,agent};p.creatures.push(c);if(!p.selected)p.selected=c.id;}
   Object.assign(c,{name:fighter.name,svg:fighter.svg,arenaSource:fighter.svg,arenaSkills:fighter.skills,elements:fighter.elements,base:fighter.baseStats||fighter.stats,skills:fighter.moves,customSkills:fighter.skills,actions:fighter.actions||{}});});
 }
 reward(match,state,owners){
  if(state.winner===null||this.store.data.garden.settled.includes(match))return;
  this.store.update(d=>{d.garden.settled.push(match);for(let side=0;side<2;side++){const p=d.garden.players[owners[side]],fighter=state.fighters[side];if(!p)continue;const ids=(state.teams?.[side]||[fighter]).map(f=>f.creature);for(const c of p.creatures.filter(c=>ids.includes(c.id)||!fighter.creature&&c.agent===fighter.agent?.key))gain(c,side===state.winner?60:25);p.coins+=side===state.winner?20:8;p.notice='竞技场结算：经验 +'+(side===state.winner?60:25)+'。';}});
 }
}
module.exports={Garden,SPECIES,art,stats,gain};
