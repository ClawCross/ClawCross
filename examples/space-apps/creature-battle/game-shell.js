/* A game home, persistent navigation and a collapsible, member-owned design assistant. */
(function(global){
'use strict';
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
const image=svg=>'data:image/svg+xml;charset=UTF-8,'+encodeURIComponent(svg);
function landscape(){
 const tree=(x,y,s)=>`<g transform="translate(${x} ${y}) scale(${s})"><path d="M0 15V-34" stroke="#846c5b" stroke-width="7"/><path d="M-32-20Q-46-42-20-57Q-18-87 9-72Q42-81 41-49Q64-17 22-12Z" fill="#73bbaa"/><path d="M-22-39Q-10-60 14-55" fill="none" stroke="#b8e8bf" stroke-width="7" stroke-linecap="round"/></g>`;
 return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 780" preserveAspectRatio="xMidYMid slice"><defs><linearGradient id="sky" x2=".8" y2="1"><stop stop-color="#173951"/><stop offset=".6" stop-color="#468584"/><stop offset="1" stop-color="#b6cbb1"/></linearGradient><linearGradient id="rock" x2="1" y2="1"><stop stop-color="#395a6c"/><stop offset="1" stop-color="#192f49"/></linearGradient><linearGradient id="land" x2=".8" y2="1"><stop stop-color="#cbe3ad"/><stop offset="1" stop-color="#75b6a0"/></linearGradient><radialGradient id="glow"><stop stop-color="#fff3c3" stop-opacity=".5"/><stop offset="1" stop-color="#fff3c3" stop-opacity="0"/></radialGradient><filter id="mist"><feGaussianBlur stdDeviation="18"/></filter></defs><path fill="url(#sky)" d="M0 0H1200V780H0Z"/><circle cx="850" cy="170" r="260" fill="url(#glow)"/><circle cx="859" cy="156" r="47" fill="#f3e6bd" opacity=".8"/>
 <g fill="#c9e7d5" opacity=".1"><path d="M-100 530 96 260 260 505 385 310 575 610H0Z"/><path d="M510 570 700 290 900 545 1060 280 1320 610Z"/></g><g opacity=".2" fill="#d3e5ce" filter="url(#mist)"><ellipse cx="210" cy="650" rx="350" ry="60"/><ellipse cx="1040" cy="600" rx="350" ry="70"/></g>
 <g opacity=".5"><path d="M90 370Q163 341 219 375L165 428Z" fill="#3c6572"/><ellipse cx="155" cy="369" rx="66" ry="12" fill="#84b7a0"/>${tree(145,362,.4)}<path d="M1010 350Q1080 328 1145 350L1087 416Z" fill="#3b6474"/><ellipse cx="1080" cy="348" rx="66" ry="13" fill="#91c5a8"/>${tree(1071,342,.55)}</g>
 <g class="island-float"><ellipse cx="795" cy="662" rx="270" ry="27" fill="#1c4058" opacity=".13"/><path d="M500 449 546 543 629 562 693 652 749 600 824 681 890 571 994 549 1072 450Z" fill="url(#rock)"/><path d="m546 468 38 67 45 27 64 90-20-181M813 476l11 205 66-110 43-98" fill="#70a5a1" opacity=".18"/><path d="m944 475-54 96-66 110 27-203" fill="#142c43" opacity=".4"/><ellipse cx="786" cy="448" rx="290" ry="85" fill="#528e86"/><ellipse cx="786" cy="436" rx="286" ry="78" fill="url(#land)"/><path d="M595 452Q751 395 998 448" fill="none" stroke="#e1e2b4" stroke-width="20" opacity=".6"/><ellipse cx="821" cy="455" rx="146" ry="31" fill="#b8dac0"/><ellipse cx="821" cy="455" rx="112" ry="23" fill="#d8e7c7" stroke="#f1efcf" stroke-width="4"/>
 ${tree(598,416,1.05)}${tree(1014,423,.85)}${tree(964,394,.65)}<g transform="translate(678 404)"><path d="M-30 12V-65H21V12" fill="#a9c0af"/><path d="M-42-65-4-94 33-65Z" fill="#477780"/><path d="M-20-49H11V-23H-20Z" fill="#edf0bd"/><path d="M-35 16H28" stroke="#658e83" stroke-width="10"/><path d="M-3-93V-123" stroke="#b9d1bb" stroke-width="3"/><path d="M-1-123 24-115-1-108" fill="#dfbf87"/></g>
 <g fill="#f3dba6">${Array.from({length:14},(_,i)=>`<circle cx="${560+(i*53)%460}" cy="${425+(i*17)%52}" r="${i%3+2}"/>`).join('')}</g><path d="M930 467Q920 533 936 565" fill="none" stroke="#b5e7df" stroke-width="16" opacity=".5"/><path d="M931 473Q927 546 943 590" fill="none" stroke="#e0f5e3" stroke-width="5" opacity=".6"/></g>
 <g fill="#f1efc9" opacity=".65">${Array.from({length:32},(_,i)=>`<circle class="realm-mote" cx="${(i*173+47)%1200}" cy="${(i*97+35)%700}" r="${i%3===0?2:1}" style="animation-delay:-${i%7}s"/>`).join('')}</g><path d="M0 700Q250 638 450 728T1200 700V780H0Z" fill="#122f40" opacity=".55"/></svg>`;
}
function mount(root,{chat,bar,api,onChannel}){
 const app=el('div','creature-app'),header=el('header','realm-header');
 header.innerHTML='<button class="realm-brand" data-nav="home" aria-label="返回游戏首页"><span>✧</span><span>星屿纪行<small>COMPANION ODYSSEY</small></span></button><nav class="realm-nav" aria-label="游戏导航"><button data-nav="home">首页</button><button data-nav="bag">精灵背包</button><button data-nav="journey">探索</button><button data-nav="arena">竞技场</button></nav><button class="realm-designer-toggle" aria-expanded="false">✧ <span>设计师</span></button>';
 const scene=el('div','realm-scene'),home=el('section','realm-home');home.setAttribute('aria-label','游戏首页');
 home.innerHTML='<div class="realm-landscape" aria-hidden="true">'+landscape()+'</div><div class="realm-home-copy"><p class="realm-eyebrow">每一段冒险，都从相遇开始</p><h1>去往星光<br>与你的伙伴同行</h1><p class="realm-description">遇见、培养、探索。让你的想象，成为独一无二的精灵。</p><div class="realm-home-actions"><button class="realm-start">开始冒险 <span>↗</span></button><button class="realm-bag-link">打开精灵背包 <span data-count>0</span></button></div><p class="realm-home-save">✦ 成长与旅程自动保存</p></div><img class="realm-home-pet" alt="你的精灵伙伴"><div class="realm-home-pet-label"><small>等待相遇</small><strong>你的第一位伙伴</strong></div><button class="realm-field-link">星光草原 <span>探索野生精灵 →</span></button>';
 const aside=el('aside','realm-designer');aside.id='realm-designer';aside.hidden=true;aside.setAttribute('aria-label','AI Agent 精灵设计师');
 aside.innerHTML='<div class="designer-heading"><span class="designer-mark">✧</span><div><strong>精灵设计师</strong><small>把你的想象变成伙伴</small></div><button class="designer-close" aria-label="收起设计师">×</button></div><div class="designer-channels"><button data-channel="assistant" aria-pressed="true">设计助手</button><button data-channel="group" aria-pressed="false">对战聊天</button></div><div class="designer-config"><label>我的设计师<select aria-label="选择设计师"></select></label><p class="designer-context">先选择一位伙伴</p></div><div class="designer-history" role="log" aria-label="设计对话"></div><div class="designer-public" hidden></div><div class="designer-pending" role="status" hidden><span>设计师正在构思…</span><button>取消等待</button></div><div class="designer-suggestions"><button data-suggest="appearance">重绘形象</button><button data-suggest="animation">设计技能动画</button></div>';
 aside.querySelector('.designer-heading strong').textContent='AI Agent 精灵设计师';aside.querySelector('.designer-heading small').textContent='原创形象 · 技能动画 · 精灵介绍';aside.querySelector('[data-channel=group]').textContent='玩家聊天';
 const toggle=header.querySelector('.realm-designer-toggle'),history=aside.querySelector('.designer-history'),publicChat=aside.querySelector('.designer-public'),select=aside.querySelector('select');toggle.setAttribute('aria-label','AI Agent 精灵设计师');toggle.querySelector('span').textContent='AI 设计师';
 const backdrop=el('button','designer-backdrop');backdrop.hidden=true;backdrop.setAttribute('aria-label','关闭设计师侧栏');
 scene.append(home,root);root.hidden=true;app.append(header,scene,backdrop,aside);document.body.append(app);publicChat.append(chat);aside.append(bar);
 let garden,snapshot={},channel='assistant',screen='home',selected='',open=false,creating=false,rosterKey='',historyKey='',uiId=null;
 const text=bar.querySelector('textarea'),seen=new Set();
 function setChannel(value){channel=value;history.hidden=value!=='assistant';publicChat.hidden=value!=='group';aside.querySelector('.designer-config').hidden=value!=='assistant';aside.querySelector('.designer-suggestions').hidden=value!=='assistant';aside.querySelectorAll('[data-channel]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.channel===value)));onChannel?.(value);paintPending();}
 function paintPending(){aside.querySelector('.designer-pending').hidden=channel!=='assistant'||!snapshot.assistant?.pending;}
 function showDesigner(value=true,mode=channel){open=value;aside.hidden=!value;backdrop.hidden=!value;app.classList.toggle('designer-open',value);toggle.setAttribute('aria-expanded',String(value));if(value)setChannel(mode);window.dispatchEvent(new Event('resize'));}
 function navigate(next,id=''){
  if(!['home','bag','training','journey','arena'].includes(next))return;
  screen=next;if(id)selected=id;garden?.close();home.hidden=next!=='home';root.hidden=next!=='arena';
  if(['arena','journey'].includes(next)&&open&&matchMedia('(max-width:1000px)').matches)showDesigner(false);
  if(['bag','training','journey'].includes(next))garden?.open({bag:'partners',training:'training',journey:'journey'}[next],id);
  header.querySelectorAll('[data-nav]').forEach(b=>b.setAttribute('aria-current',b.dataset.nav===(next==='training'?'bag':next)?'page':'false'));
  window.dispatchEvent(new Event('resize'));
 }
 function current(){if(creating)return null;return snapshot.garden?.creatures.find(c=>c.id===(garden?.selected()||selected))||snapshot.garden?.creatures.find(c=>c.id===snapshot.garden.selected)||snapshot.garden?.creatures[0];}
 function suggest(kind,c=current()){
  creating=false;if(c)selected=c.id;showDesigner(true,'assistant');
  text.value=c?(kind==='animation'?'请为「'+c.name+'」的四个技能设计有辨识度的 SVG 动画和动作，保持现有形象。':'请为「'+c.name+'」设计一个更精致、独特的原创 SVG 形象，保留培养进度，并打开精灵背包让我看看。'):'我想设计自己的精灵，请先带我领取第一位伙伴。';text.focus({preventScroll:true});
 }
 function newCreature(){creating=true;showDesigner(true,'assistant');aside.querySelector('.designer-context').textContent='正在新建精灵 · AI Agent 原创设计';text.value='请为我创造一只独特的新精灵，包括名字、介绍、原创 SVG 形象、四个技能与对应的技能动画，完成后加入我的精灵背包。';text.focus({preventScroll:true});}
 header.querySelectorAll('[data-nav]').forEach(b=>b.onclick=()=>navigate(b.dataset.nav));
 home.querySelector('.realm-start').onclick=()=>navigate(snapshot.garden?.creatures.length?'journey':'bag');
 home.querySelector('.realm-bag-link').onclick=()=>navigate('bag');home.querySelector('.realm-field-link').onclick=()=>navigate('journey');
 toggle.onclick=()=>showDesigner(!open);aside.querySelector('.designer-close').onclick=()=>showDesigner(false);backdrop.onclick=()=>showDesigner(false);
 aside.querySelectorAll('[data-channel]').forEach(b=>b.onclick=()=>setChannel(b.dataset.channel));
 aside.querySelectorAll('[data-suggest]').forEach(b=>b.onclick=()=>!current()&&b.dataset.suggest==='appearance'?newCreature():suggest(b.dataset.suggest));
 aside.querySelector('.designer-pending button').onclick=()=>api('assistant/cancel',{}).then(()=>api('state')).then(data=>update(data.snapshot)).catch(e=>{bar.querySelector('#hint').textContent=e.message;});
 select.onchange=()=>onChannel?.(channel);
 function update(next){
  snapshot=next;const c=current(),g=next.garden,a=next.assistant;
  if(g){const hero=c||g.creatures.find(p=>p.id===g.selected)||g.creatures[0],pet=hero||g.species[0],portrait=home.querySelector('.realm-home-pet');if(pet?.svg&&portrait.dataset.svg!==pet.svg){portrait.dataset.svg=pet.svg;portrait.src=image(pet.svg);}home.querySelector('[data-count]').textContent=g.creatures.length;
   home.querySelector('.realm-home-pet-label strong').textContent=hero?hero.name:'你的第一位伙伴';home.querySelector('.realm-home-pet-label small').textContent=hero?'Lv.'+hero.level+' · 一起踏上新的旅程':'等待相遇';}
  const person=next.members?.find(m=>(m.key||m.principal)===next.actor?.key),agents=(next.members||[]).filter(m=>m.is_agent&&person&&m.device===person.device),key=JSON.stringify(agents.map(m=>[m.key||m.principal,m.name]));
  if(key!==rosterKey){rosterKey=key;const prior=select.value;select.replaceChildren();for(const m of agents)select.append(new Option(m.name,m.key||m.principal));if(!agents.length)select.append(new Option('尚未加入自己的 Agent',''));select.value=agents.some(m=>(m.key||m.principal)===prior)?prior:a?.agent||agents[0]?.key||agents[0]?.principal||'';}
  aside.querySelector('.designer-context').textContent=!agents.length?'加入自己的 AI Agent，即可创作新精灵；也可以先领取初始伙伴。':creating?'正在新建精灵 · AI Agent 原创设计':c?'正在设计：'+c.name+' · Lv.'+c.level:'描述你的想象，让 AI Agent 创作第一只精灵。';
  aside.querySelector('[data-suggest=appearance]').textContent=c?'重绘形象':'创造新精灵';
  const messages=a?.messages||[],ids=messages.map(m=>m.id).join(',');
  if(ids!==historyKey){historyKey=ids;const following=history.scrollHeight-history.scrollTop-history.clientHeight<50;for(const m of messages){if(seen.has(m.id))continue;seen.add(m.id);const item=el('article','designer-message '+m.role),who=el('small','',m.role==='user'?'你':m.role==='status'?'游戏提示':'设计师');item.append(who,el('p','',m.text));history.append(item);}if(following)history.scrollTop=history.scrollHeight;}
  if(!messages.length&&!history.querySelector('.designer-welcome')){const intro=el('div','designer-welcome');intro.append(el('span','','✧'),el('h3','','你的精灵，由你想象'),el('p','','描述你喜欢的形象，或告诉设计师技能应该怎样释放。设计会直接出现在游戏里。'));history.append(intro);}if(messages.length)history.querySelector('.designer-welcome')?.remove();
  paintPending();
  if(a?.ui){if(uiId===null)uiId=a.ui.id;else if(a.ui.id!==uiId){uiId=a.ui.id;creating=false;navigate(a.ui.screen,a.ui.creature);}}
  else if(uiId===null)uiId='';
  onChannel?.(channel);
 }
 navigate('home');showDesigner(true,'assistant');
 return{scene,attachGarden(value){garden=value;},navigate,update,newCreature,openDesigner(c){suggest('appearance',c);},openChat(){showDesigner(true,'group');},channel:()=>channel,canSend:()=>channel==='group'||!!select.value&&!snapshot.assistant?.pending,
  async submit(content){if(channel==='group')return api('send',{content,mentions:[]});const c=current();return api('assistant',{content,agent:select.value,creature:c?.id||'',create:creating||!snapshot.garden?.creatures.length});}};
}
global.ClawCrossGameShell={mount,landscape};
})(globalThis);
