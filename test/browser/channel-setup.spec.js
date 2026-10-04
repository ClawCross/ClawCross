import {test, expect} from '@playwright/test';
import path from 'node:path';

for (const width of [390, 1280]) {
  test(`private channel form submits outside chat and fits ${width}px`, async ({page}) => {
    await page.setViewportSize({width, height:850});
    const submitted = [];
    const item = {id:'setup-test',session_id:'agent-one',channel:'telegram',draft:{name:'My bot'},schema:{label:'Telegram',fields:[
      {name:'token',label:'Bot token',type:'password',required:true,human_only:true},
      {name:'name',label:'Name',type:'text'}]}};
    await page.route(/\/proxy_(?:configuration|channel)_setup/, route => {
      if(route.request().method()==='POST') {submitted.push(route.request().postDataJSON());return route.fulfill({json:{status:'completed'}});}
      return route.fulfill({json:{requests:[item],channels:[item.schema]}});
    });
    await page.goto('/studio');
    await page.setContent('<html lang="zh"><body><div id="chat"></div></body></html>');
    await page.addStyleTag({path:path.resolve('src/frontend/static/css/channel-setup.css')});
    await page.addScriptTag({path:path.resolve('src/frontend/static/js/channel-setup.js')});
    await page.evaluate(() => ClawcrossChannelSetup.sync(document.querySelector('#chat'), ['agent-one']));
    await expect(page.locator('.cc-channel-form')).toHaveCount(1);
    await page.locator('[name="token"]').fill('PRIVATE_API_TOKEN');
    await page.getByRole('button',{name:'保存连接设置'}).click();
    await expect(page.locator('.cc-channel-form')).toContainText('已保存');
    expect(submitted[0]).toEqual({request_id:'setup-test',values:{token:'PRIVATE_API_TOKEN',name:'My bot'},cancel:false});
    await expect(page.locator('[name="token"]')).toHaveCount(0);
    expect(await page.locator('body').textContent()).not.toContain('PRIVATE_API_TOKEN');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test('channel cancel returns only cancellation and no credential values', async ({page}) => {
  const submitted = [];
  const item = {id:'setup-cancel',topic:'channel:telegram',session_id:'one',status:'pending',draft:{},schema:{label:'Telegram',fields:[
    {name:'token',label:'Token',type:'password',human_only:true}]}};
  await page.route('**/proxy_configuration_setup**',route => {
    if (route.request().method() === 'POST') {
      submitted.push(route.request().postDataJSON()); item.status = 'cancelled';
      return route.fulfill({json:{status:'cancelled'}});
    }
    return route.fulfill({json:{requests:[item]}});
  });
  await page.goto('/studio'); await page.setContent('<html lang="zh"><body><div id="chat"></div></body></html>');
  await page.addScriptTag({path:path.resolve('src/frontend/static/js/channel-setup.js')});
  await page.evaluate(() => ClawcrossChannelSetup.sync(document.querySelector('#chat'),['one']));
  await page.locator('[name=token]').fill('PRIVATE_NOT_SUBMITTED');
  await page.getByRole('button',{name:'取消',exact:true}).click();
  await expect(page.locator('.cc-channel-form')).toContainText('已取消');
  expect(submitted).toEqual([{request_id:'setup-cancel',values:{},cancel:true}]);
  await page.evaluate(() => ClawcrossChannelSetup.sync(document.querySelector('#chat'),['one']));
  await expect(page.locator('.cc-channel-form')).toHaveCount(1);
  await expect(page.locator('input')).toHaveCount(0);
});
