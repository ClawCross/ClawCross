/* World tools for the voxel engine: superflat layers, JSON builds and the character control API.
 * Patches the upstream game from outside, like touch.js; the vendor engine stays as published. */
'use strict';
(()=>{
 // ── shared dialog ─────────────────────────────────────────────────────────
 const style=document.createElement('style');style.textContent=`
 .voxel-tool[hidden]{display:none}.voxel-tool{position:fixed;inset:0;z-index:20;display:grid;place-items:center;padding:18px;background:#09131dbc;color:#dfefee;font:14px/1.6 system-ui;box-sizing:border-box;overflow:auto}
 .voxel-tool-card{box-sizing:border-box;width:min(100%,560px);max-height:100%;overflow:auto;background:#182e3b;border:1px solid #a9c9c636;border-radius:22px;padding:24px;box-shadow:0 24px 90px #0007}
 .voxel-tool h2{font-size:21px;margin:0 0 10px}.voxel-tool p,.voxel-tool small{font-size:12px;color:#a4c0c3}.voxel-tool code{font-size:12px;color:#d9eddf;word-break:break-all}
 .voxel-tool textarea,.voxel-tool input[type=text],.voxel-tool input[type=number]{box-sizing:border-box;width:100%;border:1px solid #99babb55;border-radius:11px;background:#0b1c28;color:#e3f1ef;font:13px/1.5 ui-monospace,monospace;padding:9px 11px}
 .voxel-tool textarea{min-height:150px;resize:vertical}.voxel-tool label{display:block;margin:12px 0 4px;font-size:12px;color:#bcd5d4}
 .voxel-tool-row{display:flex;gap:8px;flex-wrap:wrap}.voxel-tool-row>*{flex:1;min-width:90px}
 .voxel-tool-actions{display:flex;gap:10px;margin-top:18px;flex-wrap:wrap}.voxel-tool button{border:1px solid #accac344;border-radius:11px;padding:9px 14px;min-height:40px;background:#a5cdbf12;color:#d9eddf;font:inherit;cursor:pointer}
 .voxel-tool button.primary{background:#bdded0;color:#153c33}.voxel-tool button:disabled{opacity:.45;cursor:default}.voxel-tool :focus-visible{outline:2px solid #c2e3d9;outline-offset:3px}
 .voxel-tool-status{min-height:20px;color:#ead5ad!important;white-space:pre-wrap}.voxel-tool pre{background:#0b1c28;border-radius:11px;padding:10px;overflow:auto;font-size:12px;color:#cfe3e0;white-space:pre-wrap;word-break:break-all}`;
 document.head.append(style);
 function dialog(title,html){
  const root=document.createElement('section');root.className='voxel-tool';root.hidden=true;root.setAttribute('role','dialog');root.setAttribute('aria-modal','true');root.setAttribute('aria-label',title);
  root.innerHTML='<div class="voxel-tool-card"><h2></h2>'+html+'</div>';root.querySelector('h2').textContent=title;
  // The game listens on the document; keep its keys and clicks out of the dialog.
  for(const name of ['mousedown','mouseup','mousemove','wheel','keydown','keyup'])root.addEventListener(name,event=>{event.stopPropagation();if(name==='keydown'&&event.key==='Escape'){event.preventDefault();root.close();}});
  root.close=()=>{root.hidden=true;root.onclose?.();};
  root.open=()=>{if(window.game?.ui)window.game.ui.mouse.down=false;root.hidden=false;};
  document.body.append(root);return root;
 }
 const $=(root,selector)=>root.querySelector(selector);

 // ── block names ───────────────────────────────────────────────────────────
 const BLOCK_ID=Object.fromEntries(BLOCKS.filter(Boolean).map(b=>[b.key,b.id]));
 function blockId(type){
  if(Number.isInteger(type)&&BLOCKS[type])return type;
  const id=BLOCK_ID[String(type).trim()];
  if(id===undefined)throw Error('未知方块：'+type);
  return id;
 }

 // ── 1. superflat layers ───────────────────────────────────────────────────
 // Bottom to top; "2*dirt" is two layers of dirt. The engine's own flat world is the 默认 preset.
 const PRESETS=[['默认','bedrock,56*stone,3*dirt,grass_block'],['经典','bedrock,2*dirt,grass_block']];
 const parseLayers=text=>VoxelFlatLayers.parseLayers(text,BLOCKS,HEIGHT),layersHeight=VoxelFlatLayers.layersHeight;
 VoxelFlatLayers.install(World,Chunk,BI);
 const setupWorld=Game.prototype.setupWorld;
 Game.prototype.setupWorld=function(seed){
  setupWorld.call(this,seed);
  try{this.world.flatLayers=this.worldMeta?.flatLayers?parseLayers(this.worldMeta.flatLayers):null;}catch(_){this.world.flatLayers=null;}
 };
 const findSpawn=Game.prototype.findSpawn;
 Game.prototype.findSpawn=function(){return this.world.flat&&this.world.flatLayers?[0,layersHeight(this.world.flatLayers),0]:findSpawn.call(this);};
 // As the engine's createWorld, plus the layers chosen for a superflat world.
 Game.prototype.createWorld=async function(name,seedStr,mode,flat,layers){
  let seed=parseInt(seedStr,10);
  if(!seedStr)seed=(Math.random()*2**31)|0;
  else if(isNaN(seed)||String(seed)!==seedStr.trim())seed=hashStr(seedStr);
  const id='w'+Date.now().toString(36);
  const meta={id,name:name||'新的世界',seed,mode,flat:!!flat,created:Date.now(),lastPlayed:Date.now(),time:1000,player:null};
  if(flat&&layers)meta.flatLayers=layers;
  await DB.putWorld(meta);
  return this.loadWorld(id);
 };
 const flatDialog=dialog('超平坦设置','<p>从下到上，用逗号分隔；<code>2*dirt</code> 表示两层泥土。方块名见下方列表。</p><div class="voxel-tool-row" data-presets></div><label for="voxel-layers">层设置</label><input id="voxel-layers" type="text" spellcheck="false" autocomplete="off"><p class="voxel-tool-status" role="status"></p><details><summary>方块名</summary><p data-names></p></details><div class="voxel-tool-actions"><button class="primary" data-ok>使用这些层</button><button data-cancel>取消</button></div>');
 $(flatDialog,'[data-names]').textContent=BLOCKS.filter(b=>b&&b.id).map(b=>b.key+' '+b.cn).join('　');
 let flatScreen=null;
 for(const[name,text]of PRESETS){const button=document.createElement('button');button.textContent=name;button.onclick=()=>{$(flatDialog,'input').value=text;check();};$(flatDialog,'[data-presets]').append(button);}
 function check(){try{const layers=parseLayers($(flatDialog,'input').value);$(flatDialog,'[role=status]').textContent='共 '+layersHeight(layers)+' 层，出生在 y='+layersHeight(layers);return true;}catch(error){$(flatDialog,'[role=status]').textContent=error.message;return false;}}
 $(flatDialog,'input').oninput=check;
 $(flatDialog,'[data-cancel]').onclick=()=>flatDialog.close();
 $(flatDialog,'[data-ok]').onclick=()=>{if(!check())return;flatScreen.flatLayers=$(flatDialog,'input').value.split(',').map(s=>s.trim()).filter(Boolean).join(',');flatScreen.flat=true;flatDialog.close();};
 flatDialog.onclose=()=>{if(flatScreen&&flatScreen.game.ui.screen===flatScreen)flatScreen.focus(flatScreen.field);};
 const presetName=text=>(PRESETS.find(([,value])=>value===text)||['自定义'])[0];
 const createLayout=CreateWorldScreen.prototype.layout;
 CreateWorldScreen.prototype.layout=function(){
  createLayout.call(this);
  this.flatLayers??=PRESETS[0][1];
  const u=this.game.ui;
  this.widgets.splice(2,0,{x:u.w/2-100,y:184,w:200,h:20,label:()=>'超平坦设置: '+presetName(this.flatLayers),
   tip:['超平坦世界的地层，从下到上','可选预设或自己填写'],onClick:()=>{flatScreen=this;u.hideInput();$(flatDialog,'input').value=this.flatLayers;check();flatDialog.open();$(flatDialog,'input').focus();}});
 };
 CreateWorldScreen.prototype.create=function(){this.game.ui.hideInput();this.game.createWorld(this.name.trim()||'新的世界',this.seed.trim(),this.mode,this.flat,this.flat?this.flatLayers:undefined);};

 // ── 2. JSON builds ────────────────────────────────────────────────────────
 // {"origin":[x,y,z],"blocks":[[dx,dy,dz,"stone"],[dx,dy,dz,"oak_planks",meta],…]}; "air" removes a block.
 const MAX_BUILD=500000;
 function readBuild(spec){
  if(!spec||typeof spec!=='object'||!Array.isArray(spec.blocks))throw Error('需要 {"blocks":[[x,y,z,"方块名"],…]}');
  if(spec.blocks.length>MAX_BUILD)throw Error('一次最多 '+MAX_BUILD+' 个方块');
  const origin=spec.origin??[0,0,0];
  if(!Array.isArray(origin)||origin.length!==3||!origin.every(Number.isInteger))throw Error('origin 需为三个整数');
  return{origin,blocks:spec.blocks};
 }
 function applyBuild(g,spec){
  if(g.state!=='play'||!g.world)throw Error('先进入一个世界');
  const{origin:[ox,oy,oz],blocks}=readBuild(spec),w=g.world,touched=new Set(),errors=[];
  let placed=0,unchanged=0;
  blocks.forEach((entry,n)=>{
   try{
    if(!Array.isArray(entry)||entry.length<4)throw Error('格式应为 [x,y,z,"方块名"]');
    const[dx,dy,dz,type,meta=0]=entry;
    if(![dx,dy,dz,meta].every(Number.isInteger))throw Error('坐标需为整数');
    const x=ox+dx,y=oy+dy,z=oz+dz,id=blockId(type);
    if(y<0||y>=HEIGHT)throw Error('高度需在 0–'+(HEIGHT-1));
    if(!w.getChunk(x>>4,z>>4))g.loadChunk(x>>4,z>>4);
    // Flag 2: no per-block relight or neighbour updates; the touched chunks relight once below.
    if(w.setBlock(x,y,z,id,meta&255,2))placed++;else unchanged++;
    touched.add(ckey(x>>4,z>>4));
   }catch(error){if(errors.length<20)errors.push('第 '+(n+1)+' 个：'+error.message);}
  });
  for(const key of touched){
   const ch=w.chunks.get(key);if(!ch)continue;
   w.initLight(ch);
   for(let a=-1;a<=1;a++)for(let b=-1;b<=1;b++){const near=w.getChunk(ch.cx+a,ch.cz+b);if(near)near.dirty=0xFFFF;}
  }
  if(placed)g.saveWorld(true);
  return{placed,unchanged,failed:blocks.length-placed-unchanged,errors};
 }
 const describe=r=>'放置 '+r.placed+' 个，未变 '+r.unchanged+' 个'+(r.failed?'，失败 '+r.failed+' 个\n'+r.errors.join('\n'):'');
 // Builds sent while this world was closed wait in the member's save; they are built when it opens.
 let checkedWorld=null;
 setInterval(async()=>{
  const g=window.game,id=g?.state==='play'?g.worldMeta?.id:null;
  if(!id||id===checkedWorld)return;checkedWorld=id;
  try{
   const{builds}=await(await fetch('builds?world='+encodeURIComponent(id))).json();
   if(!builds?.length||g.worldMeta?.id!==id)return;
   const done=[];
   for(const build of builds){try{const result=applyBuild(g,build);g.ui.message('已建造等待中的建筑：'+describe(result).split('\n')[0],'#55FF55');}catch(error){g.ui.message('建筑未能建造：'+error.message,'#FF5555');}done.push(build.id);}
   await fetch('builds',{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify({world:id,done})});
  }catch(_){checkedWorld=null;}
 },1000);
 const importDialog=dialog('导入建筑 JSON','<p>格式：<code>{"origin":[x,y,z],"blocks":[[x,y,z,"stone"],…]}</code>，坐标相对原点，<code>"air"</code> 表示删除。没有 origin 时以你脚下为原点。</p><label for="voxel-build">JSON</label><textarea id="voxel-build" spellcheck="false"></textarea><div class="voxel-tool-row"><input type="file" accept=".json,application/json" aria-label="选择 JSON 文件"></div><label>原点（x, y, z）</label><div class="voxel-tool-row"><input type="number" data-axis="0" aria-label="原点 x"><input type="number" data-axis="1" aria-label="原点 y"><input type="number" data-axis="2" aria-label="原点 z"></div><p class="voxel-tool-status" role="status"></p><div class="voxel-tool-actions"><button class="primary" data-build>建造</button><button data-cancel>关闭</button></div>');
 const axes=[...importDialog.querySelectorAll('[data-axis]')],buildStatus=$(importDialog,'[role=status]');
 function fillOrigin(){
  let origin=null;try{origin=JSON.parse($(importDialog,'textarea').value).origin;}catch(_){}
  const p=window.game.player.pos;
  (Array.isArray(origin)&&origin.length===3?origin:[Math.floor(p[0]),Math.floor(p[1]),Math.floor(p[2])]).forEach((v,i)=>{axes[i].value=v;});
 }
 $(importDialog,'textarea').onchange=fillOrigin;
 $(importDialog,'input[type=file]').onchange=async event=>{const file=event.target.files[0];if(!file)return;$(importDialog,'textarea').value=await file.text();fillOrigin();buildStatus.textContent=file.name+'：'+file.size+' 字节';};
 $(importDialog,'[data-cancel]').onclick=()=>importDialog.close();
 $(importDialog,'[data-build]').onclick=()=>{
  try{
   const spec=JSON.parse($(importDialog,'textarea').value);
   buildStatus.textContent=describe(applyBuild(window.game,{...spec,origin:axes.map(a=>Math.round(+a.value))}));
  }catch(error){buildStatus.textContent=error.message;}
 };
 const pauseLayout=PauseScreen.prototype.layout;
 PauseScreen.prototype.layout=function(){
  pauseLayout.call(this);
  const g=this.game,u=g.ui,quit=this.widgets.at(-1),y=quit.y;
  quit.y+=24;
  this.widgets.splice(-1,0,
   {x:u.w/2-102,y,w:98,h:20,label:'导入建筑…',onClick:()=>{$(importDialog,'textarea').value||=JSON.stringify({blocks:[[0,0,0,'stone'],[1,0,0,'glass'],[2,0,0,'oak_planks']]});fillOrigin();buildStatus.textContent='';importDialog.open();}},
   {x:u.w/2+4,y,w:98,h:20,label:'Agent 控制…',onClick:openControl});
 };

 // ── 3. character control API ──────────────────────────────────────────────
 // The app server queues commands from <app URL>/api/…; this page long-polls them and runs them in order.
 const deg=r=>Math.round(r*1800/Math.PI)/10,rad=d=>d*Math.PI/180,round=v=>Math.round(v*100)/100;
 const stackOf=s=>s?{item:ITEMS[s.id]?.key||s.id,count:s.count}:null;
 const control={input:null,move:null};
 const readInput=Game.prototype.readInput;
 Game.prototype.readInput=function(){const input=readInput.call(this);return control.input?{...input,...control.input}:input;};
 const tick=Game.prototype.tick;
 Game.prototype.tick=function(){
  const before=this.tickCount;tick.call(this);
  const m=control.move;if(!m||this.tickCount===before)return;
  if(--m.left<=0){control.input=null;control.move=null;m.done();}
 };
 const eyeOf=p=>[p.pos[0],p.pos[1]+p.eye(),p.pos[2]];
 function reachable(g,x,y,z){
  const e=eyeOf(g.player),d=Math.hypot(x+0.5-e[0],y+0.5-e[1],z+0.5-e[2]);
  if(d>g.reach()+1)throw Error('超出够得着的距离（'+round(d)+' 格）');
 }
 function playing(){const g=window.game;if(!g||g.state!=='play'||!g.player)throw Error('没有打开世界：先在群里打开方块世界并进入一个世界');return g;}
 function state(g){
  const p=g.player,t=g.target;
  return{world:{id:g.worldMeta.id,name:g.worldMeta.name,mode:g.mode,flat:!!g.worldMeta.flat,flatLayers:g.worldMeta.flatLayers||null},
   player:{pos:p.pos.map(round),block:p.pos.map(Math.floor),yaw:deg(p.yaw),pitch:deg(p.pitch),health:p.health,food:p.food,onGround:p.onGround,flying:!!p.flying,inWater:!!p.inWater,
    selected:p.inv.sel,held:stackOf(p.inv.slots[p.inv.sel]),hotbar:p.inv.slots.slice(0,9).map(stackOf)},
   target:t?{pos:t.pos,block:BLOCKS[t.id]?.key||t.id}:null,time:g.worldTime%24000,paused:!!g.ui.screen?.pauses,hidden:document.hidden};
 }
 const COMMANDS={
  state:()=>state(playing()),
  blocks:()=>BLOCKS.filter(Boolean).map(b=>({key:b.key,name:b.cn,id:b.id})),
  inspect:({from,to})=>{
   const g=playing(),w=g.world;
   if(![from,to].every(v=>Array.isArray(v)&&v.length===3&&v.every(Number.isInteger)))throw Error('from 和 to 需为 [x,y,z] 整数');
   const lo=from.map((v,i)=>Math.min(v,to[i])),hi=from.map((v,i)=>Math.max(v,to[i]));
   if((hi[0]-lo[0]+1)*(hi[1]-lo[1]+1)*(hi[2]-lo[2]+1)>32768)throw Error('一次最多查看 32768 格');
   const blocks=[],unloaded=new Set();
   for(let x=lo[0];x<=hi[0];x++)for(let z=lo[2];z<=hi[2];z++){
    if(!w.isLoaded(x,z)){unloaded.add((x>>4)+','+(z>>4));continue;}
    for(let y=Math.max(0,lo[1]);y<=Math.min(HEIGHT-1,hi[1]);y++){const id=w.getBlock(x,y,z);if(id)blocks.push([x,y,z,BLOCKS[id].key]);}
   }
   return{blocks,unloadedChunks:[...unloaded]};
  },
  move:({forward=0,strafe=0,jump=false,sneak=false,sprint=false,ticks=10})=>{
   const g=playing();
   if(!Number.isInteger(ticks)||ticks<1||ticks>200)throw Error('ticks 需为 1–200 的整数（每秒 20 刻）');
   const clamp=v=>Math.max(-1,Math.min(1,+v||0));
   control.move?.done();
   return new Promise(resolve=>{
    control.input={fwd:clamp(forward),strafe:clamp(strafe),jump:!!jump,sneak:!!sneak,sprint:!!sprint};
    const timer=setTimeout(()=>{if(control.move===m){control.input=null;control.move=null;resolve({stopped:'页面在后台或游戏已暂停，移动未走完',pos:g.player.pos.map(round)});}},ticks*50+15000);
    const m=control.move={left:ticks,done:()=>{clearTimeout(timer);resolve({pos:g.player.pos.map(round),onGround:g.player.onGround});}};
   });
  },
  look:({yaw,pitch,at})=>{
   const p=playing().player;
   if(at){
    if(!Array.isArray(at)||at.length!==3||!at.every(Number.isFinite))throw Error('at 需为 [x,y,z]');
    const e=eyeOf(p),d=[at[0]-e[0],at[1]-e[1],at[2]-e[2]];
    p.yaw=Math.atan2(-d[0],-d[2]);p.pitch=Math.atan2(d[1],Math.hypot(d[0],d[2]));
   }else{
    if(yaw!==undefined){if(!Number.isFinite(yaw))throw Error('yaw 需为角度');p.yaw=rad(yaw);}
    if(pitch!==undefined){if(!Number.isFinite(pitch))throw Error('pitch 需为角度');p.pitch=Math.max(-1.55,Math.min(1.55,rad(pitch)));}
   }
   return{yaw:deg(p.yaw),pitch:deg(p.pitch)};
  },
  select:({slot})=>{const p=playing().player;if(!Number.isInteger(slot)||slot<0||slot>8)throw Error('slot 需为 0–8');p.inv.sel=slot;return{held:stackOf(p.inv.slots[slot])};},
  dig:({x,y,z})=>{
   const g=playing();if(![x,y,z].every(Number.isInteger))throw Error('需要整数坐标 x,y,z');
   const id=g.world.getBlock(x,y,z);if(!id)throw Error('那里没有方块');if(IS_FLUID[id])throw Error('液体不能挖');
   reachable(g,x,y,z);g.breakBlock(x,y,z);
   return{removed:BLOCKS[id].key};
  },
  place:({x,y,z,block})=>{
   const g=playing(),p=g.player,w=g.world;if(![x,y,z].every(Number.isInteger))throw Error('需要整数坐标 x,y,z');
   const held=p.inv.slots[p.inv.sel],id=block===undefined?held?.id:blockId(block);
   if(id===undefined||id>=256||!BLOCKS[id])throw Error('请指定方块，或手持一个方块');
   const here=w.getBlock(x,y,z);if(here&&!REPLACEABLE[here])throw Error('那里已经有方块：'+BLOCKS[here].key);
   reachable(g,x,y,z);
   const box=p.box();if(box[0]<x+1&&box[3]>x&&box[1]<y+1&&box[4]>y&&box[2]<z+1&&box[5]>z)throw Error('不能放在自己身上');
   let stack=held&&held.id===id?held:null;
   if(g.mode!=='creative'){stack??=p.inv.slots.find(s=>s&&s.id===id);if(!stack)throw Error('背包里没有 '+BLOCKS[id].key);}
   g.placeAt(x,y,z,id,0,stack);
   return{placed:BLOCKS[id].key};
  },
  chat:({text})=>{if(typeof text!=='string'||!text.trim())throw Error('需要 text');runCommand(playing(),text.trim().slice(0,256));return{sent:true};},
  wait:({ticks=20})=>COMMANDS.move({ticks}),
  build:spec=>applyBuild(playing(),spec),
 };
 async function execute(command){
  try{
   const run=COMMANDS[command?.type];
   if(!run)throw Error('未知命令：'+command?.type+'；可用 '+Object.keys(COMMANDS).join(' '));
   return{ok:true,result:await run(command)};
  }catch(error){return{ok:false,error:error.message};}
 }
 (async()=>{
  let results=[];
  for(;;){
   try{
    const g=window.game,world=g?.state==='play'?g.worldMeta?.id||null:null;
    const response=await fetch('control',{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify({results,world})});
    const data=await response.json();results=[];
    if(!response.ok)throw Error(data.error);
    for(const{id,command}of data.commands||[])results.push({id,result:await execute(command)});
   }catch(_){await new Promise(done=>setTimeout(done,3000));}
  }
 })();
 const apiDialog=dialog('Agent 控制 API','<p>把下面的地址交给 agent 或脚本，就能控制你在这个页面里的角色、读取状态和建造。<b>这个地址等同你的身份，只交给信任的 agent。</b>页面关掉或退出世界后，控制命令会失败，建造会先存进存档，下次打开该世界时自动建好。</p><label>API 地址</label><input type="text" readonly data-url><div class="voxel-tool-actions"><button data-copy>复制地址</button></div><label>示例</label><pre data-example></pre><p>命令：state · blocks · inspect · move · look · select · dig · place · chat · wait · build。完整说明见 <code>GET api</code>。</p><div class="voxel-tool-actions"><button data-cancel>关闭</button></div>');
 $(apiDialog,'[data-cancel]').onclick=()=>apiDialog.close();
 $(apiDialog,'[data-copy]').onclick=async()=>{const input=$(apiDialog,'[data-url]');input.select();try{await navigator.clipboard.writeText(input.value);}catch(_){document.execCommand?.('copy');}};
 function openControl(){
  const url=new URL('api/',location.href.split(/[?#]/)[0]).href;
  $(apiDialog,'[data-url]').value=url;
  $(apiDialog,'[data-example]').textContent=['curl '+url+'state',
   "curl -X POST "+url+"commands -d '{\"commands\":[{\"type\":\"look\",\"yaw\":90},{\"type\":\"move\",\"forward\":1,\"ticks\":40}]}'",
   "curl -X POST "+url+"build -d '{\"origin\":[0,61,0],\"blocks\":[[0,0,0,\"stone\"],[0,1,0,\"glass\"]]}'"].join('\n\n');
  apiDialog.open();
 }
 window.VoxelTools={applyBuild,readBuild,parseLayers,blockId,execute};
})();
