/* Deterministic combat shared by the arena, wild encounters and tower. No network or DOM. */
(function(root,factory){const value=factory();if(typeof module==='object'&&module.exports)module.exports=value;else root.ClawCrossCombat=value;})(globalThis,function(){
 'use strict';
 const limit=type=>type==='guard'?4:type==='heal'?3:12;
 function init(fighter){fighter.cooldowns={...(fighter.cooldowns||{})};fighter.pp={...(fighter.pp||{})};for(const skill of fighter.skills||[])if(skill&&typeof skill==='object'&&!Object.hasOwn(fighter.pp,skill.name))fighter.pp[skill.name]=limit(skill.type);return fighter;}
 function unavailable(fighter,skill){return fighter.hp<=0?'精灵已倒下，请换上替补。':(fighter.cooldowns?.[skill.type]||0)>0?'这项技能还在冷却。':(fighter.pp?.[skill.name]??limit(skill.type))<=0?'这项技能的次数已用完。':'';}
 function act(actor,target,skill,effectiveness){
  init(actor);init(target);const problem=unavailable(actor,skill);if(problem)throw new Error(problem);
  for(const key of Object.keys(actor.cooldowns))actor.cooldowns[key]=Math.max(0,actor.cooldowns[key]-1);
  actor.pp[skill.name]--;let damage=0,recovered=0,blocked=false,text;
  if(skill.type==='guard'){actor.guard=true;actor.cooldowns.guard=2;text='守住：抵挡下一次攻击，成功后下一招攻击增强 25%';}
  else if(skill.type==='heal'){recovered=Math.min(actor.maxHp-actor.hp,Math.max(skill.power,Math.round(actor.maxHp*.22)));actor.hp+=recovered;actor.cooldowns.heal=3;text='回复 '+recovered+' HP';}
  else{
   const multiplier=effectiveness(skill.type,target.elements);
   if(target.guard&&multiplier>0){target.guard=false;target.riposte=true;blocked=true;text='被守住挡下，对方获得反击增益';}
   else{damage=multiplier===0?0:Math.max(1,Math.round(skill.power*(actor.elements.includes(skill.type)?1.5:1)*multiplier*actor.stats.attack/target.stats.defense*(actor.riposte?1.25:1)));damage=Math.min(target.hp,damage);target.hp-=damage;text='造成 '+damage+' 点伤害'+(actor.riposte?' · 反击强化':'');}
   actor.riposte=false;
  }
  return{damage,recovered,blocked,text};
 }
 return{init,act,unavailable,limit};
});
