const { test, expect } = require('@playwright/test');
test.use({ launchOptions: { executablePath: process.env.CLAWCROSS_TEST_CHROME || "/usr/bin/google-chrome" } });
const path = require('node:path');

const defaults = {
  context: { auto_compact: true, context_window_tokens: 1000000, history_tokens: 0, trigger_tokens: 0, target_tokens: 0,
    preserve_recent_turns: 4, summary_tokens: 2000, summarizer_input_tokens: 8000,
    summarizer_model: '', preserve_instructions: '' },
  approval: { mode: 'auto', approvals_reviewer: 'user', reviewer_model: '', reviewer_policy: '', reviewer_timeout_seconds: 30, command_sandbox: 'off' },
};

async function setup(page, options = {}) {
  const requests = [];
  const user = structuredClone(defaults);
  Object.assign(user.context, options.context || {});
  let session = {};
  await page.route('**/studio', route => route.fulfill({ contentType: 'text/html', body: '<html><body></body></html>' }));
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
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ settings, context_usage: options.usage, last_compaction: scoped ? { before_tokens: 12000, after_tokens: 4000, duration_ms: 50, target_met: true } : null }) });
  });
  await page.goto('/studio');
  await page.evaluate(() => {
    window.currentLang = 'zh-CN';
    window.currentSessionId = 'session-1';
    window.escapeHtml = text => { const el = document.createElement('div'); el.textContent = text; return el.innerHTML; };
  });
  await page.addStyleTag({ path: path.resolve('src/frontend/static/css/style.css') });
  await page.addScriptTag({ path: path.resolve('src/frontend/static/js/runtime-settings.js') });
  return requests;
}

test('settings save only changes to the selected scope and reset inheritance', async ({ page }) => {
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
  await page.locator('#runtime-settings-scope').selectOption('user');
  await expect(page.locator('[data-key="preserve_recent_turns"]')).toHaveValue('4');
  await expect(page.locator('[data-key="mode"]')).toHaveValue('auto');
  await page.locator('#runtime-settings-scope').selectOption('session');
  await expect(page.locator('[data-key="preserve_recent_turns"]')).toHaveValue('2');
  await page.getByRole('button', { name: '恢复继承设置' }).click();
  await expect(page.locator('[data-key="preserve_recent_turns"]')).toHaveValue('4');
  expect(requests[1].reset).toBe(true);
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
