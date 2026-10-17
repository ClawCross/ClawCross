/* Original procedural artwork. No Minecraft images, glyphs or GUI assets are embedded. */
'use strict';
(()=>{
 const layout=window.VOXEL_LAYOUT,canvas=(w,h)=>Object.assign(document.createElement('canvas'),{width:w,height:h});
 const hash=s=>Array.from(s).reduce((n,c)=>(n*31+c.charCodeAt(0))>>>0,17);
 const color=name=>{
  const choices=[[/grass|leaves|foliage|moss|fern|wheat/,'#7aa573'],[/water|ice/,'#81b6c5'],[/lava|fire|magma/,'#e49c60'],[/sand|birch/,'#d5c6a3'],[/dirt|log|wood|oak|chest/,'#aa896a'],[/stone|ore|gravel/,'#89929c']];
  return choices.find(([r])=>r.test(name))?.[1]||'hsl('+(hash(name)%360)+' 32% 64%)';
 };
 const strip=canvas(16,16*layout.tiles.length),x=strip.getContext('2d'),tiles={};
 layout.tiles.forEach((name,index)=>{
  tiles[name]=index;const y=index*16,plant=/flower|poppy|sapling|grass$|sugar_cane|torch|mushroom|fern|p_/.test(name);
  x.fillStyle=color(name);if(plant){x.fillRect(7,y+3,2,13);x.fillRect(2,y+5,12,4);}else x.fillRect(0,y,16,16);
  for(let n=0;n<30;n++){const seed=hash(name+':'+n);x.fillStyle=n%2?'#ffffff18':'#00000012';x.fillRect(seed%16,y+(seed>>>8)%16,2,2);}
  if(/ore/.test(name)){x.fillStyle=/diamond/.test(name)?'#a2e2e4':/gold/.test(name)?'#f6da91':'#c9b2a3';x.fillRect(3,y+4,4,3);x.fillRect(10,y+9,3,4);}
  if(name==='white'){x.fillStyle='white';x.fillRect(0,y,16,16);}
 });
 const images={};
 for(const [name,[w,h]] of Object.entries(layout.images)){
  const c=canvas(w,h),g=c.getContext('2d');
  if(name==='font'){
   g.fillStyle='white';g.font='7px monospace';g.textBaseline='top';for(let code=33;code<127;code++)g.fillText(String.fromCharCode(code),(code%16)*8,Math.floor(code/16)*8,7);
  }else if(name==='logo'||name==='edition'){
   g.fillStyle=name==='logo'?'#e5f5ea':'#9fd8cb';g.font='bold '+(name==='logo'?100:32)+'px sans-serif';g.textAlign='center';g.textBaseline='middle';g.fillText(name==='logo'?'方块世界':'OpusMC × ClawCross',w/2,h/2,w-20);
  }else if(name.includes('color')){
   g.fillStyle=name==='grass_color'?'#8daf74':'#75a47b';g.fillRect(0,0,w,h);
  }else if(name==='vignette'||name==='pumpkinblur'||name==='spider_eyes'){
   // Transparent optional overlays.
  }else if(name==='clouds'){
   g.fillStyle='white';for(let n=0;n<50;n++)g.fillRect(hash('cx'+n)%256,hash('cy'+n)%256,20,9);
  }else if(name==='crosshair'){
   g.fillStyle='white';g.fillRect(7,2,1,11);g.fillRect(2,7,11,1);
  }else if(name==='sun'||name.startsWith('moon')||name==='shadow'){
   g.fillStyle=name==='shadow'?'#0006':name==='sun'?'#ffe1a8':'#d5e7ed';g.beginPath();g.ellipse(w/2,h/2,w*.36,h*.36,0,0,Math.PI*2);g.fill();
  }else if(name.startsWith('heart')||name.startsWith('food')||name.startsWith('armor')||name.startsWith('air')){
   g.fillStyle=name.includes('empty')||name.includes('container')?'#334657':name.startsWith('heart')?'#e99597':'#d6d59f';g.beginPath();g.arc(w/2,h/2,w*.36,0,Math.PI*2);g.fill();
  }else{
   g.fillStyle=/button_hl|sel|progress|xp_fg/.test(name)?'#7bafa4':/button|inventory|crafting|furnace|generic|tab_|toast|hotbar/.test(name)?'#2e414f':color(name);g.fillRect(0,0,w,h);
   g.strokeStyle='#c1d6d566';g.lineWidth=1;g.strokeRect(.5,.5,w-1,h-1);
   if(w===256&&h===256){g.strokeStyle='#a5b8bd44';for(let row=0;row<12;row++)for(let col=0;col<9;col++)g.strokeRect(7+col*18,7+row*18,17,17);}
  }
  images[name]=c.toDataURL('image/png');
 }
 window.ASSETS={strip:strip.toDataURL('image/png'),tiles,anims:{},count:layout.tiles.length,images};
})();
