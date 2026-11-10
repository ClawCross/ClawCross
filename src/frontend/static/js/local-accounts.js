/* Account selection uses the existing login endpoint and its authentication. */
(function () {
  const english = () => document.documentElement.lang.startsWith('en');
  const label = (zh, en) => english() ? en : zh;
  const esc = value => String(value || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function open() {
    document.getElementById('local-account-dialog')?.remove();
    let local;
    try { const res = await fetch('/proxy_local_accounts'); if (res.ok) { const data=await res.json(); if(Array.isArray(data.accounts)) local=data; } } catch (_) {}
    const dialog = document.createElement('dialog'); dialog.id = 'local-account-dialog';
    dialog.style.cssText = 'border:1px solid #ddd;border-radius:20px;padding:24px;max-width:360px;width:calc(100% - 40px);box-sizing:border-box;';
    dialog.innerHTML = `<form style="display:grid;gap:16px"><h3 style="margin:0">${label('选择账号','Choose account')}</h3>
      <label>${label('ClawCross 账号','ClawCross account')}${local ? `<select name="user" style="width:100%;padding:12px">${local.accounts.map(name=>`<option value="${esc(name)}" ${name===(local.current || local.default)?'selected':''}>${esc(name)}${name===local.default?label(' · 默认',' · Default'):''}</option>`).join('')}</select>` : '<input name="user" autocomplete="username" required style="width:100%;box-sizing:border-box;padding:12px">'}</label>
      ${local ? '' : `<label>${label('密码','Password')}<input name="password" type="password" autocomplete="current-password" required style="width:100%;box-sizing:border-box;padding:12px"></label>`}
      <small>${label('群聊、Agent 和记录属于选中的账号。','Groups, Agents and history belong to the selected account.')}</small>
      <span role="status"></span><div style="display:flex;gap:12px;justify-content:flex-end"><button type="button" data-close>${label('取消','Cancel')}</button><button type="submit">${label('继续','Continue')}</button></div></form>`;
    document.body.appendChild(dialog); dialog.querySelector('[data-close]').onclick=()=>dialog.close();
    dialog.addEventListener('close',()=>dialog.remove(),{once:true});
    dialog.querySelector('form').onsubmit=async event=>{
      event.preventDefault(); const form=event.currentTarget, button=form.querySelector('[type=submit]'); button.disabled=true;
      try {
        const values=new FormData(form), res=await fetch('/proxy_login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:values.get('user'),password:values.get('password') || ''})});
        const body=await res.json(); if(!res.ok) throw new Error(body.error || 'Login failed'); location.reload();
      } catch(error) { form.querySelector('[role=status]').textContent=error.message; button.disabled=false; }
    };
    dialog.showModal();
  }
  window.ClawCrossAccounts={open};
  document.addEventListener('DOMContentLoaded', async()=>{
    const footer=document.querySelector('.studio-activity-footer');
    if (footer) { const button=document.createElement('button'); button.type='button';button.className='studio-activity-item';button.title=label('选择账号','Choose account');button.innerHTML='<svg class="ui-icon" aria-hidden="true"><use href="/static/icons.svg#user"/></svg><span class="studio-activity-label">'+label('账号','Account')+'</span>';button.onclick=open;footer.prepend(button); }
    try {
      const res=await fetch('/proxy_local_accounts'); if(!res.ok) return;const data=await res.json();
      const input=document.getElementById('username-input'); if(input && !input.value && typeof data.default==='string') input.value=data.default;
    } catch(_) {}
  });
})();
