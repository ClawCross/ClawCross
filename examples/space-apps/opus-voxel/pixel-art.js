/* 方块世界 original pixel art ("浮岛工坊"): every texture is drawn here from code, tile by tile.
 * No third-party image is copied, traced or sampled. Runs in the page and in Node (for previews).
 * Tiles are 16×16 RGBA and tile seamlessly; textures the engine tints (grass, leaves, water) are light grey. */
'use strict';
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.VoxelPixelArt=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
 // ── canvas-free drawing ───────────────────────────────────────────────────
 const hashOf=s=>Array.from(String(s)).reduce((n,c)=>Math.imul(n^c.charCodeAt(0),16777619)>>>0,2166136261);
 function rng(seed){let a=seed>>>0;return()=>{a=(a+0x6D2B79F5)>>>0;let t=a;t=Math.imul(t^(t>>>15),t|1);t^=t+Math.imul(t^(t>>>7),t|61);return((t^(t>>>14))>>>0)/4294967296;};}
 const hex=h=>{const n=parseInt(h.slice(1),16);return[n>>16&255,n>>8&255,n&255];};
 const mix=(a,b,t)=>a.map((v,i)=>Math.round(v+(b[i]-v)*t));
 const shade=(c,f)=>f>=0?mix(c,[255,255,255],f):mix(c,[0,0,0],-f);
 function image(w=16,h=16){return{w,h,data:new Uint8ClampedArray(w*h*4)};}
 function put(img,x,y,c,a=255){
  x=((x%img.w)+img.w)%img.w;y=((y%img.h)+img.h)%img.h;const i=(y*img.w+x)*4,d=img.data;
  if(a>=255||d[i+3]===0){d[i]=c[0];d[i+1]=c[1];d[i+2]=c[2];d[i+3]=a;return;}
  const k=a/255;d[i]+=(c[0]-d[i])*k;d[i+1]+=(c[1]-d[i+1])*k;d[i+2]+=(c[2]-d[i+2])*k;d[i+3]=Math.max(d[i+3],a);
 }
 const clear=(img,x,y)=>{const i=(y*img.w+x)*4;img.data[i+3]=0;};
 const alphaAt=(img,x,y)=>img.data[(y*img.w+x)*4+3];
 function rect(img,x,y,w,h,c,a){for(let j=0;j<h;j++)for(let i=0;i<w;i++)put(img,x+i,y+j,c,a);}
 function frame(img,x,y,w,h,c,a){for(let i=0;i<w;i++){put(img,x+i,y,c,a);put(img,x+i,y+h-1,c,a);}for(let j=1;j<h-1;j++){put(img,x,y+j,c,a);put(img,x+w-1,y+j,c,a);}}
 function line(img,x0,y0,x1,y1,c,a){const n=Math.max(Math.abs(x1-x0),Math.abs(y1-y0),1);for(let k=0;k<=n;k++)put(img,Math.round(x0+(x1-x0)*k/n),Math.round(y0+(y1-y0)*k/n),c,a);}
 function disc(img,cx,cy,r,c,a){for(let y=Math.floor(cy-r);y<=Math.ceil(cy+r);y++)for(let x=Math.floor(cx-r);x<=Math.ceil(cx+r);x++)if((x-cx)**2+(y-cy)**2<=r*r)put(img,x,y,c,a);}
 // Smooth value noise that wraps every 16 pixels, so neighbouring blocks join without seams.
 function noise(r,cells=4,size=16){
  const g=Array.from({length:cells*cells},r),at=(i,j)=>g[((j%cells+cells)%cells)*cells+((i%cells+cells)%cells)];
  return(x,y)=>{const fx=x*cells/size,fy=y*cells/size,i=Math.floor(fx),j=Math.floor(fy),u=fx-i,v=fy-j,s=t=>t*t*(3-2*t);
   return at(i,j)*(1-s(u))*(1-s(v))+at(i+1,j)*s(u)*(1-s(v))+at(i,j+1)*(1-s(u))*s(v)+at(i+1,j+1)*s(u)*s(v);};
 }
 function field(img,r,colors,{cells=4,grain=0.35}={}){
  const n=noise(r,cells,img.w),m=noise(r,cells*2,img.w);
  for(let y=0;y<img.h;y++)for(let x=0;x<img.w;x++){const v=Math.min(0.999,Math.max(0,n(x,y)*0.6+m(x,y)*0.4+(r()-0.5)*grain));put(img,x,y,colors[Math.floor(v*colors.length)]);}
 }
 const ramp=(base,steps=[-0.28,-0.14,0,0.12,0.24])=>steps.map(f=>shade(hex(base),f));
 const grey=v=>[v,v,v];

 // ── materials ─────────────────────────────────────────────────────────────
 const PAL={
  limestone:ramp('#bdb6a6'),shale:ramp('#5c6b79'),dirt:ramp('#94654a'),sand:ramp('#e0cfa2',[-0.12,-0.06,0,0.06,0.12]),redsand:ramp('#c7835a',[-0.12,-0.06,0,0.06,0.12]),
  gravel:[hex('#6f6c69'),hex('#8a8580'),hex('#a39d96'),hex('#7b7f86'),hex('#5d5a58')],snow:ramp('#eef4f6',[-0.1,-0.05,0,0.03,0.05]),
  clay:ramp('#9fa9b4',[-0.1,-0.05,0,0.05,0.1]),terracotta:ramp('#b8714f',[-0.12,-0.06,0,0.05,0.1]),moss:ramp('#4f9a86'),obsidian:ramp('#2a2236',[-0.3,-0.15,0,0.12,0.25]),
  bedrock:[hex('#2b2b2e'),hex('#46464b'),hex('#5f5f66'),hex('#1d1d20'),hex('#77777f')],netherrack:ramp('#7c3b3e'),
 };
 const WOOD={oak:'#b48653',birch:'#ddd2b8',spruce:'#6e4d36',dark_oak:'#4c3524',jungle:'#b07d57',acacia:'#c7703f',cherry:'#e2aaa6',mangrove:'#8d3f3a'};
 const BARK={oak:'#6c5034',birch:'#e8e2d2',spruce:'#3e2c20'};
 const DYE={white:'#eceae4',orange:'#e08a3c',magenta:'#c25bb4',light_blue:'#79b8de',yellow:'#e9cf4a',lime:'#8cc74b',pink:'#e7a0b8',gray:'#5b6066',light_gray:'#a3a7a8',
  cyan:'#2f9a9c',purple:'#7e45b0',blue:'#3d58a8',brown:'#7a5233',green:'#4f7a33',red:'#b23a33',black:'#25252a'};
 const GEM={diamond:'#6fe0d8',gold:'#f2c35a',iron:'#e2a688',coal:'#2e2b2b',redstone:'#d8343a',lapis:'#3f5fd0',emerald:'#3fcf77',copper:'#d9875a'};
 const METAL={iron:'#d7d9dc',gold:'#f1c24c',diamond:'#69d9d0',emerald:'#3ac06f',lapis:'#3557bd',redstone:'#bf2a2f',coal:'#2c2a2a',wooden:'#b48653',stone:'#9a958c',golden:'#f1c24c',leather:'#9a5f39'};

 function stone(img,r,pal=PAL.limestone){
  field(img,r,pal,{cells:2,grain:0.25});
  for(let k=0;k<3;k++){const y0=Math.floor(r()*16),wave=r()*6;for(let x=0;x<16;x++){const y=y0+Math.round(Math.sin((x+wave)/16*Math.PI*2)*1.2);put(img,x,y,pal[0],120);}}
 }
 function speckle(img,r,base,dots,amount=22){field(img,r,base,{cells:2,grain:0.2});for(let k=0;k<amount;k++){const x=Math.floor(r()*16),y=Math.floor(r()*16),c=dots[k%dots.length];put(img,x,y,c);if(r()<0.4)put(img,x+1,y,c);}}
 function cobble(img,r,pal=PAL.limestone,mortar=null){
  const seeds=Array.from({length:9},()=>[r()*16,r()*16,Math.floor(r()*3)+1]);
  for(let y=0;y<16;y++)for(let x=0;x<16;x++){
   let a=99,b=99,who=0;
   for(const[i,[sx,sy]]of seeds.entries())for(const ox of[-16,0,16])for(const oy of[-16,0,16]){const d=Math.hypot(x-sx-ox,y-sy-oy);if(d<a){b=a;a=d;who=i;}else if(d<b)b=d;}
   const edge=b-a,c=pal[seeds[who][2]];
   put(img,x,y,edge<0.8?(mortar||shade(pal[0],-0.18)):edge<1.8?shade(c,-0.08):(y+x)%7===0?shade(c,0.08):c);
  }
 }
 function bricks(img,r,face,mortar,{rows=4,cols=2}={}){
  rect(img,0,0,16,16,mortar);const h=16/rows,w=16/cols;
  for(let row=0;row<rows;row++){const off=row%2?w/2:0;for(let k=-1;k<=cols;k++){
   const x0=Math.round(k*w+off),c=shade(face,(r()-0.5)*0.12);
   for(let y=row*h;y<row*h+h-1;y++)for(let x=x0;x<x0+w-1;x++)put(img,x,y,(y===row*h)?shade(c,0.1):(x===x0+w-2)?shade(c,-0.1):c);}}
 }
 function planks(img,r,base){
  const c=hex(base);
  for(let row=0;row<4;row++){const tone=shade(c,(r()-0.5)*0.1),g=noise(r,2);
   for(let y=row*4;y<row*4+4;y++)for(let x=0;x<16;x++){const grain=Math.sin((x+row*5)/2.3+g(x,y)*5)*0.5+0.5;put(img,x,y,y===row*4+3?shade(c,-0.3):shade(tone,(grain-0.5)*0.12));}
   const seam=Math.floor(r()*16);put(img,seam,row*4+1,shade(c,-0.25));put(img,seam,row*4+2,shade(c,-0.25));}
 }
 function logSide(img,r,bark,birch){
  const c=hex(bark);
  for(let x=0;x<16;x++){const ridge=(x*7+Math.floor(r()*3))%5;for(let y=0;y<16;y++)put(img,x,y,shade(c,ridge===0?-0.2:ridge===3?0.08:(r()-0.5)*0.06));}
  if(birch)for(let k=0;k<6;k++){const x=Math.floor(r()*14),y=Math.floor(r()*16),w=2+Math.floor(r()*3);rect(img,x,y,w,1,hex('#3c3a36'));}
 }
 function logTop(img,r,wood,bark){
  const c=hex(wood),b=hex(bark);
  for(let y=0;y<16;y++)for(let x=0;x<16;x++){const d=Math.max(Math.abs(x-7.5),Math.abs(y-7.5));put(img,x,y,d>6.6?b:Math.floor(d+r()*0.4)%2?shade(c,-0.1):shade(c,0.06));}
 }
 function leaves(img,r){
  const n=noise(r,4);
  for(let y=0;y<16;y++)for(let x=0;x<16;x++){const v=n(x,y)+(r()-0.5)*0.35;if(v<0.3)continue;put(img,x,y,grey(Math.round(150+v*70+((x*3+y*5)%4===0?-30:0))));}
 }
 function grassTop(img,r){field(img,r,[grey(150),grey(168),grey(184),grey(198),grey(212)],{cells:4,grain:0.6});}
 function fringe(img,r,c,depth=4){for(let x=0;x<16;x++){const d=depth-1+Math.floor(r()*3);for(let y=0;y<d;y++)put(img,x,y,shade(c,(r()-0.5)*0.15));}}
 function gemCluster(img,r,color,deep){
  const c=hex(color),spots=[[3,4],[10,3],[6,10],[12,11],[2,12]].slice(0,deep?4:5);
  for(const[x,y]of spots){if(r()<0.25)continue;put(img,x,y,shade(c,0.35));put(img,x+1,y,c);put(img,x,y+1,c);put(img,x+1,y+1,shade(c,-0.25));if(r()<0.5)put(img,x+2,y+1,shade(c,-0.1));}
 }
 function metalBlock(img,r,color){
  const c=hex(color);rect(img,0,0,16,16,c);
  for(let y=0;y<16;y++)for(let x=0;x<16;x++){if(x+y<6)put(img,x,y,shade(c,0.25),140);if(x+y>24)put(img,x,y,shade(c,-0.2),140);}
  frame(img,0,0,16,16,shade(c,-0.3));frame(img,1,1,14,14,shade(c,0.15),160);line(img,3,9,9,3,shade(c,0.4),120);
 }
 function wool(img,r,color){const c=hex(color);for(let y=0;y<16;y++)for(let x=0;x<16;x++)put(img,x,y,shade(c,((x*3+y*2+(y>>1))%5-2)*0.035+(r()-0.5)*0.05));}
 function concrete(img,r,color){const c=hex(color);for(let y=0;y<16;y++)for(let x=0;x<16;x++)put(img,x,y,shade(c,(r()-0.5)*0.04));}
 // Clear glass keeps a see-through middle; stained glass is a translucent colour.
 function glass(img,r,tint){
  const c=tint?hex(tint):[236,246,248];
  if(tint)rect(img,0,0,16,16,c,120);
  frame(img,0,0,16,16,tint?shade(c,0.2):c,220);line(img,3,5,5,3,[255,255,255],200);line(img,4,8,8,4,[255,255,255],120);
 }
 function liquid(img,r,base,glow){
  const n=noise(r,3),m=noise(r,6);
  for(let y=0;y<16;y++)for(let x=0;x<16;x++){const v=Math.sin((n(x,y)*4+m(x,y)*2+y/5)*Math.PI);put(img,x,y,glow?mix(hex('#d4521d'),hex('#ffd27a'),v*0.5+0.5):grey(Math.round(190+v*40)),glow?255:205);}
 }
 // Small transparent sprites: plants, items and particles.
 function plant(img,r,stem,petal,shape){
  const s=stem?hex(stem):null;
  if(shape==='grass'){for(let k=0;k<7;k++){const x=1+Math.floor(r()*14),h=6+Math.floor(r()*8);line(img,x,15,x+Math.round((r()-0.5)*3),15-h,grey(170+Math.floor(r()*50)));}return;}
  if(shape==='fern'){line(img,8,15,8,3,grey(180));for(let y=4;y<15;y+=2){const w=Math.max(1,5-Math.abs(9-y)/2);line(img,8,y,8-w,y-1,grey(190));line(img,8,y,8+w,y-1,grey(170));}return;}
  if(shape==='bush'){const c=hex('#8b6a43');line(img,8,15,8,8,c);line(img,8,10,4,5,c);line(img,8,9,12,4,c);line(img,6,7,4,8,c);line(img,10,6,13,7,c);return;}
  if(shape==='cane'){for(const x of[4,8,12]){line(img,x,0,x,15,s);for(let y=2;y<16;y+=5)put(img,x,y,shade(s,-0.3));put(img,x+1,(x*3)%16,shade(s,0.2));}return;}
  if(shape==='sapling'){line(img,8,15,8,8,hex('#6c5034'));disc(img,8,6,3.5,s);disc(img,6,8,2,shade(s,-0.15));disc(img,10,7,2,shade(s,0.1));return;}
  if(shape==='mushroom'){const p=hex(petal);rect(img,7,10,2,6,hex('#e6dccb'));for(let x=3;x<13;x++)for(let y=6;y<11;y++)if((x-7.5)**2/25+(y-10)**2/16<1)put(img,x,y,y<8?shade(p,0.1):p);if(petal==='#c8433b'){put(img,5,8,[245,240,230]);put(img,9,7,[245,240,230]);}return;}
  line(img,8,15,8,8,s);line(img,8,12,5,10,s);put(img,4,10,s);
  const p=hex(petal);
  if(shape==='tulip'){rect(img,6,4,5,4,p);put(img,6,3,p);put(img,8,3,p);put(img,10,3,p);rect(img,7,5,3,2,shade(p,0.15));return;}
  if(shape==='bell'){for(const[x,y]of[[6,4],[9,6],[7,8]]){put(img,x,y,p);put(img,x,y+1,shade(p,-0.1));}line(img,8,8,7,4,s);return;}
  if(shape==='ball'){disc(img,8,5,3,p);put(img,7,4,shade(p,0.3));return;}
  disc(img,8,6,3.2,p);put(img,8,6,shape==='daisy'?hex('#e8c43c'):shade(p,-0.35));put(img,7,5,shade(p,0.25));
 }
 function crop(img,r,stage){const h=2+stage*1.7,c=stage<7?hex('#6fa64a'):hex('#d2b04c');for(const x of[2,5,8,11,14]){line(img,x,15,x+(x%2?1:-1),15-h,c);if(stage>4)put(img,x,15-Math.floor(h),shade(c,0.25));}}
 function torch(img){rect(img,7,6,2,10,hex('#7a5a3a'));rect(img,7,4,2,2,hex('#ffd67a'));put(img,7,3,hex('#fff2c4'));put(img,8,5,hex('#f08a3a'));}
 function fire(img,r){for(let x=0;x<16;x++){const h=6+Math.floor((Math.sin(x*1.7)*0.5+0.5)*8+r()*2);for(let y=16-h;y<16;y++){const t=(16-y)/h;put(img,x,y,mix(hex('#ffe08a'),hex('#e2481c'),t),t>0.9?150:235);}}}
 function cracks(img,r,stage){const n=3+stage*3,c=[24,22,22];for(let k=0;k<n;k++){let x=8,y=8;const dx=r()*2-1,dy=r()*2-1;for(let s=0;s<3+stage;s++){x+=dx*1.6+(r()-0.5);y+=dy*1.6+(r()-0.5);put(img,Math.round(x),Math.round(y),c,170);}}}
 function bar(img,c){const m=hex(c);for(let y=6;y<11;y++)for(let x=3+(10-y);x<13-(y-6);x++)put(img,x,y,y===6?shade(m,0.3):y===10?shade(m,-0.3):m);}
 function gem(img,c){const m=hex(c);for(let y=3;y<13;y++){const w=y<6?y-2:12-y;for(let x=8-w;x<=8+w;x++)put(img,x,y,x<8?shade(m,0.2):x===8?shade(m,0.45):m);}put(img,7,5,[255,255,255]);}
 function lump(img,r,c,count=5){const m=hex(c);for(let k=0;k<count;k++){const x=4+r()*8,y=5+r()*7;disc(img,x,y,1.8+r(),shade(m,(r()-0.5)*0.3));}put(img,7,6,shade(m,0.4));}
 function tool(img,kind,material){
  const handle=hex('#7a5a3a'),head=hex(METAL[material]||'#999999');
  line(img,3,13,10,6,handle);line(img,4,13,11,6,shade(handle,-0.2));
  const H=(pts)=>pts.forEach(([x,y])=>{put(img,x,y,head);put(img,x+1,y,shade(head,-0.2));});
  if(kind==='pickaxe')H([[5,3],[6,3],[7,3],[8,3],[9,3],[10,4],[11,5],[12,6],[12,7],[4,4],[3,5]]);
  else if(kind==='axe')H([[9,2],[10,2],[11,3],[12,4],[12,5],[11,6],[10,6],[9,5],[9,4],[8,3]]);
  else if(kind==='shovel')H([[10,3],[11,3],[12,4],[12,5],[11,6],[10,6],[9,5],[9,4]]);
  else if(kind==='hoe')H([[8,3],[9,3],[10,3],[11,4],[12,5]]);
  else{const b=head;for(let k=0;k<9;k++){put(img,5+k,10-k,b);put(img,6+k,10-k,shade(b,0.25));put(img,5+k,11-k,shade(b,-0.25));}line(img,3,11,6,14,hex('#5c4632'));}
 }
 function armor(img,piece,material){
  const m=hex(METAL[material]),d=shade(m,-0.3);
  if(piece==='helmet'){rect(img,3,4,10,4,m);rect(img,3,8,2,3,m);rect(img,11,8,2,3,m);frame(img,3,4,10,4,d);}
  else if(piece==='chestplate'){rect(img,3,3,10,10,m);for(let y=3;y<7;y++){clear(img,6,y);clear(img,7,y);clear(img,8,y);clear(img,9,y);}rect(img,1,4,2,4,m);rect(img,13,4,2,4,m);frame(img,3,7,10,6,d);}
  else if(piece==='leggings'){rect(img,4,3,8,3,m);rect(img,4,6,3,8,m);rect(img,9,6,3,8,m);frame(img,4,3,8,3,d);}
  else{rect(img,3,9,4,5,m);rect(img,9,9,4,5,m);rect(img,2,12,5,2,d);rect(img,9,12,5,2,d);}
 }
 function egg(img,r,a,b){const A=hex(a),B=hex(b);for(let y=3;y<14;y++)for(let x=4;x<12;x++)if((x-7.5)**2/16+(y-8.5)**2/30<1)put(img,x,y,A);for(let k=0;k<6;k++)disc(img,5+r()*6,5+r()*7,0.9,B);put(img,6,5,shade(A,0.4));}
 function bucket(img,fill){const m=hex('#c9ccd0');for(let y=5;y<14;y++){const w=5-Math.floor((y-5)/3);for(let x=8-w;x<8+w;x++)put(img,x,y,x===8-w?shade(m,0.2):m);}line(img,3,5,8,1,hex('#8a8d91'));line(img,8,1,12,5,hex('#8a8d91'));if(fill)rect(img,4,5,8,2,hex(fill));}
 function food(img,r,c,shape){
  const m=hex(c);
  if(shape==='round'){disc(img,8,8.5,5,m);put(img,6,6,shade(m,0.4));line(img,8,3,9,1,hex('#6b4a2e'));return;}
  if(shape==='loaf'){for(let y=6;y<13;y++)for(let x=2;x<14;x++)if((x-7.5)**2/36+(y-9.5)**2/12<1)put(img,x,y,y<8?shade(m,0.15):m);for(const x of[5,8,11])line(img,x,7,x+1,9,shade(m,-0.25));return;}
  if(shape==='meat'){disc(img,9,7,4.5,m);disc(img,6,10,3,m);line(img,3,13,6,10,hex('#ece4d6'));put(img,2,14,hex('#ece4d6'));put(img,8,6,shade(m,0.25));return;}
  if(shape==='fish'){for(let x=3;x<12;x++)for(let y=5;y<11;y++)if((x-7.5)**2/20+(y-8)**2/8<1)put(img,x,y,m);line(img,12,8,14,5,m);line(img,12,8,14,11,m);put(img,5,7,[30,30,30]);return;}
  if(shape==='long'){line(img,4,12,12,4,m);line(img,5,12,12,5,m);line(img,4,11,11,4,shade(m,0.2));line(img,12,4,13,2,hex('#5fa64a'));return;}
  if(shape==='slice'){for(let y=5;y<14;y++)for(let x=3;x<14;x++)if(y>4+Math.abs(x-8)*0.9)put(img,x,y,y>11?hex('#5d9a4a'):m);put(img,7,9,[30,30,30]);put(img,10,8,[30,30,30]);return;}
  if(shape==='bowl'){for(let y=8;y<13;y++)for(let x=3;x<14;x++)if((x-8)**2/30+(y-8)**2/20<1&&y>=8)put(img,x,y,hex('#9b6b44'));if(c!=='#9b6b44')rect(img,4,8,9,1,m);return;}
  lump(img,r,c,3);
 }
 function particle(img,r,name){
  const n=+(/_(\d+)$/.exec(name)?.[1]||0);
  if(/smoke|generic/.test(name)){disc(img,8,8,1+n*0.45,grey(200-n*6),200-n*10);return;}
  if(/explosion/.test(name)){disc(img,8,8,2+n*0.35,mix(hex('#fff1c4'),hex('#7a6a5a'),n/15),230-n*8);return;}
  if(/splash|bubble/.test(name)){frame(img,6,6,4,4,hex('#d8eef6'),220);put(img,7,7,[255,255,255]);return;}
  if(/flame|lava/.test(name)){disc(img,8,9,2.5,hex('#f08a3a'));disc(img,8,8,1.2,hex('#ffe08a'));return;}
  if(/heart/.test(name)){for(const[x,y]of[[6,6],[7,6],[9,6],[10,6],[5,7],[6,7],[7,7],[8,7],[9,7],[10,7],[11,7],[6,8],[7,8],[8,8],[9,8],[10,8],[7,9],[8,9],[9,9],[8,10]])put(img,x,y,hex('#e2606a'));return;}
  if(/note/.test(name)){rect(img,9,4,1,7,[240,240,240]);disc(img,8,11,1.6,[240,240,240]);return;}
  if(/leaf/.test(name)){disc(img,8,8,2,grey(180));put(img,9,7,grey(220));return;}
  if(/angry/.test(name)){line(img,5,5,10,10,hex('#c4373b'));line(img,10,5,5,10,hex('#c4373b'));return;}
  disc(img,8,8,1.5,/crit|damage/.test(name)?hex('#ead9a2'):/glint|effect/.test(name)?hex('#c6a7ef'):grey(230));
 }

 // ── tiles by name ─────────────────────────────────────────────────────────
 const has=(name,...words)=>words.some(w=>name.includes(w));
 function drawTile(name){
  const img=image(),r=rng(hashOf(name));
  const wood=Object.keys(WOOD).find(w=>name.startsWith(w+'_'));
  const dye=Object.keys(DYE).sort((a,b)=>b.length-a.length).find(d=>name.startsWith(d+'_'));
  const oreName=Object.keys(GEM).find(g=>name.includes(g+'_ore'));
  // items and particles
  if(name.startsWith('i_'))return item(name.slice(2),img,r);
  if(name.startsWith('p_')){particle(img,r,name.slice(2));return img;}
  if(name.startsWith('destroy_stage_')){cracks(img,r,+name.slice(14));return img;}
  if(oreName){stone(img,r,name.startsWith('deepslate')?PAL.shale:PAL.limestone);gemCluster(img,r,GEM[oreName],name.startsWith('deepslate'));return img;}
  if(name.endsWith('_wool')&&dye){wool(img,r,DYE[dye]);return img;}
  if(name.endsWith('_concrete')&&dye){concrete(img,r,DYE[dye]);return img;}
  if(name.endsWith('_stained_glass')&&dye){glass(img,r,DYE[dye]);return img;}
  if(wood&&name.endsWith('_planks')){planks(img,r,WOOD[wood]);return img;}
  if(wood&&name.endsWith('_log')){logSide(img,r,BARK[wood]||WOOD[wood],wood==='birch');return img;}
  if(wood&&name.endsWith('_log_top')){logTop(img,r,WOOD[wood],BARK[wood]||WOOD[wood]);return img;}
  if(name.endsWith('_leaves')){leaves(img,r);return img;}
  if(name.endsWith('_sapling')){plant(img,r,{oak:'#5f9a4a',birch:'#86ad5a',spruce:'#3f6d4a'}[wood]||'#5f9a4a',null,'sapling');return img;}
  if(/_door_(top|bottom)$/.test(name)){planks(img,r,WOOD[wood]||WOOD.oak);frame(img,0,0,16,16,shade(hex(WOOD[wood]||WOOD.oak),-0.35));if(name.endsWith('top')){rect(img,3,3,10,6,[200,226,232],170);frame(img,3,3,10,6,shade(hex(WOOD[wood]||WOOD.oak),-0.3));}else put(img,12,5,hex('#3a3a3a'));return img;}
  if(name.startsWith('wheat_stage')){crop(img,r,+name.slice(11));return img;}
  if(name.startsWith('red_bed')||name.startsWith('bed_')){wool(img,r,DYE.red);if(name.includes('head'))rect(img,0,0,16,6,hex('#eeeae2'));frame(img,0,0,16,16,shade(hex(WOOD.oak),-0.2));return img;}
  const flower={dandelion:['#e9c83c','ball'],poppy:['#cf3b37','cup'],cornflower:['#4c6fd6','cup'],oxeye_daisy:['#f1efe6','daisy'],azure_bluet:['#c7dcf0','daisy'],allium:['#a368d6','ball'],
   blue_orchid:['#3fa6dc','cup'],red_tulip:['#d23b34','tulip'],orange_tulip:['#e2832f','tulip'],lily_of_the_valley:['#f4f2ea','bell']}[name];
  if(flower){plant(img,r,'#4d8f45',flower[0],flower[1]);return img;}
  switch(name){
   case 'stone':stone(img,r);return img;
   case 'cobblestone':cobble(img,r);return img;
   case 'mossy_cobblestone':cobble(img,r);for(let k=0;k<26;k++){const x=Math.floor(r()*16),y=Math.floor(r()*16);put(img,x,y,PAL.moss[Math.floor(r()*5)]);put(img,x+1,y,PAL.moss[2]);}return img;
   case 'smooth_stone':field(img,r,PAL.limestone.slice(1,4),{cells:2,grain:0.1});frame(img,0,0,16,16,PAL.limestone[0]);return img;
   case 'smooth_stone_slab_side':field(img,r,PAL.limestone.slice(1,4),{cells:2,grain:0.1});rect(img,0,7,16,2,PAL.limestone[0]);frame(img,0,0,16,16,PAL.limestone[0]);return img;
   case 'andesite':speckle(img,r,ramp('#8d9499',[-0.08,-0.04,0,0.04,0.08]),[hex('#6c7378'),hex('#b5bcc0')]);return img;
   case 'diorite':speckle(img,r,ramp('#d9d4c8',[-0.06,-0.03,0,0.03,0.05]),[hex('#8f8a80'),hex('#f4f1ea')],30);return img;
   case 'granite':speckle(img,r,ramp('#b07f6c',[-0.08,-0.04,0,0.04,0.08]),[hex('#7d5244'),hex('#d7a896')],34);return img;
   case 'polished_andesite':case 'polished_diorite':case 'polished_granite':{const base={polished_andesite:'#8d9499',polished_diorite:'#d9d4c8',polished_granite:'#b07f6c'}[name];field(img,r,ramp(base,[-0.04,-0.02,0,0.02,0.04]),{cells:2,grain:0.1});frame(img,0,0,16,16,shade(hex(base),-0.2));frame(img,1,1,14,14,shade(hex(base),0.12),120);return img;}
   case 'deepslate':stone(img,r,PAL.shale);for(let k=0;k<16;k+=3)line(img,0,k,15,k+3,PAL.shale[1],110);return img;
   case 'deepslate_top':for(let y=0;y<16;y++)for(let x=0;x<16;x++)put(img,x,y,PAL.shale[(Math.floor(Math.hypot(x-7.5,y-7.5)+r()*0.5))%3+1]);return img;
   case 'dirt':case 'grass_side_base':field(img,r,PAL.dirt,{cells:3,grain:0.35});for(let k=0;k<6;k++)put(img,Math.floor(r()*16),Math.floor(r()*16),hex('#c49a7a'));return img;
   case 'grass_block_top':grassTop(img,r);return img;
   case 'grass_block_side':field(img,r,PAL.dirt,{cells:3,grain:0.35});fringe(img,r,hex('#6aa889'));return img;
   case 'grass_block_side_overlay':fringe(img,r,grey(190));return img;
   case 'grass_block_snow':field(img,r,PAL.dirt,{cells:3,grain:0.35});fringe(img,r,hex('#eef4f6'));return img;
   case 'farmland':case 'farmland_moist':{const pal=name.endsWith('moist')?PAL.dirt.map(c=>shade(c,-0.25)):PAL.dirt;field(img,r,pal,{cells:3,grain:0.2});for(let y=1;y<16;y+=4)line(img,0,y,15,y,shade(pal[0],-0.2));return img;}
   case 'bedrock':field(img,r,PAL.bedrock,{cells:4,grain:0.7});return img;
   case 'sand':field(img,r,PAL.sand,{cells:2,grain:1.1});return img;
   case 'red_sand':field(img,r,PAL.redsand,{cells:2,grain:1.1});return img;
   case 'gravel':cobble(img,r,PAL.gravel,hex('#55524f'));return img;
   case 'sandstone':case 'sandstone_bottom':field(img,r,PAL.sand.slice(0,4),{cells:2,grain:0.2});for(const y of[3,8,12])line(img,0,y,15,y,PAL.sand[0]);return img;
   case 'sandstone_top':field(img,r,PAL.sand.slice(1),{cells:2,grain:0.15});frame(img,0,0,16,16,PAL.sand[0]);return img;
   case 'snow':field(img,r,PAL.snow,{cells:3,grain:0.3});return img;
   case 'ice':for(let y=0;y<16;y++)for(let x=0;x<16;x++)put(img,x,y,mix(hex('#a9d6ec'),[255,255,255],r()*0.25),190);line(img,2,12,11,3,[255,255,255],200);return img;
   case 'packed_ice':field(img,r,ramp('#9cc7e6',[-0.06,-0.03,0,0.04,0.08]),{cells:3,grain:0.2});line(img,1,9,8,2,[240,250,255]);return img;
   case 'clay':field(img,r,PAL.clay,{cells:3,grain:0.2});return img;
   case 'terracotta':field(img,r,PAL.terracotta,{cells:3,grain:0.25});return img;
   case 'mud_bricks':bricks(img,r,hex('#8a6a52'),hex('#5c4535'),{rows:4,cols:3});return img;
   case 'moss_block':field(img,r,PAL.moss,{cells:4,grain:0.5});return img;
   case 'obsidian':field(img,r,PAL.obsidian,{cells:3,grain:0.4});for(let k=0;k<4;k++)put(img,Math.floor(r()*16),Math.floor(r()*16),hex('#9b7fd6'));return img;
   case 'netherrack':field(img,r,PAL.netherrack,{cells:4,grain:0.6});return img;
   case 'magma':field(img,r,[hex('#3b1f1a'),hex('#5a2a1e'),hex('#7a3320'),hex('#e0742c'),hex('#f8b04c')],{cells:4,grain:0.3});return img;
   case 'glass':glass(img,r);return img;
   case 'glass_pane_top':rect(img,7,0,2,16,[230,244,247],220);return img;
   case 'water_still':case 'water_flow':liquid(img,r);return img;
   case 'lava_still':case 'lava_flow':liquid(img,r,null,true);return img;
   case 'bricks':bricks(img,r,hex('#b2603f'),hex('#d8c8b4'));return img;
   case 'stone_bricks':bricks(img,r,PAL.limestone[2],PAL.limestone[0],{rows:2,cols:2});return img;
   case 'mossy_stone_bricks':bricks(img,r,PAL.limestone[2],PAL.limestone[0],{rows:2,cols:2});for(let k=0;k<30;k++)put(img,Math.floor(r()*16),Math.floor(r()*16),PAL.moss[Math.floor(r()*5)]);return img;
   case 'cracked_stone_bricks':bricks(img,r,PAL.limestone[2],PAL.limestone[0],{rows:2,cols:2});line(img,3,1,7,6,PAL.limestone[0]);line(img,7,6,6,12,PAL.limestone[0]);return img;
   case 'prismarine':field(img,r,ramp('#5aa79a'),{cells:3,grain:0.4});return img;
   case 'dark_prismarine':bricks(img,r,hex('#2f5b55'),hex('#1f3e3a'),{rows:2,cols:2});return img;
   case 'prismarine_bricks':bricks(img,r,hex('#6bb7aa'),hex('#3f7f74'),{rows:4,cols:2});return img;
   case 'quartz_block_side':case 'quartz_block_top':field(img,r,ramp('#eee8de',[-0.05,-0.02,0,0.02,0.04]),{cells:2,grain:0.1});if(name.endsWith('side'))for(const x of[4,11])line(img,x,0,x,15,hex('#d9d1c4'));return img;
   case 'bone_block_side':case 'bone_block_top':field(img,r,ramp('#e6dfcb',[-0.05,-0.02,0,0.02,0.04]),{cells:2,grain:0.15});if(name.endsWith('top'))disc(img,8,8,3,hex('#c8bfa8'));else for(const y of[3,12])line(img,0,y,15,y,hex('#c8bfa8'));return img;
   case 'hay_block_side':for(let x=0;x<16;x++)for(let y=0;y<16;y++)put(img,x,y,shade(hex('#d5b04e'),((x*5)%3-1)*0.08+(r()-0.5)*0.05));rect(img,0,5,16,1,hex('#8a6a2a'));rect(img,0,11,16,1,hex('#8a6a2a'));return img;
   case 'hay_block_top':for(let y=0;y<16;y++)for(let x=0;x<16;x++)put(img,x,y,shade(hex('#d5b04e'),(Math.floor(Math.hypot(x-7.5,y-7.5))%2?-0.08:0.05)));return img;
   case 'sea_lantern':field(img,r,ramp('#cfeee8',[-0.05,0,0.05,0.1,0.15]),{cells:2,grain:0.2});frame(img,0,0,16,16,hex('#8ccfc4'));rect(img,6,6,4,4,[255,255,255]);return img;
   case 'glowstone':cobble(img,r,[hex('#b98a3a'),hex('#f2c35a'),hex('#ffe08a'),hex('#fff2c4')],hex('#8a6428'));return img;
   case 'iron_block':case 'gold_block':case 'diamond_block':case 'emerald_block':case 'lapis_block':case 'redstone_block':case 'coal_block':metalBlock(img,r,METAL[name.split('_')[0]]);return img;
   case 'crafting_table_top':planks(img,r,WOOD.oak);frame(img,0,0,16,16,shade(hex(WOOD.oak),-0.35));for(const k of[5,10]){line(img,k,1,k,14,shade(hex(WOOD.oak),-0.3));line(img,1,k,14,k,shade(hex(WOOD.oak),-0.3));}return img;
   case 'crafting_table_side':case 'crafting_table_front':planks(img,r,WOOD.oak);rect(img,0,0,16,3,shade(hex(WOOD.oak),-0.25));if(name.endsWith('front')){line(img,4,6,4,13,hex('#8e9093'));rect(img,3,5,3,2,hex('#c9ccd0'));line(img,11,6,11,13,hex('#7a5a3a'));disc(img,11,6,1.5,hex('#9a958c'));}else{rect(img,3,6,4,6,hex('#7a5a3a'));rect(img,9,6,4,6,hex('#6a4a2e'));}return img;
   case 'furnace_top':case 'furnace_side':stone(img,r,PAL.limestone.map(c=>shade(c,-0.08)));frame(img,0,0,16,16,PAL.limestone[0]);return img;
   case 'furnace_front':case 'furnace_front_on':stone(img,r,PAL.limestone.map(c=>shade(c,-0.08)));frame(img,0,0,16,16,PAL.limestone[0]);rect(img,4,8,8,5,name.endsWith('on')?hex('#f08a3a'):hex('#232120'));if(name.endsWith('on'))rect(img,5,10,6,2,hex('#ffe08a'));rect(img,4,4,8,2,PAL.limestone[0]);return img;
   case 'chest_top':case 'chest_side':case 'chest_front':planks(img,r,'#a5713f');frame(img,0,0,16,16,hex('#5a3c22'));if(name!=='chest_top')rect(img,0,6,16,1,hex('#5a3c22'));if(name==='chest_front'){rect(img,7,5,2,4,hex('#d7d9dc'));put(img,7,7,hex('#5a5a5a'));}return img;
   case 'bookshelf':planks(img,r,WOOD.oak);for(const y0 of[2,9]){rect(img,1,y0,14,5,hex('#3b2a1c'));for(let x=1;x<15;x+=2){const c=hex(Object.values(DYE)[Math.floor(r()*16)]);rect(img,x,y0+(r()<0.3?1:0),2,5,shade(c,-0.15));put(img,x,y0+1,shade(c,0.2));}}return img;
   case 'tnt_side':for(let y=0;y<16;y++)for(let x=0;x<16;x++)put(img,x,y,shade(hex('#c4473a'),((x%4)===0?-0.2:0)+(r()-0.5)*0.05));rect(img,0,5,16,6,hex('#efe9dc'));rect(img,5,7,6,2,hex('#2a2a2a'));return img;
   case 'tnt_top':case 'tnt_bottom':rect(img,0,0,16,16,hex('#c4473a'));for(let k=0;k<16;k+=4)frame(img,k,0,4,16,shade(hex('#c4473a'),-0.2));if(name==='tnt_top')disc(img,8,8,1.5,hex('#2a2a2a'));return img;
   case 'pumpkin_side':case 'pumpkin_top':case 'carved_pumpkin':case 'jack_o_lantern':for(let y=0;y<16;y++)for(let x=0;x<16;x++)put(img,x,y,shade(hex('#d98a36'),(x%5===0?-0.2:0)+(r()-0.5)*0.05));if(name==='pumpkin_top'){disc(img,8,8,2,hex('#6b8a3a'));}else if(name!=='pumpkin_side'){const c=name==='jack_o_lantern'?hex('#ffe08a'):hex('#3a2410');rect(img,4,5,2,2,c);rect(img,10,5,2,2,c);rect(img,5,10,6,2,c);}return img;
   case 'melon_side':case 'melon_top':for(let y=0;y<16;y++)for(let x=0;x<16;x++)put(img,x,y,shade(hex('#6aa142'),(Math.floor((x+(name==='melon_top'?y:0))/3)%2?-0.15:0.05)));return img;
   case 'cactus_side':case 'cactus_top':case 'cactus_bottom':rect(img,0,0,16,16,hex('#4f8f4a'));if(name==='cactus_side'){for(const x of[2,7,12])line(img,x,0,x,15,hex('#3e7a3a'));for(let k=0;k<10;k++)put(img,Math.floor(r()*16),Math.floor(r()*16),hex('#e8e4c8'));}else disc(img,8,8,4,hex('#5fa358'));frame(img,0,0,16,16,hex('#3e7a3a'));return img;
   case 'sugar_cane':plant(img,r,'#8fc06a',null,'cane');return img;
   case 'dead_bush':plant(img,r,null,null,'bush');return img;
   case 'short_grass':plant(img,r,null,null,'grass');return img;
   case 'fern':plant(img,r,null,null,'fern');return img;
   case 'red_mushroom':plant(img,r,null,'#c8433b','mushroom');return img;
   case 'brown_mushroom':plant(img,r,null,'#9a7353','mushroom');return img;
   case 'torch':torch(img);return img;
   case 'fire_0':fire(img,r);return img;
   case 'ladder':for(const x of[2,13])rect(img,x,0,1,16,hex('#7a5a3a'));for(const y of[1,5,9,13])rect(img,2,y,12,2,hex('#9a7046'));return img;
   case 'oak_trapdoor':planks(img,r,WOOD.oak);frame(img,0,0,16,16,shade(hex(WOOD.oak),-0.35));for(const[x,y]of[[4,4],[11,4],[4,11],[11,11]]){clear(img,x,y);clear(img,x+1,y);clear(img,x,y+1);clear(img,x+1,y+1);}return img;
   case 'lily_pad':for(let y=2;y<14;y++)for(let x=2;x<14;x++)if((x-8)**2+(y-8)**2<34&&!(x>7&&y<8&&x-8<8-y))put(img,x,y,shade(hex('#4f8f45'),(r()-0.5)*0.12));return img;
   case 'note_block':case 'jukebox_side':planks(img,r,'#7a4f33');frame(img,0,0,16,16,hex('#4a2f1e'));if(name==='note_block'){rect(img,9,4,1,7,hex('#2a1a10'));disc(img,8,11,1.5,hex('#2a1a10'));}return img;
   case 'jukebox_top':planks(img,r,'#7a4f33');frame(img,0,0,16,16,hex('#4a2f1e'));rect(img,4,7,8,2,hex('#1f1410'));return img;
   case 'white':rect(img,0,0,16,16,[255,255,255]);return img;
  }
  // Anything unnamed above: a calm, recognisable block in the name's own colour.
  field(img,r,ramp('#'+(hashOf(name)&0xffffff).toString(16).padStart(6,'0')).map(c=>mix(c,[150,150,150],0.45)),{cells:2,grain:0.2});frame(img,0,0,16,16,[0,0,0],50);
  return img;
 }
 function item(name,img,r){
  const tools=/^(wooden|stone|iron|golden|diamond)_(pickaxe|axe|shovel|sword|hoe)$/.exec(name);
  if(tools){tool(img,tools[2],tools[1]);return img;}
  const gear=/^(iron|diamond|leather)_(helmet|chestplate|leggings|boots)$/.exec(name);
  if(gear){armor(img,gear[2],gear[1]);return img;}
  const spawn=/^(\w+)_spawn_egg$/.exec(name);
  if(spawn){const c={pig:['#e9a6a8','#c77b7e'],cow:['#5a4232','#d8d2c4'],sheep:['#ece8e0','#c7b9a6'],chicken:['#f2efe6','#d84a3a'],zombie:['#3f8a8a','#5c7a3a'],skeleton:['#c8c8c8','#6a6a6a'],creeper:['#4f9a4a','#1f1f1f'],spider:['#3a3230','#a8302c']}[spawn[1]]||['#999','#666'];egg(img,r,...c);return img;}
  const D={
   stick:()=>{line(img,4,12,11,5,hex('#8a6a46'));line(img,5,12,12,5,hex('#6a4e32'));},
   coal:()=>lump(img,r,'#2e2b2b'),charcoal:()=>lump(img,r,'#3a2a22'),flint:()=>lump(img,r,'#4a4a50',3),
   raw_iron:()=>lump(img,r,'#c79a82'),raw_gold:()=>lump(img,r,'#e2b44a'),iron_ingot:()=>bar(img,'#d7d9dc'),gold_ingot:()=>bar(img,'#f1c24c'),brick:()=>bar(img,'#b2603f'),
   diamond:()=>gem(img,'#6fe0d8'),emerald:()=>gem(img,'#3fcf77'),lapis_lazuli:()=>lump(img,r,'#3f5fd0',4),redstone:()=>{for(let k=0;k<12;k++)put(img,4+Math.floor(r()*8),5+Math.floor(r()*7),hex('#d8343a'));},
   apple:()=>food(img,r,'#d8433a','round'),golden_apple:()=>food(img,r,'#f1c24c','round'),bread:()=>food(img,r,'#c98f4e','loaf'),cookie:()=>{disc(img,8,8,5,hex('#c9884a'));for(let k=0;k<5;k++)put(img,5+Math.floor(r()*7),5+Math.floor(r()*7),hex('#4a2a1a'));},
   pumpkin_pie:()=>food(img,r,'#d98a36','loaf'),baked_potato:()=>food(img,r,'#d9a85a','loaf'),potato:()=>food(img,r,'#c9a066','loaf'),
   porkchop:()=>food(img,r,'#e59a96','meat'),cooked_porkchop:()=>food(img,r,'#b9774a','meat'),beef:()=>food(img,r,'#c7463f','meat'),cooked_beef:()=>food(img,r,'#7a4a2e','meat'),
   mutton:()=>food(img,r,'#d06a5e','meat'),cooked_mutton:()=>food(img,r,'#8f5a3a','meat'),chicken:()=>food(img,r,'#f0c8b0','meat'),cooked_chicken:()=>food(img,r,'#c98a4a','meat'),rotten_flesh:()=>food(img,r,'#7f8a4a','meat'),
   cod:()=>food(img,r,'#a9a28a','fish'),cooked_cod:()=>food(img,r,'#c9a066','fish'),carrot:()=>food(img,r,'#e2832f','long'),golden_carrot:()=>food(img,r,'#f1c24c','long'),melon_slice:()=>food(img,r,'#e05a5a','slice'),
   bowl:()=>food(img,r,'#9b6b44','bowl'),mushroom_stew:()=>food(img,r,'#b9874a','bowl'),
   wheat:()=>{for(const x of[5,8,11]){line(img,x,14,x,4,hex('#c9a84a'));put(img,x-1,5,hex('#e2c46a'));put(img,x+1,6,hex('#e2c46a'));}},wheat_seeds:()=>{for(let k=0;k<5;k++)put(img,5+Math.floor(r()*6),6+Math.floor(r()*6),hex('#7aa14a'));},
   bone:()=>{line(img,4,12,12,4,hex('#ece4d6'));disc(img,4,12,1.3,hex('#ece4d6'));disc(img,12,4,1.3,hex('#ece4d6'));},bone_meal:()=>{for(let k=0;k<10;k++)put(img,5+Math.floor(r()*7),6+Math.floor(r()*7),hex('#f2eee6'));},
   arrow:()=>{line(img,3,13,12,4,hex('#8a6a46'));line(img,12,4,13,3,hex('#c9ccd0'));put(img,11,3,hex('#c9ccd0'));put(img,13,5,hex('#c9ccd0'));line(img,3,11,4,13,hex('#ece8e0'));},
   bow:()=>{for(let k=0;k<10;k++)put(img,3+k,12-Math.round(Math.sin(k/9*Math.PI)*4)-k+3,hex('#8a6a46'));line(img,3,12,12,3,hex('#e8e4dc'));},
   string:()=>{for(let k=0;k<12;k++)put(img,2+k,8+Math.round(Math.sin(k)*2),hex('#ece8e0'));},gunpowder:()=>{for(let k=0;k<14;k++)put(img,4+Math.floor(r()*8),6+Math.floor(r()*7),grey(80+Math.floor(r()*60)));},
   feather:()=>{line(img,4,13,12,3,hex('#d8d4cc'));for(let k=0;k<7;k++){put(img,6+k,10-k,hex('#f2efe8'));put(img,5+k,9-k,hex('#f2efe8'));}},leather:()=>{rect(img,4,4,8,9,hex('#9a5f39'));frame(img,4,4,8,9,hex('#6f4226'));},
   shears:()=>{line(img,4,4,11,11,hex('#c9ccd0'));line(img,11,4,4,11,hex('#c9ccd0'));disc(img,4,12,1.5,hex('#b2603f'));disc(img,12,12,1.5,hex('#b2603f'));},
   flint_and_steel:()=>{rect(img,9,3,3,5,hex('#4a4a50'));line(img,3,12,8,7,hex('#c9ccd0'));line(img,3,13,6,13,hex('#c9ccd0'));},
   bucket:()=>bucket(img),water_bucket:()=>bucket(img,'#4f7fd8'),lava_bucket:()=>bucket(img,'#f08a3a'),
   oak_door:()=>{rect(img,4,1,8,14,hex(WOOD.oak));rect(img,5,2,6,4,[200,226,232]);put(img,10,9,hex('#3a3a3a'));},spruce_door:()=>{rect(img,4,1,8,14,hex(WOOD.spruce));rect(img,5,2,6,4,[200,226,232]);put(img,10,9,hex('#c9ccd0'));},
   sugar:()=>{for(let k=0;k<12;k++)put(img,5+Math.floor(r()*7),7+Math.floor(r()*6),[250,250,250]);},paper:()=>{rect(img,3,3,10,11,hex('#f4f0e6'));line(img,5,6,11,6,hex('#c9c2b2'));line(img,5,9,10,9,hex('#c9c2b2'));},
   book:()=>{rect(img,3,3,10,11,hex('#8a3a32'));rect(img,4,4,1,9,hex('#f4f0e6'));frame(img,3,3,10,11,hex('#5a2420'));},sugar_cane:()=>plant(img,r,'#8fc06a',null,'cane'),
   clay_ball:()=>disc(img,8,8,4,hex('#9fa9b4')),egg:()=>egg(img,r,'#f1e6d2','#e2d2b8'),snowball:()=>{disc(img,8,8,4.5,hex('#f4f8fa'));put(img,6,6,[255,255,255]);},
  }[name];
  if(D){D();return img;}
  if(name.startsWith('bow_pulling')){D_bow(img);return img;}
  lump(img,r,'#'+(hashOf(name)&0xffffff).toString(16).padStart(6,'0'),4);
  return img;
 }
 function D_bow(img){for(let k=0;k<10;k++)put(img,3+k,3+k,hex('#8a6a46'));line(img,3,12,12,3,hex('#e8e4dc'));line(img,4,11,11,4,hex('#8a6a46'));}

 // ── interface and sky images ──────────────────────────────────────────────
 const UI={panel:hex('#d9e3df'),edge:hex('#7d9692'),dark:hex('#3d5551'),slot:hex('#8ea6a2'),slotDark:hex('#5f7773'),light:[255,255,255]};
 function panel(img,w,h){
  for(let y=0;y<h;y++)for(let x=0;x<w;x++){const corner=(x<3&&y<3&&(3-x)**2+(3-y)**2>9)||(x>w-4&&y<3&&(x-w+4)**2+(3-y)**2>9)||(x<3&&y>h-4&&(3-x)**2+(y-h+4)**2>9)||(x>w-4&&y>h-4&&(x-w+4)**2+(y-h+4)**2>9);if(!corner)put(img,x,y,UI.panel);}
  for(let x=3;x<w-3;x++){put(img,x,0,UI.edge);put(img,x,h-1,UI.dark);put(img,x,1,UI.light);}
  for(let y=3;y<h-3;y++){put(img,0,y,UI.edge);put(img,w-1,y,UI.dark);put(img,1,y,UI.light);}
 }
 function slot(img,x,y,big){const s=big?26:18,o=big?5:1;rect(img,x-o,y-o,s,s,UI.slot);for(let k=0;k<s;k++){put(img,x-o+k,y-o,UI.slotDark);put(img,x-o,y-o+k,UI.slotDark);put(img,x-o+k,y-o+s-1,UI.light);put(img,x-o+s-1,y-o+k,UI.light);}}
 function arrow(img,x,y,c=UI.edge,shaft=16){rect(img,x,y+6,shaft,4,c);for(let k=0;k<8;k++)line(img,x+shaft+k,y+k,x+shaft+k,y+15-k,c);}
 function flame(img,x,y,c){for(let j=0;j<14;j++)for(let i=0;i<14;i++)if(Math.abs(i-6.5)<j/2.2&&(j>4||Math.abs(i-6.5)<1))put(img,x+i,y+j,c);}
 function playerSlots(img,y0,hot){for(let r=0;r<3;r++)for(let c=0;c<9;c++)slot(img,8+c*18,y0+r*18);for(let c=0;c<9;c++)slot(img,8+c*18,hot);}
 function container(name){
  const img=image(256,256);
  if(name==='inventory'){panel(img,176,166);for(let i=0;i<4;i++)slot(img,8,8+i*18);rect(img,26,8,49,70,UI.dark);rect(img,27,9,47,68,hex('#22312f'));for(let r=0;r<2;r++)for(let c=0;c<2;c++)slot(img,98+c*18,18+r*18);arrow(img,136,28,UI.edge,6);slot(img,154,28);playerSlots(img,84,142);}
  else if(name==='crafting_table'){panel(img,176,166);for(let r=0;r<3;r++)for(let c=0;c<3;c++)slot(img,30+c*18,17+r*18);arrow(img,90,35);slot(img,124,35,true);playerSlots(img,84,142);}
  else if(name==='furnace'){panel(img,176,166);slot(img,56,17);slot(img,56,53);slot(img,116,35,true);arrow(img,79,34,UI.slot);flame(img,56,36,UI.slot);playerSlots(img,84,142);}
  else if(name==='generic_54'){panel(img,176,168);for(let r=0;r<3;r++)for(let c=0;c<9;c++)slot(img,8+c*18,18+r*18);playerSlots(img,85,143);}
  else{panel(img,195,136);if(name!=='tab_inventory')for(let r=0;r<5;r++)for(let c=0;c<9;c++)slot(img,9+c*18,18+r*18);for(let c=0;c<9;c++)slot(img,9+c*18,112);slot(img,173,112);rect(img,174,17,14,92,UI.slotDark);if(name==='tab_item_search')rect(img,80,4,90,12,[255,255,255]);}
  return img;
 }
 function button(w,h,tone){const img=image(w,h),c=tone==='hl'?hex('#7fb3a7'):tone==='dis'?hex('#59625f'):hex('#4c6964');rect(img,0,0,w,h,c);for(let x=0;x<w;x++){put(img,x,0,shade(c,0.3));put(img,x,h-1,shade(c,-0.45));put(img,x,h-2,shade(c,-0.2));}for(let y=0;y<h;y++){put(img,0,y,shade(c,0.2));put(img,w-1,y,shade(c,-0.45));}return img;}
 function icon(name){
  const img=image(9,9),empty=has(name,'empty','container'),half=name.includes('half');
  const shapes={heart:['.##.##...','#########','#########','#########','.#######.','..#####..','...###...','....#....','.........'],
   food:['...###...','..#####..','.#######.','#########','#########','.#######.','..#####..','...#.#...','.........'],
   armor:['#########','#########','#########','#########','.#######.','.#######.','..#####..','...###...','....#....'],
   air:['..####...','.#....#..','#..#...#.','#.##...#.','#......#.','#......#.','.#....#..','..####...','.........']};
  const kind=name.split('_')[0],map=shapes[kind]||shapes.air;
  const c=empty?hex('#2f3c3b'):kind==='heart'?(name.includes('poison')?hex('#7a9a3a'):hex('#e2606a')):kind==='food'?(name.includes('hunger')?hex('#8a9a4a'):hex('#d9a85a')):kind==='armor'?hex('#c9d4d2'):hex('#9fd4ec');
  map.forEach((row,y)=>[...row].forEach((ch,x)=>{if(ch!=='#')return;if(half&&x>4)return put(img,x,y,hex('#2f3c3b'));put(img,x,y,(x+y<5)?shade(c,0.3):c);}));
  if(name.includes('blink'))frame(img,0,0,9,9,[255,255,255],120);
  return img;
 }
 function colormap(kind){
  const img=image(256,256),hot=hex(kind==='grass'?'#a8b85a':'#8aa64a'),cold=hex('#5f9a8f'),wet=hex(kind==='grass'?'#3f9a74':'#2f8a5a');
  for(let y=0;y<256;y++)for(let x=0;x<256;x++){const temp=1-x/255,humid=1-y/255;put(img,x,y,mix(mix(cold,hot,temp),wet,humid*temp*0.8));}
  return img;
 }
 function drawImage(name,[w,h]){
  const img=image(w,h),r=rng(hashOf(name));
  if(['inventory','crafting_table','furnace','generic_54','tab_items','tab_item_search','tab_inventory'].includes(name))return container(name);
  if(name==='button'||name==='slider'||name==='text_field')return button(w,h,name==='text_field'?'dis':'');
  if(name==='button_hl'||name==='text_field_hl'||name==='slider_handle_hl'||name==='rb_button_hl')return button(w,h,'hl');
  if(name==='button_dis')return button(w,h,'dis');
  if(name==='slider_handle'||name==='rb_button')return button(w,h,'');
  if(/^(heart|food|armor|air)/.test(name))return icon(name);
  if(name==='hotbar'){rect(img,0,0,w,h,hex('#22312f'),190);for(let c=0;c<9;c++)frame(img,1+c*20,1,20,20,UI.slot);frame(img,0,0,w,h,UI.dark);return img;}
  if(name==='hotbar_sel'){frame(img,0,0,w,h,[255,255,255]);frame(img,1,1,w-2,h-2,hex('#7fb3a7'));return img;}
  if(name==='crosshair'){rect(img,7,3,1,4,[255,255,255]);rect(img,7,8,1,4,[255,255,255]);rect(img,3,7,4,1,[255,255,255]);rect(img,8,7,4,1,[255,255,255]);return img;}
  if(name==='xp_bg'){rect(img,0,0,w,h,hex('#22312f'));frame(img,0,0,w,h,hex('#0f1716'));return img;}
  if(name==='xp_fg'){rect(img,1,1,w-2,h-2,hex('#7fd6a6'));rect(img,1,1,w-2,1,hex('#c4f2d6'));return img;}
  if(name==='scroller'||name==='scroller_disabled'){rect(img,0,0,w,h,name==='scroller'?hex('#d9e3df'):hex('#8ea6a2'));frame(img,0,0,w,h,UI.dark);return img;}
  if(name==='burn_progress'){arrow(img,0,0,[255,255,255]);return img;}
  if(name==='lit_progress'){flame(img,0,0,hex('#f08a3a'));for(let y=0;y<h;y++)for(let x=0;x<w;x++)if(alphaAt(img,x,y))put(img,x,y,mix(hex('#ffe08a'),hex('#e2481c'),y/h));return img;}
  if(name.startsWith('slot_')){const piece=name.slice(5);armor(img,piece,'iron');for(let i=3;i<img.data.length;i+=4)if(img.data[i])img.data[i]=90;return img;}
  if(name.startsWith('tab_top_')){panel(img,w,h);if(name.includes('unselected'))for(let i=3;i<img.data.length;i+=4)if(img.data[i])img.data[i]=200;return img;}
  if(name==='toast'){panel(img,w,h);return img;}
  if(name==='menu_bg'||name==='options_bg'){field(img,r,ramp('#2f4a52',[-0.1,-0.05,0,0.04,0.08]),{cells:2,grain:0.3});return img;}
  if(name==='header_sep'||name==='footer_sep'){rect(img,0,0,w,1,hex('#9fc4bc'));rect(img,0,1,w,1,hex('#1f2f2c'));return img;}
  if(name==='sun'){disc(img,15.5,15.5,9,hex('#ffe6b0'));disc(img,15.5,15.5,6,hex('#fff6dc'));return img;}
  if(/^moon\d$/.test(name)){const phase=+name.slice(4);disc(img,15.5,15.5,9,hex('#e2edf2'));const off=[0,4,8,12,99,-12,-8,-4][phase];if(off!==99)disc(img,15.5+off,15.5,9,[0,0,0],phase===0?0:255);for(let i=0;i<img.data.length;i+=4)if(img.data[i]+img.data[i+1]+img.data[i+2]===0)img.data[i+3]=0;disc(img,12,13,1.5,hex('#b9c9d0'));return img;}
  if(name==='clouds'){const n=noise(r,8,256);for(let y=0;y<h;y++)for(let x=0;x<w;x++)if(n(x,y)>0.62)put(img,x,y,[255,255,255]);return img;}
  if(name==='rain'){for(let k=0;k<160;k++){const x=Math.floor(r()*w),y=Math.floor(r()*h);line(img,x,y,x,y+5,[170,200,230],170);}return img;}
  if(name==='snowfall'){for(let k=0;k<220;k++)put(img,Math.floor(r()*w),Math.floor(r()*h),[255,255,255],230);return img;}
  if(name==='grass_color'||name==='foliage_color')return colormap(name==='grass_color'?'grass':'foliage');
  if(name==='shadow'){disc(img,w/2-0.5,h/2-0.5,w*0.42,[0,0,0],110);return img;}
  if(name==='vignette'){for(let y=0;y<h;y++)for(let x=0;x<w;x++){const d=Math.hypot(x-w/2,y-h/2)/(w*0.7);put(img,x,y,[0,0,0],Math.max(0,Math.min(255,Math.round((d-0.35)*360))));}return img;}
  if(name==='underwater'){rect(img,0,0,w,h,hex('#2f5fa8'),110);return img;}
  if(name==='arrow_entity'){line(img,2,16,26,16,hex('#8a6a46'));rect(img,26,15,4,3,hex('#c9ccd0'));line(img,2,14,5,16,hex('#ece8e0'));line(img,2,18,5,16,hex('#ece8e0'));return img;}
  if(['pumpkinblur','spider_eyes','font','logo','edition'].includes(name))return img;
  // Creature skins: soft original colourings, until the models get their own designs.
  const skin={pig:'#e9a6a8',cow:'#6a5242',sheep:'#e2dccf',sheep_wool:'#f2efe8',chicken:'#f2efe6',zombie:'#4f8f86',skeleton:'#cfcfcf',creeper:'#5fa65a',spider:'#3a3230',steve:'#c99a78'}[name]||'#999999';
  field(img,r,ramp(skin,[-0.12,-0.06,0,0.05,0.1]),{cells:4,grain:0.3});
  return img;
 }
 return{drawTile,drawImage,hashOf};
});
