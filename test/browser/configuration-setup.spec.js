import {test,expect} from '@playwright/test';
import path from 'node:path';

for(const width of [390,1280]) test(`private general configuration fits ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:850});const submitted=[];
  const item={id:'config-test',topic:'model',session_id:'one',draft:{LLM_MODEL:'test-model'},schema:{label:'模型连接',help:'主机共用连接',fields:[
    {name:'LLM_MODEL',label:'模型',type:'text',help:'模型编号'},
    {name:'LLM_API_KEY',label:'密钥',type:'password',human_only:true,configured:true},
    {name:'LLM_VISION_SUPPORT',label:'图片',type:'select',options:['','true','false']}]}};
  await page.route('**/proxy_configuration_setup**',route=>{
    if(route.request().method()==='POST'){submitted.push(route.request().postDataJSON());return route.fulfill({json:{status:'completed',message:'已保存'}});}
    return route.fulfill({json:{requests:[item]}});
  });
  await page.goto('/studio');await page.setContent('<html lang="zh"><body><div id="chat"></div></body></html>');
  await page.addStyleTag({path:path.resolve('src/frontend/static/css/channel-setup.css')});
  await page.addScriptTag({path:path.resolve('src/frontend/static/js/channel-setup.js')});
  await page.evaluate(()=>ClawcrossChannelSetup.sync(document.querySelector('#chat'),['one']));
  await expect(page.locator('[name=LLM_API_KEY]')).toHaveValue('');
  await page.locator('[name=LLM_API_KEY]').fill('PRIVATE_API_KEY');
  await page.locator('[name=LLM_VISION_SUPPORT]').selectOption('false');
  await page.getByRole('button',{name:'保存设置'}).click();await expect(page.locator('.cc-channel-form')).toContainText('已保存');
  expect(submitted[0].values).toEqual({LLM_MODEL:'test-model',LLM_API_KEY:'PRIVATE_API_KEY',LLM_VISION_SUPPORT:'false'});
  expect(await page.locator('body').textContent()).not.toContain('PRIVATE_API_KEY');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
