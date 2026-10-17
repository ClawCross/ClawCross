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
  .replace('</body>','<script src="touch.js"></script><script src="developer-settings.js"></script></body>').replaceAll('Minecraft JS 复刻版','方块世界 · OpusMC');
 async function handle(req,res){
  const reply=(status,data,type='application/json')=>res.writeHead(status,{'Content-Type':type,'Cache-Control':'no-store'}).end(type==='application/json'?JSON.stringify(data):data);
  try{
   const url=new URL(req.url,'http://app');
   if(req.method==='GET'){
    if(url.pathname==='/')return reply(200,entry,'text/html; charset=utf-8');
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
   if(!['/saves','/resources'].includes(url.pathname)||req.method!=='POST')return reply(404,{error:'没有此操作'});
   const chunks=[];let bytes=0;for await(const part of req){bytes+=part.length;if(bytes>1500000)return reply(413,{error:'存档请求过大'});chunks.push(part);}
   const body=JSON.parse(Buffer.concat(chunks)),state=store(req.member.id),id=body.id||body.world?.id;
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
    else if(body.action==='delete'){data.worlds=data.worlds.filter(w=>w.id!==id);for(const key of Object.keys(data.chunks))if(key.startsWith(id+':'))delete data.chunks[key];}
    else if(body.action==='saveChunks'){if(!Array.isArray(body.chunks))throw Error('区块无效');for(const[x,z,v]of body.chunks){if(!Number.isInteger(x)||!Number.isInteger(z)||!Array.isArray(v.b)||!Array.isArray(v.m))throw Error('区块无效');data.chunks[id+':'+x+':'+z]=v;}}
    else throw Error('没有此操作');
   });return reply(200,{ok:true});
  }catch(error){reply(400,{error:error.message});}
 }
 return{handle};
}
async function main(){const space=await connect({server:process.env.CLAWCROSS_SPACE_SERVER,key:process.env.CLAWCROSS_SPACE_KEY,name:'方块世界'});await space.serve(createApp(space).handle);}
if(require.main===module)main().catch(error=>{console.error(error.message);process.exit(1);});
module.exports={createApp,name:'方块世界'};
