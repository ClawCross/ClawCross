/* Template selection prepares settings without creating or connecting an Agent. */
(() => {
  let catalog;const views=new Map();
  const english=()=>document.documentElement.lang.startsWith('en');
  const label=value=>value?.[english()?'en':'zh'] || value?.zh || '';
  const basicViews=new Map();
  const styles=[
    {id:'warm',zh:'自然亲切',en:'Warm and natural',persona:'用自然、亲切的语气交流，清楚地回答，主动帮助用户理清想法。'},
    {id:'brief',zh:'简洁直接',en:'Short and clear',persona:'回答简洁直接，优先给出结论与可执行的下一步，避免不必要的长篇解释。'},
    {id:'patient',zh:'耐心细致',en:'Patient and thorough',persona:'耐心倾听，用容易理解的语言解释，必要时给出清晰的步骤与例子。'},
  ];
  window.AgentCreationBasics={
    persona(id,role=''){const style=styles.find(row=>row.id===basicViews.get(id)) || styles[0];return (role || '你是用户的智能伙伴。跟随用户使用的语言，按当前工具权限帮助用户。')+'\n\n'+style.persona;},
    mount(id){
      const host=document.getElementById(id);if(!host)return;
      basicViews.set(id,'warm');host.replaceChildren();host.className='agent-creation-basics';
      const title=document.createElement('span');title.className='agent-creation-basic-label';title.textContent=english()?'Conversation style':'相处方式';host.appendChild(title);
      const choices=document.createElement('div');choices.className='agent-creation-styles';choices.setAttribute('role','group');choices.setAttribute('aria-label',title.textContent);
      styles.forEach((style,index)=>{const button=document.createElement('button');button.type='button';button.dataset.style=style.id;button.setAttribute('aria-pressed',String(index===0));button.textContent=english()?style.en:style.zh;button.onclick=()=>{basicViews.set(id,style.id);choices.querySelectorAll('button').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));};choices.appendChild(button);});
      host.appendChild(choices);
    },
  };
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
