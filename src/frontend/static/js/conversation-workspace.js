/* One navigation shell, existing Studio turns and the existing group chat. */
(() => {
  const labels={agents:['我的 Agent','My agents'],groups:['群聊','Group chats'],directory:['联系人','Directory'],center:['Agent 中心','Agent center'],teams:['团队','Teams'],workflows:['工作流','Workflows'],back:['返回对话列表','Back to chats'],settings:['Agent 设置','Agent settings'],loading:['正在打开对话…','Opening conversation…'],retry:['重新打开','Try again'],failed:['对话暂时无法打开','Conversation could not be opened']};
  const text=key=>window.ClawCrossLocale?.text(...labels[key]) || labels[key][document.documentElement.lang.startsWith('zh')?0:1];
  const icon=name=>`<svg class="ui-icon" aria-hidden="true"><use href="/static/icons.svg#${name}"/></svg>`;
  let agents=[],selected='',frame,host,title,status,retry,revision=0,lastFetch=0,pending,previousFocus;
  const desktop=matchMedia('(min-width: 1024px) and (hover: hover) and (pointer: fine)');
  function localize(){document.querySelectorAll('[data-workspace-text]').forEach(el=>el.textContent=text(el.dataset.workspaceText));}
  function createFrame(){
    if(host)return;
    host=document.createElement('section');host.id='workspace-conversation';host.hidden=true;host.setAttribute('aria-label',text('agents'));
    const header=document.createElement('header');header.className='workspace-conversation-header';
    const back=document.createElement('button');back.type='button';back.className='workspace-back';back.innerHTML=icon('chevron-left');back.setAttribute('aria-label',text('back'));back.onclick=()=>hide(true);
    title=document.createElement('h2');
    const settings=document.createElement('button');settings.type='button';settings.innerHTML=icon('sliders');settings.setAttribute('aria-label',text('settings'));settings.onclick=()=>{if(selected)AgentSettings.open(selected);};settings.id='workspace-agent-settings';
    header.append(back,title,settings);
    frame=document.createElement('iframe');frame.title='Agent conversation';frame.id='workspace-studio-frame';frame.src='/studio?embedded=1';frame.setAttribute('allow','clipboard-write; microphone');
    status=document.createElement('div');status.className='workspace-frame-status';status.setAttribute('role','status');
    retry=document.createElement('button');retry.type='button';retry.textContent=text('retry');retry.hidden=true;retry.onclick=()=>{frame.src='/studio?embedded=1';if(pending)void open(pending);};
    host.append(header,frame,status,retry);document.querySelector('.app-container').append(host);
  }
  function hide(restore=true){
    ++revision;
    if(host)host.hidden=true;
    document.body.classList.remove('workspace-agent-open');
    if(restore)previousFocus?.focus({preventScroll:true});
  }
  function renderAgents(){
    const list=document.getElementById('workspace-agent-list');if(!list)return;
    const keyword=document.getElementById('chat-search-input')?.value.trim().toLowerCase()||'';
    const rows=agents.filter(a=>[a.name,a.platform,a.settings?.title].some(v=>String(v||'').toLowerCase().includes(keyword)));
    const section=document.getElementById('workspace-agent-section');section.hidden=!rows.length;
    list.replaceChildren();
    for(const a of rows){
      const row=document.createElement('button');row.type='button';row.className='workspace-agent-row'+(selected===a.agent_id&&host&&!host.hidden?' selected':'');row.dataset.agentId=a.agent_id;
      row.dataset.agentActivity=a.agent_id;
      row.setAttribute('aria-current',String(selected===a.agent_id&&host&&!host.hidden));
      const portrait=document.createElement('span');portrait.className='workspace-agent-avatar';
      portrait.innerHTML=a.settings?.avatar?Avatars.markup(a.settings.avatar,a.name,{agentId:a.agent_id}):PlatformIcons.markup(a.platform);
      const dot=document.createElement('span');dot.className='agent-activity-dot';dot.dataset.workspaceDot='';dot.hidden=true;portrait.append(dot);
      const body=document.createElement('span');body.className='workspace-agent-copy';
      const heading=document.createElement('span');heading.className='workspace-agent-name';heading.textContent=a.name;
      const badge=document.createElement('span');badge.className='workspace-agent-badge';badge.textContent='Agent';heading.append(badge);
      const meta=document.createElement('small');meta.textContent=[agentPlatformLabel(a.platform),a.location?.kind==='ssh'?'SSH':document.documentElement.lang.startsWith('zh')?'本机':'Local'].filter(Boolean).join(' · ');
      body.append(heading,meta);row.append(portrait,body);row.onclick=()=>open({view:'agent',agent:a});list.append(row);
    }
    localize();
    document.getElementById('workspace-group-heading').hidden=!groups.length;
    // The first-agent guide is only needed when there are neither agents nor chats.
    const guide=document.querySelector('#group-list-container .first-chat-guide');if(guide&&agents.length)guide.hidden=true;
  }
  async function refresh(force=false){
    if(!window.__currentUser)return;
    if(!force&&Date.now()-lastFetch<30000)return;
    if(pendingFetch)return pendingFetch;
    pendingFetch=(async()=>{
      try{const data=await api('GET','/v1/agents');agents=(data.data||[]).filter(a=>!a.archived&&!a.settings?.archived);lastFetch=Date.now();renderAgents();}
      catch(e){console.warn('Agent directory unavailable',e);}
      finally{pendingFetch=null;}
    })();return pendingFetch;
  }
  let pendingFetch;
  async function waitForStudio(){
    const start=Date.now();
    while(!frame.contentWindow?.WorkspaceStudio){if(Date.now()-start>20000)throw Error('Studio unavailable');await new Promise(r=>setTimeout(r,60));}
    return frame.contentWindow.WorkspaceStudio;
  }
  async function open(command){
    previousFocus=document.activeElement;
    if(command.view==='agent'){switchTab('chats');if(currentGroupId)goBack();}
    createFrame();const current=++revision;pending=command;
    selected=command.agent?.agent_id||'';
    title.textContent=command.agent?.name||text(command.view==='group'?'teams':command.view==='orchestrate'?'workflows':'agents');
    document.getElementById('workspace-agent-settings').hidden=!selected;
    host.hidden=false;status.textContent=text('loading');status.hidden=false;retry.hidden=true;
    document.body.classList.add('workspace-agent-open');renderAgents();syncViewport();
    try{
      const studio=await waitForStudio();if(current!==revision)return;
      let timeout;
      try{await Promise.race([studio.open(command),new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error('Studio initialization timeout')),25000);})]);}
      finally{clearTimeout(timeout);}
      if(current!==revision)return;
      status.hidden=true;
      if(desktop.matches)frame.contentWindow.document.querySelector('#user-input')?.focus({preventScroll:true});
    }catch(e){if(current!==revision)return;status.textContent=text('failed');retry.hidden=false;}
  }
  async function openAgentId(id){
    let agent=agents.find(a=>a.agent_id===id);
    if(!agent){await refresh(true);agent=agents.find(a=>a.agent_id===id);}
    if(!agent){toast(text('failed'));return false;}
    await open({view:'agent',agent});return true;
  }
  function contactsView(view){
    document.getElementById('tab-contacts').classList.toggle('workspace-cards',view==='cards');
    document.querySelectorAll('[data-contact-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.contactView===view)));
    localStorage.setItem('clawcross_contacts_view',view);
  }
  function studioView(view,compose){return open({view,compose});}
  function syncViewport(){
    if(!host)return;const vv=visualViewport;
    host.style.setProperty('--workspace-viewport-height',(vv?.height||innerHeight)+'px');
    host.style.setProperty('--workspace-viewport-top',(vv?.offsetTop||0)+'px');
  }
  async function ready(){
    await refresh(true);localize();contactsView(localStorage.getItem('clawcross_contacts_view')||'list');
    const params=new URLSearchParams(location.search);const intent=params.get('compose');
    if(intent)await studioView('chat',intent);
    else if(['group','orchestrate'].includes(params.get('tab'))&&!params.get('redirect')){switchTab('discover');await studioView(params.get('tab'));}
  }
  document.addEventListener('click',event=>{
    const link=event.target.closest('a[href^="/studio?"]');if(!link)return;
    const url=new URL(link.href);if(!url.searchParams.has('compose'))return;
    event.preventDefault();void studioView('chat',url.searchParams.get('compose'));
  });
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&host&&!host.hidden)hide(true);});
  addEventListener('focus',()=>void refresh());
  addEventListener('clawcross:language',()=>{localize();renderAgents();});
  addEventListener('agent-settings-changed',()=>void refresh(true));
  visualViewport?.addEventListener('resize',syncViewport);visualViewport?.addEventListener('scroll',syncViewport);
  window.ConversationWorkspace={ready,refresh,renderAgents,openAgentId,studioView,contactsView,hide};
  localize();
})();
