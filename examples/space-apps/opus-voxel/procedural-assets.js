/* The default pack: original pixel art drawn by pixel-art.js. Text images use the system font.
 * No Minecraft images, glyphs or GUI assets are embedded. */
'use strict';
(()=>{
 const layout=window.VOXEL_LAYOUT,art=window.VoxelPixelArt;
 const canvas=(w,h)=>Object.assign(document.createElement('canvas'),{width:w,height:h});
 const strip=canvas(16,16*layout.tiles.length),g=strip.getContext('2d'),tiles={};
 layout.tiles.forEach((name,index)=>{tiles[name]=index;const tile=art.drawTile(name);g.putImageData(new ImageData(tile.data,16,16),0,index*16);});
 const images={};
 for(const[name,size]of Object.entries(layout.images)){
  const img=art.drawImage(name,size),c=canvas(img.w,img.h),x=c.getContext('2d');
  x.putImageData(new ImageData(img.data,img.w,img.h),0,0);
  if(name==='font'){
   x.fillStyle='white';x.font='7px monospace';x.textBaseline='top';for(let code=33;code<127;code++)x.fillText(String.fromCharCode(code),(code%16)*8,Math.floor(code/16)*8,7);
  }else if(name==='logo'||name==='edition'){
   x.fillStyle=name==='logo'?'#e5f5ea':'#9fd8cb';x.font='bold '+(name==='logo'?100:32)+'px sans-serif';x.textAlign='center';x.textBaseline='middle';x.fillText(name==='logo'?'方块世界':'OpusMC × ClawCross',img.w/2,img.h/2,img.w-20);
  }
  images[name]=c.toDataURL('image/png');
 }
 window.ASSETS={strip:strip.toDataURL('image/png'),tiles,anims:{},count:layout.tiles.length,images};
})();
