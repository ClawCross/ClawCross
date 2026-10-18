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
 function parseLayers(text){
  const layers=[];let height=0;
  for(const part of String(text).split(',').map(s=>s.trim()).filter(Boolean)){
   const match=/^(?:(\d+)\s*\*\s*)?([a-z0-9_]+)$/.exec(part);
   if(!match)throw Error('无法识别：'+part);
   const count=match[1]===undefined?1:+match[1];
   if(count<1)throw Error('层数至少为 1：'+part);
   layers.push([count,blockId(match[2])]);height+=count;
  }
  if(!layers.length)throw Error('至少需要一层');
  if(height>=HEIGHT)throw Error('总层数需小于 '+HEIGHT);
  return layers;
 }
 const layersHeight=layers=>layers.reduce((sum,[count])=>sum+count,0);
 const generateFlat=World.prototype.generateFlat;
 World.prototype.generateFlat=function(cx,cz){
  if(!this.flatLayers)return generateFlat.call(this,cx,cz);
  const ch=new Chunk(cx,cz),column=[];
  for(const[count,id]of this.flatLayers)for(let k=0;k<count;k++)column.push(id);
  for(let z=0;z<16;z++)for(let x=0;x<16;x++){
   for(let y=0;y<column.length;y++)ch.blocks[(y<<8)|(z<<4)|x]=column[y];
   ch.biome[z*16+x]=BI.PLAINS;
   this.tintFor(this.grassMap,0.3,0.2,ch.grass,(z*16+x)*3);
   this.tintFor(this.foliageMap,0.3,0.2,ch.foliage,(z*16+x)*3);
  }
  ch.recount();
  return ch;
 };
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
})();
