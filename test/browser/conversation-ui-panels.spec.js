const { test, expect } = require('@playwright/test');
const path = require('node:path');

test.use({ launchOptions: { executablePath: process.env.CLAWCROSS_TEST_CHROME || '/usr/bin/google-chrome' } });

for (const width of [320, 375, 1024]) {
  test(`tool panels move, resize and restore at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 720 });
    await page.setContent('<div id="chat-box"></div><details id="studio-more-menu"><summary>+</summary><details id="conversation-ui-panel-menu"><summary>对话面板</summary><div id="conversation-ui-panel-list"></div></details></details>');
    await page.addStyleTag({ path: path.resolve('src/frontend/static/css/style.css') });
    await page.addScriptTag({ path: path.resolve('src/frontend/static/js/conversation-ui-panels.js') });
    await page.evaluate(() => {
      window.samplePanel = { title: '计数器', html: '<button id="count">0</button>', css: '', javascript: 'document.querySelector("button").onclick = e => e.target.textContent = +e.target.textContent + 1;' };
      document.querySelector('#chat-box').append(ConversationUiPanels.create(samplePanel, 'session-a'));
    });
    const panel = page.locator('.conversation-ui-panel');
    const frame = page.frameLocator('.conversation-ui-panel iframe');
    await expect(page.locator('iframe')).toHaveAttribute('sandbox', 'allow-scripts');
    await frame.locator('#count').click();
    const heading = await panel.locator('.conversation-ui-panel-heading').boundingBox();
    await page.mouse.move(heading.x + 30, heading.y + 20);
    await page.mouse.down();
    await page.mouse.move(heading.x + 65, heading.y + 110, { steps: 6 });
    await page.mouse.up();
    await expect(panel).toHaveClass(/is-floating/);
    await expect(frame.locator('#count')).toHaveText('1');
    const before = await panel.boundingBox();
    const handle = await panel.locator('.conversation-ui-panel-resize').boundingBox();
    await page.mouse.move(handle.x + 10, handle.y + 10);
    await page.mouse.down();
    await page.mouse.move(handle.x + 90, handle.y + 90, { steps: 6 });
    await page.mouse.up();
    const after = await panel.boundingBox();
    expect(after.height).toBeGreaterThan(before.height);
    expect(after.x + after.width).toBeLessThanOrEqual(width - 7);
    expect(after.y + after.height).toBeLessThanOrEqual(713);
    await panel.getByRole('button', { name: '最小化面板', exact: true }).click();
    await expect(panel).toBeHidden();
    await page.locator('#studio-more-menu > summary').click();
    await page.locator('#conversation-ui-panel-menu > summary').click();
    await page.getByRole('button', { name: '计数器 已最小化 · 恢复' }).click();
    await expect(panel).toBeVisible();
    await expect(frame.locator('#count')).toHaveText('1');
    await panel.getByRole('button', { name: '关闭面板', exact: true }).click();
    await expect(page.locator('iframe')).toHaveCount(0);
    await page.locator('#studio-more-menu > summary').click();
    await page.getByRole('button', { name: '计数器 已关闭 · 重新打开' }).click();
    await expect(frame.locator('#count')).toHaveText('0');
    await page.setViewportSize({ width: 320, height: 400 });
    const shrunk = await panel.boundingBox();
    expect(shrunk.x + shrunk.width).toBeLessThanOrEqual(313);
    expect(shrunk.y + shrunk.height).toBeLessThanOrEqual(393);
    await page.evaluate(() => ConversationUiPanels.beginSession('session-b'));
    await expect(panel).toHaveCount(0);
    await expect(page.locator('#conversation-ui-panel-list')).toHaveText('暂无对话面板');
    await page.evaluate(() => document.querySelector('#chat-box').append(ConversationUiPanels.create(samplePanel, 'session-a')));
    await expect(panel).toHaveClass(/is-floating/);
    await page.evaluate(() => ConversationUiPanels.reset());
    await expect(page.locator('iframe')).toHaveCount(0);
  });
}
