/* Touch controls for the upstream keyboard/mouse game. The original engine remains unchanged. */
'use strict';
(()=>{
 const original=Game.prototype.loadSettings;Game.prototype.loadSettings=function(){const saved=original.call(this);return{renderDist:matchMedia('(pointer:coarse)').matches?3:5,...saved};};
 if(!matchMedia('(pointer:coarse)').matches)return;
 const panel=document.createElement('div');panel.dataset.voxelTouch='';
 panel.innerHTML='<div class="walk"><button data-key="KeyW">↑</button><div><button data-key="KeyA">←</button><button data-key="KeyS">↓</button><button data-key="KeyD">→</button></div></div><div class="actions"><button data-key="Space">跳跃</button><button data-mouse="0">挖掘</button><button data-mouse="2">放置</button><button data-ui="1">背包</button><button data-ui="2">菜单</button></div>';
 const style=document.createElement('style');style.textContent='[data-voxel-touch]{position:fixed;inset:auto 12px 18px;z-index:20;display:flex;justify-content:space-between;pointer-events:none;touch-action:none}[data-voxel-touch] button{pointer-events:auto;background:#182b42b8;color:white;border:1px solid #b6d8e555;border-radius:12px;min-width:43px;height:43px;margin:3px;touch-action:none;font:14px sans-serif}[data-voxel-touch] .walk>button{margin-left:52px}[data-voxel-touch] .actions{max-width:180px;text-align:right}';
 document.head.append(style);document.body.append(panel);
 const buttons=[...panel.querySelectorAll('button')];
 buttons.forEach(button=>{
  button.onpointerdown=event=>{event.preventDefault();event.stopPropagation();button.setPointerCapture(event.pointerId);const g=window.game;if(!g)return;Sound.init();if(button.dataset.key)g.keys[button.dataset.key]=true;if(button.dataset.mouse){g.mouseBtn[+button.dataset.mouse]=true;if(button.dataset.mouse==='0')g.mouseClicked0=true;}if(button.dataset.ui==='1')g.ui.open(g.mode==='creative'?new CreativeScreen(g):new CraftingScreen(g,2));if(button.dataset.ui==='2')g.ui.open(new PauseScreen(g));};
  const release=event=>{event.preventDefault();const g=window.game;if(!g)return;if(button.dataset.key)g.keys[button.dataset.key]=false;if(button.dataset.mouse)g.mouseBtn[+button.dataset.mouse]=false;};
  button.onpointerup=button.onpointercancel=button.onlostpointercapture=release;
 });
 panel.addEventListener('mousedown',e=>{e.stopPropagation();e.preventDefault();});
 let prior=null;const canvas=document.getElementById('gl');
 canvas.addEventListener('touchstart',e=>{const t=e.changedTouches[0];prior={id:t.identifier,x:t.clientX,y:t.clientY};},{passive:true});
 canvas.addEventListener('touchmove',e=>{const g=window.game,t=[...e.changedTouches].find(t=>t.identifier===prior?.id);if(!t||!g||g.ui.screen)return;e.preventDefault();g.player.yaw-=(t.clientX-prior.x)*.006;g.player.pitch=Math.max(-1.55,Math.min(1.55,g.player.pitch-(t.clientY-prior.y)*.006));prior={id:t.identifier,x:t.clientX,y:t.clientY};},{passive:false});
 canvas.addEventListener('touchend',()=>{prior=null;},{passive:true});
 setInterval(()=>{const g=window.game;panel.hidden=!g||g.state!=='play'||!!g.ui.screen;if(g){g.capturePointer=()=>{};if(g.settings.renderDist>4)g.settings.renderDist=4;}},150);
})();
