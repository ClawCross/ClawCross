const { test, expect } = require('@playwright/test');

async function stubCreatorNetwork(page, calls, options = {}) {
  const arxivResponse = options.arxivResponse || {
    ok: true,
    papers_count: 3,
    auto_imported: true,
    summary: {
      import_source: 'supervisor-mentor',
      mentor_meta: { name: 'Geoffrey Hinton' },
    },
    team_config: {
      team_name: '导师团队',
      oasis_experts: [
        {
          name: 'Geoffrey Hinton',
          tag: 'geoffrey-hinton',
          persona: '# Geoffrey Hinton AI Mentor\n\nAlways question shallow reasoning.',
        },
      ],
    },
  };
  const feishuResponse = options.feishuResponse || {
    ok: true,
    messages_length: 512,
    auto_imported: true,
    distillation: {
      personality_tags: ['直接', '数据驱动'],
      culture_tags: ['结果导向'],
      impression: '说话短，判断快。',
      evidence_summary: 'Based on Feishu messages.',
    },
    summary: {
      import_source: 'colleague-skill',
      colleague_meta: { name: '张三' },
    },
    team_config: {
      team_name: '张三团队',
      oasis_experts: [
        {
          name: '张三',
          tag: 'zhangsan',
          persona: '## PART A：工作能力\n\n- 负责核心后端交付\n\n---\n\n## PART B：人物性格\n\n- 先给结论',
        },
      ],
    },
  };

  await page.route('**/proxy_visual/experts', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

  await page.route('**/api/team-creator/jobs?limit=12*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ok: true, jobs: [] }),
    });
  });

  await page.route('**/api/tinyfish/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        config: {
          api_key_configured: true,
          base_url: 'https://agent.tinyfish.ai',
        },
        recent_runs: [],
      }),
    });
  });

  await page.route('**/api/team-creator/translate', async (route) => {
    const body = JSON.parse(route.request().postData() || '{}');
    const texts = Array.isArray(body.texts) ? body.texts : [body.text || ''];
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ok: true, translations: texts }),
    });
  });

  await page.route('**/api/team-creator/arxiv-search', async (route) => {
    calls.arxiv = (calls.arxiv || 0) + 1;
    calls.lastArxivPayload = JSON.parse(route.request().postData() || '{}');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(arxivResponse),
    });
  });

  await page.route('**/api/team-creator/feishu-collect', async (route) => {
    calls.feishu = (calls.feishu || 0) + 1;
    calls.lastFeishuPayload = JSON.parse(route.request().postData() || '{}');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(feishuResponse),
    });
  });
}

test('team creator can generate a mentor directly from ArXiv', async ({ page }) => {
  const calls = { arxiv: 0, feishu: 0, lastArxivPayload: null };
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await stubCreatorNetwork(page, calls);
  await page.addInitScript(() => {
    window.alert = () => {};
    window.confirm = () => true;
    localStorage.setItem('lang', 'zh-CN');
    localStorage.setItem('clawcross_lang', 'zh');
  });

  await page.goto('/creator');
  await page.locator('#builder-mode-import-mentor').click();
  await page.locator('#mentor-arxiv-name').fill('Geoffrey Hinton');
  await page.locator('#mentor-arxiv-affiliation').fill('University of Toronto');
  await page.locator('#mentor-arxiv-max-results').fill('15');
  await page.locator('#generate-mentor-btn').click();

  await expect.poll(() => calls.arxiv).toBe(1);
  expect(calls.lastArxivPayload).toMatchObject({
    author_name: 'Geoffrey Hinton',
    affiliation: 'University of Toronto',
    auto_import: true,
    max_results: 15,
  });

  await expect(page.locator('#builder-step-roles')).toBeVisible();
  await expect(page.locator('#builder-roles-list .builder-role-name').first()).toHaveValue('Geoffrey Hinton');
  await expect(page.locator('#builder-status-text')).toContainText('Geoffrey Hinton');
  expect(pageErrors).toEqual([]);
});

test('team creator can collect Feishu data and auto-import a colleague', async ({ page }) => {
  const calls = { arxiv: 0, feishu: 0, lastFeishuPayload: null };
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await stubCreatorNetwork(page, calls);
  await page.addInitScript(() => {
    window.alert = () => {};
    window.confirm = () => true;
    localStorage.setItem('lang', 'zh-CN');
    localStorage.setItem('clawcross_lang', 'zh');
  });

  await page.goto('/creator');
  await page.locator('#builder-mode-import-colleague').click();
  await page.locator('#feishu-app-id').fill('cli_test');
  await page.locator('#feishu-app-secret').fill('secret-test');
  await page.locator('#feishu-target-name').fill('张三');
  await page.locator('#feishu-role').fill('后端工程师');
  await page.locator('#feishu-company').fill('Clawcross');
  await page.locator('#feishu-level').fill('L4');
  await page.locator('#feishu-personality-tags').fill('直接, 数据驱动');
  await page.locator('#feishu-msg-limit').fill('800');
  await page.locator('#generate-colleague-btn').click();

  await expect.poll(() => calls.feishu).toBe(1);
  expect(calls.lastFeishuPayload).toMatchObject({
    app_id: 'cli_test',
    app_secret: 'secret-test',
    target_name: '张三',
    role: '后端工程师',
    company: 'Clawcross',
    level: 'L4',
    auto_distill: true,
    auto_import: true,
    msg_limit: 800,
  });

  await expect(page.locator('#builder-step-roles')).toBeVisible();
  await expect(page.locator('#builder-roles-list .builder-role-name').first()).toHaveValue('张三');
  await expect(page.locator('#builder-status-text')).toContainText('张三');
  expect(pageErrors).toEqual([]);
});

test('team creator dag uses pretext-driven sizing for long mentor names', async ({ page }) => {
  const calls = { arxiv: 0, feishu: 0, lastArxivPayload: null };
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await stubCreatorNetwork(page, calls, {
    arxivResponse: {
      ok: true,
      papers_count: 2,
      auto_imported: true,
      summary: {
        import_source: 'supervisor-mentor',
        mentor_meta: { name: 'Professor Geoffrey Everest Hinton AlphaBetaGamma' },
      },
      team_config: {
        team_name: '长名导师团队',
        oasis_experts: [
          {
            name: 'Professor Geoffrey Everest Hinton AlphaBetaGamma',
            tag: 'hinton-long',
            persona: '# Mentor\n\nThink carefully and explain tradeoffs.',
          },
        ],
      },
    },
  });

  await page.addInitScript(() => {
    window.alert = () => {};
    window.confirm = () => true;
    localStorage.setItem('lang', 'zh-CN');
    localStorage.setItem('clawcross_lang', 'zh');
  });

  await page.goto('/creator');
  await page.locator('#builder-mode-import-mentor').click();
  await page.locator('#mentor-arxiv-name').fill('Professor Geoffrey Everest Hinton AlphaBetaGamma');
  await page.locator('#generate-mentor-btn').click();

  await expect.poll(() => calls.arxiv).toBe(1);

  const result = await page.evaluate(() => {
    const metrics = window.__ClawcrossCreatorBuilder && typeof window.__ClawcrossCreatorBuilder.getDagNodeMetrics === 'function'
      ? window.__ClawcrossCreatorBuilder.getDagNodeMetrics({
          name: 'Professor Geoffrey Everest Hinton AlphaBetaGamma',
          tag: 'hinton-long',
        })
      : null;
    return {
      ready: Boolean(window.ClawcrossTextLayout && typeof window.ClawcrossTextLayout.measureDisplay === 'function'),
      width: metrics ? metrics.w : 0,
      nameText: metrics ? metrics.displayNameText : '',
    };
  });

  expect(result.ready).toBeTruthy();
  expect(result.width).toBeGreaterThan(174);
  expect(result.nameText.length).toBeGreaterThan(10);
  expect(pageErrors).toEqual([]);
});
