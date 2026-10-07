/* null permits all tools; an explicit [] permits no tools. */
(() => {
  const states=new Map();let catalog;
  const text=(zh,en)=>document.documentElement.lang.startsWith('en')?en:zh;
  const load=()=>catalog ||= fetch('/proxy_tools').then(async response=>{
    const data=await response.json();if(!response.ok)throw new Error(data.error || text('工具列表暂时不可用','Tools are unavailable'));return data.tools || [];
  }).catch(error=>{catalog=null;throw error;});
  function sync(prefix,state){
    state.host.querySelector('[data-tool-all]').checked=state.all;
    state.host.querySelector('[data-tool-select]').hidden=state.all;
    document.getElementById(prefix+'-tools').value=state.all?'':[...state.enabled].join(', ');
  }
  function paint(prefix,state){
    renderGroupedToolPicker(state.host.querySelector('[data-tool-list]'),state.tools,state.enabled,(name,button)=>{
      state.custom=true;
      if(state.enabled.has(name))state.enabled.delete(name);else state.enabled.add(name);
      button.classList.toggle('enabled',state.enabled.has(name));button.classList.toggle('disabled',!state.enabled.has(name));sync(prefix,state);
    });
  }
  window.MobileAgentCreationTools={
    reset(){states.clear();},
    value(prefix){const state=states.get(prefix);return state?state.all?null:[...state.enabled]:undefined;},
    set(prefix,names){const state=states.get(prefix);if(!state)return;state.all=names==null;state.custom=names!=null;state.enabled=new Set(names || state.tools.map(tool=>tool.name));paint(prefix,state);sync(prefix,state);},
    async mount(prefix){
      const host=document.getElementById(prefix+'-tools-picker');if(!host)return;
      const state={host,all:true,custom:false,enabled:new Set(),tools:[]};states.set(prefix,state);
      host.innerHTML=`<label class="create-agent-tool-all"><input type="checkbox" data-tool-all checked><span>${text('使用全部工具','Use all tools')}</span></label><p class="side-panel-hint">${text('可以手动选择范围，以后也能在 Agent 设置中调整。','Choose a smaller set if needed. You can change it later.')}</p><div class="create-agent-tool-select" data-tool-select hidden><div class="create-agent-tool-actions"><button type="button" data-select-all>${text('全选','Select all')}</button><button type="button" data-select-none>${text('全部取消','Clear all')}</button></div><div data-tool-list role="group">${text('正在加载工具…','Loading tools…')}</div></div>`;
      host.querySelector('[data-tool-all]').onchange=event=>{state.all=event.target.checked;sync(prefix,state);};
      host.querySelector('[data-select-all]').disabled=true;
      host.querySelector('[data-select-all]').onclick=()=>{state.enabled=new Set(state.tools.map(tool=>tool.name));paint(prefix,state);sync(prefix,state);};
      host.querySelector('[data-select-none]').onclick=()=>{state.custom=true;state.enabled.clear();paint(prefix,state);sync(prefix,state);};
      try{const tools=await load();if(states.get(prefix)!==state || !host.isConnected)return;
        state.tools=tools;if(!state.custom)state.enabled=new Set(tools.map(tool=>tool.name));host.querySelector('[data-select-all]').disabled=false;paint(prefix,state);sync(prefix,state);
      }catch(error){if(states.get(prefix)===state)state.host.querySelector('[data-tool-list]').textContent=error.message;}
    }
  };
})();
