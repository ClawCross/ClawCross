const { test, expect } = require('@playwright/test');
test.use({ launchOptions: { executablePath: process.env.CLAWCROSS_TEST_CHROME || '/usr/bin/google-chrome' } });

// The mobile message center speaks to agents by id: groups, members, @mentions and
// contacts all come from /proxy_groups and /v1/agents, the same for every platform.

const LEAD = { agent_id: 's_lead', name: 'Lead', platform: 'webot',
  settings: { persona: 'synthesis', team: 'dev', tools: null } };
const CODEX = { agent_id: 'ag_codex00001', name: 'Codex', platform: 'codex',
  settings: { persona: 'critical', team: 'dev', global_name: '', has_api_key: false, model: '', api_url: '' } };

const MESSAGES = [
  { id: 1, sender: 'u:tester', sender_name: 'tester', content: '@Codex 看一下', mentions: [CODEX.agent_id], attachments: [], created_at: 1790000000 },
  { id: 2, sender: CODEX.agent_id, sender_name: 'Codex', content: '好的，已完成', mentions: [], attachments: [], created_at: 1790000010 },
];

const GROUP = {
  group_id: 'g_dev', title: 'Dev', kind: 'group', team: 'dev', owner: 'tester', member_count: 3,
  member_names: ['Lead', 'Codex', 'tester'], message_count: 2, last_message: MESSAGES[1], dnd: false, updated_at: 1790000010,
};

function member(agent) {
  return { principal: agent.agent_id, name: agent.name, is_agent: true, agent, muted: false, nickname: '' };
}

async function stub(page, calls) {
  const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  for (const [pattern, body] of [
    [/cdnjs\.cloudflare\.com\/.*marked.*\.js/, 'window.marked = { parse: (s) => String(s || ""), setOptions() {} };'],
    [/cdnjs\.cloudflare\.com\/.*highlight.*\.js/, 'window.hljs = { highlightAll() {}, highlightElement() {} };'],
    [/cdnjs\.cloudflare\.com\/.*jszip.*\.js/, 'window.JSZip = function JSZip() {};'],
  ]) {
    await page.route(pattern, (route) => route.fulfill({ contentType: 'application/javascript', body }));
  }
  await page.route('**/proxy_check_session', (route) => json(route, { valid: true, user_id: 'tester', has_password: true, mode: 'local' }));
  await page.route('**/api/llm_config_status', (route) => json(route, { configured: true }));
  await page.route('**/teams', (route) => json(route, { teams: ['dev'] }));
  await page.route(/\/proxy_visual\/experts/, (route) => json(route, [
    { name: '创意专家', tag: 'creative', persona: 'creative persona', source: 'public', emoji: '🎨' },
  ]));
  await page.route(/\/proxy_webot_tool_approvals/, (route) => json(route, { approvals: [] }));
  await page.route(/\/proxy_visual\/load-layouts/, (route) => json(route, []));
  await page.route(/\/v1\/teams(\?.*)?$/, (route) => json(route, { object: 'list', data: [
    { team: 'dev', lead: LEAD.agent_id,
      members: [{ agent: LEAD, role: 'Lead', is_lead: true }, { agent: CODEX, role: 'Codex', is_lead: false }] },
  ] }));
  await page.route(/\/v1\/agents(\?.*)?$/, (route) => json(route, { object: 'list', data: [LEAD, CODEX] }));
  await page.route(/\/v1\/agents\/[^/]+\/control$/, (route) => {
    calls.control.push(route.request().postDataJSON());
    return json(route, { agent: CODEX, actions: ['status', 'cancel', 'reset'], state: 'idle' });
  });
  await page.route(/\/v1\/agents\/ag_codex00001$/, (route) => json(route, { ...CODEX, groups: [{group_id: 'g_dev', title: 'Dev'}], status: { state: 'idle', actions: ['status'] } }));
  await page.route(/\/proxy_groups(\?.*)?$/, (route) => json(route, { groups: [GROUP] }));
  await page.route(/\/proxy_groups\/g_dev\/messages/, (route) => {
    if (route.request().method() === 'POST') {
      calls.posts.push(route.request().postDataJSON());
      return json(route, { message: { ...MESSAGES[0], id: 3 }, created: true });
    }
    return json(route, { messages: [] });
  });
  await page.route(/\/proxy_groups\/g_dev\/typing/, (route) => json(route, { typing: [], names: [] }));
  await page.route(/\/proxy_groups\/g_dev$/, (route) => json(route, {
    ...GROUP, primary_agent: LEAD.agent_id,
    members: [{ principal: 'u:tester', name: 'tester', is_agent: false, agent: null, muted: false, nickname: '' }, member(LEAD), member(CODEX)],
    messages: MESSAGES,
  }));
}

test('mobile message center works with agents of any platform by id', async ({ page }) => {
  const calls = { posts: [], control: [] };
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await stub(page, calls);
  await page.addInitScript(() => {
    window.alert = () => {};
    window.confirm = () => true;
    localStorage.setItem('clawcross_lang', 'zh');
  });

  await page.goto('/mobile/group_chat');
  await expect(page.locator('#group-list-container')).toContainText('Dev');
  await expect(page.locator('#group-list-container')).toContainText('好的，已完成');  // last message preview

  await page.locator('.group-item', { hasText: 'Dev' }).first().click();
  await expect(page.locator('#chat-body')).toContainText('好的，已完成');
  expect(await page.locator('.msg-sender').allTextContents()).toEqual(['tester', 'Codex']);
  await expect(page.locator('.msg-row.self')).toHaveCount(1);

  // Members: one kind of row for every platform, the same controls
  await expect(page.locator('#member-list')).toContainText('Codex');
  await expect(page.locator('#member-list')).toContainText('ag_codex00001');
  await expect(page.locator('#member-list')).toContainText('WeBot');
  await page.evaluate(() => controlMemberAgent('ag_codex00001', 'reset', 'Codex'));
  expect(calls.control).toEqual([{ action: 'reset' }]);
  await page.evaluate(() => showAgentDetail('ag_codex00001'));
  await expect(page.locator('#agent-detail-body')).toContainText('Dev (g_dev)');
  await page.evaluate(() => closeAgentDetail());

  // An @ picked from the list is sent as the agent's id
  await page.locator('#msg-input').fill('@Co');
  await page.locator('#msg-input').dispatchEvent('input');
  await expect(page.locator('#mention-popup-body')).toContainText('Codex');
  await page.evaluate(() => insertSelectedMention());
  await page.locator('#msg-input').type('请复查');
  await page.evaluate(() => sendMessage());
  await expect.poll(() => calls.posts.length).toBe(1);
  expect(calls.posts[0].content).toBe('@Codex 请复查');
  expect(calls.posts[0].mentions).toEqual(['ag_codex00001']);

  // Contacts: agents grouped by platform, then personas
  await page.evaluate(() => goBack());
  await page.evaluate(() => switchTab('contacts'));
  await expect(page.locator('#contacts-list')).toContainText('WeBot');
  await expect(page.locator('#contacts-list')).toContainText('Codex');
  await expect(page.locator('#contacts-list')).toContainText('创意专家');
  await page.locator('#contacts-list .contact-item', { hasText: 'Codex' }).first().click();
  await expect(page.locator('#agent-detail-body')).toContainText('ag_codex00001');

  expect(pageErrors).toEqual([]);
});

test('remote group join and sharing stay usable on a narrow phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const calls = { posts: [], control: [] };
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await stub(page, calls);
  await page.addInitScript(() => { window.alert = () => {}; window.confirm = () => true; localStorage.setItem('clawcross_lang', 'zh'); });
  const remote = { ...CODEX, agent_id: 'p_remote', name: 'Remote friend', remote: true };
  const group = { ...GROUP, group_id: 'rg_network', federated: true, local_join: true, owner: 'tester',
    members: [{ principal: 'u:tester', name: 'tester', is_agent: false, muted: false },
      { ...member(remote), remote: true, user_id: 'bob', node_id: 'another-device' }], messages: [] };
  let joined = false;
  const joins = [], sharing = [];
  await page.route(/\/proxy_groups(\?.*)?$/, route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ groups: joined ? [GROUP, group] : [GROUP] }) }));
  await page.route('**/proxy_groups/join', route => {
    joins.push(route.request().postDataJSON()); joined = true;
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(group) });
  });
  await page.route('**/proxy_groups/rg_network', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(group) }));
  await page.route('**/proxy_groups/rg_network/messages*', route => route.fulfill({ contentType: 'application/json', body: '{"messages":[]}' }));
  await page.route('**/proxy_groups/rg_network/typing', route => route.fulfill({ contentType: 'application/json', body: '{"typing":[],"names":[]}' }));
  await page.route('**/proxy_groups/rg_network/invite', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ server_url: 'https://friends.example', group_id: 'g_remote', password_enabled: true }) }));
  await page.route('**/proxy_groups/rg_network/sharing', route => {
    sharing.push(route.request().postDataJSON());
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(group) });
  });
  await page.goto('/mobile/group_chat');
  await page.evaluate(() => GroupNetworkUI.join());
  const dialog = page.locator('.group-network-dialog');
  await dialog.locator('input[name=server]').fill('https://friends.example');
  await dialog.locator('input[name=group]').fill('g_remote');
  await dialog.locator('input[name=password]').fill('secret');
  const bounds = await dialog.boundingBox();
  expect(bounds.x).toBeGreaterThanOrEqual(0); expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
  await dialog.locator('button[type=submit]').click();
  await expect(dialog).toHaveCount(0);
  expect(joins).toEqual([{ server_url: 'https://friends.example', group_id: 'g_remote', password: 'secret', agents: [] }]);
  await expect(page.locator('#member-list')).toContainText('Remote friend');
  await expect(page.locator('#member-list')).not.toContainText('取消任务');
  await page.evaluate(() => showAgentDetail('p_remote'));
  await expect(page.locator('#agent-detail-body')).toContainText('所属设备管理');
  await expect(page.locator('#agent-detail-body')).not.toContainText('删除 Agent');
  await page.evaluate(() => closeAgentDetail());
  await page.evaluate(() => GroupNetworkUI.sharing('rg_network'));
  await expect(dialog.locator('input[name=group]')).toHaveValue('g_remote');
  await dialog.locator('input[name=password]').fill('rotated');
  await dialog.locator('input[name=revoke]').check();
  await dialog.locator('button[type=submit]').click();
  await expect(dialog).toHaveCount(0);
  expect(sharing).toEqual([{ password: 'rotated', local_join: true, revoke_connections: true }]);
  expect(errors).toEqual([]);
});
