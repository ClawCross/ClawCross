/* Invitation-page UI for the shared personal browser Agent runtime. */
(() => {
  'use strict';
  const R=window.ClawCrossBrowser,el=id=>document.getElementById(id);
  R.configureBrowserAgent({worker:'/group-guest-worker.js'});
  function start({ticket,token,refresh}){
    let group='',title='',owner='',members=[],selected='',view='main',state={state:'off'},files=null,runtime=null;
    let unwatch=()=>{},unstatus=()=>{},chatBusy=false,ready=false,initializing=null;
    const settings=()=>R.personalAgents.get(selected||R.personalAgents.binding(group)?.agent_id||'');
    const self=()=>{const binding=R.personalAgents.binding(group);return members.find(m=>m.own&&m.is_agent&&m.agent_id===binding?.relay_id);};
    function select(id){
      if(selected===id&&runtime)return;
      runtime?.stop();unwatch();unstatus();selected=id;
      const config=settings();files=config?new R.Workspace(config.workspace):null;
      runtime=config?new R.BrowserAgent('',()=>'',next=>{state=next;render();},id):null;
      unwatch=config?R.watchPersonalMemory(id,()=>{if(view==='chat')void drawHistory();}):()=>{};
      unstatus=config?R.watchAgentStatus(id,next=>{if(!chatBusy){state=next;render();}}):()=>{};
    }
    function show(next){view=next;render();if(next==='chat')void drawHistory();}
    function fail(error){el('agent-error').textContent=error?error.message||String(error):'';el('agent-error').hidden=!error;}
    function render(){
      const config=settings(),me=self();
      el('agent-open').hidden=!group;el('agent-open').disabled=!ready;
      el('agent-open').classList.toggle('running',Boolean(me)&&['idle','replying','compacting'].includes(state.state));
      el('agent-open').classList.toggle('failing',state.state==='error');
      if(!el('agent-dialog').open)return;
      for(const [id,page] of [['agent-main','main'],['agent-form','edit'],['agent-files','files'],['agent-choose','choose'],['agent-private','chat']])el(id).hidden=view!==page;
      if(view==='choose'){
        const rows=R.personalAgents.list().filter(item=>item.group_scope===group);
        el('agent-existing').replaceChildren(...rows.map(item=>{
          const label=document.createElement('label'),radio=document.createElement('input'),name=document.createElement('span');
          radio.type='radio';radio.name='personal-agent';radio.value=item.id;radio.checked=item.id===selected;
          radio.onchange=()=>{select(item.id);render();};name.textContent=item.name+' · '+item.model;label.append(radio,name);return label;
        }));
        el('agent-join-existing').disabled=!config;el('agent-existing-empty').hidden=rows.length>0;
      }
      const names={off:'等待接收消息',idle:'运行中',replying:'正在处理…',compacting:'正在压缩上下文…',paused:'已暂停',elsewhere:'在另一个标签页运行',error:'出错：'+(state.detail||'')};
      el('agent-status').textContent=config?config.name+' · '+config.model+'：'+(config.paused?'已暂停':names[state.state]):'请选择或创建个人 Agent';
      el('agent-status').classList.toggle('error',state.state==='error');
      el('agent-pause').textContent=config?.paused?'继续':'暂停';
      const usage=state.context;el('agent-context').textContent=usage?usage.used.toLocaleString()+' / '+usage.limit.toLocaleString()+' tokens · '+(usage.estimated?'估算':'API 实测')+(state.state==='compacting'?' · 正在压缩':''):'';
    }
    function openForm(newAgent=false){
      const config=newAgent?null:settings();if(newAgent)select('');
      el('agent-elsewhere').hidden=true;
      el('agent-name').value=config?.name??owner+'的助手';el('agent-persona').value=config?.persona??'';
      el('agent-base').value=config?.base_url??'';el('agent-model').value=config?.model??'';el('agent-key').value=config?.api_key??'';
      el('agent-window').value=config?.context_window??32768;el('agent-output').value=config?.max_output_tokens??2048;
      el('agent-save').textContent=config?'保存设置':'创建并加入群聊';show('edit');
    }
    async function joinSelected(){
      const config=settings();if(!config)throw new Error('请选择个人 Agent');
      const previous=R.personalAgents.binding(group);
      if(previous&&previous.agent_id!==config.id)throw new Error('请先从当前群移除原来的 Agent');
      const beginning=await R.groupApi('state',{group,token:token(),query:{after_id:-1}});
      await R.registerAgent(token(),config.name,previous?.relay_id??config.id,group);
      R.personalAgents.bind(group,config.id,previous?.relay_id??config.id);
      if(!previous)R.agentSettings.saveCursor(group,beginning.cursor,config.id);
      window.dispatchEvent(new Event('clawcross-personal-agents-changed'));await refresh();show('main');
    }
    async function drawHistory(){
      if(!runtime)return;const id=selected,turns=await runtime.memory.history();if(id!==selected)return;
      const box=el('agent-private-history'),near=box.scrollHeight-box.scrollTop-box.clientHeight<80;
      box.replaceChildren(...turns.map(turn=>{
        const row=document.createElement('article'),source=document.createElement('small'),input=document.createElement('p');
        source.textContent=turn.source;input.textContent=turn.input.replace(/^【当前对话来源】[\s\S]*?\n\n/,'');row.append(source,input);
        if(turn.output){const output=document.createElement('p');output.className='personal-answer';output.textContent=turn.output;row.append(output);}
        if(turn.error){const error=document.createElement('p');error.textContent=turn.error;error.className='error';row.append(error);}
        if(turn.trace.some(m=>m.tool_calls?.length)){const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent='工具记录';pre.textContent=JSON.stringify(turn.trace,null,2);details.append(summary,pre);row.append(details);}
        return row;
      }));if(near)box.scrollTop=box.scrollHeight;
    }
    async function listFiles() {
      const list = await files.list();
      el('agent-file-list').replaceChildren(...list.map(file => {
        const li = document.createElement('li');
        const name = document.createElement('button');
        name.type = 'button'; name.className = 'file-name'; name.textContent = file.path;
        name.addEventListener('click', () => act(async () => editFile(file.path, await files.read(file.path), false)));
        const size = document.createElement('small');
        size.textContent = file.size < 1024 ? file.size + ' B' : Math.ceil(file.size / 1024) + ' KB';
        const download = document.createElement('button');
        download.type = 'button'; download.textContent = '下载';
        download.addEventListener('click', () => act(async () => {
          const url = URL.createObjectURL(new Blob([await files.read(file.path)], { type: 'text/plain;charset=utf-8' }));
          const link = document.createElement('a');
          link.href = url; link.download = file.path.split('/').pop() || 'file.txt'; link.click();
          URL.revokeObjectURL(url);
        }));
        const remove = document.createElement('button');
        remove.type = 'button'; remove.className = 'danger'; remove.textContent = '删除';
        remove.addEventListener('click', () => confirm('删除 ' + file.path + '？') && act(async () => { await files.remove(file.path); await listFiles(); }));
        li.append(name, size, download, remove);
        return li;
      }));
      el('agent-files-empty').hidden = list.length > 0;
    }

    function editFile(path, content, isNew) {
      el('agent-file-editor').hidden = false;
      el('agent-file-path').value = path;
      el('agent-file-path').readOnly = !isNew;
      el('agent-file-content').value = content;
      el('agent-file-content').focus();
    }

    async function act(run) {
      fail(null);
      try { await run(); } catch (error) { fail(error); }
    }


    el('agent-open').onclick=()=>{fail(null);select(R.personalAgents.binding(group)?.agent_id??R.personalAgents.list().find(item=>item.group_scope===group)?.id??'');el('agent-dialog').showModal();show(self()&&settings()?'main':'choose');};
    el('agent-close').onclick=()=>el('agent-dialog').close();
    el('agent-edit').onclick=()=>openForm();el('agent-new').onclick=()=>openForm(true);
    el('agent-join-existing').onclick=()=>act(joinSelected);
    el('agent-pause').onclick=()=>{const config=settings();if(config){R.personalAgents.save({...config,paused:!config.paused},config.id);render();}};
    el('agent-chat-open').onclick=()=>show('chat');el('agent-private-back').onclick=()=>show('main');
    el('agent-private-send').onsubmit=event=>{event.preventDefault();if(chatBusy||!runtime)return;const text=el('agent-private-input').value.trim();if(!text)return;
      chatBusy=true;el('agent-private-submit').disabled=true;el('agent-private-input').value='';
      act(async()=>{try{await runtime.chat(text);}finally{chatBusy=false;el('agent-private-submit').disabled=false;await drawHistory();}});
    };
    el('agent-reset').onclick=()=>act(async()=>{if(chatBusy||!runtime||!confirm('重置当前群 Agent 的对话记忆？文件和群归属保留。'))return;await runtime.memory.reset();state={state:'idle'};await drawHistory();render();});
    el('agent-files-open').onclick=()=>act(async()=>{el('agent-file-editor').hidden=true;show('files');await listFiles();});
    el('agent-files-back').onclick=()=>show('main');el('agent-file-new').onclick=()=>editFile('','',true);
    el('agent-file-cancel').onclick=()=>{el('agent-file-editor').hidden=true;};
    el('agent-file-editor').onsubmit=event=>{event.preventDefault();act(async()=>{await files.write(el('agent-file-path').value,el('agent-file-content').value);el('agent-file-editor').hidden=true;await listFiles();});};
    el('agent-file-upload').onclick=()=>el('agent-file-input').click();el('agent-file-input').onchange=event=>{const file=event.target.files[0];event.target.value='';if(file)act(async()=>{await files.write(file.name,await file.text());await listFiles();});};
    el('agent-form').onsubmit=event=>{event.preventDefault();const button=el('agent-save');button.disabled=true;
      act(async()=>{const old=settings(),config={name:el('agent-name').value.trim(),persona:el('agent-persona').value,source:'key',group_scope:group,base_url:el('agent-base').value.trim(),model:el('agent-model').value.trim(),api_key:el('agent-key').value.trim(),paused:old?.paused??false,context_window:Number(el('agent-window').value),max_output_tokens:Number(el('agent-output').value)};
        const profile=R.personalAgents.save(config,old?.id);select(profile.id);await joinSelected();
      }).finally(()=>{button.disabled=false;});
    };
    el('agent-remove').onclick=()=>{const me=self();if(!me||!confirm('从这个群移除 Agent？个人配置、记忆和文件会保留。'))return;
      act(async()=>{await R.relayApi('manage/remove_member',token(),{principal:me.principal},group);R.personalAgents.unbind(group);window.dispatchEvent(new Event('clawcross-personal-agents-changed'));await refresh();show('choose');});
    };
    return {update(data){
      if(!data.identity_key)return;group=data.identity_key;title=data.title;owner=data.name;members=data.members;
      R.identity.save(ticket,group,{token:token(),name:data.name,endpoint:{base:location.origin,invite:ticket},group_id:data.group_id,title});
      if(!ready&&!initializing)initializing=R.ensureSharePageAgent(group).then(profile=>{if(profile)select(profile.id);ready=true;R.startPersonalAgentHost({onlyGroup:group});render();}).catch(fail).finally(()=>{initializing=null;});
      render();
    }};
  }
  window.GuestAgent={start};
})();
