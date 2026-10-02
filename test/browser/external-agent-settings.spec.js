const {test, expect} = require('@playwright/test');
const path = require('node:path');
test.use({launchOptions: {executablePath: process.env.CLAWCROSS_TEST_CHROME || '/usr/bin/google-chrome'}});

async function setup(page, platform = 'codex') {
  const key = platform === 'claude' ? 'effort' : 'reasoning_effort';
  const card = {platform, transport: 'acpx', clawcross_tools: false, settings: {}, config_options: [
    {id: key, name: '思考强度', currentValue: 'medium', options: [{value:'low',name:'Low'},{value:'medium',name:'Medium'},{value:'high',name:'High'}]},
  ]};
  const writes = [];
  await page.route('**/studio', route => route.fulfill({contentType:'text/html',body:`<button id="return-focus">Open</button>
    <button data-webot-runtime id="studio-sandbox-settings">Sandbox</button><button id="studio-external-settings" hidden>ACP</button>
    <p id="external-runtime-hint" hidden></p><div class="oc-context-usage-wrap"></div>
    <button id="tool-toggle-btn" style="display:flex">Tools</button><div id="tool-panel"></div><select id="oc-run-mode"></select>`}));
  await page.route('**/v1/agents/session-1/capabilities', route => route.fulfill({json:card}));
  await page.route('**/v1/agents/session-1/acp-settings', route => {
    const value = route.request().postDataJSON(); writes.push(value);
    card.settings = {...card.settings, ...value}; card.clawcross_tools = value.clawcross_tools;
    return route.fulfill({json:card});
  });
  await page.goto('/studio');
  await page.evaluate(() => {window.currentSessionId = 'session-1';});
  await page.addStyleTag({path:path.resolve('src/frontend/static/css/external-agent-settings.css')});
  await page.addScriptTag({path:path.resolve('src/frontend/static/js/external-agent-settings.js')});
  return {writes, key};
}

test('ACP menu and settings follow native capabilities and save only this Agent', async ({page}) => {
  const {writes,key} = await setup(page);
  await page.evaluate(() => ExternalAgentSettings.syncMenu('session-1'));
  await expect(page.locator('#studio-external-settings')).toBeVisible();
  await expect(page.locator('#studio-sandbox-settings')).toBeHidden();
  await expect(page.locator('#tool-toggle-btn')).toBeHidden();
  await page.locator('#return-focus').focus();
  await page.evaluate(() => openExternalAgentSettings('session-1'));
  await expect(page.locator('#external-tools')).not.toBeChecked();
  await page.locator(`[data-option="${key}"]`).selectOption('high');
  await page.locator('#external-tools').check();
  await page.getByText('连接与工具范围', {exact:true}).click();
  await page.locator('#external-tool-list').fill('get_current_time');
  await page.locator('[data-save]').click();
  await expect(page.locator('[data-status]')).toContainText('已保存');
  expect(writes).toEqual([{config_options:{reasoning_effort:'high'},clawcross_tools:true,tools:['get_current_time'],timeout_sec:180,ttl_sec:300}]);
  await page.keyboard.press('Escape');
  await expect(page.locator('#external-agent-settings')).toHaveCount(0);
  await expect(page.locator('#return-focus')).toBeFocused();
});

test('Claude uses its own effort key and the dialog fits a mobile viewport', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await setup(page,'claude');
  await page.evaluate(() => openExternalAgentSettings('session-1'));
  await expect(page.locator('[data-option="effort"]')).toBeVisible();
  await expect(page.locator('[data-option="reasoning_effort"]')).toHaveCount(0);
  const box = await page.locator('.external-settings-dialog').boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  expect(box.y + box.height).toBeLessThanOrEqual(844);
});

test('ACP tabs use the selected external Agent rather than the WeBot session', async ({page}) => {
  await setup(page);
  const writes=[];
  await page.route('**/v1/agents/codex-selected/capabilities',route=>route.fulfill({json:{
    platform:'codex',transport:'acpx',clawcross_tools:false,settings:{},config_options:[]}}));
  await page.route('**/v1/agents/codex-selected/acp-settings',route=>{
    writes.push(route.request().postDataJSON());
    return route.fulfill({json:{platform:'codex',transport:'acpx',clawcross_tools:true,settings:writes.at(-1),config_options:[]}});
  });
  await page.evaluate(()=>{
    window._ocChatMode='acp';window._acpTool='codex';
    window.acpResolveSessionName=()=> 'codex-selected';
  });
  await page.evaluate(()=>ExternalAgentSettings.syncMenu());
  await expect(page.locator('#studio-external-settings')).toBeVisible();
  await page.evaluate(()=>openExternalAgentSettings());
  await expect(page.locator('#external-agent-settings')).toBeVisible();
  await page.locator('#external-tools').check();
  await page.locator('[data-save]').click();
  await expect(page.locator('[data-status]')).toContainText('已保存');
  expect(writes.length).toBe(1);
  expect(writes[0].clawcross_tools).toBe(true);
  await page.locator('[data-close]').click();
  await page.evaluate(()=>{window._ocChatMode='internal';});
  await page.route('**/v1/agents/session-1/capabilities',route=>route.fulfill({json:{transport:'webot'}}));
  await page.evaluate(()=>ExternalAgentSettings.syncMenu('other-session'));
  // A stale asynchronous update for a different Agent must not replace the menu.
  await expect(page.locator('#studio-external-settings')).toBeVisible();
  await page.evaluate(()=>ExternalAgentSettings.syncMenu());
  await expect(page.locator('#studio-external-settings')).toBeHidden();
  await expect(page.locator('#studio-sandbox-settings')).toBeVisible();
});

test('uncreated ACP profile opens settings without creation and creates only on explicit save', async ({page}) => {
  await setup(page);
  let created=0;
  await page.route('**/v1/agents/new-codex/capabilities',route=>route.fulfill({status:404,json:{detail:'no agent'}}));
  await page.evaluate(()=>{
    window._ocChatMode='acp';window._acpTool='codex';
    window.acpResolveSessionName=()=> 'new-codex';
    window.studioEnsureAgent=(id,fields)=>fetch('/v1/agents',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent_id:id,...fields})});
  });
  await page.route('**/v1/agents',route=>{created++;return route.fulfill({json:{agent_id:'new-codex'}});});
  await page.evaluate(()=>ExternalAgentSettings.syncMenu());
  await expect(page.locator('#studio-external-settings')).toBeVisible();
  expect(created).toBe(0);
  await page.evaluate(()=>openExternalAgentSettings());
  await expect(page.locator('#external-agent-settings')).toBeVisible();
  await expect(page.locator('[data-save]')).toHaveText('创建并保存');
  expect(created).toBe(0);
  await page.route('**/v1/agents/new-codex/acp-settings', route=>route.fulfill({json:{platform:'codex',transport:'acpx',clawcross_tools:true,settings:{}}}));
  await page.locator('[data-save]').click();
  await expect(page.locator('[data-status]')).toContainText('已保存');
  expect(created).toBe(1);
  await page.locator('[data-save]').click();
  await expect(page.locator('[data-status]')).toContainText('已保存');
  expect(created).toBe(1);
});


test('test connection refreshes configuration without sending a chat', async ({page}) => {
  await setup(page);
  let probes=0;
  const chats=[];
  await page.route('**/v1/chat/completions',route=>{chats.push(route.request().url());return route.abort();});
  await page.route('**/v1/agents/session-1/test-connection',route=>{
    probes++;
    return route.fulfill({json:{platform:'codex',transport:'acpx',clawcross_tools:true,settings:{},config_options:[
      {id:'model',name:'模型',currentValue:'gpt-5.5',options:[{value:'gpt-5.5',name:'gpt-5.5'}]},
      {id:'reasoning_effort',name:'思考强度',currentValue:'medium',options:[{value:'low',name:'Low'},{value:'medium',name:'Medium'},{value:'high',name:'High'}]}
    ]}});
  });
  await page.evaluate(()=>openExternalAgentSettings('session-1'));
  await page.locator('[data-option="reasoning_effort"]').selectOption('high');
  await page.locator('[data-test]').click();
  await expect(page.locator('[data-status]')).toContainText('连接成功');
  await expect(page.locator('[data-option="model"]')).toHaveValue('gpt-5.5');
  await expect(page.locator('[data-option="reasoning_effort"]')).toHaveValue('high');
  expect(probes).toBe(1);expect(chats).toEqual([]);
});
