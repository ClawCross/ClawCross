/* Template selection prepares settings without creating or connecting an Agent. */
(() => {
  let catalog;const views=new Map();
  const english=()=>document.documentElement.lang.startsWith('en');
  const label=value=>value?.[english()?'en':'zh'] || value?.zh || '';
  window.AgentCreationPresets={
    selected(id){return views.get(id)?.selected || null;},
    select(id,preset){const view=views.get(id);const row=view?.rows?.find(row=>row.id===preset);if(!row || !view.choose)return false;view.choose(row);return true;},
    platform(id,external){const host=document.getElementById(id);if(!host)return;host.querySelectorAll('button').forEach(button=>{button.disabled=external && ['chat','group'].includes(button.dataset.preset);button.title=button.disabled?(english()?'Use WeBot for this isolated template':'此隔离模板需要 WeBot'):'';});},
    async mount(id,onChange,options={}){
      const host=document.getElementById(id);if(!host)return;
      const view={selected:null};views.set(id,view);host.textContent=english()?'Loading templates…':'正在加载模板…';onChange(null);
      try {
        catalog ||= fetch('/v1/agents/creation-templates').then(async response=>{const data=await response.json();if(!response.ok)throw new Error(data.detail || data.error || 'Templates unavailable');return data.data || [];}).catch(error=>{catalog=null;throw error;});
        const rows=await catalog;if(views.get(id)!==view || !host.isConnected)return;
        host.replaceChildren();host.classList.add('agent-creation-presets');host.setAttribute('role','group');
        host.setAttribute('aria-label',english()?'Agent template':'Agent 模板');
        const choose=row=>{view.selected=row;host.querySelectorAll('button').forEach(button=>{const selected=button.dataset.preset===row.id;button.classList.toggle('is-selected',selected);button.setAttribute('aria-pressed',String(selected));});onChange(row);};
        view.rows=rows;view.choose=choose;
        for(const row of rows){const button=document.createElement('button');button.type='button';button.dataset.preset=row.id;button.className='agent-creation-preset';
          const title=document.createElement('strong');title.textContent=label(row.label);const hint=document.createElement('small');hint.textContent=label(row.description);
          button.append(title,hint);button.onclick=()=>choose(row);host.append(button);}
        const initial=rows.find(row=>row.id===(options.initial || 'personal')) || rows[0];if(initial)choose(initial);
      } catch(error){if(views.get(id)===view)host.textContent=error.message;}
    }
  };
})();
