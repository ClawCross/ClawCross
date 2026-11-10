/* Tower navigation and story wrap the same combat view used by the arena. */
(function(root){
 'use strict';
 const el=(tag,cls,text)=>{const node=document.createElement(tag);node.className=cls||'';if(text!==undefined)node.textContent=text;return node;};
 function mount(parent,api,options){
  const host=el('section','cc-tower');host.hidden=true;host.setAttribute('aria-label','星塔冒险');parent.append(host);
  const head=el('header','tower-head'),title=el('div'),eyebrow=el('small','','COMPANION EXPEDITION'),name=el('h2','','星光之塔'),progress=el('span','tower-progress');title.append(eyebrow,name);head.append(title,progress);
  const story=el('p','tower-story'),controls=el('div','tower-controls'),deck=el('div','tower-deck'),stage=el('div','tower-battle'),checkpoint=el('div','tower-checkpoint'),notice=el('p','tower-notice');notice.setAttribute('role','status');host.append(head,story,controls,checkpoint,deck,stage,notice);
  let current=null,busy=false;
  let client,game;function ensureGame(){if(game)return;client=new ClawCrossRooms.RoomClient({tower:true,readyCompanion:()=>{},send:content=>{const event=ClawCrossRooms.parseEvent(content);return action({...event,requestId:event.id});},onComposer:()=>{}});game=ClawCrossBattle.mount(stage,client);}
  async function action(body){if(busy)return;busy=true;notice.textContent='';try{update(await api('tower/action',{requestId:crypto.randomUUID(),...body}));}catch(error){notice.textContent=error.message;throw error;}finally{busy=false;paint();}}
  function button(text,fn,primary=false){const b=el('button','garden-button'+(primary?' primary':''),text);b.type='button';b.disabled=busy;b.onclick=()=>Promise.resolve(fn()).catch(error=>notice.textContent=error.message);return b;}
  function paint(){
   if(!current)return;const run=current.run;controls.replaceChildren();name.textContent=run?run.title:'星光之塔';progress.textContent=run?'第 '+run.floor+' / 10 层 · 已通过 '+run.cleared+' 层':'三只伙伴 · 十层冒险';story.textContent=run?run.story:'带上自己的精灵队伍，挑战十层星塔。每五层有首领，每三层可休整。伙伴的生命和技能次数会延续到下一层。';
   const active=run&&!['complete','lost','abandoned'].includes(run.status);stage.hidden=!run||run.status!=='fighting';deck.hidden=!!active;
   if(!active){const start=button('开始攀登',async()=>{busy=true;try{update(await api('tower/start',{team:current.garden.team}));}finally{busy=false;paint();}},true);start.disabled=busy||!current.garden?.team?.length;controls.append(start,button('配置参战队伍',()=>options.onBag()));}
   if(run?.status==='cleared'){controls.append(button('前往第 '+(run.floor+1)+' 层',()=>action({kind:'next'}),true));if(run.cleared%3===0&&run.rested!==run.cleared)controls.append(button('营地休整 · 恢复生命与次数',()=>action({kind:'rest'})));}
   checkpoint.replaceChildren();checkpoint.hidden=!run||!['cleared','complete','lost'].includes(run.status);if(!checkpoint.hidden){checkpoint.append(el('h3','',run.status==='lost'?'下一次，会走得更远':run.status==='complete'?'与你的伙伴，一起抵达星光':'第 '+run.cleared+' 层通过'),el('p','','成长和奖励已保存。生命、技能次数与队伍将延续到下一层。'));for(const c of run.team){const pet=el('article'),image=el('img');image.alt=c.name;image.src='data:image/svg+xml;charset=UTF-8,'+encodeURIComponent(c.svg);pet.append(image,el('strong','',c.name),el('span','',c.battle.hp+' / '+c.stats.hp+' HP'));checkpoint.append(pet);}}
   if(run?.status==='complete')story.textContent+=' 你和伙伴已登顶！奖励与成长已保存。';
   if(run?.status==='lost')story.textContent+=' 队伍已无可出战伙伴。本次成长已保存，可以调整队伍再次挑战。';
   controls.append(button('让我的 Agent 设计关卡',()=>options.onDesign()),button('星光草原',()=>options.onField()));
   if(active)controls.append(button('结束本次旅程',()=>action({kind:'abandon'})));
   if(run?.status==='fighting')progress.textContent+=' · 预计敌人使用「'+run.intent+'」';
   if(!active){deck.replaceChildren();for(const floor of current.preview||[]){const card=el('article','tower-floor'+(floor.floor%5===0?' boss':''));card.append(el('small','',String(floor.floor).padStart(2,'0')),el('strong','',floor.title),el('span','',floor.designer?'由你的 Agent 设计':floor.floor%5===0?'首领楼层':'守护者'));deck.append(card);}}
  }
  function update(next){if(!next)return;current=next;if(next.battle&&!host.hidden){ensureGame();client.update(next.battle);game.update(client.snapshot);}if(!host.hidden)paint();}
  return{open(){host.hidden=false;api('tower').then(update).catch(error=>notice.textContent=error.message);},close(){host.hidden=true;},update};
 }
 root.ClawCrossTower={mount};
})(globalThis);
