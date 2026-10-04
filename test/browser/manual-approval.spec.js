const {test, expect} = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

async function setup(page) {
  let approvals = [{approval_id: 'approval-live1', session_id: 's', status: 'pending',
    tool_name: 'run_command', args: {command: 'echo approved'}, request_reason: '请确认此操作', review: {reviewer: 'user'}}];
  const actions = [];
  await page.route('**/studio', route => route.fulfill({contentType: 'text/html', body:
    '<html lang="zh"><body><div id="studio-approval-strip"></div><div id="chat-box"></div><input id="input"><button id="send">发送</button></body></html>'}));
  await page.route('**/proxy_webot_tool_approvals?*', route => route.fulfill({json: {approvals}}));
  await page.route('**/proxy_webot_tool_approval_resolve', async route => {
    const body = route.request().postDataJSON();
    actions.push(body);
    approvals = [];
    await route.fulfill({json: {status: 'success', continuation: 'resumed', approval: {status: body.action === 'deny' ? 'denied' : 'approved'}}});
  });
  await page.goto('/studio');
  await page.addScriptTag({path: path.resolve('src/frontend/static/js/approval-ui.js')});
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
      const response = await fetch('/proxy_webot_tool_approvals?status=pending');
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
  await expect(page.locator('#input')).toBeDisabled();
  expect(actions).toEqual([{approval_id: 'approval-live1', action: 'approve', remember: false, session_id: 's'}]);
  expect(await page.evaluate(() => continuations)).toEqual([]);
});

test('typed KEEP Y uses the same controls and does not enter Agent chat', async ({page}) => {
  const actions = await setup(page);
  await page.locator('#input').fill('KEEP Y');
  const result = await page.evaluate(() => ClawcrossApproval.reply(inputField.value, ['s']));
  expect(result.continuation).toBe('resumed');
  expect(actions[0]).toEqual({approval_id: 'approval-live1', action: 'approve', remember: true, session_id: 's'});
  expect(await page.evaluate(() => continuations)).toEqual([]);
});

test('denial remains in the same bubble and returns to the waiting call', async ({page}) => {
  const actions = await setup(page);
  await page.getByRole('button', {name: '拒绝', exact: true}).click();
  await expect(page.locator('.cc-approval-status')).toHaveText('已拒绝');
  expect(actions[0].action).toBe('deny');
  expect(await page.evaluate(() => continuations)).toEqual([]);
});
