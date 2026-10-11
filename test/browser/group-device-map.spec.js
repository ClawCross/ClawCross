const { test, expect } = require('@playwright/test');
test.use({ launchOptions: { executablePath: process.env.CLAWCROSS_TEST_CHROME || '/usr/bin/google-chrome' } });

const agent = (id, name, platform, remote) => ({agent_id:id,name,platform,remote,settings:{persona:'',tools:[],teams:[]},status:{state:remote?'remote':'idle',actions:[]}});
const members = [
  {principal:'u:alice',name:'Alice',is_agent:false,is_owner:true,connection_id:'c1',remote:false,online:true,device_kind:'client'},
  {principal:'writer',name:'写手',is_agent:true,connection_id:'c1',remote:false,online:true,device_kind:'client',platform:'webot',agent:agent('writer','写手','webot',false)},
  {principal:'p_bob',name:'Bob',is_agent:false,connection_id:'c2',remote:true,online:true,device_kind:'browser'},
  {principal:'p_bot',name:'Bob 的助手',is_agent:true,connection_id:'c2',remote:true,online:true,device_kind:'browser',platform:'browser',agent:agent('p_bot','Bob 的助手','browser',true)},
  {principal:'p_carol',name:'Carol <b>',is_agent:false,connection_id:'c3',remote:true,online:false,device_kind:'client'},
];

async function expectDevices(page, map, own) {
  await expect(map.locator('.dm-device')).toHaveCount(3);
  await expect(map.locator('.dm-device').first()).toContainText(own);
  await expect(map.locator('.dm-device').first()).toContainText('Alice');
  await expect(map.locator('.dm-device').first().locator('.dm-agent')).toHaveText('写手');
  await expect(map.locator('.dm-device').nth(1)).toContainText('Bob 的浏览器');
  await expect(map.locator('.dm-device').nth(1).locator('.dm-agent')).toHaveText('Bob 的助手');
  await expect(map.locator('.dm-offline')).toHaveCount(1);
  await expect(map.locator('.dm-offline')).toContainText('Carol <b> 的电脑');
  await expect(map.locator('.dm-offline')).toContainText('离线');
  await expect(map.locator('.dm-hub')).toContainText('3 台设备 · 2 台在线');
  await expect(map.locator('.dm-track')).toHaveCount(3);   // every device is linked to the group
  await expect(map.locator('.dm-flow')).toHaveCount(2);    // messages flow only from online devices
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

for (const mobile of [false, true]) {
  test(`message center: members by device, ${mobile ? 'mobile' : 'desktop'}`, async ({ page }) => {
    await page.setViewportSize(mobile ? {width:390,height:844} : {width:1200,height:850});
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => { localStorage.setItem('clawcross_lang','zh'); window.alert = () => {}; });
    await page.route('**/proxy_check_session', route => route.fulfill({json:{valid:true,user_id:'alice',has_password:true,mode:'local'}}));
    await page.route('**/api/llm_config_status', route => route.fulfill({json:{configured:true}}));
    await page.route('**/proxy_tunnel/status', route => route.fulfill({json:{running:false,public_domain:'https://chat.example'}}));
    await page.route('**/proxy_groups**', route => {
      const group = {group_id:'rg_one',title:'朋友们',announcement:'',version:1,kind:'group',owner:'alice',members,messages:[],federated:true,member_count:members.length};
      const path = new URL(route.request().url()).pathname;
      return route.fulfill({json: path === '/proxy_groups' ? {groups:[group]} : path.endsWith('/messages') ? {messages:[]} : group});
    });
    await page.goto('/mobile/group_chat');
    await expect(page.locator('#tab-chats')).toBeVisible();
    await page.evaluate(() => openChat('rg_one','朋友们'));
    await page.evaluate(() => toggleDrawer());
    await expect(page.locator('#member-list .member-item')).toHaveCount(5);
    await page.getByRole('button', {name:'按设备查看'}).click();
    await expect(page.locator('#member-list')).toBeHidden();
    await expectDevices(page, page.locator('#member-map'), '你的电脑');
    await page.getByRole('button', {name:'按列表查看'}).click();
    await expect(page.locator('#member-list')).toBeVisible();
    await expect(page.locator('#member-map')).toBeHidden();
    expect(errors).toEqual([]);
  });
}

test('guest page: members by device', async ({ page }) => {
  await page.setViewportSize({width:390,height:844});
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.route('**/group-guest-api/**', route => {
    const action = new URL(route.request().url()).pathname.split('/').pop();
    if (action === 'info') return route.fulfill({json:{title:'朋友们',identity_key:'g'}});
    if (action === 'join') return route.fulfill({json:{token:'alice-token',name:'Alice'}});
    const device = {c1:0,c2:1,c3:2};
    return route.fulfill({json:{title:'朋友们',identity_key:'g',principal:'u:alice',name:'Alice',messages:[],cursor:0,has_more:false,
      members:members.map(m => ({principal:m.principal,name:m.name,is_agent:m.is_agent,online:m.online,own:!m.remote,device:device[m.connection_id],
        device_kind:m.connection_id === 'c1' ? 'browser' : m.device_kind,platform:m.platform || 'human'}))}});
  });
  await page.goto('/group-guest#ticket');
  await page.locator('#name').fill('Alice'); await page.locator('#password').fill('password123');
  await page.getByRole('button',{name:'进入群聊'}).click();
  await page.locator('#members summary').click();
  await expect(page.locator('#member-list li.online')).toHaveCount(4);
  await page.getByRole('button',{name:'设备图',exact:true}).click();
  await expect(page.locator('#member-list')).toBeHidden();
  await expectDevices(page, page.locator('#member-map'), '你的浏览器');
  await page.getByRole('button',{name:'列表',exact:true}).click();
  await expect(page.locator('#member-list')).toBeVisible();
  expect(errors).toEqual([]);
});
