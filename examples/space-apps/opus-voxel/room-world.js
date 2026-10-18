/* The room's shared world. The space's owner opens one of their worlds; members join it.
 * This server keeps its blocks: every change comes here, a member's change is checked (within reach,
 * not inside another player) and sent to everyone. Each page keeps its own player, inventory and health.
 * The owner's page simulates creatures, physics and time; this server passes them on.
 *
 * Messages are JSON over one WebSocket per member (<app URL>/room). Outgoing messages stay under
 * the group servers' 1 MB WebSocket limit by sending chunks in batches. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {Store}=require('../../../sdk/js/space-store');
const {accept}=require('./room-socket');
const FlatLayers=require('./flat-layers');

const BATCH=400000;          // bytes of chunk data per message
const REACH=8;               // blocks from a member's eyes
const SAVE_MS=10000;

// The engine's own block, world and save code, run here without a page.
function engine(){
 const game=path.join(__dirname,'vendor/opusmc/game/js'),layout=JSON.parse(fs.readFileSync(path.join(__dirname,'asset-layout.json'),'utf8'));
 const context={console,ASSETS:{tiles:Object.fromEntries(layout.tiles.map((name,i)=>[name,i])),anims:{},count:layout.tiles.length,images:{}}};
 context.window=context;vm.createContext(context);
 for(const file of['util.js','blocks.js','world.js','save.js'])vm.runInContext(fs.readFileSync(path.join(game,file),'utf8'),context,{filename:file});
 const E=vm.runInContext('({World,Chunk,BI,BLOCKS,HEIGHT,encodeChunk,decodeChunk})',context);
 FlatLayers.install(E.World,E.Chunk,E.BI);
 return E;
}

function createRoom(space){
 const E=engine();
 const store=new Store(space.dataDir,{world:null,open:false,chunks:{},players:{}},'room-world.json');
 const peers=new Map();       // member id -> {conn,id,name,owner,joined,pos}
 const loaded=new Map();      // "cx:cz" -> engine Chunk
 const dirty=new Set();
 let world=null;

 function setupWorld(){
  loaded.clear();dirty.clear();world=null;
  const meta=store.data.world;if(!meta)return;
  world=new E.World(meta.seed);world.flat=!!meta.flat;
  try{world.flatLayers=meta.flatLayers?FlatLayers.parseLayers(meta.flatLayers,E.BLOCKS,E.HEIGHT):null;}catch(_){world.flatLayers=null;}
  world.grassMap=world.foliageMap=new Uint8ClampedArray(256*256*4);
 }
 setupWorld();
 function chunk(cx,cz){
  const key=cx+':'+cz;let ch=loaded.get(key);if(ch)return ch;
  const saved=store.data.chunks[key];
  if(saved){ch=new E.Chunk(cx,cz);E.decodeChunk(saved,ch);}else ch=world.generate(cx,cz);
  loaded.set(key,ch);return ch;
 }
 const record=ch=>{const r=E.encodeChunk(ch);return{b:Array.from(r.b),m:Array.from(r.m)};};
 function blockAt(x,y,z){const ch=chunk(x>>4,z>>4),i=(y<<8)|((z&15)<<4)|(x&15);return[ch.blocks[i],ch.meta[i]];}
 function setBlock(x,y,z,id,meta){
  const ch=chunk(x>>4,z>>4),i=(y<<8)|((z&15)<<4)|(x&15);
  if(ch.blocks[i]===id&&ch.meta[i]===meta)return false;
  ch.blocks[i]=id;ch.meta[i]=meta;dirty.add((x>>4)+':'+(z>>4));return true;
 }
 function save(){
  if(!dirty.size||!world)return;
  const keys=[...dirty];dirty.clear();
  store.update(data=>{for(const key of keys)data.chunks[key]=record(loaded.get(key));});
 }
 const timer=setInterval(save,SAVE_MS);timer.unref?.();

 const send=(peer,message)=>peer.conn.send(JSON.stringify(message));
 function broadcast(message,except,onlyJoined=true){const text=JSON.stringify(message);for(const p of peers.values())if(p!==except&&(!onlyJoined||p.joined))p.conn.send(text);}
 const ownerPeer=()=>[...peers.values()].find(p=>p.owner&&p.joined);
 function status(peer){
  const meta=store.data.world;
  return{t:'status',open:!!(store.data.open&&meta),exists:!!meta,owner:peer.owner,world:meta?{name:meta.name,mode:meta.mode,flat:!!meta.flat}:null,
   ownerOnline:!!ownerPeer(),players:[...peers.values()].filter(p=>p.joined).map(p=>p.name)};
 }
 const announce=()=>{for(const p of peers.values())send(p,status(p));};
 // Every saved chunk, plus changed ones not yet saved, in batches.
 function sendChunks(peer,type){
  const all={...store.data.chunks};for(const key of dirty)all[key]=record(loaded.get(key));
  let list=[],bytes=0;
  for(const[key,rec]of Object.entries(all)){
   const[cx,cz]=key.split(':').map(Number),entry=[cx,cz,rec],size=JSON.stringify(rec).length;
   if(bytes+size>BATCH&&list.length){send(peer,{t:type,list});list=[];bytes=0;}
   list.push(entry);bytes+=size;
  }
  if(list.length)send(peer,{t:type,list});
 }
 const ints=(...values)=>values.every(Number.isInteger);
 const finite=value=>Array.isArray(value)&&value.length===3&&value.every(Number.isFinite);
 // A member's change must be within their reach and must not put a solid block inside another player.
 function allowed(peer,x,y,z,id){
  if(peer.owner)return true;
  if(!peer.pos)return false;
  const[px,py,pz]=peer.pos;
  if(Math.hypot(x+0.5-px,y+0.5-(py+1.62),z+0.5-pz)>REACH)return false;
  if(id&&E.BLOCKS[id]?.solid)for(const other of peers.values()){
   if(other===peer||!other.joined||!other.pos)continue;
   const[ox,oy,oz]=other.pos;
   if(x+1>ox-0.3&&x<ox+0.3&&y+1>oy&&y<oy+1.8&&z+1>oz-0.3&&z<oz+0.3)return false;
  }
  return true;
 }
 function changeBlocks(peer,list){
  if(!Array.isArray(list)||list.length>5000)throw Error('一次最多 5000 个方块改动');
  const accepted=[],refused=[];
  for(const entry of list){
   if(!Array.isArray(entry))continue;
   const[x,y,z,id,meta=0]=entry;
   if(!ints(x,y,z,id,meta)||y<0||y>=E.HEIGHT||!(id===0||E.BLOCKS[id])||meta<0||meta>255)continue;
   if(!allowed(peer,x,y,z,id)){refused.push([x,y,z,...blockAt(x,y,z)]);continue;}
   if(setBlock(x,y,z,id,meta))accepted.push([x,y,z,id,meta]);
  }
  if(accepted.length)broadcast({t:'set',list:accepted},peer);
  if(refused.length)send(peer,{t:'set',list:refused,refused:true});
 }

 const handlers={
  status:peer=>send(peer,status(peer)),
  join(peer){
   const meta=store.data.world;
   if(!store.data.open||!meta)throw Error('群主还没有开放联机世界');
   send(peer,{t:'welcome',you:{id:peer.id,owner:peer.owner},world:{name:meta.name,seed:meta.seed,mode:meta.mode,flat:!!meta.flat,flatLayers:meta.flatLayers||null,spawn:meta.spawn||null,time:meta.time||1000},
    player:store.data.players[peer.id]||null,players:[...peers.values()].filter(p=>p.joined&&p!==peer).map(p=>({id:p.id,name:p.name,pos:p.pos,yaw:p.yaw,pitch:p.pitch}))});
   sendChunks(peer,'chunks');
   send(peer,{t:'ready'});
   peer.joined=true;
   broadcast({t:'pjoin',id:peer.id,name:peer.name},peer);
   announce();
  },
  leave(peer){if(!peer.joined)return;peer.joined=false;broadcast({t:'pleave',id:peer.id},peer);announce();},
  // The owner opens a world: its description first, its saved chunks in batches, then done.
  open(peer,{meta,player}){
   if(!peer.owner)throw Error('只有群主可以开放联机世界');
   if(!meta||!Number.isInteger(meta.seed))throw Error('世界无效');
   for(const p of peers.values())if(p.joined&&p!==peer)send(p,{t:'closed',reason:'群主开放了另一个世界'});
   for(const p of peers.values())p.joined=false;
   store.update(data=>{data.world={name:String(meta.name||'联机世界').slice(0,60),seed:meta.seed,mode:meta.mode==='creative'?'creative':'survival',flat:!!meta.flat,
    flatLayers:typeof meta.flatLayers==='string'?meta.flatLayers:null,spawn:finite(meta.spawn)?meta.spawn:null,time:Number.isFinite(meta.time)?meta.time:1000};
    data.open=false;data.chunks={};data.players=player?{[peer.id]:player}:{};});
   setupWorld();
  },
  openChunks(peer,{list}){
   if(!peer.owner)throw Error('只有群主可以开放联机世界');
   if(!Array.isArray(list))throw Error('区块无效');
   store.update(data=>{for(const[cx,cz,rec]of list){if(!ints(cx,cz)||!Array.isArray(rec?.b)||!Array.isArray(rec?.m))throw Error('区块无效');data.chunks[cx+':'+cz]=rec;}});
  },
  openDone(peer){if(!peer.owner)throw Error('只有群主可以开放联机世界');setupWorld();store.update(data=>{data.open=true;});announce();},
  reopen(peer){if(!peer.owner)throw Error('只有群主可以开放联机世界');if(!store.data.world)throw Error('还没有联机世界');store.update(data=>{data.open=true;});announce();},
  close(peer){
   if(!peer.owner)throw Error('只有群主可以关闭联机世界');
   save();store.update(data=>{data.open=false;});
   for(const p of peers.values())if(p.joined){p.joined=false;if(p!==peer)send(p,{t:'closed',reason:'群主关闭了联机世界'});}
   announce();
  },
  move(peer,{pos,yaw,pitch,held,sneak}){
   if(!peer.joined||!finite(pos))return;
   peer.pos=pos;peer.yaw=+yaw||0;peer.pitch=+pitch||0;
   broadcast({t:'pmove',id:peer.id,pos,yaw:peer.yaw,pitch:peer.pitch,held:Number.isInteger(held)?held:null,sneak:!!sneak},peer);
  },
  set(peer,{list}){if(peer.joined)changeBlocks(peer,list);},
  mobs(peer,message){if(peer.owner&&peer.joined&&Array.isArray(message.list))broadcast({t:'mobs',list:message.list},peer);},
  hit(peer,{mob,damage,from}){const owner=ownerPeer();if(peer.joined&&owner&&owner!==peer&&Number.isInteger(mob)&&Number.isFinite(damage))send(owner,{t:'hit',mob,damage:Math.min(40,Math.max(0,damage)),from:finite(from)?from:null});},
  time(peer,{time,weather,rain}){
   if(!peer.owner||!peer.joined||!Number.isFinite(time))return;
   if(store.data.world)store.data.world.time=time;   // saved with the next chunk save
   broadcast({t:'time',time,weather:+weather||0,rain:+rain||0},peer);
  },
  save(peer,{player}){if(peer.joined&&player&&JSON.stringify(player).length<65536)store.update(data=>{data.players[peer.id]=player;});},
 };

 async function upgrade(req,socket,head){
  const conn=accept(req,socket,head);if(!conn)return;
  const id=req.member.id,name=req.member.name||'玩家';
  let owner=false;try{owner=(await space.members()).members.some(m=>m.id===id&&m.owner&&!m.agent);}catch(_){}
  peers.get(id)?.conn.close(4000);   // one connection per member: the newest page wins
  const peer={conn,id,name,owner,joined:false,pos:null};peers.set(id,peer);
  send(peer,status(peer));
  conn.on('message',text=>{
   let message;try{message=JSON.parse(text);}catch(_){return;}
   try{handlers[message.t]?.(peer,message);}catch(error){send(peer,{t:'error',error:error.message,about:message.t});}
  });
  conn.on('close',()=>{if(peers.get(id)!==peer)return;peers.delete(id);if(peer.joined)broadcast({t:'pleave',id},peer);announce();});
 }
 function close(){save();clearInterval(timer);for(const p of peers.values())p.conn.close(1001);}
 return{upgrade,close,status:id=>{const p=peers.get(id);return status(p||{owner:false});},save};
}
module.exports={createRoom};
