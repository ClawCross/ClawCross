const {test, expect} = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

async function setup(page) {
  let approvals = [{approval_id: 'approval-live1', session_id: 's', status: 'pending',
    tool_name: 'run_command', args: {command: 'echo approved'}, request_reason: '请确认此操作', review: {reviewer: 'user'}}];
  const actions = [];
  await page.route('**/studio', route => route.fulfill({contentType: 'text/html', body:
    '<html lang="zh"><body><div id="studio-approval-strip"></div><div id="chat-box"></div><input id="input"><button id="send">发送</button></body></html>'}));
  await page.route('**/proxy_webot_tool_approvals?*', route => route.fulfill({json: {
    approvals: new URL(route.request().url()).searchParams.get('status') === 'pending' ? approvals.filter(item => item.status === 'pending') : approvals,
  }}));
  await page.route('**/proxy_webot_tool_approval_resolve', async route => {
    const body = route.request().postDataJSON();
    actions.push(body);
    approvals = approvals.map(item => ({...item, status: body.action === 'deny' ? 'denied' : 'approved',
      review: {...item.review, remembered: body.remember}}));
    await route.fulfill({json: {status: 'success', continuation: 'resumed', approval: {
      status: body.action === 'deny' ? 'denied' : 'approved', remember: body.remember}}});
  });
  await page.goto('/studio');
  await page.addScriptTag({path: path.resolve('src/frontend/static/js/approval-ui.js')});
  await page.addStyleTag({path: path.resolve('src/frontend/static/css/markdown-shared.css')});
  await page.evaluate(() => {
    window.currentSessionId = 's'; window.currentLang = 'zh-CN'; window.cancelTargetSessionId = 's';
    window.inputField = document.getElementById('input'); window.sendBtn = document.getElementById('send');
    inputField.disabled = true; sendBtn.style.display = 'none';
    window.continuations = []; window.watchApprovalContinuation = sid => continuations.push(sid);
    window._projectUpdateToast = error => {throw Error(error);};
    window.appendMessage = (text, user) => {
      const node = document.createElement('div');
      node.innerHTML = user ? text : ClawcrossApproval.renderPrompt(text);
      document.getElementById('chat-box').appendChild(node);
      return node;
    };
    window.refreshStudioApprovalStrip = async () => {
      const response = await fetch('/proxy_webot_tool_approvals?status=');
      renderStudioApprovalStrip((await response.json()).approvals);
    };
  });
  const source = fs.readFileSync('src/frontend/static/js/main.js', 'utf8');
  await page.addScriptTag({content: source.slice(source.indexOf('let _studioPendingApproval ='),
    source.indexOf('async function refreshStudioApprovalStrip()'))});
  await page.evaluate(() => refreshStudioApprovalStrip());
  return actions;
}

test('live approval button continues the existing call without starting a new turn', async ({page}) => {
  const actions = await setup(page);
  await expect(page.locator('#input')).toBeEnabled();
  await expect(page.locator('#send')).toBeVisible();
  await page.getByRole('button', {name: '同意', exact: true}).click();
  await expect(page.locator('.cc-approval-status')).toHaveText('已批准');
  await expect(page.locator('.cc-approval-disclosure')).toHaveJSProperty('open', false);
  await expect(page.locator('.cc-approval-bubble pre')).toBeHidden();
  await page.locator('.cc-approval-heading').click();
  await expect(page.locator('.cc-approval-disclosure')).toHaveJSProperty('open', true);
  await page.evaluate(() => refreshStudioApprovalStrip());
  await expect(page.locator('.cc-approval-disclosure')).toHaveJSProperty('open', true);
  await expect(page.locator('.cc-approval-actions')).toHaveCount(0);
  await expect(page.locator('#input')).toBeDisabled();
  expect(actions).toEqual([{approval_id: 'approval-live1', action: 'approve', remember: false, session_id: 's'}]);
  expect(await page.evaluate(() => continuations)).toEqual([]);
});

test('typed KEEP Y uses the same controls and does not enter Agent chat', async ({page}) => {
  const actions = await setup(page);
  await page.locator('#input').fill('KEEP Y');
  const result = await page.evaluate(() => ClawcrossApproval.reply(inputField.value, ['s']));
  expect(result.continuation).toBe('resumed');
  await expect(page.locator('.cc-approval-status')).toHaveText('已批准并记住');
  await expect(page.locator('.cc-approval-disclosure')).toHaveJSProperty('open', false);
  expect(actions[0]).toEqual({approval_id: 'approval-live1', action: 'approve', remember: true, session_id: 's'});
  expect(await page.evaluate(() => continuations)).toEqual([]);
});

test('denial remains in the same bubble and returns to the waiting call', async ({page}) => {
  const actions = await setup(page);
  await page.getByRole('button', {name: '拒绝', exact: true}).click();
  await expect(page.locator('.cc-approval-status')).toHaveText('已拒绝');
  await expect(page.locator('.cc-approval-disclosure')).toHaveJSProperty('open', false);
  expect(actions[0].action).toBe('deny');
  expect(await page.evaluate(() => continuations)).toEqual([]);
});

for (const surface of ['studio', 'mobile']) for (const reply of ['Y', 'N', 'KEEP Y']) {
  test(`${surface} input ${reply} resolves approval without sending an Agent message`, async ({page}) => {
    const actions = await setup(page);
    if (surface === 'studio') {
      await page.evaluate(() => {
        window.pendingImages = []; window.pendingFiles = []; window.pendingAudios = []; window.pendingWorkflows = [];
        window._setWeBotPolicyStatus = () => {};
      });
      const source = fs.readFileSync('src/frontend/static/js/main.js', 'utf8');
      const start = source.indexOf('async function handleSend() {');
      await page.addScriptTag({content: source.slice(start, source.indexOf("    if (_ocChatMode === 'acp'", start)) +
        "throw Error('Approval entered Agent chat');\n}"});
      await page.evaluate(() => sendBtn.addEventListener('click', handleSend));
    } else {
      await page.evaluate(() => {
        inputField.id = 'msg-input';
        window.currentGroupId = 'g'; window.currentMembers = [{agent: {agent_id:'s'}}];
        window.pendingAttachments = []; window.mcPendingAudios = []; window.selectedOasisContexts = [];
        window.selectedWorkflow = null; window.closeInputActions = () => {}; window.t = key => key; window.toast = () => {};
        window.refreshPendingApprovalsForCurrentGroup = refreshStudioApprovalStrip;
      });
      const source = fs.readFileSync('src/frontend/templates/group_chat_mobile.html', 'utf8');
      const start = source.indexOf('async function sendMessage() {');
      await page.addScriptTag({content: source.slice(start, source.indexOf("  closeInputActions();\n\n  const btn", start)) +
        "throw Error('Approval entered Agent chat');\n}"});
      await page.evaluate(() => sendBtn.addEventListener('click', sendMessage));
    }
    await page.locator(surface === 'studio' ? '#input' : '#msg-input').fill(reply);
    await page.locator('#send').click();
    await expect.poll(() => actions.length).toBe(1);
    expect(actions[0]).toEqual({approval_id:'approval-live1', session_id:'s', action:reply === 'N' ? 'deny' : 'approve', remember:reply === 'KEEP Y'});
    await expect(page.locator('.cc-approval-disclosure')).toHaveJSProperty('open', false);
    expect(await page.evaluate(() => continuations)).toEqual([]);
  });
}

test('approval completed from another client is collapsed on the next status refresh', async ({page}) => {
  await setup(page);
  await page.evaluate(() => renderStudioApprovalStrip([{approval_id:'approval-live1', session_id:'s',
    status:'used', review:{reviewer:'user', remembered:true}}]));
  await expect(page.locator('.cc-approval-status')).toHaveText('已批准并记住');
  await expect(page.locator('.cc-approval-disclosure')).toHaveJSProperty('open', false);
  await expect(page.locator('.cc-approval-actions')).toHaveCount(0);
});
