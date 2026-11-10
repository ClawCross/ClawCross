const {test,expect}=require('@playwright/test');
const path=require('node:path');
test.use({launchOptions:{executablePath:process.env.CLAWCROSS_TEST_CHROME||'/usr/bin/google-chrome'}});

for(const width of [390,1360])test(`Agent settings keep one editor open and preserve unrelated settings at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:844});
  const agent={agent_id:'chosen-agent',name:'小曼',platform:'webot',settings:{persona:'A companion',title:'My conversation',tools:[]}};
  const writes=[],errors=[];page.on('pageerror',error=>errors.push(error.message));
  const settings={context:{auto_compact:true,context_window_tokens:128000,history_tokens:0,trigger_tokens:0,target_tokens:0,preserve_recent_turns:2,summary_tokens:8000,summarizer_input_tokens:16000,summarizer_model:'',preserve_instructions:''},approval:{mode:'manual',command_sandbox:'auto',sandbox_security:'strict',sandbox_allowed_domains:[],sandbox_grants:[],reviewer_model:'',reviewer_policy:'',reviewer_timeout_seconds:120,reviewer_max_tokens:16384},inference:{reasoning_level:5,reasoning_effort:''}};
  await page.route('**/studio',route=>route.fulfill({contentType:'text/html',body:'<html lang="zh"><body></body></html>'}));
  await page.route('**/v1/agents/chosen-agent',route=>{
    if(route.request().method()==='PATCH'){const body=route.request().postDataJSON();writes.push(body);Object.assign(agent,body.name?{name:body.name}:{});Object.assign(agent.settings,body.settings);}
    return route.fulfill({json:agent});
  });
  await page.route('**/v1/agents/chosen-agent/capabilities',route=>route.fulfill({json:{transport:'webot'}}));
  await page.route('**/proxy_tools',route=>route.fulfill({json:{tools:[{name:'read_file'},{name:'manage_team'}]}}));
  await page.route('**/proxy_webot_runtime_settings**',route=>route.fulfill({json:{settings,model_capabilities:{},context_usage:{}}}));
  await page.route('**/v1/agents/chosen-agent/workspaces',route=>route.fulfill({json:{settings:{companion:true,user_shared:false,cli:false,teams:true,paths:[]},folders:[]}}));
  await page.route('**/v1/agents/model-profiles',route=>route.fulfill({json:{default:{model:'test-model',provider:'openai',model_capabilities:{}},profiles:[]}}));
  await page.goto('/studio');
  await page.evaluate(()=>{window.escapeHtml=value=>{const el=document.createElement('span');el.textContent=value;return el.innerHTML;};});
  for(const file of ['external-agent-settings','runtime-settings','workspace-settings','agent-settings'])await page.addStyleTag({path:path.resolve(`src/frontend/static/css/${file}.css`)});
  for(const file of ['reasoning-levels','external-agent-settings','runtime-settings','workspace-settings','model-profiles','agent-settings'])await page.addScriptTag({path:path.resolve(`src/frontend/static/js/${file}.js`)});
  await page.evaluate(()=>AgentSettings.open('chosen-agent'));
  const home=page.locator('#agent-settings-home');
  await home.locator('[data-category=profile]').click();
  await home.locator('input[name=name]').fill('新的小曼');
  await home.locator('[data-save]').click();
  await expect(home.locator('[data-status]')).toHaveText('已保存');
  expect(writes).toEqual([{name:'新的小曼',settings:{persona:'A companion',title:'My conversation'}}]);
  await home.locator('.agent-settings-back').click();
  await home.locator('[data-category=tools]').click();
  await home.locator('input[value=read_file]').check();
  await home.locator('[data-save]').click();
  await expect(home.locator('[data-status]')).toHaveText('已保存');
  expect(writes[1]).toEqual({settings:{tools:['read_file']}});
  await home.locator('.agent-settings-back').click();
  for(const [category,dialog]of [['approval','#runtime-settings-modal'],['workspaces','#workspace-settings-modal'],['model','#agent-model-settings']]){
    await home.locator(`[data-category=${category}]`).click();
    await expect(page.locator(dialog)).toBeVisible();
    await expect(page.getByRole('dialog')).toHaveCount(1);
    if(category==='approval'){await expect(page.locator('[data-key=mode]')).toHaveValue('manual');await expect(page.locator('[data-section=inference]')).toHaveCount(0);}
    const box=await page.locator(dialog).boundingBox();expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(width+1);
    await page.locator(`${dialog} .agent-settings-back`).click();
    await expect(home).toBeVisible();await expect(home).toContainText('新的小曼');
    await expect(page.getByRole('dialog')).toHaveCount(1);
  }
  expect(writes).toHaveLength(2);expect(errors).toEqual([]);
  await page.screenshot({path:`/tmp/clawcross-agent-settings-${width}.png`});
});
