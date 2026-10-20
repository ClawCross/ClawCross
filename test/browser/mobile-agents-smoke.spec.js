const { test, expect } = require('@playwright/test');
const creation=require('./agent-creation-fixture');

test('partner profiles keep common actions simple, load skills on demand and keep remote agents read-only',async({page})=>{
  await stub(page,{posts:[],control:[]});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/v1/agents/s_lead',route=>route.fulfill({json:{...LEAD,name:'负责跨设备协作的伙伴',status:{state:'running'},settings:{...LEAD.settings,llm:{model:'test-model'},persona:'认真负责的协作伙伴。'.repeat(30)},groups:[{group_id:'g_dev',title:'Dev'}]}}));
  let skillCalls=0;
  await page.route('**/v1/agents/s_lead/skills',route=>{skillCalls++;return route.fulfill({json:{skills:[{name:'Research',source:'companion',description:'查阅资料'},{name:'Review',source:'team',team_name:'dev'}]}});});
  await page.goto('/mobile/group_chat');await page.evaluate(()=>showAgentDetail('s_lead'));
  await expect(page.locator('.ap-hero')).toContainText('负责跨设备协作的伙伴');
  await expect(page.locator('.ap-presence')).toContainText('正在工作');
  await expect(page.locator('.ap-facts')).toContainText('test-model');expect(skillCalls).toBe(0);
  await page.locator('.ap-skills > summary').click();await expect(page.locator('.ap-skill')).toHaveCount(2);
  await expect(page.locator('.ap-skills')).toContainText('专属');await expect(page.locator('.ap-skills')).toContainText('团队 · dev');
  for(const width of [320,390,1360]){await page.setViewportSize({width,height:844});await page.locator('#agent-detail-body').evaluate(el=>el.scrollTop=0);await expect(page.locator('.ap-primary')).toBeInViewport();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:`/tmp/clawcross-agent-profile-${width}.png`});}
  await page.evaluate(()=>{currentMembers=[{principal:'remote-principal',name:'远方伙伴',remote:true,is_agent:true,user_id:'Bob',node_id:'Device-B',online:true,agent:{platform:'codex',agent_id:'remote-principal'}}];return showAgentDetail('remote-principal');});
  await expect(page.locator('.ap-hero')).toContainText('远方伙伴');await expect(page.locator('.ap-facts')).toContainText('Device-B');
  await expect(page.locator('.ap-primary-actions,.ap-advanced,.ap-danger')).toHaveCount(0);expect(errors).toEqual([]);
});

test('Contacts use a compact team filter and keep names readable near the top',async({page})=>{
  const calls={posts:[],control:[]},errors=[];
  await stub(page,calls);
  const longName='负责复杂任务与跨设备协作的长期伙伴';
  const agents=[{...LEAD,name:longName},CODEX];
  await page.route(/\/v1\/agents(\?.*)?$/,route=>route.fulfill({json:{data:agents}}));
  await page.route('**/v1/teams/dev',route=>route.fulfill({json:{team:'dev',members:[{agent:agents[0],role:longName}]}}));
  page.on('pageerror',error=>errors.push(error.message));
  await page.setViewportSize({width:390,height:844});
  await page.goto('/mobile/group_chat');
  await page.evaluate(()=>switchTab('contacts'));
  await expect(page.locator('#contacts-scope')).toHaveAttribute('data-user-space','true');
  await expect(page.locator('#contacts-team-select option:checked')).toContainText('用户空间');
  await expect(page.locator('#tab-contacts .contacts-intro, #tab-contacts .contacts-actions, #contacts-import-panel')).toHaveCount(0);
  const name=page.locator('#contacts-list .contact-name').filter({hasText:longName});
  await expect(name).toBeVisible();
  expect(await name.evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBe(true);
  await page.screenshot({path:'/tmp/clawcross-contacts-personal-phone.png'});
  await page.locator('#contacts-team-select').selectOption('dev');
  await expect(page.locator('#contacts-scope')).toHaveAttribute('data-user-space','false');
  await expect(page.locator('#contacts-team-info')).toBeVisible();
  await expect(name).toBeVisible();
  await expect(page.locator('#contacts-list [data-agent-activity="ag_codex00001"]')).toHaveCount(0);
  await page.locator('#contacts-search-input').fill('codex');
  await expect(page.locator('#contacts-list .contacts-empty')).toBeVisible();
  await page.locator('#contacts-search-input').fill(longName);
  await expect(name).toBeVisible();
  for(const width of [390,360,1440]){
    await page.setViewportSize({width,height:560});
    await expect(name).toBeInViewport({ratio:1});
    if(width<=600) expect((await name.boundingBox()).y).toBeLessThan(320);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:`/tmp/clawcross-contacts-${width}.png`});
  }
  await page.locator('#contacts-team-select').selectOption('');
  await expect(page.locator('#contacts-team-info')).toBeHidden();
  await page.setViewportSize({width:390,height:844});
  await page.locator('#contacts-list [data-agent-activity="ag_codex00001"]').click();
  await expect(page.locator('#agent-detail-overlay')).toHaveClass(/open/);
  expect(errors).toEqual([]);
});

test('Contacts category labels scroll with long lists without covering contacts',async({page})=>{
  await stub(page,{posts:[],control:[]});
  const agents=Array.from({length:20},(_,index)=>({...LEAD,agent_id:`scroll-agent-${index}`,name:`伙伴 ${index+1}`}));
  await page.route(/\/v1\/agents(\?.*)?$/,route=>route.fulfill({json:{data:[...agents,CODEX]}}));
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/mobile/group_chat');
  await page.evaluate(()=>switchTab('contacts'));
  await expect(page.locator('#contacts-list .contact-item')).toHaveCount(22);
  for(const width of [390,360,1440]){
    await page.setViewportSize({width,height:560});
    const geometry=await page.evaluate(()=>{
      const list=document.getElementById('contacts-area');
      list.scrollTop=0;
      const labels=[...document.querySelectorAll('#contacts-list .contact-section-title, #contacts-list .contacts-cat-header')];
      const before=labels.map(label=>label.getBoundingClientRect().top);
      list.scrollTop=300;
      return {scroll:list.scrollTop,labels:labels.map((label,index)=>({
        position:getComputedStyle(label).position,
        moved:before[index]-label.getBoundingClientRect().top,
      }))};
    });
    expect(geometry.scroll).toBe(300);
    for(const label of geometry.labels){
      expect(label.position).toBe('static');
      expect(label.moved).toBeCloseTo(geometry.scroll,0);
    }
    await page.screenshot({path:`/tmp/clawcross-contacts-scroll-${width}.png`});
  }
  expect(errors).toEqual([]);
});

test('workflow run cards fit phones and retain their existing controls',async({page})=>{
  const calls={posts:[],control:[]},errors=[];
  await stub(page,calls);
  const topics=[{topic_id:'workflow-one',question:'整理资料并完成团队的周报，保留所有参考与交付结果',status:'discussing',post_count:4,current_round:2,max_rounds:5,created_at:1790000000},
    {topic_id:'workflow-two',question:'已完成的资料审阅',status:'concluded',post_count:6,current_round:3,max_rounds:3,created_at:1789999990}];
  await page.route('**/proxy_oasis/topics',route=>route.fulfill({json:topics}));
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/mobile/group_chat');
  await page.evaluate(()=>openOasisSidebar());
  await expect(page.locator('#oasis-topic-list .oasis-topic-item')).toHaveCount(2);
  for(const width of [390,1440]){
    await page.setViewportSize({width,height:844});
    await expect(page.locator('#oasis-launch-wf-btn')).toBeInViewport();
    await expect(page.locator('.oasis-topic-item').first().locator('.oasis-topic-question')).toBeVisible();
    const box=await page.locator('#oasis-sidebar').boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x+box.width).toBeLessThanOrEqual(width+1);
    await page.screenshot({path:`/tmp/clawcross-workflow-runs-${width}.png`});
  }
  expect(errors).toEqual([]);
});

test('Agent details and subagent pages stay inside the desktop conversation column', async ({page}) => {
  await page.setViewportSize({width:1360,height:900});
  await stub(page,{posts:[],control:[]});
  await page.goto('/mobile/group_chat');
  await page.evaluate(()=>openSubagentPanel());
  await expect(page.locator('#subagent-panel-overlay')).toHaveClass(/open/);
  const check = async selector => {
    const geometry = await page.evaluate(selector=>{
      const shell=document.querySelector('.app-container');
      const box=shell.getBoundingClientRect(),cols=getComputedStyle(shell).gridTemplateColumns.split(' ').map(parseFloat);
      const panel=document.querySelector(selector).getBoundingClientRect();
      return {expectedLeft:box.left+cols[0]+cols[1],expectedRight:box.right,expectedTop:box.top,
        left:panel.left,right:panel.right,top:panel.top};
    },selector);
    expect(geometry.left).toBeCloseTo(geometry.expectedLeft,0);
    expect(geometry.right).toBeCloseTo(geometry.expectedRight,0);
    expect(geometry.top).toBeCloseTo(geometry.expectedTop,0);
  };
  await page.waitForFunction(()=>getComputedStyle(document.getElementById('subagent-panel-overlay')).transform==='matrix(1, 0, 0, 1, 0, 0)');
  await check('#subagent-panel-overlay');
  await page.evaluate(()=>closeSubagentPanel());
  await page.evaluate(()=>showAgentDetail('ag_codex00001'));
  await page.waitForFunction(()=>getComputedStyle(document.getElementById('agent-detail-overlay')).transform==='matrix(1, 0, 0, 1, 0, 0)');
  await check('#agent-detail-overlay');
  await page.setViewportSize({width:390,height:844});
  await expect.poll(async()=>Math.round((await page.locator('#agent-detail-overlay').boundingBox()).width)).toBe(390);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

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
  await expect(page.locator('#create-agent-name')).toBeVisible();
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

test('a space with an app shows the app site sandboxed in place of the chat',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  const calls={posts:[],control:[]};await stub(page,calls);
  let card={...GROUP,federated:true,owner:'tester',app:{name:'精灵对战',online:false},members:[{principal:'u:tester',name:'tester',is_agent:false},member(LEAD)]};
  let sessions=0;
  await page.route('**/proxy_groups',route=>route.fulfill({json:{groups:[card]}}));
  await page.route(/\/proxy_groups\/g_dev$/,route=>route.fulfill({json:{...card,messages:MESSAGES}}));
  await page.route('**/proxy_groups/g_dev/messages**',route=>route.fulfill({json:{messages:MESSAGES}}));
  await page.route('**/proxy_groups/g_dev/app-session',route=>{sessions++;return route.fulfill({json:{url:'https://space.example/relay/app/site/s1/'}});});
  await page.route('https://space.example/**',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><title>精灵对战</title><p id="app">arena</p>'}));
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>{if(self.origin!=='null')localStorage.setItem('clawcross_lang','zh');});
  await page.goto('/mobile/group_chat');
  await page.locator('.group-item',{hasText:'Dev'}).first().click();
  // Offline: a notice, and the chat log and input stay hidden.
  await expect(page.locator('#room-app-surface')).toContainText('「精灵对战」暂时不在线');
  await expect(page.locator('#chat-body')).toBeHidden();
  await expect(page.locator('#msg-input')).toBeHidden();
  // Online: the member's own entrance opens in a sandboxed frame.
  card={...card,app:{name:'精灵对战',online:true}};
  await page.evaluate(card=>applyGroupDetail({...card,messages:[]}),card);
  const frame=page.locator('#room-app-surface iframe');
  await expect(frame).toHaveAttribute('sandbox','allow-scripts allow-forms allow-popups allow-modals allow-downloads');
  await expect(page.frameLocator('#room-app-surface iframe').locator('#app')).toHaveText('arena');
  expect(sessions).toBe(1);
  const before=await frame.boundingBox();
  await page.locator('#app-fullscreen-btn').click();
  await expect(page.locator('body')).toHaveClass(/app-maximized/);
  await expect.poll(()=>page.evaluate(()=>document.fullscreenElement?.id)).toBe('app-fullscreen-host');
  const full=await frame.boundingBox();expect(full.x).toBe(0);expect(full.y).toBe(0);expect(full.width).toBe(390);expect(full.height).toBe(844);
  expect((await frame.boundingBox()).height).toBeGreaterThan(before.height);
  await page.locator('#app-fullscreen-exit').click();
  await expect(page.locator('body')).not.toHaveClass(/app-maximized/);
  expect(sessions).toBe(1); // Expanding keeps the same app and its state.
  // The members, invitation and menu stay outside the app.
  await page.locator('#chat-more-btn').click();
  await expect(page.locator('#action-group-app')).toBeVisible();
  expect(errors).toEqual([]);
  await page.locator('#chat-actions-overlay').click({position:{x:8,y:8}});
  await page.locator('#app-fullscreen-btn').click();
  await expect.poll(()=>page.evaluate(()=>document.fullscreenElement?.id)).toBe('app-fullscreen-host');
  await page.evaluate(()=>goBack());
  await expect.poll(()=>page.evaluate(()=>document.fullscreenElement)).toBeNull();
  await expect(page.locator('#chat-page')).not.toHaveClass(/room-app-view/);
});

test('mobile empty chats create a first companion once and open its private conversation',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await stub(page,{posts:[],control:[]});
  await page.addInitScript(()=>localStorage.setItem('clawcross_lang','zh'));
  const created=[],privateChats=[];
  let agent=null,conversation=null,release;
  const held=new Promise(resolve=>{release=resolve;});
  await page.route(/\/v1\/agents(\?.*)?$/,async route=>{
    if(route.request().method()==='POST'){
      const body=route.request().postDataJSON();created.push(body);await held;
      agent={...body,agent_id:'first-companion',settings:{},status:{state:'idle'}};
      return route.fulfill({json:agent});
    }
    return route.fulfill({json:{data:agent?[agent]:[]}});
  });
  await page.route(/\/proxy_groups(\?.*)?$/,route=>{
    if(route.request().method()==='POST'){
      privateChats.push(route.request().postDataJSON());
      conversation={group_id:'g_first',kind:'direct',title:agent.name,owner:'tester',members:[member(agent)],messages:[]};
      return route.fulfill({json:conversation});
    }
    return route.fulfill({json:{groups:conversation?[conversation]:[]}});
  });
  await page.route('**/proxy_groups/g_first**',route=>route.fulfill({json:route.request().url().includes('/messages')?{messages:[]}:conversation}));
  await page.goto('/mobile/group_chat');
  await expect(page.locator('#first-companion-create')).toHaveText('创建我的第一个伙伴');
  expect(created).toHaveLength(0);expect(privateChats).toHaveLength(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.locator('#first-companion-create').click();
  await expect.poll(()=>created.length).toBe(1);
  await expect(page.locator('#first-companion-create')).toBeDisabled();
  await page.evaluate(()=>createFirstCompanion());
  expect(created).toHaveLength(1);
  release();
  await expect.poll(()=>privateChats.length).toBe(1);
  expect(created[0]).toMatchObject({name:'我的伙伴',platform:'webot',creation_template:'personal'});
  expect(privateChats[0]).toEqual({kind:'direct',agents:['first-companion']});
  await expect(page.locator('#chat-title')).toHaveText('我的伙伴');
  await expect(page.locator('#first-companion-create')).toHaveCount(0);
});

test('mobile search with no matching chat does not show first-companion onboarding',async({page})=>{
  await stub(page,{posts:[],control:[]});
  await page.goto('/mobile/group_chat');
  await expect(page.locator('.group-item')).toHaveCount(1);
  await page.locator('#chat-search-input').fill('no-such-conversation');
  await page.locator('#chat-search-input').dispatchEvent('input');
  await expect(page.locator('.empty-state')).toBeVisible();
  await expect(page.locator('#first-companion-create')).toHaveCount(0);
});

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
  const legacyRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (request) => {
    if (/\/proxy_(fleet_|remote_claude_sessions)/.test(request.url())) legacyRequests.push(request.url());
  });
  await stub(page, calls);
  await page.addInitScript(() => {
    window.alert = () => {};
    window.confirm = () => true;
    localStorage.setItem('clawcross_lang', 'zh');
  });

  await page.goto('/mobile/group_chat');
  await expect(page.locator('#group-list-container')).toContainText('Dev');
  await expect(page.locator('#fleet-section, #remote-claude-page')).toHaveCount(0);
  await page.route('**/v1/connections', (route) => route.fulfill({ json: { data: [] } }));
  await page.evaluate(() => document.getElementById('device-connections-menu-item').click());
  await expect(page.locator('.device-connections-dialog')).toBeVisible();
  await page.locator('.device-connections-dialog [data-close]').click();
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
  expect(legacyRequests).toEqual([]);
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
  await expect(dialog.locator('input, textarea')).toHaveCount(3);  // legacy password lives in collapsed advanced settings
  await expect(dialog.getByLabel('密码')).toBeHidden();
  await dialog.getByLabel('你在群里的名字').fill('');
  await dialog.locator('textarea').fill('https://friends.example/group-guest#ticket');
  await expect(dialog.getByRole('button', { name: '加入' })).toBeDisabled();
  await dialog.getByLabel('你在群里的名字').fill('Tester');
  await expect(dialog.getByRole('button', { name: '加入' })).toBeEnabled();
  const bounds = await dialog.boundingBox();
  expect(bounds.x).toBeGreaterThanOrEqual(0); expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
  await dialog.getByRole('button', { name: '加入' }).click();
  await expect(dialog).toHaveCount(0);
  expect(joins).toEqual([{ invite: 'https://friends.example/group-guest#ticket', agents: [], name: 'Tester' }]);
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
  const installs = [];
  await page.route('**/proxy_acpx_status', route => route.fulfill({json:{tools:['codex','claude']}}));
  await page.route('**/proxy_tools', route => route.fulfill({json:{tools:creation.tools}}));
  await page.route('**/proxy_components/acpx', route => {
    if (route.request().method() === 'POST') installs.push('acpx');
    return route.fulfill({contentType:'application/json',body:JSON.stringify({name:'acpx',installed:installs.length > 0,ready:installs.length > 0,missing:[],can_install:true,state:installs.length ? 'complete' : '',platform:'linux'})});
  });
  await page.addInitScript(() => { localStorage.setItem('clawcross_lang','zh'); });
  await page.goto('/mobile/group_chat');
  await page.evaluate(async () => {
    window.registerAgent = async fields => { window.__createdFields = fields; return {agent_id:'ag_generated'}; };
    window.startPrivateChat = async () => true;
    await showCreateAgentModal();
  });
  await page.locator('#create-agent-modal details:has(#create-agent-companion) > summary').click();
  await page.locator('#create-agent-companion').selectOption('codex');
  await expect(page.locator('[data-component-status]')).toContainText('尚未安装');
  expect(installs).toEqual([]);
  expect(await page.evaluate(() => window.__createdFields)).toBeUndefined();
  await page.locator('[data-component-install]').click();
  await expect(page.locator('[data-component-status]')).toContainText('已安装');
  expect(installs).toEqual(['acpx']);
  await page.locator('#create-agent-name').fill('中文助手');
  await expect(page.locator('#ca-acp-runtime')).toHaveValue('');
  await page.locator('#ca-acp-submit').click();
  expect(await page.evaluate(() => window.__createdFields)).toEqual({name:'中文助手',platform:'codex',creation_template:'personal',tools:['read_file','send_to_group'],meta:{acp:{clawcross_tools:true,tools:['read_file','send_to_group']}}});
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

for (const platform of ['qwen','acp']) test('contact creation preserves drafts and scoped tools: '+platform, async ({page})=>{
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
  await expect(page.locator('#create-agent-name')).toBeVisible();
  await page.locator('#create-agent-companion').evaluate(el=>el.closest('details').open=true);
  await page.locator('#create-agent-companion').selectOption(platform);
  if(platform==='acp') await page.locator('#ca-acp-launch [data-launch-value]').fill('node "/Apps/MyAgent/Server.js" --stdio');
  await page.locator('#create-agent-name').fill('Research');
  await page.locator('#create-agent-panel-acp .create-agent-advanced > summary').click();
  await page.locator('#ca-acp-persona').fill('Help me research');
  await page.locator('#ca-acp-tools-picker [data-tool-all]').uncheck();
  await expect(page.locator('#ca-acp-tools-picker .tool-tag.enabled')).toHaveCount(2);
  await page.locator('#ca-acp-tools-picker [data-select-all]').click();
  await expect(page.locator('#ca-acp-tools-picker .tool-tag.enabled')).toHaveCount(3);
  await page.locator('#ca-acp-tools-picker .tool-tag',{hasText:'manage_team'}).click();
  await expect(page.locator('#ca-acp-tools-picker .tool-tag.enabled')).toHaveCount(2);
  await page.evaluate(()=>{setCreateAgentMode('webot');setCreateAgentMode('acp');});
  await expect(page.locator('#create-agent-name')).toHaveValue('Research');
  await expect(page.locator('#ca-acp-persona')).toHaveValue('Help me research');
  expect(await page.evaluate(()=>window.__createdFields)).toBeUndefined();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:'/tmp/clawcross-mobile-agent-create.png'});
  await page.locator('#ca-acp-submit').click();
  expect(await page.evaluate(()=>window.__createdFields)).toEqual({name:'Research',platform,persona:'Help me research',tools:['read_file','send_to_group'],meta:{acp:{clawcross_tools:true,tools:['read_file','send_to_group'],...(platform==='acp'?{agent_name:'',agent_command:'node "/Apps/MyAgent/Server.js" --stdio'}:{})}},creation_template:'personal'});
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
  await expect(page.locator('#create-agent-name')).toBeVisible();
  await expect(page.locator('#ca-acp-submit')).toBeDisabled();
  await expect(page.locator('#mobile-agent-template-hint')).toContainText('适用于内置伙伴');
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
