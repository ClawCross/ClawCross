#!/usr/bin/env node
/* 方块世界 · Voxel World — OpusMC's MIT voxel engine, original procedural assets and member-owned saves.
 * 简介：浏览器方块沙盒，原创程序化材质；每位成员独立存档，支持手机触控。这是单机世界，不是多人联机。
 */
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),os=require('node:os');
const {connect}=require('../../../sdk/js/clawcross-space');
const {Store}=require('../../../sdk/js/space-store');
const root=path.join(__dirname,'vendor/opusmc/game');
const revision='ca677140a47e668aeb2cfab65d4b3057f786f25d';
function createApp(space,{testAssets=process.env.CLAWCROSS_VOXEL_TEST_ASSETS??path.join(os.homedir(),'.clawcross','developer-assets','opusmc-'+revision,'assets.js')}={}){
 const saves=new Map();
 const store=id=>{if(!saves.has(id))saves.set(id,new Store(space.dataDir,{worlds:[],chunks:{}},'member-'+crypto.createHash('sha256').update(id).digest('hex')+'.json'));return saves.get(id);};
 const resources=async id=>{
  const owner=(await space.members()).members.some(m=>m.id===id&&m.owner&&!m.agent);
  const available=!!testAssets&&fs.existsSync(testAssets),mode=owner&&available&&store(id).data.preferences?.testAssets?'upstream':'original';
  return{mode,owner,available:owner&&available};
 };
 const entry=fs.readFileSync(path.join(root,'index.html'),'utf8').replace('js/assets.js','resource-assets.js')
  .replace('<script src="resource-assets.js">','<script>window.VOXEL_LAYOUT='+fs.readFileSync(path.join(__dirname,'asset-layout.json'),'utf8')+';</script><script src="resource-assets.js">')
  .replace('<script src="js/game.js">','<script src="save-bridge.js"></script><script src="js/game.js">')
  .replace('</body>','<script src="touch.js"></script><script src="developer-settings.js"></script><script src="world-tools.js"></script></body>').replaceAll('Minecraft JS 复刻版','方块世界 · OpusMC');
 // Character control: agents queue commands at <app URL>/api/…; the member's open page long-polls /control,
 // runs them in its game and returns the results with its next poll.
 const links=new Map();
 const link=id=>{if(!links.has(id))links.set(id,{queue:[],poll:null,pending:new Map(),seen:0,world:null});return links.get(id);};
 const live=l=>!!l.poll||Date.now()-l.seen<35000;
 const fail=(status,message)=>Object.assign(Error(message),{status});
 function run(id,commands,timeout=120000){
  const l=link(id);
  if(!live(l))throw fail(409,'世界没有在浏览器里打开：先在群里打开方块世界并进入一个世界');
  const results=commands.map(command=>new Promise(resolve=>{
   const key=crypto.randomUUID(),timer=setTimeout(()=>{l.pending.delete(key);resolve({ok:false,error:'页面没有在时限内执行这条命令'});},timeout);
   l.pending.set(key,result=>{clearTimeout(timer);resolve(result);});
   l.queue.push({id:key,command});
  }));
  l.poll?.();
  return Promise.all(results);
 }
 async function poll(req,res,body,reply){
  const l=link(req.member.id);l.seen=Date.now();l.world=typeof body.world==='string'?body.world:null;
  for(const r of Array.isArray(body.results)?body.results:[]){const done=l.pending.get(r?.id);if(done){l.pending.delete(r.id);done(r.result);}}
  if(!l.queue.length){
   l.poll?.();   // a newer poll (another tab, a reconnect) replaces a waiting one
   await new Promise(resolve=>{const timer=setTimeout(finish,25000);function finish(){clearTimeout(timer);if(l.poll===finish)l.poll=null;resolve();}l.poll=finish;res.on('close',finish);});
  }
  l.seen=Date.now();
  if(res.destroyed||res.writableEnded)return;   // queued commands wait for the next poll
  return reply(200,{commands:l.queue.splice(0)});
 }
 const recent=state=>[...state.data.worlds].sort((a,b)=>b.lastPlayed-a.lastPlayed)[0]?.id;
 async function build(member,body,reply){
  if(!Array.isArray(body.blocks)||body.blocks.length>500000)throw fail(400,'需要 {"blocks":[[x,y,z,"方块名"],…]}，一次最多 500000 个');
  if(body.origin!==undefined&&!(Array.isArray(body.origin)&&body.origin.length===3&&body.origin.every(Number.isInteger)))throw fail(400,'origin 需为三个整数');
  const l=link(member);
  if(live(l)&&l.world&&(!body.world||body.world===l.world)){
   const[result]=await run(member,[{type:'build',origin:body.origin,blocks:body.blocks}],300000);
   return reply(result.ok?200:400,result.ok?{built:true,world:l.world,...result.result}:{error:result.error});
  }
  // The world is not open: keep the build in the member's save; the page builds it when that world opens.
  const state=store(member),world=body.world||recent(state);
  if(!world||!state.data.worlds.some(w=>w.id===world))throw fail(404,body.world?'没有这个世界':'还没有世界：先在方块世界里创建一个');
  const entry={id:crypto.randomUUID(),origin:body.origin||[0,0,0],blocks:body.blocks,at:Date.now()};
  state.update(data=>{data.pendingBuilds??={};data.pendingBuilds[world]=[...(data.pendingBuilds[world]||[]),entry];});
  return reply(202,{queued:true,world,id:entry.id,message:'世界没有打开，建筑已存进存档，下次打开该世界时自动建造'});
 }
 const HELP={
  about:'控制你在方块世界页面里的角色。需要页面开着并进入一个世界；build 在世界没打开时会先存进存档。',
  endpoints:{'GET api/state':'角色与世界状态','POST api/commands':'{"commands":[…]} 依次执行，返回每条结果','POST api/build':'{"world"?,"origin"?:[x,y,z],"blocks":[[x,y,z,"方块名",meta?],…]}，"air" 表示删除'},
  commands:{state:'{}',blocks:'{} 全部方块名',inspect:'{"from":[x,y,z],"to":[x,y,z]} 最多 32768 格',move:'{"forward":-1..1,"strafe":-1..1,"jump","sneak","sprint","ticks":1..200}（每秒 20 刻）',
   look:'{"yaw":度,"pitch":度} 或 {"at":[x,y,z]}',select:'{"slot":0..8}',dig:'{"x","y","z"} 够得着的方块',place:'{"x","y","z","block"?} 生存模式需背包里有',
   chat:'{"text"} 也可发 /tp /time /give 等命令',wait:'{"ticks"}',build:'{"origin","blocks"}'},
 };
 async function handle(req,res){
  const reply=(status,data,type='application/json')=>res.writeHead(status,{'Content-Type':type,'Cache-Control':'no-store'}).end(type==='application/json'?JSON.stringify(data):data);
  try{
   const url=new URL(req.url,'http://app');
   if(req.method==='GET'){
    if(url.pathname==='/')return reply(200,entry,'text/html; charset=utf-8');
    if(url.pathname==='/api'||url.pathname==='/api/')return reply(200,HELP);
    if(url.pathname==='/api/state'){const[result]=await run(req.member.id,[{type:'state'}],20000);return reply(result.ok?200:409,result.ok?result.result:{error:result.error});}
    if(url.pathname==='/builds')return reply(200,{builds:store(req.member.id).data.pendingBuilds?.[url.searchParams.get('world')]||[]});
    if(url.pathname==='/resources')return reply(200,await resources(req.member.id));
    if(url.pathname==='/resource-assets.js'){
     const pack=await resources(req.member.id);
     const source=fs.readFileSync(pack.mode==='upstream'?testAssets:path.join(__dirname,'procedural-assets.js'),'utf8');
     return reply(200,'window.VOXEL_PACK='+JSON.stringify(pack)+';\n'+source,'application/javascript; charset=utf-8');
    }
    const file=url.pathname.startsWith('/js/')?path.join(root,url.pathname):path.join(__dirname,url.pathname);
    if(!/^\/(?:js\/)?[a-z-]+\.js$/.test(url.pathname)||!fs.existsSync(file))return reply(404,{error:'文件不存在'});
    return reply(200,fs.readFileSync(file),'application/javascript; charset=utf-8');
   }
   if(!['/saves','/resources','/control','/builds','/api/commands','/api/build'].includes(url.pathname)||req.method!=='POST')return reply(404,{error:'没有此操作'});
   const limit=url.pathname==='/api/build'?16000000:1500000;
   const chunks=[];let bytes=0;for await(const part of req){bytes+=part.length;if(bytes>limit)return reply(413,{error:'请求过大'});chunks.push(part);}
   const body=JSON.parse(Buffer.concat(chunks).toString()||'{}');
   if(url.pathname==='/control')return await poll(req,res,body,reply);
   if(url.pathname==='/api/build')return await build(req.member.id,body,reply);
   if(url.pathname==='/api/commands'){
    if(!Array.isArray(body.commands)||!body.commands.length||body.commands.length>50||!body.commands.every(c=>c&&typeof c.type==='string'))throw fail(400,'需要 {"commands":[{"type":"…"},…]}，最多 50 条');
    return reply(200,{results:await run(req.member.id,body.commands)});
   }
   const state=store(req.member.id),id=body.id||body.world?.id;
   if(url.pathname==='/builds'){
    const done=new Set(Array.isArray(body.done)?body.done:[]);
    state.update(data=>{const list=data.pendingBuilds?.[body.world];if(!list)return;data.pendingBuilds[body.world]=list.filter(b=>!done.has(b.id));if(!data.pendingBuilds[body.world].length)delete data.pendingBuilds[body.world];});
    return reply(200,{ok:true});
   }
   if(url.pathname==='/resources'){
    const pack=await resources(req.member.id);
    if(!pack.owner)return reply(403,{error:'开发测试材质仅供群主本人切换，其他成员继续使用原创材质。'});
    if(typeof body.testAssets!=='boolean')return reply(400,{error:'请选择是否启用开发测试材质。'});
    if(body.testAssets&&!pack.available)return reply(409,{error:'主机尚未配置开发测试素材，仍使用原创材质。'});
    state.update(data=>{data.preferences={...data.preferences,testAssets:body.testAssets};});
    return reply(200,await resources(req.member.id));
   }
   if(body.action!=='list'&&(typeof id!=='string'||!/^[\w-]{1,100}$/.test(id)))return reply(400,{error:'存档编号无效'});
   if(body.action==='list')return reply(200,{worlds:[...state.data.worlds].sort((a,b)=>b.lastPlayed-a.lastPlayed)});
   if(body.action==='get')return reply(200,{world:state.data.worlds.find(w=>w.id===id)||null});
   if(body.action==='chunks')return reply(200,{chunks:Object.entries(state.data.chunks).filter(([k])=>k.startsWith(id+':')).map(([k,v])=>{const[,x,z]=k.split(':');return[+x,+z,v];})});
   state.update(data=>{
    if(body.action==='put'){if(!body.world||body.world.id!==id)throw Error('无效世界');const n=data.worlds.findIndex(w=>w.id===id);if(n<0)data.worlds.push(body.world);else data.worlds[n]=body.world;}
    else if(body.action==='delete'){data.worlds=data.worlds.filter(w=>w.id!==id);for(const key of Object.keys(data.chunks))if(key.startsWith(id+':'))delete data.chunks[key];if(data.pendingBuilds)delete data.pendingBuilds[id];}
    else if(body.action==='saveChunks'){if(!Array.isArray(body.chunks))throw Error('区块无效');for(const[x,z,v]of body.chunks){if(!Number.isInteger(x)||!Number.isInteger(z)||!Array.isArray(v.b)||!Array.isArray(v.m))throw Error('区块无效');data.chunks[id+':'+x+':'+z]=v;}}
    else throw Error('没有此操作');
   });return reply(200,{ok:true});
  }catch(error){if(!res.headersSent)reply(error.status||400,{error:error.message});}
 }
 return{handle};
}
async function main(){const space=await connect({server:process.env.CLAWCROSS_SPACE_SERVER,key:process.env.CLAWCROSS_SPACE_KEY,name:'方块世界'});await space.serve(createApp(space).handle);}
if(require.main===module)main().catch(error=>{console.error(error.message);process.exit(1);});
module.exports={createApp,name:'方块世界'};
