/* Saved devices and remote Agents share the ordinary Agent API. */
window.DeviceConnections = (() => {
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function request(path,method='GET',body) {
    const response=await fetch('/v1/connections'+path,{method,headers:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});
    const data=await response.json();if(!response.ok)throw Error(data.detail||data.error||'连接请求失败');return data;
  }
  async function list(){return (await request('')).data||[];}
  async function open() {
    const dialog=document.createElement('dialog');dialog.className='device-connections-dialog';
    dialog.innerHTML='<header><h2>设备与 SSH 连接</h2><button type="button" data-close aria-label="关闭">×</button></header><p>本机和远端 Agent 共用对话、通讯录与管理功能。使用 SSH 密钥或 SSH Agent 登录。</p><div data-list></div><button type="button" data-add>添加设备</button><div data-editor></div><p data-status role="status"></p>';
    document.body.append(dialog);dialog.showModal();
    let finish;const closed=new Promise(resolve=>{finish=()=>{dialog.close();dialog.remove();resolve();};});
    dialog.querySelector('[data-close]').onclick=finish;
    dialog.addEventListener('cancel',event=>{event.preventDefault();finish();});
    const status=dialog.querySelector('[data-status]'),host=dialog.querySelector('[data-list]');
    const run=async task=>{status.textContent='处理中…';try{await task();}catch(error){status.textContent=error.message;}};
    async function refresh(){
      const rows=await list();host.replaceChildren();
      if(!rows.length)host.textContent='还没有远端设备，Agent 默认运行在本机。';
      for(const row of rows){
        const item=document.createElement('article');item.className='device-connection-row';
        item.innerHTML=`<strong>${escape(row.name)}</strong><small>${escape(row.username?row.username+'@':'')}${escape(row.host)} · ${row.state==='connected'?'已连接':row.state==='paused'?'已暂停':'未连接'} · ${row.agent_count||0} 个 Agent</small><div class="device-connection-actions"></div>`;
        for(const [label,action] of [
          ['连接并检测',async()=>{const result=await request('/'+row.id+'/connect','POST');await refresh();status.textContent=result.remote.acpx_available?'连接成功，远端 acpx 已就绪。':'SSH 已连接，远端未找到 acpx。请在设备上安装或填写已有可执行文件路径。';}],
          ['新增远端 Agent',()=>agentForm(row)],['编辑',()=>editor(row)],
          ['断开',async()=>{await request('/'+row.id+'/disconnect','POST');await refresh();status.textContent='已断开；Agent 和历史保留。';}],
          ['删除连接',async()=>{if(!confirm('删除此设备连接？已有 Agent 的连接不能直接删除。'))return;await request('/'+row.id,'DELETE');await refresh();status.textContent='已删除连接。';}]
        ]){const button=document.createElement('button');button.type='button';button.textContent=label;button.onclick=()=>run(action);item.querySelector('div').append(button);}
        host.append(item);
      }
    }
    function editor(row={}){
      const form=document.createElement('form');form.className='device-connection-form';
      form.innerHTML=[['name','设备名称',row.name||''],['host','IP、域名或 SSH 配置别名',row.host||''],['username','SSH 用户名（使用配置别名时可留空）',row.username||''],['port','SSH 端口',row.port||22]].map(([name,label,value])=>`<label>${label}<input name="${name}" value="${escape(value)}" ${name==='host'?'required':''} ${name==='port'?'type="number" min="1" max="65535"':''}></label>`).join('')+
        `<details><summary>密钥与远端环境</summary><label>本机私钥文件路径<input name="identity_file" placeholder="${row.has_identity_file?'已配置；留空保留':'留空使用 SSH Agent 或 SSH 配置'}"></label><label>已知主机文件路径<input name="known_hosts_file" placeholder="${row.has_known_hosts_file?'已配置；留空保留':'留空使用 OpenSSH 的 known_hosts'}"></label><label>主机密钥<select name="host_key_policy"><option value="yes">仅连接已信任的主机</option><option value="accept-new">允许首次登记，拒绝密钥变化</option></select></label>${['python','acpx','node'].map(name=>`<label>远端 ${name}<input name="${name}" value="${escape(row[name]||(name==='python'?'python3':name))}"></label>`).join('')}</details><div><button type="submit">保存连接</button><button type="button" data-cancel>取消</button></div>`;
      form.elements.host_key_policy.value=row.host_key_policy||'yes';
      form.onsubmit=event=>{event.preventDefault();run(async()=>{
        const body=Object.fromEntries(new FormData(form));body.port=Number(body.port);
        for(const key of ['identity_file','known_hosts_file'])if(row.id&&!body[key])delete body[key];
        await request(row.id?'/'+row.id:'',row.id?'PATCH':'POST',body);form.remove();await refresh();status.textContent='已保存。点击连接并检测。';
      });};
      form.querySelector('[data-cancel]').onclick=()=>form.remove();dialog.querySelector('[data-editor]').replaceChildren(form);form.elements.host.focus();
    }
    function agentForm(row){
      const form=document.createElement('form');form.className='device-connection-form';
      form.innerHTML=`<h3>在 ${escape(row.name)} 上添加 Agent</h3><label>Agent 名称<input name="name" required maxlength="160"></label><label>平台<input name="platform" value="codex" required placeholder="codex、claude、gemini 等 ACP 平台"></label><label>远端工作目录<input name="remote_cwd" placeholder="留空创建这个 Agent 的独立工作目录"></label><small>不自动下载远端软件。原生 CLI 工具在远端执行；ClawCross 工具由本机统一审核。</small><div><button type="submit">创建 Agent</button><button type="button" data-cancel>取消</button></div>`;
      form.onsubmit=event=>{event.preventDefault();run(async()=>{
        const response=await fetch('/v1/agents',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...Object.fromEntries(new FormData(form)),connection_id:row.id,creation_template:'personal'})});
        const agent=await response.json();if(!response.ok)throw Error(agent.detail||'创建失败');
        form.remove();await refresh();status.textContent='Agent 已添加到统一列表。';
        window.dispatchEvent(new CustomEvent('clawcross:agent-imported',{detail:agent}));
        if(typeof refreshAgentCenter==='function')void refreshAgentCenter();
        if(typeof loadContactsForTeam==='function')void loadContactsForTeam();
        if(typeof refreshStudioConversations==='function')void refreshStudioConversations();
      });};
      form.querySelector('[data-cancel]').onclick=()=>form.remove();dialog.querySelector('[data-editor]').replaceChildren(form);form.elements.name.focus();
    }
    dialog.querySelector('[data-add]').onclick=()=>editor();
    await run(refresh);if(status.textContent==='处理中…')status.textContent='';
    return closed;
  }
  return {open,list};
})();
