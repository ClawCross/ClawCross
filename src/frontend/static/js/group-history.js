/* Paging old messages never advances live polling or wakes an Agent. */
window.GroupHistory=(()=>{
  let sequence=0;
  const text=(cn,en)=>typeof _currentLang==='undefined'||_currentLang==='zh'?cn:en;
  function attach(gid,messages,page={}){
    const current=++sequence;
    let before=Number(page.next_before_id);
    if(!String(gid||'').startsWith('rg_')||!messages.length||!Number.isSafeInteger(before)||before<=0)return;
    const body=document.getElementById('chat-body');if(!body)return;
    const bar=document.createElement('div');bar.className='group-history-bar';
    const button=document.createElement('button');button.type='button';button.textContent=text('加载更早的聊天记录','Load earlier messages');
    const note=document.createElement('span');note.setAttribute('role','status');note.hidden=true;
    bar.append(button,note);body.prepend(bar);
    let busy=false;
    button.onclick=async()=>{
      if(busy)return;busy=true;button.disabled=true;button.textContent=text('加载中…','Loading…');note.hidden=true;
      try{
        const data=await api('GET',`/proxy_groups/${encodeURIComponent(gid)}/history?before_id=${before}&limit=50`);
        if(current!==sequence||currentGroupId!==gid||!bar.isConnected)return;
        const rows=(data.messages||[]).filter(m=>!renderedMsgIds.has(m.id));
        const height=body.scrollHeight,top=body.scrollTop;
        if(rows.length){body.querySelector('.refresh-hint')?.remove();bar.insertAdjacentHTML('afterend',rows.map(renderOneMsgHTML).join(''));rows.forEach(m=>renderedMsgIds.add(m.id));}
        before=Number(data.next_before_id)||0;
        if(before<=0)bar.remove();
        if(window.ClawcrossMarkdown)ClawcrossMarkdown.highlight(body);
        body.scrollTop=top+body.scrollHeight-height;
      }catch(error){if(current===sequence){note.textContent=error.message||text('加载失败，点击重试','Could not load messages. Try again');note.hidden=false;}}
      finally{busy=false;if(current===sequence){button.disabled=false;button.textContent=text('加载更早的聊天记录','Load earlier messages');}}
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
