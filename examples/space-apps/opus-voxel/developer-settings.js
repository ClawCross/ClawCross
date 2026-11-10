/* The optional private test pack is never bundled. A member's choice does not alter worlds. */
'use strict';
(()=>{
 const dialog=document.createElement('section');dialog.hidden=true;dialog.className='voxel-resources';dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-label','材质与开发测试');
 const style=document.createElement('style');style.textContent=`
 .voxel-resources[hidden]{display:none}.voxel-resources{position:fixed;inset:0;z-index:20;display:grid;place-items:center;padding:18px;background:#09131dbc;color:#dfefee;font:14px/1.7 system-ui;box-sizing:border-box;overflow:auto}
 .voxel-resource-card{box-sizing:border-box;width:min(100%,460px);max-height:100%;overflow:auto;background:#182e3b;border:1px solid #a9c9c636;border-radius:22px;padding:26px;box-shadow:0 24px 90px #0007}
 .voxel-resources h2{font-size:22px;margin:0 0 12px}.voxel-resources p{font-size:12px;color:#a4c0c3}.voxel-resources label{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 0;border-block:1px solid #c2dbdc22}
 .voxel-resources input{appearance:none;flex:none;width:44px;height:26px;border:1px solid #99babb55;border-radius:20px;background:#0b1c28;position:relative;cursor:pointer}.voxel-resources input:before{content:'';position:absolute;width:18px;height:18px;left:3px;top:3px;background:#c4dadb;border-radius:50%;transition:transform .15s}.voxel-resources input:checked{background:#5d9e8b}.voxel-resources input:checked:before{transform:translateX(18px)}.voxel-resources input:disabled{opacity:.4;cursor:default}
 .voxel-resource-actions{display:flex;gap:10px;margin-top:20px;flex-wrap:wrap}.voxel-resources button{border:1px solid #accac344;border-radius:11px;padding:10px 15px;min-height:42px;background:#a5cdbf12;color:#d9eddf;font:inherit;cursor:pointer}.voxel-resources button.primary{background:#bdded0;color:#153c33}.voxel-resources button:disabled{opacity:.45;cursor:default}.voxel-resources :focus-visible{outline:2px solid #c2e3d9;outline-offset:3px}.voxel-resource-status{min-height:20px;color:#ead5ad!important}
 @media(max-width:420px){.voxel-resource-card{padding:20px}.voxel-resources h2{font-size:20px}}
 `;document.head.append(style);
 const card=document.createElement('div');card.className='voxel-resource-card';
 card.innerHTML='<h2>材质与开发测试</h2><p data-current></p><label><span>完整上游素材<br><small>个人开发测试 · 仅本人显示</small></span><input type="checkbox" role="switch" aria-label="完整上游素材开发测试"></label><p>关闭时使用原创程序化材质。测试素材的贴图、字体与界面不属于 OpusMC 的 MIT 许可，不能因此视为可商用素材。</p><p data-note></p><p class="voxel-resource-status" role="status"></p><div class="voxel-resource-actions"><button class="primary" data-apply>应用并重新加载</button><button data-back>返回选项</button></div>';
 dialog.append(card);document.body.append(dialog);
 const toggle=card.querySelector('input'),apply=card.querySelector('[data-apply]'),status=card.querySelector('[role=status]');
 let pack=null;
 function close(){dialog.hidden=true;}
 for(const name of ['mousedown','mouseup','mousemove','wheel','keydown','keyup'])dialog.addEventListener(name,event=>{event.stopPropagation();if(name==='keydown'&&event.key==='Escape'){event.preventDefault();close();}});
 card.querySelector('[data-back]').onclick=close;
 async function open(){
  if(window.game?.ui)window.game.ui.mouse.down=false;
  dialog.hidden=false;status.textContent='';toggle.disabled=apply.disabled=true;
  try{
   const response=await fetch('resources');pack=await response.json();if(!response.ok)throw Error(pack.error||'无法读取设置');
   toggle.checked=pack.mode==='upstream';
   card.querySelector('[data-current]').textContent='当前材质：'+(window.VOXEL_PACK?.mode==='upstream'?'完整上游素材 · 开发测试':'原创程序化材质');
   const playing=window.game?.state==='play';toggle.disabled=!pack.available||playing;apply.disabled=!pack.owner||playing;
   card.querySelector('[data-note]').textContent=playing?'先保存并退出世界，再从标题页的选项切换，避免重载中断游戏。':!pack.owner?'开发测试素材仅供群主本人切换；你的世界仍使用原创材质。':!pack.available?'主机尚未配置独立的测试素材文件，当前继续使用原创材质。':'切换后会重载画面；世界存档保持不变。';
  }catch(error){status.textContent=error.message;}
  card.querySelector('[data-back]').focus();
 }
 apply.onclick=async()=>{
  if(!pack?.owner||window.game?.state==='play')return;
  apply.disabled=toggle.disabled=true;status.textContent='正在保存设置…';
  try{const response=await fetch('resources',{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify({testAssets:toggle.checked})});const result=await response.json();if(!response.ok)throw Error(result.error||'设置保存失败');location.reload();}
  catch(error){status.textContent=error.message;apply.disabled=false;toggle.disabled=!pack.available;}
 };
 const layout=OptionsScreen.prototype.layout;
 OptionsScreen.prototype.layout=function(){layout.call(this);const u=this.game.ui,done=this.widgets.pop();this.widgets.push({x:u.w/2-155,y:Math.min(u.h-56,184),w:310,h:20,label:'材质与开发测试…',onClick:open},done);};
 window.VoxelResources={open};
})();
