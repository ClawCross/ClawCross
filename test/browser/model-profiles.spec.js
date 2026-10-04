const {test, expect} = require('@playwright/test');
const path = require('node:path');

async function setup(page, {uncreated=false} = {}) {
  const writes = [];
  let llm = {};
  let effort = 'medium';
  const profiles = [{id:'user:coding',name:'编程',model:'gpt-5.5',provider:'openai',has_api_key:true}];
  await page.route('**/studio',route=>route.fulfill({contentType:'text/html',body:'<html lang="zh-CN"><button id="open">模型</button></html>'}));
  await page.route('**/v1/agents/model-profiles',route=>{
    if(route.request().method()==='POST') {
      const body=route.request().postDataJSON(); writes.push({path:'save',...body});
      const item={id:'user:'+body.name,name:body.name,model:body.model,provider:body.provider,has_api_key:true};
      profiles.push(item); return route.fulfill({json:item});
    }
    return route.fulfill({json:{default:{model:'gpt-5.5',provider:'openai'},profiles}});
  });
  await page.route('**/v1/agents/one',route=>route.fulfill(uncreated ? {status:404,json:{detail:'no Agent'}} : {json:{agent_id:'one',name:'我的助理',platform:'webot',settings:{llm}}}));
  await page.route('**/v1/agents/one/model-profile',route=>{
    const body=route.request().postDataJSON();writes.push({path:'apply',...body});
    const p=profiles.find(p=>p.id===body.profile_id);
    llm=p ? {...p,profile_id:p.id} : {};
    return route.fulfill({json:{}});
  });
  await page.route('**/proxy_webot_runtime_settings*',route=>{
    if(route.request().method()==='POST') {
      const body=route.request().postDataJSON();writes.push({path:'effort',...body});
      effort=body.settings.inference.reasoning_effort;
      return route.fulfill({json:{}});
    }
    return route.fulfill({json:{settings:{inference:{reasoning_effort:effort}},model_capabilities:{model:'gpt-5.5',reasoning_effort_levels:['low','medium','high'],reasoning_effort_default:'medium'}}});
  });
  await page.goto('/studio');
  await page.addStyleTag({path:path.resolve('src/frontend/static/css/external-agent-settings.css')});
  await page.addScriptTag({path:path.resolve('src/frontend/static/js/model-profiles.js')});
  await page.locator('#open').focus();
  await page.evaluate(()=>openAgentModelSettings('one'));
  return writes;
}

test('saved profile and reasoning apply to the selected Agent only',async({page})=>{
  const writes=await setup(page);
  await expect(page.locator('.model-profile-current')).toContainText('gpt-5.5');
  await page.locator('[data-profile]').selectOption('user:coding');
  await page.locator('[data-effort]').selectOption('high');
  await page.locator('[data-save]').click();
  await expect(page.locator('[data-status]')).toContainText('已保存');
  expect(writes).toEqual([{path:'apply',profile_id:'user:coding'},
    {path:'effort',session_id:'one',settings:{inference:{reasoning_effort:'high'}}}]);
  await expect(page.locator('[data-profile]')).toHaveValue('user:coding');
  await expect(page.locator('[data-effort]')).toHaveValue('high');
});

test('saving a profile does not apply it and clears the secret field',async({page})=>{
  const writes=await setup(page);
  await page.locator('.model-profile-create summary').click();
  await page.locator('[name=name]').fill('日常');
  await page.locator('[name=model]').fill('everyday-model');
  await page.locator('[name=api_key]').fill('SECRET');
  await page.locator('[data-profile-form] button').click();
  await expect(page.locator('[data-form-status]')).toContainText('配置已保存');
  await expect(page.locator('[name=api_key]')).toHaveValue('');
  await expect(page.locator('[data-profile]')).toHaveValue('user:日常');
  expect(writes.map(write=>write.path)).toEqual(['save']);
});

test('unsaved conversation remains uncreated; profile form fits 320px',async({page})=>{
  await page.setViewportSize({width:320,height:640});
  const writes=await setup(page,{uncreated:true});
  await expect(page.locator('[data-save]')).toBeEnabled();
  await page.locator('.model-profile-create summary').click();
  const box=await page.locator('.model-profile-dialog').boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x+box.width).toBeLessThanOrEqual(320);
  expect(box.y+box.height).toBeLessThanOrEqual(640);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(writes).toEqual([]);
  await page.keyboard.press('Escape');
  await expect(page.locator('#agent-model-settings')).toHaveCount(0);
  await expect(page.locator('#open')).toBeFocused();
});
