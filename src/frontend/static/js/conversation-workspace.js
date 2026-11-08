/* One navigation shell, existing Studio turns and the existing group chat. */
(() => {
  const labels={agents:['我的 Agent','My agents'],manage:['管理 Agent','Manage agents'],groups:['群聊','Group chats'],directory:['联系人','Directory'],center:['Agent 中心','Agent center'],teams:['团队','Teams'],workflows:['工作流','Workflows'],back:['返回聊天','Back to chats'],settings:['Agent 设置','Agent settings'],loading:['正在打开对话…','Opening conversation…'],retry:['重新打开','Try again'],failed:['对话暂时无法打开','Conversation could not be opened'],all:['全部','All'],more:['展开全部','Show all'],less:['收起','Show less'],empty:['没有匹配的 Agent','No matching agents'],preferences:['设置','Settings'],language:['语言','Language'],account:['我的账号','My account']};
  const text=key=>window.ClawCrossLocale?.text(...labels[key]) || labels[key][document.documentElement.lang.startsWith('zh')?0:1];
  const icon=name=>`<svg class="ui-icon" aria-hidden="true"><use href="/static/icons.svg?v=20261108-directory2#${name}"/></svg>`;
  let agents=[],selected='',frame,host,title,status,retry,revision=0,lastFetch=0,pending,previousFocus;
  const recentLimit=4;
  let chatView='all',agentsCollapsed=false,agentsExpanded=false,recentAgents={},studioSidebarOpen=false;
  const storageKey=name=>'clawcross_'+name+':'+(window.__currentUser||'');
  const desktop=matchMedia('(min-width: 1024px) and (hover: hover) and (pointer: fine)');
  function localize(){
    document.querySelectorAll('[data-workspace-text]').forEach(el=>el.textContent=text(el.dataset.workspaceText));
    document.getElementById('contacts-list')?.setAttribute('data-center-title',text('center'));
    const heading=document.querySelector('#workspace-agent-collapse [data-workspace-text]');
    if(heading&&chatView==='all')heading.textContent=window.ClawCrossLocale?.lookup('studio_history',ClawCrossLocale.current())||text('agents');
    const manage=document.getElementById('workspace-open-agent-sidebar');
    if(manage){manage.textContent=text('manage');manage.title=text('manage');manage.setAttribute('aria-label',text('manage'));}
    for(const [key,id]of Object.entries({preferences:'workspace-rail-settings',language:'workspace-rail-language',account:'workspace-rail-account'})){
      const button=document.getElementById(id);if(button){button.title=text(key);button.setAttribute('aria-label',text(key));}
    }
    const appearance=document.getElementById('workspace-rail-appearance');
    if(appearance&&window.InterfacePreferences){appearance.title=InterfacePreferences.appearanceLabel();appearance.setAttribute('aria-label',appearance.title);appearance.innerHTML=icon(document.documentElement.dataset.theme==='dark'?'moon':'sun');}
  }
  function installFooter(){
    const bar=document.querySelector('.tab-bar');if(!window.InterfacePreferences||!bar||document.getElementById('workspace-rail-footer'))return;
    const footer=document.createElement('div');footer.id='workspace-rail-footer';
    for(const [id,symbol,action]of [['settings','settings',()=>openSettings()],['appearance','sun',()=>InterfacePreferences.openAppearance()],['language','languages',()=>ClawCrossLocale.open()]]){
      const button=document.createElement('button');button.type='button';button.id='workspace-rail-'+id;button.className='workspace-rail-tool';button.innerHTML=icon(symbol);button.onclick=action;footer.append(button);
    }
    const account=document.createElement('button');account.type='button';account.id='workspace-rail-account';account.className='workspace-rail-tool workspace-rail-avatar';
    account.innerHTML='<span class="tab-icon"></span>';account.onclick=()=>switchTab('profile');footer.append(account);
    const brand=document.createElement('button');brand.type='button';brand.className='workspace-rail-brand';brand.title='ClawCross';brand.setAttribute('aria-label','ClawCross');brand.innerHTML='<img src="/static/assets/clawcross-mark.svg" alt="">';brand.onclick=()=>switchTab('chats');bar.prepend(brand);bar.append(footer);localize();
  }
  function applyListView(){
    document.querySelectorAll('[data-chat-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.chatView===chatView)));
    const visible=chatView!=='agents';
    const groupList=document.getElementById('group-list-container');if(groupList)groupList.hidden=!visible;
    document.getElementById('workspace-group-heading').hidden=!visible||!groups.length;
    const total=document.getElementById('workspace-agent-total');if(total)total.textContent=String(agents.length);
    const groupTotal=document.getElementById('workspace-group-total');if(groupTotal)groupTotal.textContent=String(groups.length);
  }
  function listView(view){
    if(!['all','agents','groups'].includes(view))return;
    chatView=view;localStorage.setItem(storageKey('chat_view'),view);
    document.getElementById('chat-scroll-area').scrollTop=0;renderAgents();
  }
  function toggleAgents(){agentsCollapsed=!agentsCollapsed;renderAgents();}
  function moreAgents(){agentsExpanded=!agentsExpanded;renderAgents();}

  function createFrame(){
    if(host)return;
    host=document.createElement('section');host.id='workspace-conversation';host.hidden=true;host.setAttribute('aria-label',text('agents'));
    const header=document.createElement('header');header.className='workspace-conversation-header';
    const back=document.createElement('button');back.type='button';back.className='workspace-back';back.innerHTML=icon('chevron-left')+'<span class="workspace-back-label" data-workspace-text="back">'+text('back')+'</span>';back.setAttribute('aria-label',text('back'));back.onclick=()=>hide(true);
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
    studioSidebarOpen=false;frame?.contentWindow?.WorkspaceStudio?.sidebar?.(false);
    document.body.classList.remove('workspace-agent-open','workspace-studio-sidebar-open','workspace-editor-open');
    if(restore)previousFocus?.focus({preventScroll:true});
  }
  function renderAgents(){
    const list=document.getElementById('workspace-agent-list');if(!list)return;
    const keyword=document.getElementById('chat-search-input')?.value.trim().toLowerCase()||'';
    const rows=agents.filter(a=>[a.name,a.platform,a.settings?.title].some(v=>String(v||'').toLowerCase().includes(keyword)))
      .sort((a,b)=>(Number(recentAgents[b.agent_id])||0)-(Number(recentAgents[a.agent_id])||0)
        ||(Number(a.created_at)||0)-(Number(b.created_at)||0)||a.agent_id.localeCompare(b.agent_id,undefined,{numeric:true}));
    const section=document.getElementById('workspace-agent-section');section.hidden=chatView==='groups'||(!rows.length&&!keyword);
    const collapse=document.getElementById('workspace-agent-collapse');collapse?.setAttribute('aria-expanded',String(!agentsCollapsed));
    list.hidden=agentsCollapsed;list.replaceChildren();
    const limited=chatView==='all'&&!keyword;
    const visible=limited&&!agentsExpanded?rows.slice(0,recentLimit):rows;
    for(const a of agentsCollapsed?[]:visible){
      const row=document.createElement('button');row.type='button';row.className='workspace-agent-row'+(selected===a.agent_id&&host&&!host.hidden?' selected':'');row.dataset.agentId=a.agent_id;
      row.dataset.agentActivity=a.agent_id;
      row.setAttribute('aria-current',String(selected===a.agent_id&&host&&!host.hidden));
      const portrait=document.createElement('span');portrait.className='workspace-agent-avatar';
      portrait.innerHTML=a.settings?.avatar?Avatars.markup(a.settings.avatar,a.name,{agentId:a.agent_id}):PlatformIcons.markup(a.platform);
      const dot=document.createElement('span');dot.className='agent-activity-dot';dot.dataset.workspaceDot='';dot.hidden=true;portrait.append(dot);
      const body=document.createElement('span');body.className='workspace-agent-copy';
      const heading=document.createElement('span');heading.className='workspace-agent-name';heading.textContent=a.name;
      heading.title=a.name;
      const meta=document.createElement('small');meta.textContent=[agentPlatformLabel(a.platform),a.location?.kind==='ssh'?'SSH':document.documentElement.lang.startsWith('zh')?'本机':'Local'].filter(Boolean).join(' · ');
      body.append(heading,meta);row.append(portrait,body);row.onclick=()=>open({view:'agent',agent:a});list.append(row);
    }
    if(!rows.length&&keyword){const empty=document.createElement('p');empty.className='workspace-agent-empty';empty.textContent=text('empty');list.append(empty);}
    const more=document.getElementById('workspace-agents-more');
    if(more){more.hidden=agentsCollapsed||!limited||rows.length<=recentLimit;more.textContent=agentsExpanded?text('less'):text('more')+' · '+rows.length;more.setAttribute('aria-expanded',String(agentsExpanded));}
    localize();applyListView();
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
    if(selected){recentAgents[selected]=Date.now()/1000;localStorage.setItem(storageKey('recent_agents'),JSON.stringify(recentAgents));}
    title.textContent=command.agent?.name||text(command.view==='group'?'teams':command.view==='orchestrate'?'workflows':'agents');
    document.getElementById('workspace-agent-settings').hidden=!selected;
    host.hidden=false;status.textContent=text('loading');status.hidden=false;retry.hidden=true;
    document.body.classList.add('workspace-agent-open');renderAgents();syncViewport();
    document.body.classList.toggle('workspace-editor-open',['group','orchestrate'].includes(command.view));
    try{
      const studio=await waitForStudio();if(current!==revision)return;
      let timeout;
      try{await Promise.race([studio.open(command),new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error('Studio initialization timeout')),25000);})]);}
      finally{clearTimeout(timeout);}
      if(current!==revision)return;
      if(command.sidebar)studioSidebarOpen=true;
      await applyStudioSidebar(studio);
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
  async function applyStudioSidebar(studio){
    if(typeof studio.sidebar!=='function')return;
    await studio.sidebar(studioSidebarOpen);
    document.body.classList.toggle('workspace-studio-sidebar-open',studioSidebarOpen);
  }
  function myAgents(){return open({view:'chat',sidebar:true});}
  function studioView(view,compose){return open({view,compose});}
  function syncViewport(){
    if(!host)return;const vv=visualViewport;
    host.style.setProperty('--workspace-viewport-height',(vv?.height||innerHeight)+'px');
    host.style.setProperty('--workspace-viewport-top',(vv?.offsetTop||0)+'px');
  }
  async function ready(){
    // Cached pages can outlive the template worker. Keep the new gallery
    // compatible until their next full navigation loads versioned markup.
    const contacts=document.getElementById('contacts-area'),list=document.getElementById('contacts-list');
    if(contacts&&list&&!contacts.querySelector('.workspace-contact-controls')){
      const controls=document.createElement('div');controls.className='workspace-contact-controls';
      for(const child of [...contacts.children])if(child!==list)controls.append(child);
      contacts.prepend(controls);
    }
    const agentHeading=document.querySelector('#workspace-agent-section .workspace-section-label');
    if(agentHeading&&!document.getElementById('workspace-open-agent-sidebar')){
      const button=document.createElement('button');button.type='button';button.id='workspace-open-agent-sidebar';button.textContent=text('manage');button.onclick=()=>void myAgents();agentHeading.append(button);
    }
    installFooter();
    try{recentAgents=JSON.parse(localStorage.getItem(storageKey('recent_agents'))||'{}');}catch{recentAgents={};}
    const savedView=localStorage.getItem(storageKey('chat_view'));if(['all','agents','groups'].includes(savedView))chatView=savedView;
    await refresh(true);localize();
    const avatar=document.querySelector('#workspace-rail-account .tab-icon');
    if(avatar){avatar.innerHTML=Avatars.markup('',window.__currentUser||'',{});void Avatars.request('/v1/agents/avatars/me').then(data=>Avatars.fill(avatar,data.url,window.__currentUser)).catch(()=>{});}
    contactsView(localStorage.getItem('clawcross_contacts_view')||'list');
    // Initialize the renderer while the directory is idle; this only reads
    // local UI/auth state and never creates an Agent or sends a model prompt.
    if('requestIdleCallback'in window)requestIdleCallback(()=>createFrame(),{timeout:1200});
    else setTimeout(createFrame,300);
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
  document.addEventListener('clawcross:appearance',localize);
  document.addEventListener('clawcross:language',()=>{localize();renderAgents();});
  addEventListener('agent-settings-changed',()=>void refresh(true));
  addEventListener('message',event=>{
    if(event.origin!==location.origin||event.source!==frame?.contentWindow)return;
    if(event.data?.type==='clawcross:studio-sidebar'){
      studioSidebarOpen=!!event.data.open;
      document.body.classList.toggle('workspace-studio-sidebar-open',studioSidebarOpen&&host&&!host.hidden);
    }
    if(event.data?.type==='clawcross:studio-selection'){
      const agent=agents.find(a=>a.agent_id===event.data.agentId);if(!agent)return;
      selected=agent.agent_id;title.textContent=agent.name;document.getElementById('workspace-agent-settings').hidden=false;
      recentAgents[selected]=Date.now()/1000;localStorage.setItem(storageKey('recent_agents'),JSON.stringify(recentAgents));renderAgents();
    }
  });
  visualViewport?.addEventListener('resize',syncViewport);visualViewport?.addEventListener('scroll',syncViewport);
  window.ConversationWorkspace={ready,refresh,renderAgents,openAgentId,studioView,contactsView,hide,listView,toggleAgents,moreAgents,myAgents};
  localize();
})();
