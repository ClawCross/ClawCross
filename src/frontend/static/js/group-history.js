/* Paging old messages never advances live polling or wakes an Agent. */
window.GroupHistory=(()=>{
  let sequence=0;
  const text=(cn,en)=>typeof _currentLang==='undefined'||_currentLang==='zh'?cn:en;
  function attach(gid,messages){
    const current=++sequence;
    if(!String(gid||'').startsWith('rg_'))return;
    const body=document.getElementById('chat-body');if(!body)return;
    const bar=document.createElement('div');bar.className='group-history-bar';
    const button=document.createElement('button');button.type='button';button.textContent=text('加载更早的聊天记录','Load earlier messages');
    const note=document.createElement('span');note.setAttribute('role','status');
    const connection=(typeof groups==='undefined'?[]:groups).find(g=>g.group_id===gid)?.connection_state;
    note.textContent=connection==='paused'?text('联网已暂停，已保存记录可继续浏览','Networking paused · saved messages remain available'):
      connection==='reconnecting'?text('正在重连，当前显示本机记录','Reconnecting · showing saved messages'):text('新消息自动同步，更早历史按需加载','New messages sync automatically; load older history as needed');
    bar.append(button,note);body.prepend(bar);
    let before=messages.length?Math.min(...messages.map(m=>Number(m.id)).filter(Number.isFinite)):0,busy=false;
    button.onclick=async()=>{
      if(busy)return;busy=true;button.disabled=true;note.textContent=text('正在同步历史…','Syncing history…');
      try{
        const data=await api('GET',`/proxy_groups/${encodeURIComponent(gid)}/history?before_id=${before}&limit=50`);
        if(current!==sequence||currentGroupId!==gid||!bar.isConnected)return;
        const rows=(data.messages||[]).filter(m=>!renderedMsgIds.has(m.id));
        const height=body.scrollHeight,top=body.scrollTop;
        if(rows.length){body.querySelector('.refresh-hint')?.remove();bar.insertAdjacentHTML('afterend',rows.map(renderOneMsgHTML).join(''));rows.forEach(m=>renderedMsgIds.add(m.id));}
        before=data.next_before_id||0;
        note.textContent=data.source==='cache'?text('暂时离线，显示本机已保存记录','Offline · showing saved messages'):
          before?text('历史已同步','History synced'):text('已到最早的聊天记录','Beginning of the conversation');
        if(!before)button.hidden=true;
        if(window.ClawcrossMarkdown)ClawcrossMarkdown.highlight(body);
        body.scrollTop=top+body.scrollHeight-height;
      }catch(error){if(current===sequence)note.textContent=error.message||text('同步失败，点击重试','Sync failed. Try again');}
      finally{busy=false;if(current===sequence)button.disabled=false;}
    };
  }
  let availability;
  async function initHostEntry(){
    try{
      availability??=fetch('/proxy_group_host/available').then(r=>r.json()).catch(()=>({available:false}));
      const d=await availability;
      for(const id of ['action-group-host','profile-group-host']){const entry=document.getElementById(id);if(entry)entry.hidden=!d.available;}
    }catch(_){}
  }
  document.addEventListener('DOMContentLoaded',initHostEntry,{once:true});
  return{attach,refreshHostEntry:initHostEntry};
})();
