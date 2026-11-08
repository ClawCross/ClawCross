/* Reuse the real Studio conversation, tool history and editors in one shell. */
(() => {
  if(new URLSearchParams(location.search).get('embedded')!=='1')return;
  document.documentElement.classList.add('studio-embedded');
  const style=document.createElement('style');style.textContent=`
    .studio-embedded .studio-activity-bar,.studio-embedded .studio-mobile-menu-toggle,.studio-embedded .studio-conversation-switcher,.studio-embedded #oasis-panel,.studio-embedded .header,.studio-embedded .top-inline-banner{display:none!important}
    .studio-embedded #chat-screen{padding:0!important;grid-template-columns:minmax(0,1fr)!important;gap:0!important}
    .studio-embedded .chat-main{width:100%!important;margin:0!important;min-width:0!important}
    .studio-embedded #page-chat{padding:0!important}
    .studio-embedded #chat-box{padding-top:18px!important}
    .studio-embedded #agent-center-modal{position:fixed!important;inset:0!important;padding:0!important}
    .studio-embedded .agent-center-modal{width:100%!important;height:100%!important;max-height:none!important;border:0!important;border-radius:0!important}
    .studio-embedded #page-group,.studio-embedded #page-orchestrate{height:100%!important}
    .studio-embedded body{background:var(--ui-surface)!important}
  `;document.head.append(style);
  let resolveReady;
  const ready=new Promise(resolve=>resolveReady=resolve);
  if(window.__workspaceStudioReady)resolveReady();
  addEventListener('clawcross:studio-ready',()=>{resolveReady();parent.postMessage({type:'clawcross:studio-ready'},location.origin);});
  let queue=Promise.resolve();
  window.WorkspaceStudio={open(command){
    // Serialize selections; never start a model request as part of navigation.
    queue=queue.catch(()=>{}).then(async()=>{
      await ready;
      if(command.view==='agent'){
        await switchPage('chat');
        const a=command.agent;
        if(['webot','internal'].includes(a.platform))await openAgentSession(a.agent_id,{force:true,quiet:true});
        else{
          if(!_acpToolsCache.includes(a.platform))_acpToolsCache.push(a.platform);
          localStorage.setItem('clawcross_acp_session_pick_'+a.platform,a.agent_id);
          await ocSwitchTo('acp',a.platform);
          const pick=document.getElementById('oc-acp-session-pick');
          if(pick&&pick.value!==a.agent_id){pick.value=a.agent_id;acpOnSessionPickChange();await acpPaintTranscript();}
        }
      }else{
        await switchPage(command.view==='orchestrate'?'orchestrate':command.view==='group'?'group':'chat');
        if(command.compose)ConversationStart.draft(command.compose);
      }
    });return queue;
  }};
})();
