const { test, expect } = require('@playwright/test');
test.use({ launchOptions: { executablePath: process.env.CLAWCROSS_TEST_CHROME || "/usr/bin/google-chrome" } });
const path = require('node:path');

const defaults = {
  context: { auto_compact: true, context_window_tokens: 1000000, history_tokens: 0, trigger_tokens: 0, target_tokens: 0,
    preserve_recent_turns: 4, summary_tokens: 2000, summarizer_input_tokens: 8000,
    summarizer_model: '', preserve_instructions: '' },
  approval: { mode: 'auto', approvals_reviewer: 'user', reviewer_model: '', reviewer_policy: '', reviewer_timeout_seconds: 120, reviewer_max_tokens: 16384, command_sandbox: 'off', sandbox_security: 'standard', sandbox_allowed_domains: [], sandbox_grants: [] },
  inference: {reasoning_effort: ''},
};

async function setup(page, options = {}) {
  const requests = [];
  const user = structuredClone(defaults);
  Object.assign(user.context, options.context || {});
  Object.assign(user.approval, options.approval || {});
  let session = structuredClone(options.session || {});
  const rememberRequests = options.rememberRequests || [];
  let remembered = {actions:structuredClone(options.actions || []),
    sandbox_security:session.approval?.sandbox_security || user.approval.sandbox_security,
    sandbox_grants:(session.approval?.sandbox_grants || []).map((grant,index)=>({...grant,key:'grant-'+index}))};
  await page.route('**/remembered-approvals**', async route => {
    const request = route.request();
    if (request.method() !== 'GET') {
      const body = request.method() === 'POST' ? request.postDataJSON() : null;
      rememberRequests.push({method:request.method(),url:request.url(),body});
      if (options.rememberReject) return route.fulfill({status:400,json:{detail:'管理员不允许此权限'}});
      if (body?.kind === 'tool') remembered.actions.push({tool:body.tool_name,key:'tool-new',arguments:body.arguments});
      else if (body) remembered.sandbox_grants.push({access:body.kind,target:body.target,key:'grant-new'});
      else {
        const key = request.url().split('/').at(-1);
        remembered.sandbox_grants = remembered.sandbox_grants.filter(grant=>grant.key!==key);
        remembered.actions = remembered.actions.filter(action=>action.key!==key);
      }
    }
    return route.fulfill({json:remembered});
  });
  await page.route('**/studio', route => route.fulfill({ contentType: 'text/html', body: '<html lang="zh"><body></body></html>' }));
  await page.route('**/proxy_webot_runtime_settings**', async route => {
    const request = route.request();
    const body = request.method() === 'POST' ? request.postDataJSON() : null;
    const scoped = body ? Boolean(body.session_id) : Boolean(new URL(request.url()).searchParams.get('session_id'));
    if (body) {
      requests.push(body);
      if (options.reject) return route.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({ detail: 'target_tokens must be smaller than trigger_tokens' }) });
      if (scoped && body.reset) session = {};
      else {
        const target = scoped ? session : user;
        for (const [section, patch] of Object.entries(body.settings)) Object.assign(target[section] ||= {}, patch);
      }
    }
    const settings = structuredClone(user);
    if (scoped) for (const [section, patch] of Object.entries(session)) Object.assign(settings[section], patch);
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ settings, model_capabilities: options.capabilities, context_usage: options.usage, last_compaction: scoped ? { before_tokens: 12000, after_tokens: 4000, duration_ms: 50, target_met: true } : null }) });
  });
  await page.goto('/studio');
  await page.addScriptTag({path:path.resolve('src/frontend/static/js/reasoning-levels.js')});
  await page.evaluate(() => {
    window.currentLang = 'zh-CN';
    window.currentSessionId = 'session-1';
    window.escapeHtml = text => { const el = document.createElement('div'); el.textContent = text; return el.innerHTML; };
  });
  await page.addStyleTag({ path: path.resolve('src/frontend/static/css/style.css') });
  await page.addStyleTag({ path: path.resolve('src/frontend/static/css/external-agent-settings.css') });
  await page.addStyleTag({ path: path.resolve('src/frontend/static/css/runtime-settings.css') });
  await page.addScriptTag({ path: path.resolve('src/frontend/static/js/remembered-approvals-ui.js') });
  await page.addScriptTag({ path: path.resolve('src/frontend/static/js/runtime-settings.js') });
  return requests;
}

test('settings save only this Agent and reset its inheritance without a user-wide scope', async ({ page }) => {
  const requests = await setup(page);
  await page.evaluate(() => openRuntimeSettings('session-1'));
  await expect(page.locator('#runtime-settings-scope')).toHaveValue('session');
  await expect(page.locator('#runtime-settings-result')).toContainText('12000 → 4000');
  await page.locator('[data-key="preserve_recent_turns"]').fill('2');
  await page.getByRole('tab', { name: '工具审核' }).click();
  await page.locator('[data-key="mode"]').selectOption('readonly');
  await page.locator('#runtime-settings-save').click();
  await expect(page.locator('#runtime-settings-result')).toContainText('已保存');
  expect(requests[0]).toEqual({ session_id: 'session-1', settings: { context: { preserve_recent_turns: 2 }, approval: { mode: 'readonly' } }, reset: false });
  await expect(page.locator('#runtime-settings-scope option')).toHaveCount(1);
  await expect(page.locator('[data-key="preserve_recent_turns"]')).toHaveValue('2');
  await page.getByRole('button', { name: '恢复继承设置' }).click();
  await expect(page.locator('[data-key="preserve_recent_turns"]')).toHaveValue('4');
  expect(requests[1].reset).toBe(true);
});

test('Manual and Bypass are distinct selectable modes with different saved values', async ({ page }) => {
  const requests = await setup(page);
  await page.evaluate(() => openRuntimeSettings('session-1'));
  await page.getByRole('tab', { name: '工具审核' }).click();
  const select = page.locator('[data-key="mode"]');
  await expect(select.locator('option')).toHaveCount(5);
  await select.selectOption({ label: 'Manual · 人工审核' });
  await expect(select).toHaveValue('manual');
  await expect(page.locator('#runtime-settings-reviewer-hint')).toContainText('需要批准的操作由你');
  await page.locator('#runtime-settings-save').click();
  await expect(page.locator('#runtime-settings-result')).toContainText('已保存');
  expect(requests[0].settings.approval.mode).toBe('manual');
  await select.selectOption({ label: 'Bypass · 无审核' });
  await expect(select).toHaveValue('bypass');
  await expect(page.locator('#runtime-settings-reviewer-hint')).toContainText('跳过操作确认');
  await page.locator('#runtime-settings-save').click();
  await expect(page.locator('#runtime-settings-result')).toContainText('已保存');
  expect(requests[1].settings.approval.mode).toBe('bypass');
});

test('remembered sandbox permissions are removed immediately without overwriting other settings', async ({ page }) => {
  const grants = [{access:'network',target:'example.com:443'}, {access:'read_path',target:'/tmp/approved-public-file.txt'}];
  const rememberRequests = [];
  const requests = await setup(page, {session:{approval:{sandbox_grants:grants}}, rememberRequests});
  await page.evaluate(() => openRuntimeSettings('session-1'));
  await page.getByRole('tab',{name:'工具审核'}).click();
  await page.locator('summary').filter({hasText:'KEEP Y'}).click();
  await expect(page.locator('[data-sandbox-grant]')).toHaveCount(2);
  await page.locator('[data-sandbox-grant]').filter({hasText:'example.com:443'}).getByRole('button',{name:'移除'}).click();
  await expect(page.locator('[data-sandbox-grant]')).toHaveCount(1);
  expect(requests).toHaveLength(0);
  expect(rememberRequests).toHaveLength(1);
  expect(rememberRequests[0].method).toBe('DELETE');
  expect(rememberRequests[0].url).toContain('/session-1/remembered-approvals/sandbox/network/grant-0');
  await expect(page.locator('[data-status]')).toContainText('已移除');
});

test('KEEP Y can add and remove both exact tools and sandbox targets on mobile', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  const rememberRequests = [];
  await setup(page, {rememberRequests});
  await page.evaluate(()=>openRuntimeSettings('session-1','approval'));
  await page.locator('summary').filter({hasText:'KEEP Y'}).click();
  await expect(page.locator('[data-list]')).toContainText('还没有 KEEP Y');
  await page.locator('.remembered-add > summary').click();
  await page.locator('[name=target]').fill('example.com:443');
  await page.getByRole('button',{name:'添加授权',exact:true}).click();
  await expect(page.locator('[data-sandbox-grant]')).toContainText('example.com:443');
  expect(rememberRequests[0].body).toEqual({kind:'network',target:'example.com:443'});
  await page.locator('[name=kind]').selectOption('tool');
  await page.locator('[name=tool_name]').fill('web_fetch');
  await page.locator('[name=arguments]').fill('{"url":"https://example.com"}');
  await page.getByRole('button',{name:'添加授权',exact:true}).click();
  await expect(page.locator('[data-tool-grant]')).toContainText('web_fetch');
  expect(rememberRequests[1].body).toEqual({kind:'tool',tool_name:'web_fetch',arguments:{url:'https://example.com'}});
  await page.locator('[data-tool-grant]').getByRole('button',{name:'移除'}).click();
  await expect(page.locator('[data-tool-grant]')).toHaveCount(0);
  expect(rememberRequests[2].url).toContain('/session-1/remembered-approvals/web_fetch/tool-new');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:'/tmp/clawcross-keepy-mobile.png'});
});

test('strict KEEP Y shows inactive sandbox grants and prevents adding expansions', async ({page}) => {
  await setup(page,{session:{approval:{sandbox_security:'strict',sandbox_grants:[{access:'network',target:'example.com:443'}]}}});
  await page.evaluate(()=>openRuntimeSettings('session-1','approval'));
  await page.locator('summary').filter({hasText:'KEEP Y'}).click();
  await expect(page.locator('[data-strict]')).toBeVisible();
  await expect(page.locator('[data-sandbox-grant]')).toContainText('不生效');
  await page.locator('.remembered-add > summary').click();
  await expect(page.locator('[name=kind]')).toHaveValue('tool');
  await expect(page.locator('[name=kind] option[value=network]')).toHaveJSProperty('disabled',true);
  await page.locator('[name=kind]').press('Home');
  await expect(page.locator('[name=kind]')).toHaveValue('tool');
  await expect(page.locator('[data-tool-fields]')).toBeVisible();
});

test('KEEP Y reports backend rejection without creating a permission', async ({page}) => {
  await setup(page,{rememberReject:true});
  await page.evaluate(()=>openRuntimeSettings('session-1','approval'));
  await page.locator('summary').filter({hasText:'KEEP Y'}).click();
  await page.locator('.remembered-add > summary').click();
  await page.locator('[name=target]').fill('example.com:443');
  await page.getByRole('button',{name:'添加授权',exact:true}).click();
  await expect(page.locator('[data-status]')).toContainText('管理员不允许');
  await expect(page.locator('[data-sandbox-grant]')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'添加授权',exact:true})).toBeEnabled();
});

test('model and summary instructions render as literal text', async ({ page }) => {
  const model = 'gpt" autofocus onfocus="window.injected=true';
  const instructions = '</textarea><img src=x onerror="window.injected=true">';
  await setup(page, { context: { summarizer_model: model, preserve_instructions: instructions } });
  await page.evaluate(() => openRuntimeSettings());
  await expect(page.locator('[data-key="summarizer_model"]')).toHaveValue(model);
  await expect(page.locator('[data-key="preserve_instructions"]')).toHaveValue(instructions);
  await expect(page.locator('#runtime-settings-modal [onfocus], #runtime-settings-modal img')).toHaveCount(0);
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
});

test('Auto SRT sandbox is saved for the selected session', async ({ page }) => {
  const requests = await setup(page);
  await page.evaluate(() => openRuntimeSettings('session-1'));
  await page.getByRole('tab', { name: '工具审核' }).click();
  await page.locator('[data-key="command_sandbox"]').selectOption('srt');
  await page.locator('#runtime-settings-save').click();
  await expect(page.locator('#runtime-settings-result')).toContainText('已保存');
  expect(requests[0]).toEqual({ session_id: 'session-1', settings: { approval: { command_sandbox: 'srt' } }, reset: false });
});

test('invalid settings show backend error and preserve edited values', async ({ page }) => {
  await setup(page, { reject: true });
  await page.evaluate(() => openRuntimeSettings());
  await page.locator('[data-key="trigger_tokens"]').fill('3000');
  await page.locator('[data-key="target_tokens"]').fill('4000');
  await page.locator('#runtime-settings-save').click();
  await expect(page.locator('#runtime-settings-result')).toContainText('target_tokens must be smaller');
  await expect(page.locator('[data-key="target_tokens"]')).toHaveValue('4000');
  await expect(page.locator('#runtime-settings-save')).toBeEnabled();
});

test('context meter shows actual usage against the default 1M window', async ({ page }) => {
  await setup(page, { usage: { tokens: 300000, budget: 1000000, source: 'api', breakdown: { messages: 200000, tool_results: 50000, summary: 25000, tools: 25000 } } });
  await page.evaluate(() => openRuntimeSettings('session-1'));
  await expect(page.getByRole('meter')).toHaveAttribute('aria-valuenow', '300000');
  await expect(page.getByRole('meter')).toHaveAttribute('aria-valuemax', '1000000');
  await expect(page.locator('.runtime-context-usage-heading strong')).toHaveText('30.0%');
  await expect(page.locator('[data-key="context_window_tokens"]')).toHaveValue('1000000');
  await expect(page.locator('.runtime-context-usage-legend')).toContainText('工具结果');
});

test('saving a manual window updates the meter even with cached usage', async ({ page }) => {
  const requests = await setup(page, { usage: { tokens: 10000, budget: 1000000, source: 'api' } });
  await page.evaluate(() => openRuntimeSettings('session-1'));
  await page.locator('[data-key="context_window_tokens"]').fill('20000');
  await page.locator('#runtime-settings-save').click();
  await expect(page.locator('#runtime-settings-result')).toContainText('已保存');
  expect(requests[0].settings.context.context_window_tokens).toBe(20000);
  await expect(page.getByRole('meter')).toHaveAttribute('aria-valuemax', '20000');
  await expect(page.locator('.runtime-context-usage-heading strong')).toHaveText('50.0%');
});

test('sandbox install is explicit and does not enable the sandbox', async ({ page }) => {
  let installs = 0;
  await setup(page);
  await page.addScriptTag({path:path.resolve('src/frontend/static/js/components.js')});
  await page.route('**/proxy_components/srt', route => {
    if (route.request().method() === 'POST') installs++;
    return route.fulfill({contentType:'application/json',body:JSON.stringify({name:'srt',installed:installs>0,ready:installs>0,missing:[],can_install:true,state:installs?'complete':'',platform:'linux'})});
  });
  await page.evaluate(() => openRuntimeSettings('session-1'));
  await page.getByRole('tab',{name:'工具审核'}).click();
  await expect(page.locator('[data-component-status]')).toContainText('尚未安装');
  expect(installs).toBe(0);
  await page.locator('[data-component-install]').click();
  await expect(page.locator('[data-component-status]')).toContainText('已安装');
  await expect(page.locator('[data-key="command_sandbox"]')).toHaveValue('off');
  expect(installs).toBe(1);
});

test('desktop new agent can select an external runtime and persists that platform', async ({page}) => {
  const created = [];
  await page.route('**/proxy_visual/experts*',route => route.fulfill({contentType:'application/json',body:'[]'}));
  await page.route('**/proxy_components/acpx',route => route.fulfill({contentType:'application/json',body:JSON.stringify({name:'acpx',installed:false,ready:false,missing:[],can_install:true,state:'',platform:'linux'})}));
  await page.route(/\/v1\/agents(?:\?.*)?$/, route => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON(); created.push(body);
      return route.fulfill({contentType:'application/json',body:JSON.stringify({...body,settings:{}})});
    }
    return route.fulfill({contentType:'application/json',body:JSON.stringify({data:created.map(a=>({...a,settings:{},status:{state:'idle'}}))})});
  });
  await page.route(/\/v1\/agents\/[^/?]+$/,route => route.fulfill({status:404,contentType:'application/json',body:'{}'}));
  await page.route('**/v1/teams*',route => route.fulfill({contentType:'application/json',body:'{"data":[]}'}));
  await page.goto('/studio');
  await page.evaluate(() => handleNewSession());
  await page.locator('#agent-meta-platform').selectOption('codex');
  await page.locator('#agent-meta-name').fill('中文助手');
  await expect(page.locator('#agent-meta-component')).toContainText('尚未安装');
  await page.locator('#agent-meta-modal .agent-meta-btn-save').click();
  await expect.poll(()=>created.length).toBe(1);
  expect(created[0].platform).toBe('codex');
  expect(created[0].name).toBe('中文助手');
  expect(created[0].agent_id.length).toBeGreaterThan(0);
});

test('compacted context displays the smaller estimate instead of claiming the old API total', async ({page}) => {
  await setup(page, {usage:{source:'estimate',tokens:32100,budget:1000000,breakdown:{system_prompt:600,tools:8000,summary:500,messages:21000,tool_results:2000}}});
  await page.evaluate(() => openRuntimeSettings('session-1'));
  await expect(page.locator('#runtime-settings-usage')).toContainText('32,100');
  await expect(page.locator('#runtime-settings-usage')).toContainText('估算');
  await expect(page.locator('#runtime-settings-usage')).not.toContainText('API 实测');
  await page.unroute('**/studio');
  await page.goto('/studio');
  await page.evaluate(() => { currentLang='zh-CN'; currentSessionId='session-1'; updateSessionContextUsageBadge(3,967900,32100,1000000,'estimate',{system_prompt:600,tools:8000,summary:500,messages:21000,tool_results:2000},0); });
  await expect(page.locator('#session-context-detail')).toContainText('待下一次 API 调用校准');
  await expect(page.locator('#session-context-detail')).not.toContainText('合计为 API 实测值');
});

test('known reasoning levels save a session override while unknown models hide the field', async ({page}) => {
  const requests = await setup(page, {capabilities:{model:'known-model',reasoning_effort_levels:['low','high'],reasoning_effort_default:'low'}});
  await page.evaluate(()=>openRuntimeSettings('session-1'));
  await expect(page.locator('[data-key="reasoning_effort"]')).toBeVisible();
  await page.locator('[data-key="reasoning_effort"]').press('End');
  await page.locator('#runtime-settings-save').click();
  await expect(page.locator('#runtime-settings-result')).toContainText('已保存');
  expect(requests[0].settings).toEqual({inference:{reasoning_effort:'high'}});
  await page.route('**/proxy_webot_runtime_settings**', route=>route.fulfill({json:{settings:defaults,model_capabilities:{model:'unknown',reasoning_effort_levels:[]}}}));
  await page.evaluate(()=>loadRuntimeSettingsScope());
  await expect(page.locator('[data-key="reasoning_effort"]')).toHaveCount(0);
});

test('unified effort saves a numeric Agent level without changing retained turns', async ({page}) => {
  const map={'1':'low','2':'low','3':'low','4':'medium','5':'high','6':'high','7':'max'};
  const requests=await setup(page,{capabilities:{model:'claude-opus-4-6',reasoning_effort_levels:['low','medium','high','max'],reasoning_level_map:map}});
  await page.evaluate(()=>openRuntimeSettings('session-1'));
  const slider=page.locator('[data-key="reasoning_level"]');
  await expect(slider).toHaveAttribute('type','range');
  await expect(page.locator('[data-reasoning-stop]')).toHaveCount(8);
  await slider.press('End');
  await expect(page.locator('[data-reasoning-stop][data-selected=true]')).toHaveAttribute('data-reasoning-stop','7');
  await page.screenshot({path:'/tmp/clawcross-effort-slider.png'});
  await expect(page.locator('[data-reasoning-output]')).toContainText('max');
  await page.locator('#runtime-settings-save').click();
  await expect(page.locator('#runtime-settings-result')).toContainText('已保存');
  expect(requests[0]).toMatchObject({session_id:'session-1',settings:{inference:{reasoning_level:7,reasoning_effort:''}}});
  expect(requests[0].settings.context).toBeUndefined();
  await expect(page.locator('[data-key="preserve_recent_turns"]')).toHaveValue('4');
});
