/* Early, shared interface preferences. Auto detection never becomes a manual choice. */
(() => {
  const supported=['zh-CN','zh-TW','en','ja-JP','ko-KR','fr-FR','de-DE','es-ES','pt-BR','ru-RU','it-IT','ar','hi-IN','id-ID'];
  const get=key=>{try{return localStorage.getItem(key);}catch{return null;}};
  const save=(key,value)=>{try{localStorage.setItem(key,value);}catch{}};
  function match(raw){
    const code=String(raw||'').replace(/_/g,'-').toLowerCase();
    if(code==='zh'||code.startsWith('zh-'))return /(?:hant|tw|hk|mo)(?:-|$)/.test(code)?'zh-TW':'zh-CN';
    return supported.find(item=>item.toLowerCase()===code)||supported.find(item=>item.split('-')[0]===code.split('-')[0]);
  }
  function detectedLanguage(){for(const code of navigator.languages||[navigator.language]){const found=match(code);if(found)return found;}return 'en';}
  function automaticLanguage(){return get('clawcross_language_mode')==='auto'||!['clawcross_ui_locale','clawcross_lang','lang'].some(get);}
  function language(){return automaticLanguage()?detectedLanguage():match(get('clawcross_ui_locale')||get('clawcross_lang')||get('lang'))||detectedLanguage();}
  const dark=matchMedia('(prefers-color-scheme: dark)');
  const modes=['system','light','dark'];
  const appearance=()=>modes.includes(get('clawcross_appearance'))?get('clawcross_appearance'):'system';
  const text=(zh,en)=>window.ClawCrossLocale?.text(zh,en)||(language().startsWith('zh')?zh:en);
  function appearanceLabel(mode=appearance()){return text('外观','Appearance')+' · '+({system:text('跟随系统','System'),light:text('浅色','Light'),dark:text('深色','Dark')})[mode];}
  function applyAppearance(){
    const effective=appearance()==='system'?(dark.matches?'dark':'light'):appearance();
    document.documentElement.dataset.theme=effective;document.documentElement.style.colorScheme=effective;
    document.dispatchEvent(new Event('clawcross:appearance'));
  }
  function setAppearance(mode){if(!modes.includes(mode))return;save('clawcross_appearance',mode);applyAppearance();document.getElementById('clawcross-appearance-dialog')?.close();}
  function openAppearance(){
    document.getElementById('clawcross-appearance-dialog')?.remove();
    const dialog=document.createElement('dialog');dialog.id='clawcross-appearance-dialog';dialog.className='language-picker-dialog appearance-picker-dialog';
    const header=document.createElement('header'),title=document.createElement('h2'),close=document.createElement('button');
    title.textContent=text('外观','Appearance');close.type='button';close.textContent='×';close.setAttribute('aria-label',text('关闭','Close'));close.onclick=()=>dialog.close();header.append(title,close);
    const list=document.createElement('div');list.className='language-picker-list';
    for(const mode of modes){const button=document.createElement('button');button.type='button';button.textContent=appearanceLabel(mode);button.dataset.appearance=mode;button.setAttribute('aria-pressed',String(appearance()===mode));button.onclick=()=>setAppearance(mode);list.append(button);}
    dialog.append(header,list);document.body.append(dialog);dialog.showModal();dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.onclick=e=>{if(e.target===dialog)dialog.close();};
  }
  function useAutomaticLanguage(){save('clawcross_language_mode','auto');window.ClawCrossLocale?.set(detectedLanguage(),{automatic:true});}
  function syncLanguage(){if(window.ClawCrossLocale&&ClawCrossLocale.current()!==language())ClawCrossLocale.set(language(),{persist:false});}
  window.InterfacePreferences={language,detectedLanguage,automaticLanguage,useAutomaticLanguage,appearance,appearanceLabel,setAppearance,openAppearance,applyAppearance};
  document.documentElement.lang=language();document.documentElement.dir=language()==='ar'?'rtl':'ltr';applyAppearance();
  dark.addEventListener('change',()=>{if(appearance()==='system')applyAppearance();});
  addEventListener('languagechange',()=>{if(automaticLanguage())syncLanguage();});
  addEventListener('storage',event=>{if(event.key==='clawcross_appearance')applyAppearance();else if(['clawcross_ui_locale','clawcross_language_mode','clawcross_lang','lang'].includes(event.key))syncLanguage();});
})();
