/* Players enter with their own saved companions. Designs are frozen for the match. */
'use strict';
const crypto=require('node:crypto'),Battle=require('./battle');
const fail=message=>{throw Object.assign(new Error(message),{status:409});};
class Arena{
 constructor(store,garden,roster,getConfig,setConfig,changed){Object.assign(this,{store,garden,roster,getConfig,setConfig,changed});if(!store.data.arenaChoices)store.update(d=>{d.arenaChoices={};});}
 ready(){return this.roster().members.filter(m=>!m.agent&&this.store.data.arenaChoices[m.id]).flatMap(m=>{const c=this.garden.view(m.id).creatures.find(c=>c.id===this.store.data.arenaChoices[m.id]);return c?[{key:m.id,name:m.name,creature:c}]:[];});}
 active(){const c=this.getConfig();if(!c?.companion_battle)return false;const state=new Battle.BattleModel().rebuild(c,this.roster().members.map(m=>({principal:m.id,name:m.name,is_agent:m.agent,device:m.device})),this.store.data.rows||[]);return state.started&&state.winner===null&&state.fighters.length===2&&state.fighters.every(f=>f.svg);}
 display(){const current=this.getConfig(),ready=this.ready();if(current?.companion_battle)return current;return{v:1,app:'battle',id:'companion-lobby',host:{key:'',name:''},players:ready.slice(0,2).map(p=>({key:p.key,name:p.name,controller:p.name})),companions:ready.slice(0,2).map(p=>p.creature),companion_battle:true,battle_rules:3,manual_start:true};}
 view(owner){const config=this.display();return{ready:this.ready(),selected:this.store.data.arenaChoices[owner]||'',fighters:config?.companions||[],active:this.active()};}
 choose(person,id){if(!this.roster().members.some(m=>m.id===person.id&&!m.agent))fail('只有玩家可以报名。');if(!this.garden.view(person.id).creatures.some(c=>c.id===id))fail('请选择自己背包里的精灵。');this.store.update(d=>{d.arenaChoices[person.id]=id;});this.changed();return this.view(person.id);}
 leave(person){this.store.update(d=>{delete d.arenaChoices[person.id];});this.changed();return this.view(person.id);}
 start(person,opponent){
  if(this.active())fail('本场对战尚未结束。');
  const ready=this.ready(),me=ready.find(p=>p.key===person.id),foe=opponent?ready.find(p=>p.key===opponent&&p.key!==person.id):ready.find(p=>p.key!==person.id);
  if(!me||!foe)fail('双方都需要先从自己的背包选择精灵参战。');
  const players=[me,foe],config={v:1,app:'battle',id:crypto.randomUUID(),host:{key:person.id,name:person.name},players:players.map(p=>({key:p.key,name:p.name,controller:p.name})),companions:structuredClone(players.map(p=>p.creature)),companion_battle:true,battle_rules:3,manual_start:false};
  this.store.update(d=>{for(const p of players)delete d.arenaChoices[p.key];});this.setConfig(config);this.changed();return{ok:true};
 }
}
module.exports={Arena};
