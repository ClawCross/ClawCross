/* A compact companion base: grow, train and explore, with all outcomes decided by the server. */
(function(root){
'use strict';
const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!=null)node.textContent=text;return node;};
const icon=(svg,name)=>{const image=el('img');image.alt=name||'';image.src='data:image/svg+xml;charset=UTF-8,'+encodeURIComponent(svg);return image;};
const types=c=>c.elements.map(id=>ClawCrossBattle.TYPES[id]?.name||id).join(' / ');
function mount(container,api,options={}){
 const open=el('button','cc-room-btn secondary','精灵基地');open.type='button';open.dataset.garden='';
 if(options.toolbar!==false)container.querySelector('.cc-room-head > div:last-child').prepend(open);
 const overlay=el('section','cc-garden');overlay.hidden=true;overlay.setAttribute('role','region');overlay.setAttribute('aria-label','精灵背包');(options.parent||document.body).append(overlay);
 let data=null,selected='',tab='partners',busy=false,problem='',signature='';
 const button=(text,action,body={},cls='')=>{const b=el('button','garden-button '+cls,text);b.type='button';b.disabled=busy;b.onclick=()=>act(action,body);return b;};
 async function act(action,body={}){
  const moving=data?.creatures.find(c=>c.id===data.encounter?.creature),move=action==='move'?moving?.skills[body.slot]:'';
  if(busy)return;busy=true;problem='';render();
  try{const target=['move','catch','potion','retreat'].includes(action)?data.encounter?.creature:selected;
   data=await api('garden/'+action,{creature:target,requestId:crypto.randomUUID(),...body});selected=data.selected||selected;if(action==='encounter')tab='journey';}
  catch(error){problem=error.message;}finally{busy=false;signature='';render();options.onUpdate?.(data);if(move&&!problem)preview(moving,move);}
 }
 function preview(c,name){
  const stage=overlay.querySelector('.garden-encounter')||overlay.querySelector('.garden-portrait');if(!stage)return;
  stage.scrollIntoView({block:'nearest'});
  const skill=c.customSkills?.find(s=>s.name===name)||ClawCrossBattle.skillFor(name),own=c.actions?.[name];
  stage.querySelector('.garden-effect')?.remove();const effect=el('img','garden-effect');effect.alt=name+' 动画';effect.src='data:image/svg+xml;charset=UTF-8,'+encodeURIComponent(ClawCrossBattle.safeSvg(own?.effect,true)||ClawCrossBattle.effectSvg(skill||{type:'normal'},0));effect.dataset.own=own?.effect?'1':'0';stage.append(effect);
  const image=stage.querySelector('img:not(.garden-effect)'),motion=own?.motion||'pulse';image?.animate(motion==='jump'?[{transform:'translateY(0)'},{transform:'translateY(-18px)'},{transform:'translateY(0)'}]:motion==='spin'?[{transform:'rotate(0)'},{transform:'rotate(360deg)'}]:[{transform:'scale(1)'},{transform:'scale(1.08)'},{transform:'scale(1)'}],{duration:700});setTimeout(()=>effect.remove(),1500);
 }
 function meter(value,max,cls=''){const bar=el('div','garden-meter '+cls),fill=el('span');fill.style.width=Math.max(0,Math.min(100,value/max*100))+'%';bar.append(fill);return bar;}
 function render(){
  if(overlay.hidden||!data)return;
  const head=el('header','garden-head'),title=el('div');title.append(el('small','','YOUR COMPANIONS'),el('h2','',tab==='journey'?'星光草原':tab==='training'?'伙伴训练':'精灵背包'));
  const wallet=el('span','garden-wallet','✦ '+data.coins+' 星币'),back=el('button','garden-back',options.onClose?'返回首页':'返回竞技场');back.type='button';back.onclick=()=>{overlay.hidden=true;if(options.onClose)options.onClose();else open.focus({preventScroll:true});};head.append(title,wallet,back);
  const nav=el('nav','garden-tabs');for(const [id,label]of[['partners','伙伴'],['training','训练'],['journey','探险']]){const b=el('button',tab===id?'active':'',label);b.type='button';b.onclick=()=>{tab=id;render();};nav.append(b);}
  const body=el('div','garden-scroll');
  if(tab==='partners'&&options.onCreate){const create=el('section','garden-create-card'),copy=el('div');copy.append(el('strong','','AI Agent 设计你的新精灵'),el('p','','形象、四个技能、动画与介绍，一起创造。'));const b=el('button','garden-button primary','新建精灵');b.onclick=()=>options.onCreate();create.append(copy,b);body.append(create);}
  let c=data.creatures.find(c=>c.id===selected)||data.creatures.find(c=>c.id===data.selected)||data.creatures[0];if(c)selected=c.id;
  if(!data.creatures.length){
   const welcome=el('div','garden-intro');welcome.append(el('small','','第一位伙伴'),el('h3','','从一次相遇开始'),el('p','','选择你的初始伙伴。训练、探索、结契，让它陪你一起成长。'));body.append(welcome);
   const choices=el('div','garden-starters');for(const s of data.species){const card=el('article','garden-starter');card.append(icon(s.svg,s.name),el('h3','',s.name),el('p','',ClawCrossBattle.TYPES[s.element].name+'属性'),button('选择 '+s.name,'adopt',{species:s.id}));choices.append(card);}body.append(choices);
  }else if(tab==='journey'){
   const e=data.encounter;
   if(!e||e.done){
    const intro=el('section','garden-expedition');intro.append(el('small','','星光草原'),el('h3','',e?.result==='won'?'满载而归':e?.result==='caught'?'新伙伴，欢迎回来':'下一段旅途就在前方'),el('p','',e?e.log.at(-1):'遇见野生精灵，获得经验与星币；削弱对方后，试着与它结契。'),icon(c.svg,c.name),button('和 '+c.name+' 出发','encounter',{},'primary'));body.append(intro);
   }else{
    c=data.creatures.find(c=>c.id===e.creature);
    const stage=el('div','garden-encounter');for(const [pet,hp,max,side]of[[c,e.hp,e.maxHp,'伙伴'],[e.wild,e.wildHp,e.wildMaxHp,'野生']]){const fighter=el('article','garden-opponent');fighter.append(el('small','',side+' · Lv.'+pet.level),el('h3','',pet.name),meter(hp,max),el('p','garden-hp',hp+' / '+max),icon(pet.svg,pet.name));stage.append(fighter);}body.append(stage);
    const moves=el('div','garden-moves');for(let i=0;i<c.skills.length;i++){const b=button(c.skills[i]+'  '+e.pp[i]+'次','move',{slot:i},'skill');b.disabled=busy||!e.pp[i];moves.append(b);}body.append(moves);
    const tools=el('div','garden-tools');tools.append(button('结契球 · '+data.balls,'catch'),button('恢复药 · '+data.potions,'potion'),button('返回基地','retreat'));body.append(tools);
    const log=el('div','garden-battle-log');for(const line of e.log.slice(-3))log.append(el('p','',line));body.append(log);
   }
  }else{
   const team=el('div','garden-team');for(const pet of data.creatures){const b=el('button','garden-companion'+(pet.id===selected?' selected':''));b.type='button';b.setAttribute('aria-pressed',String(pet.id===selected));b.append(icon(pet.svg,pet.name),el('span','',pet.name),el('small','','Lv.'+pet.level));b.onclick=()=>{selected=pet.id;render();};team.append(b);}
   let detail=body;if(tab==='partners'){const layout=el('div','garden-bag-layout'),collection=el('section','garden-collection');collection.append(el('h3','','我的伙伴 · '+data.creatures.length),team);detail=el('section','garden-detail');layout.append(collection,detail);body.append(layout);}else body.append(team);
   const hero=el('section','garden-hero'),portrait=el('div','garden-portrait');portrait.append(icon(c.svg,c.name));
   const about=el('div','garden-about');about.append(el('small','',types(c)+' · '+['初始形态','成长形态','共鸣形态'][c.form]),el('h3','',c.name),el('p','garden-description',c.description||(c.agent||c.designer?'由 AI Agent 为你创作的伙伴':'星光草原的伙伴')),el('strong','','Lv.'+c.level),meter(c.xp,c.nextXp,'experience'),el('small','','经验 '+c.xp+' / '+c.nextXp));hero.append(portrait,about);detail.append(hero);
   const vitals=el('div','garden-vitals');vitals.append(el('span','','体力 '+c.energy+'/100'),el('span','','亲密度 '+c.bond+'/100'));for(const[key,label]of[['hp','生命'],['attack','攻击'],['defense','防御'],['speed','速度']])vitals.append(el('span','',label+' '+c.stats[key]));detail.append(vitals);
   if(tab==='training'){
    body.append(el('h3','garden-section-title','今天一起练什么？'));
    const training=el('div','garden-training');for(const[focus,label]of[['hp','耐力'],['attack','力量'],['defense','防守'],['speed','敏捷']])training.append(button(label+'训练 · 8星币','train',{focus}));body.append(training,el('p','garden-note','每次获得 35 经验、2 点专项成长；等级 3 / 5 / 10 解锁新招式，等级 5 / 10 进入新形态。'));
    const actions=el('div','garden-tools');actions.append(button('喂点心 · 6星币','feed'),button('休息恢复体力','rest'));body.append(actions);
   }else{
    const actions=el('div','garden-tools');actions.append(button(data.selected===c.id?'当前出战伙伴':'设为出战伙伴','select'),button('补充旅行物资 · 12星币','supplies'));if(options.onDesign){const design=el('button','garden-button','请设计师改造');design.onclick=()=>options.onDesign(c);actions.append(design);}detail.append(actions);
    detail.append(el('h3','garden-section-title','技能 · 点击预览动画'));const previews=el('div','garden-moves');for(const name of c.skills){const b=el('button','garden-button skill',name);b.onclick=()=>preview(c,name);previews.append(b);}detail.append(previews,el('h3','garden-section-title','已解锁技能'));
    const skills=el('div','garden-learn');for(const name of c.unlocked){const row=el('div');row.append(el('span','',name));if(c.skills.includes(name))row.append(el('small','','已装备'));else{const slot=el('select');slot.setAttribute('aria-label',name+' 替换技能');c.skills.forEach((value,i)=>slot.append(new Option('替换 '+value,String(i))));const learn=button('学习','learn');learn.onclick=()=>act('learn',{move:name,slot:Number(slot.value)});row.append(slot,learn);}skills.append(row);}detail.append(skills);
    const more=el('details','garden-more');more.append(el('summary','','结识更多伙伴 · 25星币'));for(const s of data.species)more.append(button('结识 '+s.name,'adopt',{species:s.id}));detail.append(more);
   }
  }
  const notice=el('footer','garden-notice'+(problem?' error':''),busy?'正在保存…':problem||data.notice);notice.setAttribute('role','status');
  overlay.replaceChildren(head,nav,body,notice);
 }
 const show=async(view='partners',id='')=>{tab=view;if(id)selected=id;overlay.hidden=false;try{data=await api('garden');render();}catch(error){overlay.replaceChildren(el('p','',error.message));const back=el('button','','关闭');back.onclick=()=>{overlay.hidden=true;options.onClose?.();};overlay.append(back);}};
 open.onclick=()=>show();
 return{open:show,close(){overlay.hidden=true;},selected(){return selected;},update(next){if(!next)return;const value=JSON.stringify(next);if(value===signature)return;signature=value;data=next;render();}};
}
root.ClawCrossGarden={mount};
})(globalThis);
