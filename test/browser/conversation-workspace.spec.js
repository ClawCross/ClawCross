const {test,expect}=require('@playwright/test');
test.use({serviceWorkers:'block'});
const agents=[{agent_id:'codex-one',platform:'codex',name:'我的伙伴',driver:'acpx',settings:{},location:{kind:'local'}},{agent_id:'webot-one',platform:'webot',name:'研究伙伴',settings:{},location:{kind:'local'}}];
async function fixtures(page,options={}){
 const errors=[],calls=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(options=>{if(options.manualLocale!==false&&!localStorage.getItem('clawcross_ui_locale'))localStorage.setItem('clawcross_lang','zh');window.alert=()=>{};},options);
 await page.route('**/*',async route=>{
  const url=new URL(route.request().url()),p=url.pathname;
  if(url.hostname==='cdnjs.cloudflare.com')return route.fulfill({contentType:'application/javascript',body:'window.marked={parse:s=>s,setOptions(){}};window.hljs={highlightElement(){},highlightAll(){}};'});
  if(p==='/proxy_check_session')return route.fulfill({json:{valid:true,user_id:'workspace-test',has_password:true}});
  if(p==='/proxy_groups')return route.fulfill({json:{groups:options.groups||[{group_id:'g1',title:'未来之室',kind:'group',member_count:3,member_names:['我','伙伴','朋友'],members:[]}]}});
  if(p==='/proxy_groups/g1')return route.fulfill({json:{group_id:'g1',title:'未来之室',members:[],messages:[],has_more:false}});
  if(p==='/proxy_groups/g1/messages')return route.fulfill({json:{messages:[],has_more:false}});
  if(p.includes('/v1/agents')&&!p.includes('/activity'))return route.fulfill({json:{data:options.agents||agents}});
  if(p==='/teams'||p==='/v1/teams'||p==='/proxy_agent_api/v1/teams')return route.fulfill({json:{teams:[],data:[]}});
  if(p==='/proxy_visual/experts')return route.fulfill({json:options.experts||[]});
  if(p.includes('llm_config_status')||p.includes('setup_status'))return route.fulfill({json:{configured:true,llm_configured:true}});
  if(p.startsWith('/proxy')||p.startsWith('/api/'))return route.fulfill({json:{data:[],status:'success'}});
  if(p==='/studio'&&url.searchParams.get('embedded')==='1'){
   return route.fulfill({contentType:'text/html',body:`<!doctype html><html><body><main>Studio tool history</main><script>window.WorkspaceStudio={open:async command=>{document.querySelector('main').textContent=command.view==='agent'?'工具记录 · '+command.agent.name:command.view;parent.postMessage({type:'fixture-open',command},location.origin);}};<\/script></body></html>`});
  }
  return route.continue();
 });
 page.on('console',m=>{if(m.type()==='error')calls.push(m.text());});return {errors,calls};
}
test('one directory opens Agent tool history and existing groups without page switching',async({page})=>{
 await page.setViewportSize({width:1400,height:900});const {errors}=await fixtures(page);
 await page.goto('/mobile/group_chat');
 await expect(page.locator('#workspace-agent-list button')).toHaveCount(2);
 await expect(page.locator('.mobile-studio-link,.mobile-studio-header-link')).toHaveCount(0);
 await page.locator('[data-agent-id="codex-one"]').click();
 await expect(page.locator('#workspace-conversation')).toBeVisible();
 await expect(page.frameLocator('#workspace-studio-frame').locator('main')).toContainText('工具记录 · 我的伙伴');
 await expect(page.locator('#workspace-conversation .workspace-frame-status')).toBeHidden();
 await page.locator('#group-list-container .group-item').click();
 await expect(page.locator('#workspace-conversation')).toBeHidden();
 await expect(page.locator('#chat-title')).toContainText('未来之室');
 await page.locator('.tab-item[data-tab="contacts"]').click();
 await page.locator('[data-contact-view="cards"]').click();
 await expect(page.locator('#tab-contacts')).toHaveClass(/workspace-cards/);
 await expect(page.locator('[data-contact-view="cards"]')).toHaveAttribute('aria-pressed','true');
 await page.locator('.tab-item[data-tab="discover"]').click();
 await expect(page.locator('#tab-discover .create-shortcuts')).toContainText('工作流');
 await page.locator('#tab-discover button.create-shortcut').filter({hasText:'工作流'}).click();
 await expect(page.frameLocator('#workspace-studio-frame').locator('main')).toHaveText('orchestrate');
 expect((await page.locator('#workspace-conversation').boundingBox()).x).toBe(68);
 await expect(page.locator('.workspace-back')).toBeVisible();
 await page.locator('.workspace-back').click();await expect(page.locator('#workspace-conversation')).toBeHidden();
 expect(errors).toEqual([]);
});
test('phone Agent conversation uses the full screen and has a working back button',async({page})=>{
 await page.setViewportSize({width:390,height:844});const {errors}=await fixtures(page);
 await page.goto('/mobile/group_chat');await page.locator('[data-agent-id="codex-one"]').click();
 const bounds=await page.locator('#workspace-conversation').boundingBox();expect(bounds.x).toBe(0);expect(bounds.width).toBe(390);
 await expect(page.locator('.tab-bar')).toBeHidden();
 await page.locator('.workspace-back').click();await expect(page.locator('#workspace-conversation')).toBeHidden();
 await expect(page.locator('.tab-bar')).toBeVisible();expect(errors).toEqual([]);
});

test('the real embedded Studio renderer shows saved tool steps without sending a prompt',async({page})=>{
 const {errors}=await fixtures(page);
 await page.unroute('**/*');
 // Keep the normal fixture APIs, but serve the real Studio page and its scripts.
 await fixtures(page);
 await page.route('**/studio?embedded=1',route=>route.continue());
 const prompts=[];
 let catalogRequests=0;
 await page.route('**/v1/teams',route=>{catalogRequests++;return route.fulfill({json:{data:[]}});});
 await page.route('**/v1/agents/codex-one/history**',route=>route.fulfill({json:{messages:[
  {role:'user',content:'查看项目',ts:1},{role:'tool',tool_name:'read_file',content:'file preview',status:'completed',ts:2},
  {role:'assistant',content:'项目已经查看',ts:3}],has_more:false}}));
 await page.route('**/proxy_acpx_status',route=>route.fulfill({json:{available:true,transport:'direct',tools:['codex']}}));
 await page.route('**/v1/agents/codex-one/events**',route=>route.fulfill({json:{turn:null,items:[],cursor:0}}));
 await page.route('**/v1/agents/*/messages',route=>{prompts.push(route.request().method());return route.fulfill({json:{ok:true}});});
 await page.goto('/mobile/group_chat');await page.locator('[data-agent-id="codex-one"]').click();
 const studio=page.frameLocator('#workspace-studio-frame');
 await expect(studio.locator('#chat-box')).toContainText('项目已经查看',{timeout:20000});
 await studio.locator('.agent-turn-process>summary').click();
 await expect(studio.locator('.agent-step-title')).toContainText('read_file');
 await expect(studio.locator('.studio-activity-bar')).toBeHidden();
 await expect(studio.locator('#user-input')).toBeVisible();
 for(const height of [844,390]){
  await page.setViewportSize({width:390,height});
  await expect(studio.locator('#user-input')).toBeInViewport();
  const composer=await studio.locator('.composer-dock').boundingBox();
  expect(composer.y+composer.height).toBeLessThanOrEqual(height+1);
  await expect(page.locator('.workspace-back')).toBeInViewport();
 }
 await page.setViewportSize({width:1400,height:900});
 await page.locator('#workspace-studio-sidebar-toggle').click();
 await expect(studio.locator('.studio-conversation-switcher')).toBeVisible();
 await expect(studio.locator('#studio-new-conversation')).toBeVisible();
 await expect(studio.locator('#studio-team-agent-tree-list')).toHaveAttribute('data-loaded','1');
 await expect(studio.locator('#studio-team-agent-tree-list .studio-team-node')).toHaveCount(1);
 const loadedRequests=catalogRequests;
 await page.locator('#workspace-studio-frame').evaluate(iframe=>{iframe.contentWindow.renderStudioConversations();iframe.contentWindow.renderStudioConversations();});
 expect(catalogRequests).toBe(loadedRequests);
 expect((await page.locator('#workspace-conversation').boundingBox()).x).toBe(68);
 await page.locator('#workspace-studio-sidebar-toggle').click();
 await expect(studio.locator('.studio-conversation-switcher')).toBeHidden();
 expect(prompts).toEqual([]);expect(errors).toEqual([]);
 await page.screenshot({path:'/tmp/clawcross-workspace-desktop.png'});
 await page.locator('.tab-item[data-tab="discover"]').click();
 await page.locator('#tab-discover button.create-shortcut').filter({hasText:'工作流'}).click();
 await expect(studio.locator('#page-orchestrate')).toBeVisible();
 const editor=await page.locator('#workspace-conversation').boundingBox(),canvas=await studio.locator('.orch-canvas-wrapper').boundingBox();
 expect(editor.x).toBe(68);expect(canvas.width).toBeGreaterThan(editor.width/2);expect(canvas.height).toBeGreaterThan(350);
 await expect(page.locator('#workspace-studio-sidebar-toggle')).toBeHidden();
 await page.screenshot({path:'/tmp/clawcross-workflow-wide.png'});
 expect(errors).toEqual([]);
});

const largeDirectory=Array.from({length:21},(_,i)=>({agent_id:'agent-'+i,platform:i<14?'webot':'codex',name:'伙伴 '+i,settings:{},location:{kind:'local'},updated_at:100-i}));
test('large directories keep groups reachable and the Agent center fills the main column',async({page})=>{
 await page.setViewportSize({width:1440,height:900});const {errors}=await fixtures(page,{agents:largeDirectory,experts:[{name:'设计模板',tag:'design-template',source:'agency',category:'design',persona:'Design helper'}]});
 await page.goto('/mobile/group_chat');
 await expect(page.locator('#workspace-agent-list button')).toHaveCount(4);
 await expect(page.locator('#group-list-container .group-item')).toBeInViewport();
 await page.locator('#workspace-agents-more').click();await expect(page.locator('#workspace-agent-list button')).toHaveCount(21);
 await page.locator('#workspace-agent-collapse').click();await expect(page.locator('#workspace-agent-list')).toBeHidden();
 await expect(page.locator('#group-list-container .group-item')).toBeInViewport();
 await page.locator('[data-chat-view="groups"]').click();await expect(page.locator('#workspace-agent-section')).toBeHidden();
 await page.locator('[data-chat-view="agents"]').click();await expect(page.locator('#group-list-container')).toBeHidden();
 await page.locator('#workspace-agent-collapse').click();await expect(page.locator('#workspace-agent-list button')).toHaveCount(21);
 await page.locator('#chat-search-input').fill('伙伴 20');await expect(page.locator('#workspace-agent-list button')).toHaveCount(1);
 await page.locator('.tab-item[data-tab="contacts"]').click();await page.locator('[data-contact-view="cards"]').click();
 await expect(page.locator('#contacts-list .contact-agent')).toHaveCount(21);
 await expect(page.locator('#contacts-list .contacts-ext-sub[aria-expanded="false"]')).toHaveCount(2);
 await expect(page.locator('#contacts-list .expert-item')).toBeHidden();
 await page.locator('#contacts-list .contacts-ext-sub[data-platform="webot"]').click();
 const panel=await page.locator('#contacts-list').boundingBox(),controls=await page.locator('.workspace-contact-controls').boundingBox();
 expect(panel.x).toBeGreaterThan(controls.x+controls.width-1);expect(panel.width).toBeGreaterThan(900);
 for(const selector of ['.contacts-search-bar','.contacts-team-switcher','.workspace-contact-views','.contacts-library-heading']){
  const child=await page.locator(selector).first().boundingBox();expect(child.x+child.width).toBeLessThanOrEqual(controls.x+controls.width);
 }
 const cards=await page.locator('#contacts-list .contact-agent').evaluateAll(items=>items.slice(0,4).map(el=>({x:el.getBoundingClientRect().x,y:el.getBoundingClientRect().y})));
 expect(cards[1].x).toBeGreaterThan(cards[0].x);expect(cards[1].y).toBe(cards[0].y);
 await page.screenshot({path:'/tmp/clawcross-agent-center-wide.png'});
 await page.locator('[data-contact-view="list"]').click();expect((await page.locator('#tab-contacts').boundingBox()).width).toBe(310);
 await expect(page.locator('#contacts-list .contacts-ext-sub[data-platform="webot"]')).toHaveAttribute('aria-expanded','true');
 await expect(page.locator('#contacts-list .contact-section-l4-preset')).toBeVisible();
 await page.locator('[data-contact-view="cards"]').click();
 await page.evaluate(()=>{
  const controls=document.querySelector('.workspace-contact-controls');controls.replaceWith(...controls.children);
  document.body.classList.add('group-list-collapsed');document.getElementById('chat-page').classList.remove('offscreen');
  return ConversationWorkspace.ready();
 });
 await expect(page.locator('.workspace-contact-controls')).toBeVisible();
 expect((await page.locator('.workspace-contact-controls').boundingBox()).width).toBe(310);
 expect((await page.locator('#contacts-list').boundingBox()).x).toBe(378);
 expect(errors).toEqual([]);
});

test('recent Agent shortcuts ignore settings update times and keep private chats in the group list',async({page})=>{
 const {errors}=await fixtures(page,{agents:largeDirectory.map(a=>({...a,updated_at:a.agent_id==='agent-20'?99999999:1})),groups:[{group_id:'g1',title:'我的伙伴',kind:'direct',member_count:2,members:[]}]});
 await page.addInitScript(()=>localStorage.setItem('clawcross_recent_agents:workspace-test',JSON.stringify({'agent-17':20,'agent-8':10})));
 await page.goto('/mobile/group_chat');
 await expect(page.locator('#workspace-agent-list button').first()).toHaveAttribute('data-agent-id','agent-17');
 await expect(page.locator('#group-list-container .group-item')).toHaveCount(1);
 await page.locator('[data-agent-id="agent-8"]').click();
 await expect(page.locator('#workspace-agent-list button').first()).toHaveAttribute('data-agent-id','agent-8');
 expect(errors).toEqual([]);
});

test('embedded Agent history does not wait for platform discovery or Team catalog',async({page})=>{
 const {errors}=await fixtures(page);await page.route('**/studio?embedded=1',route=>route.continue());
 let release;const held=new Promise(resolve=>release=resolve);
 await page.route('**/proxy_acpx_status',async route=>{await held;await route.fulfill({json:{available:true,tools:['codex'],transport:'direct'}});});
 await page.route('**/proxy_visual/experts',async route=>{await held;await route.fulfill({json:[]});});
 await page.route('**/api/llm_config_status',async route=>{await held;await route.fulfill({json:{configured:true}});});
 let teamRequests=0;
 await page.route('**/v1/teams',async route=>{teamRequests++;await held;await route.fulfill({json:{data:[]}});});
 const histories=[];
 await page.route('**/v1/agents/*/history**',route=>{histories.push(new URL(route.request().url()).pathname);return route.fulfill({json:{messages:[{role:'assistant',content:'Ready without discovery',ts:1}],has_more:false}});});
 await page.route('**/v1/agents/codex-one/events**',async route=>{await held;await route.fulfill({json:{turn:null,items:[],cursor:0}});});
 try{
  await page.goto('/mobile/group_chat');await page.locator('[data-agent-id="codex-one"]').click();
  await expect(page.frameLocator('#workspace-studio-frame').locator('#chat-box')).toContainText('Ready without discovery',{timeout:3000});
  await expect(page.locator('#workspace-conversation .workspace-frame-status')).toBeHidden({timeout:1000});
  await expect(page.locator('#app-splash')).toBeHidden({timeout:1000});
  expect(histories).toEqual(['/v1/agents/codex-one/history']);expect(teamRequests).toBe(0);
 }finally{release();}
 expect(errors).toEqual([]);
});

test('WeBot history renders before a slow runtime status request',async({page})=>{
 const {errors}=await fixtures(page);await page.route('**/studio?embedded=1',route=>route.continue());
 let release;const held=new Promise(resolve=>release=resolve);
 await page.route('**/v1/agents/webot-one?**',async route=>{await held;await route.fulfill({json:{agent_id:'webot-one',status:{state:'idle'}}});});
 await page.route('**/v1/agents/webot-one/history**',route=>route.fulfill({json:{messages:[{role:'assistant',content:'WeBot history ready',ts:1}],has_more:false}}));
 try{
  await page.goto('/mobile/group_chat');await page.locator('[data-agent-id="webot-one"]').click();
  await expect(page.frameLocator('#workspace-studio-frame').locator('#chat-box')).toContainText('WeBot history ready',{timeout:3000});
  await expect(page.locator('#workspace-conversation .workspace-frame-status')).toBeHidden({timeout:1000});
 }finally{release();}
 expect(errors).toEqual([]);
});

test('footer controls stay at the bottom, set appearance and keep mobile navigation compact',async({page})=>{
 await page.setViewportSize({width:1440,height:900});const {errors}=await fixtures(page);
 await page.goto('/mobile/group_chat');await expect(page.locator('#workspace-rail-settings')).toBeVisible();
 const footer=await page.locator('#workspace-rail-footer').boundingBox();expect(footer.y).toBeGreaterThan(650);
 await page.locator('#workspace-rail-appearance').click();await page.locator('[data-appearance="dark"]').click();
 await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
 await page.reload();await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
 await expect(page.locator('#workspace-rail-account')).toBeVisible();
 await expect(page.locator('#app-splash')).toBeHidden();await page.screenshot({path:'/tmp/clawcross-directory-dark.png'});
 await page.setViewportSize({width:390,height:844});
 await expect(page.locator('.workspace-rail-brand')).toBeHidden();await expect(page.locator('#workspace-rail-settings')).toBeHidden();
 await expect(page.locator('#workspace-rail-account')).toBeHidden();
 await expect(page.locator('.tab-item[data-tab="profile"]')).toBeVisible();
 expect(errors).toEqual([]);
});

test.describe('automatic interface language',()=>{
 test.use({locale:'ja-JP'});
 test('browser preference is detected, manual language wins, and automatic mode can be restored',async({page})=>{
  await fixtures(page,{manualLocale:false});await page.goto('/mobile/group_chat');
  await expect(page.locator('html')).toHaveAttribute('lang','ja-JP');
  await expect(page.locator('.tab-item[data-tab="contacts"] .tab-label')).toHaveText('連絡先');
  await page.locator('#workspace-rail-language').click();await page.locator('#clawcross-language-dialog button[lang="fr-FR"]').click();
  await expect(page.locator('html')).toHaveAttribute('lang','fr-FR');
  await page.reload();await expect(page.locator('html')).toHaveAttribute('lang','fr-FR');
  await page.locator('#workspace-rail-language').click();await page.locator('[data-language-auto]').click();
  await expect(page.locator('html')).toHaveAttribute('lang','ja-JP');
 });
});
