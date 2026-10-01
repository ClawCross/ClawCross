const { test, expect } = require('@playwright/test');
const fs = require('node:fs');

test.use({ launchOptions: { executablePath: process.env.CLAWCROSS_TEST_CHROME || '/usr/bin/google-chrome' } });

test('manual compaction polls a background job instead of holding one request', async ({ page }) => {
  await page.setContent('<div id="status"></div>');
  await page.addScriptTag({ content: `
    window.currentSessionId = 's1'; window.currentLang = 'zh-CN';
    window.sessionCompactBusy = false; window.sessionCompactStatus = '';
    window.renderSessionContextDetail = () => document.querySelector('#status').textContent = sessionCompactStatus;
    window.fetchSessionStatus = async () => ({});
    window.calls = []; window.pollCount = 0;
    window.agentApi = async (method, path, body) => {
      calls.push(body.action);
      if (body.action === 'compact_async' || ++pollCount < 3) return {state: 'running', job_id: 'job1'};
      return {state: 'completed', job_id: 'job1', result: {triggered: true, before_tokens: 74000,
        after_tokens: 4000, saved_tokens: 70000, metadata: {preserved_tokens: 3500}}};
    };
    const nativeTimeout = window.setTimeout;
    window.setTimeout = fn => nativeTimeout(fn, 1);
  ` });
  const source = fs.readFileSync('src/frontend/static/js/main.js', 'utf8');
  const start = source.indexOf('async function compactCurrentSession(');
  const end = source.indexOf('\nfunction ', start + 10);
  await page.addScriptTag({ content: source.slice(start, end) });
  await page.evaluate(() => compactCurrentSession());
  await expect(page.locator('#status')).toContainText('省 70,000');
  await expect(page.locator('#status')).toContainText('近期保留原文约 3,500');
  expect(await page.evaluate(() => calls)).toEqual(['compact_async', 'compact_status', 'compact_status', 'compact_status']);
  expect(await page.evaluate(() => sessionCompactBusy)).toBe(false);
});
