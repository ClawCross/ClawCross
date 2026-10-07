const { test, expect } = require('@playwright/test');
const creation=require('./agent-creation-fixture');

test('contact creation offers group roles and keeps capabilities in advanced settings',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await stub(page,{posts:[],control:[]});
  await page.route('**/proxy_acpx_status',route=>route.fulfill({json:{tools:['codex']}}));
  await page.route('**/proxy_tools',route=>route.fulfill({json:{tools:creation.tools}}));
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/mobile/group_chat');
  await page.evaluate(async()=>{
    window.registerAgent=async fields=>{window.__createdFields=fields;return {agent_id:'group-role-agent'};};
    window.startPrivateChat=async()=>true;
    await showCreateAgentModal('','group');
  });
  const roles=page.locator('#mobile-group-role-picker');
  await expect(roles.locator('[data-group-role]')).toHaveCount(3);
  await expect(page.locator('#mobile-agent-capability-presets')).not.toHaveAttribute('open','');
  await roles.locator('[data-group-role="advisor"]').click();
  await expect.poll(()=>page.evaluate(()=>AgentCreationPresets.selected('mobile-agent-creation-presets')?.id)).toBe('group');
  expect(await page.evaluate(()=>window.__createdFields)).toBeUndefined();
  await page.evaluate(()=>setCreateAgentMode('acp'));
  await expect(page.locator('#ca-acp-name')).toBeVisible();
  await expect(page.locator('#ca-acp-submit')).toBeEnabled();
  expect(await page.evaluate(()=>AgentCreationPresets.selected('mobile-agent-creation-presets').id)).toBe('personal');
  await page.evaluate(()=>setCreateAgentMode('webot'));
  await expect.poll(()=>page.evaluate(()=>AgentCreationPresets.selected('mobile-agent-creation-presets').id)).toBe('group');
  await page.locator('#mobile-agent-capability-presets > summary').click();
  await page.locator('#mobile-agent-creation-presets [data-preset="admin"]').click();
  expect(await page.evaluate(()=>AgentCreationPresets.selected('mobile-agent-creation-presets').id)).toBe('admin');
  await page.locator('#mobile-agent-creation-presets [data-preset="group"]').click();
  await page.locator('#mobile-agent-capability-presets > summary').click();
  await page.locator('#create-agent-name').fill('科学顾问');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:'/tmp/clawcross-contact-group-roles.png'});
  await page.locator('#create-agent-submit').click();
  const created=await page.evaluate(()=>window.__createdFields);
  expect(created).toMatchObject({name:'科学顾问',platform:'webot',creation_template:'group',tools:['send_to_group']});
  expect(created.persona).toContain('专业顾问');
  expect(errors).toEqual([]);
});
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
  const modes=new Map();
  calls.runtimeSettings ||= [];
  await page.route('**/proxy_webot_runtime_settings**',route=>{
    const request=route.request(),body=request.method()==='POST'?request.postDataJSON():null;
    const id=body?.session_id || new URL(request.url()).searchParams.get('session_id');
    if(body?.settings?.approval?.mode){modes.set(id,body.settings.approval.mode);calls.runtimeSettings.push(body);}
    const mode=modes.get(id) || 'auto';
    return json(route,{settings:{approval:{mode,command_sandbox:'off'}},effective_mode:mode});
  });
  await page.route('**/v1/agents/creation-templates',route=>json(route,{data:creation.templates}));
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
  await expect(page.locator('#fleet-section')).toBeHidden();
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

test('mobile saves five distinct permission modes per Agent and keeps Manual separate from Bypass', async ({ page }) => {
  const calls = { posts: [], control: [] };
  await stub(page, calls);
  await page.addInitScript(() => {
    window.alert = () => {};
    localStorage.setItem('clawcross_lang', 'zh');
  });
  await page.goto('/mobile/group_chat');
  await page.locator('.group-item', { hasText: 'Dev' }).first().click();
  const target=await page.evaluate(()=>getMobileRuntimeAgentId());
  expect(target).toBe(LEAD.agent_id);
  for (const mode of ['chat', 'readonly', 'manual', 'auto', 'bypass']) {
    await page.evaluate(() => openRunModeSheet());
    await page.locator(`#run-mode-sheet [data-mode="${mode}"]`).click();
    await expect.poll(()=>page.evaluate(() => getRunMode())).toBe(mode);
    await page.evaluate(() => closeRunModeSheet());
    await page.locator('#msg-input').fill(`测试模式 ${mode}`);
    await page.locator('#send-btn').click();
    await expect.poll(() => calls.posts.length).toBe(['chat', 'readonly', 'manual', 'auto', 'bypass'].indexOf(mode) + 1);
    expect(calls.runtimeSettings.at(-1)).toEqual({session_id:target,settings:{approval:{mode}}});
    expect(calls.posts.at(-1)).not.toHaveProperty('run_mode');
  }
});

test('remote group join and sharing stay usable on a narrow phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const calls = { posts: [], control: [] };
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await stub(page, calls);
  await page.route('**/proxy_tunnel/status',route=>route.fulfill({json:{running:false,public_domain:'https://me.example'}}));
  await page.addInitScript(() => { window.alert = () => {}; window.confirm = () => true; localStorage.setItem('clawcross_lang', 'zh'); });
  const remote = { ...CODEX, agent_id: 'p_remote', name: 'Remote friend', remote: true };
  const group = { ...GROUP, group_id: 'rg_network', federated: true, owner: 'tester',
    members: [{ principal: 'u:tester', name: 'tester', is_agent: false, muted: false },
      { ...member(remote), remote: true, user_id: 'bob', node_id: 'another-device' }], messages: [] };
  let joined = false;
  const joins = [], links = [];
  await page.route(/\/proxy_groups(\?.*)?$/, route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ groups: joined ? [GROUP, group] : [GROUP] }) }));
  await page.route('**/proxy_groups/join', route => {
    joins.push(route.request().postDataJSON()); joined = true;
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(group) });
  });
  await page.route('**/proxy_groups/rg_network', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(group) }));
  await page.route('**/proxy_groups/rg_network/messages*', route => route.fulfill({ contentType: 'application/json', body: '{"messages":[]}' }));
  await page.route('**/proxy_groups/rg_network/typing', route => route.fulfill({ contentType: 'application/json', body: '{"typing":[],"names":[]}' }));
  await page.route('**/proxy_groups/rg_network/guest-link', route => {
    links.push(route.request().postDataJSON());
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ url: 'https://me.example/group-guest#new-ticket' }) });
  });
  await page.goto('/mobile/group_chat');
  await page.evaluate(() => GroupNetworkUI.join());
  const dialog = page.locator('.group-network-dialog');
  await expect(dialog.locator('input, textarea')).toHaveCount(1);  // only the link: no address or password
  await dialog.locator('textarea').fill('https://friends.example/group-guest#ticket');
  const bounds = await dialog.boundingBox();
  expect(bounds.x).toBeGreaterThanOrEqual(0); expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
  await dialog.getByRole('button', { name: '加入' }).click();
  await expect(dialog).toHaveCount(0);
  expect(joins).toEqual([{ invite: 'https://friends.example/group-guest#ticket', agents: [] }]);
  await expect(page.locator('#member-list')).toContainText('Remote friend');
  await expect(page.locator('#member-list')).not.toContainText('取消任务');
  await page.evaluate(() => showAgentDetail('p_remote'));
  await expect(page.locator('#agent-detail-body')).toContainText('所属设备管理');
  await expect(page.locator('#agent-detail-body')).not.toContainText('删除 Agent');
  await page.evaluate(() => closeAgentDetail());
  await page.evaluate(() => GroupNetworkUI.sharing('rg_network'));
  await expect(dialog.locator('input[type=password]')).toHaveCount(0);
  await dialog.getByRole('button', { name: '生成邀请链接' }).click();
  await expect(dialog.locator('.gn-link')).toHaveValue('https://me.example/group-guest#new-ticket');
  await expect(dialog.getByRole('button', { name: '复制链接' })).toBeVisible();
  expect(links).toEqual([{}]);
  expect(errors).toEqual([]);
});

test('external agent accepts a Chinese name without a manual runtime id and offers explicit install', async ({ page }) => {
  await page.setViewportSize({width:390,height:844});
  await stub(page, {posts:[],control:[]});
  const installs = [], created = [];
  await page.route('**/proxy_components/acpx', route => {
    if (route.request().method() === 'POST') installs.push('acpx');
    return route.fulfill({contentType:'application/json',body:JSON.stringify({name:'acpx',installed:installs.length > 0,ready:installs.length > 0,missing:[],can_install:true,state:installs.length ? 'complete' : '',platform:'linux'})});
  });
  await page.addInitScript(() => { localStorage.setItem('clawcross_lang','zh'); });
  await page.goto('/mobile/group_chat');
  await page.evaluate(async () => {
    window.registerAgent = async fields => { window.__createdFields = fields; return {agent_id:'ag_generated'}; };
    window.startPrivateChat = async () => true;
    setCreateAgentMode('acp');
    await renderCreateAgentAcpPanel();
    document.getElementById('create-agent-modal').classList.add('show');
  });
  await expect(page.locator('[data-component-status]')).toContainText('尚未安装');
  expect(installs).toEqual([]);
  await page.locator('[data-component-install]').click();
  await expect(page.locator('[data-component-status]')).toContainText('已安装');
  expect(installs).toEqual(['acpx']);
  await page.locator('#ca-acp-platform').selectOption('codex');
  await page.locator('#ca-acp-name').fill('中文助手');
  await expect(page.locator('#ca-acp-runtime')).toHaveValue('');
  await page.evaluate(() => mobileSubmitCreateAcpAgent());
  expect(await page.evaluate(() => window.__createdFields)).toEqual({name:'中文助手',platform:'codex',creation_template:'personal',tools:null});
});


test('group owner can remove a human guest; other members have no human removal controls', async ({page})=>{
  await stub(page,{posts:[],control:[]});
  const members=[{principal:'u:tester',name:'tester',is_agent:false,is_owner:true,can_remove:false},
    {principal:'p_bob',name:'Bob',is_agent:false,remote:true,can_remove:true}];
  await page.route(/\/proxy_groups\/g_dev$/,route=>route.fulfill({json:{...GROUP,federated:true,members}}));
  const removed=[];
  await page.route('**/proxy_groups/g_dev/members/p_bob',route=>{
    expect(route.request().method()).toBe('DELETE');removed.push('Bob');members.pop();
    return route.fulfill({json:{...GROUP,federated:true,members}});
  });
  await page.addInitScript(()=>{window.confirm=()=>true;localStorage.setItem('clawcross_lang','zh');});
  await page.goto('/mobile/group_chat');await page.locator('.group-item',{hasText:'Dev'}).first().click();
  await page.evaluate(()=>toggleDrawer());
  await page.getByRole('button',{name:'移除 Bob',exact:true}).click();
  await expect(page.locator('#member-list')).not.toContainText('Bob');
  expect(removed).toEqual(['Bob']);
  await page.evaluate(()=>renderMembers([{principal:'p_bob',name:'Bob',is_agent:false,remote:true,can_remove:false}]));
  await expect(page.getByRole('button',{name:'移除 Bob',exact:true})).toHaveCount(0);
});

test('mobile accepts Y/N as scoped approval controls for external agents without a group message', async ({page}) => {
  const calls={posts:[],control:[]};
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await stub(page,calls);
  await page.addInitScript(()=>localStorage.setItem('clawcross_lang','zh'));
  const decisions=[];
  let pending=true;
  const item={approval_id:'approval-mobile123',session_id:CODEX.agent_id,tool_name:'write_file',status:'pending',
    request_reason:'确认写入',args:{filename:'hello.txt'},review:{reviewer:'user',conversation_reply:true}};
  await page.route('**/proxy_webot_tool_approvals?*',route=>route.fulfill({json:{approvals:pending?[item]:[]}}));
  await page.route('**/proxy_webot_tool_approval_resolve',route=>{
    decisions.push(route.request().postDataJSON());pending=false;
    return route.fulfill({json:{status:'success',continuation:'queued',approval:{tool_name:'write_file',status:'denied'}}});
  });
  await page.goto('/mobile/group_chat');
  await page.locator('.group-item',{hasText:'Dev'}).first().click();
  await page.evaluate(()=>refreshPendingApprovalsForCurrentGroup());
  await expect(page.locator('#approval-strip')).not.toBeVisible();
  await expect(page.locator('#chat-body .cc-approval-actions button')).toHaveCount(3);
  await page.locator('#msg-input').fill('N');
  await page.evaluate(()=>sendMessage());
  expect(decisions).toEqual([{approval_id:item.approval_id,action:'deny',remember:false,session_id:CODEX.agent_id}]);
  expect(calls.posts).toEqual([]);
  await expect(page.locator('#msg-input')).toHaveValue('');
  await expect(page.locator('#approval-strip')).not.toBeVisible();
  await expect(page.locator('.msg-row.self').last()).toContainText('拒绝这次操作');
  await expect(page.locator('.cc-approval-actions')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('contact creation preserves drafts and offers persona and scoped ClawCross tools', async ({page})=>{
  await page.setViewportSize({width:390,height:844});
  await stub(page,{posts:[],control:[]});
  await page.route('**/proxy_acpx_status',route=>route.fulfill({json:{tools:['codex','claude','gemini','qwen']}}));
  await page.route('**/proxy_tools',route=>route.fulfill({json:{tools:creation.tools}}));
  await page.addInitScript(()=>{localStorage.setItem('clawcross_lang','zh');});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/mobile/group_chat');
  await page.evaluate(async()=>{
    window.registerAgent=async fields=>{window.__createdFields=fields;return {agent_id:'ag_new'};};
    window.startPrivateChat=async()=>true;
    await showCreateAgentModal();setCreateAgentMode('acp');
  });
  await expect(page.locator('#ca-acp-name')).toBeVisible();
  await page.locator('#ca-acp-platform').selectOption('qwen');
  await page.locator('#ca-acp-name').fill('Research');
  await page.locator('#create-agent-panel-acp .create-agent-advanced > summary').click();
  await page.locator('#ca-acp-persona').fill('Help me research');
  await page.locator('#ca-acp-tools-picker [data-tool-all]').uncheck();
  await expect(page.locator('#ca-acp-tools-picker .tool-tag.enabled')).toHaveCount(2);
  await page.locator('#ca-acp-tools-picker [data-select-all]').click();
  await expect(page.locator('#ca-acp-tools-picker .tool-tag.enabled')).toHaveCount(3);
  await page.locator('#ca-acp-tools-picker .tool-tag',{hasText:'manage_team'}).click();
  await expect(page.locator('#ca-acp-tools-picker .tool-tag.enabled')).toHaveCount(2);
  await page.evaluate(()=>{setCreateAgentMode('webot');setCreateAgentMode('acp');});
  await expect(page.locator('#ca-acp-name')).toHaveValue('Research');
  await expect(page.locator('#ca-acp-persona')).toHaveValue('Help me research');
  expect(await page.evaluate(()=>window.__createdFields)).toBeUndefined();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:'/tmp/clawcross-mobile-agent-create.png'});
  await page.locator('#ca-acp-submit').click();
  expect(await page.evaluate(()=>window.__createdFields)).toEqual({name:'Research',platform:'qwen',persona:'Help me research',tools:['read_file','send_to_group'],meta:{acp:{clawcross_tools:true,tools:['read_file','send_to_group']}},creation_template:'personal'});
  expect(errors).toEqual([]);
});

test('small-screen Agent templates prepare safely and chat submits an explicit empty tool set',async({page})=>{
  await page.setViewportSize({width:320,height:600});
  await stub(page,{posts:[],control:[]});
  await page.route('**/proxy_acpx_status',route=>route.fulfill({json:{tools:['codex']}}));
  await page.route('**/proxy_tools',route=>route.fulfill({json:{tools:creation.tools}}));
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/mobile/group_chat');
  await page.evaluate(async()=>{
    window.registerAgent=async fields=>{window.__createdFields=fields;return {agent_id:'ag_chat'};};
    window.startPrivateChat=async()=>true;
    await showCreateAgentModal();
  });
  const choices=page.locator('#mobile-agent-creation-presets');
  await expect(choices.locator('button')).toHaveCount(4);
  await choices.locator('[data-preset="chat"]').click();
  await page.evaluate(()=>setCreateAgentMode('acp'));
  await expect(page.locator('#ca-acp-name')).toBeVisible();
  await expect(page.locator('#ca-acp-submit')).toBeDisabled();
  await expect(page.locator('#mobile-agent-template-hint')).toContainText('需要 WeBot');
  await choices.locator('[data-preset="admin"]').click();
  await expect(page.locator('#ca-acp-submit')).toBeEnabled();
  expect(await page.evaluate(()=>window.__createdFields)).toBeUndefined();
  await page.evaluate(()=>setCreateAgentMode('webot'));
  await choices.locator('[data-preset="chat"]').click();
  await page.locator('#create-agent-name').fill('Chat companion');
  expect(await page.evaluate(()=>MobileAgentCreationTools.value('create-agent'))).toEqual([]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const footer=await page.locator('#create-agent-submit').boundingBox();
  expect(footer.y+footer.height).toBeLessThanOrEqual(600);
  await page.screenshot({path:'/tmp/clawcross-mobile-agent-templates.png'});
  await page.locator('#create-agent-submit').click();
  expect(await page.evaluate(()=>window.__createdFields)).toEqual({name:'Chat companion',platform:'webot',tools:[],creation_template:'chat'});
  expect(errors).toEqual([]);
});
