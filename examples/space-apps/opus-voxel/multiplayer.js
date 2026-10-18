/* Room multiplayer. The space's owner opens one of their worlds to the room and members join it.
 * Every block change goes through the app server (room-world.js), which checks and relays it; each page
 * keeps its own player, inventory and health. The owner's page simulates creatures, physics and time
 * and passes them on; while the owner is away, creatures and time stand still. */
'use strict';
(()=>{
 const ROOM='room',MOB_NAMES=Object.keys(MOB_TYPES);
 const r2=v=>Math.round(v*100)/100,r3=v=>Math.round(v*1000)/1000;
 const net={ws:null,status:null,owner:false,joined:false,welcome:null,chunks:[],queue:[],applying:null,pending:new Map(),players:new Map(),puppets:new Map(),mobSeq:0,others:0,lastMove:''};
 const shared=()=>net.joined&&!!window.game?.worldMeta?.shared;
 const send=message=>{if(net.ws?.readyState===1)net.ws.send(JSON.stringify(message));};
 const say=(text,color='#FFFF55')=>window.game?.ui?.message(text,color);

 // ── connection ────────────────────────────────────────────────────────────
 function connect(){
  const url=new URL('room',location.href.split(/[?#]/)[0]);url.protocol=url.protocol==='https:'?'wss:':'ws:';
  const ws=net.ws=new WebSocket(url.href);
  ws.onmessage=event=>{let message;try{message=JSON.parse(event.data);}catch(_){return;}handlers[message.t]?.(message);};
  ws.onclose=()=>{
   if(net.ws!==ws)return;net.ws=null;
   if(shared())leave('联机连接已断开，已回到标题画面');
   net.joined=false;setTimeout(connect,3000);
  };
 }
 const handlers={
  status:m=>{net.status=m;net.owner=m.owner;refresh();},
  error:m=>{say('联机：'+m.error,'#FF5555');setStatus(m.error);},
  welcome:m=>{net.welcome=m;net.chunks=[];},
  chunks:m=>{net.chunks.push(...m.list);},
  ready:()=>{enter().catch(error=>setStatus(error.message));},
  set:m=>{applyRemote(m.list);if(m.refused)say('这些方块改动没有通过：超出够得着的距离，或会压到其他玩家','#FF5555');},
  pjoin:m=>{if(!shared())return;net.others++;player(m.id,m.name);say(m.name+' 加入了联机世界');},
  pleave:m=>{if(!shared())return;net.others=Math.max(0,net.others-1);const e=net.players.get(m.id);if(e){e.dead=true;net.players.delete(m.id);say(e.name+' 离开了联机世界','#AAAAAA');}},
  pmove:m=>{if(shared())movePlayer(m);},
  mobs:m=>{if(shared()&&!net.owner)syncPuppets(m.list);},
  hit:m=>{if(shared()&&net.owner)hitMob(m);},
  time:m=>{const g=window.game;if(!shared()||net.owner)return;g.worldTime=m.time;g.weather=m.weather;g.rainTarget=m.rain;},
  closed:m=>{if(shared())leave(m.reason||'联机世界已关闭');},
 };

 // ── entering and leaving the shared world ─────────────────────────────────
 // The engine loads worlds from DB; the room's world comes from the server and is never saved locally.
 let room=null;
 const getWorld=DB.getWorld,loadChunks=DB.loadChunks,putWorld=DB.putWorld,putChunks=DB.putChunks;
 DB.getWorld=async id=>id===ROOM?room?.meta:getWorld.call(DB,id);
 DB.loadChunks=async id=>id===ROOM?room.chunks:loadChunks.call(DB,id);
 DB.putWorld=async world=>{if(world.id!==ROOM)return putWorld.call(DB,world);send({t:'save',player:world.player});};
 DB.putChunks=async(id,list)=>{if(id!==ROOM)return putChunks.call(DB,id,list);};
 async function enter(){
  const w=net.welcome,g=window.game;if(!w||!g)return;
  room={meta:{id:ROOM,name:w.world.name+' · 联机',seed:w.world.seed,mode:w.world.mode,flat:w.world.flat,flatLayers:w.world.flatLayers,created:0,lastPlayed:Date.now(),
   time:w.world.time,spawn:w.world.spawn,player:w.player,shared:true},
   chunks:new Map(net.chunks.map(([cx,cz,rec])=>[ckey(cx,cz),{b:new Uint16Array(rec.b),m:new Uint16Array(rec.m)}]))};
  net.chunks=[];
  if(g.worldMeta&&g.state==='play'){await g.saveWorld(true);g.worldMeta=null;}
  net.owner=w.you.owner;net.joined=true;net.pending.clear();net.players.clear();net.puppets.clear();net.queue.length=0;net.others=w.players.length;net.lastMove='';
  roomDialog.close();
  await g.loadWorld(ROOM);
  for(const p of w.players){player(p.id,p.name);if(p.pos)movePlayer(p);}
  say(net.owner?'已进入联机世界。你的页面负责生物与时间。':'已进入联机世界'+(w.players.length?'，在线：'+w.players.map(p=>p.name).join('、'):''));
 }
 async function leave(reason){
  net.joined=false;
  const g=window.game;
  if(g?.worldMeta?.shared)await quitToTitle.call(g);
  if(reason)setTimeout(()=>say(reason,'#FFAA00'),300);
 }
 const quitToTitle=Game.prototype.quitToTitle;
 Game.prototype.quitToTitle=async function(){
  const wasShared=shared();
  await quitToTitle.call(this);
  if(wasShared){send({t:'leave'});net.joined=false;}
 };
 // The owner opens the world they are in: its description, its saved chunks in batches, then they join it.
 async function openWorld(){
  const g=window.game,m=g.worldMeta;if(!m||m.shared||!net.owner)return;
  setStatus('正在开放「'+m.name+'」…');
  await g.saveWorld(true);
  const chunks=await loadChunks.call(DB,m.id);
  send({t:'open',meta:{name:m.name,seed:m.seed,mode:m.mode,flat:!!m.flat,flatLayers:m.flatLayers||null,spawn:m.spawn||null,time:g.worldTime},player:m.player});
  let list=[],bytes=0;
  for(const[key,rec]of chunks){
   const entry=[Math.floor(key/65536)-32768,(key%65536)-32768,{b:Array.from(rec.b),m:Array.from(rec.m)}],size=JSON.stringify(entry).length;
   if(bytes+size>100000&&list.length){send({t:'openChunks',list});list=[];bytes=0;}
   list.push(entry);bytes+=size;
  }
  if(list.length)send({t:'openChunks',list});
  send({t:'openDone'});send({t:'join'});
 }

 // ── blocks ────────────────────────────────────────────────────────────────
 // Every change in the shared world is sent, except the one being applied from the server.
 const setBlock=World.prototype.setBlock;
 World.prototype.setBlock=function(x,y,z,id,meta=0,flags=1){
  const changed=setBlock.call(this,x,y,z,id,meta,flags);
  if(changed&&this===window.game?.world&&shared()&&net.applying!==x+','+y+','+z)net.queue.push([x,y,z,id,meta&255]);
  return changed;
 };
 const setMeta=World.prototype.setMeta;
 World.prototype.setMeta=function(x,y,z,meta){
  const before=this.getMeta(x,y,z);setMeta.call(this,x,y,z,meta);
  if(this===window.game?.world&&shared()&&net.applying!==x+','+y+','+z&&this.getMeta(x,y,z)!==before)net.queue.push([x,y,z,this.getBlock(x,y,z),meta&255]);
 };
 setInterval(()=>{if(!shared()){net.queue.length=0;return;}while(net.queue.length)send({t:'set',list:net.queue.splice(0,3000)});},50);
 function applyRemote(list){
  const g=window.game;if(!shared()||!Array.isArray(list))return;
  const w=g.world;
  for(const entry of list){
   const[x,y,z,id,meta]=entry,key=ckey(x>>4,z>>4);
   if(!w.chunks.has(key)){if(!net.pending.has(key))net.pending.set(key,[]);net.pending.get(key).push(entry);continue;}
   net.applying=x+','+y+','+z;
   // The owner's page reacts (water flows, sand falls); other pages only show the result.
   try{w.setBlock(x,y,z,id,meta,net.owner?1:0);}finally{net.applying=null;}
  }
 }
 const loadChunk=Game.prototype.loadChunk;
 Game.prototype.loadChunk=function(cx,cz){
  const result=loadChunk.call(this,cx,cz),key=ckey(cx,cz),list=net.pending.get(key);
  if(list&&shared()){net.pending.delete(key);applyRemote(list);}
  return result;
 };
 // Other pages leave world physics, random growth and creature spawning to the owner's page.
 for(const[owner,name]of[[World,'tickScheduled'],[World,'randomTick'],[World,'notify'],[Game,'spawnMobs']]){
  const original=owner.prototype[name];
  owner.prototype[name]=function(...args){if(shared()&&!net.owner)return;return original.apply(this,args);};
 }

 // ── players and creatures from other pages ────────────────────────────────
 function puppetTick(){
  this.savePrev();this.age=(this.age||0)+1;
  if(this.hurtTime>0)this.hurtTime--;if(this.swingT>0)this.swingT--;
  const t=this.targetPos;if(!t)return;
  const dx=t[0]-this.pos[0],dy=t[1]-this.pos[1],dz=t[2]-this.pos[2],k=Math.abs(dx)+Math.abs(dy)+Math.abs(dz)>8?1:0.35;
  this.pos[0]+=dx*k;this.pos[1]+=dy*k;this.pos[2]+=dz*k;
  let d=this.targetYaw-this.bodyYaw;while(d>Math.PI)d-=Math.PI*2;while(d<-Math.PI)d+=Math.PI*2;
  this.bodyYaw+=d*0.5;this.yaw=this.bodyYaw;
  const speed=Math.min(1,Math.hypot(dx,dz)*4);this.limbAmt+=(speed-this.limbAmt)*0.4;this.limbSwing+=this.limbAmt;
 }
 function playerPose(a){
  const swing=lerp(this.limbSwing-this.limbAmt,this.limbSwing,a)*0.6662,l=Math.cos(swing)*1.4*this.limbAmt,r=-l;
  const sw=this.swingT>0?Math.sin((6-this.swingT+a)/6*Math.PI):0;
  return{head:{rx:this.pitch||0},rarm:{rx:r*0.7-sw*1.2},larm:{rx:l*0.7},rleg:{rx:l},lleg:{rx:r}};
 }
 // Another member: the player model, moved by what their page sends. They cannot be hurt here.
 function player(id,name){
  const g=window.game;if(!g?.world)return null;
  let e=net.players.get(id);if(e&&!e.dead)return e;
  e=new Mob(g,'zombie',0,-200,0);
  Object.assign(e,{type:'player',T:{...MOB_TYPES.zombie,cn:name,model:'steve',scale:0.9375,hostile:false},remote:true,name,persistent:false,targetPos:null,targetYaw:0});
  e.tick=puppetTick;e.pose=playerPose;e.hurt=()=>false;
  net.players.set(id,e);g.entities.push(e);return e;
 }
 function movePlayer({id,name,pos,yaw,pitch,sneak}){
  const e=player(id,name||net.players.get(id)?.name||'玩家');if(!e||!Array.isArray(pos))return;
  if(!e.targetPos){e.pos=[...pos];e.prev=[...pos];e.bodyYaw=yaw;}
  e.targetPos=pos;e.targetYaw=yaw;e.pitch=pitch;e.sneaking=!!sneak;
 }
 // Creatures the owner's page simulates, shown on other pages.
 function syncPuppets(list){
  const g=window.game,seen=new Set();
  for(const[id,type,x,y,z,yaw,head,health,flags,wool]of list){
   seen.add(id);let m=net.puppets.get(id);
   if(!m||m.dead){
    const kind=MOB_NAMES[type];if(!kind)continue;
    m=new Mob(g,kind,x,y,z);Object.assign(m,{netId:id,puppet:true,persistent:false,bodyYaw:yaw,prev:[x,y,z]});
    m.tick=puppetTick;m.hurt=()=>false;net.puppets.set(id,m);g.entities.push(m);
   }
   m.targetPos=[x,y,z];m.targetYaw=yaw;m.headYaw=head;m.health=health;m.baby=flags&1?1:0;m.sheared=!!(flags&2);m.woolColor=wool;
   if(flags&4&&!m.hurtTime)m.hurtTime=10;
   m.fuse=flags&8?Math.max(0,m.fuse+1):-1;
  }
  for(const[id,m]of net.puppets)if(!seen.has(id)){m.dead=true;net.puppets.delete(id);}
 }
 function hitMob({mob,damage,from}){
  const m=window.game.entities.find(e=>e.netId===mob&&!e.dead&&e instanceof Mob);
  if(m)m.hurt(damage,{type:'player',knockFrom:from||m.pos,knock:0.4});
 }
 // Hitting a creature from another page asks the owner's page to apply it; players cannot be hurt.
 const attack=Game.prototype.attack;
 Game.prototype.attack=function(e){
  if(!e?.puppet&&!e?.remote)return attack.call(this,e);
  const p=this.player;p.swingT=6;this.attackCd=10;
  if(e.puppet){
   const def=p.inv.held&&ITEMS[p.inv.held.id];let damage=def&&def.attack?def.attack:1;
   if(p.vel[1]<-0.05&&!p.onGround&&!p.inWater)damage*=1.5;
   send({t:'hit',mob:e.netId,damage,from:[...p.pos]});e.hurtTime=10;
  }
 };
 // Shearing, feeding or milking only works on the owner's own creatures.
 const use=Game.prototype.use;
 Game.prototype.use=function(){
  const e=this.targetEntity;
  if(!e?.puppet&&!e?.remote)return use.call(this);
  this.targetEntity=null;try{return use.call(this);}finally{this.targetEntity=e;}
 };
 // What this page tells the room: its player 10 times a second, and the owner's creatures and time.
 setInterval(()=>{
  const g=window.game;if(!shared()||!g.player)return;const p=g.player;
  const message={t:'move',pos:p.pos.map(r2),yaw:r3(p.yaw),pitch:r3(p.pitch),held:p.inv.held?.id??null,sneak:!!p.sneaking};
  const key=JSON.stringify(message);if(key===net.lastMove)return;net.lastMove=key;send(message);
 },100);
 setInterval(()=>{
  const g=window.game;if(!shared()||!net.owner||!net.others)return;
  const list=[];
  for(const e of g.entities){
   if(!(e instanceof Mob)||e.dead||e.remote)continue;e.netId??=++net.mobSeq;
   list.push([e.netId,MOB_NAMES.indexOf(e.type),r2(e.pos[0]),r2(e.pos[1]),r2(e.pos[2]),r3(e.bodyYaw),r3(e.headYaw||0),Math.ceil(e.health),(e.baby?1:0)|(e.sheared?2:0)|(e.hurtTime>0?4:0)|(e.fuse>=0?8:0),e.woolColor||0]);
  }
  send({t:'mobs',list});
 },300);
 setInterval(()=>{const g=window.game;if(shared()&&net.owner&&net.others)send({t:'time',time:g.worldTime,weather:g.weather,rain:g.rainTarget});},3000);

 // ── room dialog ───────────────────────────────────────────────────────────
 const style=document.createElement('style');style.textContent=`.voxel-room[hidden]{display:none}.voxel-room{position:fixed;inset:0;z-index:20;display:grid;place-items:center;padding:18px;background:#09131dbc;color:#dfefee;font:14px/1.6 system-ui;box-sizing:border-box}
 .voxel-room>div{box-sizing:border-box;width:min(100%,460px);background:#182e3b;border:1px solid #a9c9c636;border-radius:22px;padding:24px;box-shadow:0 24px 90px #0007}.voxel-room h2{font-size:21px;margin:0 0 10px}
 .voxel-room p{font-size:13px;color:#b8d0d2}.voxel-room small{color:#93aeb1}.voxel-room [role=status]{color:#ead5ad;min-height:20px}.voxel-room-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}
 .voxel-room button{border:1px solid #accac344;border-radius:11px;padding:9px 14px;min-height:40px;background:#a5cdbf12;color:#d9eddf;font:inherit;cursor:pointer}.voxel-room button.primary{background:#bdded0;color:#153c33}.voxel-room button[hidden]{display:none}`;
 document.head.append(style);
 const roomDialog=document.createElement('section');roomDialog.className='voxel-room';roomDialog.hidden=true;roomDialog.setAttribute('role','dialog');roomDialog.setAttribute('aria-modal','true');roomDialog.setAttribute('aria-label','房间联机');
 roomDialog.innerHTML='<div><h2>房间联机</h2><p data-text></p><small>方块改动经服务器同步；各人的背包、血量各自保存。群主的页面负责生物与时间，群主离开时它们会暂停。</small><p role="status"></p><div class="voxel-room-actions"><button class="primary" data-join>加入联机世界</button><button data-open>开放当前世界</button><button data-reopen>重新开放</button><button data-close-room>关闭联机</button><button data-back>返回</button></div></div>';
 document.body.append(roomDialog);
 for(const name of['mousedown','mouseup','mousemove','wheel','keydown','keyup'])roomDialog.addEventListener(name,event=>{event.stopPropagation();if(name==='keydown'&&event.key==='Escape')roomDialog.close();});
 roomDialog.close=()=>{roomDialog.hidden=true;};
 const q=selector=>roomDialog.querySelector(selector);
 function setStatus(text){q('[role=status]').textContent=text||'';}
 function refresh(){
  if(roomDialog.hidden)return;
  const s=net.status,g=window.game,inShared=shared(),inOwn=!!g?.worldMeta&&!g.worldMeta.shared&&g.state==='play';
  q('[data-text]').textContent=!net.ws?'正在连接…':!s?'':s.open?'群主开放了「'+s.world.name+'」（'+(s.world.mode==='creative'?'创造':'生存')+(s.world.flat?'，超平坦':'')+'）。在线：'+(s.players.join('、')||'暂无')+(s.ownerOnline?'':'。群主暂时不在。'):s.owner?'还没有开放联机世界。进入你的一个世界，再在这里选「开放当前世界」。':'群主还没有开放联机世界。';
  q('[data-join]').hidden=!s?.open||inShared;
  q('[data-open]').hidden=!s?.owner||!inOwn;
  q('[data-reopen]').hidden=!s?.owner||s.open||!s.exists;
  q('[data-close-room]').hidden=!s?.owner||!s.open;
 }
 function openRoom(){if(window.game?.ui)window.game.ui.mouse.down=false;setStatus('');roomDialog.hidden=false;refresh();send({t:'status'});}
 q('[data-back]').onclick=()=>roomDialog.close();
 q('[data-join]').onclick=()=>{setStatus('正在进入…');send({t:'join'});};
 q('[data-open]').onclick=()=>{if(confirm('把「'+window.game.worldMeta.name+'」复制为本房间的联机世界？之后的建造在联机世界里进行，你的单人存档保持现在的样子。'))openWorld().catch(error=>setStatus(error.message));};
 q('[data-reopen]').onclick=()=>send({t:'reopen'});
 q('[data-close-room]').onclick=()=>{if(confirm('关闭联机世界？在线成员会回到标题画面，世界数据保留，可以重新开放。'))send({t:'close'});};
 const titleLayout=TitleScreen.prototype.layout;
 TitleScreen.prototype.layout=function(){titleLayout.call(this);const first=this.widgets[0];this.widgets.splice(1,0,{x:first.x,y:first.y+24,w:200,h:20,label:'房间联机…',onClick:openRoom});for(const w of this.widgets.slice(2))w.y+=24;};
 const pauseLayout=PauseScreen.prototype.layout;
 PauseScreen.prototype.layout=function(){
  pauseLayout.call(this);
  const u=this.game.ui,quit=this.widgets.at(-1),y=quit.y;quit.y+=24;
  this.widgets.splice(-1,0,{x:u.w/2-102,y,w:204,h:20,label:()=>shared()?'房间联机 · '+(net.others+1)+' 人在线':'房间联机…',onClick:openRoom});
 };
 connect();
})();
