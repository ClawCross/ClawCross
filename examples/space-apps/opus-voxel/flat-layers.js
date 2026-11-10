/* Superflat layers, shared by the page and the room server: "bedrock,2*dirt,grass_block", bottom to top. */
'use strict';
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.VoxelFlatLayers=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
 function parseLayers(text,blocks,height){
  const ids=Object.fromEntries(blocks.filter(Boolean).map(b=>[b.key,b.id]));
  const layers=[];let total=0;
  for(const part of String(text).split(',').map(s=>s.trim()).filter(Boolean)){
   const match=/^(?:(\d+)\s*\*\s*)?([a-z0-9_]+)$/.exec(part);
   if(!match)throw Error('无法识别：'+part);
   const count=match[1]===undefined?1:+match[1];
   if(count<1)throw Error('层数至少为 1：'+part);
   if(ids[match[2]]===undefined)throw Error('未知方块：'+match[2]);
   layers.push([count,ids[match[2]]]);total+=count;
  }
  if(!layers.length)throw Error('至少需要一层');
  if(total>=height)throw Error('总层数需小于 '+height);
  return layers;
 }
 const layersHeight=layers=>layers.reduce((sum,[count])=>sum+count,0);
 // A world with flatLayers generates them; other flat worlds keep the engine's own generator.
 function install(World,Chunk,BI){
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
 }
 return{parseLayers,layersHeight,install};
});
