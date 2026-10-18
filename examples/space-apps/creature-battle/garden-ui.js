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
 let data=null,selected='',tab='partners',busy=false,problem='',signature='',presenting=false,displayHp=null,generation=0;
 const button=(text,action,body={},cls='')=>{const b=el('button','garden-button '+cls,text);b.type='button';b.disabled=busy;b.onclick=()=>act(action,body);return b;};
 async function act(action,body={}){
  const before=data?.encounter?{hp:data.encounter.hp,wildHp:data.encounter.wildHp,turn:data.encounter.turn}:null;
  const moving=data?.creatures.find(c=>c.id===data.encounter?.creature),name=action==='move'?moving?.skills[body.slot]:'';
  if(busy)return;busy=true;problem='';render();
  try{const target=['move','catch','potion','retreat'].includes(action)?data.encounter?.creature:selected;
   data=await api('garden/'+action,{creature:target,requestId:crypto.randomUUID(),...body});if(['adopt','select','encounter'].includes(action))selected=data.selected||selected;if(action==='encounter')tab='journey';
   if(before&&['move','catch','potion'].includes(action)){
    // Older running hosts return the authoritative HP/turn but no animation events.
    // Derive visuals from those results so a UI refresh does not require restarting a room.
    const e=data.encounter;if(e&&!Array.isArray(e.events)&&e.turn>before.turn){const events=[],skill=moving?.customSkills?.find(s=>s.name===name)||ClawCrossBattle.skillFor(name);
     if(name)events.push({side:0,move:name,type:skill?.type||'normal',damage:before.wildHp-e.wildHp,hp:skill?.type==='heal'?Math.min(e.maxHp,before.hp+skill.power):before.hp,wildHp:e.wildHp});
     if(!['won','caught','retreated'].includes(e.result)){const move=ClawCrossBattle.skillFor(e.wild.skills[before.turn%2]);events.push({side:1,move:move?.name||'撞击',type:move?.type||'normal',damage:Math.max(0,(events[0]?.hp??before.hp)-e.hp),hp:e.hp,wildHp:e.wildHp});}e.events=events;}
    await playTurn(before);
   }}
  catch(error){problem=error.message;}finally{busy=false;presenting=false;displayHp=null;signature='';render();options.onUpdate?.(data);}
 }
 async function preview(c,name,side=0,action=null){
  const stage=overlay.querySelector('.garden-encounter')||overlay.querySelector('.garden-portrait');if(!stage)return;
  const skill=c.customSkills?.find(s=>s.name===name)||ClawCrossBattle.skillFor(name)||{type:action?.type||'normal'},own=c.actions?.[name],custom=ClawCrossBattle.safeSvg(own?.effect,true),reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
  stage.querySelectorAll('.garden-effect').forEach(n=>n.remove());const effect=el('img','garden-effect');effect.alt=c.name+' · '+name+' 动画';effect.dataset.side=String(side);effect.dataset.move=name;effect.dataset.own=custom?'1':'0';
  let field;if(!reduce&&own?.domain){field=el('img','garden-effect garden-domain');field.alt='领域效果';field.dataset.domain=own.domain.style;field.src='data:image/svg+xml;charset=UTF-8,'+encodeURIComponent(ClawCrossBattle.domainSvg(own.domain));stage.append(field);}
  if(!reduce){effect.src='data:image/svg+xml;charset=UTF-8,'+encodeURIComponent(custom||ClawCrossBattle.effectSvg(skill,side));if(custom&&side===1)effect.style.transform='scaleX(-1)';stage.append(effect);}
  const caster=stage.querySelector('[data-side="'+side+'"]')||stage,image=caster.querySelector('img:not(.garden-effect)'),motion=own?.motion||(['guard','heal'].includes(skill.type)?'pulse':'lunge'),toward=side?-24:24;
  if(image&&!reduce)image.animate(motion==='jump'?[{transform:'translateY(0)'},{transform:'translateY(-22px)'},{transform:'translateY(0)'}]:motion==='spin'?[{transform:'rotate(0)'},{transform:'rotate(360deg)'}]:motion==='lunge'?[{transform:'translateX(0)'},{transform:'translateX('+toward+'px) scale(1.06)'},{transform:'translateX(0)'}]:[{transform:'scale(1)'},{transform:'scale(1.12)'},{transform:'scale(1)'}],{duration:700});
  const status=overlay.querySelector('.garden-turn-status');if(status)status.textContent=c.name+' 使用 '+name;
  await new Promise(done=>setTimeout(done,reduce?70:350));
  if(action&&stage.isConnected){
   displayHp=[action.hp,action.wildHp];stage.querySelectorAll('.garden-opponent').forEach((pet,index)=>{const value=displayHp[index],max=index?data.encounter.wildMaxHp:data.encounter.maxHp;pet.querySelector('.garden-meter>span').style.width=Math.max(0,value/max*100)+'%';pet.querySelector('.garden-hp').textContent=value+' / '+max;});
   const target=stage.querySelector('[data-side="'+(1-side)+'"]');if(action.damage>0&&target){const number=el('span','garden-damage','−'+action.damage);target.append(number);if(!reduce)target.querySelector('img').animate([{filter:'brightness(1)'},{filter:'brightness(1.9)',transform:'translateX('+(side?'-5':'5')+'px)'},{filter:'brightness(1)',transform:'translateX(0)'}],{duration:350});setTimeout(()=>number.remove(),900);}
  }
  await new Promise(done=>setTimeout(done,reduce?70:550));effect.remove();field?.remove();
 }
 async function playTurn(before){
  const encounter=data.encounter,token=generation;if(overlay.hidden||tab!=='journey'||!encounter?.events?.length)return;
  presenting=true;displayHp=[before.hp,before.wildHp];render();
  for(const action of encounter.events){if(token!==generation||overlay.hidden||tab!=='journey')break;const c=action.side?encounter.wild:data.creatures.find(c=>c.id===encounter.creature);await preview(c,action.move,action.side,action);}
 }
 function meter(value,max,cls=''){const bar=el('div','garden-meter '+cls),fill=el('span');fill.style.width=Math.max(0,Math.min(100,value/max*100))+'%';bar.append(fill);return bar;}
 function render(){
  if(overlay.hidden||!data)return;
  const scroll=overlay.dataset.view===tab?overlay.querySelector('.garden-scroll')?.scrollTop||0:0;overlay.dataset.view=tab;
  const head=el('header','garden-head'),title=el('div');title.append(el('small','','YOUR COMPANIONS'),el('h2','',tab==='journey'?'星光草原':tab==='training'?'精灵培养':'精灵背包'));
  const wallet=el('span','garden-wallet','✦ '+data.coins+' 星币'),back=el('button','garden-back',options.onClose?'返回首页':'返回竞技场');back.type='button';back.onclick=()=>{overlay.hidden=true;if(options.onClose)options.onClose();else open.focus({preventScroll:true});};head.append(title,wallet,back);
  const nav=el('nav','garden-tabs');nav.setAttribute('aria-label','背包功能');nav.hidden=options.toolbar===false&&tab==='journey';for(const [id,label]of[['partners','伙伴'],['training','培养'],...(options.toolbar!==false?[['journey','探索']]:[])]){const b=el('button',tab===id?'active':'',label);b.type='button';b.setAttribute('aria-pressed',String(tab===id));b.onclick=()=>{generation++;tab=id;render();};nav.append(b);}
  const body=el('div','garden-scroll');
  if(tab==='partners'&&options.onCreate){const create=el('section','garden-create-card'),copy=el('div');copy.append(el('strong','','AI Agent 设计你的新精灵'),el('p','','形象、四个技能、动画与介绍，一起创造。'));const b=el('button','garden-button primary','新建精灵');b.onclick=()=>options.onCreate();create.append(copy,b);body.append(create);}
  let c=data.creatures.find(c=>c.id===selected)||data.creatures.find(c=>c.id===data.selected)||data.creatures[0];if(c)selected=c.id;
  if(!data.creatures.length){
   const welcome=el('div','garden-intro');welcome.append(el('small','','第一位伙伴'),el('h3','','从一次相遇开始'),el('p','','选择你的初始伙伴。训练、探索、结契，让它陪你一起成长。'));body.append(welcome);
   const choices=el('div','garden-starters');for(const s of data.species){const card=el('article','garden-starter');card.append(icon(s.svg,s.name),el('h3','',s.name),el('p','',ClawCrossBattle.TYPES[s.element].name+'属性'),button('选择 '+s.name,'adopt',{species:s.id}));choices.append(card);}body.append(choices);
  }else if(tab==='journey'){
   const e=data.encounter;
   if(!e||e.done&&!presenting){
    const intro=el('section','garden-expedition');intro.append(el('small','','星光草原'),el('h3','',e?.result==='won'?'满载而归':e?.result==='caught'?'新伙伴，欢迎回来':'下一段旅途就在前方'),el('p','',e?e.log.at(-1):'遇见野生精灵，获得经验与星币；削弱对方后，试着与它结契。'),icon(c.svg,c.name),button('和 '+c.name+' 出发','encounter',{},'primary'));body.append(intro);
   }else{
    c=data.creatures.find(c=>c.id===e.creature);
    const stage=el('div','garden-encounter');for(const [index,[pet,hp,max,side]]of[[c,displayHp?.[0]??e.hp,e.maxHp,'伙伴'],[e.wild,displayHp?.[1]??e.wildHp,e.wildMaxHp,'野生']].entries()){const fighter=el('article','garden-opponent');fighter.dataset.side=String(index);fighter.append(el('small','',side+' · Lv.'+pet.level),el('h3','',pet.name),meter(hp,max),el('p','garden-hp',hp+' / '+max),icon(pet.svg,pet.name));stage.append(fighter);}body.append(stage);const turn=el('p','garden-turn-status',presenting?'正在出招…':'第 '+e.turn+' 回合 · 选择技能');turn.setAttribute('role','status');body.append(turn);
    const moves=el('div','garden-moves');for(let i=0;i<c.skills.length;i++){const b=button(c.skills[i]+'  '+e.pp[i]+'次','move',{slot:i},'skill');b.disabled=busy||!e.pp[i];moves.append(b);}body.append(moves);
    const tools=el('div','garden-tools');tools.append(button('结契球 · '+data.balls,'catch'),button('恢复药 · '+data.potions,'potion'),button('返回基地','retreat'));body.append(tools);
    const log=el('div','garden-battle-log');for(const line of e.log.slice(-3))log.append(el('p','',line));body.append(log);
   }
  }else{
   const team=el('div','garden-team');for(const pet of data.creatures){const b=el('button','garden-companion'+(pet.id===selected?' selected':''));b.type='button';b.setAttribute('aria-pressed',String(pet.id===selected));b.append(icon(pet.svg,pet.name),el('span','',pet.name),el('small','','Lv.'+pet.level));b.onclick=()=>{selected=pet.id;render();};team.append(b);}
   let detail=body,trainingPanel=null;if(tab==='partners'){const layout=el('div','garden-bag-layout'),collection=el('section','garden-collection');collection.append(el('h3','','我的伙伴 · '+data.creatures.length),team);detail=el('section','garden-detail');layout.append(collection,detail);body.append(layout);}else{if(data.creatures.length>1)body.append(team);const layout=el('div','garden-training-layout');detail=el('section','garden-training-profile');trainingPanel=el('section','garden-training-plan');layout.append(detail,trainingPanel);body.append(layout);}
   const hero=el('section','garden-hero'),portrait=el('div','garden-portrait');portrait.append(icon(c.svg,c.name));
   const about=el('div','garden-about');about.append(el('small','',types(c)+' · '+['初始形态','成长形态','共鸣形态'][c.form]),el('h3','',c.name),el('p','garden-description',c.description||(c.agent||c.designer?'由 AI Agent 为你创作的伙伴':'星光草原的伙伴')),el('strong','','Lv.'+c.level),meter(c.xp,c.nextXp,'experience'),el('small','','经验 '+c.xp+' / '+c.nextXp));hero.append(portrait,about);detail.append(hero);
   const condition=el('div','garden-condition');for(const[label,value,cls]of[['体力',c.energy,'energy'],['亲密',c.bond,'bond']]){const item=el('div');item.append(el('span','',label),el('strong','',value+' / 100'),meter(value,100,cls));condition.append(item);}detail.append(condition);
   const vitals=el('div','garden-vitals');for(const[key,label]of[['hp','生命'],['attack','攻击'],['defense','防御'],['speed','速度']]){const item=el('div');item.append(el('small','',label),el('strong','',String(c.stats[key])));vitals.append(item);}detail.append(vitals);
   if(tab==='training'){
    trainingPanel.append(el('small','garden-plan-eyebrow','一起变得更强'),el('h3','garden-plan-title','今天，练一点什么？'),el('p','garden-note','每次训练获得 35 经验，消耗 10 体力。'));
    const training=el('div','garden-training');for(const[focus,label,stat,symbol]of[['hp','耐力','生命','♡'],['attack','力量','攻击','↗'],['defense','防守','防御','⬡'],['speed','敏捷','速度','ϟ']]){const b=button(label+'训练 · 8星币','train',{focus}),growth=c.training?.[focus]||0;b.setAttribute('aria-label',label+'训练 · 8星币');b.dataset.focus=focus;b.disabled=busy||data.coins<8||c.energy<10||growth>=20||!!data.encounter&&!data.encounter.done;b.replaceChildren(el('span','garden-training-symbol',symbol),el('strong','',label),el('span','garden-training-gain',stat+'成长 +2'),meter(growth,20,'growth'),el('small','','专项成长 '+growth+' / 20'),el('span','garden-training-cost',growth>=20?'已练满':c.energy<10?'先休息恢复体力':'8 星币 · 训练 →'));training.append(b);}trainingPanel.append(training);
    const actions=el('div','garden-care');const feed=button('喂点心 · 6星币','feed'),rest=button('休息恢复体力','rest');feed.disabled=busy||data.coins<6||!!data.encounter&&!data.encounter.done;rest.disabled=busy||!!data.encounter&&!data.encounter.done;feed.append(el('small','','体力 +25 · 亲密 +8'));rest.append(el('small','','免费 · 体力恢复至 100'));actions.append(feed,rest);detail.append(actions);
    const guide=el('details','garden-growth-guide');guide.append(el('summary','','成长与技能解锁'),el('p','','等级 3 / 5 / 10 解锁新招式；等级 5 / 10 进入新形态。训练成长各有 20 点上限，等级最高 '+data.levelCap+'。'));trainingPanel.append(guide);
   }else{
    const actions=el('div','garden-tools');actions.append(button(data.selected===c.id?'当前出战伙伴':'设为出战伙伴','select'),button('补充旅行物资 · 12星币','supplies'));const roster=data.team||[data.selected],inTeam=roster.includes(c.id),squad=button(inTeam?'移出参战队伍':'加入参战队伍','team',{ids:inTeam?roster.filter(id=>id!==c.id):[...roster,c.id]});squad.disabled=busy||inTeam&&roster.length<=1||!inTeam&&roster.length>=3;actions.append(squad);if(options.onDesign){const design=el('button','garden-button','请设计师改造');design.onclick=()=>options.onDesign(c);actions.append(design);}detail.append(actions);
    detail.append(el('h3','garden-section-title','技能 · 点击预览动画'));const previews=el('div','garden-moves');for(const name of c.skills){const b=el('button','garden-button skill',name);b.onclick=()=>preview(c,name);previews.append(b);}detail.append(previews,el('h3','garden-section-title','已解锁技能'));
    const skills=el('div','garden-learn');for(const name of c.unlocked){const row=el('div');row.append(el('span','',name));if(c.skills.includes(name))row.append(el('small','','已装备'));else{const slot=el('select');slot.setAttribute('aria-label',name+' 替换技能');c.skills.forEach((value,i)=>slot.append(new Option('替换 '+value,String(i))));const learn=button('学习','learn');learn.onclick=()=>act('learn',{move:name,slot:Number(slot.value)});row.append(slot,learn);}skills.append(row);}detail.append(skills);
    const more=el('details','garden-more');more.append(el('summary','','结识更多伙伴 · 25星币'));for(const s of data.species)more.append(button('结识 '+s.name,'adopt',{species:s.id}));detail.append(more);
   }
  }
  const notice=el('footer','garden-notice'+(problem?' error':''),presenting?'双方正在出招…':busy?'正在处理…':problem||data.notice);notice.setAttribute('role','status');
  overlay.replaceChildren(head,nav,body,notice);
  body.scrollTop=scroll;
 }
 const show=async(view='partners',id='')=>{const token=++generation;tab=view;if(id)selected=id;overlay.hidden=false;try{const next=await api('garden');if(token!==generation||overlay.hidden)return;data=next;render();}catch(error){if(token!==generation)return;overlay.replaceChildren(el('p','',error.message));const back=el('button','','关闭');back.onclick=()=>{overlay.hidden=true;options.onClose?.();};overlay.append(back);}};
 open.onclick=()=>show();
 return{open:show,close(){generation++;overlay.hidden=true;},selected(){return selected;},update(next){if(!next)return;const value=JSON.stringify(next);if(value===signature)return;signature=value;data=next;if(!busy)render();}};
}
root.ClawCrossGarden={mount};
})(globalThis);
