const {test,expect}=require('@playwright/test');
test.use({serviceWorkers:'block'});
const agents=[{agent_id:'codex-one',platform:'codex',name:'我的伙伴',driver:'acpx',settings:{},location:{kind:'local'}},{agent_id:'webot-one',platform:'webot',name:'研究伙伴',settings:{},location:{kind:'local'}}];
async function fixtures(page){
 const errors=[],calls=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{localStorage.setItem('clawcross_lang','zh');window.alert=()=>{};});
 await page.route('**/*',async route=>{
  const url=new URL(route.request().url()),p=url.pathname;
  if(url.hostname==='cdnjs.cloudflare.com')return route.fulfill({contentType:'application/javascript',body:'window.marked={parse:s=>s,setOptions(){}};window.hljs={highlightElement(){},highlightAll(){}};'});
  if(p==='/proxy_check_session')return route.fulfill({json:{valid:true,user_id:'workspace-test',has_password:true}});
  if(p==='/proxy_groups')return route.fulfill({json:{groups:[{group_id:'g1',title:'未来之室',kind:'group',member_count:3,member_names:['我','伙伴','朋友'],members:[]}]}});
  if(p==='/proxy_groups/g1')return route.fulfill({json:{group_id:'g1',title:'未来之室',members:[],messages:[],has_more:false}});
  if(p==='/proxy_groups/g1/messages')return route.fulfill({json:{messages:[],has_more:false}});
  if(p.includes('/v1/agents')&&!p.includes('/activity'))return route.fulfill({json:{data:agents}});
  if(p==='/teams'||p==='/proxy_agent_api/v1/teams')return route.fulfill({json:{teams:[],data:[]}});
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
 expect(prompts).toEqual([]);expect(errors).toEqual([]);
 await page.screenshot({path:'/tmp/clawcross-workspace-desktop.png'});
});
