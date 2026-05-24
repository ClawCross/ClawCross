const { test, expect } = require('@playwright/test');

async function stubMessageCenterNetwork(page) {
  await page.route(/https:\/\/cdnjs\.cloudflare\.com\/.*marked.*\.js/, (route) =>
    route.fulfill({
      contentType: 'application/javascript',
      body: 'window.marked = { parse: (s) => String(s || ""), setOptions() {} };',
    })
  );
  await page.route(/https:\/\/cdnjs\.cloudflare\.com\/.*highlight.*\.js/, (route) =>
    route.fulfill({
      contentType: 'application/javascript',
      body: 'window.hljs = { highlightAll() {}, highlightElement() {} };',
    })
  );
  await page.route(/https:\/\/cdnjs\.cloudflare\.com\/.*jszip.*\.js/, (route) =>
    route.fulfill({
      contentType: 'application/javascript',
      body: 'window.JSZip = function JSZip() {};',
    })
  );

  await page.route('**/proxy_check_session', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ valid: true, user_id: 'test-user', has_password: true, mode: 'local' }),
    });
  });

  await page.route('**/api/llm_config_status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ configured: true }),
    });
  });

  await page.route('**/proxy_groups', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ok: true, groups: [] }),
    });
  });
}

test('message center loads pretext and uses it for overview label gutter sizing', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await stubMessageCenterNetwork(page);
  await page.addInitScript(() => {
    window.alert = () => {};
    window.confirm = () => true;
    localStorage.setItem('clawcross_lang', 'zh');
  });

  await page.goto('/mobile/group_chat');
  await expect(page.locator('#tab-chats')).toBeVisible();

  const result = await page.evaluate(() => {
    const names = ['超级超级超级长的中文研究协调员名字 AlphaBetaGammaDelta'];
    const expectedWidth = window.ClawcrossTextLayout
      ? window.ClawcrossTextLayout.measureLabelGutter(names, {
          font: '600 10px Arial',
          lineHeight: 12,
          minWidth: 108,
          maxWidth: 176,
          padding: 26,
        })
      : 0;
    _overviewDetailCache = {
      timeline: [
        { elapsed: 0, event: 'start' },
        { elapsed: 8, event: 'agent_call', agent: names[0] },
        { elapsed: 19, event: 'agent_done', agent: names[0] },
      ],
      posts: [
        {
          elapsed: 14,
          author: names[0],
          content: 'update',
        },
      ],
      current_round: 1,
    };
    showDiscussionOverview();
    const overlay = document.getElementById('oasis-overview-overlay');
    return {
      ready: Boolean(window.ClawcrossTextLayout && typeof window.ClawcrossTextLayout.measureLabelGutter === 'function'),
      expectedWidth,
      overlayExists: Boolean(overlay),
      htmlHasMeasuredWidth: Boolean(overlay && overlay.innerHTML.includes(`width:${expectedWidth}px;flex-shrink:0;overflow:hidden;border-right:1.5px solid #e2e8f0;`)),
    };
  });

  expect(result.ready).toBeTruthy();
  expect(result.expectedWidth).toBeGreaterThan(110);
  expect(result.overlayExists).toBeTruthy();
  expect(result.htmlHasMeasuredWidth).toBeTruthy();
  expect(pageErrors).toEqual([]);
});
