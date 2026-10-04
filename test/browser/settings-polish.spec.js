import {test,expect} from '@playwright/test';

async function fixtures(page){
  await page.route('**/proxy_check_session',r=>r.fulfill({json:{valid:true,user_id:'tester'}}));
  await page.route('**/api/setup_status',r=>r.fulfill({json:{llm_configured:true}}));
  await page.route('**/proxy_settings_full',r=>r.fulfill({json:{settings:{LLM_MODEL:'example-model',LLM_BASE_URL:'https://api.example.com'}}}));
  await page.route('**/proxy_configuration_setup**',r=>r.fulfill({json:{topics:[{id:'model',label:'模型连接',scope:'host',help:'仅私密填写密钥'}],requests:[]}}));
  await page.route('**/proxy_channel_setup**',r=>r.fulfill({json:{channels:[{id:'telegram',label:'Telegram',help:'连接你的机器人'}],requests:[]}}));
}

test('Studio settings have a readable centered dialog and accessible actions',async({page})=>{
  await fixtures(page);await page.setViewportSize({width:1280,height:900});await page.goto('/studio');
  await page.evaluate(()=>openSettings());await expect(page.locator('#configuration-assistant')).toContainText('配置助手');
  const dialog=page.locator('#settings-modal .settings-modal');await expect(dialog).toBeVisible();
  const bounds=await dialog.boundingBox();expect(bounds.width).toBeGreaterThan(600);expect(bounds.x+bounds.width).toBeLessThanOrEqual(1280);
  await expect(page.locator('#settings-modal .settings-footer .settings-btn-save')).toBeVisible();
  expect(await page.locator('#settings-body').evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);
  await page.screenshot({path:'/tmp/clawcross-studio-settings.png'});
});

test('Mobile settings and creation drawers fit small screens',async({page})=>{
  await fixtures(page);await page.setViewportSize({width:390,height:844});await page.goto('/mobile_group_chat');
  await page.evaluate(()=>openSettings());await expect(page.locator('#mobile-configuration-assistant')).toContainText('配置助手');
  await expect(page.locator('#settings-modal .settings-footer .settings-btn-save')).toBeVisible();
  expect(await page.locator('#settings-body').evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);
  await page.evaluate(()=>closeSettings());
  for(const id of ['mobile-alarm-modal','mobile-channel-config','create-agent-modal','create-modal']){
    await page.evaluate(id=>document.getElementById(id).classList.add('show'),id);
    await expect(page.locator('#'+id)).toBeVisible();
    await expect.poll(async()=>{const bounds=await page.locator('#'+id).boundingBox();return Math.round(bounds.x+bounds.width);}).toBeLessThanOrEqual(390);
    const body=page.locator('#'+id+' .drawer-body');expect(await body.evaluate(e=>e.scrollWidth<=e.clientWidth+1)).toBe(true);
    if(id==='mobile-alarm-modal')await page.screenshot({path:'/tmp/clawcross-mobile-alarm.png'});
    await page.evaluate(id=>document.getElementById(id).classList.remove('show'),id);
  }
});
