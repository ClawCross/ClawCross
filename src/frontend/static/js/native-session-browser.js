(function(global) {
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const zh = () => !String(document.documentElement.lang || 'zh').startsWith('en');
    const text = (cn,en) => zh() ? cn : en;
    async function request(method, body, query='') {
        const response = await fetch('/v1/agents/native-sessions'+query, {method,headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
        const data=await response.json();
        if(!response.ok) throw new Error(data.detail || data.error || text('读取失败','Failed to load'));
        return data;
    }
    function open() {
        document.getElementById('native-session-browser')?.remove();
        const overlay=document.createElement('div');overlay.id='native-session-browser';overlay.className='external-settings-overlay';
        overlay.innerHTML=`<section class="external-settings-dialog" role="dialog" aria-modal="true" aria-labelledby="native-session-title">
          <header><h2 id="native-session-title">${text('登记已有外部会话','Register existing external session')}</h2><button type="button" data-close aria-label="${text('关闭','Close')}">×</button></header>
          <p>${text('读取主机 Codex / Claude 的会话目录。点击登记才创建 Agent，不发送消息、不复制原生历史；下一次对话恢复该会话。','Browse the host Codex / Claude session directory. Registration creates an Agent without sending messages or copying native history; the next message resumes it.')}</p>
          <div class="native-session-toolbar"><select aria-label="${text('平台','Platform')}"><option value="codex">Codex</option><option value="claude">Claude Code</option></select><button type="button" data-load>${text('读取会话列表','Load session list')}</button></div>
          <div data-sessions></div><footer><span role="status" data-status></span><button type="button" data-more hidden>${text('更多会话','More sessions')}</button></footer></section>`;
        const close=()=>overlay.remove();overlay.querySelector('[data-close]').onclick=close;
        overlay.onclick=event=>{if(event.target===overlay)close();};
        overlay.addEventListener('keydown',event=>{if(event.key==='Escape')close();});
        let cursor='';let generation=0;
        const select=overlay.querySelector('select');
        select.onchange=()=>{generation++;cursor='';overlay.querySelector('[data-sessions]').replaceChildren();overlay.querySelector('[data-more]').hidden=true;};
        async function load(more=false) {
            const own=++generation;const platform=select.value;
            const status=overlay.querySelector('[data-status]');status.textContent=text('正在读取…','Loading…');
            const loadButton=overlay.querySelector('[data-load]');loadButton.disabled=true;
            try {
                const data=await request('GET',null,'?platform='+encodeURIComponent(platform)+(more&&cursor?'&cursor='+encodeURIComponent(cursor):''));
                if(own!==generation || !overlay.isConnected) return;
                const container=overlay.querySelector('[data-sessions]');if(!more)container.replaceChildren();
                for(const row of data.sessions || []) {
                    const card=document.createElement('article');card.className='native-session-row';
                    card.innerHTML=`<strong>${esc(row.title || row.session_id)}</strong><small>${esc(row.cwd)}</small><small>${esc(row.updated_at || '')} · ${esc(row.session_id)}</small>
                        <div class="native-session-actions"><input aria-label="${text('Agent 名称','Agent name')}" maxlength="160" placeholder="${text('名称（可选）','Name (optional')}" value="${esc(row.title || '')}"><button type="button">${row.registered_agent_id?text('已登记','Registered'):text('登记为 Agent','Register Agent')}</button></div>`;
                    const button=card.querySelector('button');button.disabled=Boolean(row.registered_agent_id);
                    button.onclick=async()=>{
                        button.disabled=true;
                        try{
                            const agent=await request('POST',{ticket:row.ticket,name:card.querySelector('input').value});
                            button.textContent=text('已登记','Registered');
                            status.textContent=text('Agent 已登记：','Registered Agent: ')+(agent.name||agent.agent_id);
                            global.dispatchEvent(new CustomEvent('clawcross:agent-imported',{detail:agent}));
                        }catch(error){status.textContent=error.message;button.disabled=false;}
                    };
                    container.appendChild(card);
                }
                cursor=data.next_cursor || '';overlay.querySelector('[data-more]').hidden=!cursor;
                status.textContent=(data.sessions || []).length?text('选择需要接入的会话。','Choose a session to connect.'):text('未找到可登记的会话。','No available sessions found.');
            }catch(error){status.textContent=error.message;}
            finally{loadButton.disabled=false;}
        }
        overlay.querySelector('[data-load]').onclick=()=>void load();overlay.querySelector('[data-more]').onclick=()=>void load(true);
        document.body.appendChild(overlay);select.focus();
    }
    global.ClawcrossNativeSessions={open};
})(window);
