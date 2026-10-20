/* Public, group-scoped human profiles. Private Agent user profiles are never read here. */
window.HumanProfile = (() => {
  'use strict';
  const text = (zh, en) => document.documentElement.lang.startsWith('zh') ? zh : en;
  function node(tag, cls, value) {
    const result=document.createElement(tag); if(cls)result.className=cls; if(value)result.textContent=value; return result;
  }
  function action(label, callback, cls='gn-btn gn-btn-secondary') {
    const button=node('button',cls,label);button.type='button';button.addEventListener('click',callback);return button;
  }
  function open({member, members=[], title='', canEdit=false, save, loadImage, mention, agent, remove}) {
    const root=node('dialog','group-network-dialog human-profile');
    root.setAttribute('aria-label',text('个人资料','Personal profile'));
    const head=node('header','gn-head'), heading=node('h2','gn-title',text('个人资料','Personal profile'));
    const close=action('×',()=>root.close(),'gn-close');close.setAttribute('aria-label',text('关闭','Close'));
    head.append(heading,close);root.append(head);
    let current=member;
    const body=node('div','gn-body'), status=node('p','gn-status');status.setAttribute('role','status');root.append(body,status);
    function draw() {
      body.replaceChildren();status.textContent='';
      const profile=current.profile||{},hero=node('section','hp-hero');
      const avatar=node('div','hp-avatar',Array.from(current.name||'?').slice(0,1).join(''));
      const identity=node('div','hp-identity');identity.append(node('h3','',current.name||current.principal));
      const role=current.is_owner?text('群主','Group owner'):text('群成员','Member');
      const state=current.online===undefined?'':current.online?text('在线','Online'):text('离线','Offline');
      identity.append(node('p','hp-meta',[role,state].filter(Boolean).join(' · ')));hero.append(avatar,identity);body.append(hero);
      const portrait=node('section','hp-portrait');portrait.append(node('h4','',text('用户画像','User portrait')));UserPortrait.render(portrait,profile,{loadImage});body.append(portrait);
      const facts=node('dl','hp-facts');
      function fact(label,value){if(value){facts.append(node('dt','',label),node('dd','',value));}}
      fact(text('所在群聊','Group'),title);
      fact(text('账号标识','Account ID'),current.user_id);
      fact(text('连接方式','Connection'),current.device_kind==='browser'?text('浏览器','Browser'):current.device_kind==='client'?text('ClawCross 客户端','ClawCross client'):'');
      if(current.joined_at)fact(text('加入时间','Joined'),new Date(current.joined_at*1000).toLocaleDateString(document.documentElement.lang));
      if(facts.children.length)body.append(facts);
      const own=members.filter(m=>m.is_agent&&((current.connection_id&&m.connection_id===current.connection_id)||(!current.connection_id&&current.device!==undefined&&m.device===current.device)));
      const section=node('section','hp-agents');section.append(node('h4','',text('TA 的 Agent','Their Agents')+' · '+own.length));
      if(!own.length)section.append(node('p','gn-note',text('还没有带入 Agent','No Agents brought into this group')));
      for(const m of own){
        const row=agent?action(m.name||m.principal,()=>{root.close();agent(m);},'hp-agent'):node('div','hp-agent',m.name||m.principal);
        row.append(node('span','',m.agent?.status?.state==='running'?text('运行中','Running'):m.platform||'Agent'));section.append(row);
      }
      body.append(section);
      const actions=node('div','gn-actions');
      if(mention)actions.append(action('@ '+(current.name||''),()=>{root.close();mention(current);}));
      if(canEdit&&save)actions.append(action(text('编辑我的资料','Edit my profile'),edit,'gn-btn gn-btn-primary'));
      if(remove)actions.append(action(text('移出群聊','Remove from group'),()=>{root.close();remove(current);},'gn-btn gn-btn-quiet'));
      body.append(actions);
      const details=node('details','hp-ids');details.append(node('summary','gn-note',text('成员与设备标识','Member and device IDs')));
      for(const value of [current.principal,current.node_id].filter(Boolean))details.append(node('code','',value));
      body.append(details);
    }
    function edit() {
      body.replaceChildren();status.textContent='';
      const form=node('form','hp-form'),inputs={};
      const fields=[['name',text('群昵称','Group nickname'),40]];
      for(const [key,label,max] of fields){
        const row=node('label','hp-field');row.append(node('span','',label));
        const input=node('input','gn-input');input.name=key;input.maxLength=max;input.value=current.name||'';input.required=true;
        row.append(input);form.append(row);inputs[key]=input;
      }
      const editor=UserPortrait.edit(form,current.profile||{},{loadImage});
      form.append(node('p','gn-note',text('这些资料只用于当前群聊，群成员可见。','Visible to members of this group only.')));
      const actions=node('div','gn-actions'),submit=node('button','gn-btn gn-btn-primary',text('保存','Save'));submit.type='submit';
      actions.append(action(text('取消','Cancel'),draw),submit);form.append(actions);body.append(form);
      form.addEventListener('submit',async event=>{
        event.preventDefault();submit.disabled=true;status.textContent=text('正在保存…','Saving…');status.classList.remove('is-error');
        try{
          const data=await save({...Object.fromEntries(Object.entries(inputs).map(([key,input])=>[key,input.value.trim()])),...editor.value()});
          if(!root.isConnected)return;
          current=data.members?.find(m=>m.principal===current.principal)||current;
          members=data.members||members;draw();status.textContent=text('资料已更新','Profile updated');
        }catch(error){if(root.isConnected){status.textContent=error.message;status.classList.add('is-error');}}
        finally{submit.disabled=false;}
      });
      inputs.name.focus();
    }
    root.addEventListener('click',event=>{if(event.target===root)root.close();});
    root.addEventListener('close',()=>root.remove(),{once:true});
    draw();document.body.append(root);root.showModal();return root;
  }
  return {open};
})();
