/* One profile for contacts and group members. Remote profiles stay read-only. */
window.AgentProfile=(()=>{
  const zh=()=>typeof _currentLang==='undefined'||_currentLang==='zh';
  const text=(cn,en)=>zh()?cn:en;
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon=name=>`<svg class="ui-icon" aria-hidden="true"><use href="/static/icons.svg#${name}"/></svg>`;
  const action=(name,label,symbol='chevron-right',primary=false)=>`<button type="button" data-profile-action="${name}" class="ap-action${primary?' ap-primary':''}">${icon(symbol)}<span>${esc(label)}</span>${primary?'':icon('chevron-right')}</button>`;
  function render(agent){
    const s=agent.settings||{},remote=!!agent.remote,m=agent.profile_member||{},state=agent.status?.state;
    const stateLabel=remote?(m.online?text('设备在线','Device online'):text('远端设备','Remote device')):
      ({running:text('正在工作','Working'),idle:text('空闲','Idle'),error:text('需要留意','Needs attention')})[state]||text('状态未获取','Status unavailable');
    const platform=typeof agentPlatformLabel==='function'?agentPlatformLabel(agent.platform):agent.platform||'Agent';
    const model=s.llm?.model||s.model||s.meta?.acp?.model||'';
    const tools=s.tools===null?text('全部工具','All tools'):Array.isArray(s.tools)?text(`${s.tools.length} 项工具`,`${s.tools.length} tools`):text('查看配置','View configuration');
    const name=String(agent.name||agent.agent_id||'Agent');
    const groups=agent.groups||[],teams=s.teams||[];
    return `<section class="ap-hero"><div class="ap-avatar ${remote?'is-remote':''}">${esc(Array.from(name)[0])}</div><div class="ap-identity"><h2>${esc(name)}</h2><span class="ap-platform">${esc(platform)} · ${esc(remote?text('其他设备','Other device'):text('我的 Agent','My Agent'))}</span><span class="ap-presence ${state==='running'?'is-running':''}"><i></i>${esc(stateLabel)}</span></div></section>
      ${remote?`<p class="ap-remote-note">${esc(text('由所属设备管理，仅显示群聊中共享的资料。','Managed by its own device. Only shared group information is shown.'))}</p>`:`<div class="ap-primary-actions">${action('chat',text('发消息','Message'),'message-plus',true)}${action('history',text('对话记录','History'),'history')}${action('settings',text('设置','Settings'),'settings')}</div>`}
      ${s.persona?`<section class="ap-card"><h3>${esc(text('关于这个伙伴','About this companion'))}</h3><p class="ap-bio">${esc(s.persona)}</p><details class="ap-persona"><summary>${esc(text('完整人设','Full persona'))}</summary><p>${esc(s.persona)}</p></details></section>`:''}
      <section class="ap-card"><h3>${esc(text('协作空间','Shared spaces'))}</h3>${groups.length?`<div class="ap-groups">${groups.map(g=>`<button type="button" data-profile-group="${esc(g.group_id)}">${icon('users')}<span>${esc(g.title||g.group_id)}</span>${icon('chevron-right')}</button>`).join('')}</div>`:`<p class="ap-muted">${esc(text('还没有加入群聊','No conversations yet'))}</p>`}${teams.length?`<div class="ap-teams">${teams.map(team=>`<span>${esc(team)}</span>`).join('')}</div>`:''}</section>
      ${remote?`<section class="ap-card"><h3>${esc(text('所属设备','Hosted by'))}</h3><dl class="ap-facts"><dt>${esc(text('用户','User'))}</dt><dd>${esc(m.user_id||'—')}</dd><dt>${esc(text('设备','Device'))}</dt><dd>${esc(m.node_id||'—')}</dd></dl></section>`:`<section class="ap-card"><h3>${esc(text('能力','Capabilities'))}</h3><dl class="ap-facts"><dt>${esc(text('使用模型','Model'))}</dt><dd>${esc(model||text('尚未获取','Not available yet'))}</dd><dt>${esc(text('工具','Tools'))}</dt><dd>${esc(tools)}</dd></dl><details class="ap-skills"><summary>${esc(text('Skill 列表','Skills'))}<span data-profile-skill-count></span></summary><div data-profile-skills><p class="ap-muted">${esc(text('展开后加载','Expand to load'))}</p></div></details></section>
      <section class="ap-card ap-advanced"><details><summary>${esc(text('高级设置','Advanced settings'))}</summary><div class="ap-setting-list">${action('workspaces',text('工作区','Workspaces'),'folder')}${action('approval',text('模式、沙盒与 KEEP Y','Mode, sandbox and KEEP Y'),'shield')}${action('model',text('模型与思考强度','Model and reasoning'),'settings')}${agent.platform==='webot'?action('context',text('上下文与压缩','Context and compaction'),'history'):''}</div></details></section>`}
      <section class="ap-card ap-address"><details><summary>${esc(text('Agent 标识','Agent identity'))}</summary><code>${esc(agent.agent_id)}</code><button type="button" data-profile-action="copy">${icon('copy')}<span>${esc(text('复制编号','Copy ID'))}</span></button></details></section>
      ${remote?'':`<div class="ap-danger">${action('delete',text('删除 Agent','Delete Agent'),'trash')}</div>`}`;
  }
  function mount(body,agent){
    body.dataset.profileAgent=agent.agent_id;
    body.onclick=event=>{
      const group=event.target.closest('[data-profile-group]');
      if(group){closeAgentDetail();openChat(group.dataset.profileGroup,(agent.groups||[]).find(g=>g.group_id===group.dataset.profileGroup)?.title||'');return;}
      const button=event.target.closest('[data-profile-action]');if(!button)return;
      const id=agent.agent_id;
      switch(button.dataset.profileAction){
        case 'chat':closeAgentDetail();startPrivateChat(id);break;
        case 'history':showAgentHistory(id,agent.name);break;
        case 'settings':openAgentSettingsSheet(id);break;
        case 'workspaces':openWorkspaceSettings(id);break;
        case 'approval':openRuntimeSettings(id,'approval');break;
        case 'model':openAgentModelSettings(id);break;
        case 'context':openRuntimeSettings(id,'context');break;
        case 'delete':deleteAgentFromDetail(id);break;
        case 'copy':navigator.clipboard?.writeText(id).then(()=>toast(text('已复制','Copied'))).catch(()=>toast(id));break;
      }
    };
    const skills=body.querySelector('.ap-skills');let loading=false,loaded=false;
    if(skills)skills.addEventListener('toggle',async()=>{
      if(!skills.open||loading||loaded)return;loading=true;
      const target=skills.querySelector('[data-profile-skills]');target.textContent=text('正在加载…','Loading…');
      try{
        const data=await api('GET',`/v1/agents/${encodeURIComponent(agent.agent_id)}/skills`);
        if(body.dataset.profileAgent!==agent.agent_id||!skills.isConnected)return;
        const rows=data.skills||[];skills.querySelector('[data-profile-skill-count]').textContent=rows.length;
        const sources={companion:text('专属','Personal'),agent:text('专属','Personal'),team:text('团队','Team'),user:text('用户空间','User space')};
        target.innerHTML=rows.map(s=>`<div class="ap-skill"><strong>${esc(s.title||s.name||s.key)}</strong><small>${esc(sources[s.source]||s.source||'Skill')}${s.team_name?' · '+esc(s.team_name):''}</small>${s.description?`<p>${esc(s.description)}</p>`:''}</div>`).join('')||`<p class="ap-muted">${esc(text('暂未配置 Skill','No skills configured'))}</p>`;loaded=true;
      }catch(error){if(skills.isConnected)target.textContent=text('加载失败，收起后可重试。','Unable to load. Close and reopen to retry.');}
      finally{loading=false;}
    });
  }
  return{render,mount};
})();
