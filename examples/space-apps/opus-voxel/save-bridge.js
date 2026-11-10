/* Member-scoped server saves; works inside an opaque-origin app iframe. */
'use strict';
(()=>{
 const api=async(action,body={})=>{const r=await fetch('saves',{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify({action,...body})});const data=await r.json();if(!r.ok)throw Error(data.error||'存档失败');return data;};
 DB.open=async()=>{DB.backend='server';return true;};
 DB.listWorlds=async()=>(await api('list')).worlds;
 DB.getWorld=async id=>(await api('get',{id})).world;
 DB.putWorld=async world=>api('put',{world});
 DB.deleteWorld=async id=>api('delete',{id});
 DB.loadChunks=async id=>new Map((await api('chunks',{id})).chunks.map(([x,z,v])=>[ckey(x,z),{b:new Uint16Array(v.b),m:new Uint16Array(v.m)}]));
 DB.putChunks=async(id,list)=>{let batch=[],size=0;for(const [x,z,v]of list){const entry=[x,z,{b:Array.from(v.b),m:Array.from(v.m)}],length=JSON.stringify(entry).length;if(size+length>400000&&batch.length){await api('saveChunks',{id,chunks:batch});batch=[];size=0;}batch.push(entry);size+=length;}if(batch.length)await api('saveChunks',{id,chunks:batch});};
})();
