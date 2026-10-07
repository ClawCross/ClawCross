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
    function open(options = {}) {
        document.getElementById('native-session-browser')?.remove();
        const overlay=document.createElement('div');overlay.id='native-session-browser';overlay.className='external-settings-overlay';
        overlay.innerHTML=`<section class="external-settings-dialog" role="dialog" aria-modal="true" aria-labelledby="native-session-title">
          <header><h2 id="native-session-title">${text('登记已有外部会话','Register existing external session')}</h2><button type="button" data-close aria-label="${text('关闭','Close')}">×</button></header>
          <p>${text('读取已安装 ACP 平台的会话目录。登记后尝试加载原生历史并保存到 ClawCross；不会发送新消息。历史是否可读取取决于适配器能力。','Browse installed ACP platforms. Registration loads and saves native history when the adapter supports it, without sending a new message.')}</p>
          <div data-native-acpx><p>${text('接入外部 Agent 需要 acpx。尚未安装时，可以在这里下载；外部平台自身的程序和登录仍需准备。','Connecting an external Agent requires acpx. Install it here if needed; the platform program and account must also be prepared.')}</p>${typeof global.componentControlMarkup === 'function' ? global.componentControlMarkup('acpx') : ''}</div>
          <div class="native-session-toolbar"><select aria-label="${text('平台','Platform')}">${['codex','claude','openclaw','gemini','cursor','copilot','droid','iflow','kilocode','kimi','kiro','opencode','pi','qoder','qwen','trae','aider'].map(p=>`<option value="${p}">${p === 'claude' ? 'Claude Code' : p}</option>`).join('')}</select><button type="button" data-load>${text('读取会话列表','Load session list')}</button></div>
          <div data-sessions></div><footer><span role="status" data-status></span><button type="button" data-more hidden>${text('更多会话','More sessions')}</button></footer></section>`;
        const close=()=>overlay.remove();overlay.querySelector('[data-close]').onclick=close;
        overlay.onclick=event=>{if(event.target===overlay)close();};
        overlay.addEventListener('keydown',event=>{if(event.key==='Escape')close();});
        let cursor='';let generation=0;
        const select=overlay.querySelector('select');
        // Discover custom ACP platforms without querying any native session.
        void fetch('/proxy_acpx_status').then(response=>response.json()).then(data=>{
            if (!overlay.isConnected) return;
            for (const platform of data.tools || []) {
                if (![...select.options].some(option=>option.value===platform)) {
                    const option=document.createElement('option'); option.value=platform; option.textContent=platform; select.append(option);
                }
            }
        }).catch(()=>{});
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
                        button.disabled=true;button.textContent=text('登记并加载历史…','Registering and loading…');
                        try{
                            const agent=await request('POST',{ticket:row.ticket,name:card.querySelector('input').value});
                            button.textContent=text('已登记','Registered');
                            global.dispatchEvent(new CustomEvent('clawcross:agent-imported',{detail:agent}));
                            if (options.onRegister) await options.onRegister(agent);
                            const state=agent.native_history || {};
                            status.textContent=text('Agent 已登记：','Registered Agent: ')+(agent.name||agent.agent_id)+
                              (state.status==='loaded' ? text(' · 已保存历史 ',' · History saved: ')+(state.message_count || 0)+text(' 条',' messages') :
                                state.status ? ' · '+(state.detail || text('适配器未提供原生历史','Native history is unavailable')) : '');
                        }catch(error){status.textContent=error.message;button.disabled=false;button.textContent=text('登记为 Agent','Register Agent');}
                    };
                    if (row.registered_agent_id) {
                        const history=document.createElement('button'); history.type='button';history.textContent=text('加载原生历史','Load native history');
                        history.onclick=async()=>{
                            history.disabled=true;status.textContent=text('正在加载历史…','Loading history…');
                            try {
                                const response=await fetch('/v1/agents/'+encodeURIComponent(row.registered_agent_id)+'/native-history',{method:'POST'});
                                const state=await response.json();
                                if(!response.ok) throw new Error(state.detail || text('加载失败','Load failed'));
                                status.textContent=state.status==='loaded' ? text('已保存历史 ','History saved: ')+(state.message_count || 0)+text(' 条',' messages') : (state.detail || state.status);
                                global.dispatchEvent(new CustomEvent('clawcross:agent-imported',{detail:{agent_id:row.registered_agent_id}}));
                            } catch(error) {status.textContent=error.message;} finally {history.disabled=false;}
                        };
                        card.querySelector('.native-session-actions').append(history);
                    }
                    container.appendChild(card);
                }
                cursor=data.next_cursor || '';overlay.querySelector('[data-more]').hidden=!cursor;
                status.textContent=(data.sessions || []).length?text('选择需要接入的会话。','Choose a session to connect.'):text('未找到可登记的会话。','No available sessions found.');
            }catch(error){status.textContent=error.message;}
            finally{loadButton.disabled=false;}
        }
        overlay.querySelector('[data-load]').onclick=()=>void load();overlay.querySelector('[data-more]').onclick=()=>void load(true);
        document.body.appendChild(overlay);
        if (typeof global.initComponentControls === 'function') global.initComponentControls(overlay.querySelector('[data-native-acpx]'));
        select.focus();
    }
    async function historyRequest(agentId, before) {
        const response=await fetch('/v1/agents/'+encodeURIComponent(agentId)+'/history?limit=200'+(before != null ? '&before='+encodeURIComponent(before) : ''));
        const data=await response.json();
        if(!response.ok) throw new Error(data.detail || text('读取失败','Failed to load'));
        return data;
    }
    function attachHistoryPager({agentId,container,nextBefore,render}) {
        container.querySelector('[data-history-older]')?.remove();
        if(nextBefore == null) return;
        const button=document.createElement('button');button.type='button';button.dataset.historyOlder='';
        button.className='native-history-older';button.textContent=text('加载更早记录','Load earlier messages');
        button.onclick=async()=>{
            const cursor=nextBefore;button.disabled=true;button.textContent=text('正在读取…','Loading…');
            try {
                const data=await historyRequest(agentId,cursor);
                if(!container.isConnected || !button.isConnected) return;
                const oldHeight=container.scrollHeight;
                const oldTop=container.scrollTop;
                button.insertAdjacentHTML('afterend', render(data.messages || []));
                nextBefore=data.next_before;
                container.scrollTop=oldTop+container.scrollHeight-oldHeight;
                if(nextBefore == null) button.remove();
            } catch(error) {
                let alert=container.querySelector('[data-history-error]');
                if(!alert){alert=document.createElement('p');alert.dataset.historyError='';alert.setAttribute('role','alert');button.after(alert);}
                alert.textContent=error.message;
            }
            finally {button.disabled=false;if(nextBefore != null) button.textContent=text('加载更早记录','Load earlier messages');}
        };
        container.prepend(button);
    }
    global.ClawcrossNativeSessions={open,attachHistoryPager};
})(window);
