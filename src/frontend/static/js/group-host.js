/* Host management uses machine-authenticated local proxies, never group credentials. */
(()=>{
  const $=id=>document.getElementById(id);let state={groups:[],network:{enabled:true}},busy=false;
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function api(path='',body){const r=await fetch('/proxy_group_host'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||d.detail||'操作失败');return d;}
  function paint(){
    const enabled=state.network.enabled;$('network').classList.toggle('paused',!enabled);$('network-title').textContent=enabled?'群聊联网已开启':'群聊联网已关闭';
    $('network-desc').textContent=enabled?'外部设备可以连接。关闭后，本机聊天继续，成员、邀请和历史保留。':'外部连接已暂停。本机聊天正常，恢复后原设备可继续连接。';
    $('network-toggle').textContent=enabled?'一键关闭群聊联网':'恢复群聊联网';$('network-toggle').disabled=busy;
    const query=$('search').value.trim().toLowerCase(),groups=state.groups.filter(g=>[g.title,g.group_id,...g.members.map(m=>m.name)].join(' ').toLowerCase().includes(query));
    $('count').textContent=groups.length+' 个群聊';
    $('groups').innerHTML=groups.map(g=>`<article class="group" data-group="${esc(g.group_id)}"><div class="summary"><div><h2>${esc(g.title)}</h2><div class="meta">${g.members.length} 位成员 · ${esc(g.group_id)}</div><span class="pill ${g.external_access_enabled?'':'off'}">${g.external_access_enabled?'允许外部连接':'仅本机连接'}</span></div><button data-toggle="${esc(g.group_id)}" ${!state.network.enabled?'disabled':''}>${g.group_external_access_enabled===false?'恢复联网':'暂停联网'}</button></div><details><summary>群设置与成员</summary><form data-edit="${esc(g.group_id)}"><label>群聊名称<input name="title" maxlength="120" value="${esc(g.title)}" required></label><label>群公告<textarea name="announcement" maxlength="8000">${esc(g.announcement||'')}</textarea></label><button type="submit">保存设置</button></form><div class="members">${g.members.map(m=>`<div class="member"><div>${esc(m.name)} ${m.is_agent?'· Agent':''}${m.connection_id===g.owner?' · 群主':''}<small>${esc(m.user_id)} · ${m.host_local?'本机':'其他设备'}${m.online?' · 在线':''}</small></div>${m.connection_id!==g.owner||m.is_agent?`<button data-remove="${esc(m.principal)}" data-gid="${esc(g.group_id)}" data-name="${esc(m.name)}">移除</button>`:''}</div>`).join('')}</div></details></article>`).join('')||'<div class="empty">没有匹配的群聊</div>';
  }
  async function load(){try{state=await api();paint();$('status').textContent='';}catch(e){$('status').textContent=e.message;}}
  async function action(path,body){if(busy)return;busy=true;$('network-toggle').disabled=true;try{await api(path,body);await load();$('status').textContent='已更新';}catch(e){$('status').textContent=e.message;}finally{busy=false;$('network-toggle').disabled=false;}}
  $('network-toggle').onclick=()=>{const enabled=!state.network.enabled;if(!enabled&&!confirm('关闭本机所有群聊的外部联网？本机聊天继续，成员与历史保留。'))return;action('/network',{enabled});};
  $('search').oninput=paint;$('refresh').onclick=load;
  $('groups').onclick=e=>{const toggle=e.target.closest('[data-toggle]'),remove=e.target.closest('[data-remove]');if(toggle){const g=state.groups.find(g=>g.group_id===toggle.dataset.toggle);action('/groups/'+encodeURIComponent(g.group_id)+'/external_access',{enabled:g.group_external_access_enabled===false});}if(remove&&confirm('移除 '+remove.dataset.name+'？移除人类成员时，其设备引入的 Agent 也会离开。'))action('/groups/'+encodeURIComponent(remove.dataset.gid)+'/remove_member',{principal:remove.dataset.remove});};
  $('groups').onsubmit=e=>{const form=e.target.closest('[data-edit]');if(!form)return;e.preventDefault();action('/groups/'+encodeURIComponent(form.dataset.edit)+'/patch',{title:form.elements.title.value,announcement:form.elements.announcement.value});};
  load();
})();
