import {test,expect} from '@playwright/test';
import path from 'node:path';

test('native list and registration require explicit clicks and fit mobile',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  let lists=0;const imports=[];
  await page.route('**/v1/agents/native-sessions**',route=>{
    if(route.request().method()==='POST'){imports.push(route.request().postDataJSON());return route.fulfill({json:{agent_id:'ag_imported',name:'Old task'}});}
    lists++;return route.fulfill({json:{sessions:[{session_id:'native-id',title:'Old task',cwd:'/workspace',ticket:'ticket-1'}],next_cursor:null}});
  });
  await page.goto('/studio');await page.setContent('<html lang="zh"><body></body></html>');
  await page.addStyleTag({path:path.resolve('src/frontend/static/css/external-agent-settings.css')});
  await page.addStyleTag({path:path.resolve('src/frontend/static/css/native-session-browser.css')});
  await page.addScriptTag({path:path.resolve('src/frontend/static/js/native-session-browser.js')});
  await page.evaluate(()=>ClawcrossNativeSessions.open());
  expect(lists).toBe(0);expect(imports).toHaveLength(0);
  await page.getByRole('button',{name:'读取会话列表'}).click();
  await expect(page.locator('.native-session-row')).toHaveCount(1);expect(imports).toHaveLength(0);
  await page.getByRole('button',{name:'登记为 Agent'}).click();
  await expect(page.getByRole('button',{name:'已登记'})).toBeDisabled();
  expect(imports).toEqual([{ticket:'ticket-1',name:'Old task'}]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
